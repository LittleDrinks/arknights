# 真实 coop：冻结 heuristic 队友池

基线 `4f23f97`；游戏规则沿用 `rl/env.mjs` 的 `6ab2468279e926d107788f63d7fe219b85f67b4a`。只新增 `coop/`，不修改 `rl/` 或游戏源码。启动通过父类校验游戏 HEAD 以及已跟踪的 `server/shared/data` 干净状态。

这是 **frozen heuristic partner pool**，不是 FCP 学习模型快照，也不是完整 FCP。各策略部署期间不更新；多水平指不同运营预算/策略，不代表已经测定的能力等级。它提供 normal-pool / mixed-pool 训练和不同家族交叉评测的环境接口，不提供已经改善泛化或消除相互过拟合的结论。

## 使用现有 learner

从本工作树根目录运行。`--game` 是 Node 环境参数，须放在 `--env-command` 的引号内；现有 Python CLI 没有独立 `--game` 参数。

```bash
GAME=/absolute/path/Stronghold-Protocol
ENV_COMMAND="node coop/env.mjs --game $GAME"
# 若路径含空格，在 ENV_COMMAND 内也需为路径保留引号（Python 使用 shlex.split）。
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export COOP_POOL=mixed COOP_SPLIT=train COOP_POOL_SEED=1
unset COOP_TEAM

python3 -B rl/learner.py --env-command "$ENV_COMMAND" --seed 7 demo \
  --episodes 1 --difficulty NORMAL --out /tmp/coop-demo.jsonl

# 以下为未来实验命令；本次仅执行极小兼容性 smoke，不运行批量训练。
python3 -B rl/learner.py --env-command "$ENV_COMMAND" --seed 1001 ppo \
  --checkpoint /tmp/solo-bc.pt --out /tmp/coop-mixed.pt \
  --steps-per-stage 1024 --curriculum NORMAL,HARD,ABYSS

COOP_POOL=heldout COOP_SPLIT=eval python3 -B rl/learner.py \
  --env-command "$ENV_COMMAND" --seed 60001 eval \
  --checkpoint /tmp/coop-mixed.pt --episodes 1 --difficulty ABYSS \
  --trace-dir /tmp/coop-heldout
```

直接 JSONL 接口保留父类 `reset/step/demo/close`：

```json
{"cmd":"reset","seed":11,"mode":"coop","difficulty":"NORMAL","budget":128}
{"cmd":"step","candidateIndex":0}
{"cmd":"demo","seed":7,"difficulty":"NORMAL"}
{"cmd":"close"}
```

现有 `learner.evaluate()` 硬编码发送 `mode:solo`，因此这里将 `solo` 作为兼容别名接受。**真正创建的 Match、四个玩家和返回的 `episode.mode` 始终是 coop**，不是拼接三个 solo 轨迹。学习席位固定为 seat 0，另外三个 seat 都是真实的 `isBot:true` 玩家。

## 冻结 manifest 与配置

`pool.json` 的 ID、family、split、strategy 和池成员均由 `pool.mjs` 严格校验，再递归冻结。禁止未知成员、重名、修改家族身份或向训练池塞入 heldout；动作噪声不构成新家族。改策略需要更新版本、身份校验与测试，记录代码 commit。

| ID | family | split | PREP 运营策略 |
| --- | --- | --- | --- |
| `original-v1` | `upstream-bot` | train | 原引擎完整 bot prep，rehearsal=0 |
| `buy2-v1` | `first-offer-buy2` | train | 当前合法购买列表中顺序买至多 2 次，然后整理并 ready；不刷新/升本 |
| `saver-v1` | `level-first-saver` | train | 若当前可负担则升本 1 次，之后最多买 1 个最便宜合法商品，然后 ready |
| `random-v1` | `random-legal` | heldout | 在当前 buy/sell/refresh/levelUp/ready 合法集合中均匀选取，最多 8 个决策，之后整理并 ready |

所有新策略共用固定部署/装备/免费奖励模块；表中上限是商店自主决策数，不包含整理和奖励领取。固定控制器可能领取免费合并奖励，因此 buy2 并不意味着整轮只能获得两个棋子。原 bot 家族保持原生行为。

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `STRONGHOLD_GAME` | 父环境默认相邻游戏目录 | 可由 `node ... --game GAME` 覆盖 |
| `COOP_POOL` | `mixed` | `normal`=original；`mixed`=original/buy2/saver；`weak`=buy2/saver；`heldout`=random |
| `COOP_SPLIT` | `train` | `train` 禁止 heldout；`eval` 允许所有已定义池 |
| `COOP_POOL_SEED` | `1` | 严格的 1..4294967295 整数种子 |
| `COOP_TEAM` | 未设置 | 可选，按 seat 1/2/3 固定三个成员 ID，必须全部属于选中池；空字符串是错误 |

例如 `COOP_POOL=mixed COOP_TEAM=original-v1,buy2-v1,saver-v1` 保证每局有原 bot 和两个弱策略；不设置 `COOP_TEAM` 时，每个席位在池内独立均匀抽取，允许重复，因此 mixed 某局也可能全抽到 original。抽取在 reset 时进行，整局冻结。相同 game seed、pool seed、配置不依赖 reset 次数或进程历史，结果可复现。

伙伴选择及 random-legal 决策使用单独的 SHA256 域分离种子和本地 xorshift32 状态，绝不调用 `m.rng*`。原引擎的购买、布阵、draft 和战斗仍会按原规则推进自身随机流；不同队友策略因此可改变后续局面，不能把“专属选择 PRNG”误读为整个比赛随机流恒定。

`episode.partners` 包含 manifest 版本/SHA256、pool、split、pool seed、固定组合和实际席位家族；现有 learner 的 episode 输出与 eval trace 会保留这些字段。**现有 PPO checkpoint 不保证保存环境配置**（尤其未完成任何整局的更新），开始正式训练时还须保存命令、上述变量、工作树 commit 和 pool.json 的副本作为 run manifest。配置不是防恶意使用的权限系统：手动把 `COOP_SPLIT=eval` 用于 PPO 仍会造成 heldout 污染，实验驱动必须禁止这种组合。

## 复用及可见性边界

`CoopEnv extends ShopEnv`，直接复用 `observe/candidates/step/installRecorder/demo/features`。`ShopEnv.reset` 只支持 solo，并且在策略模式把全局 `scheduleBotPrep` 设为空，因此本实现只小复制 reset 编排和状态初始化。coop 用按席位分发替代全局禁用：primary 在非 teacher 模式绝不进入原 bot prep；original 队友仍走原 `scheduleBotPrep`；新策略走每轮一次的受守卫回调。primary 先做决策，ready 后虚拟调度器推进队友、战斗、下一 PREP。primary 淘汰后继续真实队友比赛直到完整终局。

训练/推理输入仍是自己的 `privateView`、当前 `publicView` 及自己已观察到的历史；不附加其他玩家 privateView、未来商店/战斗、RNG 状态、共享库存隐藏计数。原游戏在自己的 privateView 公开的 bossMate 棋盘保留。`features.mjs` 和 schema 完全不变，因此 solo BC 可直接热启动，但同一 schema 不代表 solo/coop 分布相同；当前编码器也没有额外队友身份/队友棋盘 embedding。

新策略的商店选择只看自己的当前合法候选；候选由父类逻辑产生，执行使用真实 `ps` 方法。布阵/选秀/装备是明确的固定 upstream 辅助模块，不把整个原 bot 宣称为隔离的纯 observation policy。original bot 仍使用其原生引擎启发式（包括内部估值），这些内部信息不会作为 primary observation/features 输出。

固定部署模块沿用 `rl/controller.mjs`：禁止自动卖棋、普通装备、免费奖励、必要时真实 `resolveTemp`，不会创建资金、物品或战斗结果。可见容量过滤保留：无出售布阵器在棋盘满且无法撤回旧棋子时会提议超出部署容量的 move；`controller.mjs` 在调用引擎方法前过滤并返回明确拒绝，计入终局 `controllerBlockedMoves`。它不是成功动作、不进入动作轨迹。

原 bot 会尝试被引擎拒绝的操作；**不承诺没有拒绝**。`rejections.mjs` 按固定规则版本核实过的方法/错误码分类，并比较调用前后的内部状态（所有玩家的可变字段、共享库存/债务、UID、RNG、调度队列和 dirty 标记；不含规则引用、方法及惰性 deploy-map 缓存）。原版队友/teacher 的已知纯 validation 拒绝只记 diagnostic；固定原版 arrange 的 move 仅对 `BAD_TILE/BOARD_FULL` 使用此规则。调用过程中若进入 `dispatchItem`，即使回滚后状态相同也不豁免。未知拒绝、状态变化、异常，以及 primary 学习候选真正拒绝、自写 weak 策略操作错误仍使整 arm 失败。原版 `tryDo` 会吞掉异常，因此额外锁存 fatal 状态，不能靠捕获异常继续提交 rollout。

计数不清零、不回滚、不把拒绝伪造成成功：`controllerErrors` 保留实际 primary 控制器拒绝总数，只有被确认的部分另记 `controllerDiagnosticErrors`；健康检查要求两者相等且没有 fatal。每席 `partnerAudit.rejected` 和总 `partnerActionErrors` 保留全部队友方法拒绝，`rejections` 保存 round/phase/method/args/result/diagnostic/reason；诊断拒绝不进入表示无效 rollout 的 `actionErrors`，不进入成功动作计数/hash。容量前置过滤仍单列，不冒充实际 engine 拒绝。

`partnerAudit`、`partnerActionErrors`、`rejectedActions` 和 `controllerDiagnosticErrors` 仅在真实终局返回；不中途返回队友私有操作参数或商品身份，也不进入 observation/features。真实 `onEnd` 才设置 done；reward 仅来自完整 `summary.victory`，赢 +1、输 -1，非终局为 0。结束结果必须有 4 个玩家、boolean victory、无 summary errors。超预算截断、层数、loss 或参数变化都不是胜利。schema/checkpoint 格式保持不变；终局同时将诊断附加到 `episode.diagnostics`，现有 learner 的 `episode_result` 会原样保留它，批量产物因此包含拒绝计数和原因。非终局 episode 不含该字段。

## 后续交叉实验

从同一个 solo BC checkpoint、相同训练 seed 列表/难度/步数/primary budget 出发，分别用 normal 和 mixed 池训练；可固定 mixed 三家族各一席，或预先声明独立抽样。模型冻结后，用不与训练重叠的评测 seeds，对两个模型各执行以下四列：

| 评测列 | `COOP_POOL` | `COOP_TEAM` |
| --- | --- | --- |
| original | normal | original-v1,original-v1,original-v1 |
| weak buy2 | weak | buy2-v1,buy2-v1,buy2-v1 |
| weak saver | weak | saver-v1,saver-v1,saver-v1 |
| 新家族 heldout | heldout | random-v1,random-v1,random-v1 |

评测全部设置 `COOP_SPLIT=eval`，两个模型使用相同 game seeds 与 pool seeds；只汇总通过上述健康检查的完整 `summary.victory`，同时报告 diagnostic 拒绝、局数和不确定性。heldout 不用于训练、选 checkpoint、调策略参数；当前一个新家族的 smoke 只证明接口，不证明家族泛化。无需 SSH，本次未做这些批量实验。

## 受限检查

```bash
python3 -B coop/check.py --only rejections --seconds 8  # 拒绝分类与真实终局可见性
python3 -B coop/check.py --only engine --seconds 17    # 原有固定 seed 引擎 suite
```

监控器默认和最高单次上限均为 30 秒，RSS 在 0.90 GiB 提前终止以给 1 GiB 预算留余量，产物总计至多 20 MB；采样周期 50ms，属于监控预算而非内核硬限。Node 堆限制为 192 MiB。调用方还需累计多次检查的耗时；本次累计记录见 `VALIDATION.md`。不带 `--only` 会顺序执行 engine、rejections 和旧 learner smoke，可能触发时间保护；不要在已使用本次预算后追加运行。所有模型/trace/log 只写新建的 `/tmp/coop-check-*`；`coop/artifacts/`、`coop/__pycache__/` 也在本目录 ignore 内。Python 使用 `-B`，不在 `rl/` 生成 bytecode。

engine 测试固定真实 seed，覆盖 3 类 pool 的完整终局、weak 完整重放、主学习席位真实买卖、每个存活队友每轮调度并 ready、无越权经济动作、schema/可见性与 PRNG 隔离。旧 learner smoke 用 2 个真实 solo teacher 标签做 1 次 BC 更新，再进行 4 个 coop PPO transitions/1 次更新、1 次 coop demo 和 1 次完整 heldout eval；本次没有重跑它。完整输出和实测边界见 `VALIDATION.md`。

本次检查还发现一个未改动的独立限制：未指定 `COOP_TEAM` 时，`selectTeam` 在 `find` 内反复取随机数，weak/seed 11 可产生 undefined 成员。此次只改拒绝处理，服务器 normal/mixed 重跑应沿用已声明的固定队伍；随机队伍选择另行修复，不据此声称任意池配置均已健壮。
