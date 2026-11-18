# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""引擎适配层：DemoEngine 仅取第一个合法走法；Pikafish 通过持久子进程使用 UCI 协议搜索。"""
import subprocess  # 导入启动并管理引擎子进程。
import threading  # 导入棋局/引擎锁以及串口和读管道后台线程。
import queue  # 导入引擎输出的线程安全队列。
import time  # 导入单调时钟、搜索/动作超时与日志时间。
from pathlib import Path  # 导入跨平台文件路径。
from board import legal_moves  # 导入本项目的坐标/FEN转换和规则函数。

class DemoEngine:  # 定义演示替身：返回第一个合法走法，便于离线验证链路，不能代表AI棋力。
    """演示替身：返回第一个合法走法，便于离线验证链路，不能代表AI棋力。"""
    name = 'DEMO first legal move - NOT AI'  # 标识这是第一个合法走法演示，不能当作真实AI。

    def best_move(self, root, moves, ms=1000, current=None):
        """功能：取当前局面的第一个合法走法，仅供链路测试；不会按搜索时长思考。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            root：当前对局初始FEN，配合完整moves传给引擎。
            moves：从root开始已经正式提交的ICCS走法列表，按先后排序。
            ms：单步搜索时间（毫秒），默认1000；真实引擎允许50..10000，演示引擎忽略。
            current：当前正式FEN，用于复核结果；连续对局应传入，默认None时退回root。

        返回：ICCS 走法；无合法走法返回 None。"""
        # [PY-engine-001] next｜来源：Python内置函数
        # 功能：取迭代器的下一项；本项目也用它检查是否存在合法走法。
        # 参数 iterator=legal_moves(current or root)：要读取的生成器/迭代器
        # 参数 default=None：迭代器耗尽时的备用值，此处通常为None
        # 返回：下一项；耗尽时返回传入的default。
        # [PY-engine-002] legal_moves｜来源：本项目 board.py，legal_moves
        # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
        # 参数 fen=current or root：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        # 返回：逐项产生4字符 ICCS 走法。
        return next(legal_moves(current or root), None)

    def close(self):
        """功能：演示引擎没有子进程，此处为空操作，以便与 Pikafish 使用统一接口。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。

        返回：无。"""
        pass

class Pikafish:  # 定义真实引擎进程适配器。
    """真实引擎进程适配器。p为进程；lines为输出队列；lock避免两次搜索同时读写；name为引擎路径。"""

    def __init__(self, path, nnue=None, threads=2, hash_mb=128, args=None):
        """功能：启动并持有 UCI 子进程，建立读队列，完成 uci/isready 握手并设置线程、Hash和可选权重；失败时清理子进程。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            path：Pikafish可执行文件路径；测试中也可传Python解释器路径。
            nnue：匹配该引擎版本的权重文件路径，None表示使用引擎默认配置。
            threads：引擎搜索线程数，默认2。
            hash_mb：引擎Hash内存大小，单位MB，默认128。
            args：额外子进程启动参数列表，默认None；测试用于让Python运行fake_uci.py。

        返回：无；初始化失败向上传递异常。"""
        # [PY-engine-003] str｜来源：Python内置类型str的构造调用
        # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
        # 参数 object=Path(path).resolve()：要转换的对象
        # 返回：字符串。
        # [PY-engine-004] Path｜来源：Python标准库pathlib.Path
        # 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
        # 参数 *pathsegments[1]=path：文件或目录路径的各部分
        # 返回：适合当前操作系统的Path对象。
        # [PY-engine-005] Path(path).resolve｜来源：Python标准库pathlib.Path.resolve
        # 功能：将当前Path转换为规范绝对路径。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：新的绝对Path对象。
        path = str(Path(path).resolve())
        self.name = path
        # [PY-engine-006] threading.Lock｜来源：Python标准库threading
        # 功能：创建普通互斥锁，避免引擎同时执行两次搜索。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：锁对象。
        self.lock = threading.Lock()
        # [PY-engine-007] queue.Queue｜来源：Python标准库queue.Queue
        # 功能：创建线程安全队列，连接引擎读线程和等待线程。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：队列对象；本项目使用默认无容量上限。
        self.lines = queue.Queue()
        self.p = None
        try:
            # [PY-engine-008] subprocess.Popen｜来源：Python标准库subprocess.Popen
            # 功能：启动并持有外部引擎子进程，创建双向文本管道。
            # 参数 args=[path] + (args or [])：命令与参数列表；首项是引擎文件或测试用Python解释器
            # 参数 cwd=str(Path(path).parent)：子进程工作目录；此处是引擎文件所在目录
            # 参数 stdin=subprocess.PIPE：PIPE建立父进程向引擎写命令的管道
            # 参数 stdout=subprocess.PIPE：PIPE建立读取引擎输出的管道
            # 参数 stderr=subprocess.STDOUT：STDOUT把标准错误合并到标准输出，方便诊断
            # 参数 text=True：True让管道按文本读写
            # 参数 encoding='utf-8'：管道文本编码，此处UTF-8
            # 参数 errors='replace'：replace用替换符处理异常编码字节
            # 参数 bufsize=1：1表示文本行缓冲；发命令后仍显式flush
            # 返回：Popen进程对象。
            # [PY-engine-009] str｜来源：Python内置类型str的构造调用
            # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
            # 参数 object=Path(path).parent：要转换的对象
            # 返回：字符串。
            # [PY-engine-010] Path｜来源：Python标准库pathlib.Path
            # 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
            # 参数 *pathsegments[1]=path：文件或目录路径的各部分
            # 返回：适合当前操作系统的Path对象。
            self.p = subprocess.Popen([path] + (args or []), cwd=str(Path(path).parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)

            def reader():
                """功能：后台持续读取引擎输出放入队列；输出结束放入 None，让等待方识别引擎退出。

                参数：
                    无显式参数；可能读取外层闭包或命令行。

                返回：无；通过 self.lines 队列传递结果。"""
                for line in self.p.stdout:
                    # [PY-engine-011] self.lines.put｜来源：Python标准库queue.Queue.put
                    # 功能：把一条引擎输出或结束标记放入队列。
                    # 参数 item=line.rstrip()：输出文本；None表示引擎输出流已结束
                    # 返回：None。
                    # [PY-engine-012] line.rstrip｜来源：Python内置str类型的方法
                    # 功能：移除右侧空白；此处用于移除引擎输出的换行。
                    # 参数 本次没有显式参数；省略的可选参数使用默认值
                    # 返回：新字符串。
                    self.lines.put(line.rstrip())
                # [PY-engine-013] self.lines.put｜来源：Python标准库queue.Queue.put
                # 功能：把一条引擎输出或结束标记放入队列。
                # 参数 item=None：输出文本；None表示引擎输出流已结束
                # 返回：None。
                self.lines.put(None)
            # [PY-engine-014] threading.Thread｜来源：Python标准库threading.Thread
            # 功能：创建后台线程对象；此构造调用还不会启动线程。
            # 参数 target=reader：线程启动后执行的函数对象；这里不加括号调用它
            # 参数 daemon=True：True表示守护线程，主程序退出时不等待它继续运行
            # 返回：Thread对象。
            # [PY-engine-015] threading.Thread(target=reader, daemon=True).start｜来源：Python标准库threading.Thread.start
            # 功能：启动已建立的线程，使target在后台执行。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：None。
            threading.Thread(target=reader, daemon=True).start()
            # [PY-engine-016] self.send｜来源：本项目 engine.py，Pikafish.send
            # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
            # 参数 s='uci'：单条UCI命令字符串，不含换行，例如 isready。
            # 返回：无。
            self.send('uci')
            # [PY-engine-017] self.until｜来源：本项目 engine.py，Pikafish.until
            # 功能：从后台队列中有限等待，直到谓词匹配；超时或进程退出时抛异常。
            # 参数 predicate=lambda s: s == 'uciok'：接受一行引擎输出字符串、返回是否匹配的函数。
            # 参数 seconds=15：等待UCI响应的最长秒数。
            # 返回：第一条满足 predicate 的输出字符串。
            self.until(lambda s: s == 'uciok', 15)
            # [PY-engine-018] self.send｜来源：本项目 engine.py，Pikafish.send
            # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
            # 参数 s=f'setoption name Threads value {threads}'：单条UCI命令字符串，不含换行，例如 isready。
            # 返回：无。
            self.send(f'setoption name Threads value {threads}')
            # [PY-engine-019] self.send｜来源：本项目 engine.py，Pikafish.send
            # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
            # 参数 s=f'setoption name Hash value {hash_mb}'：单条UCI命令字符串，不含换行，例如 isready。
            # 返回：无。
            self.send(f'setoption name Hash value {hash_mb}')
            if nnue:
                # [PY-engine-020] self.send｜来源：本项目 engine.py，Pikafish.send
                # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
                # 参数 s='setoption name EvalFile value ' + str(Path(nnue).resolve())：单条UCI命令字符串，不含换行，例如 isready。
                # 返回：无。
                # [PY-engine-021] str｜来源：Python内置类型str的构造调用
                # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                # 参数 object=Path(nnue).resolve()：要转换的对象
                # 返回：字符串。
                # [PY-engine-022] Path｜来源：Python标准库pathlib.Path
                # 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
                # 参数 *pathsegments[1]=nnue：文件或目录路径的各部分
                # 返回：适合当前操作系统的Path对象。
                # [PY-engine-023] Path(nnue).resolve｜来源：Python标准库pathlib.Path.resolve
                # 功能：将当前Path转换为规范绝对路径。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：新的绝对Path对象。
                self.send('setoption name EvalFile value ' + str(Path(nnue).resolve()))
            # [PY-engine-024] self.send｜来源：本项目 engine.py，Pikafish.send
            # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
            # 参数 s='isready'：单条UCI命令字符串，不含换行，例如 isready。
            # 返回：无。
            self.send('isready')
            # [PY-engine-025] self.until｜来源：本项目 engine.py，Pikafish.until
            # 功能：从后台队列中有限等待，直到谓词匹配；超时或进程退出时抛异常。
            # 参数 predicate=lambda s: s == 'readyok'：接受一行引擎输出字符串、返回是否匹配的函数。
            # 参数 seconds=30：等待UCI响应的最长秒数。
            # 返回：第一条满足 predicate 的输出字符串。
            self.until(lambda s: s == 'readyok', 30)
        except Exception:
            # [PY-engine-026] self.close｜来源：本项目 engine.py，Pikafish.close
            # 功能：结束并等待该实例持有的子进程，关闭管道；目前使用 kill，不向其他 Python 程序发送命令。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：无。
            self.close()
            raise

    def send(self, s):
        """功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            s：单条UCI命令字符串，不含换行，例如 isready。

        返回：无。"""
        if '\n' in s or '\r' in s:
            # [PY-engine-027] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='UCI newline injection'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('UCI newline injection')
        # [PY-engine-028] self.p.stdin.write｜来源：Python标准库io.TextIOBase/文本文件对象
        # 功能：向已打开的文本文件或引擎输入管道写入字符串。
        # 参数 s=s + '\n'：待写文本；UCI命令和JSONL日志都显式加换行
        # 返回：写入字符数；写入不一定立即刷新。
        self.p.stdin.write(s + '\n')
        # [PY-engine-029] self.p.stdin.flush｜来源：Python标准库io.IOBase/文件对象
        # 功能：把尚在用户态缓冲中的内容刷新到下层流。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：None。
        self.p.stdin.flush()

    def until(self, predicate, seconds):
        """功能：从后台队列中有限等待，直到谓词匹配；超时或进程退出时抛异常。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            predicate：接受一行引擎输出字符串、返回是否匹配的函数。
            seconds：等待UCI响应的最长秒数。

        返回：第一条满足 predicate 的输出字符串。"""
        # [PY-engine-030] time.monotonic｜来源：Python标准库time
        # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：浮点秒数。
        deadline = time.monotonic() + seconds
        while True:
            try:
                # [PY-engine-031] self.lines.get｜来源：Python标准库queue.Queue.get
                # 功能：从队列取一项，必要时等待；超时抛queue.Empty。
                # 参数 timeout=max(0, deadline - time.monotonic())：最长等待秒数，不是毫秒
                # 返回：队列中最早放入的项。
                # [PY-engine-032] max｜来源：Python内置函数
                # 功能：返回参数中的较大值；与0比较可把负时间截为0。
                # 参数 *values[1]=0：参与比较的数值；本项目传入多个位置参数
                # 参数 *values[2]=deadline - time.monotonic()：参与比较的数值；本项目传入多个位置参数
                # 返回：最大值。
                # [PY-engine-033] time.monotonic｜来源：Python标准库time
                # 功能：读取只用于计算时间间隔的单调时钟；不能当作日期时间戳。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：浮点秒数。
                s = self.lines.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                # [PY-engine-034] TimeoutError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='UCI engine timeout'：错误说明字符串
                # 返回：异常对象。
                raise TimeoutError('UCI engine timeout')
            if s is None:
                # [PY-engine-035] RuntimeError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Engine exited; inspect binary and NNUE compatibility'：错误说明字符串
                # 返回：异常对象。
                raise RuntimeError('Engine exited; inspect binary and NNUE compatibility')
            # [PY-engine-036] predicate｜来源：由调用方传给 engine.py:Pikafish.until 的回调；本项目在engine.py中用lambda定义
            # 功能：检查刚读到的引擎输出是否是uciok、readyok或bestmove。
            # 参数 s=s：刚从self.lines取出的单行引擎输出文本
            # 返回：True表示找到目标输出。
            if predicate(s):
                return s

    def best_move(self, root, moves, ms=1000, current=None):
        """功能：发送初始 FEN 和完整历史，按毫秒限制搜索，读取 bestmove 并再次校验合法性；任何异常后关闭该引擎，不自动重播。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            root：当前对局初始FEN，配合完整moves传给引擎。
            moves：从root开始已经正式提交的ICCS走法列表，按先后排序。
            ms：单步搜索时间（毫秒），默认1000；真实引擎允许50..10000，演示引擎忽略。
            current：当前正式FEN，用于复核结果；连续对局应传入，默认None时退回root。

        返回：合法 ICCS 走法，或终局标记对应的 None。"""
        if not 50 <= ms <= 10000:
            # [PY-engine-037] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Search time must be 50..10000 ms'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Search time must be 50..10000 ms')
        with self.lock:
            try:
                # [PY-engine-038] self.send｜来源：本项目 engine.py，Pikafish.send
                # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
                # 参数 s='position fen ' + root + (' moves ' + ' '.join(moves) if moves else '')：单条UCI命令字符串，不含换行，例如 isready。
                # 返回：无。
                # [PY-engine-039] ' '.join｜来源：Python内置str类型的方法
                # 功能：使用调用者字符串作为分隔符连接各项。
                # 参数 iterable=moves：待连接的字符串序列
                # 返回：拼接后的字符串。
                self.send('position fen ' + root + (' moves ' + ' '.join(moves) if moves else ''))
                # [PY-engine-040] self.send｜来源：本项目 engine.py，Pikafish.send
                # 功能：向引擎 stdin 发送一条 UCI 命令并立即刷新；拒绝包含换行的命令。
                # 参数 s=f'go movetime {ms}'：单条UCI命令字符串，不含换行，例如 isready。
                # 返回：无。
                self.send(f'go movetime {ms}')
                # [PY-engine-041] self.until｜来源：本项目 engine.py，Pikafish.until
                # 功能：从后台队列中有限等待，直到谓词匹配；超时或进程退出时抛异常。
                # 参数 predicate=lambda s: s.startswith('bestmove ')：接受一行引擎输出字符串、返回是否匹配的函数。
                # 参数 seconds=ms / 1000 + 8：等待UCI响应的最长秒数。
                # 返回：第一条满足 predicate 的输出字符串。
                # [PY-engine-042] s.startswith｜来源：Python内置str类型的方法
                # 功能：判断字符串是否具有指定前缀。
                # 参数 prefix='bestmove '：要匹配的开头文本
                # 返回：True/False。
                s = self.until(lambda s: s.startswith('bestmove '), ms / 1000 + 8)
                # [PY-engine-043] s.split｜来源：Python内置str类型的方法
                # 功能：按分隔符切开文本；未传分隔符时按连续空白切分。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：字符串列表。
                move = s.split()[1]
                if move in ('(none)', '0000'):
                    return None
                # [PY-engine-044] legal_moves｜来源：本项目 board.py，legal_moves
                # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
                # 参数 fen=current or root：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
                # 返回：逐项产生4字符 ICCS 走法。
                if move not in legal_moves(current or root):
                    # [PY-engine-045] RuntimeError｜来源：Python内置异常类型
                    # 功能：构造异常；前面带raise才会真正抛出。
                    # 参数 *args[1]='Engine returned illegal move'：错误说明字符串
                    # 返回：异常对象。
                    raise RuntimeError('Engine returned illegal move')
                return move
            except Exception:
                # [PY-engine-046] self.close｜来源：本项目 engine.py，Pikafish.close
                # 功能：结束并等待该实例持有的子进程，关闭管道；目前使用 kill，不向其他 Python 程序发送命令。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：无。
                self.close()
                raise

    def close(self):
        """功能：结束并等待该实例持有的子进程，关闭管道；目前使用 kill，不向其他 Python 程序发送命令。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。

        返回：无。"""
        if self.p:
            # [PY-engine-047] self.p.poll｜来源：Python标准库subprocess.Popen.poll
            # 功能：非阻塞检查子进程是否已经退出。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：仍运行返回None；已退出返回退出码。
            if self.p.poll() is None:
                # [PY-engine-048] self.p.kill｜来源：Python标准库subprocess.Popen.kill
                # 功能：终止这个实例持有的引擎子进程。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：None。
                self.p.kill()
            # [PY-engine-049] self.p.wait｜来源：Python标准库subprocess.Popen.wait
            # 功能：等待子进程退出并回收资源。
            # 参数 timeout=5：最多等待秒数
            # 返回：退出码；超时抛TimeoutExpired。
            self.p.wait(timeout=5)
            for stream in (self.p.stdin, self.p.stdout):
                if stream:
                    # [PY-engine-050] stream.close｜来源：Python标准库io.IOBase/文件对象
                    # 功能：关闭文件或管道，释放句柄。
                    # 参数 本次没有显式参数；省略的可选参数使用默认值
                    # 返回：None。
                    stream.close()
