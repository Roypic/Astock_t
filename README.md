# AStock T Signal Monitor

个人 A 股做 T 信号监控工具。新版是可视化界面 exe。

功能：

- 通过模型 JSON 文件决定监控哪些股票。
- 默认带剑桥科技、东山精密两个模型。
- 模型文件夹里放几个 `.json` 模型，就监控几只股票。
- 使用 FTShare / `market.ft.tech` 分钟行情。
- 默认在北京时间 `09:30-15:00` 的盘中分钟数据里检查入场信号。
- 检测到信号后通过 PushPlus 推送到微信。
- 信号也会显示在 exe 界面表格和日志里。
- 不保存 PushPlus token；每次启动在界面输入。

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

界面里有两个核心输入：

```text
模型文件/文件夹：选择一个模型 JSON，或选择包含多个模型 JSON 的文件夹。
PushPlus token：填自己的 PushPlus token。
```

第一次运行时，程序会在 exe 同目录创建 `models` 文件夹，并放入默认模型：

```text
models\jianqiao_tech.json
models\dongshan_precision.json
```

你可以复制一个模型 JSON，改股票名、代码、篮子股和参数，然后放回 `models` 文件夹。下次在界面选择这个文件夹，就会一起监控。

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

## 模型文件格式

每个模型 JSON 对应一只目标股票：

```json
{
  "name": "剑桥科技",
  "code": "603083.XSHG",
  "basket": [
    {"name": "华工科技", "code": "000988.SZ"}
  ],
  "params": {
    "basket_threshold": 0.012,
    "market_threshold": 0.0,
    "relative_threshold": 0.0,
    "avg_threshold": 0.006,
    "take_profit": 0.024,
    "stop_loss": 0.012,
    "max_basket_dispersion": 0.04,
    "max_daily_signals": 1
  }
}
```

## 滚动优化

仓库包含每日滚动优化脚本：

```bash
cd backend
python -u rolling_optimize.py --models models --since "TRADE_DAYS_AGO(31)" --train-days 30 --write-models
```

工作流：

```text
昨日收盘往前 30 个交易日训练基准
加入今日收盘后再训练更新参数
交叉回放两段窗口
只有新参数通过稳定性门槛才写回模型 JSON
```

GitHub Actions 会在北京时间收盘后自动运行 `Rolling Optimize Models`。

## 免责声明

这是分钟级辅助提醒工具，不是自动交易程序，也不是投资建议。真实下单前请以券商行情、盘口、仓位和个人风控为准。
