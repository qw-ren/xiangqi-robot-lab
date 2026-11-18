# Web / ArUco 与项目目录整合验证

## 输入状态

本地 `main` 为 `edf7fe2`（合并队友 `strategy_Jin`）；拉取为快进，工作区干净。
视觉分支为 `ad3e112`，包含此前 `4559b8a` 的观测接口；当时尚未进入 `main`。
本轮在 `vision` 合入 `main`，不直接在 `main` 开发。

## 改动

去掉成员目录下的完整重复工程，将队友新增 Web / DOFBOT / 引擎功能整合到共用模块；
独立策略服务与权重分别放到 `xiangqi_strategy/`、`engine_assets/`。
共用 ArUco 核心移至 `vision/aruco.py`，观测协议移至 `vision/observation.py`。
Web 默认 ArUco；旧 RTMPose 显式选择，不在缺码时回退。
上传图片与动态接口返回原始观测，前端显示质量，不更新正式棋局或产生走子事件。
修复队友每 2 秒同步正式状态会覆盖预览的冲突；实时预览 5 秒未刷新则提示过期，该值为联调参数。

`run_local.py --vision-only` 可以不启动 AI，引擎 / 开局 / 控制写入接口在此模式不可调用。
目录与路径对应表见 [STRUCTURE.md](../../project/STRUCTURE.md)，运行步骤见 [Web 说明](../../../web_simulation/README.md)。

## 验证结果

- 原 84 项 Python 测试与 6 项 Web / ArUco 新测试，共 90 项通过。
- 4 项 Node 前端测试通过：周期状态同步保留空闲预览、对局保留正式棋盘、拒绝帧清除预测、实时观测过期。
- ArUco 合成测试 4 场景通过，最大误差约 0.0971 px，非现场精度证明。
- 使用真实 ONNX 和真实照片，通过 Flask 请求处理器调用 `POST /api/vision/observation`：14:48 为 90/90，14:52 为 89/90。
- 两张照片的布局、逐格分数与此前独立工具记录一致；正式游戏状态逐字段不变。
- 全白缺码图经原 `/api/recognize` 返回 rejected；既不复用上帧观测，也不调用旧定位。
- 重命名后的 Web / 部署 / 监督器入口 `--help` 可运行；队友规则层 7 项离线自测通过。
- 使用 `run_local.py --vision-only` 实际启动本机 HTTP 服务：页面与 JS 返回正常，真实照片上传走通 ArUco，再通过认证停止接口正常退出；没有打开相机或连接设备。

一个原有模拟测试使用了“红方行棋的初始 FEN，却让黑车走棋”的不一致输入；
保留队友新增的规则校验，将测试输入改为红方合法走一步后的真实局面，再检查模拟不发送 STM32 指令。

本地证据：`artifacts/vision-interface/web-verification.json`、`server-verification.json`、`web-1448.json`、`web-1452.json` 和 `refactor-aruco/`。
没有现场摄像头新采样、真实引擎搜索、硬件运动或树莓派部署结论。
个人 ArUco 几何仍是 test_only；遮挡拒识和有效棋局自动接受仍待独立数据验证。
