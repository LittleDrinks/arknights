# 自然叠层攻略教师（默认关闭）

这是供后续蒸馏的确定性**启发式教师**，不是模型，也不是胜率提升成果。只新增 `teacher/`；复用已审批的 `../macros/stacking.mjs`，不修改宏、原游戏规则、数据或战斗模拟。

## 固定依据与身份

游戏固定于 `../Stronghold-Protocol` 的 `6ab2468279e926d107788f63d7fe219b85f67b4a`；bot 源 SHA256：
`bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b`。
收尾只读检查时源仓库有本任务未写入的前端/recordings 工作区改动；`server/match`、`server/sim`、`shared`、`data` 相对 HEAD 无修改，bot SHA 符合固定值。本任务没有清理或提交那些改动。
`loader.mjs` 只对这一份 `server/match/bot.js` 作内存变换，一个 SHA guard，锚点不唯一、源码漂移或重复变换即拒绝。
不要串联 `macros/stacking-loader.mjs`，不要启用 `ARK_STACKING`。

知识依据：`../knowledge/distilled/pilot-strategies.json` 两张 pilot 卡，以及
`../knowledge/corpus/distilled/BV1a9wbzdE2S.json` 的机制卡与版本冲突说明。
源码依据：固定游戏 `server/match/bot.js` 的购买、lineup、planLayout/applyPlan；
`player/economy.js` 的 buy/refresh/sell；`player/placement.js` 的合法移动；
`bondsMeta.js` 的真实阈值/装备/独立 base 计数；`server/sim/content/garrisons/meta.js` 的事件和收益。

| 策略身份 | 实际中文 | ID | 条件 |
|---|---|---|---|
| Aroma | 阿罗玛 | chess_char_4_10_a | 在场；刷新次数→已激活叙拉古/奥术 |
| Angelina | 安洁莉娜 | chess_char_5_20_a | 在场；刷新次数→已激活叙拉古 |
| Lappland | 拉普兰德 | chess_char_2_16_a | 在场/手牌；该副本本回合首次见证主动刷新→叙拉古 |
| Dusk | 夕 | chess_char_5_12_a | 在场；获得干员次数→已激活炎/奥术 |
| Xiaoman | 小满 | chess_char_2_04_a | 在场；获得干员次数→已激活炎 |

## 接入和预算

`ARK_TEACHER` 默认 `off`，可选 `refresh` / `acquire` / `mixed`。
`ARK_TEACHER_BUDGET` 默认 `conservative`，可选 `aggressive`。非法参数拒绝启动。

教师在原 prep 开始和原购买循环中优先用 **当前可见** 槽位购买缺失核心、核心配对和达到目标盟约首档所需的不同补位牌；不自行刷新搜索未来牌。
原 shop scoring 增加同一优先权，原 lineup scoring 增加不同核心、实际目标盟约计数和激活奖励。
部署仍由原布局算法选择位置，经 `ps.move` 验证；不会直接设置 board、funds、shop、layers 或 RNG。
普通 bot 的六处 `ps.sell` 调用统一经卖出保护：核心（含配对和精锐）、带装备棋子、每个目标盟约首档所需的补位代表。
正常合法合成仍允许；不是永久冻结 UID。

开始部署后和 prep 尾部各调用一次已审批宏，报告保存在模块 WeakMap，动作/购买/净成本预算跨这两个阶段共享。
教师不读取私有副本触发计数、未来商店或 RNG；原 bot 自身既有可见池统计与波次预览逻辑保留。

| 每玩家每回合预算 | 保守 | 激进 |
|---|---:|---:|
| 教师直接购置核心/补位的实际购买价总额 | 8 | 16 |
| 教师直接购买次数 | 3 | 6 |
| 教师/宏付费动作后的资金保留线 | 4 | 0 |
| 两阶段宏总动作上限（含卖出） | 12 | 24 |
| 两阶段宏购买次数上限 | 2 | 4 |
| 两阶段宏净成本上限 | 4 | 8 |

宏 `maxBuyPrice=3`、`maxCostPerLayer=1`。普通原价拉普兰德循环按 `买3 + 刷新1 - 卖1 = 净3` 计算；买入之前须负担完整买入+刷新成本4，不预支退款。
免费刷新、折扣、赠币可能改变实际净成本，以日志为准。开始/尾部不是两套独立预算。
直接购置预算与宏预算分开，原 bot 正常购买/刷新/升级仍由原经济策略管理；这里的预算**不是整回合总开销上限**，原 bot 的 scoring 优先权也不计入教师直接购买额度。

```bash
# 单配置：原 botbench，自然商店、自然起始资金、真实 ABYSS 战斗
ARK_TEACHER=mixed ARK_TEACHER_BUDGET=aggressive \
  node --max-old-space-size=384 --loader ./teacher/loader.mjs \
  ../Stronghold-Protocol/tools/botbench.mjs \
  --configs solo:ABYSS --seed 1 --seeds 4 --rehearsal 0 --jobs 1

# 合同检查（测试 fixture 不作为自然证据）
node --test teacher/policy.test.mjs

# 对已提交的自然记录做只读检查，再生成 teacher/results/evidence.json
python3 teacher/summarize.py
```

`screen.py` 按固定顺序运行 off 加 3×2 种消融，并使用 `observe.mjs` 记录实际 onBuy、onLayers 和 onPrepEnd 前后状态。
观察器只透传原方法调用，不向策略提供 seed，不更改游戏动作或收益。
批量入口 `python3 teacher/screen.py --seconds 115 --seeds 4`；可用 `--cases mixed:aggressive,acquire:conservative` 缩小范围。
入口拒绝覆盖非空 `teacher/results/`，复跑须先保留既有证据并在独立副本运行。
本次所有本地真实 smoke 合计只有下述这一次 screen。

## 自然证据和消融结果

2026-10-18，原 `tools/botbench.mjs`，solo:ABYSS、seeds 1..4、rehearsal=0、jobs=1。
7 配置×4局，43.495 秒；峰值子进程 RSS 333,844,480 bytes（318.38 MiB），screen 运行时文件约1.65 MB，低于 768 MiB/20 MB；全部正常退出，游戏错误数0。
每一条已记录 prep 的共享预算、真实净成本和动作成功情况已由 `summarize.py` 检查。

| 配置 | 胜/局 | 平均通过回合 | 刷新宏动作 | 获得宏动作 | Lap循环宏动作 |
|---|---:|---:|---:|---:|---:|
| off | 0/4 | 12.25 | 0 | 0 | 0 |
| refresh 保守 | 0/4 | 11.75 | 52 | 0 | 0 |
| refresh 激进 | 0/4 | 11.75 | 53 | 0 | 0 |
| acquire 保守 | 0/4 | 11.50 | 0 | 24 | 0 |
| acquire 激进 | 0/4 | 11.50 | 0 | 41 | 0 |
| mixed 保守 | 0/4 | 10.50 | 39 | 2 | 0 |
| mixed 激进 | 0/4 | 11.00 | 43 | 9 | 0 |

表中“宏动作”包括成功 buy/refresh/sell，不等同于触发次数或层数。4个种子被各配置重复使用，不能表述为28个独立种子。用户提供的此前896局0胜不是本次重新复现的数据，不与本次直接合并统计。

可直接复查 [results/evidence.json](results/evidence.json) 的两个 witness，原始数据为 [results/mixed-aggressive-trace.json](results/mixed-aggressive-trace.json)：

1. **seed 3，第9回合**：教师从可见槽0以3资金买入阿罗玛，资金6→3，真实 onBuy UID=30。prep 结束前 UID30 在场；奥术 count2/tier1 激活，炎 count3/tier1 激活，叙拉古 count2/tier0 未激活。尾部刷新宏付1，刷新计数2→3；宏即时层数增量0，原 onPrepEnd 后奥术8→14（+6），叙拉古仍为6。+6是整回合三次刷新的收益；此次宏的预测边际是+2，不把+6全部归功于宏。
2. **同 seed 第10回合**：教师买入补位牌 `chess_char_3_19_a`，三盟约自然同时激活，叙拉古/奥术/炎 count=3/2/3，tier均为1。阿罗玛 UID30 保留在场；开始阶段刷新宏连续3次，真实资金13→10，净成本3。onPrepEnd 叙拉古6→12、奥术14→20，各+6；炎保持11。这里是实际层数，不是只有 projectedLayerGain。
3. **seed 4，第4→6回合**：第4回合自然买入小满 UID11，价格3。第6回合仍在场且带装备，炎 count3/tier1 激活；获得宏完成一次 buy+sell（折扣后净0），全回合获得数3；onPrepEnd 炎3→6（+3）。全回合层数包含原 bot 的获得行为，不能将全部+3视为宏的独立边际效果。

## 已知边界，交独立 review

- 只证明自然购买→部署→盟约激活→真实叠层链路打通；全部0胜，平均存活回合弱于 off。没有 heldout 成功，没有最佳配置选优结论，不适合作为已验证强教师训练标签。
- 启发式奖励可能牺牲阻挡、输出、升级节奏。首档补位购买按棋子静态 bonds 计数，未完整利用装备授予盟约/调和；实际 lineup 仍使用原 `computeBonds`，实际宏检查 active。
- 本次 Lap 循环宏为0动作，不能声称原价Lap3循环已获得自然验证。普通拉普兰德首次刷新特质有真实触发；核心/配对优先购买也可能在宏之前消耗备用Lap可见槽，另有未激活、手满、合成风险、资金不足等停止条件。maxBuyPrice3只是允许价格，不保证该循环发生。
- 没有收齐五名核心的要求或证据；安洁莉娜、夕依赖自然可见牌/原升级曲线，缺件不会构造牌。mixed 能同时激活三盟约，不代表三种发动机齐全。
- 保护覆盖 bot 主动卖出和宏的新购可丢弃检查；不能阻止游戏强制溢出清理、淘汰或装备触发的合法变形。过多受保护牌可能堵手；没有无限腾挪循环。
- 本次只验证 rehearsal=0。尾部为确保新购核心和补位部署而再跑原默认布局；非零 rehearsal 的最优布局可能被覆盖，尚未验收。
- 本目录合同测试3项全部通过；只做这些检查和限时自然 smoke，未跑原仓库全量CI、长期训练、合作模式或外部评审。提交后停止写入，等待独立 review。
