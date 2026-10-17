# Survivor data-source diagnostic v1

仅 `survivor-study/`，基线 `23bdfc3051a5059b41c3ffb7dcb9ddf421b3a697`。
先 `train`，再由 main 的服务器 posttrain 流程显式调用 `eval --dev-only`。
本实现不自动采集、不做 PPO、不修改原 learner/coop/encoder/controller，不安装依赖。

这是**单一已见团队胜局的数据来源诊断**。两个幸存队友来自同一 `episodeGroup`，
不是两个独立胜局。survivor 样本有幸存者选择偏差；primary 是该局个人失败 learner 的
行为对照，不能称为正专家。原轨迹可能跨 PPO 行为版本，不能归因于单一 frozen 模型。
队友 `prepSteps` 沿用提取器的 `observable-teammate-teacher` 假设：按观察到且被接受的
队友购物调用计步，没有真实安装独立 teacher 去验证 scheduler/清理/预算计步等价。

输入接口来自独立工作树 `experiment-winning-labels/winning-labels/evidence/`，
其 `17225e4` 尚在独立 review。程序始终记录 `unapproved-independent-review-17225e4`；
audit/哈希/契约通过不等于输入已批准，也不代替其独立 review。这里不提供“自动批准”开关。
metadata 原有 provenance、limitations、完整终局、四席胜负和提取计数全部原样保留。

## 两个服务器命令

`--root` 是数据部署根，必须有 `Stronghold-Protocol/`；代码自动从 `--root` 或
`--root/experiment-code` 查找，歧义时传 `--experiment-code`。所有相对路径参数（包括
`--input-dir`）均相对 `--root`，也接受绝对路径。无开发工作树绝对路径硬编码。
先由 main 将完整 evidence 放到下例的 `--root/winning-labels/evidence/`，将 primary
参考文件放到 `--root/experiments/`；若部署在代码目录内，应明确传对应路径。

```bash
python3 -B experiment-code/survivor-study/run.py train \
  --root /data1/zsm/arknights \
  --input-dir winning-labels/evidence \
  --primary experiments/coop-training-win-primary-labels-reference.jsonl.gz \
  --out experiments/survivor-ablation-v1 \
  --parent experiments/bc-expansion-v1/all.pt

python3 -B experiment-code/survivor-study/run.py eval --dev-only \
  --root /data1/zsm/arknights \
  --trained experiments/survivor-ablation-v1 \
  --parent experiments/bc-expansion-v1/all.pt \
  --out experiments/survivor-ablation-v1-dev-eval \
  --workers 4 --timeout 300 --total-timeout 14400 --output-mib 32
```

两个 `--out` 都必须不存在（含符号链接）；不续跑、不覆盖。失败目录留作证据，下一次
必须选新目录。`--parent` 必须是 main 指定的 **bc-expansion `all.pt`**，程序记录其文件
SHA 与全参数 SHA，名称与 schema 检查不能替 main 证明文件来历。eval 强制使用 train
记录的同一 parent 字节，不接受替换模型。eval 无 seed/难度/池选择参数，不访问验证
90001、sealed 100001 或其他 seed，不按结果选择 checkpoint 或 seed。

## 训练与入场

| arm | 不同标签数 | 来源 | label draws | Adam updates |
| --- | ---: | --- | ---: | ---: |
| primary68 | 68 | 完整 primary reference | 6800 | 200 |
| survivor68 | 68 | 207 中固定 seat/round 分层抽取 | 6800 | 200 |
| survivor207 | 207 | 全部 positive，93/114 两席 | 6800 | 200 |

三组都完整复制 parent 的 `L.ActorCritic.state_dict()`（包括 critic），重新创建 Adam，
不继承 parent optimizer。只调用原 `L.bc_update(epochs=1, lr=.0003, batch_size=34)`；
原梯度裁剪 0.5 不变。每组索引按 `selected[i % n]` 循环到 6800，只有对象引用，
不复制大特征、不使用 tensor cache。68 标签各出现 100 次；207 标签中前 176 个出现
33 次、其余 31 个出现 32 次，完整展开索引存档。BC 的全局随机排列再打散这些位置。

survivor68：按 `(seat, round)` 分组，用 Hamilton 最大余数法按每组数量比例分配
68 个名额；余数平局按数字 seat/round 排序；固定 Python `Random(313)` 依次在各组
shuffle，无放回抽取。最终按原 label 索引排序。只读 seat/round，不读模型分数、
回报、loss 或终局表现择优；207 的所有标签都进入 survivor207。

模型构造后，每组重置同一 `torch.manual_seed(101)`。记录并验证原 BC **实际 indexed
读取序列**等于同一 6800 位置的 randperm、实际 draws=6800、updates=200 和 Adam
状态 step=200。critic 头参数不被 BC 更新；共享 state 表征会学习，critic 输出不保证不变。

入场读取到所有 gzip member 的 EOF 并校验 trailer/CRC，拒绝截断、坏尾部、空记录、
重复 JSON key、NaN/Inf。每个压缩文件 ≤5 MB、展开 ≤16 MiB。验证：

- audit.ok、四个输出完整集合的字节数/SHA、207/68 数量、1066 disposition 连续序号和计数。
- 原始 primary 压缩 SHA、payload SHA/字节数与 extraction metadata 完全一致。
- shop-v1 完整词表、原规则版本、静态加载数据 SHA、模型维度、有限特征、合法 mask/action。
- 每个 sample SHA、候选 SHA、顺序/选中 candidate、raw disposition、seat/round/计步绑定。
- positive 仅 partner-1=93、partner-2=114；四席终局、header/terminal 完整 summary 对照，
  seed=50003、coop/ABYSS、steps=68、无错误，两个 survivor 的 individual outcome 均胜利。
- metadata 的 capture sources 对应基线 source pins；代码原文件、全游戏规则树和静态数据
  在运行前检查，运行后再次检查。原始 raw capture 未再次读取，其哈希明确标为提取器声明；
  `traceSHA256` 也不是这次重新核验远端训练 trace 的证明。

模型只接收键集合严格为 `{features, action}` 的样本，features 只允许原三字段。
source、终局、labels、selection、limitations 均走 metadata，不拼进特征。特征是否忠实
重建仍依赖独立 extraction review；这里的结构和哈希检查不声称重新重建了 raw。

输出 `primary68.pt`、`survivor68.pt`、`survivor207.pt` 为原 L 标准 checkpoint（含 optimizer
与 torch RNG）；逐个回读比较全 state_dict。`train.json` 保存 parent/三模型文件 SHA、
全参数 SHA、raw input 集合 SHA、source/config SHA、实际预算、平均训练 NLL、训练前后
所选不同标签上的 greedy accuracy/NLL。`selection.json` 保存三个选择索引、各层名额和
epoch 顺序；`input-metadata.json` 保存全部输入元数据、完整 labels 和审计，记录自己的
SHA。这些训练拟合指标不是评估胜率。异常写 failed；仅 complete 可进入 eval。

## 有限 dev-only eval

固定 parent + 3 arms × 2 pools × seeds 1..16 × ABYSS = **128 games**。
learner 固定 seat 0，coop，budget 128，CPU frozen greedy；不调用学习或采样。

| pool 名 | COOP_POOL | 固定三队友 |
| --- | --- | --- |
| original | normal | original, original, original |
| mixed | mixed | original, buy2, saver |

始终 `COOP_POOL_SEED=313`、`COOP_SPLIT=eval`。normal/mixed 均是已用家族，**不是
heldout families**；这 16 个开发 seed 也不承担 sealed 或未见 seed 的泛化结论。
复用未改动 `coop/env.mjs --game ...`、原 schema、原五类候选空间及当前可见 features。
环境自带的已证明纯 validation diagnostic 保留原计数；其它 engine/action/fatal/controller
异常均 failed。不中途把队友诊断/终局注入 actor。

开始环境前写不可变 `manifest.json`：128 项初始均 unrun。`summary.json` 每次汇总保留
全部清单，单 cell 分母恒为 16，总分母恒为 128。failed/unrun 不算已完成胜利、也不从
分母删除；分别报告完成数/失败数/未运行数、team wins 和 learner individual wins。
执行失败、超时、预算用尽均非正常输局。全局 source/model drift 使结果失效。

每局目录：`protocol.jsonl` 逐字节保存所有原始 stdout 响应；`actions.jsonl` 保存每个
决策的**全部候选**、mask、选中索引和完整动作；`terminal.json` 是未经改写的完整终局
响应（校验失败的终局也保留）；`stderr.log` 保留错误；`result.json` 保存文件 SHA、
seed/mode/difficulty/seat/steps、错误与 EOF 状态。terminal summary 四席、individual outcome
与 team victory 分开校验。成功还要求 close ack 后无额外数据、正常进程退出和 stdout/stderr
真正 EOF；不能在 learner 淘汰时提前结束。无 terminal 的失败自然没有 terminal 文件。

最多四个 CPU 游戏并发、每个 torch 运算线程数为 1。每局默认绝对 300s（上限 600s），
整批默认 14400s（上限 21600s），每局至多 4096 policy steps。stdout/stderr、actions 与
terminal 复制共享默认 32 MiB/局的字节预算（上限 64 MiB），总协议证据最多 128 倍该
预算；result/summary 的有限元数据另计。超限时保留已经写出的部分，不无限收集错误输出。
调度剩余未运行项仍占分母；最终状态有 failed/unrun 就返回非零。kill 负责清理私有进程组。

## 本地验证边界

```bash
timeout 20s python3 -B survivor-study/check.py
```

仅临时小维度合成契约数据、原 L 的 600 次合成更新及 Python 伪协议进程；不读取生产
样本、不导入真实游戏、不 SSH、不安装、不调用子 agent/goal。监督器限制整个测试子树
合计 RSS ≤1 GiB、时间 <20s。覆盖哈希/EOF/元数据篡改、选择确定性、同起点/同顺序/
真实 draws/updates、checkpoint 回读、固定调度/失败分母、未改写终局、错误/超时/超预算
与坏 EOF。实际训练、实际 128 局评估和服务器部署均由 main 后续执行，当前未运行。
