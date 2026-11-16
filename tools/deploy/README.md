# 从本地 Git 提交部署

开发在本地 VS Code 进行。树莓派仅接收部署包和运行设备验证，不在设备上直接改源码。

先提交当前改动，打包指定 HEAD：

```powershell
.\tools\deploy\deploy.ps1 -PackageOnly
```

确认需要部署时：

```powershell
.\tools\deploy\deploy.ps1 -HostName 'dofbot@<设备地址>' -IdentityFile '<本地SSH私钥路径>'
```

将占位符换成自己的本机参数。脚本拒绝未提交的工作区，使用 `git archive HEAD` 只打包已跟踪的提交内容，排除 `.git`、本地依赖和运行产物。上传至 `/home/dofbot/xiangqi-robot-lab/releases/<完整提交SHA>/`，已有相同 release 时停止，不覆盖它。

脚本仅上传与解包，不修改原 `/home/dofbot/chch-robot`，不安装依赖、不切换当前运行版本、不启动 Web/AI/机械臂/电磁铁服务。本轮只验证本地打包，没有执行设备部署。

需要在已部署目录运行视觉测试时，可明确指定原有依赖目录：

```bash
CHRO_VISION_DEPS=<现有依赖目录> python3 <release目录>/tools/vision/aruco_image_test.py --self-test --out <设备结果目录>
```

运行版本由提交 SHA 和输出目录共同记录。任何修复回到本地提交，再部署新 release。
