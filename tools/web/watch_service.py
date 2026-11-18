# -*- coding: utf-8 -*-  # 所有日志和说明采用中文 UTF-8。
"""监督网页子进程：退出重启、退避、单实例锁、轮转日志和主动停止。"""  # 不恢复或续走机械臂棋局。
import argparse  # 读取运行方式。
import json  # 保存不含凭据的状态。
import logging  # 统一记录监督器和网页输出。
from logging.handlers import RotatingFileHandler  # 避免日志无限增长。
import os  # 进程号和平台判断。
from pathlib import Path  # 跨平台路径。
import subprocess  # 启动受监督的网页进程。
import sys  # 使用同一虚拟环境。
import threading  # 持续消费网页输出，避免管道堵塞。
import time  # 重试退避和时间记录。
from typing import Optional  # 兼容 Python3.8 的可空返回值注解。
import urllib.request  # 仅访问本机停止接口。

BASE = Path(__file__).resolve().parents[2]  # 不依赖调用目录。


def paths(port, directory=None):  # 按端口隔离不同实例。
    root = Path(directory) if directory else BASE / 'data'  # 测试可用临时目录。
    root.mkdir(parents=True, exist_ok=True)  # 首次运行创建数据目录。
    return dict(lock=root/f'watch_{port}.lock', stop=root/f'watch_{port}.stop', status=root/f'watch_{port}.json', log=root/f'watch_{port}.log')  # 不在状态中保存停止凭据。


class InstanceLock:  # 使用操作系统文件锁，进程崩溃时自动释放。
    def __init__(self, path):  # 保存锁路径。
        self.path = path  # 锁文件可长期存在，不等于服务正在运行。
        self.stream = None  # 成功后保持句柄开启。
    def __enter__(self):  # 同端口只允许一个监督器。
        self.stream = self.path.open('a+b')  # 不破坏已有文件。
        self.stream.seek(0)  # 锁住首字节。
        if self.path.stat().st_size == 0:  # 读取文件长度，不读取另一实例已锁住的字节。
            self.stream.write(b'0')  # Windows 字节锁要求文件有内容。
            self.stream.flush()  # 写入操作系统。
        self.stream.seek(0)  # 回到首字节。
        try:  # 不等待其他实例释放。
            if os.name == 'nt':  # Windows 使用标准库字节锁。
                import msvcrt  # 系统自带，无第三方依赖。
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)  # 重复启动立即失败。
            else:  # 树莓派/Linux 对应文件锁。
                import fcntl  # Linux 标准库。
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 同样防止重复实例。
        except OSError:  # 已有监督器持有锁。
            self.stream.close()  # 不泄漏句柄。
            raise RuntimeError('本端口的守护进程已经运行')  # 交给启动器显示。
        return self  # 在 with 块中维持锁。
    def __exit__(self, *args):  # 退出时系统自动解锁。
        self.stream.close()  # 不依赖删除 PID 文件。


def request_stop(port):  # 只停止当前项目记录的网页，不按进程名杀 Python。
    control = BASE/'data'/f'local_control_{port}.json'  # 原网页的认证凭据。
    if not control.exists():  # 子进程可能尚未启动。
        return False  # 监督器仍会看到主动停止标记。
    data = json.loads(control.read_text(encoding='utf-8'))  # 不输出凭据。
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/local/stop', data=b'{}', headers={'Authorization':'Bearer '+data['token'], 'Content-Type':'application/json'})  # 带随机凭据认证。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机请求不经过代理软件。
    with opener.open(req, timeout=3) as response:  # 请求正常停止，而不是强杀。
        return bool(json.load(response).get('success'))  # 等待接口确认。


def restart_delay(failures):  # 连续启动失败时退避，避免每秒反复占用 CPU。
    return min(60, 2 ** min(failures, 6))  # 首次2秒，最高60秒；稳定运行后重新计数。


def existing_service_pid(port: int, directory: Optional[Path] = None) -> Optional[int]:
    """本文件定义：只读检查遗留网页；port为端口，directory为测试数据目录，返回记录的PID或None。"""
    root = Path(directory) if directory else BASE / 'data'  # 与本端口的停止凭据使用同一目录。
    control = root / f'local_control_{port}.json'  # 守护退出时，网页子进程可能仍在线。
    try:
        data = json.loads(control.read_text(encoding='utf-8'))  # pathlib读取文件；json.loads解析JSON，不输出凭据。
        pid = data['pid']  # 只取记录的进程号，不向状态文件复制token。
        if type(pid) is not int or pid <= 0 or data.get('port') != port:  # dict.get读取键并检查记录是否有效。
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Python标准库：本机HTTP绕过代理。
        with opener.open(f'http://127.0.0.1:{port}/api/status', timeout=1) as response:  # 只读接口，不恢复棋局或控制机械臂。
            status = json.load(response)  # json.load将HTTP响应解析为对象。
        if isinstance(status, dict) and status.get('success') is True and isinstance(status.get('state'), dict):  # 内置isinstance校验返回结构。
            return pid  # 仅据此等待，不将这个PID当作可以强制终止的授权。
    except (OSError, ValueError, KeyError, TypeError):  # 无文件、过期凭据或端口不可达时，交给正常启动流程。
        pass
    return None


def supervise(command, port=5000, directory=None, poll_seconds=0.5):  # 测试可注入故意崩溃的子进程。
    files = paths(port, directory)  # 独立状态文件。
    logger = logging.getLogger(f'watch.{port}')  # 每个端口独立日志。
    logger.setLevel(logging.INFO)  # 保存正常退出与异常信息。
    logger.propagate = False  # 不向父进程控制台重复输出。
    handler = RotatingFileHandler(files['log'], maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')  # 最多四份、约20MB日志。
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))  # 带本机时间。
    logger.addHandler(handler)  # 子进程输出也写同一轮转日志。
    restarts, failures = 0, 0  # 分别记录累计重启与连续失败。
    child = None  # 未启动时不伪造进程号。
    def save(state, **extra):  # 原子替换状态，避免读到半个 JSON。
        payload = dict(state=state, guardian_pid=os.getpid(), child_pid=child.pid if child else None, port=port, restarts=restarts, time=time.strftime('%Y-%m-%d %H:%M:%S'), **extra)  # 不含密码或停止 token。
        temporary = files['status'].with_suffix('.tmp')  # 同目录临时文件。
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')  # 易于诊断。
        temporary.replace(files['status'])  # 读者只会看到完整版本。
    def pump(stream):  # 读取 stdout/stderr 的后台线程。
        for line in stream:  # 子进程关闭输出管道时结束。
            logger.info('网页 %s', line.rstrip())  # 记录 traceback，不输出任何凭据文件。
        stream.close()  # 释放管道资源。
    try:  # 锁与日志最后都会释放。
        with InstanceLock(files['lock']):  # 防止任务计划程序和双击重复启动。
            logger.info('监督器启动 pid=%s；服务退出后重启，不恢复机械臂任务', os.getpid())  # 记录本次生命周期。
            while not files['stop'].exists():  # 主动停止与异常退出分开处理。
                existing_pid = existing_service_pid(port, directory)  # 本文件函数：检查前一个监督器遗留的健康网页。
                if existing_pid is not None:  # 守护意外退出不一定会连带结束网页，不能反复抢占同一端口。
                    save('monitoring_existing', existing_pid=existing_pid)  # 明确记录正在监测遗留进程，child_pid仍为None。
                    logger.info('已有健康网页 pid=%s；监测其可达性，避免重复占用端口', existing_pid)
                    while existing_pid is not None and not files['stop'].exists():
                        deadline = time.monotonic() + 2  # time.monotonic来自标准库：每两秒复查一次。
                        while time.monotonic() < deadline and not files['stop'].exists():
                            time.sleep(poll_seconds)  # 标准库暂停：仍可及时响应用户停止。
                        existing_pid = existing_service_pid(port, directory)  # 网页不可达后，再进入新子进程启动流程。
                    if files['stop'].exists():
                        try:
                            request_stop(port)  # 本文件函数：用户主动停止时，仍通过认证接口请求正常退出。
                        except (OSError, ValueError):
                            pass  # 不因停止接口不可达而强杀机械臂进程。
                        break
                    logger.warning('遗留网页已不可达，尝试重新启动；不会恢复旧棋局')
                env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', CHRO_SUPERVISED='1')  # 子进程可以识别监督模式。
                started = time.monotonic()  # 判断是否稳定运行。
                try:  # 缺依赖、缺引擎等启动错误也记录。
                    child = subprocess.Popen(command, cwd=BASE, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)  # 后台运行，不依赖浏览器或控制台。
                    worker = threading.Thread(target=pump, args=(child.stdout,), daemon=True)  # 不让管道写满。
                    worker.start()  # 开始收集网页日志。
                    save('running')  # 保存实际子进程号。
                    logger.info('已启动网页 pid=%s，累计重启=%s', child.pid, restarts)  # 可追溯重启次数。
                    stop_sent = False  # 主动停止请求只发送一次。
                    while child.poll() is None:  # 不因为某一次 HTTP 超时就杀机械臂进程。
                        if files['stop'].exists() and not stop_sent:  # 用户主动点击停止。
                            save('stopping')  # 不会再启动下一实例。
                            try:  # 进程可能正在初始化，暂时没有 HTTP 接口。
                                stop_sent = request_stop(port)  # 初始化结束后再试，绝不强杀动作中的机械臂。
                            except (OSError, ValueError):  # 服务已崩溃或还没就绪。
                                pass  # 下一轮继续检查。
                        time.sleep(poll_seconds)  # 低 CPU 占用。
                    code = child.returncode  # Windows 强制结束的退出码也会记录。
                    worker.join(timeout=2)  # 尽量写完 traceback。
                    logger.warning('网页进程退出 pid=%s code=%s uptime=%.1fs', child.pid, code, time.monotonic()-started)  # 即使退出码为0也记录。
                except OSError as exc:  # 无法创建子进程。
                    code = None  # 没有实际退出码。
                    logger.exception('启动网页失败：%s', exc)  # 保留异常细节。
                if files['stop'].exists():  # 主动停止绝不自动重启。
                    break  # 正常退出监督器。
                failures = 1 if time.monotonic()-started >= 60 else failures+1  # 稳定一分钟后重置退避。
                delay = restart_delay(failures)  # 计算下次重启间隔。
                restarts += 1  # 显示累计异常恢复次数。
                save('retrying', exit_code=code, retry_seconds=delay)  # 方便判断是否一直启动失败。
                logger.warning('%s秒后重启网页；棋局不恢复，机械臂不自动续走', delay)  # 明确行为边界。
                deadline = time.monotonic()+delay  # 退避期间仍能停止。
                while time.monotonic() < deadline and not files['stop'].exists():  # 不一次睡满60秒。
                    time.sleep(poll_seconds)  # 及时响应停止文件。
            save('stopped')  # 不留下假 running 状态。
            logger.info('监督器已主动停止')  # 便于区分崩溃。
        return 0  # 任务计划程序不会重启正常停止的任务。
    finally:  # 重复测试或退出时不泄漏日志句柄。
        logger.removeHandler(handler)  # 移除当前处理器。
        handler.close()  # 刷新到磁盘。


def main():  # 命令行入口。
    parser = argparse.ArgumentParser(description='CH-RO 网页守护：异常退出自动重启')  # 显示用途。
    parser.add_argument('--port', type=int, default=5000)  # 不同实例可选择端口。
    parser.add_argument('--reset-stop', action='store_true')  # 只有用户点击启动时清除主动停止标记。
    args, extra = parser.parse_known_args()  # 其余引擎/硬件参数传给 run_local.py。
    if not 1 <= args.port <= 65535:  # 校验端口。
        parser.error('端口需要为1..65535')  # 不启动错误实例。
    if args.reset_stop:  # 明确请求重新开始服务。
        paths(args.port)['stop'].unlink(missing_ok=True)  # 仅删除本项目该端口的停止标记。
    try:  # 重复实例是正常提示，不崩溃。
        executable = str(Path(sys.executable).with_name('python.exe')) if os.name == 'nt' else sys.executable  # 监督器使用 pythonw，网页子进程仍有可捕获的标准输出。
        return supervise([executable, '-u', str(BASE/'run_local.py'), '--port', str(args.port), *extra], args.port)  # 使用当前虚拟环境。
    except RuntimeError as exc:  # 已持锁时无需另启网页。
        if sys.stdout is not None:  # pythonw 没有控制台时不调用 print。
            print(str(exc))  # 不输出敏感信息。
        return 0  # 不触发任务计划程序重试风暴。


if __name__ == '__main__':  # 导入测试时不自动运行。
    raise SystemExit(main())  # 返回给 Windows 任务计划程序。
