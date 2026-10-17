# 当前状态 DAgger 式弱教师纠偏

只新增 `dagger/`。复用 `rl/env.mjs` 的真实 `ShopEnv`、特征编码、固定 controller 和 `rl/learner.py` 的 `model.act` / `bc_update` / checkpoint 格式。游戏规则固定 `6ab2468279e926d107788f63d7fe219b85f67b4a`，启动沿用原环境的 HEAD 和规则工作区检查。没有修改 rl/game，没有依赖安装、SSH、子 agent 或 goal。

这是 **weak heuristic teacher**，版本 `weak-visible-heuristic/v1`；不是 expert、训练模型或原 bot 的精确复刻。用户给出的既有结果（原 bot NORMAL 35/64、初版 BC 2/16、PPO 0–4/16、ABYSS 全零）是动机，没有在本次重测。本实验只证明当前状态标注→BC 微调闭环；没有 ABYSS 胜例，也没有性能改善证据。

## 数据与行为边界

`teacher.mjs:query(observation, candidates, mask, records)` 是纯函数，只读自己的 `own`、`public`、已经观察到的本回合刷新次数，以及投影后的静态棋子/盟约 records。没有 Match、PlayerState、pool、RNG、未来商店或私有计数器引用。没有调用 `buyScoreOf`、botPrep、展示快照 clone 或探索性多步动作。规则 SHA 固定静态 records，teacherHash 是教师源码的 SHA256，manifest 另存环境、features、controller 和 Python 代码 hash。

- 买入按同 ID 副本的合成机会、缺人、静态 tier、五个预设核心、盟约下一档接近程度及价格评分；盟约分只是部署前近似，不能保证触发效果。
- 升级目标为 `min(maxLevel, 2 + floor(round/3))`，保留购置资金；刷新优先级受本回合已观察刷新次数（最多优先两次）及储备金约束；ready 可保留资金。
- 教师保护所有场上棋子、核心、精锐和带装备棋子；其余手牌/临时区溢出棋子才考虑出售。若合法动作全被教师保护规则排除，查询明确报错并保留错误记录，不伪造标签。此罕见阻塞分支没有自然局覆盖。
- 同分选原候选中第一个。`choose()` 与 wrapper 直接使用同一 query；真实单步测试核对执行的 type/slot/uid/id。

这些是**教师偏好**，不会过滤模型候选或覆盖模型动作。本实现未加入可选 safety subset，所有 manifest 明示 `safety: off`；模型仍能卖空场面、空手 ready。原 rl 的 PREP 动作预算（默认128）和固定 controller 原样保留，不冒称它们是新 safety。控制器标识：`upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1`。botPrep 禁用；部署、普通装备、免费奖励及分队/机变仍由原固定 controller 负责。

## JSONL wrapper

```bash
node dagger/env.mjs
```

每行 `reset` / `step` / `close` 请求对应一行响应。支持 Node `--game /absolute/path` 和原 `STRONGHOLD_GAME`。例如：

```json
{"cmd":"reset","seed":101,"difficulty":"NORMAL","budget":128}
{"cmd":"step","candidateIndex":0}
{"cmd":"close"}
```

正常响应保留原 env 的所有字段，附加 `teacherIndex`、`teacherVersion`、`teacherHash`、逐候选 `teacherScores`。候选顺序、features、mask 完全不变；终局 label 为 null。Python 仅把 `state['features']` 交给模型，新响应字段不会进入 features。拒绝 `demo` 和 `reset teacher:true`，不能用这个入口让原 bot 代打。

## 收集、聚合、微调

已有 Python/PyTorch/Node 即可。默认输出限制在 `/tmp`；服务器可用 `DAGGER_OUTPUT_ROOT=/data1/zsm/arknights` 明确指定项目输出根，拒绝覆盖现有文件；推荐 `.jsonl.gz`。seed 沿用 rl 的 `1..4294967295`，整个 episode seed 区间也校验。以下是本次实际执行的收集/训练参数；已有文件须换新输出名才能复跑：

```bash
python3 -B dagger/run.py collect \
  --checkpoint ../experiment-rl/rl/artifacts/bc.pt \
  --seed 101 --episodes 1 --difficulty NORMAL --budget 128 --beta 0 \
  --out /tmp/dagger-audit-20261018/rollout.jsonl.gz

python3 -B dagger/run.py train \
  --checkpoint ../experiment-rl/rl/artifacts/bc.pt --seed 102 \
  --data /tmp/dagger-audit-20261018/rollout.jsonl.gz \
  --max-samples 32 --epochs 1 --batch-size 8 \
  --out /tmp/dagger-audit-20261018/dagger.pt
```

每个状态先用 `model.act(greedy=False)` 采样，再以独立 Python RNG 按 beta 选择模型动作或**当前这一状态**的教师动作。`--beta 0.5,0.25,0` 表示各 episode 的混合概率，超出列表后重复末值；默认 `0`。beta=1 是纯弱教师行为，必须作为独立配置解读，不算模型自主表现。

每条 sample 保存 seed/difficulty/round/step、原 features/candidates、合法教师 action/teacherIndex、教师版本/hash/评分、modelIndex/logprob/value、executedIndex/behaviorSource/beta。教师标签在执行前写入；标签不是实际行为动作的别名。每局保留完整终局响应，包括原 summary、win、错误计数与动作历史。异常写 `type:error` 及可用错误细节；未完成局明确 `done:false, terminal:null`，不算失败终局或胜利。训练拒绝有 error 行的数据以及没有完整 episode 记录的所选样本。

下一轮将 `--checkpoint` 改为上轮 `dagger.pt`；train 重复 `--data old.jsonl.gz --data new.jsonl.gz` 即聚合历史 DAgger 标签。可额外 `--existing-bc /tmp/old-demo.jsonl` 导入原 `rl/learner.py demo` 格式，必须有匹配的 schema/rules 和无错误 episode 行。原格式没有完整 summary/done，因此旧样本的终局可信度继承原 demo 采集器，不能由本入口补证。旧数据标记 `existing-BC`，新数据为 `dagger-current-state`；每个来源单独记录文件 hash、metadata 和样本数，不混称同一教师。本次未导入旧样本，只热启动其已有 checkpoint。

`--max-samples` 是**每个输入文件**按文件顺序取的上限，默认32；不是随机抽样或验证集划分。重复 episode/seed 数据不自动去重，由操作者控制聚合清单。来源配置保存在 checkpoint metadata 和相邻 `.manifest.json`。热启动只加载权重，重新初始化 optimizer 和 RNG；不恢复中途对局。输出可由原 `rl/learner.py eval --checkpoint ...` 加载（原 CLI 的全局 `--seed` 须放 eval 前）；本次验证原 load/validate/act 接口，未额外运行更新后的完整评测。

## 有界本地证据

```bash
python3 -B dagger/smoke.py \
  --checkpoint ../experiment-rl/rl/artifacts/bc.pt \
  --out-dir /tmp/a-new-empty-dagger-smoke-dir
```

smoke 执行契约测试、一个完整模型自主局、最多32条标签的4个BC batch，并检查保存重载。24秒保护、1.1GiB进程树RSS、20MB输出保护；不进行批量胜率试验。外部传入更大模型或复杂 seed 时可能提前中止，不能保证任意配置均满足预算。RSS 以25ms间隔对 harness 与后代的 `/proc/*/statm` 求和，共享页重复计入，属于采样峰值。

2026-10-18 实测见 [evidence.json](evidence.json)，完整日志/manifest/数据/checkpoint 在 `/tmp/dagger-audit-20261018/`：

| 检查 | 结果 |
|---|---|
| 总墙钟（含 Torch 导入、测试和落盘） | 8.384 秒 |
| 进程树采样峰值 RSS | 743,153,664 bytes，约709 MiB |
| 大产物总量（含原始 evidence） | 4,582,378 bytes |
| 真实完整局 | NORMAL seed101，6轮，失败，49个模型动作 |
| 教师接管 / 预算强制ready | 0 / 0 |
| 引擎 / 动作 / controller错误 | 0 / 0 / 0 |
| 合法当前状态标签 / 与模型分歧 | 49 / 41 |
| BC样本 / 更新 | 32 / 4 |
| 参数 L2 变化 | 0.1073417068 |
| 原 learner checkpoint 重载 | 参数 hash、schema/rules、greedy动作一致 |

两项 Node 测试通过：真实状态四次教师单步执行、冻结输入、重复query、原features/candidates逐项相等、所有 Match RNG state及env view不变；另验合成优先级、mask、保护销售和刷新预算。测试只使用自然 reset/step，不把 snapshot 当 clone；评分单测是明确的人工 fixture，不计自然战绩。

本次完成一轮最小纠偏，没有验证多轮收敛、beta>0自然完整局、旧BC混合训练或更新后胜率。NORMAL失败不能解释为策略提升；没有ABYSS成功，不作强教师或通关承诺。seed101已用于本地收集/契约检查，seed102用于BC RNG，不作为未见seed证据。提交后等待独立review。

数据准入修复：DAgger 每条终局必须匹配 episode 编号、外层 seed/difficulty、内部 terminal.episode 及 summary.seed；同文件拒绝重复终局。读取完整文件并跟踪所有已观察 episode，即使超过优化样本上限，尾部未完成局仍拒绝，且要求 metadata 声明的全部请求局都有完整终态。
