# A 股板块与大盘因子算法报告

> 目标读者：后续接手优化本模型的其他模型/工程师。本文档描述 v14 起纳入的行业板块（BK）与大盘指数（沪深300）因子的完整计算链路、因果性保证、已知弱点与可调参数。所有实现位于 `scripts/providers/eastmoney.py`、`scripts/monitoring/ml.py`、`scripts/finance.py`。

## 1. 设计目标

A 股个股走势与行业板块轮动、大盘贝塔高度相关。v13 及之前模型只看到个股自身 K 线、个股资金流与盘口，完全没有市场结构信息。v14 引入两层市场参照：

1. **训练特征**（日线模型，离线因果序列）：行业板块与大盘的历史收益/乖离进入回归特征；
2. **实时语境**（全模型共享的 live_context）：板块实时涨跌热度作为一个有界加项。

## 2. 数据源与获取协议

| 数据 | 端点（均免 key） | 字段 | 缓存 |
|---|---|---|---|
| 个股行业归属 | `push2delay/api/qt/stock/get?secid={1.SH\|0.SZ}{code}&fields=f127` | f127 = 行业名（如"电力"） | **7 天** |
| 行业板块目录 + 实时热度 | `push2delay/api/qt/clist/get?...fs=m:90+t:2&fid=f3` | f12=板块代码(BKxxxx)、f14=名称、f3=当日涨跌%、f62=主力净流入 | 5 分钟 |
| 板块指数日 K | `push2his/api/qt/stock/kline/get?secid=90.{BKcode}&klt=101&fqt=1&lmt=1200` | 收盘价序列 | **30 分钟** |
| 大盘指数日 K | 同上，`secid=1.000300`（沪深300） | 同上 | 30 分钟 |

### 2.1 传输与限流（重要，曾两次踩坑）

- `push2delay`（实时/列表）接受普通 urllib；`push2his`（历史 K 线）会**在 TLS 层断开 Python 客户端**，必须走 `scripts/http_client.py` 的 curl 回退传输（`RemoteDisconnected` 已被捕获并触发回退）。
- 东财按 IP 限流，**突发密集测试会触发小时级封锁**（表现为两种传输都"Empty reply"）。对策：
  - 全部请求有缓存层（见上表），行业分类七天内不重复请求；
  - kline 结果独立缓存 30 分钟（`kline:{secid}:{lmt}`），板块上下文与训练路径共享同一份缓存，不重复请求；
  - 失败静默降级：板块特征置零、响应附 warning，模型主体（个股 K 线 + 资金流）不受影响；
  - 后台训练器（`tools/background_train.py`，30 分钟一 Timer）顺序预热缓存；A 股盘前和交易时段只刷新轻量当日资金流，延后历史请求和训练。
- **经验阈值**：push2his 对单 IP 的高频窗口约在"连续 10+ 次无间隔请求"后触发；生产频率（前端 10s 一次分析，但上下文缓存 10 分钟）远低于此。

## 3. 特征定义（全部因果）

给定个股 bar 日期序列 `dates`，板块日 K 序列 `B = [(date, close)]`，大盘日 K 序列 `I = [(date, close)]`，逐 bar 计算参考特征 `ref_by_index[i]`：

对每个 bar 日期 d，取 d 之前（含 d）最近的板块行 `B_≤d` 与大盘行 `I_≤d`（双独立指针，两序列互不干扰）：

| 特征名 | 公式 | 含义 |
|---|---|---|
| `index_return_1` | 沪深300当日收益 | 大盘当日方向 |
| `index_momentum_20` | `I_≤d.close / I_(d-20).close − 1` | 大盘 20 日动量 |
| `sector_return_1` | `B_≤d.close / B_prev.close − 1` | 板块当日收益 |
| `sector_momentum_20` | `B_≤d.close / B_(d-20).close − 1` | 板块 20 日动量 |
| `stock_vs_sector_1` | `stock.return_1 − sector_return_1` | **个股相对板块的当日强弱**，区分“板块带飞”与独立行情 |

因果性保证：日期指针只前进；d 之前无数据的 bar 特征置 0。`ref_series_by_index` 的双指针实现中任一序列缺失（板块 K 线被限流）时，仅对应特征置零，另一序列继续生效。

### 3.1 训练特征 vs 实时特征的边界

- 板块/大盘**历史序列只作为日线（interval=1d）模型的特征**（与资金流特征同一约束：日线 bar 收盘时板块日线才完整，避免日内未来函数）；
- 日内（5m）模型通过 live_context 的**实时板块热度**获得板块信息（见 §4）。

## 4. 实时板块热度（live_context 加项）

`_live_context_return` 的 present-only 加权混合中新增一项：

```
sector_signal = clamp( board_change_pct_now / 2 + board_ma20_gap × 2 ,  -1, 1 )
weight = 0.06（与 情绪0.60 / 异动0.18 / 资金流0.10 / 盘口0.06 按可用性归一）
```

- `board_change_pct_now` 来自 clist 的板块实时涨跌（分钟级）；
- `board_ma20_gap` 来自板块日 K（收盘口径，日内不变）；
- 权重刻意最低（0.06）：板块热度是慢变量，只允许微调方向置信度。

## 5. v14 模型全景（供对照优化）

```
预测目标: log(close[t+h] / close[t])          h = 各产品画像(cn/hk=1, us/etf=3, ...)
特征 33 维: 个股K线26维 + 资金流2维 + 板块/大盘5维
组件: ridge(岭回归, 标准化截断±6σ) + analogue(kNN, 池240) + trend(动量先验) + reversion(均值回归先验)
组件权重: prior × f(方向可靠度EMA) × f(相对基线误差) / 误差, 截断[0.04, 0.76]后归一
分量输出: 按训练窗 95 分位 × 1.6 的因果上限裁剪
振幅校准: 非负加权中位数(实际/预测比) × 样本可靠度 n/(n+18), 未成熟时 ≤0.03
regime 冲突: 只压幅(50%→8%), 不改方向
在线更新: SGD rate=0.004/√n, 梯度截断±0.08, 权重界±max(0.025, cap×2.5)
验证: 时间顺序 60/40, 验证窗 ≤ 最新240样本, 3窗口稳定性诊断
发布闸门: skill>0 且 方向≥50% 且 相位≥0, 否则置信度封顶34 + 路径幅压35% + 禁预测类告警
置信度: 100×样本因子×(0.55×方向EMA + 0.45×clamp(skill/5%)), 上限85
```

## 6. 已知弱点（按影响排序）

1. **东财限流**：板块 K 线依赖 push2his，IP 封锁期间特征退化为 0（模型自动降级为 v13 行为）。可考虑：多镜像主机轮换、与资金流共用请求批次、或把板块 K 线落库为长缓存（历史部分不变化，只需增量更新当日）。
2. **行业分类静态**：f127 是东财"行业版块"口径（申万一级近似），成分调整不反映；概念板块（t:3）未接入——题材驱动的个股（概念联动 > 行业联动）当前覆盖不到。
3. **线性进入**：板块特征以线性方式进岭回归；板块与个股的**条件相关**（板块涨时个股贝塔放大）未建模。可尝试：相对强弱特征的非线性变换、或按板块涨跌分域的独立子模型。
4. **板块口径单一**：只用了行业板块，未用量价更敏感的"概念热度"或"北向资金行业分布"。
5. **样本历史深度**：当前板块 K 线拉取 1200 日，覆盖约五个交易年；更长个股历史的早期参考特征仍会置 0。

## 7. 调参旋钮速查（代码位置）

| 旋钮 | 位置 | 默认 | 说明 |
|---|---|---|---|
| 板块特征开关 | `finance.analyze_asset` market_ref 装配 | 开 | 去掉即可回退 v13 |
| `sector_signal` 公式 | `ml._live_context_return` | pct/2 + gap×2 | 热度→信号映射 |
| sector 权重 | 同上 terms 表 | 0.06 | 建议范围 0.04–0.10 |
| kline 缓存时长 | `eastmoney.kline` | 1800s | 限流恶化时可加长 |
| ref 特征窗 | `_enrich_series` 的 momentum_20 | 20 日 | 板块与大盘趋势窗口 |
| 验证窗 | `VALIDATION_WINDOW` | 240 | 调小→更贴近近期状态 |

## 8. 给后续优化者的验证协议（请勿跳过）

1. 任何特征/权重改动 → `STATE_VERSION` 必须递增（当前 14），全量重训；
2. 用 `train` 命令跑完整 A 股篮子，看**三窗口 skill** 是否同时改善，而不是只看聚合值；
3. 回测用 `backtest` 命令（T+1、万五佣金、印花税、整手），**阈值网格在留出集前半段选择、后半段验证**——只看 MAE 改善不算数；
4. 方向命中的置信区间：240 样本下 50%±3.2%（1σ），任何"改善"结论至少要跑 3 个以上标的且方向一致；
5. 对比基线永远是"不交易/不变"，不要和其他模型比绝对收益。

## 9. 变更记录

- v14（本文档）：原 28 个价量/资金流特征 + 大盘 2 特征 + 板块 3 特征 = 33 维；实时板块热度 0.06 权重；行业 7 天与 K 线 30 分钟缓存；UI 板块卡片（07 区右侧）。
- v13（上一代）：四组件集成、方向感知权重、中位数振幅校准、240 验证窗、6 锚点路径。

## 10. 结构化风险报表(风险证据面板的后端升级)

风险证据监测(19 类目)原依赖新闻词项匹配 + 资金流,财报类目只能"来源受限"。现接入**东财 datacenter-web 报表 API**(与 push2 不同的主机,免 key,结构化 JSON,实测验证):

| 类目 | 报表 | 结构化字段 | 判定规则(启发式,可调) |
|---|---|---|---|
| 股权质押 | `RPT_CSDC_LIST` | PLEDGE_RATIO(质押比例)、TRADE_DATE | ≥30% → detected,否则 covered(no_evidence) |
| 限售解禁 | `RPT_LIFT_STAGE` | FREE_DATE、FREE_RATIO | 未来 90 天内且比例 ≥1% → detected,否则 covered 并给出下次解禁日期 |
| 业绩风险 | `RPT_PUBLIC_OP_NEWPREDICT` | PREDICT_TYPE、REPORT_DATE、NOTICE_DATE | 预告类型含 预亏/首亏/续亏/大幅下降/略降/增亏 → detected;预增/扭亏 → covered;**仅保留 400 天内的预告**(旧预测不得覆盖更新的事实);另有 F10 归母净利同比 ≤−30% 的交叉判定 |
| ST 风险 | 证券简称规则 | 名称含 ST/*ST | 双向判定:命中 → detected;干净简称 → covered(该类目无"受限"状态) |
| 商誉风险 | `RPT_F10_FINANCE_GBALANCE` | GOODWILL、TOTAL_ASSETS | 商誉/总资产 ≥15% → detected,无商誉 → covered |
| 存货减值 | 同上 | INVENTORY、INVENTORY_YOY | 同比 ≥+60% 且 占总资产 ≥10% → detected |
| 应收账款坏账 | 同上 | ACCOUNTS_RECE、ACCOUNTS_RECE_YOY | 同比 ≥+60% 且 占总资产 ≥15% → detected |
| 存贷双高 | 同上 | MONETARYFUNDS、INTEREST_DEBT_RATIO | 货币资金 ≥30% 且 带息负债率 ≥20% → detected |
| 财务困境 | 同上 | TOTAL_LIABILITIES、TOTAL_ASSETS | 资不抵债(负债率 ≥100%) → detected |
| 财务分析 | 同上 | 最新报告期标记 | 覆盖标记(covered),无命中态 |
| 股东减持 | `RPT_EXECUTIVE_HOLD_CHANGE` | CHANGE_NUM、CHANGE_REASON、CHANGE_DATE | 近 180 天存在负变动或含"减持" → detected(口径:董监高;大股东层级二期);排序字段是 CHANGE_DATE 而非 REPORT_DATE |

### 状态机(结构化数据接入后)

```
结构化命中(detected=true) → detected,证据行 type=structured(带报表数值)
结构化未命中(detected=false) → covered(no_evidence),附结构化备注(不参与命中计数)
无结构化数据(限流/未预热) → 回退旧行为:新闻命中 detected,否则 financial_headline 类目 source_limited
```

**关键不变量**:结构化备注(如"质押比例 25.03%")只是信息披露,**绝不**把类目翻成 detected;detected 只能来自命中规则或新闻/资金流证据。

### 缓存与请求预算

报表 24h 缓存(`dc:{report}:{secucode}`),风险发现整体 10 分钟缓存(`riskreports:{symbol}`);后台训练器在非 A 股时段预热。盘中交互路径**零请求**(只读缓存),与限流完全解耦。

### 情绪与预测的接入

1. 市场情绪新增第五因子"结构化风险"(权重 0.15):`value = −max(0.35, priority/100)`,只在结构化数据存在时渲染;
2. 预测 live_context 新增 risk 项(权重 0.08):`risk_signal = −clamp(priority/100)`,仅在 detected_count > 0 时生效。

### 未覆盖与后续

当前仅剩**审计意见**未覆盖(报表名未核实)。踩坑记录:`data/get?type=` 端点必须显式 columns(不接受 ALL);`data/v1/get` 只认 datacenter 主机上存在的报表;报表排序字段错误会**静默返回空行**而非报错。减持的"大股东"层级(非董监高)与巨潮公告检索(orgId 映射)列为二期;审计意见优先。
