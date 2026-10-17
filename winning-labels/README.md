# 存活通关队友的 shop-v1 标签

范围：从 `ce9c4c6f624b1093872b3192db95a122aafaf0e0` 独立增加本目录。只读原 `rl/`、`coop/`、`coop-replay/` 与固定游戏源码；不修改这些目录。不启动 Match/Battle，不调用 `newMatch/reset/step`，不 SSH、安装、训练或重新模拟游戏。

输入是已核实的自然合作 ABYSS seed 50003 完整 raw download，1,066 条 method transitions。**一局、两条相关的存活席位序列**，不能当两局或拆到训练/验证两侧。`episodeGroup` 是同一个原 trace SHA。

## 运行

在仓库根目录执行（只需已有 Node 与游戏源码）：

```sh
# 只读审计，不写任何文件
timeout 20s node --max-old-space-size=512 winning-labels/extract.mjs --audit

# 默认新建 winning-labels/output-<timestamp>-<pid>/，绝不覆盖
timeout 20s node --max-old-space-size=512 winning-labels/extract.mjs

# 可指定本目录下不存在的输出目录；父目录必须已存在
timeout 20s node --max-old-space-size=512 winning-labels/extract.mjs --out winning-labels/my-labels

# 全部验证；无真实游戏模拟
timeout 20s node --max-old-space-size=512 --test --test-concurrency=1 winning-labels/core.test.mjs winning-labels/integration.test.mjs
```

默认输入（路径相对仓库根目录）为：

- `../experiments/coop-training-win-50003.jsonl.gz`
- `../experiments/coop-training-win-primary-labels-reference.jsonl.gz`
- 游戏静态数据 `../Stronghold-Protocol`，必须位于 `6ab2468279e926d107788f63d7fe219b85f67b4a`，且 server/shared/data 无修改和未跟踪文件。

可用 `--raw`、`--reference`、`--game` 指定这些文件/目录的其他位置；这不是通用其他 seed/模式转换器。非空 DIY/stand-in 私有数据覆盖明确拒绝，避免误用基础 GameData。`--audit` 与 `--out` 不能并用。输出路径及其真实父目录必须在本目录，既存目录/文件/符号链接不覆盖；输入只读。校验失败时不创建标签目录。磁盘写入失败可能留下部分新目录，审阅时必须确认 `audit.json` 存在并核对其中所有文件哈希。

预算：每次处理检查 20 秒、峰值 RSS ≤768 MiB；上述 `timeout` 提供硬超时，V8 堆限制 512 MiB。输出总实际字节数 ≤5,000,000（gzip 文件的压缩大小）。压缩输入上限 5 MB，raw 解压上限 64 MiB，reference 解压上限 16 MiB；不打印大数组。

## 来源与重建边界

实际 raw 的调用前字段是 **`observation`**，调用后是 **`next_observation`**；`terminal_observation` 是事后终局，只用于校验/metadata。viewer 的 `beforeObservation/observation` 格式不接受。完整 raw 的 `seq` 是入栈顺序，按 `parentSeq` 构造连续子树：父调用前取特征 → 子调用前/后 → 父调用接受后更新。拒绝调用只保留入口 observe，不更新已接受动作计数。真实输入的 parentSeq 均为空；嵌套语义由合成测试独立覆盖。

`core.mjs` 复刻原 `ShopEnv.observe/installRecorder` 所需历史语义：`totals/rounds/byId`、`prepSteps/prepBudget`、piece `firstRound/refreshes/boardRefreshes`。刷新只累加调用前已存在的己方棋子，轮次变化重置暴露与 prepSteps，保留首次出现轮次与累计购买/出售计数。记录调用后的轮次用于该次计数。完整动作参数不充当特征；所有 raw 动作及接受结果保留在 dispositions。

候选动作直接调用**原 `ShopEnv.prototype.candidates`**，显式传入己方 observation 与只读静态 GameData 的最小上下文，没有环境实例或游戏对象。使用原 `makeSchema/encode`，不手写编码器。五类动作保持原顺序、价格、tier、golden、bonds、slot/uid 等全部字段；不把 reward、move、装备、resolveTemp 等伪造为购物动作。买卖绑定实际 slot/uid，ready 必须为 true，接受动作也必须唯一映射到当前合法候选。metadata 中保留候选列表、选中完整 candidate、索引及哈希。没有唯一映射时进入 dispositions，不伪造 label。

特征输入只来自该席位自己的调用前 public/own、此前自己的已接受调用与静态数据。子调用只能看到父方法尚未完成的真实当前视图和已完成的历史。其他席位 private、terminal、reference 均不提供队友特征。终局只决定哪些席位有资格进入 positive。保留终局及坏尾部检查，不在 learner 淘汰处截断。

两种明确不同的预算计数口径：

- **actual-primary-policy**：只有带实际 `policy` 绑定的 primary 根调用计一步，位于 recorder 的 after-observe 之后。`source=policy` 本身不充分：嵌套调用、外来奖励、controller、清理不能自动成为模型步。预算 ready 属于 controller。
- **observable-teammate-teacher**：假设 teacher 从该席位实际观察到的、已接受的 `partner-controller` 五类调用学习，每次计一步，位于 after-observe 之前。resolveTemp 子树中的清理不计 teacher 决策；其已接受 sell 仍进入出售历史。这是明确的可观察方法边界口径，**不是实际安装过 ShopEnv teacher，也不声称完全等同其 scheduler/清理计步**。不实施预算强制 ready、不改变行为。

primary 的每条重构 `{features, action}` 都与独立真实 env 逐 step reference 的 **68 条完整样本** 做无容差递归对照：global 为 5,267 维，每个候选 419 维，连候选顺序、mask、action 都必须完全一致。原 policy candidate 另作全字段绑定。失败报告包含 step、seq、首差路径、actual/expected；未完成全部 68 条不导出队友数据。reference 仅作校验，不复制为训练样本。末尾 primary 计数也与 raw terminal 严格比较；其 R11 淘汰后 RESULT 的轮次重置由最后事件的公开轮次投影，不读取未来私有特征。

## 文件与使用

| 文件 | 内容 |
| --- | --- |
| `samples.jsonl.gz` | 207 行纯 `{features, action}`，供原 `rl/learner.py::bc_update` |
| `labels.jsonl.gz` | 同序索引；原 playerId、seat、round、seq/parentSeq、source、实际 intent、所有候选及完整选中 candidate、计步口径、样本和候选哈希 |
| `dispositions.jsonl.gz` | 全 1,066 条，不丢尾部；包括接受结果、被排除席位、非购物、拒绝与不可映射原因 |
| `metadata.json.gz` | shop-v1 schema、rules、mode=coop、完整原终局与 header/provenance、四席胜负/淘汰轮次、历史计数、来源哈希、68 条校验证据 |
| `audit.json` | 汇总与各输出文件 SHA-256/字节数，最后写入 |

`metadata.terminal.summary` 使用原始 `learner/partner-1/partner-2/partner-3` IDs。header 保留原 payload（其 summary 是 viewer 别名），验证时将这些别名还原后与原终局完整逐字段比较。

供现有调用者读取的接口如下；本工具本身不导入 torch，不调用训练：

```python
import gzip, json
with gzip.open("winning-labels/my-labels/samples.jsonl.gz", "rt") as f:
    samples = [json.loads(line) for line in f]
# 在已有、schema/rules 匹配的模型流程中使用 L.bc_update(model, samples, ...)。
```

所有来源保留压缩文件 SHA-256 与解压 payload SHA-256；transition payload 另有 SHA-256。`traceSHA256` 是 raw provenance 声明的原训练 trace 哈希（`a1b997…e651`），**不是** raw download 的哈希；本任务没有再次读取远端原 trace，不能把该声明写成重新核验远端训练。静态加载数据和本实现源文件也有哈希，原 rl/recorder 文件同时核对基线与 capture provenance。

## 标签含义与限制

positive 仅 partner-1 的 93 条、partner-2 的 114 条：二者均个人 victory=true、alive=true、roundsPassed=14。learner、partner-3 个人失败、R11 淘汰、roundsPassed=10；两者完整终局 metadata 和动作 disposition 保留作对照，训练样本为零。团队 victory=true 不覆盖个人失败。

partner-2 终局为精锐羁绊 6、谢拉格 4，以及维观、灵巧、迅捷等组合；8 个部署棋子中 6 个 golden，另两个非 golden；不是 8 金，也不是“全 6 本家”。Boss 伤害为 **6,163,482**。以完整原 summary 的 lineup/bonds 为准，不从阵容叙述生成动作。

这些标签是自然胜局中存活队友实际被接受、当前合法的购物行为，不表示每个动作最优或因果上导致胜利。primary 的严格一致验证了本轨迹上的重构算法；队友独立 teacher 的 scheduler 状态没有被真实运行验证。单局有幸存者选择偏差，不能声称泛化专家、独立胜率、冻结 checkpoint 成功，或给父 `L.learner` 自动附加 expert 属性。原训练轨迹可能跨 PPO 行为版本。生产环境重训/新轨迹泛化均不在本任务内。
