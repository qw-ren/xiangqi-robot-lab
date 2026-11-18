"""
视觉识别模块 - 棋盘检测和棋子识别

提供:
- CameraManager: 摄像头管理
- ChessboardDetector: ONNX检测器
- BoardMapper: 坐标映射
- StableBoardBuffer: 多帧稳定化
- BoardRecognizer: 识别器主类
"""

from importlib import import_module

_EXPORTS = {
    "CameraManager": ".camera", "ChessboardDetector": ".detector",
    "BoardMapper": ".mapper", "StableBoardBuffer": ".stabilizer",
    "BoardRecognizer": ".recognizer",
}


def __getattr__(name):
    """Allow JSON-only consumers to import vision without camera dependencies."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value

__all__ = [
    'CameraManager',
    'ChessboardDetector', 
    'BoardMapper',
    'StableBoardBuffer',
    'BoardRecognizer'
]
