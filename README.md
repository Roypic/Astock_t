# AStock T Signal Monitor

个人 A 股做 T 信号监控工具。

功能：

- 监控剑桥科技、东山精密。
- 使用 FTShare / `market.ft.tech` 分钟行情。
- 只在北京时间 `09:45-10:30`、`14:00-14:30` 检查入场信号。
- 检测到信号后通过 PushPlus 推送到微信。
- 不保存 PushPlus token；每次启动可手动输入，也可通过环境变量传入。

## 下载 Windows exe

进入 GitHub 仓库的 **Actions** 页面，打开最新一次 `Build Windows EXE`，在 Artifacts 里下载：

```text
AShareTSignalMonitor-windows
```

解压后运行：

```text
AShareTSignalMonitor.exe
```

程序会提示输入 PushPlus token。输入后会常驻监控，有信号就推送到微信。

测试推送：

```bat
AShareTSignalMonitor.exe --test
```

自定义检查间隔：

```bat
AShareTSignalMonitor.exe --interval 30
```

## 本地 Windows 打包

如果要自己在 Windows 上打包：

```bat
cd backend
build_exe_windows.bat
```

生成文件：

```text
backend\dist\AShareTSignalMonitor.exe
```

## 免责声明

这是分钟级辅助提醒工具，不是自动交易程序，也不是投资建议。真实下单前请以券商行情、盘口、仓位和个人风控为准。
