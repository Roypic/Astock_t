# A 股做 T 信号与微信推送方案总结

## 目标

这套系统用于个人盘中辅助提醒，不做自动交易。后台程序在 A 股交易时段自动拉取分钟行情，按做 T 策略判断剑桥科技和东山精密是否出现入场机会；如果出现信号，就把入场价、目标价、止损价和盘面因子通过 PushPlus 推送到微信。

## 数据来源

数据来自 FTShare-market-data 技能对应的 `market.ft.tech` 接口。

当前使用的数据包括：

- 个股分钟级分时：目标股和相似股篮子。
- 指数分钟级分时：上证、深成、创业板。
- 个股日 K：MA5、MA10、MA20，仅作为推送里的参考信息，当前老版本策略不把 MA 当硬触发条件。

主要接口形态：

```text
GET https://market.ft.tech/app/api/v2/stocks/{code}/prices?since=TODAY
GET https://market.ft.tech/app/api/v2/indices/{code}/prices?since=TODAY
GET https://market.ft.tech/app/api/v2/stocks/{code}/ohlcs?span=DAY1&limit=40
```

当前目标股：

```text
剑桥科技 603083.XSHG
东山精密 002384.SZ
```

相似股篮子用于判断板块共振。当前篮子在 `wx_t_signal/backend/strategy.py` 的 `BASKETS` 中维护。

## 策略逻辑

当前运行的是老版本触发逻辑，核心因子是：

- 相似股篮子涨幅：确认参考股整体在走强。
- 大盘强弱：上证、深成、创业板加权后过滤弱盘。
- 个股相对强弱：目标股不能明显弱于参考篮子。
- 分时均价线：当前价需要站上均价线一定幅度。
- 篮子分化：参考股内部分化过大时不追。

当前只做正 T 入场信号：

```text
BUY_T：建议买入 T 仓
```

收到信号后，推送会给出：

- 入场价
- 目标卖出价
- 止损价
- 个股涨幅
- 大盘强弱
- 篮子涨幅
- 篮子分化
- 相对强弱
- MA5/MA10/MA20 参考值

## A 股 T+1 约束

策略按 A 股 T+1 的实际限制设计：当天买入的 T 仓当天不能再卖，所以不能只看瞬时套利空间。

回测和策略设计里重点考虑：

- 如果入场后继续下跌，当天无法用同一笔买入仓位退出。
- 微弱套利意义不大，因此目标收益倾向设在 1.5% 以上。
- 如果入场后没有达到目标价或止损价，需要把尾盘残余浮动损益计入评估。
- 每只股每天最多触发 1 次，避免频繁交易和重复提醒。

## 入场提醒时间

用户在瑞士，但策略时间必须按中国 A 股时间计算。代码中使用北京时间 `UTC+8`，不依赖本地机器时区。

当前入场提醒窗口：

```text
北京时间 09:45-10:30
北京时间 14:00-14:30
```

中午和其他时间不推新入场信号。

## 回测方法

回测使用近 30 个交易日的分钟级历史走势和日内因子。

基本过程：

1. 拉取目标股、相似股篮子、上证/深成/创业板的分钟行情。
2. 对每个交易日，只在指定入场窗口内扫描候选入场点。
3. 对每个候选入场点计算当时的盘面因子：
   - 目标股相对当日开盘涨幅
   - 参考篮子平均涨幅
   - 参考篮子分化程度
   - 大盘加权涨幅
   - 目标股相对篮子强弱
   - 目标股是否站上分时均价线
4. 用参数网格搜索不同阈值组合：
   - `basket_threshold`
   - `market_threshold`
   - `relative_threshold`
   - `avg_threshold`
   - `take_profit`
   - `stop_loss`
   - `max_basket_dispersion`
5. 入场后模拟后续分钟走势：
   - 先到目标价，记为目标止盈。
   - 先到止损价，记为止损。
   - 两者都没到，用尾盘价格计算残余收益/风险。
6. 每只股票每天最多保留一次信号。
7. 多目标评估，不只看胜率：
   - 交易次数不能太少。
   - 胜率越高越好。
   - 平均 T 收益越高越好。
   - 最大回撤越小越好。
   - 负残余风险越小越好。
   - 单次坏尾部越少越好。

近 30 日旧版参数参考结果：

```text
剑桥科技：交易 12 次，胜率约 58.3%，平均 T 收益约 +0.70%，累计约 +8.38%，最大回撤约 -2.38%，负残余约 -0.17%。
东山精密：交易 7 次，胜率约 57.1%，平均 T 收益约 +0.63%，累计约 +4.44%，最大回撤约 -1.08%，负残余约 -0.47%。
```

这些结果只是策略研发参考，不代表未来收益。继续优化时要优先看 T+1 残余风险、目标收益、交易频率和极端坏结果，而不是单纯追求胜率。

## 微信推送链路

当前不用企业微信，使用 PushPlus。

链路：

```text
FTShare 行情接口
    -> Python worker
    -> SignalEngine 判断做 T 信号
    -> PushPlus API
    -> 微信收到提醒
    -> 用户手动决定是否下单
```

PushPlus 接口：

```text
POST https://www.pushplus.plus/send
```

请求体包括：

```json
{
  "token": "从环境变量 PUSHPLUS_TOKEN 读取",
  "title": "做T信号：股票名",
  "content": "信号正文",
  "template": "txt",
  "channel": "wechat"
}
```

token 不写进代码，启动后台时通过环境变量传入。

## 后台运行

后台 worker 默认每 30 秒检查一次：

```bash
cd wx_t_signal/backend
export PUSHPLUS_TOKEN='你的 PushPlus token'
python worker.py
```

当前常驻运行时使用：

```bash
cd wx_t_signal/backend
setsid bash -c "exec env PUSHPLUS_TOKEN='你的 PushPlus token' SIGNAL_CHECK_INTERVAL=30 python -u worker.py >> logs/worker.log 2>&1" < /dev/null &
echo $! > worker.pid
```

检查状态：

```bash
cd wx_t_signal/backend
ps -p $(cat worker.pid) -o pid,etime,cmd
tail -30 logs/worker.log
```

## 重要提醒

这套系统是分钟级辅助提醒，不是低延迟交易系统，也不是投资建议。真实下单前仍要看券商行情、盘口流动性、仓位、持仓成本和个人风险承受能力。
