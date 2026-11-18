# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""走棋串口输出和离线动作预演；本机不访问舵机、夹爪或电磁铁。"""  # 不使用原 STM32 的五值指令。
import copy  # 隔离调用者的标定数据。
import json  # 读写示教文件。
import math  # 拒绝 NaN 等非法角度。
from pathlib import Path  # 处理跨平台路径。
import threading  # 串行化机械臂动作并提供停止信号。
import time  # 等待舵机运动完成。


def validate_pose(pose):  # 校验六个舵机角度。
    if not isinstance(pose, list) or len(pose) != 6:  # 每个姿态必须完整。
        raise ValueError('姿态必须是六个舵机角度的数组')  # 不猜测缺失角度。
    limits = [180, 180, 180, 180, 270, 180]  # 标准 DOFBOT 第五舵机范围为 270°。
    for value, limit in zip(pose, limits):  # 分别检查各轴。
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= limit:  # 限制到标准库接口范围。
            raise ValueError(f'舵机角度需为有限数值，范围 0..{limit}')  # 提供明确反馈。
    return [int(round(v)) for v in pose]  # Arm_Lib 接收整数角度。


def validate_calibration(data, complete=False):  # 部分示教可保存，实体开局要求完整。
    result = copy.deepcopy(data)  # 不修改网页提交的数据。
    if result.get('end_effector', 'gripper') not in ('gripper', 'magnet'):  # 两种末端工具。
        raise ValueError('end_effector 仅支持 gripper 或 magnet')  # 不默认驱动未知硬件。
    if not 200 <= result.get('move_ms', 1000) <= 10000:  # 限制单步运动时间。
        raise ValueError('move_ms 需为 200..10000 毫秒')  # 避免过快运动。
    if not 0.05 <= result.get('settle_s', 0.3) <= 5:  # 每步附加稳定时间。
        raise ValueError('settle_s 需为 0.05..5 秒')  # 方便调参。
    if not 1 <= result.get('tolerance_deg', 8) <= 30:  # 反馈角度误差限。
        raise ValueError('tolerance_deg 需为 1..30')  # 校验机械臂到位反馈。
    if result.get('end_effector', 'gripper') == 'magnet':  # 电磁铁通过独立驱动电路。
        pin = result.get('magnet_bcm')  # 使用 BCM 编号。
        if type(pin) is not int or not 0 <= pin <= 27:  # 用户必须明确接线引脚。
            raise ValueError('电磁铁需要配置 magnet_bcm（0..27）')  # 不猜测 GPIO。
        if type(result.get('magnet_active_high', True)) is not bool:  # 极性必须明确。
            raise ValueError('magnet_active_high 必须是布尔值')  # 避免字符串误判。
    for name in ('open_angle', 'close_angle'):  # 夹爪开合角度可单独调整。
        value = result.get(name, 60 if name == 'open_angle' else 125)  # 仅作配置默认值。
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 180:  # 夹爪为六号舵机。
            raise ValueError(f'{name} 需为 0..180')  # 要求实物示教合适力度。
    if result.get('park') is not None:  # 停靠姿态可分步录入。
        result['park'] = validate_pose(result['park'])  # 检查安全停靠角度。
    cells = result.get('cells', {})  # 棋格采用字符串 col,row。
    slots = result.get('capture_slots', [])  # 每个被吃棋子使用独立槽位。
    if not isinstance(cells, dict) or not isinstance(slots, list) or len(slots) > 32:  # 限制结构大小。
        raise ValueError('cells 需为字典，capture_slots 需为最多32个槽位的数组')  # 避免混淆坐标。
    for key, entry in cells.items():  # 检查所有已示教棋格。
        if key not in {f'{c},{r}' for c in range(9) for r in range(10)}:  # 排除越界或其他坐标格式。
            raise ValueError(f'非法棋格 {key}；格式为 col,row')  # 提醒前端坐标顺序。
        for name, pose in entry.items():  # above 和 down 可分开录入。
            if name not in ('above', 'down'):  # 不接受拼写错误。
                raise ValueError('棋格姿态仅支持 above 和 down')  # 减少调试误差。
            entry[name] = validate_pose(pose)  # 检查角度。
    for entry in slots:  # 检查吃子存放位置。
        for name, pose in entry.items():  # 允许部分示教。
            if name not in ('above', 'down'):  # 固定姿态名称。
                raise ValueError('槽位姿态仅支持 above 和 down')  # 提示错误。
            entry[name] = validate_pose(pose)  # 检查各轴角度。
    if complete:  # 实体开局前检查全部可达格。
        missing = [f'{c},{r}' for r in range(10) for c in range(9) if set(cells.get(f'{c},{r}', {})) != {'above', 'down'}]  # 不凭插值猜测逆解。
        if missing or not result.get('park') or not slots or any(set(x) != {'above', 'down'} for x in slots):  # 所有必需位置都要示教。
            raise ValueError(f'标定未完成：缺少 {len(missing)} 个棋格的完整姿态，且需 park 和至少一个完整吃子槽位')  # 给出补齐方向。
    return result  # 返回可保存的数据。


def mock_calibration():  # 生成仅供电脑预演的角度表。
    pose = [90, 90, 90, 90, 90, 60]  # 不对应任何实际棋盘位置。
    entry = dict(above=pose[:], down=[90, 100, 90, 90, 90, 60])  # 模拟接近与下降。
    return dict(end_effector='gripper', move_ms=1000, settle_s=0.3, tolerance_deg=8, open_angle=60, close_angle=125, park=pose, cells={f'{c},{r}': copy.deepcopy(entry) for c in range(9) for r in range(10)}, capture_slots=[copy.deepcopy(entry) for _ in range(32)], mock_only=True)  # 标记不得作为实体标定。


class DofbotController:  # 将硬件访问与棋局逻辑隔离。
    def __init__(self, backend='mock', calibration_path='data/dofbot_calibration.json', device_factory=None, sleeper=time.sleep):  # 测试可注入假设备。
        self.backend = 'serial' if backend == 'dofbot' else backend  # 旧参数仅作为串口后端别名。
        if self.backend not in ('mock', 'serial'):
            raise ValueError('backend 只能为 mock 或 serial')
        self.serial_port = None  # pyserial 对象；只有显式连接时才打开。
        self.last_payload = None  # 最近一次发送的棋盘级消息。
        self.attempted = set()  # 已尝试的 command_id，异常也不自动重发。
        self.path = Path(calibration_path)  # 真机示教文件。
        self.device_factory = device_factory  # 单元测试注入点。
        self.sleep = sleeper  # 真机使用真实等待。
        self.connected = False  # 连接状态独立于对局。
        self.busy = False  # 阻止并发动作。
        self.fault = ''  # 失败后锁定，防止自动重发。
        self.stop_event = threading.Event()  # 下一段指令前检查停止。
        self.lock = threading.RLock()  # 标定、读取和动作共用锁。
        self.capture_index = 0  # 每次吃子后占用一个独立槽位。
        self.slots_confirmed = self.backend == 'mock'  # 真机每次启动都需要人工确认收纳区已清空。
        self.trace = []  # 保存最近一次动作预演。
        self.calibration = (validate_calibration(json.loads(self.path.read_text(encoding='utf-8')))
                            if self.path.is_file() else mock_calibration()) if self.backend == 'mock' else dict(cells={}, capture_slots=[])  # 串口不读取或依赖实机角度标定。

    def connect(self, port=None, baud=None):
        """功能：打开走棋串口；port 是 COM3 或 /dev/ttyUSB0；baud 是波特率。

        来源：pyserial.Serial；timeout/write_timeout 单位为秒。mock 仅建立模拟连接。
        连接本身不发送走棋、归零、角度或末端工具命令。
        """
        with self.lock:
            if self.backend == 'mock':
                self.connected = True
            else:
                import config
                import serial
                port = port or getattr(config, 'ROBOT_SERIAL_PORT', '')
                baud = int(baud or getattr(config, 'ROBOT_SERIAL_BAUD', 115200))
                if not isinstance(port, str) or not port.strip() or baud <= 0:
                    raise ValueError('请填写串口名和正数波特率')
                if self.serial_port is not None and self.serial_port.is_open:
                    if self.serial_port.port == port and self.serial_port.baudrate == baud:
                        return self.status()
                    self.serial_port.close()
                self.connected = False
                self.serial_port = serial.Serial(port, baud, timeout=0.1, write_timeout=2)
                self.connected = bool(self.serial_port.is_open)
            return self.status()

    def send_move(self, move, before_fen, preview, command_id, game_id, ply):
        """功能：串口发送与网页相同的 AI 决策，返回消息字典；无动作控制。

        move：ICCS 如 b2e2；before_fen：走前 FEN；preview：rules.checked_transition 返回值；
        command_id：本步唯一编号；game_id：对局编号；ply：走前半步数。
        json.dumps 为标准库编码，Serial.write 为 pyserial 字节写入；写入成功不等于机械臂执行成功。
        """
        payload = dict(type='MOVE', command_id=command_id, game_id=game_id, ply=ply,
                       move=move, **{'from': dict(row=9-int(move[1]), col=ord(move[0])-97),
                                    'to': dict(row=9-int(move[3]), col=ord(move[2])-97)},
                       piece=preview['board_state'][f'{ord(move[2])-97},{9-int(move[3])}'], capture=preview['capture'],
                       before_fen=before_fen, expected_fen=preview['fen'], pieces=preview['pieces'])
        with self.lock:
            if self.backend != 'serial' or not self.connected or self.serial_port is None or not self.serial_port.is_open:
                raise ValueError('走棋串口尚未连接；本步没有发送')
            if self.fault or self.stop_event.is_set():
                raise ValueError(self.fault or '输出已停止')
            if command_id in self.attempted:
                raise ValueError('本步已经尝试发送，禁止自动重发；请由下位机按 command_id 去重')
            wire = (json.dumps(payload, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii')
            self.attempted.add(command_id)
            self.busy = True
            try:
                if self.serial_port.write(wire) != len(wire):
                    raise IOError('串口发生短写，接收情况未知')
                self.last_payload = payload
                return payload
            except Exception as exc:
                self.fault = '串口发送失败（可能已部分发送，禁止自动重发）：' + str(exc)
                self.connected = bool(self.serial_port.is_open)
                raise
            finally:
                self.busy = False

    def close(self):
        """功能：关闭本进程持有的串口；无参数，不向下位机发送命令。"""
        with self.lock:
            if self.serial_port is not None:
                self.serial_port.close()
            self.connected = False

    def read_angles(self):
        """功能：保留离线预演的固定角度；无参数，串口后端不读取真实关节。"""
        if self.backend != 'mock':
            raise ValueError('本项目只发送如何走棋，不读取或控制机械臂关节')
        return [90, 90, 90, 90, 90, 60]

    def status(self):  # 只返回页面需要的状态。
        if self.backend == 'serial':
            self.connected = bool(self.serial_port is not None and self.serial_port.is_open)
            return dict(backend='serial', connected=self.connected, busy=self.busy, fault=self.fault,
                        port=getattr(self.serial_port, 'port', ''), baud=getattr(self.serial_port, 'baudrate', None),
                        calibrated=True, calibration_error='', capture_used=0, capture_capacity=0,
                        slots_confirmed=True, trace=[], last_payload=self.last_payload, execution_confirmed=False)
        try:  # 完整性只作校验，不动作。
            validate_calibration(self.calibration, complete=True)  # 检查全部示教数据。
            ready = self.backend == 'mock' or not self.calibration.get('mock_only')  # 真机拒绝模拟表。
            reason = '' if ready else '模拟角度表不能用于实体机械臂'  # 清楚标识限制。
        except (ValueError, TypeError, AttributeError) as exc:  # 部分标定仍可连接。
            ready, reason = False, str(exc)  # 提示还需示教。
        return dict(backend=self.backend, connected=self.connected, busy=self.busy, fault=self.fault, calibrated=ready, calibration_error=reason, capture_used=self.capture_index, capture_capacity=len(self.calibration.get('capture_slots', [])), slots_confirmed=self.slots_confirmed, trace=self.trace[-100:])  # 不暴露无关信息。

    def save_calibration(self, data):  # 可通过网页 JSON 编辑或示教保存。
        if self.busy:  # 动作过程中不更换参数。
            raise ValueError('机械臂忙，不能修改标定')  # 保持计划一致。
        checked = validate_calibration(data)  # 写入前校验。
        if self.backend != 'mock' and checked.get('mock_only'):  # 阻止复制模拟参数到真机。
            raise ValueError('不能将模拟标定用于实体机械臂')  # 需要真实示教。
        self.path.parent.mkdir(parents=True, exist_ok=True)  # 创建数据目录。
        temporary = self.path.with_suffix('.tmp')  # 原子替换防止断电损坏旧文件。
        temporary.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding='utf-8')  # 保存易于修改的 JSON。
        temporary.replace(self.path)  # 完整写入后替换。
        self.calibration = checked  # 更新内存参数。
        return self.status()  # 显示最新完整性。

    def plan(self, move, board):  # 仅生成离线预览，绝不发送。
        if not isinstance(move, str) or len(move) != 4 or move[0] not in 'abcdefghi' or move[2] not in 'abcdefghi' or move[1] not in '0123456789' or move[3] not in '0123456789':  # 拒绝错误坐标。
            raise ValueError('走法格式应为 b2e2')  # Pikafish 的纵坐标为 0..9。
        source, target = [f'{ord(move[i])-97},{9-int(move[i+1])}' for i in (0, 2)]  # 转为黑方在上的 col,row。
        if not board.get(source):  # 不能搬空格。
            raise ValueError('起点没有棋子')  # 由规则层先检查合法性。
        capture = bool(board.get(target))  # 先移走被吃棋子。
        if self.backend == 'serial':
            return dict(move=move, source=source, target=target, capture=capture, steps=[], transport='serial', preview_only=True)
        data = self.calibration  # 本次采用当前标定。
        if self.backend != 'mock' and data.get('mock_only'):  # 不允许模拟表控制硬件。
            raise ValueError('真机需要实际标定')  # 不执行假角度。
        validate_pose(data.get('park'))  # 完成搬运必须能退出棋盘。
        transfers = []  # 每项含起止姿态。
        if capture:  # 吃子需要独立槽位。
            slots = data.get('capture_slots', [])  # 查询收纳区。
            if self.capture_index >= len(slots):  # 不覆盖已占用槽位。
                raise ValueError('吃子槽位已满，请清空后重新确认')  # 禁止继续动作。
            transfers.append((target, f'capture:{self.capture_index}', data['cells'].get(target, {}), slots[self.capture_index]))  # 首先移除被吃棋子。
        transfers.append((source, target, data['cells'].get(source, {}), data['cells'].get(target, {})))  # 再搬主棋子。
        steps = []  # 预先检查完整计划后才允许任何运动。
        for start, end, a, b in transfers:  # 两次搬运之间经停靠点。
            for name, pose, grip in [('park', data['park'], False), (start+':above', a.get('above'), False), (start+':down', a.get('down'), False), (start+':grasp', a.get('down'), True), (start+':lift', a.get('above'), True), (end+':above', b.get('above'), True), (end+':down', b.get('down'), True), (end+':release', b.get('down'), False), (end+':lift', b.get('above'), False), ('park', data['park'], False)]:  # 固定取放次序。
                angles = validate_pose(pose)  # 缺任一姿态就整条拒绝。
                if data.get('end_effector', 'gripper') == 'gripper':  # 夹爪使用六号舵机。
                    angles[5] = round(data.get('close_angle', 125) if grip else data.get('open_angle', 60))  # 使用示教开合角。
                steps.append(dict(label=name, angles=angles, gripping=grip))  # 前端可查看每段。
        return dict(move=move, source=source, target=target, capture=capture, steps=steps, backend=self.backend)  # 预演不改变棋局。

    def execute(self, plan):
        """功能：仅在 mock 模式记录离线预演；plan 是 plan() 生成的步骤字典。

        串口后端必须通过 send_move 发送棋盘级决策，不接受关节或末端工具命令。
        """
        if self.backend != 'mock':
            raise ValueError('本机机械臂动作控制已移除，请使用串口走棋消息')
        with self.lock:
            if not self.connected or self.busy or self.fault:
                raise ValueError(self.fault or '请先连接空闲的模拟器')
            self.busy = True
            try:
                for step in plan['steps']:
                    if self.stop_event.is_set():
                        raise RuntimeError('离线预演已停止')
                    self.trace.append(copy.deepcopy(step))
                if plan.get('capture'):
                    self.capture_index += 1
                return True
            finally:
                self.busy = False

    def stop(self):  # 无需等待动作锁即可接受请求。
        self.stop_event.set()  # 当前段结束后不再执行下一段。
        self.fault = '本机已停止后续输出；已发出的消息由下位机自行处理'  # 不冒充物理急停。
        return self.status()  # 返回给网页。
