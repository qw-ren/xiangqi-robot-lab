# -*- coding: utf-8 -*-  # 中文说明采用 UTF-8。
"""Windows/Linux 通用 CH-RO 网页入口；mock 模拟，dofbot 通过 Arm_Lib 控制。"""  # 是否连接实物由 backend 明确选择。
import argparse  # 读取端口、引擎和相机选项。
import atexit  # 退出时清理引擎。
import json  # 保存本机服务的停止凭据。
import os  # 设置项目级环境变量。
from pathlib import Path  # 处理带空格的路径。
import sys  # 确保可以导入项目根目录模块。
import secrets  # 为本机停止接口生成随机凭据。
import threading  # 将服务器停止动作放到独立线程。
import webbrowser  # 由用户双击启动脚本时自动打开本机网页。
import faulthandler  # 保存 Python 崩溃时的线程堆栈。

BASE_DIR = Path(__file__).resolve().parent  # 项目根目录以文件位置为准。
sys.path.insert(0, str(BASE_DIR))  # 从任意工作目录启动都能找到 CH-RO。


def main() -> int:  # 命令行主入口。
    parser = argparse.ArgumentParser(description='CH-RO 完整网页：电脑模拟或树莓派 DOFBOT')  # 显示用途。
    parser.add_argument('--host', default='127.0.0.1')  # 默认只供本机浏览器访问。
    parser.add_argument('--port', type=int, default=5000)  # 端口可修改，网页使用相对接口地址。
    parser.add_argument('--engine', default=str(BASE_DIR / 'Pikafish' / ('pikafish.exe' if os.name == 'nt' else 'pikafish')))  # 根据当前操作系统选择文件名，目录始终以本文件为准。
    parser.add_argument('--nnue', default=None)  # 可选：匹配权重路径；省略时使用引擎目录下的 pikafish.nnue。
    parser.add_argument('--threads', type=int, default=2)  # 给网页和识别留出 CPU。
    parser.add_argument('--hash', type=int, default=128)  # 引擎哈希表大小，单位 MB。
    parser.add_argument('--ms', type=int, default=1000)  # 每步搜索时间上限，单位毫秒。
    parser.add_argument('--camera', action='store_true')  # 用户明确选择后才扫描相机。
    parser.add_argument('--camera-index', type=int, default=0)
    parser.add_argument('--vision-only', action='store_true', help='Preview vision without requiring an AI engine')
    parser.add_argument('--vision-backend', choices=['aruco', 'rtmpose'], default='aruco')
    parser.add_argument('--aruco-config', type=Path, default=BASE_DIR / 'configs/local/board-local.json')
    parser.add_argument('--board-orientation', choices=['unknown', 'black-top', 'red-top'], default='unknown')
    parser.add_argument('--allow-approximate-geometry', action='store_true')
    parser.add_argument('--backend', choices=['mock', 'dofbot'], default='mock')  # 默认仅预演，真机必须明确选择。
    parser.add_argument('--calibration', default=None)  # 可指定参数文件；默认将模拟与实体分开保存。
    parser.add_argument('--open-browser', action='store_true')  # 供用户的一键启动脚本使用。
    args = parser.parse_args()  # 获取实际参数。
    if not 1 <= args.threads <= 32 or not 16 <= args.hash <= 4096 or not 50 <= args.ms <= 10000:  # 约束常用参数。
        parser.error('threads 需为 1..32，hash 需为 16..4096，ms 需为 50..10000')  # 防止误填造成过量资源占用。
    engine_path = Path(args.engine).resolve()  # 获取完整路径。
    if not engine_path.is_file() and not args.vision_only:  # 视觉预览不需要引擎。
        parser.error(f'找不到引擎：{engine_path}')  # 不启动一个无法搜索的服务。
    nnue_path = Path(args.nnue).resolve() if args.nnue else None  # 在切换工作目录前解析用户传入的权重路径。
    if nnue_path is not None and not nnue_path.is_file():  # 显式指定的权重必须实际存在。
        parser.error(f'找不到权重：{nnue_path}')  # 避免网页启动后才发现路径错误。
    os.chdir(BASE_DIR)  # 统一日志、模型和相对路径基准。
    faulthandler.enable()  # 守护启动时 stderr 会写入轮转日志。
    os.environ.setdefault('CHRO_SKIP_STARTUP_PROMPT', '1')  # 不进入原交互式下位机参数提示。
    os.environ.setdefault('CHRO_ONNX_PROVIDERS', 'CPUExecutionProvider')  # 普通电脑先用 CPU 推理。
    import config  # 在导入网页模块之前设置配置。
    config.ENGINE_PATH = str(engine_path)  # 替换实际可执行文件。
    config.ENGINE_NNUE_PATH = str(nnue_path) if nnue_path else None  # 让引擎适配器显式设置 EvalFile；不要求权重和引擎放同一目录。
    config.ENGINE_THREADS = args.threads  # 设置引擎线程数。
    config.HASH_SIZE_MB = args.hash  # 设置引擎哈希内存。
    config.THINK_TIME = args.ms  # 网页和引擎封装使用同一时间上限。
    config.VISION_ONLY = args.vision_only
    config.VISION_BACKEND = args.vision_backend
    config.ARUCO_CONFIG_PATH = str(args.aruco_config.resolve())
    config.ARUCO_BOARD_ORIENTATION = args.board_orientation
    config.ARUCO_ALLOW_APPROXIMATE = args.allow_approximate_geometry
    if args.vision_backend == 'aruco':
        config.CAMERA_WIDTH, config.CAMERA_HEIGHT = 1600, 1200
    config.DESKTOP_SIMULATION_ONLY = False  # 完整网页允许进入所选后端，默认仍为 mock。
    config.ROBOT_BACKEND = args.backend  # 明确区分模拟预演与直接 Arm_Lib。
    config.DOFBOT_CALIBRATION = str(Path(args.calibration).resolve() if args.calibration else BASE_DIR / 'data' / ('mock_calibration.json' if args.backend == 'mock' else 'dofbot_calibration.json'))  # 模拟保存不能覆盖实体标定。
    config.CAMERA_INDEX = args.camera_index
    config.USE_IP_CAMERA = False  # 不连接原作者的内网相机。
    from web_simulation import app as web_app  # 复用 CH-RO 的真实网页、路由和状态。
    from flask import request, jsonify  # 处理只在本机调试入口注册的停止接口。
    from werkzeug.serving import make_server  # 允许清理后正常结束后台服务。
    web_app.app.context_processor(lambda: dict(desktop_simulation_only=False, desktop_virtual_input=not args.camera, robot_backend=args.backend))  # 展示完整操作入口。
    control_token = secrets.token_urlsafe(32)  # 不在网页或日志中显示停止凭据。
    server = make_server(args.host, args.port, web_app.app, threaded=True)  # 先绑定端口，失败时不覆盖旧凭据。
    @web_app.app.post('/api/local/stop')  # 仅本机启动器注册这个管理接口。
    def stop_server():  # 配合停止脚本正常退出。
        if request.remote_addr not in ('127.0.0.1', '::1') or not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + control_token):  # 同时限制地址和凭据。
            return jsonify(success=False, error='拒绝访问'), 403  # 不接受未授权的页面请求。
        if os.environ.get('CHRO_SUPERVISED') == '1':  # 主动停止与程序崩溃区别对待。
            from tools.web.watch_service import paths  # 与监督器使用同一端口标记。
            paths(args.port)['stop'].touch()  # 告诉监督器不要重启。
        threading.Thread(target=server.shutdown, daemon=True).start()  # 避免在请求线程里等待自己。
        return jsonify(success=True)  # 先返回结果，再由主线程清理引擎。
    data_dir = BASE_DIR / 'data'  # 本机日志和运行记录目录。
    data_dir.mkdir(exist_ok=True)  # 首次启动时创建目录。
    control_path = data_dir / f'local_control_{args.port}.json'  # 不同端口使用不同文件。
    control_path.write_text(json.dumps(dict(pid=os.getpid(), port=args.port, token=control_token)), encoding='utf-8')  # 停止脚本只访问本机。
    def cleanup() -> None:  # 关闭本进程持有的引擎。
        web_app.game_state['is_game_running'] = False  # 结束后不执行迟到的搜索结果。
        from web_simulation.dofbot import controller  # 退出时也停止后续机械臂指令。
        if controller is not None:  # 可能从未点击过连接。
            controller.stop()  # 只设停止标志，不在退出时移动或释放夹持物。
        if web_app.ai_engine is not None:  # 引擎可能尚未延迟启动。
            web_app.ai_engine.stop()  # 发 quit 并等待结束。
    atexit.register(cleanup)  # 正常关闭时执行清理。
    print(f'打开浏览器：http://{args.host}:{args.port}', flush=True)  # 给出访问地址。
    print(f'机械臂后端：{args.backend}；mock 不会驱动硬件。实体模式需要现场标定和视觉确认。', flush=True)  # 防止把电脑预演当成真机连接。
    if args.open_browser:  # 只有用户选择自动打开时执行。
        browser_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host  # 监听所有网卡时仍从本机访问。
        threading.Thread(target=webbrowser.open, args=(f'http://{browser_host}:{args.port}',), daemon=True).start()  # 打开默认浏览器。
    try:  # Ctrl+C 退出后仍执行清理。
        server.serve_forever()  # 只启动一个服务实例，Ctrl+C 或停止脚本退出。
    except KeyboardInterrupt:  # 用户在启动窗口中按 Ctrl+C。
        if os.environ.get('CHRO_SUPERVISED') == '1':  # Ctrl+C 也属于主动停止。
            from tools.web.watch_service import paths  # 延迟导入标准库辅助模块。
            paths(args.port)['stop'].touch()  # 不违背用户停止意图。
        print('正在停止网页和引擎。', flush=True)  # 使用正常清理流程。
    finally:  # 避免后台 Pikafish 遗留。
        cleanup()  # 清理可重复调用。
        server.server_close()  # 释放监听端口。
        control_path.unlink(missing_ok=True)  # 正常结束后删除当前服务凭据。
    return 0  # 正常结束。


if __name__ == '__main__':  # 被导入时不自动启动服务。
    raise SystemExit(main())  # 直接运行时进入主函数。
