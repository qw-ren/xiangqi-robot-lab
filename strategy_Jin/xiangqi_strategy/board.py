# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""棋局基础层：统一 row/col、FEN 与 cchess 坐标，校验输入、生成合法走法、推断视觉中的人类走子。这里不拍照、不控制电机。"""
import re  # 导入正则校验ICCS走法格式。
from collections import Counter  # 导入棋子计数、请求缓存顺序和重复局面统计。
from cchess import ChessBoard  # 导入第三方中国象棋规则与中文记谱库。
START = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'  # 标准32子初始FEN，w表示红方先手。
CHARS = 'kabrncpKABRNCP'  # 允许的14种棋子编码，大写红方、小写黑方。

def parse(fen):
    """功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。

    返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。"""
    # [PY-board-001] isinstance｜来源：Python内置函数
    # 功能：检查对象是否属于指定类型或其子类。
    # 参数 object=fen：被检查的对象
    # 参数 classinfo=str：允许的类型，或多个类型组成的元组
    # 返回：True/False。
    if not isinstance(fen, str):
        # [PY-board-002] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='FEN must be a string'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('FEN must be a string')
    # [PY-board-003] fen.split｜来源：Python内置str类型的方法
    # 功能：按分隔符切开文本；未传分隔符时按连续空白切分。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：字符串列表。
    f = fen.split()
    # [PY-board-004] len｜来源：Python内置函数
    # 功能：取得容器、字符串或字节序列的元素数量。
    # 参数 obj=f：要计数的对象；字节序列按字节数计，字符串按字符数计
    # 返回：整数长度。
    if len(f) != 6 or f[1] not in ('w', 'b') or f[2:4] != ['-', '-']:
        # [PY-board-005] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Expected Xiangqi FEN with six fields (w=red, b=black)'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Expected Xiangqi FEN with six fields (w=red, b=black)')
    # [PY-board-006] f[4].isdigit｜来源：Python内置str类型的方法
    # 功能：判断是否非空且所有字符都是数字字符。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：True/False。
    # [PY-board-007] f[5].isdigit｜来源：Python内置str类型的方法
    # 功能：判断是否非空且所有字符都是数字字符。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：True/False。
    # [PY-board-008] int｜来源：Python内置类型int的构造调用
    # 功能：把可转换的值变成整数。
    # 参数 x=f[5]：数字、数字字符串或布尔值；True转1、False转0
    # 返回：整数；转换失败抛异常。
    if not f[4].isdigit() or not f[5].isdigit() or int(f[5]) < 1:
        # [PY-board-009] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Invalid FEN counters'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Invalid FEN counters')
    rows = []
    # [PY-board-010] f[0].split｜来源：Python内置str类型的方法
    # 功能：按分隔符切开文本；未传分隔符时按连续空白切分。
    # 参数 sep='/'：分隔符；省略时按空白拆分
    # 返回：字符串列表。
    for rank in f[0].split('/'):
        row = []
        for ch in rank:
            if ch in '123456789':
                # [PY-board-011] int｜来源：Python内置类型int的构造调用
                # 功能：把可转换的值变成整数。
                # 参数 x=ch：数字、数字字符串或布尔值；True转1、False转0
                # 返回：整数；转换失败抛异常。
                row += ['.'] * int(ch)
            elif ch in CHARS:
                # [PY-board-012] row.append｜来源：Python内置list类型的方法
                # 功能：在列表末尾添加一项，原地修改列表。
                # 参数 object=ch：要追加的单项内容，不是整个列表的展开
                # 返回：None。
                row.append(ch)
            else:
                # [PY-board-013] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Unknown piece; normalize H/E to N/B first'：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Unknown piece; normalize H/E to N/B first')
        # [PY-board-014] len｜来源：Python内置函数
        # 功能：取得容器、字符串或字节序列的元素数量。
        # 参数 obj=row：要计数的对象；字节序列按字节数计，字符串按字符数计
        # 返回：整数长度。
        if len(row) != 9:
            # [PY-board-015] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Every row must have nine intersections'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Every row must have nine intersections')
        # [PY-board-016] rows.append｜来源：Python内置list类型的方法
        # 功能：在列表末尾添加一项，原地修改列表。
        # 参数 object=row：要追加的单项内容，不是整个列表的展开
        # 返回：None。
        rows.append(row)
    # [PY-board-017] len｜来源：Python内置函数
    # 功能：取得容器、字符串或字节序列的元素数量。
    # 参数 obj=rows：要计数的对象；字节序列按字节数计，字符串按字符数计
    # 返回：整数长度。
    if len(rows) != 10:
        # [PY-board-018] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Expected ten rows'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Expected ten rows')
    # [PY-board-019] int｜来源：Python内置类型int的构造调用
    # 功能：把可转换的值变成整数。
    # 参数 x=f[4]：数字、数字字符串或布尔值；True转1、False转0
    # 返回：整数；转换失败抛异常。
    # [PY-board-020] int｜来源：Python内置类型int的构造调用
    # 功能：把可转换的值变成整数。
    # 参数 x=f[5]：数字、数字字符串或布尔值；True转1、False转0
    # 返回：整数；转换失败抛异常。
    return (rows, f[1], int(f[4]), int(f[5]))

def encode(rows, turn='w', half=0, full=1):
    """功能：将棋盘二维列表压缩为六字段 FEN；此函数本身不做完整局面合法性校验。

    参数：
        rows：10行×9列二维棋盘；空点用 "."，红棋大写、黑棋小写。
        turn：行棋方：w红、b黑，默认w。
        half：无吃子/无兵卒移动的半回合计数，默认0；不是完整长将长捉规则。
        full：完整回合号，从1开始，黑走后增加。

    返回：FEN 字符串。"""
    ranks = []
    for row in rows:
        s, n = ('', 0)
        for ch in row:
            if ch == '.':
                n += 1
            else:
                if n:
                    # [PY-board-021] str｜来源：Python内置类型str的构造调用
                    # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                    # 参数 object=n：要转换的对象
                    # 返回：字符串。
                    s += str(n)
                    n = 0
                s += ch
        if n:
            # [PY-board-022] str｜来源：Python内置类型str的构造调用
            # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
            # 参数 object=n：要转换的对象
            # 返回：字符串。
            s += str(n)
        # [PY-board-023] ranks.append｜来源：Python内置list类型的方法
        # 功能：在列表末尾添加一项，原地修改列表。
        # 参数 object=s：要追加的单项内容，不是整个列表的展开
        # 返回：None。
        ranks.append(s)
    # [PY-board-024] '/'.join｜来源：Python内置str类型的方法
    # 功能：使用调用者字符串作为分隔符连接各项。
    # 参数 iterable=ranks：待连接的字符串序列
    # 返回：拼接后的字符串。
    return '/'.join(ranks) + f' {turn} - - {half} {full}'

def validate(fen):
    """功能：检查 FEN 结构、棋子数量、将帅是否存在及位置约束，拒绝上一方留下将帅被攻击等非法初始局面。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。

    返回：校验通过的原 FEN；不合法时抛 ValueError。"""
    # [PY-board-025] parse｜来源：本项目 board.py，parse
    # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
    # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
    rows, _, _, _ = parse(fen)
    # [PY-board-026] Counter｜来源：Python标准库collections.Counter
    # 功能：统计每个元素出现次数，用于棋子上限和重复局面检查。
    # 参数 iterable=(ch for row in rows for ch in row if ch != '.')：需要统计的元素序列/生成器
    # 返回：计数字典Counter。
    counts = Counter((ch for row in rows for ch in row if ch != '.'))
    for ch, limit in [('k', 1), ('a', 2), ('b', 2), ('n', 2), ('r', 2), ('c', 2), ('p', 5)]:
        # [PY-board-027] ch.upper｜来源：Python内置str类型的方法
        # 功能：将文本中的字母转为大写，得到红方编码。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：新字符串，不原地修改。
        for p in (ch, ch.upper()):
            if counts[p] > limit:
                # [PY-board-028] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]='Too many pieces: ' + p：错误说明字符串
                # 返回：异常对象。
                raise ValueError('Too many pieces: ' + p)
    if counts['K'] != 1 or counts['k'] != 1:
        # [PY-board-029] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Both kings must exist'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Both kings must exist')
    # [PY-board-030] ChessBoard｜来源：第三方cchess库：cchess/board.py，ChessBoard
    # 功能：按FEN创建规则库棋盘；它的坐标是(x,y)=(col,9-row)。
    # 参数 fen=fen：用于初始化的完整中国象棋FEN
    # 返回：ChessBoard对象。
    b = ChessBoard(fen)
    # [PY-board-031] enumerate｜来源：Python内置函数
    # 功能：遍历对象时同时给出从0开始的索引。
    # 参数 iterable=rows：被遍历的序列
    # 返回：逐项产生(index,value)的迭代器。
    for r, row in enumerate(rows):
        # [PY-board-032] enumerate｜来源：Python内置函数
        # 功能：遍历对象时同时给出从0开始的索引。
        # 参数 iterable=row：被遍历的序列
        # 返回：逐项产生(index,value)的迭代器。
        for c, ch in enumerate(row):
            # [PY-board-033] b.get_piece｜来源：第三方cchess库：cchess/board.py，ChessBoard.get_piece
            # 功能：取得指定位置的棋子对象，便于调用该棋种的位置规则。
            # 参数 pos=(c, 9 - r)：cchess坐标(x,y)，不是[row,col]
            # 返回：Piece或其棋种子类对象；空点返回None。
            # [PY-board-034] b.get_piece((c, 9 - r)).is_valid_pos｜来源：第三方cchess库：cchess/piece.py，Piece及各棋种子类
            # 功能：检查该棋种是否允许放在这个位置；将士象等子类具有各自位置约束。
            # 参数 pos=(c, 9 - r)：待核对的cchess坐标(x,y)
            # 返回：True/False。
            if ch != '.' and (not b.get_piece((c, 9 - r)).is_valid_pos((c, 9 - r))):
                # [PY-board-035] ValueError｜来源：Python内置异常类型
                # 功能：构造异常；前面带raise才会真正抛出。
                # 参数 *args[1]=f'Invalid piece position {ch} at {(r, c)}'：错误说明字符串
                # 返回：异常对象。
                raise ValueError(f'Invalid piece position {ch} at {(r, c)}')
    # [PY-board-036] b.is_checking｜来源：第三方cchess库：cchess/board.py，ChessBoard.is_checking
    # 功能：本项目安装版1.25.5中，判断当前行棋方是否攻击对方将帅；初始化时用于拒绝上一方遗留的非法局面。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：True/False；语义以本项目锁定库版本为准。
    if b.is_checking():
        # [PY-board-037] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Previous player left their king attacked, or kings face'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Previous player left their king attacked, or kings face')
    return fen

def coordinates(fen):
    """功能：把 FEN 转成全部存活棋子的类别和 row/col 列表，不包含空点。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。

    返回：[{piece, row, col}, ...]。"""
    # [PY-board-038] parse｜来源：本项目 board.py，parse
    # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
    # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
    rows = parse(fen)[0]
    # [PY-board-039] dict｜来源：Python内置类型dict的构造调用
    # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
    # 参数 piece=p：单枚棋子的编码；大写红、小写黑
    # 参数 row=r：从黑方底线向下的行号0..9
    # 参数 col=c：归一化画面从左向右的列号0..8
    # 返回：新字典。
    # [PY-board-040] enumerate｜来源：Python内置函数
    # 功能：遍历对象时同时给出从0开始的索引。
    # 参数 iterable=rows：被遍历的序列
    # 返回：逐项产生(index,value)的迭代器。
    # [PY-board-041] enumerate｜来源：Python内置函数
    # 功能：遍历对象时同时给出从0开始的索引。
    # 参数 iterable=row：被遍历的序列
    # 返回：逐项产生(index,value)的迭代器。
    return [dict(piece=p, row=r, col=c) for r, row in enumerate(rows) for c, p in enumerate(row) if p != '.']

def from_coordinates(pieces, turn='w'):
    """功能：将全部棋子列表转为 FEN，检查坐标范围、类别、重复格；不在这里检查将帅数量等完整合法性，需要调用 validate。

    参数：
        pieces：全盘存活棋子列表，每项为 {piece,row,col}；不是只传发生变化的棋子。
        turn：行棋方：w红、b黑，默认w。

    返回：FEN；未列出的交叉点视为空点。"""
    # [PY-board-042] isinstance｜来源：Python内置函数
    # 功能：检查对象是否属于指定类型或其子类。
    # 参数 object=pieces：被检查的对象
    # 参数 classinfo=list：允许的类型，或多个类型组成的元组
    # 返回：True/False。
    # [PY-board-043] len｜来源：Python内置函数
    # 功能：取得容器、字符串或字节序列的元素数量。
    # 参数 obj=pieces：要计数的对象；字节序列按字节数计，字符串按字符数计
    # 返回：整数长度。
    if not isinstance(pieces, list) or len(pieces) > 32:
        # [PY-board-044] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Expected up to 32 pieces'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Expected up to 32 pieces')
    # [PY-board-045] range｜来源：Python内置函数
    # 功能：产生从0到stop之前的整数，用于行列循环。
    # 参数 stop=10：不包含在结果里的上界
    # 返回：range对象。
    rows = [['.'] * 9 for _ in range(10)]
    for item in pieces:
        r, c, p = (item['row'], item['col'], item['piece'])
        # [PY-board-046] type｜来源：Python内置函数
        # 功能：取得对象的实际类型；本项目用它拒绝把bool当作int坐标。
        # 参数 object=r：被检查的值
        # 返回：类型对象。
        # [PY-board-047] type｜来源：Python内置函数
        # 功能：取得对象的实际类型；本项目用它拒绝把bool当作int坐标。
        # 参数 object=c：被检查的值
        # 返回：类型对象。
        if type(r) != int or type(c) != int or (not (0 <= r < 10 and 0 <= c < 9)):
            # [PY-board-048] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Coordinate out of range'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Coordinate out of range')
        # [PY-board-049] len｜来源：Python内置函数
        # 功能：取得容器、字符串或字节序列的元素数量。
        # 参数 obj=p：要计数的对象；字节序列按字节数计，字符串按字符数计
        # 返回：整数长度。
        if p not in CHARS or len(p) != 1:
            # [PY-board-050] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Invalid piece code'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Invalid piece code')
        if rows[r][c] != '.':
            # [PY-board-051] ValueError｜来源：Python内置异常类型
            # 功能：构造异常；前面带raise才会真正抛出。
            # 参数 *args[1]='Two pieces occupy one intersection'：错误说明字符串
            # 返回：异常对象。
            raise ValueError('Two pieces occupy one intersection')
        rows[r][c] = p
    # [PY-board-052] encode｜来源：本项目 board.py，encode
    # 功能：将棋盘二维列表压缩为六字段 FEN；此函数本身不做完整局面合法性校验。
    # 参数 rows=rows：10行×9列二维棋盘；空点用 "."，红棋大写、黑棋小写。
    # 参数 turn=turn：行棋方：w红、b黑，默认w。
    # 返回：FEN 字符串。
    return encode(rows, turn)

def square(rc):
    """功能：将内部 (row,col) 转为 ICCS 格点，例如 [7,1] → b2。

    参数：
        rc：内部 [row,col] 或 (row,col)，row0黑底线、col0归一化画面左侧。

    返回：两个字符的格点字符串。"""
    r, c = rc
    # [PY-board-053] type｜来源：Python内置函数
    # 功能：取得对象的实际类型；本项目用它拒绝把bool当作int坐标。
    # 参数 object=r：被检查的值
    # 返回：类型对象。
    # [PY-board-054] type｜来源：Python内置函数
    # 功能：取得对象的实际类型；本项目用它拒绝把bool当作int坐标。
    # 参数 object=c：被检查的值
    # 返回：类型对象。
    if type(r) != int or type(c) != int or (not (0 <= r < 10 and 0 <= c < 9)):
        # [PY-board-055] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Invalid coordinate'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Invalid coordinate')
    # [PY-board-056] chr｜来源：Python内置函数
    # 功能：将Unicode码点整数变成字符；97对应小写a。
    # 参数 i=97 + c：字符码点整数
    # 返回：单字符字符串。
    # [PY-board-057] str｜来源：Python内置类型str的构造调用
    # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
    # 参数 object=9 - r：要转换的对象
    # 返回：字符串。
    return chr(97 + c) + str(9 - r)

def rc(s):
    """功能：将 ICCS 格点转回 [row,col]；调用者必须先保证格点字符串合法。

    参数：
        s：两个字符的合法ICCS格点，例如 b2；该函数自身不检查格式。

    返回：[row,col]，例如 b2 → [7,1]。"""
    # [PY-board-058] int｜来源：Python内置类型int的构造调用
    # 功能：把可转换的值变成整数。
    # 参数 x=s[1]：数字、数字字符串或布尔值；True转1、False转0
    # 返回：整数；转换失败抛异常。
    # [PY-board-059] ord｜来源：Python内置函数
    # 功能：取得单字符的Unicode码点；减去97可把a..i转成列号。
    # 参数 c=s[0]：单字符字符串
    # 返回：整数。
    return [9 - int(s[1]), ord(s[0]) - 97]

def legal_moves(fen):
    """功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。

    返回：逐项产生4字符 ICCS 走法。"""
    # [PY-board-060] ChessBoard｜来源：第三方cchess库：cchess/board.py，ChessBoard
    # 功能：按FEN创建规则库棋盘；它的坐标是(x,y)=(col,9-row)。
    # 参数 fen=fen：用于初始化的完整中国象棋FEN
    # 返回：ChessBoard对象。
    b = ChessBoard(fen)
    # [PY-board-061] b.create_moves｜来源：第三方cchess库：cchess/board.py，ChessBoard.create_moves
    # 功能：产生当前方各棋子的规则走法；本项目随后再过滤自将和吃将。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：逐项产生(起点,终点)的生成器；坐标不是row/col。
    for a, z in b.create_moves():
        # [PY-board-062] (b.get_fench(z) or '').lower｜来源：Python内置str类型的方法
        # 功能：将文本中的字母转为小写，便于忽略阵营比较棋种。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：新字符串，不原地修改。
        # [PY-board-063] b.get_fench｜来源：第三方cchess库：cchess/board.py，ChessBoard.get_fench
        # 功能：读取指定交叉点的棋子编码。
        # 参数 pos=z：cchess坐标(x,y)，x=col、y=9-row
        # 返回：棋码字符串；空点或越界返回None。
        if (b.get_fench(z) or '').lower() == 'k':
            continue
        # [PY-board-064] b.is_checked_move｜来源：第三方cchess库：cchess/board.py，ChessBoard.is_checked_move
        # 功能：检查走这一步后己方将帅是否被攻击；True表示自将，应过滤。
        # 参数 pos_from=a：cchess起点(x,y)
        # 参数 pos_to=z：cchess终点(x,y)
        # 返回：True/False；不改原棋盘，非法候选可能抛异常。
        if not b.is_checked_move(a, z):
            # [PY-board-065] chr｜来源：Python内置函数
            # 功能：将Unicode码点整数变成字符；97对应小写a。
            # 参数 i=97 + a[0]：字符码点整数
            # 返回：单字符字符串。
            # [PY-board-066] str｜来源：Python内置类型str的构造调用
            # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
            # 参数 object=a[1]：要转换的对象
            # 返回：字符串。
            # [PY-board-067] chr｜来源：Python内置函数
            # 功能：将Unicode码点整数变成字符；97对应小写a。
            # 参数 i=97 + z[0]：字符码点整数
            # 返回：单字符字符串。
            # [PY-board-068] str｜来源：Python内置类型str的构造调用
            # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
            # 参数 object=z[1]：要转换的对象
            # 返回：字符串。
            yield (chr(97 + a[0]) + str(a[1]) + chr(97 + z[0]) + str(z[1]))

def apply(fen, move):
    """功能：复核一步合法走法，生成走后 FEN、吃子信息和中文记谱；不修改传入棋局。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        move：4字符ICCS走法，例如 b2e2；不是坐标列表。

    返回：(new_fen, meta)，meta 含 uci/text/from/to/piece/capture。"""
    # [PY-board-069] isinstance｜来源：Python内置函数
    # 功能：检查对象是否属于指定类型或其子类。
    # 参数 object=move：被检查的对象
    # 参数 classinfo=str：允许的类型，或多个类型组成的元组
    # 返回：True/False。
    # [PY-board-070] re.fullmatch｜来源：Python标准库re
    # 功能：要求整段文本符合正则，避免非法ICCS格式。
    # 参数 pattern='[a-i][0-9][a-i][0-9]'：匹配规则，例如[a-i][0-9][a-i][0-9]
    # 参数 string=move：要检查的走法字符串
    # 返回：匹配对象，失败返回None。
    if not isinstance(move, str) or not re.fullmatch('[a-i][0-9][a-i][0-9]', move):
        # [PY-board-071] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Invalid ICCS move'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Invalid ICCS move')
    # [PY-board-072] legal_moves｜来源：本项目 board.py，legal_moves
    # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
    # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：逐项产生4字符 ICCS 走法。
    if move not in legal_moves(fen):
        # [PY-board-073] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Illegal move (including self check)'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Illegal move (including self check)')
    # [PY-board-074] parse｜来源：本项目 board.py，parse
    # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
    # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
    rows, turn, half, full = parse(fen)
    # [PY-board-075] rc｜来源：本项目 board.py，rc
    # 功能：将 ICCS 格点转回 [row,col]；调用者必须先保证格点字符串合法。
    # 参数 s=move[:2]：两个字符的合法ICCS格点，例如 b2；该函数自身不检查格式。
    # 返回：[row,col]，例如 b2 → [7,1]。
    # [PY-board-076] rc｜来源：本项目 board.py，rc
    # 功能：将 ICCS 格点转回 [row,col]；调用者必须先保证格点字符串合法。
    # 参数 s=move[2:]：两个字符的合法ICCS格点，例如 b2；该函数自身不检查格式。
    # 返回：[row,col]，例如 b2 → [7,1]。
    a, z = (rc(move[:2]), rc(move[2:]))
    p = rows[a[0]][a[1]]
    captured = rows[z[0]][z[1]]
    # [PY-board-077] ChessBoard｜来源：第三方cchess库：cchess/board.py，ChessBoard
    # 功能：按FEN创建规则库棋盘；它的坐标是(x,y)=(col,9-row)。
    # 参数 fen=fen：用于初始化的完整中国象棋FEN
    # 返回：ChessBoard对象。
    b = ChessBoard(fen)
    # [PY-board-078] b.move_iccs｜来源：第三方cchess库：cchess/board.py，ChessBoard.move_iccs
    # 功能：在该cchess棋盘对象上执行ICCS一步，生成记谱对象。
    # 参数 move_str=move：四字符ICCS，例如h2e2
    # 返回：Move对象；本项目只在临时棋盘b上执行。
    m = b.move_iccs(move)
    # [PY-board-079] m.to_text｜来源：第三方cchess库：cchess/move.py，Move.to_text
    # 功能：把该Move转换为中文记谱，例如炮二平五。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：中文字符串；未传detailed，使用默认False。
    text = m.to_text()
    rows[z[0]][z[1]] = p
    rows[a[0]][a[1]] = '.'
    # [PY-board-080] encode｜来源：本项目 board.py，encode
    # 功能：将棋盘二维列表压缩为六字段 FEN；此函数本身不做完整局面合法性校验。
    # 参数 rows=rows：10行×9列二维棋盘；空点用 "."，红棋大写、黑棋小写。
    # 参数 turn='b' if turn == 'w' else 'w'：行棋方：w红、b黑，默认w。
    # 参数 half=0 if captured != '.' or p.lower() == 'p' else half + 1：无吃子/无兵卒移动的半回合计数，默认0；不是完整长将长捉规则。
    # 参数 full=full + (turn == 'b')：完整回合号，从1开始，黑走后增加。
    # 返回：FEN 字符串。
    # [PY-board-081] p.lower｜来源：Python内置str类型的方法
    # 功能：将文本中的字母转为小写，便于忽略阵营比较棋种。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：新字符串，不原地修改。
    new = encode(rows, 'b' if turn == 'w' else 'w', 0 if captured != '.' or p.lower() == 'p' else half + 1, full + (turn == 'b'))
    # [PY-board-082] dict｜来源：Python内置类型dict的构造调用
    # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
    # 参数 uci=move：四字符ICCS走法，例如h2e2
    # 参数 text=text：中文记谱或测试文本
    # 参数 **{'from': a, 'to': z}：展开字典为关键字参数；字段见其上方定义及《调用函数说明》的请求字段表
    # 参数 展开字段 from=a：棋盘起点[row,col]，不是毫米坐标或舵机角度
    # 参数 展开字段 to=z：棋盘终点[row,col]，不是毫米坐标或舵机角度
    # 参数 piece=p：单枚棋子的编码；大写红、小写黑
    # 参数 capture=None if captured == '.' else captured：被吃棋子编码，未吃子为None
    # 返回：新字典。
    return (new, dict(uci=move, text=text, **{'from': a, 'to': z}, piece=p, capture=None if captured == '.' else captured))

def infer_move(fen, pieces):
    """功能：枚举上一局面的所有合法一步，要求恰好一个结果与观察到的全盘一致，用于识别人类走子。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
        pieces：全盘存活棋子列表，每项为 {piece,row,col}；不是只传发生变化的棋子。

    返回：唯一匹配的 ICCS 走法；零个或多个匹配都抛 ValueError。"""
    # [PY-board-083] parse｜来源：本项目 board.py，parse
    # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
    # 参数 fen=from_coordinates(pieces)：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
    # [PY-board-084] from_coordinates｜来源：本项目 board.py，from_coordinates
    # 功能：将全部棋子列表转为 FEN，检查坐标范围、类别、重复格；不在这里检查将帅数量等完整合法性，需要调用 validate。
    # 参数 pieces=pieces：全盘存活棋子列表，每项为 {piece,row,col}；不是只传发生变化的棋子。
    # 返回：FEN；未列出的交叉点视为空点。
    target = parse(from_coordinates(pieces))[0]
    # [PY-board-085] legal_moves｜来源：本项目 board.py，legal_moves
    # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
    # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：逐项产生4字符 ICCS 走法。
    # [PY-board-086] parse｜来源：本项目 board.py，parse
    # 功能：拆分六字段 FEN，得到10×9棋盘、行棋方和计数；空点用点号表示。
    # 参数 fen=apply(fen, m)[0]：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 返回：(rows, turn, half, full)：棋盘二维列表、w红/b黑、半回合计数、完整回合号。
    # [PY-board-087] apply｜来源：本项目 board.py，apply
    # 功能：复核一步合法走法，生成走后 FEN、吃子信息和中文记谱；不修改传入棋局。
    # 参数 fen=fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
    # 参数 move=m：4字符ICCS走法，例如 b2e2；不是坐标列表。
    # 返回：(new_fen, meta)，meta 含 uci/text/from/to/piece/capture。
    matches = [m for m in legal_moves(fen) if parse(apply(fen, m)[0])[0] == target]
    # [PY-board-088] len｜来源：Python内置函数
    # 功能：取得容器、字符串或字节序列的元素数量。
    # 参数 obj=matches：要计数的对象；字节序列按字节数计，字符串按字符数计
    # 返回：整数长度。
    if len(matches) != 1:
        # [PY-board-089] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Observation does not match exactly one legal move'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Observation does not match exactly one legal move')
    return matches[0]

def position_key(fen):
    """功能：提取棋子布局和行棋方，忽略 FEN 计数，用于重复局面计数。

    参数：
        fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。

    返回：FEN 前两个字段组成的字符串。"""
    # [PY-board-090] ' '.join｜来源：Python内置str类型的方法
    # 功能：使用调用者字符串作为分隔符连接各项。
    # 参数 iterable=fen.split()[:2]：待连接的字符串序列
    # 返回：拼接后的字符串。
    # [PY-board-091] fen.split｜来源：Python内置str类型的方法
    # 功能：按分隔符切开文本；未传分隔符时按连续空白切分。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：字符串列表。
    return ' '.join(fen.split()[:2])
