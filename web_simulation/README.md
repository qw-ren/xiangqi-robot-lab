# Web 与 ArUco 联调

从仓库根目录启动，当前只有这一份 Web 实现。旧的 `strategy_Jin/chch-robot` 已整合到根目录。

## Windows 最短启动

使用已有可用 Python；新环境安装 `python -m pip install -r tools/vision/requirements-windows.txt`。
将个人 ArUco 配置放到 `configs/local/board-local.json`，或在参数中传入实际路径。该文件不提交 Git。

```powershell
python run_local.py --vision-only --camera --camera-index 0 --aruco-config configs/local/board-local.json --board-orientation black-top --allow-approximate-geometry
```

打开 `http://127.0.0.1:5000/`。`black-top` 仅在确认图像上方为黑方底线后使用；反向用 `red-top`。
`--allow-approximate-geometry` 只用于 `test_only=true` 的个人临时配置，正式测量后去掉。
`--vision-only` 不要求 AI 引擎，并阻止开局、AI 和控制动作接口。
若只测试图片，可不加 `--camera`，在网页选择“本地图片”、上传带标记的照片并点击识别。

相机识别由 Web 进程独占，先退出独立 `aruco_live.py` 和 Windows 相机程序。
选择 USB 摄像头后开启动态识别；查看“画面输入”下的状态提示与观测棋盘。
若相机不在索引 0，可调整 `--camera-index` 或在网页重新选择。

## 结果含义

ArUco 原始观测与正式棋局分开：开局前可显示观测棋盘，对局中保持正式棋盘，质量状态持续提示。
`provisional` 表示临时标定、方向未定或帧数不足；`rejected` 表示定位 / 分类基础检查失败；
`review_required` 仍需确认。连续稳定和高分不能证明准确，目前不自动写入棋局或触发走子。

正式虚拟对弈仍使用原游戏 / 策略功能。不能将本次预览接入描述成实体视觉闭环已完成。
需要完整引擎功能时去掉 `--vision-only` 并传入 `--engine` / `--nnue`；默认控制后端为 mock。
ArUco 先在灰度图上检测标记，缺码时尝试局部对比度增强。分类仍使用原彩色图矫正后的结果。
Web 的 ArUco 定位失败时，会用同一帧运行 RTMPose 备用预测。低分保留为提示，备用预测仍可预览。网页会显示检测到的 ID、缺失 ID 或几何校验失败原因。
需要单独复测旧定位时，使用 `--vision-backend rtmpose`。

## HTTP 接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/vision/observation` | 上传图像或采集当前相机帧，返回 ArUco 观测预览 |
| `POST /api/recognize` | 保留原按钮地址；ArUco 模式返回相同预览包 |
| `POST /api/recognize/dynamic` | ArUco 模式逐帧预览；原定位模式仍走原动态逻辑 |
| `GET /api/status` | 正式游戏状态，区别于原始视觉观测 |

请求里的 `image` 为原图的 base64（不带 data-URL 前缀）；不传时从选择的相机取帧。
响应的 `observation.board_state` / `observed_board_state` 是离散预测，`board_state` 是正式游戏状态；
`preview_only=true` 时不把预测当成已确认棋局。完整字段和坐标见 [视觉接口](../docs/project/VISION_INTERFACE.md)。

## 代码位置

`app.py` 持有共享服务；`services.py` 管理识别器与相机；`routes/recognition.py` 处理识别响应；
`vision/aruco.py` 为唯一 ArUco 核心；`vision/observation.py` 生成质量及离散坐标；
`dofbot.py` 保留后端状态保护与视觉核对，`robot/dofbot_adapter.py` 现在仅做走棋串口输出和离线预演，移除了本机舵机/GPIO 访问。连接步骤及消息字段见 [串口走棋接口](../docs/project/SERIAL_MOVE_OUTPUT.md)。
