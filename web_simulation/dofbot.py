# -*- coding: utf-8 -*-  # 中文注释便于后续修改。
"""完整网页的走棋串口输出与视觉确认；保留旧接口名以兼容现有页面。"""  # 仅启用对应启动参数时使用。
import copy  # 保存未提交的预期棋局。
import json  # 导出棋谱与全部棋子坐标。
import threading  # 状态更新使用同一个锁。
import uuid  # 为每局生成唯一编号，避免跨局重复发送。
from urllib.parse import urlsplit  # 校验浏览器提交来源。
from flask import Blueprint, Response, g, jsonify, request  # 扩展原 Flask 网页。
from xq_utils.rules import checked_transition  # 规则层作为局面的唯一校验来源。
from robot.dofbot_adapter import DofbotController, validate_calibration, validate_pose  # 串口走棋输出与离线预演层。
from robot import RobotSendResult  # 兼容原 AI 调用接口。

bp = Blueprint('dofbot', __name__)  # 集中注册扩展接口。
state_lock = threading.RLock()  # 防止两个浏览器同时走棋。
controller = None  # 连接按钮按需创建设备。


def app_module():  # 延迟导入以避免循环引用。
    from web_simulation import app  # 与原网页共享唯一状态。
    return app  # 返回组合模块。


def enabled():
    """功能：所有 Web 启动方式统一使用安全的 mock/串口流程；无参数。"""
    return True


def get_controller():  # 创建当前进程唯一的机械臂服务。
    global controller  # 复用设备对象。
    config = app_module().config  # 获取启动参数。
    backend = getattr(config, 'ROBOT_BACKEND', 'mock')
    backend = 'serial' if backend == 'dofbot' else backend  # 电脑默认预演。
    if backend not in ('mock', 'serial'):  # 原 TCP 不创建 DOFBOT。
        raise ValueError('只支持 mock 或 serial 后端，已取消本机机械臂和 TCP 动作控制')  # 提供正确入口。
    if controller is None or controller.backend != backend:  # 首次创建或测试切换时替换。
        if controller is not None:
            controller.close()  # 切换模式前释放旧串口。
        controller = DofbotController(backend, getattr(config, 'DOFBOT_CALIBRATION', 'data/dofbot_calibration.json'))  # 路径可配置。
    return controller  # 不自动连接或运动。


def pieces_payload(board):  # 导出您负责传递的全部棋子坐标。
    return [dict(piece=piece, col=int(key.split(',')[0]), row=int(key.split(',')[1])) for key, piece in sorted(board.items())]  # row0 黑方底线，col0 左侧。


def blocked():  # 共享开局、重置、切换源的占用判断。
    gs = app_module().game_state  # 正式状态。
    return bool(gs.get('ai_thinking') or gs.get('robot_moving') or gs.get('pending_dofbot') or gs.get('awaiting_initial_board'))  # 未闭环前不接受新棋局。


def start_hardware(data):  # 替代原版启动即 STM32 归零流程。
    app = app_module()  # 共用原网页状态。
    gs = app.game_state  # 只在完整校验后修改。
    ctl = get_controller()  # 不触发连接或动作。
    if blocked():  # 拒绝在执行中重建对局。
        return jsonify(success=False, error='当前动作或视觉确认未结束；先等待，故障时使用软件停止'), 409  # 不自动取消实体搬运。
    status = ctl.status()  # 查询连接与标定。
    if not status['connected'] or status['fault'] or status['busy']:  # 开始按钮需要先连接且空闲。
        return jsonify(success=False, error=status['fault'] or '请先连接走棋串口或模拟器'), 409  # 不把连接成功当作开局成功。
    color = data.get('ai_color', 'black')  # 用户可选择程序执色。
    first = data.get('first_player', 'red')  # 黑先手仅用于调试。
    if color not in ('red', 'black') or first not in ('red', 'black'):  # 校验枚举值。
        return jsonify(success=False, error='执色和先手只能为 red 或 black'), 400  # 不猜测拼写。
    engine = app.get_ai_engine()  # 引擎启动失败时不进入对局。
    if not engine.is_ready:  # 避免开局后才发现缺引擎。
        return jsonify(success=False, error='Pikafish 未就绪'), 503  # 页面显示可操作错误。
    engine.reset_game()  # 清理上一局引擎缓存。
    fen = app.apply_turn_to_fen(app.STANDARD_INITIAL_FEN, first)  # 标准初局按选择方开始。
    gs.update(initial_fen=fen, current_fen=fen, board_state=dict(app.STANDARD_INITIAL_BOARD), move_history=[], display_history=[], turn_count=0, is_game_running=True, ai_color=color, player_color=app.opposite_color(color), first_player=first, robot_mode='hardware', robot_moving=False, pending_ai_move=None, pending_dofbot=None, vision_pause_until=0.0, vision_pause_reason='', awaiting_physical_baseline=False, awaiting_initial_board=ctl.backend == 'serial' and data.get('input_source') in ('camera', 'network'), robot_baseline_match_count=0, last_player_move=None, last_ai_command_token=None, last_dofbot_token=None, post_robot_guard_until=0.0)  # 一次建立完整棋局。
    gs['game_id'] = uuid.uuid4().hex  # command_id 包含对局编号。
    gs['vision_feedback'] = data.get('input_source') in ('camera', 'network')  # 维持原实时视觉核对。
    gs['ai_analysis'] = {}  # 不显示上一局分析。
    if app.recognizer is not None:  # 清除旧局识别缓存。
        app.reset_dynamic_tracking(app.recognizer)  # 要求开局后采集新画面。
    return jsonify(success=True, message='已开始；实体模式先等待标准棋盘视觉确认', fen=fen, current_fen=fen, board_state=gs['board_state'], ai_color=color, player_color=gs['player_color'], first_player=first, current_turn=first, robot_mode='hardware', robot_backend=ctl.backend, awaiting_initial_board=gs['awaiting_initial_board'])  # 此步骤不移动机械臂。


def commit_pending():  # 只在模拟完成或新鲜视觉确认后提交。
    app = app_module()  # 与原页面共用状态。
    gs = app.game_state  # 准备提交的正式局面。
    pending = gs.get('pending_dofbot')  # 原局面与预期结果分开。
    if not pending:  # 同一落子不能提交两次。
        return False  # 重复请求无副作用。
    if pending.get('committed'):  # 网页已经同步显示此决策；视觉确认不能再记一步。
        gs['pending_dofbot'] = None
        gs['awaiting_physical_baseline'] = False
        gs['robot_baseline_match_count'] = 0
        gs['ai_analysis']['physical_board_confirmed'] = True
        if app.recognizer is not None:
            app.sync_dynamic_baseline_to_game_state(app.recognizer)
        return True
    preview = pending['preview']  # cchess 已校验结果。
    gs['board_state'] = preview['board_state']  # 替换所有存活棋子的坐标。
    gs['current_fen'] = preview['fen']  # 同步轮次和计数。
    gs['move_history'].append(pending['move'])  # 只记录一次正式落子。
    gs['display_history'].append(f"程序 {pending['move']}")  # 供原棋谱列表显示。
    gs['turn_count'] += 1  # 更新半步计数。
    gs['last_ai_command_token'] = pending['token']  # 记录已经处理的动作。
    gs['pending_dofbot'] = None  # 解除等待状态。
    gs['awaiting_physical_baseline'] = False  # 恢复人类走子识别。
    gs['robot_baseline_match_count'] = 0  # 下一动作重新计数。
    gs['ai_analysis']['ai_move_applied'] = True  # 前端此时才更新棋盘。
    gs['ai_analysis']['all_pieces'] = pieces_payload(gs['board_state'])  # 提供下完后的全部棋位。
    if app.recognizer is not None:  # 真实基线也在提交后同步。
        app.sync_dynamic_baseline_to_game_state(app.recognizer)  # 不把未落地的局面当作人类走子基准。
    return True  # 成功提交。


def apply_move(move, wait_for_vision=True):
    """功能：同一次 AI 决策同步串口和网页；不调用任何机械臂动作。

    move：Pikafish bestmove 的 ICCS；wait_for_vision：是否继续保留原实时视觉核对。
    串口只报告已写入，实体是否落子由后续新鲜视觉判定，不能把预测当成实测。
    """
    app = app_module()
    gs = app.game_state
    ctl = get_controller()
    with state_lock:
        if not gs.get('is_game_running'):
            raise ValueError('对局已结束，忽略迟到的 AI 走法')
        if gs.get('pending_dofbot') or ctl.fault:
            raise ValueError(ctl.fault or '上一决策尚待视觉核对')
        gs.setdefault('game_id', uuid.uuid4().hex)
        token = f"{gs['game_id']}:{len(gs['move_history'])}:{move}"
        if gs.get('last_dofbot_token') == token:
            raise ValueError('本步已经尝试下发，不自动重发')
        preview = checked_transition(gs['current_fen'], move)
        analysis = gs['ai_analysis']
        analysis.update(ai_move_token=token, ai_move_applied=False, robot_backend=ctl.backend,
                        robot_send_success=None, robot_send_acknowledged=False,
                        physical_board_confirmed=False, all_pieces=preview['pieces'])
        gs['last_dofbot_token'] = token  # 在发送前预留；短写后也不重新搜索/重发。
        try:
            payload = None
            if ctl.backend == 'serial':
                payload = ctl.send_move(move, gs['current_fen'], preview, token,
                                        gs['game_id'], len(gs['move_history']))
            gs['pending_dofbot'] = dict(move=move, token=token, preview=copy.deepcopy(preview))
            commit_pending()  # 网页、FEN、历史统一使用本次决策的同一份结果。
            if wait_for_vision and ctl.backend == 'serial' and gs.get('vision_feedback'):
                gs['pending_dofbot'] = dict(move=move, token=token, preview=copy.deepcopy(preview),
                                           serial_sent=True, committed=True)
                gs['awaiting_physical_baseline'] = True
                if app.recognizer is not None:
                    app.reset_dynamic_tracking(app.recognizer)
            gs['robot_moving'] = False
            gs['vision_pause_until'] = 0.0
            analysis.update(robot_send_success=True if payload else None,
                            robot_send_acknowledged=False, serial_payload=payload,
                            robot_send_response='SERIAL_WRITTEN' if payload else 'SIMULATION_ONLY',
                            robot_send_error='', robot_log_messages=[
                                f'AI 决策 {move} 已同步网页',
                                '同一步棋已写入串口（未确认实体执行）' if payload else '离线模拟，没有发送串口'])
            return RobotSendResult(success=True, command_text=move,
                                   response='SERIAL_WRITTEN' if payload else 'SIMULATION_ONLY')
        except Exception as exc:
            gs['robot_moving'] = False
            analysis.update(robot_send_success=False, robot_send_acknowledged=False,
                            robot_send_error=str(exc), ai_move_applied=False,
                            robot_log_messages=[str(exc), '未提交网页落子；禁止自动重发，请检查接收端'])
            return RobotSendResult(success=False, command_text=move, error=str(exc))


def apply_virtual(move):
    """功能：保留虚拟对局；move 是 ICCS；已连接串口时也同步发送同一决策。"""
    return apply_move(move, wait_for_vision=False)


def accept_observation(board, stable):  # 仅动态摄像头处理器调用；无网页伪造确认接口。
    gs = app_module().game_state  # 获取最新正式状态。
    pending = gs.get('pending_dofbot')  # 查找待确认的 AI 结果。
    expected = pending['preview']['board_state'] if pending else gs.get('board_state', {})  # 开局核对标准初局。
    if pending and not pending.get('serial_sent'):  # 动作期间的画面无效。
        return False, '本步尚未完成串口发送'  # 不累计计数。
    if not stable or board != expected:  # 必须颜色、类型、位置全部相等。
        gs['robot_baseline_match_count'] = 0  # 不连续就重新累计。
        return False, '等待新鲜稳定画面与预期全部棋子完全一致'  # 不只比较占用格。
    gs['robot_baseline_match_count'] = gs.get('robot_baseline_match_count', 0) + 1  # 计数来自多次摄像头处理。
    if gs['robot_baseline_match_count'] < 3:  # 需要连续三次稳定结果。
        return False, f"视觉确认 {gs['robot_baseline_match_count']}/3"  # 页面展示进度。
    if pending:  # 机械臂落子完成后提交。
        commit_pending()  # 原子切换正式状态。
    else:  # 开局基线确认成功。
        gs['awaiting_initial_board'] = False  # 解除初始棋盘等待。
        gs['robot_baseline_match_count'] = 0  # 为后续动作重置。
        app = app_module()  # 需要同步识别器基线。
        if app.recognizer is not None:  # 正常识别器已建立。
            app.sync_dynamic_baseline_to_game_state(app.recognizer)  # 锁定初始局面。
    return True, '全部棋子视觉确认通过，正式棋局已同步'  # 此后才允许下一步。


@bp.before_app_request  # 在所有旧路由前执行状态保护。
def protect_state():  # 请求结束由 after_request 释放锁。
    if getattr(app_module().config, 'VISION_ONLY', False) and request.method == 'POST':
        allowed = {'/api/recognize', '/api/recognize/dynamic', '/api/vision/observation',
                   '/api/camera/start', '/api/camera/stop', '/api/network_camera/connect',
                   '/api/network_camera/disconnect', '/api/local/stop'}
        if request.path not in allowed:
            return jsonify(success=False, error='当前为视觉预览模式，请先完成视觉验证'), 409
    if enabled() and request.method == 'POST' and request.headers.get('Origin'):  # 机器人操作只接受当前网页来源。
        origin = urlsplit(request.headers['Origin'])  # 浏览器自动提供该头。
        if origin.netloc != request.host or origin.scheme != request.scheme:  # 阻止外部网页调用本机动作接口。
            return jsonify(success=False, error='拒绝跨来源操作，请在当前对弈网页中操作'), 403  # 命令行调试无需 Origin。
    if request.method == 'POST' and request.path != '/api/local/stop':  # 读接口保持轻量。
        state_lock.acquire()  # 两个窗口不能同时修改局面。
        g.chro_state_locked = True  # 记录本请求的锁归属。
    if not enabled():  # 原 TCP 路径保持兼容。
        return None  # 交给原路由。
    gs = app_module().game_state  # 共享占用状态。
    if request.path in ('/api/game/start', '/api/game/reset', '/api/player_move', '/api/ai_move', '/api/recognize') and request.method == 'POST':  # 会改变棋局的入口。
        if blocked():  # 执行中或待确认不能重复操作。
            return jsonify(success=False, error='AI、机械臂或视觉确认正在进行，请等待；故障时使用软件停止'), 409  # 不重复下发。
    if gs.get('is_game_running') and request.method == 'POST' and request.path in ('/api/recognize', '/api/dofbot/calibration', '/api/dofbot/teach', '/api/dofbot/park', '/api/dofbot/clear', '/api/network_camera/connect', '/api/network_camera/disconnect', '/api/camera/start'):  # 对局期间禁止改变基线和硬件参数。
        return jsonify(success=False, error='请先结束对局再修改输入源、标定或手动动作'), 409  # 避免参数中途变化。
    return None  # 正常继续。


@bp.after_app_request  # 正常返回与错误响应都释放锁。
def release_state(response):  # 不持锁跨请求。
    if getattr(g, 'chro_state_locked', False):  # 只有当前请求取得过锁时释放。
        state_lock.release()  # 与 before_request 对称。
        g.chro_state_locked = False  # 避免重复释放。
    return response  # 保留原路由响应。


@bp.route('/api/dofbot/status')  # 纯查询，不自动连接。
def status():  # 页面独立显示硬件状态。
    try:  # 缺标定文件不应让网页崩溃。
        return jsonify(success=True, **get_controller().status())  # 含 mock/真机标识。
    except Exception as exc:  # 提示配置或导入错误。
        return jsonify(success=False, error=str(exc)), 400  # 客户端错误。


@bp.route('/api/dofbot/connect', methods=['POST'])  # 连接操作与开局分离。
def connect():  # 不发运动命令。
    try:  # 真实依赖失败会显示在网页。
        if blocked() or app_module().game_state.get('is_game_running'):  # 对局中不切换输出端口。
            return jsonify(success=False, error='当前任务尚未完成，请等待'), 409  # 减少读写冲突。
        data = request.get_json(silent=True) or {}
        if data.get('port'):  # 页面填写串口后切换输出后端，不触发运动。
            app_module().config.ROBOT_BACKEND = 'serial'
            app_module().config.ROBOT_SERIAL_PORT = data['port']
            app_module().config.ROBOT_SERIAL_BAUD = int(data.get('baud', 115200))
        return jsonify(success=True, **get_controller().connect(data.get('port'), data.get('baud')))  # 读取六轴通信反馈。
    except Exception as exc:  # 例如 Windows 无 Arm_Lib。
        return jsonify(success=False, error=f'走棋串口/模拟器连接失败：{exc}'), 503  # 不伪装为模拟连接。


@bp.route('/api/dofbot/calibration', methods=['GET', 'POST'])  # 完整 JSON 编辑接口。
def calibration():  # 网页可读取、保存、下载。
    try:  # 标定错误反馈给编辑器。
        ctl = get_controller()  # 复用唯一设备。
        if request.method == 'POST':  # 写配置前必须停止对局。
            ctl.save_calibration(request.get_json())  # 内部检查角度和结构。
        return jsonify(success=True, calibration=ctl.calibration, status=ctl.status())  # 显示完整性。
    except Exception as exc:  # 不保存非法输入。
        return jsonify(success=False, error=str(exc)), 400  # 保持旧标定文件。


@bp.route('/api/dofbot/angles')  # 只读示教辅助。
def angles():  # 页面可以复制当前六轴值。
    try:  # 需要先连接。
        return jsonify(success=True, angles=get_controller().read_angles())  # 无任何运动。
    except Exception as exc:  # 忙碌或离线时拒绝。
        return jsonify(success=False, error=str(exc)), 400  # 显示原因。


@bp.route('/api/dofbot/teach', methods=['POST'])  # 读取当前角度写入指定格。
def teach():  # 通过厂商工具手动摆到合适位置后记录。
    try:  # 部分数据也可保存。
        ctl = get_controller()  # 已连接的服务。
        data = request.get_json() or {}  # 读取目标名和高度。
        pose = ctl.read_angles()  # 记录真实反馈，不移动机械臂。
        saved = copy.deepcopy(ctl.calibration)  # 完整校验通过后替换。
        key, level = data.get('key'), data.get('level', 'above')  # key 为 col,row 或 capture:N 或 park。
        if key == 'park':  # 独立停靠姿态。
            saved['park'] = pose  # 记录退出棋盘的位置。
        elif level not in ('above', 'down'):  # 只有两种棋格高度。
            raise ValueError('level 需为 above 或 down')  # 不保存未知字段。
        elif isinstance(key, str) and key.startswith('capture:'):  # 吃子槽位。
            index = int(key.split(':')[1])  # 槽位从0开始。
            if not 0 <= index < 32:  # 最多32个存放位。
                raise ValueError('吃子槽号需为 0..31')  # 避免过大数组。
            slots = saved.setdefault('capture_slots', [])  # 获取现有槽表。
            while len(slots) <= index:  # 支持逐个示教。
                slots.append({})  # 缺失高度稍后补齐。
            slots[index][level] = pose  # 保存本次高度。
        else:  # 普通棋盘交叉点。
            saved.setdefault('cells', {}).setdefault(key, {})[level] = pose  # 后续校验坐标范围。
        ctl.save_calibration(saved)  # 验证并原子保存。
        return jsonify(success=True, angles=pose, calibration=saved, status=ctl.status())  # 更新编辑器。
    except Exception as exc:  # 无效示教不改变正式数据。
        return jsonify(success=False, error=str(exc)), 400  # 提示原因。


@bp.route('/api/dofbot/plan', methods=['POST'])  # 不发送运动命令的完整动作预演。
def plan():  # 可以在电脑查看吃子次序。
    try:  # 预演也经过规则检查。
        gs = app_module().game_state  # 当前正式局面。
        move = (request.get_json() or {}).get('move')  # ICCS格式。
        preview = checked_transition(gs['current_fen'], move)  # 非法走法不生成计划。
        return jsonify(success=True, plan=get_controller().plan(move, gs['board_state']), expected_fen=preview['fen'], all_pieces=preview['pieces'])  # 返回走完后的全部棋位。
    except Exception as exc:  # 缺标定时提示具体原因。
        return jsonify(success=False, error=str(exc)), 400  # 不执行任何动作。


@bp.route('/api/dofbot/park', methods=['POST'])  # 用户明确点击才会移动到停靠姿态。
def park():  # 不把连接操作绑定为归零。
    reserved = False  # 只有当前请求预留过才清理占用标志。
    try:  # 归位可能需要多秒。
        ctl = get_controller()  # 已配置设备。
        if ctl.backend != 'mock':
            return jsonify(success=False, error='本机停靠动作已移除；只发送走棋串口消息'), 410
        pose = validate_pose(ctl.calibration.get('park'))  # 未示教时直接拒绝。
        if ctl.busy or app_module().game_state.get('robot_moving'):  # 避免重复点击排队运动。
            raise ValueError('机械臂忙，请等待')  # 不排队重发。
        app_module().game_state['robot_moving'] = True  # 阻止此时开局。
        reserved = True  # 记录本次动作拥有占用标志。
        state_lock.release()  # 慢动作不能阻塞软件停止请求。
        g.chro_state_locked = False  # after_request 不再重复释放。
        ctl.execute(dict(capture=False, steps=[dict(label='park', angles=pose, gripping=False)]))  # 只保留 mock 轨迹记录；串口后端已在上方拒绝。
        return jsonify(success=True, **ctl.status())  # 返回完成或模拟结果。
    except Exception as exc:  # 失败时动作层锁存故障。
        return jsonify(success=False, error=str(exc)), 400  # 不重试。
    finally:  # 完成或失败都恢复占用状态。
        if reserved:  # 重复点击被拒绝时不能清除别人的动作状态。
            with state_lock:  # 与棋局入口同步。
                app_module().game_state['robot_moving'] = False  # 停止请求仍保留故障锁。


@bp.route('/api/dofbot/test', methods=['POST'])  # 仅用于电脑预演完整取放序列。
def test_sequence():  # 不修改正式棋局或真实槽位。
    try:  # 仿真对象与实际控制器完全隔离。
        if get_controller().backend != 'mock':  # 真机不能调用模拟测试动作。
            raise ValueError('模拟序列测试只允许 MOCK 后端；真机请先使用不运动的动作预演')  # 避免含糊动作入口。
        ctl = DofbotController('mock')  # 新建临时模拟器。
        ctl.connect()  # 只标记模拟连接。
        gs = app_module().game_state  # 以当前正式棋盘生成测试序列。
        move = (request.get_json() or {}).get('move')  # 默认由网页填写。
        checked_transition(gs['current_fen'], move)  # 测试也遵循走棋规则。
        planned = ctl.plan(move, gs['board_state'])  # 生成搬运步骤。
        ctl.execute(planned)  # 不导入 Arm_Lib，也不写 GPIO。
        return jsonify(success=True, backend='mock', trace=ctl.trace, board_unchanged=True)  # 返回轨迹用于查看。
    except Exception as exc:  # 错误只显示，不影响棋局。
        return jsonify(success=False, error=str(exc)), 400  # 标准错误结构。


@bp.route('/api/dofbot/stop', methods=['POST'])  # 软件停止始终可达。
def stop():  # 不需要等 AI/运动结束。
    app = app_module()  # 共用棋局。
    ctl = get_controller()  # 当前动作服务。
    ctl.stop()  # 当前运动段可能仍在执行；不切断电源。
    app.game_state['is_game_running'] = False  # 不再接收任何新走棋。
    app.game_state['pending_dofbot'] = None  # 不把半完成动作提交成正式棋谱。
    app.game_state['awaiting_initial_board'] = False  # 取消开局确认。
    app.game_state['awaiting_physical_baseline'] = False  # 需要重新摆棋。
    return jsonify(success=True, **ctl.status())  # 前端标红软件停止状态。


@bp.route('/api/dofbot/clear', methods=['POST'])  # 现场检查后解除软件故障。
def clear():  # 不发任何运动命令。
    ctl = get_controller()  # 当前设备。
    if ctl.busy or app_module().game_state.get('ai_thinking'):  # 当前段未结束不能解除。
        return jsonify(success=False, error='动作仍未结束，请等待'), 409  # 不与停止请求竞争。
    ctl.fault = ''  # 用户点击意味着已经完成现场检查。
    ctl.stop_event.clear()  # 允许之后新建任务。
    if (request.get_json(silent=True) or {}).get('empty_slots'):  # 只有显式确认槽位已清空才重置计数。
        ctl.capture_index = 0  # 下一吃子可重新使用0号槽。
        ctl.slots_confirmed = True  # 真机开局前确认收纳区。
    return jsonify(success=True, **ctl.status())  # 提示需要重摆标准初局。


@bp.route('/api/game/export')  # 下载棋谱与全部棋子坐标。
def export_game():  # 本机 JSON 文件便于后续串口模块复用。
    gs = app_module().game_state  # 只导出已经正式提交的结果。
    data = dict(format='CHRO-DOFBOT-1', coordinates='row=0..9 黑上红下，col=0..8 左到右；内部键为 col,row', initial_fen=gs['initial_fen'], current_fen=gs['current_fen'], moves=gs['move_history'], pieces=pieces_payload(gs.get('board_state') or {}), pending=bool(gs.get('pending_dofbot')), serial_payload=gs.get('ai_analysis', {}).get('serial_payload'), physical_board_confirmed=gs.get('ai_analysis', {}).get('physical_board_confirmed', False))  # 不导出停止凭据。
    return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename=xiangqi_game.json'})  # 浏览器直接下载。
