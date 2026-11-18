# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""对局状态机：管理正式棋局、待执行计划、确认与视觉提交、请求去重、演示棋钟和 JSONL 日志。"""
import json  # 导入JSON消息、棋谱和事件日志读写。
import time  # 导入单调时钟、搜索/动作超时与日志时间。
import uuid  # 导入生成请求、棋局和动作的唯一编号。
import threading  # 导入棋局/引擎锁以及串口和读管道后台线程。
import copy  # 导入复制状态，避免调用者改坏正式数据。
from pathlib import Path  # 导入跨平台文件路径。
from collections import OrderedDict, Counter  # 导入棋子计数、请求缓存顺序和重复局面统计。
from board import START, validate, parse, apply, coordinates, infer_move, position_key, legal_moves  # 导入本项目的坐标/FEN转换和规则函数。

class Game:  # 定义对局状态的唯一持有者。
    """对局状态的唯一持有者。核心属性：fen正式局面；root初始局面；moves/history已提交历史；pending待执行结果；phase阶段；clocks演示棋钟；cache最近256条请求；lock保证串口和网页串行修改。"""

    def __init__(self, engine, log_dir='data'):
        """功能：建立唯一引擎、可重入锁、请求缓存和日志目录，初始状态 IDLE，尚未建局。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            engine：具备 best_move(...) 和 close() 的引擎实例，DemoEngine或Pikafish。
            log_dir：JSONL事件日志保存目录，默认data；不是模型目录。

        返回：无。"""
        self.engine = engine
        # [PY-game-001] threading.RLock｜来源：Python标准库threading
        # 功能：创建同一线程可重复进入的锁，保护共享棋局。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：可重入锁对象。
        self.lock = threading.RLock()
        # [PY-game-002] OrderedDict｜来源：Python标准库collections.OrderedDict
        # 功能：建立保留插入顺序的字典，便于淘汰最旧请求。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：空有序字典。
        self.cache = OrderedDict()
        # [PY-game-003] Path｜来源：Python标准库pathlib.Path
        # 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
        # 参数 *pathsegments[1]=log_dir：文件或目录路径的各部分
        # 返回：适合当前操作系统的Path对象。
        self.log_dir = Path(log_dir)
        # [PY-game-004] self.log_dir.mkdir｜来源：Python标准库pathlib.Path.mkdir
        # 功能：在磁盘创建目录。
        # 参数 parents=True：True时同时创建缺失的上级目录
        # 参数 exist_ok=True：True时目标目录已存在不报错
        # 返回：None。
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.started = False
        self.phase = 'IDLE'
        self.pending = None
        self.error = ''
        self.game_id = None

    def log(self, event, data):
        """功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            event：日志事件名称，例如 new、plan、commit、fault。
            data：可JSON序列化的日志内容，例如元信息字典或错误字符串。

        返回：无；写入失败会向上传递异常。"""
        # [PY-game-005] (self.log_dir / (self.game_id + '.jsonl')).open｜来源：Python标准库pathlib.Path.open
        # 功能：打开这个Path指向的文件；with退出时自动关闭文件。
        # 参数 mode='a'：打开模式，a表示追加到文件尾
        # 参数 encoding='utf-8'：文本读写编码，此处UTF-8
        # 返回：文件对象。
        with (self.log_dir / (self.game_id + '.jsonl')).open('a', encoding='utf-8') as f:
            # [PY-game-006] f.write｜来源：Python标准库io.TextIOBase/文本文件对象
            # 功能：向已打开的文本文件或引擎输入管道写入字符串。
            # 参数 s=json.dumps(dict(time=time.time(), event=event, data=data), ensure_ascii=False) + '\n'：待写文本；UCI命令和JSONL日志都显式加换行
            # 返回：写入字符数；写入不一定立即刷新。
            # [PY-game-007] json.dumps｜来源：Python标准库json
            # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
            # 参数 obj=dict(time=time.time(), event=event, data=data)：待序列化的字典、列表等对象
            # 参数 ensure_ascii=False：True把非ASCII字符转成转义；False保留中文
            # 返回：str字符串。
            # [PY-game-008] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 time=time.time()：日志的Unix时间戳秒数
            # 参数 event=event：日志事件名
            # 参数 data=data：该日志事件的数据
            # 返回：新字典。
            # [PY-game-009] time.time｜来源：Python标准库time
            # 功能：读取Unix时间戳，为日志记录事件时间。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：从Unix纪元起的浮点秒数。
            f.write(json.dumps(dict(time=time.time(), event=event, data=data), ensure_ascii=False) + '\n')

    def tick(self):
        """功能：更新 READY 阶段的演示棋钟，并检查 ACK/执行/复核超时；超时冻结到 FAULT。由状态查询或串口轮询触发，不是独立定时器。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。

        返回：无；会修改 phase/error/clocks。"""
        if self.started and self.phase == 'READY':
            # [PY-game-010] time.monotonic｜来源：Python标准库time
            # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：浮点秒数。
            now = time.monotonic()
            # [PY-game-011] parse｜来源：本项目 board.py，parse
            # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
            # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
            side = parse(self.fen)[1]
            # [PY-game-012] max｜来源：Python内置函数
            # 功能：返回参数中的较大值；与0比较可把负时间截为0。
            # 参数 *values[1]=0：参与比较的数值；本项目传入多个位置参数
            # 参数 *values[2]=self.clocks[side] - (now - self.last_tick)：参与比较的数值；本项目传入多个位置参数
            # 返回：最大值。
            self.clocks[side] = max(0, self.clocks[side] - (now - self.last_tick))
            self.last_tick = now
            if self.clocks[side] == 0:
                self.phase = 'GAME_OVER'
                self.error = side + ' clock expired (demo clock)'
        # [PY-game-013] time.monotonic｜来源：Python标准库time
        # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：浮点秒数。
        if self.pending and self.phase in ('WAIT_ACK', 'EXECUTING', 'VERIFY') and (time.monotonic() > self.pending['deadline']):
            self.phase = 'FAULT'
            self.error = 'Physical transaction timed out; inspect board, do not replay automatically'
            # [PY-game-014] self.log｜来源：本项目 game.py，Game.log
            # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
            # 参数 event='fault'：日志事件名称，例如 new、plan、commit、fault。
            # 参数 data=self.error：可JSON序列化的日志内容，例如元信息字典或错误字符串。
            # 返回：无；写入失败会向上传递异常。
            self.log('fault', self.error)

    def state(self):
        """功能：先调用 tick，再返回正式局面和待执行计划快照；不对外暴露内部单调时钟 deadline。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。

        返回：状态字典，含 phase/fen/pieces/ply/pending 等；未建局时只含少量字段。"""
        # [PY-game-015] self.tick｜来源：本项目 game.py，Game.tick
        # 功能：更新 READY 阶段的演示棋钟，并检查 ACK/执行/复核超时；超时冻结到 FAULT。由状态查询或串口轮询触发，不是独立定时器。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：无；会修改 phase/error/clocks。
        self.tick()
        if not self.started:
            # [PY-game-016] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 phase=self.phase：状态机阶段，例如READY/VERIFY/FAULT
            # 参数 engine=self.engine.name：引擎名称或路径
            # 返回：新字典。
            return dict(phase=self.phase, engine=self.engine.name)
        # [PY-game-017] copy.deepcopy｜来源：Python标准库copy
        # 功能：递归复制嵌套对象，避免修改副本影响原数据。
        # 参数 x=self.pending：待复制的字典、列表或状态对象
        # 返回：独立副本。
        p = copy.deepcopy(self.pending)
        if p:
            # [PY-game-018] p.pop｜来源：Python内置dict类型的方法
            # 功能：移除指定字段并取出它的值。
            # 参数 key='deadline'：要删除并读取的字段名；此字段表示本机单调时钟截止点，不是可跨机器解释的时间戳
            # 参数 default=None：字段不存在时的备用值；不传时可能抛KeyError
            # 返回：原字段值，或传入的default。
            p.pop('deadline', None)
        # [PY-game-019] dict｜来源：Python内置类型dict的构造调用
        # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
        # 参数 phase=self.phase：状态机阶段，例如READY/VERIFY/FAULT
        # 参数 engine=self.engine.name：引擎名称或路径
        # 参数 mode=self.mode：virtual虚拟或robot实机协议
        # 参数 game_id=self.game_id：当前棋局唯一编号
        # 参数 fen=self.fen：完整棋局FEN，含行棋方和计数
        # 参数 turn=parse(self.fen)[1]：当前行棋方w红/b黑
        # 参数 ply=len(self.moves)：已正式提交的半回合数
        # 参数 pieces=coordinates(self.fen)：全部存活棋子的{piece,row,col}列表，不是仅变化部分
        # 参数 history=self.history：已正式提交的棋谱列表
        # 参数 clocks=self.clocks：红黑剩余演示秒数
        # 参数 pending=p：尚未确认提交的实体计划
        # 参数 error=self.error：错误说明文本
        # 参数 ai_side=self.ai_side：AI阵营w红/b黑
        # 返回：新字典。
        # [PY-game-020] parse｜来源：本项目 board.py，parse
        # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
        # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
        # [PY-game-021] len｜来源：Python内置函数
        # 功能：取得容器、字符串或字节序列的元素数量。
        # 参数 obj=self.moves：要计数的对象；字节序列按字节数计，字符串按字符数计
        # 返回：整数长度。
        # [PY-game-022] coordinates｜来源：本项目 board.py，coordinates
        # 功能：把 FEN 转成全部存活棋子的类别和 row/col 列表，不包含空点。
        # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：[{piece, row, col}, ...]。
        return dict(phase=self.phase, engine=self.engine.name, mode=self.mode, game_id=self.game_id, fen=self.fen, turn=parse(self.fen)[1], ply=len(self.moves), pieces=coordinates(self.fen), history=self.history, clocks=self.clocks, pending=p, error=self.error, ai_side=self.ai_side)

    def commit(self, move):
        """功能：正式提交一手棋：同步 FEN、历史、棋钟与日志，并检查三次重复及无合法走法；只应由已确认流程调用。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            move：4字符ICCS走法，例如 b2e2；不是坐标列表。

        返回：无；修改正式棋局。"""
        before = self.fen
        # [PY-game-023] apply｜来源：本项目 board.py，apply
        # 功能：复核一步合法走法，生成走后 FEN、吃子信息和中文记谱；不修改传入棋局。
        # 参数 fen=before：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 参数 move=move：4字符ICCS走法，例如 b2e2；不是坐标列表。
        # 返回：(new_fen, meta)，meta 含 uci/text/from/to/piece/capture。
        after, meta = apply(before, move)
        # [PY-game-024] self.history.append｜来源：Python内置list类型的方法
        # 功能：在列表末尾添加一项，原地修改列表。
        # 参数 object=dict(before=before, after=after, clocks=copy.deepcopy(self.clocks), **meta)：要追加的单项内容，不是整个列表的展开
        # 返回：None。
        # [PY-game-025] dict｜来源：Python内置类型dict的构造调用
        # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
        # 参数 before=before：走棋前FEN
        # 参数 after=after：走棋后FEN
        # 参数 clocks=copy.deepcopy(self.clocks)：红黑剩余演示秒数
        # 参数 **meta：展开走法的uci、text、from、to、piece、capture元信息
        # 返回：新字典。
        # [PY-game-026] copy.deepcopy｜来源：Python标准库copy
        # 功能：递归复制嵌套对象，避免修改副本影响原数据。
        # 参数 x=self.clocks：待复制的字典、列表或状态对象
        # 返回：独立副本。
        self.history.append(dict(before=before, after=after, clocks=copy.deepcopy(self.clocks), **meta))
        # [PY-game-027] self.moves.append｜来源：Python内置list类型的方法
        # 功能：在列表末尾添加一项，原地修改列表。
        # 参数 object=move：要追加的单项内容，不是整个列表的展开
        # 返回：None。
        self.moves.append(move)
        self.fen = after
        # [PY-game-028] time.monotonic｜来源：Python标准库time
        # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：浮点秒数。
        self.last_tick = time.monotonic()
        self.phase = 'READY'
        # [PY-game-029] self.log｜来源：本项目 game.py，Game.log
        # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
        # 参数 event='commit'：日志事件名称，例如 new、plan、commit、fault。
        # 参数 data=self.history[-1]：可JSON序列化的日志内容，例如元信息字典或错误字符串。
        # 返回：无；写入失败会向上传递异常。
        self.log('commit', self.history[-1])
        # [PY-game-030] position_key｜来源：本项目 board.py，position_key
        # 功能：提取棋子布局和行棋方，忽略 FEN 计数，用于重复局面计数。
        # 参数 fen=self.root：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：FEN 前两个字段组成的字符串。
        # [PY-game-031] position_key｜来源：本项目 board.py，position_key
        # 功能：提取棋子布局和行棋方，忽略 FEN 计数，用于重复局面计数。
        # 参数 fen=h['after']：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：FEN 前两个字段组成的字符串。
        keys = [position_key(self.root)] + [position_key(h['after']) for h in self.history]
        # [PY-game-032] Counter｜来源：Python标准库collections.Counter
        # 功能：统计每个元素出现次数，用于棋子上限和重复局面检查。
        # 参数 iterable=keys：需要统计的元素序列/生成器
        # 返回：计数字典Counter。
        # [PY-game-033] position_key｜来源：本项目 board.py，position_key
        # 功能：提取棋子布局和行棋方，忽略 FEN 计数，用于重复局面计数。
        # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：FEN 前两个字段组成的字符串。
        if Counter(keys)[position_key(self.fen)] >= 3:
            self.phase = 'REVIEW'
            self.error = 'Threefold repetition: manual adjudication for perpetual check/chase'
        # [PY-game-034] next｜来源：Python内置函数
        # 功能：取迭代器的下一项；本项目也用它检查是否存在合法走法。
        # 参数 iterator=legal_moves(self.fen)：要读取的生成器/迭代器
        # 参数 default=None：迭代器耗尽时的备用值，此处通常为None
        # 返回：下一项；耗尽时返回传入的default。
        # [PY-game-035] legal_moves｜来源：本项目 board.py，legal_moves
        # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
        # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：逐项产生4字符 ICCS 走法。
        elif not next(legal_moves(self.fen), None):
            self.phase = 'GAME_OVER'
            # [PY-game-036] parse｜来源：本项目 board.py，parse
            # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
            # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
            self.error = parse(self.fen)[1] + ' loses: no legal move (mate or stalemate)'

    def require(self, *phases):
        """功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            *phases：可接受的阶段字符串，可传多个，例如 READY、VERIFY。

        返回：无；条件不满足抛 ValueError。"""
        if not self.started or self.phase not in phases:
            # [PY-game-037] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Action not allowed in phase ' + self.phase：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Action not allowed in phase ' + self.phase)

    def check_session(self, m):
        """功能：核对请求的 game_id 和 ply，拒绝旧棋局或旧步数的命令。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。

        返回：无；不一致抛 ValueError。"""
        # [PY-game-038] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='game_id'：字段名；此字段表示当前棋局唯一编号
        # 返回：字段值或default；省略default时为None。
        if m.get('game_id') != self.game_id:
            # [PY-game-039] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Stale/missing game_id'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Stale/missing game_id')
        # [PY-game-040] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='ply'：字段名；此字段表示已正式提交的半回合数
        # 返回：字段值或default；省略default时为None。
        # [PY-game-041] len｜来源：Python内置函数
        # 功能：取得容器、字符串或字节序列的元素数量。
        # 参数 obj=self.moves：要计数的对象；字节序列按字节数计，字符串按字符数计
        # 返回：整数长度。
        if m.get('ply') != len(self.moves):
            # [PY-game-042] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Stale/missing ply'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Stale/missing ply')

    def rpc(self, m):
        """功能：所有入口共用的加锁请求门面：更新时限、检查 id、缓存去重、分发 handle 并包装统一响应。同 id 同内容返回缓存响应快照，不重新执行。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。

        返回：{ok,id,result,state} 或 {ok:false,error,state}；部分 id 校验错误不附 state。"""
        with self.lock:
            # [PY-game-043] self.tick｜来源：本项目 game.py，Game.tick
            # 功能：更新 READY 阶段的演示棋钟，并检查 ACK/执行/复核超时；超时冻结到 FAULT。由状态查询或串口轮询触发，不是独立定时器。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：无；会修改 phase/error/clocks。
            self.tick()
            # [PY-game-044] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='id'：字段名；此字段表示请求唯一编号；用于幂等去重
            # 返回：字段值或default；省略default时为None。
            req = m.get('id')
            # [PY-game-045] isinstance｜来源：Python内置函数
            # 功能：检查对象是否属于指定类型或其子类。
            # 参数 object=req：被检查的对象
            # 参数 classinfo=str：允许的类型，或多个类型组成的元组
            # 返回：True/False。
            # [PY-game-046] len｜来源：Python内置函数
            # 功能：取得容器、字符串或字节序列的元素数量。
            # 参数 obj=req：要计数的对象；字节序列按字节数计，字符串按字符数计
            # 返回：整数长度。
            if not isinstance(req, str) or not 1 <= len(req) <= 80:
                # [PY-game-047] dict｜来源：Python内置类型dict的构造调用
                # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
                # 参数 ok=False：请求是否成功的布尔值
                # 参数 error='A unique id string is required'：错误说明文本
                # 返回：新字典。
                return dict(ok=False, error='A unique id string is required')
            # [PY-game-048] json.dumps｜来源：Python标准库json
            # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
            # 参数 obj=m：待序列化的字典、列表等对象
            # 参数 sort_keys=True：True按字段名排序，使请求指纹和CRC原文可复现
            # 参数 separators=(',', ':')：逗号、冒号分隔符元组；去掉空格可缩小报文
            # 返回：str字符串。
            fingerprint = json.dumps(m, sort_keys=True, separators=(',', ':'))
            if req in self.cache:
                old, response = self.cache[req]
                # [PY-game-049] copy.deepcopy｜来源：Python标准库copy
                # 功能：递归复制嵌套对象，避免修改副本影响原数据。
                # 参数 x=response：待复制的字典、列表或状态对象
                # 返回：独立副本。
                # [PY-game-050] dict｜来源：Python内置类型dict的构造调用
                # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
                # 参数 ok=False：请求是否成功的布尔值
                # 参数 error='id reused with different contents'：错误说明文本
                # 返回：新字典。
                return copy.deepcopy(response) if old == fingerprint else dict(ok=False, error='id reused with different contents')
            try:
                # [PY-game-051] self.handle｜来源：本项目 game.py，Game.handle
                # 功能：按 type 执行 new/recover/observe/human_move/ai/ack/done/undo/state；实体 AI 只生成 PLAN，只有 DONE 后的新视觉核对通过才 commit。
                # 参数 m=m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
                # 返回：动作结果字符串、走法元信息、PLAN 或状态字典；由 rpc 统一包装。
                result = self.handle(m)
                # [PY-game-052] dict｜来源：Python内置类型dict的构造调用
                # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
                # 参数 ok=True：请求是否成功的布尔值
                # 参数 id=req：请求唯一编号；用于幂等去重
                # 参数 result=result：动作返回结果
                # 参数 state=copy.deepcopy(self.state())：完整棋局状态或测试状态
                # 返回：新字典。
                # [PY-game-053] copy.deepcopy｜来源：Python标准库copy
                # 功能：递归复制嵌套对象，避免修改副本影响原数据。
                # 参数 x=self.state()：待复制的字典、列表或状态对象
                # 返回：独立副本。
                # [PY-game-054] self.state｜来源：本项目 game.py，Game.state
                # 功能：先调用 tick，再返回正式局面和待执行计划快照；不对外暴露内部单调时钟 deadline。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：状态字典，含 phase/fen/pieces/ply/pending 等；未建局时只含少量字段。
                response = dict(ok=True, id=req, result=result, state=copy.deepcopy(self.state()))
            except Exception as exc:
                # [PY-game-055] dict｜来源：Python内置类型dict的构造调用
                # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
                # 参数 ok=False：请求是否成功的布尔值
                # 参数 id=req：请求唯一编号；用于幂等去重
                # 参数 error=str(exc)：错误说明文本
                # 参数 state=copy.deepcopy(self.state())：完整棋局状态或测试状态
                # 返回：新字典。
                # [PY-game-056] str｜来源：Python内置类型str的构造调用
                # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                # 参数 object=exc：要转换的对象
                # 返回：字符串。
                # [PY-game-057] copy.deepcopy｜来源：Python标准库copy
                # 功能：递归复制嵌套对象，避免修改副本影响原数据。
                # 参数 x=self.state()：待复制的字典、列表或状态对象
                # 返回：独立副本。
                # [PY-game-058] self.state｜来源：本项目 game.py，Game.state
                # 功能：先调用 tick，再返回正式局面和待执行计划快照；不对外暴露内部单调时钟 deadline。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：状态字典，含 phase/fen/pieces/ply/pending 等；未建局时只含少量字段。
                response = dict(ok=False, id=req, error=str(exc), state=copy.deepcopy(self.state()))
            self.cache[req] = (fingerprint, response)
            # [PY-game-059] len｜来源：Python内置函数
            # 功能：取得容器、字符串或字节序列的元素数量。
            # 参数 obj=self.cache：要计数的对象；字节序列按字节数计，字符串按字符数计
            # 返回：整数长度。
            while len(self.cache) > 256:
                # [PY-game-060] self.cache.popitem｜来源：Python标准库collections.OrderedDict.popitem
                # 功能：弹出一条缓存记录，以限制缓存总量。
                # 参数 last=False：False弹出最早插入项，True弹出最后插入项
                # 返回：(key,value)二元组。
                self.cache.popitem(last=False)
            return response

    def handle(self, m):
        """功能：按 type 执行 new/recover/observe/human_move/ai/ack/done/undo/state；实体 AI 只生成 PLAN，只有 DONE 后的新视觉核对通过才 commit。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。

        返回：动作结果字符串、走法元信息、PLAN 或状态字典；由 rpc 统一包装。"""
        # [PY-game-061] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='type'：字段名；此字段表示动作类型；PLAN在响应里表示待执行计划
        # 返回：字段值或default；省略default时为None。
        t = m.get('type')
        if t == 'state':
            # [PY-game-062] self.state｜来源：本项目 game.py，Game.state
            # 功能：先调用 tick，再返回正式局面和待执行计划快照；不对外暴露内部单调时钟 deadline。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：状态字典，含 phase/fen/pieces/ply/pending 等；未建局时只含少量字段。
            return self.state()
        if t == 'new':
            if self.started and self.mode == 'robot' and self.pending:
                # [PY-game-063] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Pending motion: use recover after physically stopping and checking board'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Pending motion: use recover after physically stopping and checking board')
            # [PY-game-064] self.new｜来源：本项目 game.py，Game.new
            # 功能：校验初始 FEN、模式、AI阵营与演示棋钟；实机要求操作者确认，然后创建新 game_id 并清空本局历史。
            # 参数 m=m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
            # 返回：新局说明字符串。
            return self.new(m)
        if t == 'recover':
            # [PY-game-065] self.require｜来源：本项目 game.py，Game.require
            # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
            # 参数 *phases[1]='FAULT'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[2]='REVIEW'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[3]='GAME_OVER'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[4]='READY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[5]='VERIFY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[6]='WAIT_ACK'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[7]='EXECUTING'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 返回：无；条件不满足抛 ValueError。
            self.require('FAULT', 'REVIEW', 'GAME_OVER', 'READY', 'VERIFY', 'WAIT_ACK', 'EXECUTING')
            # [PY-game-066] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='stopped_and_checked'：字段名；此字段表示人工确认控制端已停止并核对实物
            # 返回：字段值或default；省略default时为None。
            if m.get('stopped_and_checked') is not True:
                # [PY-game-067] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Stop controller, inspect physical board, then confirm'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Stop controller, inspect physical board, then confirm')
            # [PY-game-068] self.log｜来源：本项目 game.py，Game.log
            # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
            # 参数 event='manual_recovery'：日志事件名称，例如 new、plan、commit、fault。
            # 参数 data=dict(fen=m.get('fen'), reason=m.get('reason', ''))：可JSON序列化的日志内容，例如元信息字典或错误字符串。
            # 返回：无；写入失败会向上传递异常。
            # [PY-game-069] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 fen=m.get('fen')：完整棋局FEN，含行棋方和计数
            # 参数 reason=m.get('reason', '')：人工恢复原因
            # 返回：新字典。
            # [PY-game-070] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='fen'：字段名；此字段表示完整棋局FEN，含行棋方和计数
            # 返回：字段值或default；省略default时为None。
            # [PY-game-071] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='reason'：字段名；此字段表示人工恢复原因
            # 参数 default=''：字段缺失时的备用值
            # 返回：字段值或default；省略default时为None。
            self.log('manual_recovery', dict(fen=m.get('fen'), reason=m.get('reason', '')))
            # [PY-game-072] self.new｜来源：本项目 game.py，Game.new
            # 功能：校验初始 FEN、模式、AI阵营与演示棋钟；实机要求操作者确认，然后创建新 game_id 并清空本局历史。
            # 参数 m=m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
            # 返回：新局说明字符串。
            return self.new(m)
        # [PY-game-073] self.require｜来源：本项目 game.py，Game.require
        # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
        # 参数 *phases[1]='READY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 参数 *phases[2]='WAIT_ACK'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 参数 *phases[3]='EXECUTING'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 参数 *phases[4]='VERIFY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 参数 *phases[5]='FAULT'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 参数 *phases[6]='REVIEW'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 参数 *phases[7]='GAME_OVER'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
        # 返回：无；条件不满足抛 ValueError。
        self.require('READY', 'WAIT_ACK', 'EXECUTING', 'VERIFY', 'FAULT', 'REVIEW', 'GAME_OVER')
        # [PY-game-074] self.check_session｜来源：本项目 game.py，Game.check_session
        # 功能：核对请求的 game_id 和 ply，拒绝旧棋局或旧步数的命令。
        # 参数 m=m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
        # 返回：无；不一致抛 ValueError。
        self.check_session(m)
        if t == 'observe':
            # [PY-game-075] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='stable'：字段名；此字段表示视觉是否稳定，必须严格为True
            # 返回：字段值或default；省略default时为None。
            # [PY-game-076] isinstance｜来源：Python内置函数
            # 功能：检查对象是否属于指定类型或其子类。
            # 参数 object=m.get('confidence')：被检查的对象
            # 参数 classinfo=(int, float)：允许的类型，或多个类型组成的元组
            # 返回：True/False。
            # [PY-game-077] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='confidence'：字段名；此字段表示视觉置信度，本项目门槛0.95
            # 返回：字段值或default；省略default时为None。
            if m.get('stable') is not True or not isinstance(m.get('confidence'), (int, float)) or (not 0.95 <= m['confidence'] <= 1):
                # [PY-game-078] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Need stable observation and confidence >= 0.95'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Need stable observation and confidence >= 0.95')
            # [PY-game-079] type｜来源：Python内置函数
            # 功能：取得对象的实际类型；本项目用它拒绝把bool当作int坐标。
            # 参数 object=m.get('frame_id')：被检查的值
            # 返回：类型对象。
            # [PY-game-080] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='frame_id'：字段名；此字段表示视觉帧序号，必须为递增整数
            # 返回：字段值或default；省略default时为None。
            if type(m.get('frame_id')) != int or m['frame_id'] <= self.last_frame:
                # [PY-game-081] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Stale frame_id'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Stale frame_id')
            from board import from_coordinates
            # [PY-game-082] parse｜来源：本项目 board.py，parse
            # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
            # 参数 fen=from_coordinates(m['pieces'])：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
            # [PY-game-083] from_coordinates｜来源：本项目 board.py，from_coordinates
            # 功能：将全部棋子列表转为 FEN，检查坐标范围、类别、重复格；不在这里检查将帅数量等完整合法性，需要调用 validate。
            # 参数 pieces=m['pieces']：全盘存活棋子列表，每项为 {piece,row,col}；不是只传发生变化的棋子。
            # 返回：FEN；未列出的交叉点视为空点。
            rows = parse(from_coordinates(m['pieces']))[0]
            if self.phase == 'VERIFY':
                # [PY-game-084] m.get｜来源：Python内置dict类型的方法
                # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
                # 参数 key='verification_token'：字段名；此字段表示DONE后生成的令牌，绑定这次新的走后视觉核对
                # 返回：字段值或default；省略default时为None。
                if m.get('verification_token') != self.pending['verification_token']:
                    # [PY-game-085] ValueError｜来源：Python内置异常类型
                    # 功能：构造异常；前面带raise才会真正抛出。
                    # 参数 *args[1]='Need fresh capture with verification_token from DONE'：错误说明字符串
                    # 返回：异常对象。
                    raise ValueError('Need fresh capture with verification_token from DONE')
                # [PY-game-086] parse｜来源：本项目 board.py，parse
                # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
                # 参数 fen=self.pending['expected_fen']：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
                # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
                if rows != parse(self.pending['expected_fen'])[0]:
                    self.phase = 'FAULT'
                    self.error = 'Vision mismatch after motion'
                    # [PY-game-087] self.log｜来源：本项目 game.py，Game.log
                    # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
                    # 参数 event='mismatch'：日志事件名称，例如 new、plan、commit、fault。
                    # 参数 data=m：可JSON序列化的日志内容，例如元信息字典或错误字符串。
                    # 返回：无；写入失败会向上传递异常。
                    self.log('mismatch', m)
                    # [PY-game-088] ValueError｜来源：Python内置异常类型
                    # 功能：构造异常；前面带raise才会真正抛出。
                    # 参数 *args[1]=self.error：错误说明字符串
                    # 返回：异常对象。
                    raise ValueError(self.error)
                move = self.pending['move']['uci']
                # [PY-game-089] self.commit｜来源：本项目 game.py，Game.commit
                # 功能：正式提交一手棋：同步 FEN、历史、棋钟与日志，并检查三次重复及无合法走法；只应由已确认流程调用。
                # 参数 move=move：4字符ICCS走法，例如 b2e2；不是坐标列表。
                # 返回：无；修改正式棋局。
                self.commit(move)
                self.pending = None
            else:
                # [PY-game-090] self.require｜来源：本项目 game.py，Game.require
                # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
                # 参数 *phases[1]='READY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
                # 返回：无；条件不满足抛 ValueError。
                self.require('READY')
                # [PY-game-091] parse｜来源：本项目 board.py，parse
                # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
                # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
                # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
                if rows == parse(self.fen)[0]:
                    self.last_frame = m['frame_id']
                    return 'unchanged'
                # [PY-game-092] parse｜来源：本项目 board.py，parse
                # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
                # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
                # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
                if parse(self.fen)[1] == self.ai_side:
                    # [PY-game-093] ValueError｜来源：Python内置异常类型
                    # 功能：构造异常；前面带raise才会真正抛出。
                    # 参数 *args[1]='AI turn: unexpected manual board change'：错误说明字符串
                    # 返回：异常对象。
                    raise ValueError('AI turn: unexpected manual board change')
                # [PY-game-094] self.commit｜来源：本项目 game.py，Game.commit
                # 功能：正式提交一手棋：同步 FEN、历史、棋钟与日志，并检查三次重复及无合法走法；只应由已确认流程调用。
                # 参数 move=infer_move(self.fen, m['pieces'])：4字符ICCS走法，例如 b2e2；不是坐标列表。
                # 返回：无；修改正式棋局。
                # [PY-game-095] infer_move｜来源：本项目 board.py，infer_move
                # 功能：枚举上一局面的所有合法一步，要求恰好一个结果与观察到的全盘一致，用于识别人类走子。
                # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
                # 参数 pieces=m['pieces']：全盘存活棋子列表，每项为 {piece,row,col}；不是只传发生变化的棋子。
                # 返回：唯一匹配的 ICCS 走法；零个或多个匹配都抛 ValueError。
                self.commit(infer_move(self.fen, m['pieces']))
            self.last_frame = m['frame_id']
            return 'observation accepted'
        if t == 'human_move':
            # [PY-game-096] self.require｜来源：本项目 game.py，Game.require
            # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
            # 参数 *phases[1]='READY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 返回：无；条件不满足抛 ValueError。
            self.require('READY')
            if self.mode != 'virtual':
                # [PY-game-097] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Robot mode uses observe for human moves'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Robot mode uses observe for human moves')
            # [PY-game-098] parse｜来源：本项目 board.py，parse
            # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
            # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
            if parse(self.fen)[1] == self.ai_side:
                # [PY-game-099] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='It is AI turn'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('It is AI turn')
            # [PY-game-100] self.commit｜来源：本项目 game.py，Game.commit
            # 功能：正式提交一手棋：同步 FEN、历史、棋钟与日志，并检查三次重复及无合法走法；只应由已确认流程调用。
            # 参数 move=m['move']：4字符ICCS走法，例如 b2e2；不是坐标列表。
            # 返回：无；修改正式棋局。
            self.commit(m['move'])
            return 'human move committed'
        if t == 'ai':
            # [PY-game-101] self.require｜来源：本项目 game.py，Game.require
            # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
            # 参数 *phases[1]='READY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 返回：无；条件不满足抛 ValueError。
            self.require('READY')
            # [PY-game-102] parse｜来源：本项目 board.py，parse
            # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
            # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
            if parse(self.fen)[1] != self.ai_side:
                # [PY-game-103] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='It is human turn'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('It is human turn')
            # [PY-game-104] time.monotonic｜来源：Python标准库time
            # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：浮点秒数。
            start = time.monotonic()
            try:
                # [PY-game-105] self.engine.best_move｜来源：本项目 engine.py，DemoEngine.best_move / Pikafish.best_move
                # 功能：按实际引擎类型取一步；Demo只取首个合法步，Pikafish使用UCI搜索。
                # 参数 root=self.root：当前对局初始FEN，配合完整moves传给引擎。
                # 参数 moves=self.moves：从root开始已经正式提交的ICCS走法列表，按先后排序。
                # 参数 ms=int(m.get('ms', 1000))：单步搜索时间（毫秒），默认1000；真实引擎允许50..10000，演示引擎忽略。
                # 参数 current=self.fen：当前正式FEN，用于复核结果；连续对局应传入，默认None时退回root。
                # 返回：ICCS一步或None；真实引擎会再次检查合法性。
                # [PY-game-106] int｜来源：Python内置类型int的构造调用
                # 功能：把可转换的值变成整数。
                # 参数 x=m.get('ms', 1000)：数字、数字字符串或布尔值；True转1、False转0
                # 返回：整数；转换失败抛异常。
                # [PY-game-107] m.get｜来源：Python内置dict类型的方法
                # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
                # 参数 key='ms'：字段名；此字段表示搜索时长，单位毫秒
                # 参数 default=1000：字段缺失时的备用值
                # 返回：字段值或default；省略default时为None。
                move = self.engine.best_move(self.root, self.moves, int(m.get('ms', 1000)), current=self.fen)
            except Exception as exc:
                self.phase = 'FAULT'
                # [PY-game-108] str｜来源：Python内置类型str的构造调用
                # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                # 参数 object=exc：要转换的对象
                # 返回：字符串。
                self.error = 'Engine failure: ' + str(exc)
                # [PY-game-109] self.log｜来源：本项目 game.py，Game.log
                # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
                # 参数 event='fault'：日志事件名称，例如 new、plan、commit、fault。
                # 参数 data=self.error：可JSON序列化的日志内容，例如元信息字典或错误字符串。
                # 返回：无；写入失败会向上传递异常。
                self.log('fault', self.error)
                raise
            # [PY-game-110] max｜来源：Python内置函数
            # 功能：返回参数中的较大值；与0比较可把负时间截为0。
            # 参数 *values[1]=0：参与比较的数值；本项目传入多个位置参数
            # 参数 *values[2]=self.clocks[self.ai_side] - (time.monotonic() - start)：参与比较的数值；本项目传入多个位置参数
            # 返回：最大值。
            # [PY-game-111] time.monotonic｜来源：Python标准库time
            # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：浮点秒数。
            self.clocks[self.ai_side] = max(0, self.clocks[self.ai_side] - (time.monotonic() - start))
            # [PY-game-112] time.monotonic｜来源：Python标准库time
            # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：浮点秒数。
            self.last_tick = time.monotonic()
            if self.clocks[self.ai_side] <= 0:
                self.phase = 'GAME_OVER'
                self.error = 'AI clock expired'
                return self.error
            if not move:
                # [PY-game-113] next｜来源：Python内置函数
                # 功能：取迭代器的下一项；本项目也用它检查是否存在合法走法。
                # 参数 iterator=legal_moves(self.fen)：要读取的生成器/迭代器
                # 参数 default=None：迭代器耗尽时的备用值，此处通常为None
                # 返回：下一项；耗尽时返回传入的default。
                # [PY-game-114] legal_moves｜来源：本项目 board.py，legal_moves
                # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
                # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
                # 返回：逐项产生4字符 ICCS 走法。
                if next(legal_moves(self.fen), None):
                    # [PY-game-115] RuntimeError｜来源：Python内置异常类型
                    # 功能：构造异常；前面带raise才会真正抛出。
                    # 参数 *args[1]='Engine returned no move in nonterminal position'：错误说明字符串
                    # 返回：异常对象。
                    raise RuntimeError('Engine returned no move in nonterminal position')
                self.phase = 'GAME_OVER'
                return 'AI has no legal move'
            # [PY-game-116] apply｜来源：本项目 board.py，apply
            # 功能：复核一步合法走法，生成走后 FEN、吃子信息和中文记谱；不修改传入棋局。
            # 参数 fen=self.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 参数 move=move：4字符ICCS走法，例如 b2e2；不是坐标列表。
            # 返回：(new_fen, meta)，meta 含 uci/text/from/to/piece/capture。
            expected, meta = apply(self.fen, move)
            if self.mode == 'virtual':
                # [PY-game-117] self.commit｜来源：本项目 game.py，Game.commit
                # 功能：正式提交一手棋：同步 FEN、历史、棋钟与日志，并检查三次重复及无合法走法；只应由已确认流程调用。
                # 参数 move=move：4字符ICCS走法，例如 b2e2；不是坐标列表。
                # 返回：无；修改正式棋局。
                self.commit(move)
                return meta
            # [PY-game-118] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 command_id=uuid.uuid4().hex：机械动作计划的唯一编号
            # 参数 game_id=self.game_id：当前棋局唯一编号
            # 参数 ply=len(self.moves)：已正式提交的半回合数
            # 参数 move=meta：ICCS一步或走法元信息，视该响应上下文而定
            # 参数 before_fen=self.fen：计划执行前的正式FEN
            # 参数 expected_fen=expected：计划预计执行后的FEN，尚未代表实物完成
            # 参数 pieces=coordinates(expected)：全部存活棋子的{piece,row,col}列表，不是仅变化部分
            # 参数 deadline=time.monotonic() + 5：本机单调时钟截止点，不是可跨机器解释的时间戳
            # 返回：新字典。
            # [PY-game-119] uuid.uuid4｜来源：Python标准库uuid
            # 功能：随机生成UUID；后面的.hex是属性，取得32位十六进制字符串。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：UUID对象。
            # [PY-game-120] len｜来源：Python内置函数
            # 功能：取得容器、字符串或字节序列的元素数量。
            # 参数 obj=self.moves：要计数的对象；字节序列按字节数计，字符串按字符数计
            # 返回：整数长度。
            # [PY-game-121] coordinates｜来源：本项目 board.py，coordinates
            # 功能：把 FEN 转成全部存活棋子的类别和 row/col 列表，不包含空点。
            # 参数 fen=expected：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：[{piece, row, col}, ...]。
            # [PY-game-122] time.monotonic｜来源：Python标准库time
            # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：浮点秒数。
            self.pending = dict(command_id=uuid.uuid4().hex, game_id=self.game_id, ply=len(self.moves), move=meta, before_fen=self.fen, expected_fen=expected, pieces=coordinates(expected), deadline=time.monotonic() + 5)
            self.phase = 'WAIT_ACK'
            # [PY-game-123] self.log｜来源：本项目 game.py，Game.log
            # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
            # 参数 event='plan'：日志事件名称，例如 new、plan、commit、fault。
            # 参数 data=self.pending：可JSON序列化的日志内容，例如元信息字典或错误字符串。
            # 返回：无；写入失败会向上传递异常。
            self.log('plan', self.pending)
            # [PY-game-124] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 type='PLAN'：动作类型；PLAN在响应里表示待执行计划
            # 参数 **self.pending：展开当前待执行计划的编号、走法、执行前/预期FEN、全盘和deadline等字段
            # 返回：新字典。
            return dict(type='PLAN', **self.pending)
        if t in ('ack', 'done'):
            # [PY-game-125] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='command_id'：字段名；此字段表示机械动作计划的唯一编号
            # 返回：字段值或default；省略default时为None。
            if not self.pending or m.get('command_id') != self.pending['command_id']:
                # [PY-game-126] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Unknown command_id'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Unknown command_id')
            if t == 'ack':
                # [PY-game-127] self.require｜来源：本项目 game.py，Game.require
                # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
                # 参数 *phases[1]='WAIT_ACK'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
                # 返回：无；条件不满足抛 ValueError。
                self.require('WAIT_ACK')
                self.phase = 'EXECUTING'
                # [PY-game-128] time.monotonic｜来源：Python标准库time
                # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：浮点秒数。
                self.pending['deadline'] = time.monotonic() + 60
            else:
                # [PY-game-129] self.require｜来源：本项目 game.py，Game.require
                # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
                # 参数 *phases[1]='EXECUTING'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
                # 返回：无；条件不满足抛 ValueError。
                self.require('EXECUTING')
                # [PY-game-130] m.get｜来源：Python内置dict类型的方法
                # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
                # 参数 key='success'：字段名；此字段表示控制端是否实际执行成功；必须为布尔True才进入复核
                # 返回：字段值或default；省略default时为None。
                if m.get('success') is not True:
                    self.phase = 'FAULT'
                    self.error = 'Controller reported failure'
                    # [PY-game-131] self.log｜来源：本项目 game.py，Game.log
                    # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
                    # 参数 event='fault'：日志事件名称，例如 new、plan、commit、fault。
                    # 参数 data=m：可JSON序列化的日志内容，例如元信息字典或错误字符串。
                    # 返回：无；写入失败会向上传递异常。
                    self.log('fault', m)
                    return 'controller failed'
                self.phase = 'VERIFY'
                # [PY-game-132] uuid.uuid4｜来源：Python标准库uuid
                # 功能：随机生成UUID；后面的.hex是属性，取得32位十六进制字符串。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：UUID对象。
                self.pending['verification_token'] = uuid.uuid4().hex
                # [PY-game-133] time.monotonic｜来源：Python标准库time
                # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：浮点秒数。
                self.pending['deadline'] = time.monotonic() + 15
            # [PY-game-134] self.log｜来源：本项目 game.py，Game.log
            # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
            # 参数 event=t：日志事件名称，例如 new、plan、commit、fault。
            # 参数 data=m：可JSON序列化的日志内容，例如元信息字典或错误字符串。
            # 返回：无；写入失败会向上传递异常。
            self.log(t, m)
            return t + ' accepted'
        if t == 'undo':
            # [PY-game-135] self.require｜来源：本项目 game.py，Game.require
            # 功能：要求已经开局且 phase 在允许列表内，否则拒绝当前动作。
            # 参数 *phases[1]='READY'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[2]='REVIEW'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 参数 *phases[3]='GAME_OVER'：可接受的阶段字符串，可传多个，例如 READY、VERIFY。
            # 返回：无；条件不满足抛 ValueError。
            self.require('READY', 'REVIEW', 'GAME_OVER')
            if self.mode != 'virtual':
                # [PY-game-136] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Physical undo requires manual restoration and recover'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Physical undo requires manual restoration and recover')
            # [PY-game-137] m.get｜来源：Python内置dict类型的方法
            # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
            # 参数 key='count'：字段名；此字段表示虚拟悔棋的半回合数量
            # 参数 default=2：字段缺失时的备用值
            # 返回：字段值或default；省略default时为None。
            n = m.get('count', 2)
            # [PY-game-138] type｜来源：Python内置函数
            # 功能：取得对象的实际类型；本项目用它拒绝把bool当作int坐标。
            # 参数 object=n：被检查的值
            # 返回：类型对象。
            # [PY-game-139] len｜来源：Python内置函数
            # 功能：取得容器、字符串或字节序列的元素数量。
            # 参数 obj=self.history：要计数的对象；字节序列按字节数计，字符串按字符数计
            # 返回：整数长度。
            if type(n) != int or n < 1 or n > len(self.history):
                # [PY-game-140] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Invalid undo count'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Invalid undo count')
            old = self.history[-n]
            self.fen = old['before']
            self.clocks = old['clocks']
            self.history = self.history[:-n]
            self.moves = self.moves[:-n]
            self.phase = 'READY'
            self.error = ''
            # [PY-game-141] time.monotonic｜来源：Python标准库time
            # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：浮点秒数。
            self.last_tick = time.monotonic()
            # [PY-game-142] self.log｜来源：本项目 game.py，Game.log
            # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
            # 参数 event='undo'：日志事件名称，例如 new、plan、commit、fault。
            # 参数 data=n：可JSON序列化的日志内容，例如元信息字典或错误字符串。
            # 返回：无；写入失败会向上传递异常。
            self.log('undo', n)
            return 'undone'
        # [PY-game-143] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Unknown type: ' + str(t)：错误说明字符串
        # 返回：异常对象。
        # [PY-game-144] str｜来源：Python内置类型str的构造调用
        # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
        # 参数 object=t：要转换的对象
        # 返回：字符串。
        raise ValueError('Unknown type: ' + str(t))

    def new(self, m):
        """功能：校验初始 FEN、模式、AI阵营与演示棋钟；实机要求操作者确认，然后创建新 game_id 并清空本局历史。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。

        返回：新局说明字符串。"""
        # [PY-game-145] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='mode'：字段名；此字段表示virtual虚拟或robot实机协议
        # 参数 default='virtual'：字段缺失时的备用值
        # 返回：字段值或default；省略default时为None。
        mode = m.get('mode', 'virtual')
        # [PY-game-146] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='ai_side'：字段名；此字段表示AI阵营w红/b黑
        # 参数 default='b'：字段缺失时的备用值
        # 返回：字段值或default；省略default时为None。
        ai = m.get('ai_side', 'b')
        # [PY-game-147] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='fen'：字段名；此字段表示完整棋局FEN，含行棋方和计数
        # 参数 default=START：字段缺失时的备用值
        # 返回：字段值或default；省略default时为None。
        fen = m.get('fen', START)
        # [PY-game-148] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='seconds'：字段名；此字段表示每方初始演示棋钟秒数，默认600
        # 参数 default=600：字段缺失时的备用值
        # 返回：字段值或default；省略default时为None。
        seconds = m.get('seconds', 600)
        if mode not in ('virtual', 'robot') or ai not in ('w', 'b'):
            # [PY-game-149] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Invalid mode/ai_side'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Invalid mode/ai_side')
        # [PY-game-150] isinstance｜来源：Python内置函数
        # 功能：检查对象是否属于指定类型或其子类。
        # 参数 object=seconds：被检查的对象
        # 参数 classinfo=(int, float)：允许的类型，或多个类型组成的元组
        # 返回：True/False。
        if not isinstance(seconds, (int, float)) or not 10 <= seconds <= 86400:
            # [PY-game-151] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Invalid clock duration'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Invalid clock duration')
        # [PY-game-152] validate｜来源：本项目 board.py，validate
        # 功能：检查 FEN 结构、棋子数量、将帅是否存在及位置约束，拒绝上一方留下将帅被攻击等非法初始局面。
        # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：校验通过的原 FEN；不合法时抛 ValueError。
        validate(fen)
        # [PY-game-153] m.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key='board_confirmed'：字段名；此字段表示人工确认真实初始棋盘，robot新局要求True
        # 返回：字段值或default；省略default时为None。
        if mode == 'robot' and m.get('board_confirmed') is not True:
            # [PY-game-154] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Confirm initial physical board first'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Confirm initial physical board first')
        # [PY-game-155] uuid.uuid4｜来源：Python标准库uuid
        # 功能：随机生成UUID；后面的.hex是属性，取得32位十六进制字符串。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：UUID对象。
        self.game_id = uuid.uuid4().hex
        self.started = True
        self.mode = mode
        self.ai_side = ai
        self.root = self.fen = fen
        self.moves = []
        self.history = []
        self.pending = None
        self.error = ''
        self.phase = 'READY'
        self.last_frame = -1
        # [PY-game-156] float｜来源：Python内置类型float的构造调用
        # 功能：把数字转换为浮点数，便于保存带小数的棋钟。
        # 参数 x=seconds：要转换的数值
        # 返回：浮点数。
        # [PY-game-157] float｜来源：Python内置类型float的构造调用
        # 功能：把数字转换为浮点数，便于保存带小数的棋钟。
        # 参数 x=seconds：要转换的数值
        # 返回：浮点数。
        self.clocks = {'w': float(seconds), 'b': float(seconds)}
        # [PY-game-158] time.monotonic｜来源：Python标准库time
        # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：浮点秒数。
        self.last_tick = time.monotonic()
        # [PY-game-159] self.log｜来源：本项目 game.py，Game.log
        # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
        # 参数 event='new'：日志事件名称，例如 new、plan、commit、fault。
        # 参数 data=m：可JSON序列化的日志内容，例如元信息字典或错误字符串。
        # 返回：无；写入失败会向上传递异常。
        self.log('new', m)
        return 'new game; initial position explicitly set'
