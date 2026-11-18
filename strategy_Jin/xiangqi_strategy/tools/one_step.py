# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""离线单步工具：输入全部棋子坐标，输出建议走法及预期全盘；不发送串口、不代表机械臂已经执行。"""
import argparse, json, sys  # 导入命令行参数解析、JSON读写和解释器路径功能。
from pathlib import Path  # 导入跨平台文件路径。
# [PY-tools-one_step-001] sys.path.insert｜来源：Python标准库sys中的path列表；方法为内置list.insert
# 功能：在模块搜索路径前面插入项目根目录，让tools/tests能导入项目文件。
# 参数 index=0：插入位置；0表示最优先查找
# 参数 object=str(Path(__file__).resolve().parents[1])：项目根目录的字符串路径
# 返回：None。
# [PY-tools-one_step-002] str｜来源：Python内置类型str的构造调用
# 功能：取得对象的字符串形式，例如路径、编号或异常说明。
# 参数 object=Path(__file__).resolve().parents[1]：要转换的对象
# 返回：字符串。
# [PY-tools-one_step-003] Path｜来源：Python标准库pathlib.Path
# 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
# 参数 *pathsegments[1]=__file__：文件或目录路径的各部分
# 返回：适合当前操作系统的Path对象。
# [PY-tools-one_step-004] Path(__file__).resolve｜来源：Python标准库pathlib.Path.resolve
# 功能：将当前Path转换为规范绝对路径。
# 参数 本次没有显式参数；省略的可选参数使用默认值
# 返回：新的绝对Path对象。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 把项目根目录放入模块搜索路径，允许从tools/tests启动。
from board import from_coordinates, validate, apply, coordinates  # 导入本项目的坐标/FEN转换和规则函数。
from engine import DemoEngine, Pikafish  # 导入本项目演示和真实引擎适配器。
if __name__ == '__main__':  # 仅直接运行此文件时执行入口逻辑。
    # [PY-tools-one_step-005] argparse.ArgumentParser｜来源：Python标准库argparse.ArgumentParser
    # 功能：创建命令行解析器，集中定义启动参数。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：ArgumentParser对象。
    p = argparse.ArgumentParser()  # 创建命令行参数解析器。
    # [PY-tools-one_step-006] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='input'：参数名或开关名称，如input、--engine；此参数用于设置全盘JSON输入文件路径
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('input')  # 必填：输入全盘JSON文件路径，例如 examples/start.json。
    # [PY-tools-one_step-007] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--engine'：参数名或开关名称，如input、--engine；此参数用于设置真实引擎可执行文件路径
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--engine')  # 可选：真实Pikafish可执行文件路径。
    # [PY-tools-one_step-008] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--nnue'：参数名或开关名称，如input、--engine；此参数用于设置配套NNUE权重文件路径
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--nnue')  # 可选：与引擎匹配的NNUE权重文件路径。
    # [PY-tools-one_step-009] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--demo'：参数名或开关名称，如input、--engine；此参数用于设置使用演示引擎而非真实搜索
    # 参数 action='store_true'：store_true表示出现该开关时取True
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--demo', action='store_true')  # 使用演示引擎，仅用于验证输入输出。
    # [PY-tools-one_step-010] p.parse_args｜来源：Python标准库argparse.ArgumentParser.parse_args
    # 功能：读取进程命令行，并按前面注册的规则解析。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：Namespace对象，可通过args.port等访问。
    a = p.parse_args()  # 解析实际命令行，保存到a。
    if not a.demo and (not a.engine):  # 未选择演示且未提供真实引擎时，拒绝运行。
        # [PY-tools-one_step-011] p.error｜来源：Python标准库argparse.ArgumentParser.error
        # 功能：打印命令行用法和错误说明，然后以错误状态退出。
        # 参数 message='--engine PATH or --demo required'：给操作者看的错误说明
        # 返回：通常不正常返回。
        p.error('--engine PATH or --demo required')  # 输出明确的使用错误并退出。
    # [PY-tools-one_step-012] json.loads｜来源：Python标准库json
    # 功能：把JSON字符串或字节解析成Python对象。
    # 参数 s=Path(a.input).read_text(encoding='utf-8-sig')：JSON原文，允许str/bytes/bytearray
    # 返回：解码得到的字典、列表等；格式错误抛异常。
    # [PY-tools-one_step-013] Path｜来源：Python标准库pathlib.Path
    # 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
    # 参数 *pathsegments[1]=a.input：文件或目录路径的各部分
    # 返回：适合当前操作系统的Path对象。
    # [PY-tools-one_step-014] Path(a.input).read_text｜来源：Python标准库pathlib.Path.read_text
    # 功能：读取这个Path指向文件的全部文本并关闭文件。
    # 参数 encoding='utf-8-sig'：utf-8-sig可兼容UTF-8 BOM文件
    # 返回：str文本。
    m = json.loads(Path(a.input).read_text(encoding='utf-8-sig'))  # 以UTF-8读取JSON，同时兼容Windows文件的BOM。
    # [PY-tools-one_step-015] m.get｜来源：Python内置dict类型的方法
    # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
    # 参数 key='turn'：字段名；此字段表示当前行棋方w红/b黑
    # 返回：字段值或default；省略default时为None。
    if m.get('turn') not in ('w', 'b'):  # 输入必须指明红走w或黑走b。
        # [PY-tools-one_step-016] p.error｜来源：Python标准库argparse.ArgumentParser.error
        # 功能：打印命令行用法和错误说明，然后以错误状态退出。
        # 参数 message='Input turn must be w (red) or b (black)'：给操作者看的错误说明
        # 返回：通常不正常返回。
        p.error('Input turn must be w (red) or b (black)')  # 拒绝缺失或错误的行棋方。
    # [PY-tools-one_step-017] validate｜来源：本项目 board.py，validate
    # 功能：检查 FEN 结构、棋子数量、将帅是否存在及位置约束，拒绝上一方留下将帅被攻击等非法初始局面。
    # 参数 fen=from_coordinates(m['pieces'], m['turn'])：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：校验通过的原 FEN；不合法时抛 ValueError。
    # [PY-tools-one_step-018] from_coordinates｜来源：本项目 board.py，from_coordinates
    # 功能：将全部棋子列表转为 FEN，检查坐标范围、类别、重复格；不在这里检查将帅数量等完整合法性，需要调用 validate。
    # 参数 pieces=m['pieces']：全盘存活棋子列表，每项为 {piece,row,col}；不是只传发生变化的棋子。
    # 参数 turn=m['turn']：行棋方：w红、b黑，默认w。
    # 返回：FEN；未列出的交叉点视为空点。
    fen = validate(from_coordinates(m['pieces'], m['turn']))  # 把全部棋子转为FEN，再检查局面合法性。
    # [PY-tools-one_step-019] DemoEngine｜来源：本项目 engine.py，DemoEngine类
    # 功能：创建只返回第一个合法走法的演示引擎，不加载NNUE。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：DemoEngine实例。
    # [PY-tools-one_step-020] Pikafish｜来源：本项目 engine.py，Pikafish.__init__
    # 功能：启动并持有 UCI 子进程，建立读队列，完成 uci/isready 握手并设置线程、Hash和可选权重；失败时清理子进程。
    # 参数 path=a.engine：Pikafish可执行文件路径；测试中也可传Python解释器路径。
    # 参数 nnue=a.nnue：匹配该引擎版本的权重文件路径，None表示使用引擎默认配置。
    # 返回：Pikafish实例。
    e = DemoEngine() if a.demo else Pikafish(a.engine, a.nnue)  # 建立演示或真实引擎；演示开关优先。
    try:  # 保证搜索或输出出错后也能执行finally清理引擎。
        # [PY-tools-one_step-021] e.best_move｜来源：本项目 engine.py，DemoEngine.best_move / Pikafish.best_move
        # 功能：按实际引擎类型取一步；Demo只取首个合法步，Pikafish使用UCI搜索。
        # 参数 root=fen：当前对局初始FEN，配合完整moves传给引擎。
        # 参数 moves=[]：从root开始已经正式提交的ICCS走法列表，按先后排序。
        # 参数 ms=1000：单步搜索时间（毫秒），默认1000；真实引擎允许50..10000，演示引擎忽略。
        # 参数 current=fen：当前正式FEN，用于复核结果；连续对局应传入，默认None时退回root。
        # 返回：ICCS一步或None；真实引擎会再次检查合法性。
        move = e.best_move(fen, [], 1000, current=fen)  # 从当前局面搜索一步；1000为搜索毫秒数，可在此调整。
        if move:  # 有合法建议时计算预期走后局面。
            # [PY-tools-one_step-022] apply｜来源：本项目 board.py，apply
            # 功能：复核一步合法走法，生成走后 FEN、吃子信息和中文记谱；不修改传入棋局。
            # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 参数 move=move：4字符ICCS走法，例如 b2e2；不是坐标列表。
            # 返回：(new_fen, meta)，meta 含 uci/text/from/to/piece/capture。
            after, meta = apply(fen, move)  # 得到走后FEN和包含起终点、吃子的走法元信息。
            # [PY-tools-one_step-023] print｜来源：Python内置函数
            # 功能：向标准输出打印调试信息或结果；flush可要求立即刷新。
            # 参数 *objects[1]=json.dumps(dict(kind='expected_only', move=meta, expected_fen=after, pieces=coordinates(after)), ensure_ascii=False, indent=2)：依次打印的对象
            # 返回：None。
            # [PY-tools-one_step-024] json.dumps｜来源：Python标准库json
            # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
            # 参数 obj=dict(kind='expected_only', move=meta, expected_fen=after, pieces=coordinates(after))：待序列化的字典、列表等对象
            # 参数 ensure_ascii=False：True把非ASCII字符转成转义；False保留中文
            # 参数 indent=2：输出缩进宽度；便于人工阅读但报文更长
            # 返回：str字符串。
            # [PY-tools-one_step-025] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 kind='expected_only'：单步输出类型，expected_only表示仅预测
            # 参数 move=meta：ICCS一步或走法元信息，视该响应上下文而定
            # 参数 expected_fen=after：计划预计执行后的FEN，尚未代表实物完成
            # 参数 pieces=coordinates(after)：全部存活棋子的{piece,row,col}列表，不是仅变化部分
            # 返回：新字典。
            # [PY-tools-one_step-026] coordinates｜来源：本项目 board.py，coordinates
            # 功能：把 FEN 转成全部存活棋子的类别和 row/col 列表，不包含空点。
            # 参数 fen=after：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：[{piece, row, col}, ...]。
            print(json.dumps(dict(kind='expected_only', move=meta, expected_fen=after, pieces=coordinates(after)), ensure_ascii=False, indent=2))  # 输出完整预测JSON；expected_only表示尚未发生真实机械动作。
        else:  # 没有可走步时输出终局标记。
            # [PY-tools-one_step-027] print｜来源：Python内置函数
            # 功能：向标准输出打印调试信息或结果；flush可要求立即刷新。
            # 参数 *objects[1]=json.dumps(dict(kind='game_over', pieces=m['pieces']))：依次打印的对象
            # 返回：None。
            # [PY-tools-one_step-028] json.dumps｜来源：Python标准库json
            # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
            # 参数 obj=dict(kind='game_over', pieces=m['pieces'])：待序列化的字典、列表等对象
            # 返回：str字符串。
            # [PY-tools-one_step-029] dict｜来源：Python内置类型dict的构造调用
            # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
            # 参数 kind='game_over'：单步输出类型，expected_only表示仅预测
            # 参数 pieces=m['pieces']：全部存活棋子的{piece,row,col}列表，不是仅变化部分
            # 返回：新字典。
            print(json.dumps(dict(kind='game_over', pieces=m['pieces'])))  # 保持原全盘坐标并标记game_over。
    finally:  # 无论正常完成还是抛异常都执行清理。
        # [PY-tools-one_step-030] e.close｜来源：本项目 engine.py，DemoEngine.close / Pikafish.close
        # 功能：关闭本实例引擎资源；Demo为空操作，Pikafish清理子进程。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：None。
        e.close()  # 关闭本工具持有的引擎进程，防止残留。
