# -*- coding: utf-8 -*-  # 保存为 UTF-8，便于显示中文说明。
"""CH-RO 的 cchess 规则适配和 HTTP 联调工具；不是机械臂控制程序。"""  # 本文件可以运行，也可以被 CH-RO 导入。
import argparse  # 读取命令行参数。
import json  # 编码和解析网页接口的 JSON。
import re  # 检查走法字符串。
import time  # 控制 AI 状态轮询间隔。
import urllib.error  # 处理 HTTP 错误。
import urllib.request  # 使用标准库访问 CH-RO 网页接口。
from cchess import ChessBoard  # 使用 cchess 1.25.5 校验规则。

START_FEN = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'  # 标准开局。
HTTP_TIMEOUT = 10  # 单次 HTTP 请求的等待秒数。
AI_TIMEOUT = 45  # 等待 AI 返回结果的总秒数。
POLL_SECONDS = 0.5  # AI 状态查询间隔。
PIECE_CODES = 'kabrncpKABRNCP'  # 统一使用 N 表示马、B 表示象；大写是红方。


def board_from_fen(fen):  # 把 FEN 转换为 CH-RO 的棋盘字典。
    fields = fen.split()  # 拆分 FEN 的六个字段。
    if len(fields) != 6 or fields[1] not in ('w', 'b') or fields[2:4] != ['-', '-']:  # 检查字段。
        raise ValueError('需要六字段象棋 FEN，w 为红方、b 为黑方。')  # 拒绝不明确的输入。
    if not fields[4].isdigit() or not fields[5].isdigit() or int(fields[5]) < 1:  # 检查计数。
        raise ValueError('FEN 计数不正确。')  # 不猜测错误字段的含义。
    ranks = fields[0].split('/')  # 读取从黑方底线到红方底线的十行。
    if len(ranks) != 10:  # 象棋必须有十行。
        raise ValueError('棋盘必须有十行。')  # 提示调用方修正输入。
    board = {}  # CH-RO 使用字符串键 col,row，而不是 row,col。
    for row, rank in enumerate(ranks):  # 逐行展开空位数字。
        col = 0  # 每行从第零列开始。
        for char in rank:  # 逐个处理棋子或空位数。
            if char in '123456789':  # 数字表示连续空位。
                col += int(char)  # 跳过相应数量的交叉点。
            elif char in PIECE_CODES and col < 9:  # 棋子必须位于棋盘以内。
                board[f'{col},{row}'] = char  # 保存 CH-RO 坐标形式。
                col += 1  # 移动到下一列。
            else:  # 遇到未知编码或溢出。
                raise ValueError('棋子编码或列数错误；请先将 H/E 规范为 N/B。')  # 要求接口统一。
        if col != 9:  # 每行必须恰好九列。
            raise ValueError('每行必须恰好有九个交叉点。')  # 拒绝损坏的棋盘。
    if list(board.values()).count('K') != 1 or list(board.values()).count('k') != 1:  # 基本完整性检查。
        raise ValueError('局面必须各有一个将、帅。')  # 游戏以将死或困毙结束，不执行吃将。
    return board  # 返回独立的字典，不修改调用方状态。


def fen_from_board(board, turn, half, full):  # 由棋盘和计数生成 FEN。
    ranks = []  # 收集十行压缩字符串。
    for row in range(10):  # 从黑方底线开始输出。
        rank = ''  # 当前行输出内容。
        empty = 0  # 当前连续空位数量。
        for col in range(9):  # 遍历九列。
            piece = board.get(f'{col},{row}')  # 读取该交叉点的棋子。
            if piece is None:  # 没有棋子时累计空位。
                empty += 1  # 增加空位计数。
            else:  # 先输出空位，再输出棋子。
                if empty:  # 仅输出非零的空位数。
                    rank += str(empty)  # 写入前方空位数。
                    empty = 0  # 开始下一段空位计数。
                rank += piece  # 写入棋子字符。
        if empty:  # 处理行尾空位。
            rank += str(empty)  # 写入剩余空位数。
        ranks.append(rank)  # 保存当前行。
    return '/'.join(ranks) + f' {turn} - - {half} {full}'  # 返回完整 FEN。


def checked_transition(fen, move):  # 检查一步棋，生成预期结果，但绝不提交全局状态。
    if not isinstance(move, str) or re.fullmatch('[a-i][0-9][a-i][0-9]', move) is None:  # Pikafish 使用 0 到 9。
        raise ValueError('走法必须类似 b2e2。')  # 拒绝错误格式和空走法。
    before = board_from_fen(fen)  # 检查并读取当前棋盘。
    fields = fen.split()  # 保留轮次与计数。
    start = (ord(move[0]) - ord('a'), int(move[1]))  # cchess 原点在红方左下，坐标是列、纵线数字。
    end = (ord(move[2]) - ord('a'), int(move[3]))  # 读取终点的 cchess 坐标。
    chess = ChessBoard(fen)  # 用当前 FEN 建立临时规则棋盘。
    if (start, end) not in set(chess.create_moves()):  # 检查当前一方的棋子走法和阻挡。
        raise ValueError('不符合棋子走法、阻挡或当前行棋方。')  # 拒绝伪合法输入。
    if chess.is_checked_move(start, end):  # 1.25.5 的此方法判断该步是否导致己方被将军。
        raise ValueError('这步棋会送将，或造成将帅照面。')  # 额外过滤送将。
    from_row, from_col = 9 - start[1], start[0]  # 转换为计划中的 row,col。
    to_row, to_col = 9 - end[1], end[0]  # 转换目标棋位。
    from_key = f'{from_col},{from_row}'  # 转成 CH-RO 的字符串键。
    to_key = f'{to_col},{to_row}'  # 转成 CH-RO 终点键。
    captured = before.get(to_key)  # 记录吃子对象，空格返回 None。
    if captured is not None and captured.lower() == 'k':  # 不把吃将作为机器人动作。
        raise ValueError('请在将死或困毙时结束对局，不执行吃将。')  # 保护游戏语义。
    after = dict(before)  # 复制棋盘，避免修改正式局面。
    piece = after.pop(from_key)  # 从起点拿起棋子。
    after[to_key] = piece  # 放到终点，并自然覆盖被吃棋子。
    half = 0 if captured is not None or piece.lower() == 'p' else int(fields[4]) + 1  # 维护 FEN 半回合计数。
    full = int(fields[5]) + int(fields[1] == 'b')  # 黑方走完后增加完整回合数。
    turn = 'b' if fields[1] == 'w' else 'w'  # 切换红黑方。
    result_fen = fen_from_board(after, turn, half, full)  # 生成预期局面。
    pieces = [dict(piece=p, row=int(k.split(',')[1]), col=int(k.split(',')[0])) for k, p in sorted(after.items())]  # 输出存活棋子的完整棋位。
    return dict(fen=result_fen, board_state=after, pieces=pieces, move=move, capture=captured, source=[from_row, from_col], target=[to_row, to_col])  # 返回结果给状态机。


def request_json(base_url, path, payload=None):  # 调用 CH-RO 的真实 HTTP 接口。
    body = None if payload is None else json.dumps(payload).encode('utf-8')  # 有数据时使用 POST。
    request = urllib.request.Request(base_url.rstrip('/') + path, data=body, headers={'Content-Type': 'application/json'})  # 构建请求。
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:  # 网络调用有单次超时。
        result = json.load(response)  # 解析 JSON 响应。
    if not result.get('success'):  # CH-RO 有些业务错误仍返回 HTTP 200。
        raise RuntimeError(result.get('error') or str(result))  # 必须同时检查 success 字段。
    return result  # 返回接口内容。


def check_web(base_url, new_game=False):  # 分层检查网页与引擎。
    result = request_json(base_url, '/api/status')  # 先做完全只读的后端检查。
    print('通过：Flask 状态接口可用；当前模式：', result['state'].get('robot_mode'))  # 显示实际模式。
    if not new_game:  # 默认不重置用户正在进行的对局。
        print('只读检查完成。完整虚拟测试请加 --new-simulation，会重置当前对局。')  # 解释后续动作。
        return  # 结束只读诊断。
    result = request_json(base_url, '/api/game/start', {'mode': 'simulation'})  # 明确选择虚拟模式，不连接机械臂。
    if result.get('robot_mode') != 'simulation':  # 不允许接口默默退回硬件模式。
        raise RuntimeError('服务器没有进入 simulation，停止检查。')  # 避免继续未知模式。
    print('通过：虚拟对局已开始。')  # 第一阶段验证完成。
    expected = checked_transition(START_FEN, 'b2e2')  # 计算合法的人类第一步：炮二平五。
    request_json(base_url, '/api/player_move', {'move': 'b2e2'})  # 从真实接口提交红方第一步。
    state = request_json(base_url, '/api/status')['state']  # 读取后端保存的正式状态。
    if state.get('move_history') != ['b2e2'] or state.get('board_state') != expected['board_state'] or state.get('current_fen') != expected['fen']:  # 比对棋谱、棋盘和轮次。
        raise RuntimeError('玩家走法、棋盘或 FEN 未同步。按说明修正 player_move；先不要接摄像头。')  # 报告上游手动走棋缺口。
    print('通过：红方走棋后，棋盘、棋谱和 FEN 一致。')  # 人类走棋验证完成。
    request_json(base_url, '/api/ai_move', {'depth': 8})  # 请求黑方 AI 走一步；深度由此参数决定。
    deadline = time.monotonic() + AI_TIMEOUT  # 为轮询设置总期限。
    while time.monotonic() < deadline:  # 等待后台 AI 线程结束。
        status = request_json(base_url, '/api/ai_status')  # 查询引擎状态。
        if not status.get('ai_thinking'):  # 后台任务已结束。
            break  # 开始验证结果。
        time.sleep(POLL_SECONDS)  # 防止频繁轮询占用树莓派。
    else:  # 超过总等待时间。
        raise RuntimeError('AI 等待超时：检查引擎路径、NNUE、进程输出及阻塞读取。')  # 给出定位方向。
    best = (status.get('analysis') or {}).get('best_move')  # 提取引擎走法。
    expected_ai = checked_transition(expected['fen'], best)  # 再用 cchess 检查 AI 的合法性。
    if status.get('move_history') != ['b2e2', best] or status.get('board_state') != expected_ai['board_state'] or status.get('current_fen') != expected_ai['fen']:  # 必须保持三个状态一致。
        raise RuntimeError('AI 棋盘、棋谱或 FEN 未同步；检查 apply_ai_best_move 的提交代码。')  # 不把仅有 bestmove 当作成功。
    print('通过：黑方 AI 返回合法走法', best, '；网页后端两步对弈完成。')  # 说明实际通过范围。
    print('本检查没有验证浏览器绘制、摄像头、树莓派串口或机械臂。')  # 保持验证边界明确。


def self_test():  # 在没有 CH-RO、引擎和机械臂时验证本文件的规则适配。
    import unittest  # 测试功能只在自测时导入。
    class RulesTests(unittest.TestCase):  # 定义有意义的规则边界测试。
        def test_red_move(self):  # 检查第一步、坐标及计数。
            result = checked_transition(START_FEN, 'b2e2')  # 红炮平五。
            self.assertEqual(result['source'], [7, 1])  # 计划坐标的起点。
            self.assertEqual(result['target'], [7, 4])  # 计划坐标的终点。
            self.assertEqual(result['board_state'].get('4,7'), 'C')  # CH-RO 的键是列、行。
            self.assertNotIn('1,7', result['board_state'])  # 起点必须清空。
            self.assertEqual(len(result['pieces']), 32)  # 未吃子时保留全部棋子。
            self.assertEqual(result['fen'].split()[1:], ['b', '-', '-', '1', '1'])  # 红走后轮到黑方。
        def test_black_move(self):  # 黑方走完后增加完整回合数。
            red = checked_transition(START_FEN, 'b2e2')  # 先走红方。
            black = checked_transition(red['fen'], 'a9a8')  # 黑车前进一步。
            self.assertEqual(black['fen'].split()[1:], ['w', '-', '-', '2', '2'])  # 检查轮次与计数。
        def test_wrong_side(self):  # 不接受越过轮次的黑方走法。
            with self.assertRaises(ValueError):  # 要求明确拒绝。
                checked_transition(START_FEN, 'a9a8')  # 开局轮到红方。
        def test_blocked_rook(self):  # 不允许车穿过己方兵。
            with self.assertRaises(ValueError):  # 要求明确拒绝。
                checked_transition(START_FEN, 'a0a4')  # a3 的红兵会阻挡。
        def test_self_check(self):  # 不允许走开屏障形成将帅照面。
            with self.assertRaises(ValueError):  # 要求规则库识别送将。
                checked_transition('4k4/9/9/9/9/9/4R4/9/9/4K4 w - - 0 1', 'e3d3')  # 红车是中线唯一屏障。
        def test_capture(self):  # 吃子后数组只保留存活棋子。
            result = checked_transition('3k5/9/9/9/9/9/9/p8/R8/4K4 w - - 7 2', 'a1a2')  # 红车吃黑卒。
            self.assertEqual(result['capture'], 'p')  # 必须保留吃子类型。
            self.assertEqual(len(result['pieces']), 3)  # 吃子后少一个棋子。
            self.assertEqual(result['fen'].split()[4], '0')  # 吃子重置半回合计数。
        def test_format(self):  # 拒绝不属于 Pikafish 的纵线格式。
            with self.assertRaises(ValueError):  # 防止混入 Fairy-Stockfish 的 a10 格式。
                checked_transition(START_FEN, 'a10a9')  # 本工具仅适配 Pikafish。
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RulesTests)  # 收集以上测试。
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()  # 返回测试是否全部通过。


def main():  # 命令行入口。
    parser = argparse.ArgumentParser(description='CH-RO 规则适配与网页 HTTP 检查；完整测试会重置对局。')  # 说明用途。
    parser.add_argument('--url', default='http://127.0.0.1:5000')  # 可改为树莓派局域网地址。
    parser.add_argument('--new-simulation', action='store_true', help='明确重置并测试新的虚拟对局')  # 需要用户显式选择会改状态的检查。
    parser.add_argument('--self-test', action='store_true', help='只运行本文件规则自测')  # 支持离线验证。
    args = parser.parse_args()  # 读取参数。
    if args.self_test:  # 离线自测不访问任何服务器。
        return 0 if self_test() else 1  # 用退出码表示自测结果。
    try:  # 将常见联调错误打印为简明中文。
        check_web(args.url, args.new_simulation)  # 检查选定服务器。
        return 0  # 所选检查通过。
    except (ValueError, RuntimeError, OSError, urllib.error.URLError) as exc:  # 捕获网络和业务错误。
        print('检查未通过：', exc)  # 输出可用于进一步定位的信息。
        return 1  # 失败时返回非零退出码。


if __name__ == '__main__':  # 被 CH-RO 导入时，不运行命令行检查。
    raise SystemExit(main())  # 直接运行时执行所选功能。
