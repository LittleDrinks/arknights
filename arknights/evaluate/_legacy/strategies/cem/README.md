# 黑箱 CEM 参数学习实验

从 `94fd852912a04710183c80c43550bdcd70e658be` 的独立 `exp/cem` worktree 实现，新增文件仅在 `cem/`。只依赖 Python 3 标准库、Node 22+ 和 Linux `/proc`；无安装、SSH、子 agent。游戏目录只读。

动机是已有 PPO 262144 动作、12 个手选 profiles 均未取得 ABYSS 胜局（用户提供的前序结果，本实验没有重测）。CEM 提供不同的优化算法：学习现有启发式的参数，不保证改善，也不产生 PPO 模型或神经权重。CEM 外层标明 `searchLearning` 和 `modelKind: learned parametric heuristic`；复用 policy 的 `trainedModel: false` 元数据不变。只有成功训练并冻结后，才有可称为搜索所得参数的 profile。

## 运行

在实验代码根目录执行；默认只显示计划，**不写文件、不运行游戏**：

```sh
python3 -B cem/run.py
python3 -B cem/run.py --mode smoke
```

默认计划：`16 candidates × 8 generations × 8 train seeds = 1024` 训练局，冻结后 `16 dev seeds × 2 arms = 32` 对照局，总分母 **1056**。本地 `--run` 训练即使缩小 population 也拒绝，因为固定八训练种子已经超出两局预算。

本地最多两局自然完整的等价 smoke（已在本次提交执行过，评审不需重复消耗游戏额度）：

```sh
python3 -B cem/run.py --mode smoke --run --out cem/runs/new-smoke
```

两局均用训练环境 seed `20017`：第一局无 policy loader，第二局 zero-change profile 经 `policy/register.mjs` 加载。它们只检验默认策略运行等价，不是 CEM 训练，也不是参数优劣评估。总模拟包络从第一子进程启动计时，7.5 秒停止派发/杀进程，预留至 8 秒清理；最多两局，无截断回合、无复活、无重试。未完成局保留 timeout/unrun。

**以下命令供 server 上人工启动；本实现不连接或部署 server。** server 默认诊断源为 `<root>/tools/clean-bandbench.mjs`，必须与本地已锁定的诊断源 hash 相同。代码树须包含本基线的 `policy/` 和新增 `cem/`。

```sh
python3 -B cem/run.py --server \
  --root /path/to/Stronghold-Protocol --out /path/to/new-cem-run --parallel 4

# 查看上述 dry-run 后，明确执行默认 1056 局计划：
python3 -B cem/run.py --server --run \
  --root /path/to/Stronghold-Protocol --out /path/to/new-cem-run --parallel 4

# 小规模算法接线检查：16 train + 32 dev = 48 局，仍只供 server：
python3 -B cem/run.py --server --run --population 2 --elite 1 --generations 1 \
  --root /path/to/Stronghold-Protocol --out /path/to/new-small-run --parallel 4
```

`--parallel` 在 server 为 4..16，训练时每 worker 跑一个候选的八局，**每局 botbench 永远 `--jobs 1`**，不嵌套并发。每个种子独立子进程，以保留中途失败前的完整原始记录。`--timeout` 是每局 wall timeout，默认 60 秒。server 总 RSS 默认 `parallel × 512 + 256 MiB`、输出默认 256 MiB，可用 `--rss-mib` / `--output-mib` 修改；本地上限为 512 MiB / 10 MiB，不允许上调。输出目录必须不存在；本地输出只能在 `cem/`。不支持断点继续或覆盖旧结果。

## 搜索契约

- 固定 `solo:ABYSS`、`rehearsal=0`、不强制 band。train **只能**是 `20017..20024`，每个候选、每一代相同八种子；不提供换 seed 的 CLI。dev `1..16` 只在训练完成、最优 profile 持久化冻结后运行一次，对比 frozen/default。val `90001..90016`、sealed `100001..100064` 没有执行入口；dev 也不能进入训练。seed 只传游戏环境，不进入 profile 或策略特征。
- 优化七个标量 `FOCUS_MEMBER/MAX_REFRESHES/MAX_PAIRS/BENCH_BUDGET/BUY_FULL_MIN/MERGE_HIT/PAIR_HIT`，以及 `LEVEL_TARGET` 三个类别（reviewed default/early/slow 单调曲线）。硬范围来自已锁 hash 的 `policy/lib.mjs` 私有 `PARAM_SPECS` 字面量读取，不 eval、不维护另一套数值范围。Python 校验后，每次游戏前仍经过 policy 自身 `parseProfile` 的严格 schema 校验。
- 标量归一化到 `[0,1]`，初始均值为 default，sigma 为 `0.25`。独立高斯采样后裁剪；整数 half-up，实数保留 8 位小数。搜索使用独立 Python `random.Random(314159)`，可改 `--search-seed`；游戏种子不参与搜索 RNG。随机采样全部在主线程、评测之前完成；并发完成顺序不影响选择。记录 Python/Node 版本和全部候选，跨版本不承诺位级 RNG 等价。
- elite 更新使用 `alpha=.7`：`mu ← .3 mu + .7 elite_mean`，`variance ← max(.05², .3 variance + .7 elite_population_variance)`。记录更新前后均值/方差/sigma；最小 sigma 防止静默坍缩。级别类别的平滑频率再与均匀分布混合：`p ← .05 + .85 × smoothed_p`，每类概率至少 `.05`。
- 候选必须八局全部自然结束且无引擎/格式/来源错误。失败、超时、不完整候选 `fitness=null`，不能成为 elite 或 best；某候选失败后其余种子明确 unrun。有效候选少于配置 elite 数时整代拒绝更新，停止训练，不冻结、不跑 dev。正常败局是有效 `victory=false`，绝不当作缺数据丢弃。
- 选择严格按 **完整 victory 数优先**，其次 `mean(roundsPassed + bossDeductedFraction)`，平局按 candidate ID。`bossDeductedFraction=(maxHp-hp)/maxHp` 只取终局诊断中的实际血池；未到 Boss 为 0。若进 hidden，诊断表示最后的 hidden 池，不累加或猜测已通过的池。rounds 和 Boss 比例仅是**辅助 fitness，不是通关率**。不读 activatedLayers、名义 bossDamage 或层数选优。跨代保留最优已评测候选，平分保留更早候选，不伪造“均值 profile”成绩。
- 训练结束才冻结 `profiles/frozen-best.json` 与其 SHA-256，之后 dev 只评估，不能重选、调参或续训。0 胜就报 0；不完整 dev 同时展示完成分母和计划分母，不将未跑局解释成已观察败局。

## 来源、产物与失败

`pins.json` 锁定基线 policy lib/register/loader/provenance/rules manifest 和诊断 wrapper；复用 `verifyRules` 核对 486 个规则/数据/package 文件及其树 hash，并检查原始 botbench hash。每次游戏前后、代际、冻结前、run 最终（含失败）验证来源。CEM 自身文件运行开始时记录 hash，游戏前后与阶段边界检查漂移。

唯一修改是 `policy/register.mjs` / `policy/loader.mjs` 对固定 `server/match/bot.js` 的内存常数替换。诊断源来自 `../preview-tools/clean-bandbench.mjs`，server 可更换路径但不可更换内容。清空继承的 `NODE_OPTIONS/NODE_PATH/ARK_*`、teacher/stacking runtime 和动态链接器注入变量，仅显式设置本次所需环境；不串 buildloader，不改敌人、钱、牌、层、商店、seed 或规则。实验不是用于防御恶意并发文件攻击的安全沙箱。

输出包含：

| 文件 | 用途 |
|---|---|
| `manifest.json` | 启动前即持久化完整计划；每局 unrun/running/complete/failed/timeout/interrupted、命令、原始文件 hash、所有阶段分母、来源/规则 hash、cleanup 和资源观测 |
| `profiles/*.json` | policy 严格格式的实际参数；无神经权重 |
| `candidates/*.json` | 参数、profile hash、资格、双分量 fitness |
| `history.json` | 每代采样候选、更新前后分布、elite；失败代也保留 |
| `best.json` | 已观察最优参数与 `frozen` 标志；未完成训练的 provisional best 不能当冻结模型 |
| `raw/*.json` | 每局未删减的 botbench 输出；自然败局保留 |
| `raw/*.audit.json` / `*.log` | policy `trainedModel:false`、原始/变换后 bot hash、来源验证、子进程日志 |

记账区分 `observedVictoriesPerPlanned` 与 `completeWinRate`，并给出 `allPlannedComplete` 和状态计数。训练期总胜率是混合不同候选的采样统计，不是最终策略胜率。candidate 完成数不足不能参与排序。run 有任何失败/缺局返回非零；存在足够合格 elite 时允许排除失败候选继续，但最终标 `complete_with_failures`。冻结前 source drift 中止；冻结后 source drift 使整个 run 无效。

真实子进程使用独立进程组；timeout/SIGINT/SIGTERM 后 SIGKILL 全组、wait 回收并检查无存活成员。SIGKILL 主进程、断电、磁盘写满不保证清理或最后一次状态刷新；已经写出的完整计划仍保留分母，`running` 不能算完成。不会自动重跑未完成 seed。

RSS 每约 20ms 检查总进程组，预留 64 MiB；Node heap 限 128 MiB。RSS 监控不是 Linux 硬 RSS cgroup，瞬时超额仍可能发生；最终报告实测而不是声称硬隔离。本地顺序运行额外报告当前 Python `/proc/VmHWM` 加子进程 `ru_maxrss` 的保守上界。单个子进程文件有 256 KiB RLIMIT_FSIZE，输出总量定期检查，超预算失败；最终元数据也计入输出。server 应由部署环境提供需要的硬 cgroup 限制，本入口不安装或配置它。

## 验证与本次证据

以下均不运行游戏：

```sh
node cem/contracts.test.mjs /path/to/Stronghold-Protocol /path/to/clean-bandbench.mjs
python3 -B -m unittest discover -s cem -p 'test_*.py' -v
```

JS 契约核对真实 pinned source/default 字节等价、严格 schema 边界、实际 loader 的仅常数变换、wrapper/注册器/规则内容与文件集漂移拒绝。Python 独立核对 CEM 数值闭式期望、确定性采样及 policy 接受、sigma/类别下限、失败候选拒绝、完整计划分母、真实 timeout/SIGTERM 清理、profile 漂移及冻结前禁止 dev。合成流程与故障夹具明确不算游戏成绩。

本次仅两局自然 smoke，见 `evidence/smoke/`：两个 seed=20017 结果完全等价，均 `victory=false`、roundsPassed=10、reason=eliminated；模拟包络 5.610 秒，采样总 RSS 峰值 258.18 MiB，最大游戏子进程 RSS 239.15 MiB，cleanup 已验证。未训练 CEM、未运行 dev/val/sealed；无改善结论。

**原始 smoke manifest 保持 `failed`**：两局及等价检查均通过，但旧资源上界错误地加了 `getrusage(SELF).ru_maxrss`，该值继承了启动器 pre-exec 高水位，误报约 863 MiB。无游戏复现实验证实 `/proc/VmHWM` 与其不同；已改用当前地址空间高水位，并加入回归测试。保留执行时 runner 原文 `evidence/smoke-runner-before-resource-fix.py.txt`（hash 与原 manifest 对应）。修复后没有重跑游戏；`evidence/verification.json` 独立复核原始输出，不能将其写成修复后完整 smoke 已运行。

独立审查后修复并发记账：每局先在局部 pending 对象完成 raw/outcome/audit/hash 校验，再持同一 RLock 一次发布到共享 rows；其他 worker 的 accounting/save 只能看到 running 或完整完成记录。新增双线程故障夹具在 validate_record 中暂停首局并让另一 worker 保存，确认不再出现 complete 缺 outcome 的 KeyError。15项Python测试通过，全部无游戏；原始两局smoke/failed状态不变，offline证据仅刷新当前runner hash。
