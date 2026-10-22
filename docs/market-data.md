# 共享行情与 A 股日线

`tradingagents.dataflows.market_data.load_ohlcv(symbol, as_of_date)` 是股票行情工具、技术指标、数值核验快照和 Web 行情图的共同入口。请求先规范代码与来源，再获取日线，最后标准化为 `Date, Open, High, Low, Close, Volume`；东方财富另有 `Amount`。这是参考 OpenBB 的查询、获取、标准化分层思想实现的轻量入口，不依赖 OpenBB 或 AKShare 包。

## 默认来源与配置

`DEFAULT_CONFIG["data_vendors"]` 的 `core_stock_apis` 和 `technical_indicators` 均为 `auto`：

| 标的 | 来源 | 价格 | 成交量 |
|---|---|---|---|
| 沪深 A 股 | 东方财富日线，参考 AKShare `stock_zh_a_hist` 接口约定 | 人民币 CNY，不复权，`fqt=0` | 接口的手乘 100，输出股；Amount 为人民币元 |
| 其他标的 | Yahoo Finance | `auto_adjust=False` 的 Close；不使用 Adj Close 替代 | 供应商原生单位，股票为股；其他资产不能假定为股 |

支持 `600519`、`SH600519`、`600519.SH`、`600519.SS`，以及 `000001`、`SZ000001`、`000001.SZ` 等代码别名。本期东方财富适配器只覆盖沪深 A 股；北交所和与交易所不匹配的代码不会被静默映射到沪深。

如需明确指定 Yahoo 或东方财富，请让两个分类使用同一来源：

```python
config["data_vendors"].update(
    core_stock_apis="yfinance",
    technical_indicators="yfinance",
)
```

`tool_vendors.get_stock_data` / `tool_vendors.get_indicators` 的优先级仍高于分类配置。原有 Alpha Vantage 和显式供应商回退链仍可用于工具调用；共享图表/核验快照目前仅支持一致的单一 `yfinance` 或 `eastmoney` 来源。如果两项配置冲突或使用尚未适配的来源，图表和快照明确报告不可用，不另取 Yahoo 数据冒充一致结果。`auto` 按市场选择一次，失败时不跨供应商补数。证据记录标注实际的 `eastmoney` / `yfinance`。

## 日期、数值和缓存

- 日期使用供应商本地交易日；截至分析日（包含当日），剔除未来行，排序并去重。同日重复行取最后一条有收盘价的记录。
- 缺失或非有限的收盘价行不参与计算；其他缺失 OHLC/成交量保持缺失，不前填、后填或编造交易日。stockstats 在相同历史上计算指标，数值核验和指标工具采用同一口径。历史不足的指标仍受 stockstats 的窗口与预热规则影响，不能把短历史计算当成完整 200 日验证。
- 行情工具只输出请求窗口，图表显示最近 120 个自然日，指标和核验保留较长历史用于预热。东方财富请求分析日前五年；Yahoo 复用已有的最近五年至今缓存，因此太早的分析日期可能无覆盖。
- 最新有效交易日距分析日超过 10 个自然日，按无可用数据处理（长时间停牌也可能触发）。周末和短假期使用此前交易行，并在核验快照显示实际日期。
- 当前交易日日线可能尚未收盘。A 股不复权会出现除权除息缺口，不能与前/后复权指标混用，也不能用作含分红总回报。Yahoo 历史可能按拆股重述。两者均不保证历史时点原始版本。
- 常规缓存按来源、价格口径、代码和分析日隔离；当天获取的历史请求缓存可当日复用，当日行情 15 分钟刷新。空数据和失败不写入数据缓存。
- Web 每个研究目录的 `cache/market/` 固定首次成功获取的标准化 CSV。同一研究的行情、指标、核验、图表和恢复运行都读取这份快照；线程/进程共享文件锁，原子写入。需要更新当日行情时新建研究，不刷新旧研究的数值。旧研究若没有该快照，无法据此追溯证明原报告使用的历史版本。
- 数据框 `attrs` 与图表响应含真实来源、价格口径说明、币种和成交量单位；工具输出和核验快照也提供这些信息。旧报告正文不被重算或修改。

## 参考资料与验证

- [AKShare 股票历史日线文档](https://akshare.akfamily.xyz/data/stock/stock.html)：`stock_zh_a_hist` 的不复权参数和成交量单位约定；适配器独立实现公开接口，没有复制 AKShare 源码。
- [AKShare 东方财富历史接口实现](https://github.com/akfamily/akshare/blob/fe10d1decb705e5f43cc8ca3339ca39e580fb8f4/akshare/stock_feature/stock_hist_em.py)
- [OpenBB 统一历史行情模型](https://github.com/openbq-org/OpenBB/blob/78020ee4b864ab50b8c1f56373877c2b4e7fb623/openbb_platform/core/openbb_core/provider/standard_models/equity_historical.py)：参考分层与标准字段思想，没有引入框架或复制实现。

离线回归覆盖东方财富响应校验、沪深别名、单位换算、空数据/网络失败/大小上限、日期边界、缺失值、陈旧数据、同一研究只获取一次、快照重放、真实来源凭据，以及 Yahoo Close 与 Adj Close 差异。

```sh
pytest -q
python -m unittest discover -s webapp/tests -v
node --test webapp/tests/frontend*.test.*
ruff check .
```
