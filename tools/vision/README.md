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

## Windows 固定机位实时识别

在仓库根目录运行，`$py` 指向已有的带 GUI OpenCV、ArUco 和 ONNX Runtime 的 Python。不要在这个环境安装上面的 headless 依赖。个人标定放在已忽略的 `configs/local/`，照片和输出放在已忽略的 `artifacts/`，也可传入仓库外的路径。

新 Windows 环境（Python 3.12）使用 `python -m pip install -r tools/vision/requirements-windows.txt`；已有正常环境无需重装。该文件与根目录的 ARM64/headless 依赖二选一。

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$config = 'configs/local/board-local.json'
& $py tools/vision/aruco_live.py --camera 0 --config $config --out artifacts/snapshots --allow-approximate-geometry
# 离线回归：照片路径可指向 Camera Roll，不需要复制进仓库
& $py tools/vision/aruco_live.py --image artifacts/photos/WIN_20261117_14_48_21_Pro.jpg --config $config --out artifacts/replay/1448 --allow-approximate-geometry
& $py tools/vision/aruco_live.py --image artifacts/photos/WIN_20261117_14_52_27_Pro.jpg --config $config --out artifacts/replay/1452 --allow-approximate-geometry
```

现场临时配置是由 14:45 照片估计的 ID 0–3 映射（画面左上 3、右上 2、左下 1、右下 0），保持 `test_only=true`；它未确认几何，需要显式传 `--allow-approximate-geometry`，输出不会报告正式现场标定成功。测量确认后的正式配置使用 `geometry_confirmed=true, test_only=false`，去掉该参数。工具共用 `aruco_image_test.inspect_frame` 的几何校验，不调用 RTMPose。

离线命令可追加 `--reference artifacts/reference.txt`：参考文件为从照片左上起的 10 行×9 列棋局，空位用 `.`，红棋用大写、黑棋用小写；额外输出 `comparison.json`，逐格列出不一致位置和匹配数。程序退出码表示读取/定位是否成功，匹配数应查看 comparison，已知错分不会使程序异常退出。

启动定位成功后固定复用四角。`R` 下一帧重新定位，`S` 保存当前识别帧，`Q`/Esc 退出。移动相机、棋盘或标记后必须重新定位。关闭 Windows「相机」后试索引 1；打不开或是内置摄像头则试 0。命令行棋局仅在变化时输出。自动采集验证可追加 `--frames 30 --no-preview --save-every 10`，保存路径与 S 相同；这不替代 GUI 按键检查。

每次保存创建独立时间目录，含原始 `frame.png`、实际送入分类器的 `warped.png`、`layout.txt`、1 起始行列的 `cells.csv` 和 `result.json`。识别保持原型的 960 像素宽缩放再矫正方式。JSON 记录帧序号、取帧后时间、像素 SHA256、四角及定位帧、配置 SHA256 和 10×9 分数；缓存四角来自定位帧，原图、矫正图和分类结果来自当前同一帧。分数为模型输出，不解释成校准概率。

现场红炮排查：按照片左上起第 8 行第 5 列为目标，先保存现状；给两枚红炮临时记为 A/B（不遮字），只交换两炮位置，保持字面方向并保存；再逐枚放到目标格，分别按 0/90/180/270 度旋转，每次手离开后按 S，记录炮编号、位置、方向和预测。每种状态观察多帧并保存错误与正常样本。随炮编号变化提示字形影响，随位置变化提示成像或格点影响，随旋转变化提示朝向影响；尚不能据单张照片决定微调。

2026-11-17 联调已完成照片回归、摄像头采集和 S/R/Q 消息验证；后续人工实验在两个位置复现局部遮挡引起的 `C→B`，也保留了无遮挡错帧。详见[现场验证与遮挡实验记录](../../docs/experiments/2026-11-17-vision/README.md)。遮挡拒识和棋局更新保护仍是下一步工作，当前入口输出原始分类，未进行微调。个人配置、照片及全部样本继续保存在 Git 忽略目录。

## 与 Web 对接的观测输出

在原实时命令追加以下参数（确认画面上方确为黑方底线后才使用 `black-top`）：

```powershell
& $py tools/vision/aruco_live.py --camera 0 --config $config --out artifacts/snapshots --allow-approximate-geometry --board-orientation black-top --latest-json artifacts/vision/latest.json
```

每帧发布布局、90 格类别/分数、Web 的 `"col,row"` 字典、质量状态与时间戳。发布模式每帧校验定位；程序退出时文件标记失效，离线样本的观测仍保存在 `result.json.observation`。
不指定方向时字典为 `null`。当前临时配置会标记 `unconfirmed_geometry`，连续稳定也不能自动确认棋局。
`--stable-frames 3 --min-score 0.8` 为可调的基础检查参数，未验证为准确率保证；尚无遮挡检测或自动接受逻辑。
供 Web 同学阅读的坐标、字段、读取示例及后续控制边界见[视觉接口说明](../../docs/project/VISION_INTERFACE.md)。

## 改动后验证

```bash
python -m pytest tests/ -v
python tools/vision/aruco_image_test.py --self-test --out artifacts/aruco-check
```

不要把 `artifacts/`、个人设备配置或密钥加入版本控制。
