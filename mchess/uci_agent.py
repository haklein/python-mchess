import logging
import time
import queue
import json
import os
import threading
from distutils.spawn import find_executable
import glob

import chess.uci


class UciEngines:
    """Search for UCI engines and make a list of all available engines
    """

    def __init__(self, appque, prefs):
        self.log = logging.getLogger("UciEngines")
        self.prefs = prefs
        self.appque = appque

        COMMON_ENGINES = ['stockfish', 'crafty', 'komodo']
        for engine_name in COMMON_ENGINES:
            engine_json_path = os.path.join('engines', engine_name+'.json')
            if os.path.exists(engine_json_path):
                continue
            else:
                engine_path = find_executable(engine_name)
                if engine_path is not None:
                    engine_json = {'name': engine_name,
                                   'path': engine_path, 'active': True}
                    with open(engine_json_path, 'w') as f:
                        try:
                            json.dump(engine_json, f, indent=4)
                        except:
                            self.log.error(
                                f'Failed to write no engine description {engine_json_path}')
                            continue
                    self.log.info(f'Found new UCI engine {engine_name}')
        engine_json_list = glob.glob('engines/*.json')
        if len(engine_json_list) == 0:
            self.log.warning(
                'No UCI engines found, and none is defined in engines subdir.')
        self.engines = {}
        for engine_json_path in engine_json_list:
            if '-help.json' in engine_json_path or 'engine-template.json' in engine_json_path:
                continue
            self.log.debug(f'Checking UCI engine {engine_name}')
            self.open_engine(engine_json_path)

    def open_engine(self, engine_json_path):
        try:
            with open(engine_json_path, 'r') as f:
                engine_json = json.load(f)
        except:
            self.log.error(
                f'Failed to read UCI engine description {engine_json_path}')
            return False
        if 'name' not in engine_json:
            self.log.error(
                f"Mandatory parameter 'name' is not in UCI description {engine_json_path}, ignoring this engine.")
            return False
        if 'path' not in engine_json:
            self.log.error(
                f"Mandatory parameter 'path' is not in UCI description {engine_json_path}, ignoring this engine.")
            return False
        if os.path.exists(engine_json['path']) is False:
            self.log.error(
                f"Invalid path {engine_json['path']} in UCI description {engine_json_path}, ignoring this engine.")
            return False

        if 'active' not in engine_json or engine_json['active'] is False:
            self.log.debug(
                f"UCI engine at {engine_json_path} has not property 'active': true, ignoring this engine.")
            return False

        base_name, _ = os.path.splitext(engine_json_path)
        engine_json_help_path = base_name + "-help.json"
        name = engine_json['name']
        self.engines[name] = {}
        self.engines[name]['params'] = engine_json
        try:
            self.engines[name]['engine'] = chess.uci.popen_engine(
                engine_json['path'])
        except:
            self.log.error(
                f'Failed to popen UCI engine {name} at {engine_json_path}, ignoring this engine.')
            return False

        self.engines[name]['info_handler'] = self.UciHandler()
        self.engines[name]['info_handler'].name = name
        self.engines[name]['info_handler'].active = True
        self.engines[name]['info_handler'].que = self.appque
        self.engines[name]['engine'].info_handlers.append(
            self.engines[name]['info_handler'])

        self.engines[name]['engine'].uci()

        optsh = {}
        opts = {}
        rewrite_json = False
        if os.path.exists(engine_json_path) is False:
            rewrite_json = True
            self.engines[name]['params']['uci-options'] = {}
        if 'uci-options' not in self.engines[name]['params'] or self.engines[name]['params']['uci-options'] == {}:
            rewrite_json = True
            self.engines[name]['params']['uci-options'] = {}
        else:
            for opt in self.engines[name]['engine'].options:
                if opt not in self.engines[name]['params']['uci-options']:
                    entries = self.engines[name]['engine'].options[opt]
                    # Ignore buttons
                    if entries.type != 'button':
                        self.log.warning(
                            'New UCI option {} for {}, resetting to defaults'.format(opt, name))
                        rewrite_json = True

        if rewrite_json is True:
            self.log.info("Writing defaults for {} to {}".format(
                name, engine_json_path))
            for opt in self.engines[name]['engine'].options:
                entries = self.engines[name]['engine'].options[opt]
                optvs = {}
                optvs['name'] = entries.name
                optvs['type'] = entries.type
                optvs['default'] = entries.default
                optvs['min'] = entries.min
                optvs['max'] = entries.max
                optvs['var'] = entries.var
                optsh[opt] = optvs
                # TODO: setting buttons to their default causes python_chess uci to crash (komodo 9), see above
                if entries.type != 'button':
                    opts[opt] = entries.default
            self.engines[name]['params']['uci-options'] = opts
            self.engines[name]['uci-options-help'] = optsh
            try:
                with open(engine_json_path, 'w') as f:
                    json.dump(self.engines[name]['params'], f, indent=4)
            except Exception as e:
                self.log.error(
                    f"Can't save engine.json to {engine_json_path}, {e}")
            try:
                with open(engine_json_help_path, 'w') as f:
                    json.dump(self.engines[name]
                              ['uci-options-help'], f, indent=4)
            except Exception as e:
                self.log.error(
                    f"Can't save help to {engine_json_help_path}, {e}")
        else:
            opts = self.engines[name]['params']['uci-options']

        if 'Ponder' in opts:
            self.engines[name]['use_ponder'] = opts['Ponder']
        else:
            self.engines[name]['use_ponder'] = False

        self.engines[name]['engine'].setoption(opts)
        time.sleep(0.1)
        self.engines[name]['engine'].isready()

    class UciHandler(chess.uci.InfoHandler):
        def __init__(self):
            self.que = None
            self.name = 'UciAgent'
            self.last_pv_move = ""
            self.log = logging.getLogger(self.name)
            self.cdepth = None
            self.cseldepth = None
            self.cscore = None
            self.cnps = None
            self.mpv_num = 1
            self.que_cache = {}
            self.que_cache_time = 2.0
            self.que_timer_sec = 1.0
            thr = threading.Timer(self.que_timer_sec, self.que_timer)
            thr.daemon = True
            thr.start()
            super().__init__()

        def que_timer(self):
            cur_time = time.time()
            keys = list(self.que_cache.keys())
            for key in keys:
                if cur_time-self.que_cache[key]['timestamp'] > self.que_cache_time:
                    self.que.put(self.que_cache[key])
                    del self.que_cache[key]
            thr = threading.Timer(self.que_timer_sec, self.que_timer)
            thr.daemon = True
            thr.start()

        def empty_que_cache(self):
            for msg in self.que_cache:
                self.que.put(self.que_cache[msg])
            self.que_cache = {}

        def post_info(self):
            # Called whenever a complete info line has been processed.
            # print(self.info)
            super().post_info()  # Release the lock

        def on_bestmove(self, bestmove, ponder):
            self.log.debug("Best: {}, ponder: {}".format(bestmove, ponder))
            rep = {'move': {
                'uci': bestmove.uci(),
                'actor': self.name
            }}
            with self:
                if 1 in self.info["score"]:
                    score = self.info["score"][1].cp
                    mate = self.info["score"][1].mate
                    if mate is not None:
                        rep['move']['score'] = '#{}'.format(mate)
                    else:
                        rep['move']['score'] = '{:.2f}'.format(
                            float(score)/100.0)
            # if self.cdepth is not None:
            #     rep['move']['depth'] = self.cdepth
            # if self.cseldepth is not None:
            #     rep['move']['seldepth'] = self.cseldepth
            # if self.cnps is not None:
            #     rep['move']['nps'] = self.cnps
            # if self.cscore is not None:
            #     rep['move']['score'] = self.cscore
            # if self.ctbhits is not None:
            #     rep['move']['tbhits'] = self.ctbhits
            if ponder is not None:
                rep['move']['ponder'] = ponder.uci()
                self.ponder = ponder.uci()
            else:
                self.ponder = None
            self.que.put(rep)
            self.last_pv_move = ""
            self.cdepth = None
            self.cseldepth = None
            self.cscore = None
            self.cnps = None
            self.ctbhits = None
            self.mpv_num = 1
            self.que_cache = {}

            super().on_bestmove(bestmove, ponder)

        def score(self, cp, mate, lowerbound, upperbound):
            # if self.last_board.turn == chess.BLACK:
            #     cp = cp*-1
            #     if mate is not None:
            #         mate = mate*-1

            self.que.put({'score': {'cp': cp, 'mate': mate}})
            if mate is not None:
                self.cscore = '#{}'.format(mate)
            else:
                self.cscore = '{:.2f}'.format(float(cp)/100.0)
            super().score(cp, mate, lowerbound, upperbound)

        def multipv(self, num):
            self.mpv_num = num
            super().multipv(num)

        def pv(self, moves):
            rep = {'curmove': {
                'multipv_ind': self.mpv_num,
                'variant': moves,
                'actor': self.name
            }}
            que_key = '{}-{}'.format(self.name, self.mpv_num)

            if self.cdepth is not None:
                rep['curmove']['depth'] = self.cdepth
            if self.cseldepth is not None:
                rep['curmove']['seldepth'] = self.cseldepth
            if self.cnps is not None:
                rep['curmove']['nps'] = self.cnps
            if self.cscore is not None:
                rep['curmove']['score'] = self.cscore
            if self.ctbhits is not None:
                rep['curmove']['tbhits'] = self.ctbhits
            if que_key not in self.que_cache:
                rep['timestamp'] = time.time()
            else:
                rep['timestamp'] = self.que_cache[que_key]['timestamp']
            self.que_cache[que_key] = rep
            # self.que.put(rep)
            super().pv(moves)

        def depth(self, n):
            self.cdepth = n
            self.que.put({'depth': n})
            super().depth(n)

        def seldepth(self, n):
            self.cseldepth = n
            self.que.put({'seldepth': n})
            super().seldepth(n)

        def nps(self, n):
            self.cnps = n
            self.que.put({'nps': n})
            super().nps(n)

        def tbhits(self, n):
            self.ctbhits = n
            self.que.put({'tbhits': n})
            super().tbhits(n)


class UciAgent:
    def __init__(self, engine_spec, prefs):
        self.active = False
        self.prefs = prefs
        self.name = engine_spec['params']['name']
        self.log = logging.getLogger('UciAgent_'+self.name)
        self.engine = engine_spec['engine']
        # self.ponder_board = None
        self.active = True
        self.busy = False

    def quit(self):
        ft = self.engine.terminate(async_callback=True)
        ft.result()
        self.active = False

    def agent_ready(self):
        return self.active

    def go(self, board, mtime, ponder=False):
        self.engine.position(board)
        self.last_board = board
        if mtime == 0:
            self.engine.go(infinite=True,
                           async_callback=True, ponder=ponder)
        else:
            self.engine.go(movetime=mtime,
                           async_callback=True, ponder=ponder)
