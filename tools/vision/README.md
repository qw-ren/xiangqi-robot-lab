# 视觉独立工具

所有工具不导入机械臂控制或游戏入口。Python 3.8+；安装仓库根目录 `requirements-vision.txt`。OpenCV 必须带 `aruco`；不要在同一环境并装多种 OpenCV wheel。

默认从脚本所在目录定位项目根目录。若复用树莓派旧运行目录，可设 `CHRO_ROOT`；已有独立依赖目录可设 `CHRO_VISION_DEPS`，不需要硬编码设备路径或重新安装包。

## 样图推理

```bash
python tools/vision/image_benchmark.py --image tests/beb8220b82ec467910d9846e771d82f8.jpg --ground-truth tools/vision/sample_manual_reference.txt --out artifacts/sample --repeats 5
```

输出 `original_corners.png`、`warped.png`、`layout.txt`、`cells.csv`、`result.json`。首次调用、强制重检测角点和缓存调用分别计时；结果按每次日志确认缓存命中。计时不含图片 IO、RGB 转换、缩放或调试图绘制。

## USB 采集及缺边图复测

```bash
v4l2-ctl --list-devices
timeout 35 python tools/vision/camera_stream_test.py --device /dev/video0 --seconds 10 --out artifacts/camera
python tools/vision/image_benchmark.py --image artifacts/camera/cropped_scene.png --out artifacts/cropped --repeats 2
```

采集工具请求 MJPG 1600×1200 / 10 fps，记录实际尺寸、驱动 fps、应用读帧速率、预热失败和计时段失败。应用读帧速率不是独立曝光计数。`timeout` 用于限制潜在阻塞的驱动读帧；Windows 离线图片工具不需要该命令。

## ArUco 离线图片测试

```bash
python tools/vision/aruco_image_test.py --self-test --out artifacts/aruco
python tools/vision/aruco_image_test.py --image artifacts/aruco/synthetic_perspective.png --config configs/aruco/TEST_ONLY_config.json --out artifacts/aruco-image
```

合成测试含四种断言：完整几何成功（含旋转标记）、检测到标记但几何未确认、缺标记、错误角点顺序。输出 ID、像素角点、缺失项、几何误差及标定状态。

`configs/aruco/TEST_ONLY_config.json` 只对应合成图：DICT_4X4_50，ID 10–13。每个标记的 `marker_corners_board_xy` 按解码方向 TL/TR/BR/BL 记录，坐标以棋盘左上交点为原点、格距为单位。正式布局须现场测量，不得复用合成数值。仅在确认真实几何后使用正式配置的 `geometry_confirmed=true` 和 `test_only=false`。

只检测标记时可用 `TEST_ONLY_detection_only_config.json`；它不会因检测到四枚标记就报告棋盘标定成功。图像输出只进行共面透视矫正，不估计内参或畸变。

## 改动后验证

```bash
python -m pytest tests/ -v
python tools/vision/aruco_image_test.py --self-test --out artifacts/aruco-check
```

不要把 `artifacts/`、个人设备配置或密钥加入版本控制。
