# 中国象棋机器人 · 电子工艺实习

团队使用同一份源码：本地 VS Code 开发、Git 分支 / PR 协作，树莓派仅部署与验证。
视觉开发先推送 `vision`，经 PR 合入 `main`。不要把整个仓库复制到以成员或分支命名的子文件夹。

基于 [CH-RO](https://github.com/Maicarons/chch-robot)，保留 GPL-3.0，来源见 [UPSTREAM.md](UPSTREAM.md)。

## 从这里开始

| 任务 | 入口 |
| --- | --- |
| Web 使用 ArUco 预览 | [web_simulation/README.md](web_simulation/README.md) |
| 独立视觉工具 | [tools/vision/README.md](tools/vision/README.md) |
| 树莓派机械臂示教工具 | [robot/arm_teaching/README.md](robot/arm_teaching/README.md) |
| 视觉 / Web 接口 | [docs/project/VISION_INTERFACE.md](docs/project/VISION_INTERFACE.md) |
| 目录与迁移清单 | [docs/project/STRUCTURE.md](docs/project/STRUCTURE.md) |
| Git 协作与部署 | [docs/project/LOCAL_DEVELOPMENT.md](docs/project/LOCAL_DEVELOPMENT.md) |
| 本次整合验证 | [docs/experiments/2026-11-18-web-integration/README.md](docs/experiments/2026-11-18-web-integration/README.md) |

## 当前状态

Web 和独立工具共用 ArUco 几何校验、透视矫正和整盘 ONNX 分类；Web 默认不加载 RTMPose。
原 RTMPose 可显式选择。现场照片回归为 90/90、89/90，不能代表所有实际场景。
个人标定仍是 `test_only` 临时几何，尚需实测确认。局部遮挡可导致高分炮 / 相混淆。

ArUco 输出供 Web 预览，附未知格、低分和定位质量；当前不自动确认有效棋局或触发控制。
队友的虚拟对弈、策略服务和 DOFBOT 代码已整合保留，实际引擎 / 硬件部署另行验证。

## 目录

```text
run_local.py             统一 Web 启动入口
vision/                  视觉业务：ArUco、观测、相机、原识别器
core/ model/             底层 ONNX / 几何算法和单份模型
web_simulation/          Flask、网页和 Web 与后端的衔接
xiangqi_strategy/        队友的独立策略 / 串口服务（独立棋局）
ai/ engine_assets/       引擎适配、公共权重及许可证
robot/                   TCP/串口控制适配；arm_teaching/ 是独立的树莓派本地示教工具
firmware/                固件
xq_utils/                棋盘、FEN、坐标、规则校验
tools/vision/            独立视觉命令
tools/web/               Web 运行辅助工具
tools/deploy/            打包、环境检查和部署辅助
configs/                 共享配置示例；local/ 为忽略的个人配置
tests/ docs/             自动测试、方案、接口及实验记录
artifacts/ data/          忽略的本机样本、运行状态和日志
```

Windows 视觉与 Web 环境安装 `tools/vision/requirements-windows.txt`；离线 CI 使用 `requirements-dev.txt`。
OpenCV 只安装一种带 ArUco 的 wheel，不要混装 GUI / headless / 非 contrib 版本。
本轮未执行机械臂动作或部署树莓派。
