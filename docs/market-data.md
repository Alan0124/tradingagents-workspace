# 共享行情与 A 股日线

`tradingagents.dataflows.market_data.load_ohlcv(symbol, as_of_date)` 是股票行情工具、技术指标、数值核验快照和 Web 行情图的共同入口。请求先规范代码与来源，再获取日线，最后标准化为 `Date, Open, High, Low, Close, Volume`；东方财富另有 `Amount`。这是参考 OpenBB 的查询、获取、标准化分层思想实现的轻量入口，不依赖 OpenBB 或 AKShare 包。

## 默认来源与配置

`DEFAULT_CONFIG["data_vendors"]` 的 `core_stock_apis` 和 `technical_indicators` 均为 `auto`：

| 标的 | 来源 | 价格 | 成交量 |
|---|---|---|---|
| 沪深 A 股 | 东方财富 → 腾讯 → 新浪 → Yahoo，首个通过校验的完整数据集 | 原生接口为 CNY 不复权；Yahoo 使用其 Close 口径并单独标识 | 输出股，按实际接口/板块转换；不拼接不同来源 |
| 其他标的 | Yahoo Finance | `auto_adjust=False` 的 Close；不使用 Adj Close 替代 | 供应商原生单位，股票为股；其他资产不能假定为股 |

支持 `600519`、`SH600519`、`600519.SH`、`600519.SS`，以及 `000001`、`SZ000001`、`000001.SZ` 等代码别名。本期东方财富适配器只覆盖沪深 A 股；北交所和与交易所不匹配的代码不会被静默映射到沪深。

如需锁定来源，可使用 `eastmoney`、`tencent`、`sina` 或 `yfinance`，让两个分类使用同一来源（锁定后不会自动跨来源回退）：

```python
config["data_vendors"].update(
    core_stock_apis="yfinance",
    technical_indicators="yfinance",
)
```

`tool_vendors.get_stock_data` / `tool_vendors.get_indicators` 的优先级仍高于分类配置。原有 Alpha Vantage 和显式供应商回退链仍可用于工具调用；共享图表/核验快照支持一致的 `auto` 策略或上述四种单一来源。如果两项配置冲突或使用尚未适配的来源，图表和快照明确报告不可用，不另取 Yahoo 数据冒充一致结果。`auto` 在沪深 A 股请求失败、没有数据或数据陈旧时，依次尝试东方财富、腾讯、新浪、Yahoo。每次尝试获取和校验整份历史数据，成功后固定来源；不跨来源补行、补列。证据记录标注最终实际来源，不将 `auto` 或 AKShare 包名当成供应商。

## 日期、数值和缓存

- 日期使用供应商本地交易日；截至分析日（包含当日），剔除未来行，排序并去重。同日重复行取最后一条有收盘价的记录。
- 缺失或非有限的收盘价行不参与计算；其他缺失 OHLC/成交量保持缺失，不前填、后填或编造交易日。stockstats 在相同历史上计算指标，数值核验和指标工具采用同一口径。历史不足的指标仍受 stockstats 的窗口与预热规则影响，不能把短历史计算当成完整 200 日验证。
- 行情工具只输出请求窗口，图表显示最近 120 个自然日，指标和核验保留较长历史用于预热。同一研究先按研究日期获取一份历史，工具即使查询更早的截止日，也只从该数据集截取。东方财富和腾讯覆盖分析日前五年；腾讯最多六个自然年请求，每年低于 640 根上限。新浪返回最近最多 2000 根再筛选日期；Yahoo 复用已有的最近五年至今缓存，过早日期可能无覆盖。
- 最新有效交易日距分析日超过 10 个自然日，按无可用数据处理（长时间停牌也可能触发）。周末和短假期使用此前交易行，并在核验快照显示实际日期。
- 当前交易日日线可能尚未收盘。A 股不复权会出现除权除息缺口，不能与前/后复权指标混用，也不能用作含分红总回报。Yahoo 历史可能按拆股重述。两者均不保证历史时点原始版本。
- 常规缓存按来源、价格口径、代码和分析日隔离；当天获取的历史请求缓存可当日复用，当日行情 15 分钟刷新。空数据和失败不写入数据缓存。
- Web 每个研究目录的 `cache/market/` 保存首次成功获取的标准化数据。`*-auto-日期-v2.json` 在同一个原子文件中保存最终来源、复权口径、失败尝试状态、CSV 内容和 SHA-256。文件锁涵盖完整选择过程；并发消费者只获取一次。已有的单来源 v1 CSV 会优先重放，旧快照不会因供应商恢复而换源；有多个旧来源快照时拒绝猜测。校验值或来源身份不匹配时报告不可用，不悄悄重抓覆盖。同一研究的行情、指标、核验、图表和恢复运行都读取这份快照；线程/进程共享文件锁，原子写入。需要更新当日行情时新建研究，不刷新旧研究的数值。旧研究若没有该快照，无法据此追溯证明原报告使用的历史版本。
- 数据框 `attrs`、图表响应、行情工具、指标工具及核验快照包含同一份数据的 SHA-256。图表和证据收据使用最终来源，回退到 Yahoo 时显示 Yahoo 的价格口径。旧报告正文不被重算或修改；旧研究如果此前未获取行情，后续成功加载行情不会自动修补旧报告，需发起新研究让模型依据恢复后的数据重新分析。

## 腾讯与新浪适配细节

- 参考 AKShare `stock_zh_a_hist_tx` 的不复权协议，独立接入腾讯 `web.ifzq.gtimg.cn/.../fqkline/get` 日线接口，按自然年分段。只接受 `day`，拒绝用 `qfqday` / `hfqday` 代替。响应超出所请求日期段时拒绝使用。
- 2026-10-22 实测腾讯 SH688/689 成交量原生为股，其余沪深股票为手；SZ000001 是股票，不能按上证指数处理。腾讯按手报告的值可能有舍入误差，例如同日 600522 与新浪存在 35 股差异，因此不得从另一来源补成交量。
- 新浪使用公开 `CN_MarketData.getKLineData` 的 `scale=240` 日线 JSON 接口，成交量原生为股。无需 AKShare 的新浪压缩历史解码器及 JavaScript 运行时。
- 两种新增适配器均校验 OHLCV 字段、数值、日期、价格区间和成交量；请求超时为连接 4 秒/读取 10 秒，每个响应上限 2 MB。没有执行远程 JavaScript、关闭 TLS 验证或修改系统代理。

## 参考资料与验证

- [AKShare 股票历史日线文档](https://akshare.akfamily.xyz/data/stock/stock.html)：`stock_zh_a_hist` 的不复权参数和成交量单位约定；适配器独立实现公开接口，没有复制 AKShare 源码。
- [AKShare 腾讯历史接口实现](https://github.com/akfamily/akshare/blob/fe10d1decb705e5f43cc8ca3339ca39e580fb8f4/akshare/stock_feature/stock_hist_tx.py)
- [AKShare 东方财富历史接口实现](https://github.com/akfamily/akshare/blob/fe10d1decb705e5f43cc8ca3339ca39e580fb8f4/akshare/stock_feature/stock_hist_em.py)
- [OpenBB 统一历史行情模型](https://github.com/openbq-org/OpenBB/blob/78020ee4b864ab50b8c1f56373877c2b4e7fb623/openbb_platform/core/openbb_core/provider/standard_models/equity_historical.py)：参考分层与标准字段思想，没有引入框架或复制实现。

离线回归覆盖东方财富响应校验、沪深别名、单位换算、空数据/网络失败/大小上限、日期边界、缺失值、陈旧数据、同一研究只获取一次、快照重放、真实来源凭据，以及 Yahoo Close 与 Adj Close 差异。

```sh
pytest -q
python -m unittest discover -s webapp/tests -v
node --test webapp/tests/frontend*.test.*
ruff check .
```
