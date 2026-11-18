"""Windows/Pi 通用的引擎管道读取；保留 CH-RO 的 readline 接口。"""  # 主线程不直接阻塞在操作系统管道上。
import queue  # 将引擎输出送到可超时队列。
import threading  # 单个线程负责读取每条管道。
from typing import TextIO  # 标注文本管道类型。


class TimedLineReader:  # 提供与旧代码兼容的有限等待读取。
    def __init__(self, stream: TextIO, wait_seconds: float = 0.1) -> None:  # 保存原始管道并启动读取线程。
        self.stream = stream  # 只有后台线程读取这个原始对象。
        self.wait_seconds = wait_seconds  # 一次 readline 最多等待的秒数。
        self.lines = queue.Queue()  # 保存完整行，避免丢失 bestmove。
        self.closed = threading.Event()  # 标记底层进程输出已经结束。
        self.worker = threading.Thread(target=self._pump, daemon=True)  # 不阻止主程序退出。
        self.worker.start()  # 开始持续消费 stdout。

    def _pump(self) -> None:  # 按行读取操作系统管道。
        try:  # 确保退出时标记结束并释放句柄。
            for line in self.stream:  # 只有此处可能阻塞，网页线程不会卡在这里。
                self.lines.put(line)  # 每个输出行只排队一次。
        finally:  # EOF 或读取错误时执行清理。
            self.closed.set()  # 告知调用方已经读到末尾。
            self.stream.close()  # 关闭原始文件句柄。

    def readline(self) -> str:  # 兼容原 CH-RO 的 stdout.readline 调用。
        try:  # 使用带超时的队列获取。
            return self.lines.get(timeout=self.wait_seconds)  # 返回下一条完整输出。
        except queue.Empty:  # 当前还没有输出。
            return ''  # 让上层循环重新检查截止时间和进程状态。


def watch_stderr(stream: TextIO, logger) -> threading.Thread:  # 消费标准错误，避免长时间运行后堵塞。
    def pump() -> None:  # 错误流与标准输出互相独立。
        try:  # 即使进程异常结束也释放句柄。
            for line in stream:  # 读取引擎错误行。
                logger.warning('Pikafish: %s', line.rstrip())  # 将网络缺失等问题写入后台日志。
        finally:  # 输出结束后清理。
            stream.close()  # 关闭原始 stderr。
    worker = threading.Thread(target=pump, daemon=True)  # 错误消费线程不阻止退出。
    worker.start()  # 在搜索开始前启动。
    return worker  # 便于后续进一步管理线程。
