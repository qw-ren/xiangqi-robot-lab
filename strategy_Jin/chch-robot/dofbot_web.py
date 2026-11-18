# -*- coding: utf-8 -*-  # 中文注释便于后续修改。
"""DOFBOT 网页服务和落子确认；原 STM32 TCP 路径仍单独保留。"""  # 仅启用对应启动参数时使用。
import copy  # 保存未提交的预期棋局。
import json  # 导出棋谱与全部棋子坐标。
import threading  # 状态更新使用同一个锁。
from urllib.parse import urlsplit  # 校验浏览器提交来源。
from flask import Blueprint, Response, g, jsonify, request  # 扩展原 Flask 网页。
from chro_web_tools import checked_transition  # 规则层作为局面的唯一校验来源。
from dofbot_adapter import DofbotController, validate_calibration, validate_pose  # 直接 Arm_Lib 控制层。
from robot import RobotSendResult  # 兼容原 AI 调用接口。

bp = Blueprint('dofbot', __name__)  # 集中注册扩展接口。
state_lock = threading.RLock()  # 防止两个浏览器同时走棋。
controller = None  # 连接按钮按需创建设备。


def app_module():  # 延迟导入以避免循环引用。
    from web_simulation import app  # 与原网页共享唯一状态。
    return app  # 返回组合模块。


def enabled():  # 原版测试默认仍使用原 TCP 路径。
    return getattr(app_module().config, 'ROBOT_BACKEND', 'tcp') in ('mock', 'dofbot')  # 新入口显式选择后启用。


def get_controller():  # 创建当前进程唯一的机械臂服务。
    global controller  # 复用设备对象。
    config = app_module().config  # 获取启动参数。
    backend = getattr(config, 'ROBOT_BACKEND', 'mock')  # 电脑默认预演。
    if backend not in ('mock', 'dofbot'):  # 原 TCP 不创建 DOFBOT。
        raise ValueError('当前启动方式未启用 DOFBOT；请使用 run_local.py')  # 提供正确入口。
    if controller is None or controller.backend != backend:  # 首次创建或测试切换时替换。
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
        return jsonify(success=False, error=status['fault'] or '请先点击连接 DOFBOT'), 409  # 不把连接成功当作开局成功。
    if not status['calibrated']:  # 实体对弈使用所有可达格。
        return jsonify(success=False, error=status['calibration_error']), 400  # 未标定不能运动。
    if not status['slots_confirmed']:  # 重启后不猜测吃子槽是否仍有棋子。
        return jsonify(success=False, error='请先清空收纳区，再点击确认吃子槽位已清空'), 400  # 防止覆盖上次留下的棋子。
    if ctl.backend == 'dofbot' and data.get('input_source') not in ('camera', 'network'):  # 实机依赖新鲜摄像头闭环。
        return jsonify(success=False, error='实体模式请选择本地或网络摄像头，不能用静态图片确认落子'), 400  # 阻止离线图片充当反馈。
    color = data.get('ai_color', 'black')  # 用户可选择程序执色。
    first = data.get('first_player', 'red')  # 黑先手仅用于调试。
    if color not in ('red', 'black') or first not in ('red', 'black'):  # 校验枚举值。
        return jsonify(success=False, error='执色和先手只能为 red 或 black'), 400  # 不猜测拼写。
    engine = app.get_ai_engine()  # 引擎启动失败时不进入对局。
    if not engine.is_ready:  # 避免开局后才发现缺引擎。
        return jsonify(success=False, error='Pikafish 未就绪'), 503  # 页面显示可操作错误。
    engine.reset_game()  # 清理上一局引擎缓存。
    fen = app.apply_turn_to_fen(app.STANDARD_INITIAL_FEN, first)  # 标准初局按选择方开始。
    gs.update(initial_fen=fen, current_fen=fen, board_state=dict(app.STANDARD_INITIAL_BOARD), move_history=[], display_history=[], turn_count=0, is_game_running=True, ai_color=color, player_color=app.opposite_color(color), first_player=first, robot_mode='hardware', robot_moving=False, pending_ai_move=None, pending_dofbot=None, vision_pause_until=0.0, vision_pause_reason='', awaiting_physical_baseline=False, awaiting_initial_board=ctl.backend == 'dofbot', robot_baseline_match_count=0, last_player_move=None, last_ai_command_token=None, last_dofbot_token=None, post_robot_guard_until=0.0)  # 一次建立完整棋局。
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


def apply_move(move):  # 由原 AI 工作线程调用。
    app = app_module()  # 不另建棋局。
    gs = app.game_state  # 取得唯一正式状态。
    ctl = get_controller()  # 已经通过连接按钮初始化。
    with state_lock:  # 先预留动作令牌，再离锁执行慢动作。
        if not gs.get('is_game_running'):  # 服务停止或用户结束后不能下发迟到的搜索结果。
            raise ValueError('对局已结束，忽略迟到的 AI 走法')  # 不连接硬件。
        if gs.get('pending_dofbot') or ctl.fault:  # 失败或待确认时不能重发。
            raise ValueError(ctl.fault or '上一动作正在等待视觉确认')  # 保持旧正式局面。
        token = f"{len(gs['move_history'])}:{move}"  # 相同局面只发一次。
        if gs.get('last_dofbot_token') == token:  # 网络重复请求不重复运动。
            raise ValueError('相同动作已下发，不能自动重发')  # 故障后必须人工检查。
        preview = checked_transition(gs['current_fen'], move)  # 先规则校验。
        plan = ctl.plan(move, gs['board_state'])  # 完整预检查标定和吃子槽。
        gs['pending_dofbot'] = dict(move=move, token=token, preview=copy.deepcopy(preview), motion_done=False)  # 旧棋局仍为正式状态。
        gs['last_dofbot_token'] = token  # 下发前保存，异常也不自动重发。
        gs['robot_moving'] = True  # 识别线程暂停处理人类走子。
        gs['ai_analysis'].update(ai_move_token=token, ai_move_applied=False, robot_backend=ctl.backend, robot_send_success=None, robot_send_acknowledged=False, robot_log_messages=['DOFBOT 动作已规划；正式棋局等待动作和视觉确认'])  # 显示真实阶段。
    try:  # 慢动作不持有棋局锁，软件停止请求可以进入。
        ctl.execute(plan)  # 真机调用 Arm_Lib；mock 只记录轨迹。
        with state_lock:  # 写入完成状态。
            pending = gs.get('pending_dofbot')  # 软件停止可能取消棋局。
            if not pending or ctl.stop_event.is_set():  # 已停止不能宣布成功。
                raise RuntimeError('动作已停止，正式棋局未提交')  # 留待现场检查。
            pending['motion_done'] = True  # 机械臂已返回停靠点。
            gs['robot_moving'] = False  # 允许重新采集画面。
            gs['awaiting_physical_baseline'] = ctl.backend == 'dofbot'  # 真机必须视觉核对。
            gs['robot_baseline_match_count'] = 0  # 清除动作前稳定计数。
            gs['ai_analysis'].update(robot_send_success=True, robot_send_acknowledged=True, robot_send_response='DOFBOT_MOTION_DONE', robot_log_messages=['DOFBOT 已完成动作并退回停靠点；等待新的稳定画面核对全部棋子'])  # 动作完成不等于棋局已提交。
            if app.recognizer is not None:  # 丢弃动作前的稳定帧。
                app.reset_dynamic_tracking(app.recognizer)  # 后续只接受新识别。
            if ctl.backend == 'mock':  # 电脑预演无真实棋盘。
                commit_pending()  # 模拟整轮完成，页面明确显示 mock。
        return RobotSendResult(success=True, command_text=move, response='STATE:5,RESULT:1')  # 兼容原 AI 调用者。
    except Exception as exc:  # 不回滚或猜测真实棋子位置。
        with state_lock:  # 原正式局面保持不变。
            gs['robot_moving'] = False  # 页面可接收停止/故障处理。
            gs['ai_analysis'].update(robot_send_success=False, robot_send_acknowledged=False, robot_send_error=str(exc), ai_move_applied=False, robot_log_messages=[str(exc), '禁止自动重发；检查棋盘后结束本局重新摆棋'])  # 不把失败当成玩家回合。
        return RobotSendResult(success=False, command_text=move, error=str(exc))  # 前端显示失败。


def apply_virtual(move):  # 纯虚拟棋局无需等待实体运动计时器。
    with state_lock:  # 提交棋位、FEN 和历史时保持一致。
        gs = app_module().game_state  # 唯一正式状态。
        if not gs.get('is_game_running'):  # 已结束的旧搜索不能改动新界面。
            raise ValueError('对局已结束，忽略迟到的 AI 走法')  # 保持结束状态。
        preview = checked_transition(gs['current_fen'], move)  # 校验 AI 结果。
        gs['board_state'] = preview['board_state']  # 提交全部棋位。
        gs['current_fen'] = preview['fen']  # 更新轮次。
        gs['move_history'].append(move)  # 记录 AI 走棋。
        gs['display_history'].append(f'程序 {move}')  # 原网页棋谱格式。
        gs['turn_count'] += 1  # 更新步数。
        gs['robot_moving'] = False  # 无实体动作，不保留15秒占用。
        gs['vision_pause_until'] = 0.0  # 下一轮可以继续输入。
        gs['ai_analysis'].update(ai_move_applied=True, robot_send_success=True, robot_send_acknowledged=True, robot_backend='virtual', all_pieces=preview['pieces'])  # 返回真实虚拟状态。
        if app_module().recognizer is not None:  # 摄像头模拟对弈也要同步基线。
            app_module().sync_dynamic_baseline_to_game_state(app_module().recognizer)  # 复用原识别器。
        return RobotSendResult(success=True, command_text=move, response='RESULT:1,MODE:simulation')  # 不连接任何控制器。


def accept_observation(board, stable):  # 仅动态摄像头处理器调用；无网页伪造确认接口。
    gs = app_module().game_state  # 获取最新正式状态。
    pending = gs.get('pending_dofbot')  # 查找待确认的 AI 结果。
    expected = pending['preview']['board_state'] if pending else gs.get('board_state', {})  # 开局核对标准初局。
    if pending and not pending.get('motion_done'):  # 动作期间的画面无效。
        return False, '机械臂尚未结束动作'  # 不累计计数。
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
        if blocked():  # 动作或视觉确认期间不重新探测总线。
            return jsonify(success=False, error='当前任务尚未完成，请等待'), 409  # 减少读写冲突。
        return jsonify(success=True, **get_controller().connect())  # 读取六轴通信反馈。
    except Exception as exc:  # 例如 Windows 无 Arm_Lib。
        return jsonify(success=False, error=f'DOFBOT 连接失败：{exc}'), 503  # 不伪装为模拟连接。


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
        pose = validate_pose(ctl.calibration.get('park'))  # 未示教时直接拒绝。
        if ctl.busy or app_module().game_state.get('robot_moving'):  # 避免重复点击排队运动。
            raise ValueError('机械臂忙，请等待')  # 不排队重发。
        app_module().game_state['robot_moving'] = True  # 阻止此时开局。
        reserved = True  # 记录本次动作拥有占用标志。
        state_lock.release()  # 慢动作不能阻塞软件停止请求。
        g.chro_state_locked = False  # after_request 不再重复释放。
        ctl.execute(dict(capture=False, steps=[dict(label='park', angles=pose, gripping=False)]))  # 这是实际运动入口。
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
    data = dict(format='CHRO-DOFBOT-1', coordinates='row=0..9 黑上红下，col=0..8 左到右；内部键为 col,row', initial_fen=gs['initial_fen'], current_fen=gs['current_fen'], moves=gs['move_history'], pieces=pieces_payload(gs.get('board_state') or {}), pending=bool(gs.get('pending_dofbot')))  # 不导出停止凭据。
    return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename=xiangqi_game.json'})  # 浏览器直接下载。
