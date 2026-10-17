# Wide development evaluation

仅新增 `wide-dev/`，base `ffbe8cc6b994f0676cb386b7c9f88d9e8281333f`。这是 **eval-only** 入口，没有 train、checkpoint 搜索、自动选优或续跑功能。

反复使用原开发集 16 个 seed 可能漏掉稀有胜局。本次预声明扩大到 **开发 seed 1..256**：不是 validation，也不能称 256 个全未见 seed，早期 probe 可能使用过其中部分。`90017..90048` 和 `100001..` 保留不用。

固定 `parent / steps3000 × off / deferred × original / mixed × 1..256 = 2048` 行。共享 **256 个 paired seed groups**，不能把 2048 局当独立样本。顺序为 seed、controller、pool、model，使两模型相邻；每个 model/controller/pool cell 分母 256。原样使用 coop、ABYSS、seat0、budget128、greedy、poolSeed313、teacher=False。

## main 部署和实际 CLI

需要 Linux、已有 Python 3.10+ / PyTorch、Node **>=22**。仅使用标准库及原 learner 已有的 PyTorch；不增加安装或下载步骤。

按本次提供的服务器状态，正在运行的 coop/prep source guard 绑定 Git HEAD `25c6af6`，现在不能改变它。本任务没有 SSH 或调整服务器。**main 应部署包含本提交的隔离 checkout，或等待那些运行结束后再同步代码和 `.git`**；不能只拷贝新目录、保留旧 HEAD 就声称部署完成。入口检查 actual CODE 是 base 的后代，并在首末核对 HEAD、文件集合和 SHA。隔离 checkout 可以指向同一数据 ROOT；训练证据内原有绝对输入/collector/source paths 必须继续可读且不变。

`--experiment-code` 可以明确指定代码位置，但启动 CLI 必须来自该 actual CODE。`--root` 是数据根目录，`--trained` 和 `--out` 相对 ROOT（也接受绝对路径）。输出目录必须全新且在源码、game、训练、父 checkpoint、输入目录之外；拒绝 symlink 和 `..`。候选训练目录 basename 固定为 `bc-fit-budget-train-v1`。

main 先核实已经完成的真实 F 训练及其证据，再填写明确的两个 SHA；下列占位字符串会被 CLI 拒绝，不能用假 train.json 代替训练。

```sh
ROOT=/data1/zsm/arknights
CODE=/absolute/path/to/deployed-isolated-checkout
PYTHON=/absolute/path/to/existing/python
CANDIDATE_SHA256=REPLACE_WITH_VERIFIED_steps3000_pt_SHA256
TRAIN_JSON_SHA256=REPLACE_WITH_VERIFIED_train_json_SHA256

"$PYTHON" -B "$CODE/wide-dev/run.py" eval --dev-only \
  --root "$ROOT" --experiment-code "$CODE" \
  --trained experiments/bc-fit-budget-train-v1 \
  --candidate-sha256 "$CANDIDATE_SHA256" \
  --train-json-sha256 "$TRAIN_JSON_SHA256" \
  --out experiments/wide-dev-v1 \
  --workers 16 --timeout 120 --total-timeout 2400 --output-mib 64
```

没有候选路径或训练步数开关：只加载该训练目录的 `steps3000.pt`。父模型固定 `ROOT/experiments/bc-expansion-v1/all.pt`，SHA256 固定 `f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a`。

## 准入和真实接线

`contracts_wide.py` 在导入前核对 `pins.json` 的依赖文件集合/SHA，直接导入原 `bc-fit-budget/contracts_fit.py`（F），直接加载 `prep-timing/run.py`（P）和 F 的只读训练证据辅助函数。没有复制模型、旧 runner、loader 或修改原模块 globals。新增 `workers.py` 的私有进程状态仅保存该 worker 自己的两模型。

`admission_wide.py` 复用 F 的 `model_guard`、`check_parent`、`game_identity`、`recheck_inputs`、原 `epoch_orders` 和 `critic_sha`。F 的 `evaluation.evaluate` 同时准入三个模型并启动旧调度，没有独立 admission API，所以这里单独实现只接受 steps3000 的证据核对，**不调用 F.evaluate，不触发原 P2/BC fit**。

准入要求明确的 checkpoint/train.json SHA、真实 `complete` F 报告与原 `finalChecks`、完整 28 个训练附件和 train.json 文件集合、全部 SHA/大小、相同 F source/config/game486/固定输入 manifest、498 labels / 6 games、15 epochs / 3000 updates / 102000 draws、连续 seed101 draw-plan、逐访问索引和标签/组计数、保存的 Adam step/参数映射/tensor、完整父 metadata/参数、逐 tensor 相等的 critic head。原 `L.load_checkpoint` 实例化 **ActorCritic[5267,419,128]**，标准七字段 checkpoint、CPU finite float32、schema/rules 均检查；两模型随后冻结。steps1000 文件仅作为完整训练证据按 SHA 检查，不加载、不评估。

输入沿用 F 已完成训练的准入证明：核对冻结 recapture manifest 和 input-metadata/inputSHA、原输入哈希及 raw 文件集合；**eval 不重新运行采样、recapture、BC 更新或整套 D.admit**。保留 `collector-source-trust-pending-main-independent-acceptance`，哈希不能升级为对 collector 语义的独立批准。报告或 SHA 字符串本身不能证明一次训练发生过，main 提供的两个 SHA 必须来自已核实的真实完成产物；CLI 继续检验完整证据和张量的一致性。

生产使用 `ProcessPoolExecutor(mp_context=spawn)`，最多 16 个长期复用的独立 Python worker。父进程首先完成全部准入；每个 worker 首次初始化时再次核对源码和 checkpoint，然后 **只初始化一次原 learner、加载两份冻结模型**。Torch intra/inter-op 各 1，父进程及全部后代继承最多 16 个可用 CPU 的 affinity。每局前后对**实际推理模型**核对参数 digest、类型、形状/schema、checkpoint metadata、冻结状态、线程数和 affinity；proof 含 worker PID、job、前后身份、checkpoint 和 source SHA。

实际调用在 `workers.perform_job`：

```python
P.game_job(row, model, checkpoint, game_path, out, deadline, byte_limit, pool_manifest)
```

不传 protocol 参数，因此使用原 `P.E.Protocol`。实际启动 **同一 CODE 的 `prep-timing/loader.mjs` + `coop/env.mjs`**；不是旧 F 的 E.game_job，也不是 gold loader。`row.model='steps3000'` 直接传入：原 `state_contract` 不限制 model name。原 P 对每个 response 校验真实 off/deferred controller version，terminal 调用保留原 `teacher=False` 默认语义，原 Protocol 要求 close ack、完整 EOF、无额外 stdout、零 exit/stderr，并在 finally 清理 Node 私有进程组。

父进程验证回传 proof 与准入身份、原 result 正文和原 payload seal 后，新增 `inference-proof.json`，再封存该局最终文件集合。**原 P 的 result.json 不改写**。proof 保存在 summary 的每局 evidence 中并有独立文件 SHA。只检查父进程未推理模型不足以获得信用。

## 预算、失败和最终信用

| 项目 | 上限及行为 |
| --- | --- |
| 并发 | 16 spawn workers；每进程一次 learner 初始化；无自动重启/重试 |
| CPU | 整个继承树最多 16 个可用 CPU；Torch/OMP/MKL/OpenBLAS thread1 |
| 单局 | 原 Protocol 截止不超过 120s；4096 policy steps；64MiB 全证据，内含 128KiB 原 result reserve 和 32KiB proof reserve |
| 整批 | CLI 绝对截止不超过 2400s；提前最多 60s 停止游戏，为 cleanup/末尾检查留时间 |
| 整批证据 | **64GiB 总量，含 32MiB metadata reserve**；不是 2048×64MiB，也不是生产 RSS 限制 |
| 本地合成 | 19s kill 阈值（<20s）、进程树 RSS≤1GiB、临时产物≤5,000,000 bytes |

所有 2048 行 manifest 在任何子进程（含 git/node）前写入并 fsync。每个 pending job 先预留整局 byte_limit；返回且可信封存后以实际字节结算、释放未用额度；剩余额度不足则保留 unrun。summary≤24MiB，manifest/admission 各≤4MiB。没有进度 summary 冒充最终成功。

原 Protocol 单局 timeout 的 killpg、实际模型后验 digest 和 seal 允许最多 3s 收尾，且受整批绝对截止约束。CLI 的 SIGALRM 覆盖准入、调度和末尾检查；截止后停止派发、终止仍未返回的 worker/Node，发布失败报告。操作系统 kill/reap、最终 JSON 写入有少量收尾开销；不会延长游戏或准入预算。Linux child-subreaper 接收 worker 崩溃遗留的独立 Node 进程组，避免只杀 worker 而遗留 Node。

正常返回且封存的事件失败、STOP、timeout 保留逐局失败证据和 2048 分母。future/initializer/进程池异常或 result/proof 不匹配则标记 `evidenceUnsealed=true`，停止新派发、收取 pending，随后整批失效；绝对截止不等待卡死 worker 无限返回。保留不可信文件，不补造 terminal，不把 unsealed 异常当普通输局。没有重复 BCfit/P2。

末尾核对 source/HEAD、父模型、candidate 文件与内存参数、实际 worker 每局 proof、输入、完整训练证据、486 game rule 文件和静态词表、每个 payload/result/control/proof 文件集合/大小/SHA，以及受保护路径和各级目录的 inode+symlink。任何漂移撤销全部完成局的团队/个人胜信用。失败 summary 通过最初打开的输出目录 descriptor 发布，替换输出路径不会把成功报告重定向出去。

`summary.json` 分开给团队/个人胜率；每个 pool/controller 的 paired 表分母固定 256。`baselineFailCandidateWin` **仅指两局均 complete 且 parent 未胜、candidate 胜**；技术失败→candidate 胜另列 `baselineTechnicalFailureCandidateWin`，incomplete pairs 单列，不冒充策略改善。全部 guard 通过而有正常技术失败/unrun 时，`integrity=verified` 但 `status=failed, creditGranted=false`，可信完成局统计保留。完整 2048 局且所有 guard 通过才 `status=complete, creditGranted=true`。

## 验证范围

```sh
python3 -B wide-dev/check.py
```

合成检查和源码接线审查记录见 [CHECKS.md](CHECKS.md)。本地不导入 Torch，不加载真实/合成 checkpoint，不训练，不运行 Node/game/static loader/Match/Battle，不 SSH、下载、安装或使用子 agent。

服务器提供的历史时间为 4-thread E.Protocol 的 96 局约 464s、串行 128 局约 1699s；本任务未复测。进程池避免不同 Python worker 共享 GIL，但初始化成本、CPU 竞争、JSON/特征校验、证据多次哈希及磁盘吞吐仍可能使 **2048 局无法在 2400s 内完成**。没有真实 checkpoint/game 性能试跑，不承诺吞吐或胜率；预算仍固定，未完成项留下且返回非零。是否另行设计更长预算由 main 决定，本 CLI 不接受 >2400s。
