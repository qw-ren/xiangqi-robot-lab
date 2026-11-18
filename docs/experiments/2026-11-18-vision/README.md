# 视觉观测接口验证（2026-11-18）

开发 / 推送分支：`vision`。本轮不接入 Web 原游戏状态更新或机械臂控制。

新增标准库模块 `tools/vision/observation.py`：生成每帧 90 格、Web 离散坐标字典、时间戳、定位元数据和质量状态。
实时入口新增 `--latest-json`、`--board-orientation`、`--stable-frames`、`--min-score`。
发布模式逐帧重定位，原缓存模式仍可不加发布参数使用。启动 / 重新定位 / 停止使快照失效；读者仍须检查时效，处理进程意外崩溃。
字段、坐标及交接说明见[视觉接口](../../project/VISION_INTERFACE.md)。

## 已执行验证

| 检查 | 结果 |
| --- | --- |
| 全量 `pytest tests/` | 84 passed（原有 76 + 新增 8） |
| ArUco self-test | 4 场景通过，最大角点误差约 0.0971 px，仅合成验证 |
| 14:48 照片回归 | 人工参考 90/90；布局、分数和矫正图像素哈希均与昨日记录一致 |
| 14:52 照片回归 | 人工参考 89/90；布局、分数和矫正图像素哈希均与昨日记录一致；原炮/相错格仍在 |
| 序列回放（非硬件） | 完整→全白缺码→完整：缺码帧没有布局 / 没有棋局字典，恢复后连续帧计数从 1 开始 |
| 生命周期 | 启动和停止为 rejected；离线保存样本保留对应帧 observation；原子替换后无临时残留 |
| 接口单测 | 两方向映射、未知方向、未知类别、低分空位、非有限值、稳定性重置、测试几何、高分持续误分类需确认 |

运行环境为本地忽略目录内的 CPython 3.12.8、原有 Windows OpenCV 4.10.0 / ONNX Runtime 1.20.1，另装纯 Python 测试依赖。
原用户 Python 安装路径在当前执行账户不可访问，因此使用便携解释器；没有更换 ONNX 模型或个人标定。
证据位于本地忽略目录 `artifacts/vision-interface/`：`verification.json`、`stream-verification.json`、两张照片对应的样本目录及 ArUco 合成输出。
本轮没有重新打开现场摄像头，序列回放不能代替现场帧率或精度验收。

常规环境复跑：

```bash
python -m pytest tests/ -q
python tools/vision/aruco_image_test.py --self-test --out artifacts/vision-interface/aruco
```

## 剩余工作

当前个人几何仍是未正式测量的 test_only 配置，发布状态包含 `unconfirmed_geometry`。
最低分数 0.8 / 连续 3 帧只用于基础检查，尚未按独立标注集选择阈值。
`can_update_game` 始终为 false；手部遮挡检测、合法变化检查和自动接受尚未实现。
后续先完善现场标注和拒识验收，再与 Web 同学接入原始观测展示及显式确认；控制精确坐标另行测量和标定。
