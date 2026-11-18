# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""DOFBOT 动作层：示教姿态、吃子搬运、模拟执行和软件停止。"""  # 不使用原 STM32 的五值指令。
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
        self.backend = backend  # mock 不导入 Arm_Lib。
        self.path = Path(calibration_path)  # 真机示教文件。
        self.device_factory = device_factory  # 单元测试注入点。
        self.sleep = sleeper  # 真机使用真实等待。
        self.arm = None  # 连接时才创建设备。
        self.magnet = None  # 电磁铁 GPIO 对象按需创建。
        self.connected = False  # 连接状态独立于对局。
        self.busy = False  # 阻止并发动作。
        self.fault = ''  # 失败后锁定，防止自动重发。
        self.stop_event = threading.Event()  # 下一段指令前检查停止。
        self.lock = threading.RLock()  # 标定、读取和动作共用锁。
        self.capture_index = 0  # 每次吃子后占用一个独立槽位。
        self.slots_confirmed = backend == 'mock'  # 真机每次启动都需要人工确认收纳区已清空。
        self.trace = []  # 保存最近一次动作预演。
        self.calibration = validate_calibration(json.loads(self.path.read_text(encoding='utf-8'))) if self.path.is_file() else (mock_calibration() if backend == 'mock' else dict(cells={}, capture_slots=[]))  # 未标定的真机不填假角度。

    def connect(self):  # 连接仅检测通信，绝不归零。
        with self.lock:  # 防止重复创建设备。
            if self.backend == 'mock':  # 本机调试无需硬件库。
                self.connected = True  # 标记模拟连接成功。
            else:  # 树莓派直接访问扩展板。
                if self.arm is None:  # 已连接时复用对象。
                    if self.device_factory is None:  # 实际运行使用官方库。
                        from Arm_Lib import Arm_Device  # Windows 无库时给出导入错误。
                        self.device_factory = Arm_Device  # 保存构造器。
                    self.arm = self.device_factory()  # 仅创建通信对象。
                self.read_angles()  # 必须能读到六轴反馈。
                self.connected = True  # 反馈成功才算连接。
            return self.status()  # 前端显示实际类型。

    def read_angles(self):  # 只读关节角度，用于示教。
        if self.busy:  # 搬运时不允许并发读写。
            raise ValueError('机械臂忙，请等待动作完成')  # 减少总线冲突。
        if self.backend == 'mock':  # 模拟固定姿态。
            return [90, 90, 90, 90, 90, 60]  # 不冒充实际反馈。
        if self.arm is None:  # 未建立对象。
            raise ValueError('请先连接 DOFBOT')  # 不在读取时自动运动。
        values = [self.arm.Arm_serial_servo_read(i) for i in range(1, 7)]  # 官方六轴读取接口。
        return validate_pose(values)  # None 或总线错误不能算连接成功。

    def status(self):  # 只返回页面需要的状态。
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

    def plan(self, move, board):  # 从 ICCS 走法生成完整吃子/搬运序列。
        if not isinstance(move, str) or len(move) != 4 or move[0] not in 'abcdefghi' or move[2] not in 'abcdefghi' or move[1] not in '0123456789' or move[3] not in '0123456789':  # 拒绝错误坐标。
            raise ValueError('走法格式应为 b2e2')  # Pikafish 的纵坐标为 0..9。
        source, target = [f'{ord(move[i])-97},{9-int(move[i+1])}' for i in (0, 2)]  # 转为黑方在上的 col,row。
        if not board.get(source):  # 不能搬空格。
            raise ValueError('起点没有棋子')  # 由规则层先检查合法性。
        capture = bool(board.get(target))  # 先移走被吃棋子。
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

    def execute(self, plan):  # 在 AI 工作线程里执行已校验计划。
        with self.lock:  # 整段动作只允许一个任务。
            if not self.connected or self.busy or self.fault:  # 故障不自动重试。
                raise ValueError(self.fault or '请先连接机械臂，且等待空闲')  # 明确状态要求。
            self.busy = True  # 提前占用执行权。
            self.stop_event.clear()  # 新任务建立停止边界。
            self.trace = []  # 记录本次实际下发段。
            data = copy.deepcopy(self.calibration)  # 动作期间不接受参数变化。
            try:  # 出错锁存故障。
                if self.backend != 'mock' and data.get('end_effector') == 'magnet' and self.magnet is None:  # 惰性初始化 GPIO。
                    from gpiozero import OutputDevice  # 仅电磁铁模式需要此依赖。
                    self.magnet = OutputDevice(data['magnet_bcm'], active_high=data.get('magnet_active_high', True), initial_value=False)  # 引脚连接驱动板，不能直接驱动线圈。
                for step in plan['steps']:  # 按预演顺序执行。
                    if self.stop_event.is_set():  # 每段前检查停止请求。
                        raise RuntimeError('已软件停止；检查实体棋盘后重建对局')  # 不再下发后续舵机命令。
                    if self.backend != 'mock':  # 真机调用官方接口。
                        self.arm.Arm_serial_servo_write6(*step['angles'], int(data.get('move_ms', 1000)))  # 同时写六轴。
                        self.sleep(data.get('move_ms', 1000)/1000 + data.get('settle_s', 0.3))  # 等待预计运动时间。
                        actual = validate_pose([self.arm.Arm_serial_servo_read(i) for i in range(1, 7)])  # 读取到位反馈。
                        if any(abs(a-b) > data.get('tolerance_deg', 8) for a, b in zip(actual, step['angles'])):  # 未到位不继续搬运。
                            raise RuntimeError('舵机反馈未到位，请检查卡阻、供电和标定')  # 停止后续段。
                        if self.magnet is not None:  # 电磁铁取放在到位后切换。
                            self.magnet.value = step['gripping']  # 控制隔离驱动电路。
                            self.sleep(data.get('settle_s', 0.3))  # 留出吸合/释放时间。
                    if self.stop_event.is_set():  # 即使停止发生在最后一段，也不能报告正常完成。
                        raise RuntimeError('已软件停止；检查实体棋盘后重建对局')  # 机械臂可能停在半途。
                    self.trace.append(copy.deepcopy(step))  # 仅记录已完成的段。
                if plan['capture']:  # 只有成功完成搬运才占用下一槽位。
                    self.capture_index += 1  # 不覆盖已放置棋子。
                return True  # 这里只表示动作结束，棋局还需视觉确认。
            except Exception as exc:  # 任何动作失败都禁止自动重发。
                self.fault = str(exc)  # 页面显示故障来源。
                raise  # 由上层锁住棋局。
            finally:  # 不在异常中贸然移动或释放电磁铁。
                self.busy = False  # 释放执行标志。

    def stop(self):  # 无需等待动作锁即可接受请求。
        self.stop_event.set()  # 当前段结束后不再执行下一段。
        self.fault = '已请求软件停止；这不会切断舵机电源'  # 不冒充物理急停。
        return self.status()  # 返回给网页。
