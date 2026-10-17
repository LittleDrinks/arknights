# Natural teacher win BC

范围仅 `natural-win-bc/`，base `d8805de9ccc1d4d861fe4a60f67197442078e6d9`。
没有采集器、自动部署或自动评估步骤；main 完成生产采集、独立验收来源、冻结输入后才执行。
本地验证只用合成数据，未读取/下载生产样本，未运行真实 Match/Battle 或生产训练。

## main 的两个独立命令

`--root` 支持服务器现有 `/data1/zsm/arknights/game`。代码默认从 root 或 root/experiment-code
找唯一部署；可显式传 `--experiment-code`。相对路径均相对 root。两个输出目录必须不存在，
包括悬空符号链接；不续跑、不覆盖。Linux/Python 3.10+，使用服务器已有 torch/node。

```bash
# 下述 SHA 必须由 main 在采集进程结束、来源独立验收、输入冻结后填入。
python3 -B experiment-code/natural-win-bc/run.py train \
  --root /data1/zsm/arknights \
  --input-dir experiments/natural-coop-teacher-search-v1 \
  --manifest-sha256 <FROZEN_MANIFEST_SHA256> \
  --parent experiments/bc-expansion-v1/all.pt --parent-sha256 <DESIGNATED_PARENT_SHA256> \
  --out experiments/natural-win-bc-v1

python3 -B experiment-code/natural-win-bc/run.py eval --dev-only \
  --root /data1/zsm/arknights \
  --trained experiments/natural-win-bc-v1 \
  --parent experiments/bc-expansion-v1/all.pt --parent-sha256 <SAME_PARENT_SHA256> \
  --out experiments/natural-win-bc-v1-dev-eval \
  --workers 4 --timeout 120 --total-timeout 1100 --output-mib 64
```

parent 强制为 root 下 `experiments/bc-expansion-v1/all.pt`，SHA 由 main 明确指定，
不是仅看文件名。eval 必须与 train 的 parent 文件 SHA、全参数 SHA 一致。
不存在 seed/family/难度选择参数；train 固定 30001..31024，eval 固定 1..16。
不访问 90001/100001，也不提供 heldout-family 开关。

## 冻结入场和来源边界

读取的是相邻 `preview-tools/collect-natural-coop-teachers.py` 的实际格式，未修改该脚本。
`manifest.status=running` 或无 finishedAt 一律拒绝。冻结可以是 complete/incomplete/failed，
但必须完整列出按原顺序排列的 1024 个 training seed × normal/mixed = 2048 项。
complete 批次不得含 failed/not-run；失败行必须有 error，已运行行须有开始/结束时间。
不把“2048 个计划项”写成“2048 个有效完成局”。manifest 原字节保存为 `frozen-manifest.json`。

所有 complete artifact 必须通过，而非仅检查正例：

- 文件名绑定 pool/seed，compressed bytes/SHA 匹配，读到全部 gzip member 的真实 EOF，
  校验 CRC/trailer，拒绝截断、垃圾尾、多 JSON、重复 key、非有限数、缺失末尾换行。
- 原 demo 字段、teacher、shop-v1 schema、RULES 一致；全部标签维度/有限值/mask/action
  合法，round 为按序的 1..14 整数；accepted/missing/rejected steps 计数兼容原 recorder。
- 完整 terminal 的 episode/summary/observation learner seat、四席身份、各席 alive/victory/
  roundsPassed/eliminatedRound、team victory、reward、诊断镜像及 row 结果逐项核对。
  teacher 入场严格要求 summary、engine、action、controller、controllerDiagnostic、
  partnerAction 的错误计数均为整数零；出现错误的 complete artifact 会使整次训练失败。
- 固定 coop/ABYSS/budget128、train split、poolSeed313、三席 partner id/family、
  pool.json 的 JS-compatible SHA；manifest 中全体 rl/coop `.py/.mjs/.json` 的 capture SHA，
  以及唯一 collector 脚本 SHA 均与 `pins.json` 绑定。
- 游戏使用未修改的 `survivor-study.common.game_identity`：RULES HEAD、486 文件集合/逐文件 SHA、
  原静态 schema/data SHA。训练前后、评估前后重验游戏/代码和输入/模型不变。

failed 行有 artifact 时也验证 bytes/SHA/gzip/JSON，并保留完整原始 metadata；无 artifact 的
failed/not-run 仍保留整行。所有已读 raw 的完整 terminal/teacher/其他 metadata 写为
`audit-<pool>-<seed>.json.gz`，仅删除 samples 数组；负例标签不进入内存训练集，原 gzip
由 main 冻结保留，其路径/压缩与展开 SHA、大小留在审计中，未声称复制了全部负例标签。
`input-metadata.json.gz` 保留所有 2048 行、输入身份、正例 label 来源索引及审计文件 SHA。

原源码证据：`rl/env.mjs:97` 获取 before observation，随后候选与 encode(before, cs, schema)
产生 label，`original(...args)` 在其后执行；只有 result.ok 且有 label 才 push 到 demos，
否则累计 demoRejected。`coop/env.mjs` 继承该 recorder，并保留原严格错误检查。
本模块只检查已保存 features/action 的合法性，不能从只有这些字段的 raw 重放还原全部动作前
游戏状态。源码 hash 是身份约束，不是来源自动批准；报告与 checkpoint 永远标明
`collector-source-trust-pending-main-independent-acceptance`，来源信任由 main 独立验收。

## 正例选择和相同预算

positive 必须同时满足：complete、team victory、learner individual victory、alive、
个人 roundsPassed=14、rejectedLabels=0。dead-primary 的团队 carry 只留审计。
不硬编码已发现的 normal30072/82 或 mixed30122/85，也不据此跳过全批验证。

按 manifest 原序、每局原 label 序收集全部 positive labels。不同 seed 是不同 episodeGroup；
同 seed normal/mixed 共享一个组。正例局数、eligible 组数、实际训练组数分开记录，不把
两种 pool 视为独立样本。`round`、outcomes、source、group 全留 metadata，actor 只接收
原 `{features, action}`，features 只有 global/candidates/mask。

使用未修改的原 `L.ActorCritic`、`L.bc_update` 和 survivor-study 的 `Draws/accuracy`：
完整复制同一 parent（含 critic），新 Adam，cyclic draws=6800、updates=200、batch=34、
lr=.0003、epochs=1、maxGradNorm=.5；构造模型后重置 torch seed101，核对每一次实际
indexed 读取等于固定 randperm，并检查所有 Adam step=200。critic 头张量不变；共享
state 表征更新，所以不声称 critic 输出不变。

若 eligible 标签超过 6800，固定 cyclic 序列只训练前 6800 条，不伪称所有 eligible 标签
都被抽到。`selection.json` 保存 eligible 索引、全部 expandedIndices、epochOrder，
每个 seed 的 eligibleLabels/distinctDrawnLabels/draws，报告记录实际训练组。
这是 outcome-conditioned BC，有幸存者选择偏差；没有模型分数筛选或开发局挑 checkpoint。

输出原 L 标准 `naturalWins.pt`（optimizer、RNG、完整 metadata），回读核对全 state_dict；
`train.json` 记录 initial/final 参数 SHA、parent/checkpoint/config/source/input SHA、具体训练组、
标签数、selector、实际预算、前后 greedy label accuracy/NLL。拟合准确率不是胜率。
只有全流程成功且末尾 guards 通过才写 complete；任何失败目录保留，eval 拒绝 failed。

## 有限评估

固定 parent + naturalWins × original/mixed × dev1..16 × ABYSS = **64 games**。
不含 heldout-random-v1 或其他 heldout families，后续扩展由 main 单独实现/审核。
只调用未修改的 `survivor-study.evaluate.game_job` 作为单局执行/协议/EOF/terminal 原语；
不修改 TEAMS 或任何导入模块全局。eval 继承该原语对纯 validation diagnostic 的分类，
不把经证明的诊断记为 engine/action failure；teacher 入场的零错误要求更严格。

开始前持久化不可变 `manifest.json` 的 64 项 unrun 清单；`admission.json` 绑定来源/模型。
每局 protocol/actions/stderr/full raw terminal/result 按原实现保留，失败也保留已有内容。
没有 terminal 的失败不能补造 terminal；close ack 后必须真 EOF/正常退出。
`summary.json` 保留每个计划项；总分母恒 64、每个 model/pool cell 恒 16，分别列 complete/
failed/unrun、teamWins、learnerWins。全局 source/model drift 使已完成结果失效。

默认和上限均为 4 个单线程 CPU worker、每局 120s、协议输出 64MiB、整条 CLI 1100s。
整批预算内预留最多 30s 给 child cleanup 和末尾 source checks。每局最多 4096 steps，
最多 64×64MiB 协议证据；有限 result/manifest/summary 另计。耗尽时间仍保留未运行分母，
返回非零；没有无限重试。SIGALRM 约束 admission/训练/末尾 guards，原 game_job 约束
子进程并清理进程组；极短自定义 timeout 时系统调度/强制清理可能有短暂额外开销。

训练输入限额：manifest 8MiB，每 artifact 压缩/展开各 64MiB，总压缩 2GiB、展开 16GiB，
每局最多 4096 标签，positive 原 JSON 累计 128MiB/最多 200000 标签；超限拒绝。
训练审计写入最多 512MiB，单条展开审计 64MiB；parent 32MiB、输出模型 64MiB。
限额不是预分配；本地合成验证另受更小的 1GiB RSS/5MB 输出约束。

## 合成验证

```bash
python3 -B natural-win-bc/check.py
```

监督器对整个测试子树设 <20s、合计 RSS≤1GiB、临时输出≤5MB；结束清理临时文件。
验证全计划/冻结、SHA/EOF/CRC、终局/row/label 篡改、dead carry 排除、同 seed 分组、
原 BC 真实 200 次合成更新及 checkpoint 回读、64 项模拟调度、坏 EOF 原终局保留，
并用 Python 3.10 AST 语法检查。本机实际运行的解释器不是 3.10，未声称完成 3.10 runtime 验收。
