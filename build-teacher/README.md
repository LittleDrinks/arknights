# 自然商店成型教师 v1（候选启发式，默认 off）

目标是自然、完整的 ABYSS 通关。**本次没有通关，也没有证明策略更强**：8 次有界自然运行全部失败；完整 sargon / kjerag 都比同种子的原 bot 更早淘汰。这是可复用、可消融、供独立 review 的候选控制器，不是强模型或成功训练标签。此前 combat-audit 的构造 R14 杀 300 万 Boss 只说明给定阵容的战斗能力，不是自然发育或完整通关证据；本任务没有运行该 fixture。

只新增 `build-teacher/`，基线分支 `exp/build-teacher`、提交 `d70c84b8a128c368b0eb8a97c7194295552d2c02`。游戏 HEAD `6ab2468279e926d107788f63d7fe219b85f67b4a`。不修改游戏、已有 teacher/macros/controller，不安装、不 SSH、不创建新 agent/goal。

## 接入与消融

`loader.mjs` 只变换配置游戏根目录下的 **一个** `server/match/bot.js`，SHA256 必须为
`bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b`。
每个锚点必须唯一，六处卖出必须完整匹配；源码漂移、重复变换和已启用的其他策略 loader 环境变量拒绝执行。不要串联任何其他 bot transform。off 不检查/变换源码，直接返回 `nextLoad` 的原对象；批量入口无论 off/on 都另外检查游戏 HEAD、runtime 工作区及 486 个源码/数据 hash。

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `ARK_BUILD_TEACHER` | `off` | `off` / `sargon` / `kjerag`，非法值拒绝 |
| `ARK_BUILD_ROSTER` | `1` | 目标盟约 focus、buy、lineup、角色缺口和卖出保护 |
| `ARK_BUILD_EQUIPMENT` | `1` | 商店装备/可见奖励评分、itemTarget 配对和空槽保留 |
| `ARK_BUILD_ADJACENCY` | `1` | 谢拉格圣聆初雪与灵知邻格偏好；Sargon 下无操作 |
| `ARK_BUILD_GAME_ROOT` | 同级 `Stronghold-Protocol` | 固定版本游戏 checkout 的绝对路径 |
| `ARK_BUILD_TRACE` | 无 | 仅观察器需要，JSONL 输出路径 |

三个模块开关只接受 `0` / `1`，可独立关闭；`off` 无条件关闭全部策略。候选目标主盟约若在公开 `disabledBonds` 或模式静态禁用列表中，所有新评分退回零。保留原 bot 自然选择 band，不指定 `--band`，不筛种子、不修改 RNG、商店、敌方或波次。

`policy.mjs` 导出纯评分和有限控制 hook，也可由其他 server runner 复用；函数可传独立 `cfg = options(env)`，默认读取启动时 CONFIG。`finishTemp` 和 `safeSell` 是动作 hook，必须在合法 PREP 上下文调用；其余为读取和评分。观察器报告 WeakMap 不提供给评分作决策输入。

## 候选策略及实际边界

Sargon 的期望终局八人：至简、缇缇、异客、佩佩、百炼嘉维尔、卡涅利安、莱恩哈特、空弦。至简保留黄沙罗盘＋萨尔贡浓茶同身；异客/佩佩偏好万能近战机械臂＋浓茶。至简低技力短轴技能配罗盘发动 SP 链，缇缇保留自然控制叠层，莱恩哈特保留已激活精准/迅捷的每回合收益。

Kjerag 的期望终局八人：角峰、初雪、灵知、凛御银灰、圣聆初雪、锏、空弦、夕。圣聆初雪偏好机械臂＋耶拉冈德之泪；空弦偏好谢拉格不融冰＋浓茶；锏偏好机械臂＋浓茶。初雪/灵知是自然冻结链的组成部分；不是只追圣聆初雪一张六本卡。

两套期望阵容均不是强制八卡白名单。当前可见的不同低费本家牌优先补 3/6 阈值，临时阻挡、防空、治疗可替换期望名单中的位置。原 bot `computeBonds` 提供实际 lineup 计数，包括装备赋予盟约和调和；购买补位估计只看静态本家归属，不把金/普同名当两个成员。

新增系数集中在 `policy.mjs`，均为未调参先验：主盟约 focus +18；缺失目标牌 +12、发动机再 +5；不足三/六本家成员 +16/+10，低费三人补位再 +4；目标副本合成进度 +9，已有精锐时普通重复循环 -12。lineup 本家每个成员最多计六个、每个 +6，激活 +16、六人数 +18；角色缺口另罚没有奶 -30、没有防空 -18、缺阻挡每个 -10。装备有当前可装备目标才 +16。分数不是伤害/胜率估计。

经济和部署主体仍是原 bot：`buyLoopSteps`、`levelUp`、`fundsReserve`、`refreshValue`、合法 `buy/pickReward/equip/move/sell` 和 `planLayoutSteps`。没有额外付费买牌/刷新/叠层宏，保留原升级曲线（第12回合目标六本）和补满棋盘的购买阶段。不会绕过原 bot 的手满、合成、lineupGain、经济阈值。本次所有配置 R4 起实际部署 8 人；R1–R3 自然资金只能逐步补位，没有伪造满员保证。

邻格模块只给原布局搜索中已合法的候选格/方向一个 `abs(originalValue)*0.04` 偏好，不直接搬盘、不运行额外战斗。要求实际灵巧激活、另一核心已在候选 plan 中，40 层前正交四邻格，40 层起八邻格。仍以原布局覆盖/防漏/治疗模型为主；不是硬约束，不能保证邻格或射程覆盖最优。

六处原 bot 卖出统一走 `safeSell`：有限保护目标代表、合成副本、在场治疗/本家补位和装备棋子。普通 sell 仍直接调用原 `ps.sell`。最终溢出先尝试移入手牌，再从手牌/临时区按保护分从低到高调用合法 `ps.sell`，必要时显式降级保护并记录 `overflow-sale` 的 UID、分数、结果、真实退款。还有 chess 留在 temp 时抛错，不允许把它交给直接 discard fallback；剩余 token/item 才使用原 `resolveTemp`。这不是永不卖核心的承诺，也不能拦截游戏在 Ready 后授予资源、地图撤退、淘汰等全部生命周期。

装备保留只覆盖原一般装备路径，不强行拆装、不制造装备。暂时空槽可能明显降低道中战力；消耗品/原游戏变形路径仍可能替换装备或棋子。实际自然 evidence 中完整 Sargon 至简最终没有罗盘/浓茶，同身发动机**未成型**；不能用合约测试替代自然成型证据。

## 机制与原始来源

先读了 `../experiment-combat-audit/combat-audit/{fixture.mjs,guides.json,mechanics.json}`，再查原字幕和当前源码。来源 hash 见 `source-hashes.json` 的 `evidence`；该文件还记录全部固定 game 输入 hash。没有联网重新拉取视频，不声称完整观看。

| 依据 | 原字幕窗口 / 固定源码 | 对本候选的含义 |
|---|---|---|
| BV16YSUBZETc | `../knowledge/corpus/raw/subtitle_BV16YSUBZETc.json`，46.81–149.06s | 萨尔贡 buff 并发有上限；莱恩哈特的精准/迅捷激活比无条件追本家层数重要 |
| 同上 | 151.90–264.21s | 罗盘、异客回转、至简短轴载体；ASR 的“质检”等不作为实体 ID |
| BV1xVdBBWEuu | 原字幕 450–590s、889–967s | 冻结事件与叠层；3–4本时期先凑本家/战力，不能为了叠层牺牲生存 |
| BV1X3XUB9EQb | 原字幕 139–171s、258–274s | 空弦攻频与不融冰；不为了贪层下掉六谢或唯一灵巧组件 |
| `server/sim/content/items/battle.js:748` | 罗盘 `skillStart` / `skillEnd` handlers | 必须同身浓茶；每次开启技能回 3 SP，忽略阻回；实际只作用于同 owner、在场、带萨尔贡的干员，**不是无条件全队** |
| `data/bonds.json`、`server/sim/content/bonds/addon/battle.js` | 萨尔贡阈值 3/6；灵巧阈值 2、40 层换八邻格 | 本家成员数、灵巧激活与层数都使用实际状态 |
| `data/garrisons.json` | `garrison_28_*` / `125_*` / `51_*` | 初雪新冻结触发；缇缇沉睡/晕眩触发且有每战上限；莱恩哈特只给已激活盟约层数 |
| `server/match/bondsMeta.js` | `computeBonds`、`pieceBonds` | 精锐不是两个不同干员；装备/调和可能改变实际计数 |
| `server/match/player/economy.js:215` | `sell` | 原合法退款、库存归还和装备返回；不能以清空数组代替卖出 |
| `server/match/player/prep.js:76` | 原 `resolveTemp` | 直接删临时棋子不会走合法 sell 退款，本候选明确避免该保护旁路 |

精锐 fixture 的特质数值不套给自然普通牌。两套都没有设置静态 loadout：实际 seats 的 `loadout={}`、DIY stock=0，使用固定数据默认技能/培养规则。默认至简=神工意匠（skill index1）、异客=辉煌裂片（2）、灵知=零度爆发（1）、圣聆初雪=群山俯首（2）、初雪=自然震慑（1）、空弦=箭矢·暴风（2）。这些是公开静态预设，不是通过选牌或加牌获得的奖励。

## 信息边界

原 bot 已有输入：自身棋盘/手牌/装备/资金/商店/奖励、公开共享池剩余、队友盟约、当前回合敌方及路线预览；原 bot 自己的 RNG 消耗、特质缓存、布局模型保留。这里复用该 bot，不声称把它改成仅屏幕像素策略。

新增策略输入只有自身已拥有棋子及当前局面、静态干员/装备定义、实际盟约/层数、公开禁用列表、原 bot 正在评分的当前可见 shop/reward/card、原合法候选布局及原覆盖评分。新增策略不查询共享池、不读 seed/RNG、未来商店/敌人/波次、未揭示奖励，不模拟搜索未来。改变合法行动自然会改变后续 RNG 消耗序列，消融不意味着不同策略仍收到逐次相同发牌。

观察器单独读取 seed、Boss 池、LP/alive、实际阶段、动作结果用于审计；不把日志/seed/Boss 内部状态送给策略。每个 wrapper 调原方法一次、透传参数和结果。`prep` 是原 onPrepEnd 生效后的实际快照，`result` 记录战斗结算的层数/漏怪/存活，`finish` 记录正式自然胜负和 Boss 血量。原 botbench JSON 另保留每轮 LP/alive、合成/花费和错误。可选 `newBattle` battle-end hook 在本次 worker 路径未触发，因此本次“生存”证据是 LP/alive/漏怪，**没有逐干员战斗末 HP 遥测**。

## 精确命令

从 `experiment-build-teacher/` 运行，无需安装依赖。

```bash
# 无战斗合同测试（guard、配对、邻格、角色缺口、合法退款、开关）
node --test build-teacher/contracts.test.mjs

# 已有证据只读检查：不会重新运行游戏
python3 build-teacher/verify.py

# 本次唯一自然 smoke 的实际命令；已执行，不要在本机追加运行
python3 build-teacher/run.py
```

runner 默认按 `upstream,off,modules-off,sargon,kjerag,sargon-no-equipment,kjerag-no-adjacency,sargon-no-roster` 各跑 seed1 一次；所有原始命令存于 `results/budget.json`。等价的单配置入口如下（仅说明接入，**本地八局配额已耗尽**）：

```bash
ARK_BUILD_TEACHER=sargon ARK_BUILD_TRACE="$PWD/build-teacher/new-trace.jsonl" \
  node --max-old-space-size=384 --loader ./build-teacher/loader.mjs \
  --import ./build-teacher/observe.mjs ../Stronghold-Protocol/tools/botbench.mjs \
  --configs solo:ABYSS --seed 1 --seeds 1 --rehearsal 0 --jobs 1 \
  --json ./build-teacher/new-result.json --quiet
```

server 批量入口已提供，**本次未执行**。将固定版本游戏和本目录部署到服务器后，由服务器自身调用（此任务不 SSH）：

```bash
ARK_BUILD_GAME_ROOT=/absolute/path/to/Stronghold-Protocol \
  python3 build-teacher/run.py --server --out server-heldout-101 \
  --cases upstream,off,sargon,kjerag,sargon-no-equipment,kjerag-no-adjacency,sargon-no-roster \
  --seed 101 --seeds 128 --seconds 7200 --output-mib 2048
```

`--server` 仅显式放宽运行次数/时间/输出预算，仍串行、每局独立进程、真实原版 botbench、rehearsal0/jobs1。不覆盖非空输出目录；game 源码/数据漂移拒绝；累计本地 ledger 超八局/39秒拒绝（已实际验证拒绝，无第九局）。不要删除 ledger 绕过本地配额，也不要在本机用 `--server` 绕过约束。原 botbench 的 `--band` 会覆盖 offered band 列表，runner 故意不暴露该参数。直接底层 Node 命令没有 supervisor 的预算保护，不能据此追加本地测试。

预算监控每25ms采样进程及 supervisor RSS、输出和子进程墙钟，700MiB/16MiB 提前终止，为用户的768MiB/20MB留余量；V8 heap384MiB不等于OS硬RSS限制。超时/内存/输出超限发 SIGKILL，不修改游戏结果为胜负；记 failed/incomplete，不丢弃该尝试。服务器默认总输出仍16MiB，示例用显式较大上限。并发启动多个 runner 不在此监督模型内。

## 本次自然证据

2026-10-18，固定 seed1，solo:ABYSS，原始自然开局，rehearsal0/jobs1，无 band/资金/卡/层数/商店改写，无种子筛选。8 次运行是**一个种子上的八个配置**，不是8个独立随机种子，不提供胜率统计结论。

| 配置 | 通过回合 | 最后 prep 主盟约层数 | 最终 Boss 剩余 HP | 自然结果 |
|---|---:|---:|---:|---|
| 原版 | 13 | 萨尔贡128 | 2,654,294.410 / 3,000,000 | R14失败 |
| 默认 off | 13 | 萨尔贡128 | 2,654,294.410 / 3,000,000 | R14失败 |
| 全模块关闭 | 13 | 萨尔贡128 | 2,654,294.410 / 3,000,000 | R14失败 |
| sargon | 11 | 萨尔贡36 | 未到Boss | R12淘汰 |
| kjerag | 11 | 谢拉格13 | 未到Boss | R12淘汰 |
| sargon-no-equipment | 13 | 萨尔贡79 | 2,911,099.148 / 3,000,000 | R14失败 |
| kjerag-no-adjacency | 11 | 谢拉格13 | 未到Boss | R12淘汰 |
| sargon-no-roster | 12 | 萨尔贡77 | 未到Boss | R13淘汰 |

默认 off、全模块关闭相对原版：去除 wall/CPU 计时、RSS、配置标签后，**原始结果及已记录的全部动作/状态 JSONL 完全一致**，不是只比较胜负。`results/summary.json:realControls` 有机器检查结果，合同测试另验证 off loader 对象恒等透传。所有局完整结束、错误0、调用原动作API拒绝0、原生temp无退款丢棋0。策略层拒绝卖出4次，均在 sargon-no-equipment；自然紧急 `overflow-sale` 未触发，只在无战斗合约中覆盖，不能声称自然验证了极端手满路径。

完整 Sargon：R8首次本家激活（原版R7），R11 count6含调和+1，R12仍是5名实际萨尔贡＋调和，不是6名本家全收齐。最后期望核心只有卡涅利安、至简、莱恩哈特、百炼嘉维尔；缇缇、异客、佩佩、空弦未成型，至简没有配对装备。最终白面鸮在场，仍漏怪淘汰；激活数与满八位不能代替战斗强度。

完整 Kjerag：R8首次 count3 激活，R12 count4/13层，最后目标核心角峰、凛御银灰、圣聆初雪；耶拉是可见自然过渡。没有初雪＋灵知冻结链，没有六谢；圣聆初雪没有期望装备。该 seed 公布禁用列表包含灵巧，但双盟约牌仍可出现；最终没有同时持有圣聆初雪与灵知，本次邻格模块没有有效触发，no-adjacency 相同结果不构成收益验证。

所有配置 R1–4 部署数2/4/6/8，R4后保持8；完整候选升级曲线为 R4二本、R5三本、R8四本、R10五本、R12六本。`results/summary.json:cases[].rounds` 保存**逐轮实际** level/LP/layers/core/equipment/adjacency；对应 `.jsonl` 有 board UID、位置、方向、手牌、实际动作和结算。表格层数是最后 prep 快照，不混同战斗中后续层数或全局总层数。

累计子进程墙钟 **33.380892秒**（包含启动、导入、观察和原 benchmark；比纯模拟更保守），采样总 RSS峰值 **266,846,208 bytes = 254.484MiB**；子进程 high-water **237,932KiB**。运行加派生 summary 输出 **1,145,896 bytes**，整个交付目录最终体积由 `verify.py` 检查小于20MB。`results/budget.json` 的 outputBytes=884,957 是当时 summary 生成前采样；新版 runner 会额外写回含 summary 的最终计数，历史证据不重写。smoke 后仅完善只读验收、文档和runner账本拒绝/计数，没有改动已执行的 policy/loader/observe。

## 已知限制与 review 重点

- 当前候选只是评分先验，不能保证核心早来、装备配齐、6人盟约、奶量、实际防漏或完整通关。本次完整候选早期激活晚于原版，是负面证据。
- 预留两装备槽可能导致长时间空装，是本次最值得继续独立审查的退化来源；禁装备模块活到R14，但单种子不能把全部差异因果归于空装，策略还改变了后续自然发牌序列。
- 冻结/沉睡/技能循环没有通过未来战斗搜索优化；不融冰的随机效果与战斗叠层收益不能凭持有装备推定。
- 原 bot 的合成 keeper/lineupGain 阈值仍可能拒买低阶副盟约发动机的追加副本；固定名单保护也可能挤占手牌。未实现高阶全局经济优化、每次换人后的可靠约束求解或穷举配装。
- 邻格是软分数且在原先手布局中依赖另一核心先落位；装备授予的灵巧成员、治疗覆盖和Boss站位相互作用未充分验证。没有天然双核心样本，不报告邻格模块有效收益。
- overflow hook 只封原 bot 的主动收尾路径；游戏自身其它生命周期不改。无战斗合约覆盖“全受保护仍合法卖出退款”；自然运行没有该紧急情形。
- 本次只验收 solo、rehearsal0、一个固定自然种子；合作/非零rehearsal/heldout大量跑分未执行。server入口可用不等于服务器验证完成。
- 不把构造 fixture、字幕作者自述、静态机制或本弱启发式称为“强模型”。需要独立 review 后才决定改策略、扩测试或作为教师数据来源。提交后停止，等待 review。
