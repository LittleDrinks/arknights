# 验证记录（2026-10-18）

## 独立 review 后修正 V8 状态比较

固定 `419a501` 的独立只读 review 给出 CHANGES REQUIRED：真实 reset 后的连续快照及 BAD_TARGET 空操作可出现 Buffer 字节不同，但解码值深度相同，误锁存 fatal。此问题在 `d70c84b` 基线也存在。不得按空 pathdiff 豁免。现在保留 V8 快照作为不可变投影，使用 `sameRejectionState`：相同字节快路径，否则对解码后的完整投影 `isDeepStrictEqual`；状态字段、Map/Set 顺序编码、callback identity、RNG 和 scheduler 范围未缩小。

主协调进程验证：`python3 -B coop/check.py --only rejections --seconds 14`，9/9 通过，8.567 秒，采样进程组 RSS 峰值410.71 MiB，968 bytes 输出，证据 `/tmp/coop-check-csnnq9l1`。包含180次真实连续快照/validation、V8数组内部表示差异、holes/undefined/-0/NaN/顺序/callback ID不相等，以及既有8类状态污染/effect/异常/候选拒绝与完整终局用例。未运行服务端长前缀或大训练；需独立定点复审后再执行。

## v3 move preflight：exp/coop-filter

基线 `d70c84b`，工作树 `experiment-coop-filter`；只修改 `coop/`。原规则 pin `6ab2468279e926d107788f63d7fe219b85f67b4a`，父环境的 HEAD/clean 检查保留。无 SSH、依赖安装、subagent 或大 batch。本次未重现主 server 的完整 normal 768 / mixed 3840 steps 前缀，不能宣称那两批已通过。

审查 `server/match/player/placement.js`：chess BAD_TILE 在 160（目标）/171（反向 swap）；token 在 242/248（目标与 ownerAfter）/255（反向 swap）/266（手牌 token 占用格）；board→hand 在 311（换入 chess 的原格）。chess→自身 summon 会回收它，不能错误套用反向落点检查。`deployMap` 会写缓存及 `_legalityStale`；token 回手存在删除/恢复及 count 路径，不能因错误码放行状态变化。本次仅提前过滤上述确定 BAD_TILE，不豁免 HAND_FULL、effects 或其他真实状态变化。

`preflight.mjs` 调用当前 player 原 `_legal`，使用 Object.create(ps) 派生 receiver 承接缓存写入，使用同一 pinned 游戏路径的 board helpers；不做 snapshotclone / move 模拟 / 棋盘写入。固定 arrange 记录 blocked reason；original move 记录 filtered。实际拒绝计数、成功轨迹和 guard 规则保留。新增 pathdiff 是粗粒度字段路径，不含值、Map key 或队友私有动作参数；不进入 features，partner fatal 的安全路径可在错误响应看到。

验证结果：

- `--only preflight --seconds 3`：最终 **3/3 通过**。16 个独立构造 placement fixture 对比原 `ps.move`：目标/反向 swap、hand swap、召唤物 range/occupancy、ownerAfter 双方向、自身 summon 例外、缓存 stale 等。过滤分支原引擎均 BAD_TILE，board/hand/temp 不变；预检全状态及 live cache 不变。正例原引擎成功。另有两个独立真实 reset、seed 11、相同 buy 前缀，验证 (10,6) LEFT / (11,6) DOWN 的原引擎拒绝与过滤；这不是服务器 R4/R11 的完整复现。生产 factory、original filtered 计数、未知目标实际拒绝计数以及被吞掉的 preflight 异常 fatal 均覆盖。
- 拒绝 suite 首次完整运行 **6/7 通过**：mixed/heldout 真实终局与可见性、weak/学习候选错误、primary 隐藏污染 fatal 通过；`only known unchanged` 在 BAD_TARGET 上间歇报 state-changed。单跑该项通过，随后前三项串联曾在 NEW_ERROR 的空操作上也报 state-changed；添加诊断断言后的相同前三项串联 **3/3 通过**。未修改 guard 判定来消除它，尚不能断言间歇现象已解决；review/server 应保留 reason/pathdiff（包括空 pathdiff）。
- 另一次排除终局的筛选运行未在 3 秒完成，被监控终止，无结果，**不计通过**。未继续完整重跑。
- 原容量过滤/方法恢复、冻结池身份检查通过。schema 未改，终局 `episode.diagnostics` 仍携带诊断。
- `git diff --check` 通过。未运行完整 engine 四终局 suite、learner smoke、训练或 v3 同 seed 大 batch；留给主 server 在 review 后运行。

全部监控调用（包括两次 fixture 初始化失败和一次超时）累计 **17.363 秒**，采样进程组 RSS 峰值 **401.8 MiB**，低于 20 秒 / 768 MiB。监控器阈值 700 MiB、Node heap 192 MiB、50ms 采样；这不是内核硬 RSS 限制。

| `/tmp/coop-check-` 后缀 | 秒 | 结果 |
|---|---:|---|
| `m231z3_u` | 0.823 | fixture 缺 onEnd；真实 reset 对照通过 |
| `t2k1d8cd` | 0.924 | fixture 缺 difficulty；真实 reset 对照通过 |
| `8igpi8wk` | 0.929 | 初版 preflight 2/2 |
| `sa8h6fsl` | 7.842 | 完整拒绝 suite 6/7，间歇 state-changed |
| `0xbbjnhl` | 0.822 | guard 项单跑通过 |
| `8uh59g7n` | 0.972 | 前三项串联 2/3，空操作 state-changed |
| `oa5vb5p9` | 1.029 | 前三项串联 3/3 |
| `5ydms_kg` | 0.779 | 最终 preflight 3/3 |
| `axk8zfw3` | 3.038 | 3 秒监控终止，无结果 |
| `lsh_gl7t` | 0.206 | 容量/身份检查通过 |


## 本次拒绝分类修复：exp/coop-robust

工作树 `experiment-coop-fix`，基线 `bf33344`。只修改 `coop/`；未修改游戏、`rl/`、`parallel-rl/`、schema 或 checkpoint。以下服务器现象来自主会提供的已保留证据，本次未通过 SSH 重跑：normal/mixed 首次 4 actor ABYSS 均首 batch 失败、totalSteps=0；normal 原版队友拒绝被计入 actionErrors；mixed primary arrange 在 R6 提议 uid10 → (10,5) RIGHT，BAD_TILE 被计入 controllerErrors。原 bot 11556 轨迹本已有 8 次 BAD_TILE，拒绝后游戏能继续。**本次不宣称没有拒绝，也不宣称服务器失败种子已经通过。**

源码核查（规则固定为 `6ab2468279e926d107788f63d7fe219b85f67b4a`）：

- `player/placement.js` 的棋子/召唤物 BAD_TILE 和 BOARD_FULL 在对应写入前返回；但 `_moveToHand` 的召唤物 HAND_FULL 有删除/回退与 token count 路径，不能仅凭错误码保证状态未动。
- `player/economy.js`、`prep.js` 的所列 validation 返回位于 spend/库存/ready 等写入前。
- `player/items.js` 的 `equip/useArt` 可先 dispatchItem 再返回错误；拒绝并不普遍意味着无副作用。此次对进入效果处理的拒绝一律不豁免，也不自动回滚。
- `bot.js::tryDo` 捕获异常，因此方法异常另外锁存 fatal，不能因上游忽略异常而通过健康检查。

实现口径：原版纯 validation 和固定 arrange 的已知 BAD_TILE/BOARD_FULL 经过状态比较才是 diagnostic；原始 controllerErrors、每席 rejected、队友拒绝总数及具体原因均保留。学习候选、weak 自写动作、未知错误、效果拒绝、状态污染和 engine 异常仍拒绝整个 rollout。状态守卫不是观测；终局诊断字段不加入 policy features。容量前置过滤继续单列。

本地验证：

- `python3 -B coop/check.py --only rejections --seconds 5`：修复前精确复现两条真实边界失败（action=1 / controller=1）；修复后两条通过。BAD_TILE 回归明确断言 `controllerErrors=1`、诊断计数为 1、没有成功 move 轨迹。
- `python3 -B coop/check.py --only rejections --seconds 8`：新增的前 5 项全部通过。覆盖真实拒绝前后状态相同且下一轮继续，隐藏玩家字段/其他玩家/共享库存/债务/RNG/UID/队列污染，效果调用后拒绝，候选过期、weak 错误、未知控制器拒绝和异常。固定 mixed 的真实终局保留主动触发的原版拒绝及原因，非终局不返回队友审计，拒绝不增加成功计数/hash。
- `python3 -B coop/check.py --only engine --seconds 17`：原有 3 项全部通过；normal、固定 weak、heldout、weak 重放均到真实终局，主学习席位动作、队友调度、schema/可见性和 PRNG 检查通过。
- 监控下执行 `node --test --test-name-pattern='a primary arrange BAD_TILE' coop/rejections.test.mjs`：最后新增的第 6 项通过；注入返回 BAD_TILE 的隐藏状态污染，确认 controllerErrors 保留、不能归入 diagnostic、整局拒绝，污染未被回滚掩盖。此项与前 5 项分别执行，未为凑完整输出重复仿真。

本次全部 simulation/check 墙钟累计 **26.764 秒**，采样进程组 RSS 峰值 **393.87 MiB**；监控器提前保护阈值 0.90 GiB，未触及 1 GiB 预算。目录及资源记录如下，均保存在 `/tmp`：

| 目录 | 秒 | 结果 |
| --- | --- | --- |
| `coop-check-4ba354k2` | 0.981 | 第一条复现；第二条测试暴露未固定 weak 抽样问题 |
| `coop-check-qngjfnas` | 0.927 | 固定测试队伍后，两条目标失败均复现 |
| `coop-check-7702p9x1` | 0.972 | 两条修复回归通过 |
| `coop-check-rh9lx0cj` | 6.158 | 5 项拒绝分类/终局回归通过 |
| `coop-check-pifrbq2p` | 16.173 | 原有 engine suite 3 项通过 |
| `coop-robust-final-ipefe5x7` | 0.587 | primary 污染回归通过 |
| `coop-robust-metadata-qigoh3jl` | 0.964 | 第 7 项真实终局元数据回归通过 |

边界：未重新运行 learner/checkpoint smoke、服务器 4 actor 失败种子或批量训练；没有 SSH、安装依赖、subagent 或 goal。已有 checkpoint 文件未改变，schema 相等由真实 engine 测试验证。未固定 `COOP_TEAM` 的 weak/seed 11 会因 `selectTeam` 在 find 内重复抽样产生 undefined；这一独立问题未改动，重跑需沿用固定队伍。终局将诊断同时置于 episode.diagnostics，真实 heldout 终局/重置测试确认该字段只在终局出现、schema 不变；另外直接抽取并执行未修改的 learner.episode_result，确认非零拒绝计数及原因原样保留（不导入 Torch、不加载模型）。提交后停止，等独立 review。

## 历史验证：exp/coop（不计入本次预算）

工作树 `experiment-coop` / 分支 `exp/coop`，父提交 `4f23f97`。游戏 HEAD `6ab2468279e926d107788f63d7fe219b85f67b4a`，已跟踪规则文件未修改。所有新文件位于 `coop/`。

## 通过的真实 engine 验证

命令：`python3 -B coop/check.py --seconds 32`。其中 Node engine 测试 **2/2 通过**，用时 14.466 秒；后续 learner 首次加载因原 768 MiB 软预算被监督进程终止，不能把整条命令称为全通过。日志 `/tmp/coop-check-7ouzw7si/check-0.log` 保留完整 engine 结果。资源预算更新后没有重复这 4 局。

真实 game seed 11、ABYSS、primary 每轮 4 步预算；每行均到原引擎 RESULT / 完整 onEnd，4 个玩家 summary。不是仿真快照、代理规则或截断结果。

| 池 | victory | roundsPassed | 实查 PREP 次数 | primary steps | 三席成功方法调用次数 |
| --- | --- | --- | --- | --- | --- |
| normal | false | 13 | 14 | 34 | 274 / 267 / 180 |
| weak（buy2/saver/buy2） | false | 9 | 10 | 31 | 146 / 23 / 116 |
| heldout（random 三席） | false | 4 | 5 | 13 | 26 / 27 / 15 |
| weak 完整重放 | false | 9 | 10 | 31 | 146 / 23 / 116 |

这四个历史固定用例通过当时的错误检查，但不能推断其他种子不会拒绝原 bot 的提议。玩家淘汰后不再要求其执行 PREP；测试在每次真实 endPrep 前检查所有仍存活队友 scheduled=1、ready=1。三池实际动作 hash/计数不同；weak 的完整 primary 动作序列、summary、逐轮队友审计完全一致。

测试还验证：primary 第一笔真实购买改变自身 shop.sold，并记录 policy 来源；全部 primary 买/卖/刷新/升本均来自 policy；父 features schema 相等；读取 observation 时禁止调用其他玩家 privateView；多次伙伴选择前后所有引擎 rng.state 不变；非法索引不改变局面；训练池拒绝 heldout/未知身份/无效种子/无效固定组合。

## 最小 learner 兼容性

资源补充后命令：`python3 -B coop/check.py --only learner --seconds 37`，**通过**。证据 `/tmp/coop-check-p_scw_8h/check-0.log`、同目录 `resources.json`。

- 真 solo teacher 的 2 个合法标签、1 次 BC 更新：参数 L2 改变量 `0.0072156209498643875`；保存/加载后，原 `validate_checkpoint` 对 coop schema/rules/dimensions 校验成功。
- 未修改的 `rl/learner.py demo`：固定 mixed original/buy2/saver，seed 7 NORMAL，95 个合法标签；完整 summary.victory=true，roundsPassed=14。
- 未修改的 `rl/learner.py ppo`：从上述 solo BC 热启动，4 个真实 coop transitions、1 次更新，参数 L2 改变量 `0.008320867083966732`。0 个 terminal transitions；第 3 轮课程截断明确标注为非终局，使用 bootstrap，不计胜负。
- 未修改的 `rl/learner.py eval`：冻结更新后的权重，heldout random 三席，seed 7 NORMAL、budget 2；真实完整 summary.victory=false，roundsPassed=6，14 个 primary steps。动作 hash `5e9256cdd2d8ddfb26cd78196deb424209307bb47b3ac1f3c71af346b2c26259`。
- demo/eval 通过当时的健康检查，不代表原版提议不会拒绝。`eval` 内硬编码 `mode:solo` 确实通过兼容适配变成真实 coop。

这不是批量训练，不代表 PPO 改善性能；NORMAL teacher 的胜利也不是训练后模型的胜利。当前未运行 normal-vs-mixed 正式训练或跨多种 seed 的统计评测。

## 诊断、资源和边界

早期真实 NORMAL 测试在 round 11 遇到原无出售布阵控制器提议超容量 move，实际返回 BOARD_FULL。新增的可见容量过滤在调用 engine 方法前拒绝该提议，并保留单独过滤计数；没有改游戏规则或吞掉实际错误。修复后 NORMAL original 和 weak 的真实终局成功完成，随后测试中错误地要求 heldout 必须触发 primary 强制 ready 导致断言失败；已移除该不成立的假设，最终使用上述 4 局完整 suite 验证。

原 768 MiB 软预算下的两次 learner 尝试分别在采样 RSS 775.68 / 746.25 MiB 时中止（后一轮提前保护阈值 736 MiB）；前一轮 50ms 采样存在超限检测延迟。用户随后明确将本地合计 RSS 上限提升到 1.2 GiB、累计 sim 到 90 秒。最终 learner worker 全进程组峰值 **826.61 MiB**，耗时 **33.443 秒**，产生 **4,690,538 bytes**（含 resources 记录）。所有产物只在 `/tmp`。

包含失败诊断和启动开销的所有 simulation/check 调用累计墙钟 **82.384 秒**，这是累计 sim 的保守上界；六次检查目录合计 **4,706,394 bytes**。没有 SSH、后台批量训练、模型下载或依赖安装。

| `/tmp/coop-check-` 后缀 | 检查墙钟秒 | 结果 |
| --- | --- | --- |
| hrffmoi3 | 5.041 | 定位前的控制器错误 |
| n_t1qi1n | 5.658 | 精确记录 BOARD_FULL |
| fmm_el74 | 16.166 | 修复后终局完成，测试预算假设不成立 |
| 7ouzw7si | 18.054 | 4 局 engine 通过，learner 旧 RSS 预算中止 |
| thrqiwdp | 4.021 | learner 旧 RSS 提前保护中止 |
| p_scw_8h | 33.443 | 最小 learner 全通过 |

最后只进行不启动 Match 的容量过滤单测、manifest 检查及静态语法/diff/scope 检查。容量单测覆盖满部署位替补提议被过滤、合法替换仍传给 engine，以及异常时恢复 ps.move；它不替代上述真实终局证据。提交后停止写入，等待独立 review。
