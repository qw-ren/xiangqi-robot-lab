# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""串口编码层：紧凑 JSON → CRC32 校验 → 换行分帧；只负责通信字节，不决定机械臂动作。"""
import json  # 导入JSON消息、棋谱和事件日志读写。
import zlib  # 导入标准库CRC32校验计算。
import copy  # 导入复制状态，避免调用者改坏正式数据。
MAX_FRAME = 16384  # 串口整帧最大16384字节，必须与对端保持一致。

def serial_response(response):
    """功能：复制响应并把 state/result 的完整 history 换成条数，避免长局棋谱撑爆串口帧；原 HTTP 响应保持不变。

    参数：
        response：Game.rpc生成的响应字典，可能包含完整历史。

    返回：用于串口的响应副本。"""
    # [PY-protocol-001] copy.deepcopy｜来源：Python标准库copy
    # 功能：递归复制嵌套对象，避免修改副本影响原数据。
    # 参数 x=response：待复制的字典、列表或状态对象
    # 返回：独立副本。
    out = copy.deepcopy(response)
    for field in ('state', 'result'):
        # [PY-protocol-002] out.get｜来源：Python内置dict类型的方法
        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
        # 参数 key=field：字段名
        # 返回：字段值或default；省略default时为None。
        value = out.get(field)
        # [PY-protocol-003] isinstance｜来源：Python内置函数
        # 功能：检查对象是否属于指定类型或其子类。
        # 参数 object=value：被检查的对象
        # 参数 classinfo=dict：允许的类型，或多个类型组成的元组
        # 返回：True/False。
        if isinstance(value, dict) and 'history' in value:
            # [PY-protocol-004] len｜来源：Python内置函数
            # 功能：取得容器、字符串或字节序列的元素数量。
            # 参数 obj=value.pop('history')：要计数的对象；字节序列按字节数计，字符串按字符数计
            # 返回：整数长度。
            # [PY-protocol-005] value.pop｜来源：Python内置dict类型的方法
            # 功能：移除指定字段并取出它的值。
            # 参数 key='history'：要删除并读取的字段名；此字段表示已正式提交的棋谱列表
            # 返回：原字段值，或传入的default。
            value['history_count'] = len(value.pop('history'))
    return out

def pack(message):
    """功能：把字典编码为确定顺序的紧凑 ASCII JSON，计算原 payload 字节的 CRC32，再封装外层 JSON 并加 LF。

    参数：
        message：准备发送的内层消息字典，不带payload/crc32外壳。

    返回：可直接 ser.write 的 bytes；超出 MAX_FRAME 则拒绝。"""
    # [PY-protocol-006] json.dumps｜来源：Python标准库json
    # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
    # 参数 obj=message：待序列化的字典、列表等对象
    # 参数 ensure_ascii=True：True把非ASCII字符转成转义；False保留中文
    # 参数 separators=(',', ':')：逗号、冒号分隔符元组；去掉空格可缩小报文
    # 参数 sort_keys=True：True按字段名排序，使请求指纹和CRC原文可复现
    # 返回：str字符串。
    p = json.dumps(message, ensure_ascii=True, separators=(',', ':'), sort_keys=True)
    # [PY-protocol-007] json.dumps｜来源：Python标准库json
    # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
    # 参数 obj={'payload': p, 'crc32': f"{zlib.crc32(p.encode('ascii')):08x}"}：待序列化的字典、列表等对象
    # 参数 separators=(',', ':')：逗号、冒号分隔符元组；去掉空格可缩小报文
    # 返回：str字符串。
    # [PY-protocol-008] json.dumps({'payload': p, 'crc32': f"{zlib.crc32(p.encode('ascii')):08x}"}, separators=(',', ':')).encode｜来源：Python内置str类型的方法
    # 功能：将文本按指定编码转换成字节。
    # 参数 encoding='ascii'：编码名；省略默认UTF-8，协议中显式使用ASCII
    # 返回：bytes；编码不可表示时抛异常。
    # [PY-protocol-009] zlib.crc32｜来源：Python标准库zlib
    # 功能：对原始字节计算CRC32，用于发现传输损坏，不是身份认证。
    # 参数 data=p.encode('ascii')：要校验的payload原文ASCII字节
    # 返回：32位整数；:08x将其显示为8位小写十六进制。
    # [PY-protocol-010] p.encode｜来源：Python内置str类型的方法
    # 功能：将文本按指定编码转换成字节。
    # 参数 encoding='ascii'：编码名；省略默认UTF-8，协议中显式使用ASCII
    # 返回：bytes；编码不可表示时抛异常。
    out = json.dumps({'payload': p, 'crc32': f"{zlib.crc32(p.encode('ascii')):08x}"}, separators=(',', ':')).encode('ascii') + b'\n'
    # [PY-protocol-011] len｜来源：Python内置函数
    # 功能：取得容器、字符串或字节序列的元素数量。
    # 参数 obj=out：要计数的对象；字节序列按字节数计，字符串按字符数计
    # 返回：整数长度。
    if len(out) > MAX_FRAME:
        # [PY-protocol-012] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Frame too large'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Frame too large')
    return out

def unpack(line):
    """功能：校验帧长度与 payload 原始 ASCII 字节的 CRC32，再解码出请求对象；CRC只检错，不是身份认证。

    参数：
        line：含外层payload和crc32的完整JSON帧，可带末尾LF。

    返回：内层消息字典；校验失败抛异常。"""
    # [PY-protocol-013] len｜来源：Python内置函数
    # 功能：取得容器、字符串或字节序列的元素数量。
    # 参数 obj=line：要计数的对象；字节序列按字节数计，字符串按字符数计
    # 返回：整数长度。
    if len(line) > MAX_FRAME:
        # [PY-protocol-014] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Frame too large'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Frame too large')
    # [PY-protocol-015] json.loads｜来源：Python标准库json
    # 功能：把JSON字符串或字节解析成Python对象。
    # 参数 s=line：JSON原文，允许str/bytes/bytearray
    # 返回：解码得到的字典、列表等；格式错误抛异常。
    e = json.loads(line)
    p = e['payload']
    # [PY-protocol-016] zlib.crc32｜来源：Python标准库zlib
    # 功能：对原始字节计算CRC32，用于发现传输损坏，不是身份认证。
    # 参数 data=p.encode('ascii')：要校验的payload原文ASCII字节
    # 返回：32位整数；:08x将其显示为8位小写十六进制。
    # [PY-protocol-017] p.encode｜来源：Python内置str类型的方法
    # 功能：将文本按指定编码转换成字节。
    # 参数 encoding='ascii'：编码名；省略默认UTF-8，协议中显式使用ASCII
    # 返回：bytes；编码不可表示时抛异常。
    crc = f"{zlib.crc32(p.encode('ascii')):08x}"
    if e['crc32'] != crc:
        # [PY-protocol-018] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='CRC mismatch'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('CRC mismatch')
    # [PY-protocol-019] json.loads｜来源：Python标准库json
    # 功能：把JSON字符串或字节解析成Python对象。
    # 参数 s=p：JSON原文，允许str/bytes/bytearray
    # 返回：解码得到的字典、列表等；格式错误抛异常。
    m = json.loads(p)
    # [PY-protocol-020] isinstance｜来源：Python内置函数
    # 功能：检查对象是否属于指定类型或其子类。
    # 参数 object=m：被检查的对象
    # 参数 classinfo=dict：允许的类型，或多个类型组成的元组
    # 返回：True/False。
    if not isinstance(m, dict):
        # [PY-protocol-021] ValueError｜来源：Python内置异常类型
        # 功能：构造异常；前面带raise才会真正抛出。
        # 参数 *args[1]='Payload must be an object'：错误说明字符串
        # 返回：异常对象。
        raise ValueError('Payload must be an object')
    return m

class Framer:  # 定义接收分帧器：buf保存未结束的帧，discard表示当前超长帧正在丢弃；收到LF后恢复正常接收。
    """接收分帧器：buf保存未结束的帧，discard表示当前超长帧正在丢弃；收到LF后恢复正常接收。"""

    def __init__(self):
        """功能：建立接收缓冲 buf 和超长帧丢弃标志 discard；每条串口链路使用一个实例。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。

        返回：无。"""
        # [PY-protocol-022] bytearray｜来源：Python内置类型bytearray的构造调用
        # 功能：创建可修改的字节缓冲，供串口接收累积。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：bytearray对象。
        self.buf = bytearray()
        self.discard = False

    def feed(self, data):
        """功能：把任意分片字节累积到 LF，拆出完整帧；超长帧丢弃到下一个 LF 后恢复，可处理分包和粘包。

        参数：
            self：当前类的实例，调用实例方法时由 Python 自动传入；不需要手动填写。
            data：本次 ser.read 返回的 bytes/bytearray 任意分片，可以不足一帧或含多帧。

        返回：完整帧 bytes 列表；尚未收完整时为空列表。"""
        result = []
        for b in data:
            if b == 10:
                if not self.discard and self.buf:
                    # [PY-protocol-023] result.append｜来源：Python内置list类型的方法
                    # 功能：在列表末尾添加一项，原地修改列表。
                    # 参数 object=bytes(self.buf) + b'\n'：要追加的单项内容，不是整个列表的展开
                    # 返回：None。
                    # [PY-protocol-024] bytes｜来源：Python内置类型bytes的构造调用
                    # 功能：将可变字节缓冲复制为不可变bytes，形成完整帧。
                    # 参数 source=self.buf：源字节序列
                    # 返回：bytes对象。
                    result.append(bytes(self.buf) + b'\n')
                # [PY-protocol-025] self.buf.clear｜来源：Python内置bytearray类型的方法
                # 功能：清空现有接收缓冲，用于完成帧或丢弃超长帧。
                # 参数 本次没有显式参数；省略的可选参数使用默认值
                # 返回：None。
                self.buf.clear()
                self.discard = False
            elif not self.discard:
                # [PY-protocol-026] self.buf.append｜来源：Python内置bytearray类型的方法
                # 功能：把一个0..255整数作为一个字节追加到缓冲。
                # 参数 item=b：一个字节对应的整数
                # 返回：None。
                self.buf.append(b)
                # [PY-protocol-027] len｜来源：Python内置函数
                # 功能：取得容器、字符串或字节序列的元素数量。
                # 参数 obj=self.buf：要计数的对象；字节序列按字节数计，字符串按字符数计
                # 返回：整数长度。
                if len(self.buf) >= MAX_FRAME:
                    # [PY-protocol-028] self.buf.clear｜来源：Python内置bytearray类型的方法
                    # 功能：清空现有接收缓冲，用于完成帧或丢弃超长帧。
                    # 参数 本次没有显式参数；省略的可选参数使用默认值
                    # 返回：None。
                    self.buf.clear()
                    self.discard = True
        return result
