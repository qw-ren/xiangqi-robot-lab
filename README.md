# 中国象棋机器人 · 电子工艺实习

团队项目负责人：[@qw-ren](https://github.com/qw-ren)。用于整理代码、方案、实验记录和团队任务。

基于 [Maicarons/chch-robot（CH-RO）](https://github.com/Maicarons/chch-robot)，保留上游 GPL-3.0 许可证。当前代码基线来自官方归档 `b569198`；这是独立团队仓库，不是完整上游 Git 历史。来源说明见 [UPSTREAM.md](UPSTREAM.md)。

## 当前进展

- 已在树莓派 ARM64、Python 3.8.10 上跑通 CPU ONNX 四角检测、透视矫正和整盘分类。
- 完整样图人工核对 90/90 交点一致；预热后完整推理均值 1.586 s，角点缓存均值 1.452 s。仅代表这一张样图。
- USB 摄像头已能采集；现场棋盘缺边时原识别流程仍输出棋局，后续需增加拒识验证。
- 独立 ArUco 图片入口和合成测试已通过，尚未进行现场标定。测试配置不能直接当作现场几何关系。
- 当前团队验证范围为视觉。原仓库包含机械臂、固件和 AI 模块，本轮工具不调用这些入口。

## 从这里开始

| 内容 | 入口 |
|---|---|
| 视觉测试与复跑 | [tools/vision/README.md](tools/vision/README.md) |
| 方案与接口 | [docs/project/PLAN.md](docs/project/PLAN.md) |
| 任务分解与验收 | [docs/project/ROADMAP.md](docs/project/ROADMAP.md) |
| 团队合作 | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 实验记录 | [docs/experiments/2026-11-16-vision/README.md](docs/experiments/2026-11-16-vision/README.md) |
| 原项目说明 | [docs/upstream/README_zh.md](docs/upstream/README_zh.md) |

## 视觉环境

建议独立环境，避免覆盖树莓派已有包。已有可用环境可直接复用。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-vision.txt
python tools/vision/image_benchmark.py --image tests/beb8220b82ec467910d9846e771d82f8.jpg --ground-truth tools/vision/sample_manual_reference.txt --out artifacts/sample --repeats 5
python tools/vision/aruco_image_test.py --self-test --out artifacts/aruco
```

上述命令不会启动机械臂、电磁铁或 AI 引擎。摄像头采集另见工具说明。

## 目录

```text
vision/ core/ model/      原有视觉实现和 ONNX 模型
tools/vision/            独立图片、计时、摄像头及 ArUco 工具
configs/aruco/           明确标记 TEST_ONLY 的合成测试配置
docs/project/            方案、里程碑和验收约定
docs/experiments/        可以共享的实验摘要和样图证据
tests/                  原有测试与样图
robot/ firmware/ ai/     上游控制、固件和 AI 模块
.github/                CI、Issue/PR 模板及代码负责人
```

使用 Issue 记录任务、问题及验收结果，通过分支和 Pull Request 合并代码。成员 GitHub 用户名确认后再邀请协作者，不预设团队成员或分工。
