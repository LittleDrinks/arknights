# BC state–candidate interaction ablation

独立分支 `exp/interaction`，起点 `654652785fec4c7c8e9fe3522875bef702183799`。仅新增 `interaction/`；不改 `rl/`、`parallel-rl/` 或游戏。不采集新标签、不做 PPO、不做课程扩展。实际 192 局 / 16,014 标签训练和批量评估交由主线程在 server 执行，本地仅生成小型合成权重。未 SSH、安装依赖、启动子 agent 或创建 goal。

## 要检验的假设

原 ActorCritic 的 actor 为 `h=tanh(Ws*s+bs)`，`score=w*tanh(Wa*[h,c]+ba)+b`。它并非数学上无法产生状态相关的候选排序；本模块不声称其表达不足已经被证明。假设仅为：显式 state/candidate 交互的归纳偏置，可能帮助当前 BC 数据学习候选偏好。

| arm | actor | 与原实现关系 |
| --- | --- | --- |
| `concat-baseline` | 原 state-Tanh + concat(raw candidate) + Tanh scalar | 继承原 ActorCritic，额外输入校验；契约核对初始化参数及输出逐位一致 |
| `interaction` | `h=tanh(Ws*s+bs)`；`z=tanh(Wc*c+bc)`；`score=w*(h*z)+b` | 分别编码后显式逐元素乘积，独立候选评分 |

两者只消费相同的 `features.global / candidates / mask`，候选位置和合法性不变。seed、终局、胜负、未来状态均不进入模型或 `bc_update`。数据准入层为核对身份/分区/完整性而读取这些元数据，然后只保留 `features, action`。原历史可见特征照常保留。

两 arm 共用同一内存样本列表，不做 wins-only 筛选、重采样或丢弃尾部；每次初始化后重新设定相同 order RNG，调用原 `bc_update`。每轮 shuffle、epochs、lr、batch、hidden、Adam、梯度裁剪均相同。训练清单记录输入 SHA、按原文件顺序的样本 SHA、所有 epoch 索引顺序 SHA、参数量、初始及最终参数 SHA、checkpoint SHA 和源码 SHA。契约通过 forward hook 独立记录实际 batch 顺序并核对。

默认对应原扩展设置：hidden=128、epochs=24、lr=0.0005、batch=64、init/order seed=101。state 和未参与 BC 梯度的 critic 初值复制为逐位相同；actor 使用 PyTorch Linear 默认初始化，结构不同，消耗 RNG 的位置不同，actor 初值不可能逐参数配对。interaction 少 `hidden²` 个参数（默认少 16,384），**不是等参数量实验**。共同保留 critic 以匹配原 API，单独报告 BC 活跃参数与未训练 critic 参数。合成 hidden=8 时总参数为 938 vs 874；这不是生产尺寸的参数量。

## 生产数据准入

已读取 `/tmp/run-bc-expansion.py`。它将 NORMAL/HARD/ABYSS 的旧 demo 行交错合并为 `all.jsonl`，不能按相邻行推断一局。入口按 `(difficulty, seed)` 聚合，并要求每个身份唯一、solo、budget 与 manifest 完全一致。训练支持三种难度的原标签；评估只支持 NORMAL/ABYSS。

生产 CLI 必须提供经主线程核对的 `interaction-dataset/v1` manifest：

```json
{
  "format": "interaction-dataset/v1",
  "recordFormat": "legacy-demo",
  "split": "train",
  "schema": {"version": "shop-v1", "ids": ["从原header完整复制"], "bonds": [], "bands": []},
  "rules": "6ab2468279e926d107788f63d7fe219b85f67b4a",
  "teacher": "upstream-bot/rehearsal=0",
  "datasetSHA256": "实际all.jsonl的64位SHA256",
  "sourceSha256": {"NORMAL": "原来源文件SHA256", "HARD": "原来源文件SHA256", "ABYSS": "原来源文件SHA256"},
  "sampleCount": 16014,
  "episodeCount": 192,
  "episodes": [
    {"seed": 12345, "difficulty": "NORMAL", "mode": "solo", "budget": 128,
     "split": "train", "samples": 83, "victory": false}
  ],
  "provenance": {
    "collector": "原采集器版本/源码证据位置",
    "reviewedBy": "实际核对人或主线程标识",
    "trainingSplitEvidence": "证明这192局来源于训练采集、而非validation/test的原始计划位置及SHA",
    "terminalAdmission": "原collector在写episode前检查done及engine/action errors；当前仅继承旧episode元数据"
  }
}
```

上例仅展示结构，**不是可用 manifest**；episodes 必须列出全部 192 局，schema/哈希/身份/数量必须是真实值。主线程应基于已有采集计划审查 split，再对 server 文件索引计数；不能仅将未知来源的记录标成 train 作为证据。manifest 会原样写入 checkpoint 与训练清单，保留这项信任边界。无需下载或复制大数据到本地。

准入实际执行：

- 游戏必须在固定 RULES revision，server/shared/data 无 tracked 修改；manifest schema 与该游戏 data 派生的 `shop-v1` 精确匹配。维度核对为 `global=90+8*bonds+bands+13*ids`、`candidate=15+ids+bonds`。
- metadata 只能位于首行一次，schema/rules/teacher 一致；扩展 header 的 sourceSha256 若存在必须与 manifest 一致。每局、header、样本和全局 manifest 的显式非 train split 都拒收。未标分区的旧行依赖经审核的 manifest，不能独立证明历史分区。
- 全文件读到 EOF，**没有 max-samples 截断路径**；所有字节参与同一遍 SHA。要求末行换行、JSON 无重复键/NaN、每局有且只有一个终局行，不能终局后再补样本、重复身份、缺局或截断尾部。
- 每条 features 只能有原三个键；维度固定、数值有限、mask 必须 bool 且至少一个合法候选、action 必须为当前 mask 中合法的整数。所有样本都检查，不能仅校验前 16k 条。
- 每局标签数和总数精确核对、round 单调、终局身份/胜负一致、engine/action/controller errors 均为零；`samples <= steps <= samples + rejectedLabels`。原 `roundsPassed` 不包含被淘汰的一轮，标签 round 上限允许 `roundsPassed+1`。
- 强制原 192 局、16,014 标签和原 teacher；ABYSS 胜例必须为零。`--synthetic-small` 单独限定空 vocabulary、synthetic teacher、最多 128 标签/16 局、hidden<=32、epochs<=200，产物标记 syntheticOnly。

旧格式与 `rl/learner.py demo` 相同：metadata；含 seed/difficulty/victory/round/features/action 的 sample；含 `episode_result` 和 rejectedLabels 的 episode。旧 episode 不含 `done`、完整 summary、原始 observation；必须显式加 `--allow-legacy-metadata`，记录 `terminalTrust=inherited ...`。**不能声称独立验证了这 192 个原始完整终局或原始标签的实时可见性**。局身份、文件 SHA、mask、数量以及 EOF 完整性是本模块实际验证的内容。

可选 `recordFormat=full-terminal-v1` 保持相同 sample，episode 形如 `{"type":"episode","seed":...,"difficulty":...,"rejectedLabels":...,"terminal":完整原env.view响应}`。检查完整顶层终局字段、done、summary/player/identity、reward、空候选及 mask、全局特征维度、controller 和错误计数。仍然信任采集器的数据来源，不能凭 JSON 结构证明真实执行历史。

## 训练与专用 loader

以下由主线程在 server 工作区运行；所有输出必须放到该工作区的 `interaction/` 下，输出目录必须尚不存在：

```bash
python3 -B interaction/train.py \
  --data /data1/zsm/arknights/experiments/bc-expansion-v1/all.jsonl \
  --manifest interaction/artifacts/original-16k.manifest.json \
  --game /data1/zsm/arknights/Stronghold-Protocol \
  --allow-legacy-metadata \
  --epochs 24 --lr .0005 --batch-size 64 --hidden 128 \
  --init-seed 101 --order-seed 101 \
  --out interaction/artifacts/paired-16k
```

`--game` 使用 server 实际固定游戏目录，上面路径需主线程按现场调整。两个 arm 由一次 CLI 顺序训练；`train.json` 先写 running，发生异常保留 failed，不能将单 arm 成功算完整消融。没有 warm-start 或原 checkpoint 的自动迁移。

产物 `concat-baseline.pt` / `interaction.pt` 使用 `interaction-bc/v1`，包含架构名、版本、评分公式、dimensions、schema/rules、来源和训练记录。`model.load_checkpoint` 固定白名单，`torch.load(weights_only=True, map_location='cpu')`，严格 state_dict、尺寸/版本/参数量/有限值检查；没有任意类动态导入。两种 checkpoint 都故意缺少原 loader 的 `dimensions/model` 顶层键，因此 `rl/learner.py` 不能误加载，即使 baseline 的参数结构兼容也会失败。通用 PyTorch 文件不属于此格式；只使用可信来源的小型 checkpoint，weights_only 并非对任意恶意文件的资源隔离。

## Eval、预算和分母

默认 dev seeds 1..16 × NORMAL/ABYSS，greedy；不支持 validation/test seeds 或 HARD。可通过 `--seeds 1 --difficulties NORMAL` 做有界单局。只有本地 node 引擎入口，无 SSH/env-command。默认 prep budget=128，wall=9s、产物预算=9MB；这些保守默认不会保证一个批次完成。

server 批量由主线程明确设定其允许的较大预算，例如：

```bash
python3 -B interaction/eval.py \
  --checkpoint interaction/artifacts/paired-16k/concat-baseline.pt \
  --checkpoint interaction/artifacts/paired-16k/interaction.pt \
  --game /data1/zsm/arknights/Stronghold-Protocol \
  --wall-seconds 3600 --max-output-bytes 500000000 \
  --out interaction/artifacts/dev-16
```

此批量命令本地**未执行**。64 个计划项在加载 checkpoint/启动引擎前写入 `eval.json`。每个 checkpoint/难度的分母固定为 16；加载失败、超时、输出额度耗尽、错误终局和未执行计划都保留。`winsOverPlanned` 是 wins/planned，不是去除失败后的胜率。总状态 incomplete 时 CLI 退出码为 2。记录训练身份重叠 `trainEpisodeOverlap`；dev 不能包装成未见测试结果。

每次决策 trace 保存完整 features、候选、所选下标/动作、step/round，末条关联终局 SHA。真实终局保存**完整未删减的原环境响应**，包括 observation/history、summary、controller 和计数。只在终局落盘、校验通过、steps 与 trace 对齐时记 complete。遇到额度不足不会截掉终局后冒充 complete；已有部分 trace 标记为 partial。每局关联 checkpointSHA256、sourceSHA256、traceSHA256 和 terminalSHA256。source 是各 `interaction/*.py`、`rl/*.mjs`、原 learner 文件的 SHA 清单与聚合 SHA，另存 HEAD/base；终局 rules 提供游戏 revision。源码内容 SHA 才是未提交验证时的精确依据。

## 本地验证与限制

使用现有 Torch，无安装。`guard.py` 为外部轻量监督进程，约 10ms 扫描 `/proc`，对监督器、Python、node、瞬时 git 等全部后代的 VmRSS **求和**，超限杀掉记录到的进程（包括独立 session 的引擎）。输出预算按整个测试输出目录统计，保留报告空间；真实仿真设单次总 wall=6s。RSS 是抽样峰值，不是内核强制的瞬时峰值保证，实际最大采样间隔也写入报告。

```bash
python3 -B interaction/guard.py --out interaction/artifacts/contracts-new
# 可选：一次真实局；不要为了凑成功连续重试消耗本地仿真预算。
python3 -B interaction/guard.py --out interaction/artifacts/real-new --real-only
```

本次结果见 `evidence/`：

| 检查 | 结果 |
| --- | --- |
| 5 个合成契约测试（含多个拒收子案例） | 全部通过；两 arm 各 8 标签、80 个 update，未接触生产数据 |
| 原 baseline 对齐、变长 padding/非法 mask、非法标签、EOF/身份/终局准入 | 通过 |
| 实际样本顺序、BC 更新、同候选不同状态偏好翻转、两 arm 重载及原 loader 拒收 | 通过；偏好翻转两 arm 都能学会，不构成 interaction 优越性证据 |
| 缺 checkpoint 的默认评估计划 | 32 项均保留为失败，NORMAL/ABYSS 各 16 分母 |
| 最终合成测试进程树 RSS | 734,171,136 B < 1 GiB；单次输出约 118 KB |
| 一局真实 NORMAL seed 1 | 完整终局，失败；roundsPassed=3，4 个 policy steps，全部错误计数 0 |
| 真实评估预算 | 0.9863s（含启动/加载/清理的 eval 区间）；整个监督进程 2.7861s；RSS 726,798,336 B；输出约 206 KB |

真实局只用 hidden=4 的全零 interaction 权重，greedy 平局选择第一个合法候选。它验证了真实 Environment→模型→step→完整终局和 trace 管线，**不是 16k BC 结果，更不是提升证据**。没有 ABYSS 评估或胜例；原数据也没有 ABYSS 胜例。

必须保留的失败记录：首轮测试同时在父/子进程加载 Torch，RSS 抽样达到 **1,074,221,056 B**，超过 1 GiB 479,232 B，监控终止（尚未启动仿真）。因此不能称整个会话从未超限。改为同进程调用 CLI main、实际解析 argv 后，后续检查满足预算。最终合成及真实检查的最大采样间隔分别约 39.5ms / 45.3ms。原始超限报告保存在 `evidence/resources-initial-failure.json`。

`evidence/` 保留合成测试报告/参数记录、真实完整 terminal/trace/eval 清单和资源报告；小型 checkpoint 留在被忽略的 `interaction/artifacts/`，未提交权重或生产数据。生产 manifest 来源审核、server 16k 两 arm 训练、64 项 dev 批量评估均未执行。完成此提交后停止，等待独立 review。

证据源码摘要固定于实际执行时：真实局之后仅补充了 `check.py` 的合法 mask BC 更新断言，未修改模型/训练/评估/准入生产路径；旧真实证据保留原始 SHA，不用新摘要覆盖。最终合成运行的源码清单见 `evidence/synthetic-train.json`。
