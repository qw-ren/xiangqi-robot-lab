# -*- coding: utf-8 -*-
"""迁移包入口：检查依赖与引擎，或者启动两套项目中的一套；不自动连接或移动机械臂。"""
import argparse  # 标准库：解释 check/start 及各个命令行参数。
import importlib.util  # 标准库：检查 Arm_Lib 是否可以找到，不实例化设备。
import json  # 标准库：解析子进程的检测结果。
import os  # 标准库：检查执行权限、读取环境变量及替换当前进程。
from pathlib import Path  # 标准库：构造跨平台绝对路径。
import platform  # 标准库：读取操作系统及 CPU 架构。
import struct  # 标准库：读取 Python 位数及 ELF 文件头的架构编号。
import subprocess  # 标准库：在独立解释器中检查依赖和运行真实引擎单步工具。
import sys  # 标准库：读取当前 Python 路径及版本。

ROOT = Path(__file__).resolve().parent  # 以脚本所在目录为根目录，允许从其他工作目录调用。
PROJECTS = {'strategy': 'xiangqi_strategy', 'web': 'chch-robot'}  # 两个选项对应包内的两个独立项目。
DEPS = {  # 左边是 import 名，右边是 pip 包名，错误信息用于指导安装。
    'strategy': {'flask': 'Flask', 'cchess': 'cchess', 'serial': 'pyserial'},
    'web': {'flask': 'Flask', 'flask_cors': 'flask-cors', 'cchess': 'cchess',
            'serial': 'pyserial', 'numpy': 'numpy', 'cv2': 'opencv-python',
            'onnxruntime': 'onnxruntime', 'pandas': 'pandas'},
}


def resolve_path(value):
    """功能：把路径转成绝对路径；value 为 str/Path，相对路径以迁移包目录为准；返回 Path。"""
    path = Path(value).expanduser()  # pathlib.Path.expanduser()：展开 ~ 为当前用户的主目录。
    return (path if path.is_absolute() else ROOT / path).resolve()  # 不依赖终端当前所在目录。


def engine_paths(args):
    """功能：选择引擎与权重；args 为命令行 Namespace；返回两个绝对 Path。"""
    name = 'pikafish.exe' if os.name == 'nt' else 'pikafish'  # Windows 和 Linux 的默认文件名。
    candidates = [ROOT / 'engine_assets' / name, ROOT / PROJECTS[args.project] / 'Pikafish' / name]  # 优先使用公共引擎目录。
    engine = resolve_path(args.engine) if args.engine else next((p for p in candidates if p.is_file()), candidates[0])  # 显式参数优先。
    nnue = resolve_path(args.nnue) if args.nnue else ROOT / 'engine_assets' / 'pikafish.nnue'  # 包内已提供匹配权重。
    return engine, nnue


def binary_error(path, system=None, machine=None, bits=None):
    """功能：静态检查引擎格式；path 为文件，system/machine/bits 可覆盖系统信息用于测试；返回错误字符串或 None。"""
    system = system or platform.system()  # platform.system()：Linux 或 Windows 等系统名。
    machine = (machine or platform.machine()).lower()  # platform.machine()：CPU 架构名。
    bits = bits or struct.calcsize('P') * 8  # 指针宽度代表当前 Python 用户空间位数。
    with path.open('rb') as stream:  # Path.open(mode)：以二进制模式读取引擎文件头，不执行文件。
        header = stream.read(20)  # ELF 的机器编号位于偏移 18..19。
    if system == 'Linux':  # 树莓派需要 ELF 文件，Windows 的 PE 文件不可用。
        if len(header) < 20 or header[:4] != b'\x7fELF':
            return 'Linux 需要 ELF 可执行文件；不能使用 Windows exe、Android 引擎或压缩包。'
        if header[5] not in (1, 2):  # ELF 字节序必须合法。
            return 'ELF 文件头字节序无效。'
        actual_bits = {1: 32, 2: 64}.get(header[4])  # ELFCLASS32/ELFCLASS64。
        actual_machine = struct.unpack('<H' if header[5] == 1 else '>H', header[18:20])[0]  # unpack(format, bytes)：读无符号短整型。
        arm = machine in ('aarch64', 'arm64') or machine.startswith('arm')  # 64 位内核也可能搭配 32 位用户空间。
        expected_machine = (183 if bits == 64 else 40) if arm else None  # ELF 的 AArch64=183、ARM=40。
        if actual_bits != bits or (expected_machine is not None and actual_machine != expected_machine):
            return f'引擎 ELF 架构不匹配：文件 {actual_bits} 位/e_machine={actual_machine}，Python {bits} 位/{machine}。'
        if not os.access(path, os.X_OK):  # os.access(path, mode)：检查当前用户是否有执行权限。
            return f'引擎没有执行权限，请执行 chmod +x "{path}"。'
    elif system == 'Windows' and header[:2] != b'MZ':
        return '当前是 Windows，请用 Windows 版引擎做电脑检查；树莓派包本身不包含 exe。'
    return None  # 格式检查不能证明指令集、动态库与权重匹配，仍需 --search 实测。


def import_errors(project_dir, names):
    """功能：在独立进程实际导入模块；project_dir 为工作目录，names 为模块名列表；返回失败项字典。"""
    code = """import importlib,json,sys
errors={}
for name in json.loads(sys.argv[1]):
    try: importlib.import_module(name)
    except Exception as exc: errors[name]=str(exc)
print('DEPENDENCY_RESULT='+json.dumps(errors,ensure_ascii=True))
"""  # 真正 import 可发现装了包却缺少共享库的问题；不会导入项目摄像头或创建设备。
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')  # 只修改子进程环境，不改系统配置。
    result = subprocess.run([sys.executable, '-c', code, json.dumps(names)], cwd=project_dir,
                            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)  # run(args, ...)：最多等待90秒，捕获输出以便诊断。
    rows = [line for line in result.stdout.splitlines() if line.startswith('DEPENDENCY_RESULT=')]  # 忽略第三方库的额外提示。
    if result.returncode or not rows:
        raise RuntimeError((result.stderr or result.stdout or '依赖导入进程异常退出')[-2000:])  # 不将崩溃或超时当作导入成功。
    return json.loads(rows[-1].split('=', 1)[1])  # json.loads(text)：将检查结果解析为字典。


def check(args):
    """功能：检查所选项目环境；args 为命令行参数；返回错误数；不连接串口、相机或机械臂。"""
    failures = 0  # 错误数决定进程返回码，避免检查失败后自动启动。
    project_dir = ROOT / PROJECTS[args.project]  # 所选项目的目录。
    engine, nnue = engine_paths(args)  # 本文件 engine_paths(args)：选择实际引擎和权重。
    print(f'系统：{platform.system()} {platform.machine()} / Python {platform.python_version()} / {struct.calcsize("P") * 8}位', flush=True)
    print(f'项目：{project_dir}', flush=True)
    if sys.version_info < (3, 10):  # 统一迁移包最低版本，避免依赖和语法版本混乱。
        print('[错误] 请使用 Python 3.10 以上，建议 3.11/3.12。', flush=True)
        failures += 1
    if not project_dir.is_dir():
        print('[错误] 缺少项目目录；请完整解压整个包。', flush=True)
        return failures + 1
    dependencies = dict(DEPS[args.project])  # 复制依赖表，不修改其他调用的设置。
    if args.backend == 'dofbot':  # 当前项目电磁铁通过 gpiozero 驱动；mock 不需要访问树莓派GPIO库。
        dependencies['gpiozero'] = 'gpiozero'
    try:
        errors = import_errors(project_dir, list(dependencies))  # 独立进程导入，不启动项目服务。
        for name, error in errors.items():
            print(f'[错误] {name} ({dependencies[name]}): {error}', flush=True)
        failures += len(errors)
        if not errors:
            print('[通过] 所选项目的 Python 运行依赖可导入。', flush=True)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'[错误] 检查依赖失败：{exc}', flush=True)
        failures += 1
    if args.project == 'web':
        for relative in ('model/pose/4_v6-0301.onnx', 'model/layout_recognition/nano_v3-0319.onnx'):
            if not (project_dir / relative).is_file():
                print(f'[错误] 缺少网页识别模型：{relative}', flush=True)
                failures += 1
    if args.backend == 'dofbot':
        try:
            if importlib.util.find_spec('Arm_Lib') is None:  # find_spec(name)：检查可发现性，不构造 Arm_Device。
                raise ImportError('当前 Python 环境找不到厂商 Arm_Lib；请先安装并确认虚拟环境可见。')
            print('[通过] 找到 Arm_Lib（未实例化设备，通信和型号兼容仍需实测）。', flush=True)
        except (ImportError, ValueError) as exc:
            print(f'[错误] {exc}', flush=True)
            failures += 1
        print('[待实测] DOFBOT 需有效的90格上下姿态、park、吃子槽位及电磁铁配置。', flush=True)
    if args.serial:
        port = Path(args.serial).expanduser()  # 串口参数是系统设备名，不相对于迁移包目录。
        if platform.system() == 'Linux' and (not port.exists() or not os.access(port, os.R_OK | os.W_OK)):
            print(f'[错误] 串口不存在或当前用户不可读写：{port}', flush=True)
            failures += 1
        print('[提示] 串口接收 observe 后仍需控制器发起 ai/ack/done/视觉确认；不会自动驱动 Arm_Lib。', flush=True)
    if not args.skip_engine:
        for label, path in (('引擎', engine), ('权重', nnue)):
            if not path.is_file():
                print(f'[错误] 找不到{label}：{path}', flush=True)
                failures += 1
        if engine.is_file():
            error = binary_error(engine)  # 本文件 binary_error(path)：静态验证文件格式及 ARM 位数。
            if error:
                print(f'[错误] {error}', flush=True)
                failures += 1
    if args.search and failures == 0:
        command = [sys.executable, str(ROOT / 'xiangqi_strategy' / 'tools' / 'one_step.py'),
                   str(ROOT / 'xiangqi_strategy' / 'examples' / 'start.json'), '--engine', str(engine), '--nnue', str(nnue)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1'), timeout=30)  # 调用真实引擎并由 board.py 复核走法，不使用 fake_uci。
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout)[-2000:])
            output = json.loads(result.stdout)  # 单步工具返回 JSON，异常输出不视作成功。
            if output.get('kind') != 'expected_only' or len(output.get('pieces', [])) != 32:
                raise ValueError('单步工具没有返回预期的开局搜索结果。')
            print(f'[通过] 真实引擎搜索及规则复核：{output["move"]["uci"]}；预期全盘32子。', flush=True)
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            print(f'[错误] 真实引擎搜索失败：{exc}', flush=True)
            failures += 1
    print('[范围] 本检查不验证相机识别、实际抓放或跨项目联通；完整网页尚无队友视觉串口输入。', flush=True)
    print(f'检查结束：{failures} 项错误。', flush=True)
    return failures


def launch_command(args):
    """功能：只构造启动命令；args 为 Namespace；返回字符串列表，不执行、不连接硬件。"""
    engine, nnue = engine_paths(args)
    project = ROOT / PROJECTS[args.project]
    command = [sys.executable, str(project / ('app.py' if args.project == 'strategy' else 'run_local.py')),
               '--engine', str(engine), '--nnue', str(nnue), '--host', args.host,
               '--port', str(args.port or (5001 if args.project == 'strategy' else 5000)),
               '--threads', str(args.threads), '--hash', str(args.hash_mb)]
    if args.project == 'web':
        command += ['--backend', args.backend, '--ms', str(args.ms)]  # 默认 mock，不自动改成实体控制。
        if args.calibration:
            command += ['--calibration', str(resolve_path(args.calibration))]
    elif args.serial:
        command += ['--serial', args.serial, '--baud', str(args.baud)]
    return command


def main():
    """功能：解析命令并检查/启动项目；参数来自终端；返回退出状态，start 成功时由项目替换当前进程。"""
    parser = argparse.ArgumentParser(description=__doc__)  # ArgumentParser(description)：生成帮助和错误提示。
    parser.add_argument('action', choices=['check', 'start'])  # check 只检查；start 检查通过后运行。
    parser.add_argument('--project', choices=list(PROJECTS), default='strategy')  # 默认按策略分工启动轻量项目。
    parser.add_argument('--engine')  # 引擎路径；相对路径以包目录为准。
    parser.add_argument('--nnue')  # 权重路径；相对路径以包目录为准。
    parser.add_argument('--backend', choices=['mock', 'dofbot'], default='mock')  # 仅完整网页支持。
    parser.add_argument('--calibration')  # 完整网页的现场示教 JSON，不能使用模拟角度代替。
    parser.add_argument('--serial')  # 仅策略项目支持，例如 /dev/serial/by-id/...。
    parser.add_argument('--baud', type=int, default=115200)  # 串口波特率，默认8N1。
    parser.add_argument('--host', default='127.0.0.1')  # 默认通过 SSH 通道访问。
    parser.add_argument('--port', type=int)  # 默认 strategy=5001、web=5000。
    parser.add_argument('--threads', type=int, default=2)  # Pi4B 给系统和网页留出 CPU。
    parser.add_argument('--hash', dest='hash_mb', type=int, default=64)  # 引擎哈希内存，单位MB。
    parser.add_argument('--ms', type=int, default=1000)  # 完整网页每步搜索毫秒数。
    parser.add_argument('--search', action='store_true')  # 检查时执行真实引擎开局单步搜索。
    parser.add_argument('--skip-engine', action='store_true')  # 仅检查依赖，不能用于 start 或 --search。
    args = parser.parse_args()  # parse_args()：把命令行转为 Namespace。
    if args.project == 'strategy' and (args.backend != 'mock' or args.calibration):
        parser.error('策略项目没有直接 Arm_Lib 后端；DOFBOT 使用 --project web，不能把两套代码当成已接通。')
    if args.project == 'web' and args.serial:
        parser.error('完整网页尚未接入队友视觉串口；--serial 仅供 strategy 使用。')
    if args.skip_engine and (args.action == 'start' or args.search):
        parser.error('--skip-engine 只能单独用于 check。')
    if (args.port is not None and not 1 <= args.port <= 65535) or not 1 <= args.threads <= 32 or not 16 <= args.hash_mb <= 4096 or not 50 <= args.ms <= 10000 or args.baud <= 0:
        parser.error('请检查 port(1..65535)、threads(1..32)、hash(16..4096)、ms(50..10000) 和 baud(正数)。')
    if check(args):  # 本文件 check(args)：环境缺项时返回非0，不启动半可用服务。
        return 1
    if args.action == 'start':
        command = launch_command(args)  # 本文件 launch_command(args)：生成绝对路径参数。
        print('开始运行；按 Ctrl+C 停止。默认仅监听树莓派本机，请通过 SSH 通道访问。', flush=True)
        os.chdir(ROOT / PROJECTS[args.project])  # os.chdir(path)：统一相对模型/日志路径。
        os.execv(sys.executable, command)  # os.execv(program, argv)：用项目进程替换入口进程，保留 Ctrl+C 行为，不经过 shell。
    return 0


if __name__ == '__main__':  # 导入用于测试时不检查环境、不启动服务。
    raise SystemExit(main())  # SystemExit(code)：把结果作为终端退出状态。
