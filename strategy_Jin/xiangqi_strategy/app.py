# -*- coding: utf-8 -*-  # 中文注释采用 UTF-8。
"""服务入口：创建 Flask 网页/API，并可选启动串口后台接收线程。它不是当前 CH-RO 网页的入口。"""
import os  # 导入读取环境变量和访问令牌。
import json  # 导入JSON消息、棋谱和事件日志读写。
import time  # 导入单调时钟、搜索/动作超时与日志时间。
import threading  # 导入棋局/引擎锁以及串口和读管道后台线程。
import argparse  # 导入解析命令行参数。
import atexit  # 导入正常退出时关闭引擎。
from pathlib import Path  # 导入跨平台文件路径。
from flask import Flask, request, jsonify, send_from_directory, Response  # 导入HTTP网页、接口与文件响应。
from board import START, legal_moves  # 导入本项目的坐标/FEN转换和规则函数。
from game import Game  # 导入本项目唯一棋局状态机。
from engine import DemoEngine, Pikafish  # 导入本项目演示和真实引擎适配器。
from protocol import pack, unpack, Framer, serial_response  # 导入本项目CRC封装和串口分帧器。

def create_app(game, token=''):
    """功能：把同一个 Game 实例接入网页、状态/合法走法/RPC/导出接口，并配置令牌和跨站写入保护；不在这里启动监听。

    参数：
        game：共享的 Game 实例，网页和串口必须使用同一个实例。
        token：API访问令牌，默认空字符串表示不启用令牌校验。

    返回：Flask 应用对象。"""
    # [PY-app-001] Flask｜来源：第三方Flask库：flask.Flask
    # 功能：创建网页应用对象；创建时尚未监听端口。
    # 参数 import_name=__name__：模块名称，帮助Flask定位资源
    # 参数 static_folder='static'：静态文件目录，此处是static
    # 返回：Flask应用对象。
    app = Flask(__name__, static_folder='static')
    app.config['MAX_CONTENT_LENGTH'] = 16384

    # [DEC-001] 隐式装饰器调用｜来源：第三方Flask库：Flask.before_request装饰器
    # 功能：@语法等价于把下面的auth函数传入app.before_request，登记每个请求之前执行的检查。
    # 参数 func=auth：函数对象，由@语法自动传入；不是这里立即执行auth()。
    @app.before_request
    def auth():
        """功能：请求前核对 API 的 X-Token，并拒绝浏览器标记的跨站 POST；从 Flask 请求上下文读取信息。

        参数：
            无显式参数；可能读取外层闭包或命令行。

        返回：拒绝时返回 JSON与状态码；通过时隐式返回 None。"""
        # [PY-app-002] request.path.startswith｜来源：Python内置str类型的方法
        # 功能：判断字符串是否具有指定前缀。
        # 参数 prefix='/api/'：要匹配的开头文本
        # 返回：True/False。
        # [PY-app-003] request.headers.get｜来源：第三方Werkzeug库：Headers/EnvironHeaders；由flask.request.headers提供
        # 功能：读取HTTP请求头；未找到且没指定默认值时返回None。
        # 参数 key='X-Token'：请求头名称
        # 返回：请求头值或default。
        if request.path.startswith('/api/') and token and (request.headers.get('X-Token') != token):
            # [PY-app-004] jsonify｜来源：第三方Flask库：flask.jsonify
            # 功能：把位置对象或命名字段转换为HTTP JSON响应。
            # 参数 ok=False：请求是否成功的布尔值
            # 参数 error='Missing/incorrect X-Token'：错误说明文本
            # 返回：Flask Response对象。
            return (jsonify(ok=False, error='Missing/incorrect X-Token'), 401)
        # [PY-app-005] request.headers.get｜来源：第三方Werkzeug库：Headers/EnvironHeaders；由flask.request.headers提供
        # 功能：读取HTTP请求头；未找到且没指定默认值时返回None。
        # 参数 key='Sec-Fetch-Site'：请求头名称
        # 返回：请求头值或default。
        if request.method == 'POST' and request.headers.get('Sec-Fetch-Site') == 'cross-site':
            # [PY-app-006] jsonify｜来源：第三方Flask库：flask.jsonify
            # 功能：把位置对象或命名字段转换为HTTP JSON响应。
            # 参数 ok=False：请求是否成功的布尔值
            # 参数 error='Cross-site request rejected'：错误说明文本
            # 返回：Flask Response对象。
            return (jsonify(ok=False, error='Cross-site request rejected'), 403)

    # [PY-app-007] app.get｜来源：第三方Flask库：Flask.get路由装饰器工厂
    # 功能：创建仅接收GET的路由装饰器；随后由装饰器登记下面的处理函数。
    # 参数 rule='/'：URL路径
    # 返回：路由装饰器；不是立即执行下面的函数。
    # [DEC-002] 隐式装饰器调用｜来源：第三方Flask库：app.get/post返回的路由装饰器
    # 功能：@语法把下面的index函数作为回调传入路由装饰器，登记URL；不是此时处理HTTP请求。
    # 参数 func=index：函数对象，由@语法自动传入；不是这里立即执行index()。
    @app.get('/')
    def index():
        """功能：返回 static/index.html 页面；后续数据由页面请求 API 获取。

        参数：
            无显式参数；可能读取外层闭包或命令行。

        返回：Flask 文件响应。"""
        # [PY-app-008] send_from_directory｜来源：第三方Flask库：flask.send_from_directory
        # 功能：从指定资源目录返回文件给浏览器。
        # 参数 directory=app.static_folder：文件所在目录
        # 参数 path='index.html'：目录内的相对文件名，此处index.html
        # 返回：HTTP文件响应。
        return send_from_directory(app.static_folder, 'index.html')

    # [PY-app-009] app.get｜来源：第三方Flask库：Flask.get路由装饰器工厂
    # 功能：创建仅接收GET的路由装饰器；随后由装饰器登记下面的处理函数。
    # 参数 rule='/api/state'：URL路径
    # 返回：路由装饰器；不是立即执行下面的函数。
    # [DEC-003] 隐式装饰器调用｜来源：第三方Flask库：app.get/post返回的路由装饰器
    # 功能：@语法把下面的state函数作为回调传入路由装饰器，登记URL；不是此时处理HTTP请求。
    # 参数 func=state：函数对象，由@语法自动传入；不是这里立即执行state()。
    @app.get('/api/state')
    def state():
        """功能：在棋局锁内取得并返回正式状态；state 调用会更新演示棋钟。

        参数：
            无显式参数；可能读取外层闭包或命令行。

        返回：JSON 响应。"""
        with game.lock:
            # [PY-app-010] jsonify｜来源：第三方Flask库：flask.jsonify
            # 功能：把位置对象或命名字段转换为HTTP JSON响应。
            # 参数 *args[1]=game.state()：要作为JSON正文的对象；本项目最多传一个位置对象
            # 返回：Flask Response对象。
            # [PY-app-011] game.state｜来源：本项目 game.py，Game.state
            # 功能：先调用 tick，再返回正式局面和待执行计划快照；不对外暴露内部单调时钟 deadline。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：状态字典，含 phase/fen/pieces/ply/pending 等；未建局时只含少量字段。
            return jsonify(game.state())

    # [PY-app-012] app.get｜来源：第三方Flask库：Flask.get路由装饰器工厂
    # 功能：创建仅接收GET的路由装饰器；随后由装饰器登记下面的处理函数。
    # 参数 rule='/api/legal'：URL路径
    # 返回：路由装饰器；不是立即执行下面的函数。
    # [DEC-004] 隐式装饰器调用｜来源：第三方Flask库：app.get/post返回的路由装饰器
    # 功能：@语法把下面的legal函数作为回调传入路由装饰器，登记URL；不是此时处理HTTP请求。
    # 参数 func=legal：函数对象，由@语法自动传入；不是这里立即执行legal()。
    @app.get('/api/legal')
    def legal():
        """功能：仅在已开局且 READY 时返回合法走法，其他状态返回空列表，供网页标记落点。

        参数：
            无显式参数；可能读取外层闭包或命令行。

        返回：合法 ICCS 字符串列表的 JSON 响应。"""
        with game.lock:
            # [PY-app-013] jsonify｜来源：第三方Flask库：flask.jsonify
            # 功能：把位置对象或命名字段转换为HTTP JSON响应。
            # 参数 *args[1]=list(legal_moves(game.fen)) if game.started and game.phase == 'READY' else []：要作为JSON正文的对象；本项目最多传一个位置对象
            # 返回：Flask Response对象。
            # [PY-app-014] list｜来源：Python内置类型list的构造调用
            # 功能：遍历可迭代对象并收集为列表；可把合法走法生成器实际展开。
            # 参数 iterable=legal_moves(game.fen)：列表、生成器等可迭代对象
            # 返回：新列表。
            # [PY-app-015] legal_moves｜来源：本项目 board.py，legal_moves
            # 功能：使用 cchess 枚举走法，过滤走后自将和直接吃将；这是生成器，可用于规则检查。
            # 参数 fen=game.fen：六字段中国象棋 FEN 字符串，w 表示红走、b 表示黑走。
            # 返回：逐项产生4字符 ICCS 走法。
            return jsonify(list(legal_moves(game.fen)) if game.started and game.phase == 'READY' else [])

    # [PY-app-016] app.post｜来源：第三方Flask库：Flask.post路由装饰器工厂
    # 功能：创建仅接收POST的路由装饰器，用于提交动作。
    # 参数 rule='/api/rpc'：URL路径
    # 返回：路由装饰器。
    # [DEC-005] 隐式装饰器调用｜来源：第三方Flask库：app.get/post返回的路由装饰器
    # 功能：@语法把下面的rpc函数作为回调传入路由装饰器，登记URL；不是此时处理HTTP请求。
    # 参数 func=rpc：函数对象，由@语法自动传入；不是这里立即执行rpc()。
    @app.post('/api/rpc')
    def rpc():
        """功能：读取 HTTP JSON 对象并交给同一 Game.rpc；HTTP 内层消息不需要再套 CRC。

        参数：
            无显式参数；可能读取外层闭包或命令行。

        返回：RPC 响应的 JSON；非对象输入返回400。"""
        # [PY-app-017] request.get_json｜来源：第三方Flask库：flask.request指向当前Request（Werkzeug实现）
        # 功能：解析当前HTTP请求的JSON正文。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：Python对象；调用方再检查必须为dict。
        m = request.get_json()
        # [PY-app-018] isinstance｜来源：Python内置函数
        # 功能：检查对象是否属于指定类型或其子类。
        # 参数 object=m：被检查的对象
        # 参数 classinfo=dict：允许的类型，或多个类型组成的元组
        # 返回：True/False。
        if not isinstance(m, dict):
            # [PY-app-019] jsonify｜来源：第三方Flask库：flask.jsonify
            # 功能：把位置对象或命名字段转换为HTTP JSON响应。
            # 参数 ok=False：请求是否成功的布尔值
            # 参数 error='Expected JSON object'：错误说明文本
            # 返回：Flask Response对象。
            return (jsonify(ok=False, error='Expected JSON object'), 400)
        # [PY-app-020] jsonify｜来源：第三方Flask库：flask.jsonify
        # 功能：把位置对象或命名字段转换为HTTP JSON响应。
        # 参数 *args[1]=game.rpc(m)：要作为JSON正文的对象；本项目最多传一个位置对象
        # 返回：Flask Response对象。
        # [PY-app-021] game.rpc｜来源：本项目 game.py，Game.rpc
        # 功能：所有入口共用的加锁请求门面：更新时限、检查 id、缓存去重、分发 handle 并包装统一响应。同 id 同内容返回缓存响应快照，不重新执行。
        # 参数 m=m：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
        # 返回：{ok,id,result,state} 或 {ok:false,error,state}；部分 id 校验错误不附 state。
        return jsonify(game.rpc(m))

    # [PY-app-022] app.get｜来源：第三方Flask库：Flask.get路由装饰器工厂
    # 功能：创建仅接收GET的路由装饰器；随后由装饰器登记下面的处理函数。
    # 参数 rule='/api/export'：URL路径
    # 返回：路由装饰器；不是立即执行下面的函数。
    # [DEC-006] 隐式装饰器调用｜来源：第三方Flask库：app.get/post返回的路由装饰器
    # 功能：@语法把下面的export函数作为回调传入路由装饰器，登记URL；不是此时处理HTTP请求。
    # 参数 func=export：函数对象，由@语法自动传入；不是这里立即执行export()。
    @app.get('/api/export')
    def export():
        """功能：加锁导出正式状态、初始 FEN 和完整走法，作为 JSON 附件下载。

        参数：
            无显式参数；可能读取外层闭包或命令行。

        返回：文件名为 xiangqi-game.json 的 HTTP 响应。"""
        with game.lock:
            # [PY-app-023] game.state｜来源：本项目 game.py，Game.state
            # 功能：先调用 tick，再返回正式局面和待执行计划快照；不对外暴露内部单调时钟 deadline。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：状态字典，含 phase/fen/pieces/ply/pending 等；未建局时只含少量字段。
            s = game.state()
            if game.started:
                # [PY-app-024] s.update｜来源：Python内置dict类型的方法
                # 功能：把命名字段写入现有字典，同名字段覆盖。
                # 参数 root_fen=game.root：对局初始FEN
                # 参数 moves=game.moves：从初始局面起已提交的完整ICCS历史
                # 返回：None。
                s.update(root_fen=game.root, moves=game.moves)
            # [PY-app-025] Response｜来源：第三方Flask库：flask.Response
            # 功能：建立可带附件下载头的HTTP响应。
            # 参数 response=json.dumps(s, ensure_ascii=False, indent=2)：响应正文，此处已序列化JSON文本
            # 参数 mimetype='application/json'：媒体类型，此处application/json
            # 参数 headers={'Content-Disposition': 'attachment; filename=xiangqi-game.json'}：额外响应头；Content-Disposition决定下载文件名
            # 返回：Response对象。
            # [PY-app-026] json.dumps｜来源：Python标准库json
            # 功能：把Python对象序列化为JSON文本；不是直接发送网络或串口。
            # 参数 obj=s：待序列化的字典、列表等对象
            # 参数 ensure_ascii=False：True把非ASCII字符转成转义；False保留中文
            # 参数 indent=2：输出缩进宽度；便于人工阅读但报文更长
            # 返回：str字符串。
            return Response(json.dumps(s, ensure_ascii=False, indent=2), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename=xiangqi-game.json'})
    return app

def serial_worker(game, port, baud):
    """功能：打开双向串口，用 Framer/CRC 解码请求并调用 Game.rpc，再编码响应；断线冻结棋局，不自动重连或重播机械命令。

    参数：
        game：共享的 Game 实例，网页和串口必须使用同一个实例。
        port：串口设备路径，例如 COM8 或 /dev/ttyUSB0；不是网页端口。
        baud：串口波特率，一般115200，必须与对端相同。

    返回：无；作为守护线程运行，串口错误时结束并打印原因。"""
    import serial
    try:
        # [PY-app-027] serial.Serial｜来源：第三方pyserial库：serial.Serial
        # 功能：打开串口并建立读写对象；设备名和波特率必须与实际连接一致。
        # 参数 port=port：设备名，例如COM8或/dev/ttyUSB0
        # 参数 baudrate=baud：波特率，单位bit/s；本项目默认115200
        # 参数 timeout=0.1：单次读操作最长等待秒数
        # 参数 write_timeout=2：写操作最长等待秒数
        # 返回：串口对象；打开失败抛串口异常。
        with serial.Serial(port, baud, timeout=0.1, write_timeout=2) as ser:
            # [PY-app-028] Framer｜来源：本项目 protocol.py，Framer.__init__
            # 功能：建立接收缓冲 buf 和超长帧丢弃标志 discard；每条串口链路使用一个实例。
            # 参数 本次没有显式参数；省略的可选参数使用默认值
            # 返回：Framer实例。
            framer = Framer()
            while True:
                # [PY-app-029] framer.feed｜来源：本项目 protocol.py，Framer.feed
                # 功能：把任意分片字节累积到 LF，拆出完整帧；超长帧丢弃到下一个 LF 后恢复，可处理分包和粘包。
                # 参数 data=ser.read(1024)：本次 ser.read 返回的 bytes/bytearray 任意分片，可以不足一帧或含多帧。
                # 返回：完整帧 bytes 列表；尚未收完整时为空列表。
                # [PY-app-030] ser.read｜来源：第三方pyserial库：serial.Serial.read
                # 功能：从串口读取最多指定字节；超时可返回更少字节或空bytes。
                # 参数 size=1024：本次最多读取的字节数
                # 返回：bytes片段，不保证恰好是一整帧。
                for line in framer.feed(ser.read(1024)):
                    try:
                        # [PY-app-031] game.rpc｜来源：本项目 game.py，Game.rpc
                        # 功能：所有入口共用的加锁请求门面：更新时限、检查 id、缓存去重、分发 handle 并包装统一响应。同 id 同内容返回缓存响应快照，不重新执行。
                        # 参数 m=unpack(line)：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
                        # 返回：{ok,id,result,state} 或 {ok:false,error,state}；部分 id 校验错误不附 state。
                        # [PY-app-032] unpack｜来源：本项目 protocol.py，unpack
                        # 功能：校验帧长度与 payload 原始 ASCII 字节的 CRC32，再解码出请求对象；CRC只检错，不是身份认证。
                        # 参数 line=line：含外层payload和crc32的完整JSON帧，可带末尾LF。
                        # 返回：内层消息字典；校验失败抛异常。
                        out = game.rpc(unpack(line))
                    except Exception as e:
                        # [PY-app-033] dict｜来源：Python内置类型dict的构造调用
                        # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
                        # 参数 ok=False：请求是否成功的布尔值
                        # 参数 error=str(e)：错误说明文本
                        # 返回：新字典。
                        # [PY-app-034] str｜来源：Python内置类型str的构造调用
                        # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                        # 参数 object=e：要转换的对象
                        # 返回：字符串。
                        out = dict(ok=False, error=str(e))
                    try:
                        # [PY-app-035] pack｜来源：本项目 protocol.py，pack
                        # 功能：把字典编码为确定顺序的紧凑 ASCII JSON，计算原 payload 字节的 CRC32，再封装外层 JSON 并加 LF。
                        # 参数 message=serial_response(out)：准备发送的内层消息字典，不带payload/crc32外壳。
                        # 返回：可直接 ser.write 的 bytes；超出 MAX_FRAME 则拒绝。
                        # [PY-app-036] serial_response｜来源：本项目 protocol.py，serial_response
                        # 功能：复制响应并把 state/result 的完整 history 换成条数，避免长局棋谱撑爆串口帧；原 HTTP 响应保持不变。
                        # 参数 response=out：Game.rpc生成的响应字典，可能包含完整历史。
                        # 返回：用于串口的响应副本。
                        wire = pack(serial_response(out))
                    except Exception as exc:
                        # [PY-app-037] pack｜来源：本项目 protocol.py，pack
                        # 功能：把字典编码为确定顺序的紧凑 ASCII JSON，计算原 payload 字节的 CRC32，再封装外层 JSON 并加 LF。
                        # 参数 message=dict(ok=False, id=out.get('id'), error='Response encoding failed: ' + str(exc))：准备发送的内层消息字典，不带payload/crc32外壳。
                        # 返回：可直接 ser.write 的 bytes；超出 MAX_FRAME 则拒绝。
                        # [PY-app-038] dict｜来源：Python内置类型dict的构造调用
                        # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
                        # 参数 ok=False：请求是否成功的布尔值
                        # 参数 id=out.get('id')：请求唯一编号；用于幂等去重
                        # 参数 error='Response encoding failed: ' + str(exc)：错误说明文本
                        # 返回：新字典。
                        # [PY-app-039] out.get｜来源：Python内置dict类型的方法
                        # 功能：按字段名取值；字段缺失时返回备用值，不抛KeyError。
                        # 参数 key='id'：字段名；此字段表示请求唯一编号；用于幂等去重
                        # 返回：字段值或default；省略default时为None。
                        # [PY-app-040] str｜来源：Python内置类型str的构造调用
                        # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                        # 参数 object=exc：要转换的对象
                        # 返回：字符串。
                        wire = pack(dict(ok=False, id=out.get('id'), error='Response encoding failed: ' + str(exc)))
                    # [PY-app-041] ser.write｜来源：第三方pyserial库：serial.Serial.write
                    # 功能：将二进制帧写到串口。
                    # 参数 data=wire：已由pack编码且带LF的bytes
                    # 返回：写入字节数。
                    ser.write(wire)
                    # [PY-app-042] ser.flush｜来源：第三方pyserial库：serial.Serial.flush
                    # 功能：等待待发送串口数据写出；不表示机械臂已经执行。
                    # 参数 本次没有显式参数；省略的可选参数使用默认值
                    # 返回：None。
                    ser.flush()
                with game.lock:
                    # [PY-app-043] game.tick｜来源：本项目 game.py，Game.tick
                    # 功能：更新 READY 阶段的演示棋钟，并检查 ACK/执行/复核超时；超时冻结到 FAULT。由状态查询或串口轮询触发，不是独立定时器。
                    # 参数 本次没有显式参数；省略的可选参数使用默认值
                    # 返回：无；会修改 phase/error/clocks。
                    game.tick()
    except Exception as exc:
        with game.lock:
            if game.started:
                game.phase = 'FAULT'
                # [PY-app-044] str｜来源：Python内置类型str的构造调用
                # 功能：取得对象的字符串形式，例如路径、编号或异常说明。
                # 参数 object=exc：要转换的对象
                # 返回：字符串。
                game.error = 'Serial link stopped: ' + str(exc)
                # [PY-app-045] game.log｜来源：本项目 game.py，Game.log
                # 功能：向当前 game_id 的 JSONL 文件追加带时间戳的事件；此处为审计记录，不负责重启恢复。
                # 参数 event='fault'：日志事件名称，例如 new、plan、commit、fault。
                # 参数 data=game.error：可JSON序列化的日志内容，例如元信息字典或错误字符串。
                # 返回：无；写入失败会向上传递异常。
                game.log('fault', game.error)
        # [PY-app-046] print｜来源：Python内置函数
        # 功能：向标准输出打印调试信息或结果；flush可要求立即刷新。
        # 参数 *objects[1]='Serial stopped:'：依次打印的对象
        # 参数 *objects[2]=exc：依次打印的对象
        # 参数 flush=True：True表示立即刷新输出缓冲
        # 返回：None。
        print('Serial stopped:', exc, flush=True)

def main():
    """功能：解析启动参数，选择真实或演示引擎，创建全新的虚拟棋局，可选启动串口线程，最后启动单进程 Flask 服务。

    参数：
        无显式参数；可能读取外层闭包或命令行。

    返回：无；命令行错误时退出，服务运行期间阻塞。"""
    # [PY-app-047] argparse.ArgumentParser｜来源：Python标准库argparse.ArgumentParser
    # 功能：创建命令行解析器，集中定义启动参数。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：ArgumentParser对象。
    p = argparse.ArgumentParser()
    # [PY-app-048] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--demo'：参数名或开关名称，如input、--engine；此参数用于设置使用演示引擎而非真实搜索
    # 参数 action='store_true'：store_true表示出现该开关时取True
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--demo', action='store_true')
    # [PY-app-049] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--engine'：参数名或开关名称，如input、--engine；此参数用于设置真实引擎可执行文件路径
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--engine')
    # [PY-app-050] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--nnue'：参数名或开关名称，如input、--engine；此参数用于设置配套NNUE权重文件路径
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--nnue')
    # [PY-app-051] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--serial'：参数名或开关名称，如input、--engine；此参数用于设置串口设备名
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--serial')
    # [PY-app-052] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--baud'：参数名或开关名称，如input、--engine；此参数用于设置串口波特率
    # 参数 type=int：把输入文本转换为指定类型的函数对象，例如int
    # 参数 default=115200：未提供参数时采用的默认值
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--baud', type=int, default=115200)
    # [PY-app-053] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--host'：参数名或开关名称，如input、--engine；此参数用于设置网页监听地址
    # 参数 default='127.0.0.1'：未提供参数时采用的默认值
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--host', default='127.0.0.1')
    # [PY-app-054] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--port'：参数名或开关名称，如input、--engine；此参数用于设置网页HTTP端口
    # 参数 type=int：把输入文本转换为指定类型的函数对象，例如int
    # 参数 default=5000：未提供参数时采用的默认值
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--port', type=int, default=5000)
    # [PY-app-055] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--threads'：参数名或开关名称，如input、--engine；此参数用于设置引擎搜索线程数
    # 参数 type=int：把输入文本转换为指定类型的函数对象，例如int
    # 参数 default=2：未提供参数时采用的默认值
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--threads', type=int, default=2)
    # [PY-app-056] p.add_argument｜来源：Python标准库argparse.ArgumentParser.add_argument
    # 功能：向解析器注册一个位置参数或可选开关。
    # 参数 *name_or_flags[1]='--hash'：参数名或开关名称，如input、--engine；此参数用于设置引擎Hash缓存MB数
    # 参数 type=int：把输入文本转换为指定类型的函数对象，例如int
    # 参数 default=128：未提供参数时采用的默认值
    # 返回：参数Action对象；本项目不保存返回值。
    p.add_argument('--hash', type=int, default=128)
    # [PY-app-057] p.parse_args｜来源：Python标准库argparse.ArgumentParser.parse_args
    # 功能：读取进程命令行，并按前面注册的规则解析。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：Namespace对象，可通过args.port等访问。
    args = p.parse_args()
    # [PY-app-058] os.environ.get｜来源：Python标准库os.environ（环境变量映射）
    # 功能：读取环境变量，未设置则采用默认值。
    # 参数 key='XIANGQI_TOKEN'：环境变量名，例如XIANGQI_TOKEN
    # 参数 default=''：环境变量不存在时的备用值
    # 返回：环境变量字符串或default。
    token = os.environ.get('XIANGQI_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost') and (not token):
        # [PY-app-059] p.error｜来源：Python标准库argparse.ArgumentParser.error
        # 功能：打印命令行用法和错误说明，然后以错误状态退出。
        # 参数 message='Set XIANGQI_TOKEN before exposing the console to the LAN'：给操作者看的错误说明
        # 返回：通常不正常返回。
        p.error('Set XIANGQI_TOKEN before exposing the console to the LAN')
    if not args.demo and (not args.engine):
        # [PY-app-060] p.error｜来源：Python标准库argparse.ArgumentParser.error
        # 功能：打印命令行用法和错误说明，然后以错误状态退出。
        # 参数 message='Use --engine PATH (real AI) or --demo (smoke test only)'：给操作者看的错误说明
        # 返回：通常不正常返回。
        p.error('Use --engine PATH (real AI) or --demo (smoke test only)')
    # [PY-app-061] DemoEngine｜来源：本项目 engine.py，DemoEngine类
    # 功能：创建只返回第一个合法走法的演示引擎，不加载NNUE。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：DemoEngine实例。
    # [PY-app-062] Pikafish｜来源：本项目 engine.py，Pikafish.__init__
    # 功能：启动并持有 UCI 子进程，建立读队列，完成 uci/isready 握手并设置线程、Hash和可选权重；失败时清理子进程。
    # 参数 path=args.engine：Pikafish可执行文件路径；测试中也可传Python解释器路径。
    # 参数 nnue=args.nnue：匹配该引擎版本的权重文件路径，None表示使用引擎默认配置。
    # 参数 threads=args.threads：引擎搜索线程数，默认2。
    # 参数 hash_mb=args.hash：引擎Hash内存大小，单位MB，默认128。
    # 返回：Pikafish实例。
    engine = DemoEngine() if args.demo else Pikafish(args.engine, args.nnue, args.threads, args.hash)
    # [PY-app-063] atexit.register｜来源：Python标准库atexit
    # 功能：注册解释器正常退出时调用的清理函数；此时不执行清理。
    # 参数 func=engine.close：退出时调用的回调，此处为引擎close绑定方法
    # 返回：被注册的函数对象。
    atexit.register(engine.close)
    # [PY-app-064] Game｜来源：本项目 game.py，Game.__init__
    # 功能：建立唯一引擎、可重入锁、请求缓存和日志目录，初始状态 IDLE，尚未建局。
    # 参数 engine=engine：具备 best_move(...) 和 close() 的引擎实例，DemoEngine或Pikafish。
    # 参数 log_dir=Path(__file__).parent / 'data'：JSONL事件日志保存目录，默认data；不是模型目录。
    # 返回：Game实例。
    # [PY-app-065] Path｜来源：Python标准库pathlib.Path
    # 功能：创建路径对象；仅创建对象，不会自动创建磁盘文件。
    # 参数 *pathsegments[1]=__file__：文件或目录路径的各部分
    # 返回：适合当前操作系统的Path对象。
    g = Game(engine, Path(__file__).parent / 'data')
    # [PY-app-066] g.rpc｜来源：本项目 game.py，Game.rpc
    # 功能：所有入口共用的加锁请求门面：更新时限、检查 id、缓存去重、分发 handle 并包装统一响应。同 id 同内容返回缓存响应快照，不重新执行。
    # 参数 m=dict(id='startup', type='new', mode='virtual', ai_side='b', fen=START)：内层请求字典，含id/type以及对应动作字段；详见《文件与流程详解》的请求参数表。
    # 返回：{ok,id,result,state} 或 {ok:false,error,state}；部分 id 校验错误不附 state。
    # [PY-app-067] dict｜来源：Python内置类型dict的构造调用
    # 功能：建立字典；关键字名成为字段名，**字典会把字段展开合并。
    # 参数 id='startup'：请求唯一编号；用于幂等去重
    # 参数 type='new'：动作类型；PLAN在响应里表示待执行计划
    # 参数 mode='virtual'：virtual虚拟或robot实机协议
    # 参数 ai_side='b'：AI阵营w红/b黑
    # 参数 fen=START：完整棋局FEN，含行棋方和计数
    # 返回：新字典。
    g.rpc(dict(id='startup', type='new', mode='virtual', ai_side='b', fen=START))
    if args.serial:
        # [PY-app-068] threading.Thread｜来源：Python标准库threading.Thread
        # 功能：创建后台线程对象；此构造调用还不会启动线程。
        # 参数 target=serial_worker：线程启动后执行的函数对象；这里不加括号调用它
        # 参数 args=(g, args.serial, args.baud)：传给target的位置参数元组，顺序需匹配target签名
        # 参数 daemon=True：True表示守护线程，主程序退出时不等待它继续运行
        # 返回：Thread对象。
        # [PY-app-069] threading.Thread(target=serial_worker, args=(g, args.serial, args.baud), daemon=True).start｜来源：Python标准库threading.Thread.start
        # 功能：启动已建立的线程，使target在后台执行。
        # 参数 本次没有显式参数；省略的可选参数使用默认值
        # 返回：None。
        threading.Thread(target=serial_worker, args=(g, args.serial, args.baud), daemon=True).start()
    # [PY-app-070] create_app｜来源：本项目 app.py，create_app
    # 功能：把同一个 Game 实例接入网页、状态/合法走法/RPC/导出接口，并配置令牌和跨站写入保护；不在这里启动监听。
    # 参数 game=g：共享的 Game 实例，网页和串口必须使用同一个实例。
    # 参数 token=token：API访问令牌，默认空字符串表示不启用令牌校验。
    # 返回：Flask 应用对象。
    # [PY-app-071] create_app(g, token).run｜来源：第三方Flask库：flask.Flask.run
    # 功能：启动当前应用的网页开发服务，阻塞主线程等待请求。
    # 参数 host=args.host：监听地址；127.0.0.1仅本机，0.0.0.0监听网卡
    # 参数 port=args.port：HTTP端口，与串口波特率无关
    # 参数 debug=False：False关闭调试模式
    # 参数 use_reloader=False：False防止重复启动引擎或串口线程
    # 参数 threaded=True：True允许并发HTTP线程，棋局仍由Game.lock保护
    # 返回：服务停止后返回。
    create_app(g, token).run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
if __name__ == '__main__':  # 仅直接运行此文件时执行入口逻辑。
    # [PY-app-072] main｜来源：本项目 app.py，main
    # 功能：解析启动参数，选择真实或演示引擎，创建全新的虚拟棋局，可选启动串口线程，最后启动单进程 Flask 服务。
    # 参数 本次没有显式参数；省略的可选参数使用默认值
    # 返回：无；命令行错误时退出，服务运行期间阻塞。
    main()  # 入口执行；函数的功能和参数请参阅紧邻的中文文档字符串。
