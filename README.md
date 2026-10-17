# ABYSS 实验分析

Python 3.10+ 标准库 CLI，接收上游 `tools/botbench.mjs --json` 的 JSON 数组。
唯一主标准是 ABYSS 终极模拟**完整对局 `victory == true`**；轮数、层数、道中存活与隐藏终局到达都不算通关。
本工具不运行游戏或训练，不提供模型。规则版本由实验 manifest 声明，工具不验证实际执行引擎的版本。

## 运行与复算

在本 worktree 根目录执行：

```sh
python3 analyze.py \
  --baseline ../experiments/planner-ablation-2026-10-18/r0.json \
  --candidate r1=../experiments/planner-ablation-2026-10-18/r1.json \
  --candidate r3=../experiments/planner-ablation-2026-10-18/r3.json \
  --manifest ../experiments/planner-ablation-2026-10-18/run-manifest.json \
  --out-dir observed-results
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

`--candidate label=file` 可重复；baseline 也支持 `label=file`，默认标签为文件名。
`--jobs N` 可覆盖 manifest；未知 jobs 也提示墙时不可直接比较。
`--train-seeds 1-64 --eval-seeds 65-128` 输出各分组胜率、Wilson 区间及配对结果；重叠会拒绝。
seed 分组仅为调用者声明，不自动证明未见性。不同队友策略/座位应分别运行分析，并在各次 manifest 中固定策略 hash 和座位，避免同 config/seed 重复。

输出 `report.json` 与 `report.md`。JSON 附输入、manifest 和分析脚本 SHA-256、复现命令、逐 seed 最小记录；保留原始 `ms` 精度以便重算均值。
同一工具、输入和命令重跑，除了 `generated_at` 外 JSON 相同。原始数组只读，不复制进 git。
测试 fixture 在临时目录生成并清除，都是合成数据。真实数据测试在原始文件不可用时明确 skip。

## 输入与质量规则

每行要求 `config` 字符串、整数 `seed`、布尔 `victory`。缺失或非法时丢弃并列明问题，输入元数据记录原始/有效/丢弃行数。
其他字段为 `stageId`、`bossId`、`errors`、`hidden`、`roundsPassed`、`bossRound`、`ms` 和可选 `lpLeft`。
非 ABYSS config 会产生警告，其 victory 不能解释为 ABYSS 通关。

- 同 config/seed 配对；重复键所有副本均排除，即使其中一行 victory 非法，也不能挑剩余副本配对。
- 缺少另一侧 seed、缺 stage/boss（包括两侧同时缺）、stage/boss 不同、非零/未知/非法 errors 均排除并列出。
- 汇总保留有效结果行中的错误和重复记录，供审计原始运行；配对使用合格子集。汇总不是自动清洗后的独立有效样本统计，剔除可能有选择偏差。
- hidden 缺失保留 unknown；错误缺失单独计数，不能用零错误解释。数据质量问题不会阻止生成审计报告；调用方应检查 `data_quality`，CLI 成功退出不代表数据合格。
- 没有预声明 seed 范围时，不能发现两臂共同缺失的 seed。manifest 可提供 `configs`、`seed_start`、`seed_count_per_mode_per_arm` 审计覆盖；还支持 `jobs`、`engine_commit`、`trained_model`。其他元数据原样记录。
- 当前内存加载整个 JSON 数组，适用于这批小数据；不是面向任意大输入的流式工具。

## 统计口径

每个 arm/config 报告胜/局、Wilson 95% CI、hidden 到达及其中胜局、败局平均失败轮、错误、整局墙时。
`hidden` 对应上游 `hiddenReached`，不是 `hiddenCleared`。失败轮是 `roundsPassed + 1`，仅在败局且有轮次时统计；上游隐藏终局失败轮数会被截到 bossRound，不能恢复实际隐藏终局轮次。
字段依据只读 `../source-evidence/tools/botbench.mjs` 与 `../Stronghold-Protocol/server/match/results.js` 核对。

配对差为 `(loss_to_win - win_to_loss) / n_eligible_pairs`；翻转明细保留 config、seed、stage、boss。
双侧 discordant-binomial/McNemar exact：令 `m=b+c`，`p=min(1,2*sum(comb(m,k),k=0..min(b,c))/2**m)`。
`m=0` 本工具约定返回 null/undefined，表示无不一致对、无法提供差异证据（有的实现约定 p=1）；绝非等效。
不报告把不一致对条件化区间冒充边际胜率差 CI。p 未做多重比较校正，仅供探索；small-N 必须结合原始计数。
Wilson 假设各 config 内 seed 为独立 Bernoulli 试验；不同 config 重用 seed 时合并检验禁用，合并 Wilson 仅为名义参考。
合作模式一次完整对局是一条记录，不把四名队友算四个样本。

## 已观测结果与边界

[真实报告](observed-results/report.md)：r0/r1/r3 每 arm 32 局（solo 与 coop4 各 16），总计 **96 局、0 胜**。
六个 arm/config 单元均 0/16，Wilson 95% 为 [0%, 19.36%]。
每个 candidate/config 有 16 个合格配对，无 loss→win，也无 win→loss，差值 0 个百分点，无不一致对。
hidden=0，errors=0，质量审计无问题。全零胜不证明策略等效；r3 轮数较高也不是通关提升。

| arm | solo 平均失败轮 | coop4 平均失败轮 | solo 平均墙时 ms | coop4 平均墙时 ms |
|---|---:|---:|---:|---:|
| r0 | 12.375 | 12.0625 | 1929 | 5886 |
| r1 | 12.25 | 12 | 3117 | 10288 |
| r3 | 13 | 12.5625 | 5328 | 19426 |

该批 jobs=4，墙时受并行负载影响；rehearsal=0/1/3 使用不同计算预算，不是等算力训练消融。
没有训练模型、未见 seed 留出证明或队友策略留出，因此不能声称学习进步或合作队友泛化。

另只读核对 `../experiments/band-screen-manifest.json` 的摘要：40 分队 × 8 seed = 320 局，0 胜、0 engine_errors。
其 SHA-256 为 `2152f40f414e79491908c701eda07094592d1b883c5913349e15821bf5c99643`。
这只是摘要核验，没有在此复验原始 320 局；不与 96 局混合配对、不制造逐 seed 记录。

## 后续实验矩阵

[experiment-matrix.json](experiment-matrix.json) 是设计配置，不是已接通训练器的运行脚本。
覆盖 BC、知识教师、混合叠层、弱队友、历史池、行为预测、站位预演的单模块与 full-minus-module 对照。
每项明确 `requires_trainer`、状态与空 checkpoint。除已有规则预演观测外，计划均未执行。
建议训练/验证/未见 seed 分割及独立队友策略、座位评测都只是预留方案，执行前应记录所有数据暴露与固定策略 hash。
