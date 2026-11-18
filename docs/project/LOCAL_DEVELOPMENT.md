# 本地开发与部署约定

## 成员

- `qw-ren`：仓库负责人。
- `bonglekirley`、`Shengcheng-Jin`：协作者，邀请授予代码读写权限，具体任务由成员认领。

邀请发出后需要各自接受。文档列出成员不代表邀请已经被接受。

## 本地 VS Code

每位成员克隆自己的工作副本，用 VS Code 打开仓库根目录，不打开包含本机凭据和实验归档的外层大文件夹。

```powershell
git clone https://github.com/qw-ren/xiangqi-robot-lab.git
cd xiangqi-robot-lab
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
code .
```

在 VS Code 中选择 `.venv` 解释器。仓库提供 Python/PR 扩展建议及测试发现配置。整套应用和测试推荐 Python 3.10；独立视觉核心曾在树莓派 Python 3.8 上通过，不代表整个应用都兼容 3.8。

## Git 合作

1. 从最新 `main` 新建任务分支，避免直接在主分支协同修改。
2. 本地开发、运行测试、提交代码并推送分支。
3. 发起 PR，关联 Issue，填写实际验证结果。
4. 合并后由负责人或指定成员部署对应的 Git 提交。

原始实验数据保留在本地 `artifacts/` 或设备上，只整理必要、可共享的结果入库。

视觉同学继续使用 `vision` 分支；`main` 更新后先拉取，再切回 `vision` 执行 `git merge main`。
所有成员在同一功能目录中改代码，不把完整项目再复制进以自己名字命名的子目录。
具体步骤与本轮迁移见 [STRUCTURE.md](STRUCTURE.md)。Windows 视觉预览需 GUI 环境，使用 `tools/vision/requirements-windows.txt`，不要与 headless OpenCV 混装。

## 树莓派仅用于部署

树莓派上的源码是部署产物，不作为开发工作副本。需要修复时回到本地修改并提交，重新打包部署；避免出现仅存在于设备上的改动。

部署工具见 `tools/deploy/README.md`。它打包已提交的代码，按提交 SHA 存为独立 release，不覆盖此前树莓派实验目录，不自动安装包、不切换运行版本、不启动控制服务。

CPU 环境应在设备上单独准备或复用。运行时通过 `CHRO_ROOT` 和 `CHRO_VISION_DEPS` 指向实际部署源码和依赖目录；设备 IP、SSH 私钥和路径放在本机配置中，不写入共享源码。
