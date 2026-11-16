# 团队协作

成员：`qw-ren`、`bonglekirley`、`yoyo051124`。本地 VS Code 是开发环境，GitHub 管理分支和 PR；树莓派仅部署和设备验证，不在设备上直接开发。详见 `docs/project/LOCAL_DEVELOPMENT.md`。

## 开始一个任务

先建 Issue，写清目标、当前证据、改动范围和完成标准。任务由实际认领者填写负责人；不在无人确认时分派成员。

建议分支名：`feat/aruco-calibration`、`fix/partial-board-rejection`、`docs/field-calibration`。从 `main` 建分支，完成后提交 Pull Request，并在描述里用 `Closes #编号` 关联任务。

```bash
git switch main
git pull --ff-only
git switch -c feat/your-task
# 修改、验证
git add <本次文件>
git commit -m "feat(vision): describe the change"
git push -u origin feat/your-task
```

PR 写明问题、最终行为、验证命令和结果。涉及硬件时明确哪些设备被操作、当时的状态与停止条件。视觉任务使用独立工具，不顺带启动控制系统。

## 验证与实验记录

- 修改原项目行为前，按 AGENTS.md 运行完整测试；新增图片工具至少复跑其离线测试。
- 所有耗时注明模型加载是否计入、输入分辨率、线程数、执行后端、样本数和角点缓存是否命中。
- 识别准确率需有人工作真值；只有模型输出时不写成“正确率”。画面裁切时不能把标准开局直接当作现场真值。
- ArUco 标记检测成功不等于棋盘标定成功。正式标定需实测标记四角到棋盘交点的几何关系。
- 把可共享的实验摘要放进 `docs/experiments/日期-主题/`，列出代码版本、命令、输出和限制。

原始现场图、机器 IP、SSH 密钥、凭据、虚拟环境和大量日志保留在设备或本地 `artifacts/`，按需整理去标识后的证据。不要直接把整个桌面或树莓派家目录提交进仓库。

## 合并与讨论

代码走 PR，方案选择留在 Issue 或 `docs/project/`。尽量由另一位成员检查关键改动；实际合并权限以仓库设置为准，本文不声称已启用分支保护。

主分支应保持可复现。报告的问题应附最小复现图片或描述、版本、命令、期望与实际结果。团队成员加入后，可在 GitHub Settings → Collaborators 中由负责人授予所需权限。
