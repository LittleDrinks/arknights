# 同步并行 PPO（最小可审查版本）

基线 `0de8ae5cc1b876dcd5c929231208c78ffe180cef`。仅新增 `parallel-rl/`，导入原 `rl.learner` 的 `ActorCritic`、`Environment`、`gae`、checkpoint 保存/加载/校验和终局统计。不修改游戏、候选编码、固定控制器或奖励。

## 使用

需要现有 Python/Torch、Node 和原 ShopEnv 所要求的固定游戏规则 checkout，无新增依赖。Linux CPU 运行；进程使用 **spawn**，每个 actor 持有独立 Python/Torch 和 Node 环境，跨 update 保持进程及未完对局。主 learner 不启动自己的游戏环境。OMP/MKL 强制为 1，Torch CPU 线程为 1，其余外部环境变量原样继承。

服务器训练示例（本地未运行大训练）：

```bash
python3 parallel-rl/train.py \
  --checkpoint /path/to/validated-bc.pt \
  --out parallel-rl/artifacts/train.pt \
  --workers 8 --steps 12000 --rollout 128 --seed 1
```

`--steps` 是所有 actor 合计的真实 step 数；`--rollout` 是每 actor 每 update 最多采样的步数。末批平均分配余数，余数给编号较小的 worker；总步数小于 worker 数时允许零步 worker。`--workers 1` 使用同一条路径。每轮先广播同一冻结参数和版本，等待全部 actor 采样，再更新主模型。结果按 worker 编号、各自时间顺序合并，与 IPC 到达顺序无关。

`--config` 默认为 `rl/config.json`；支持其中的 `seed`，CLI `--seed` 优先，缺省为 1。其余 PPO 配置沿用原字段；隐藏层实际大小以输入 checkpoint 为准。`--difficulty` 默认 NORMAL。`--timeout` 是单次环境请求期限，默认 120 秒；`--batch-timeout` 是启动/采样同步屏障期限，默认 600 秒。任一 actor 错误、环境拒绝、engine/action error、进程退出、种子耗尽、超时都使整个 arm 失败，不排除失败 worker 后继续更新。

切换环境只需提供同一 JSONL 接口的 `--env-command`，按 shlex 分词、不经过 shell。例如：

```bash
COOP_POOL=mixed COOP_SPLIT=train COOP_POOL_SEED=1 \
python3 parallel-rl/train.py --workers 8 --steps 12000 \
  --checkpoint /path/to/validated-bc.pt --out parallel-rl/artifacts/coop.pt \
  --env-command 'node /absolute/path/coop/env.mjs' --mode coop

ARK_ROOT=/path/to/server-layout ARK_GAME_ROOT=/path/to/game \
python3 parallel-rl/train.py --workers 8 --steps 12000 \
  --checkpoint /path/to/validated-bc.pt --out parallel-rl/artifacts/reward.pt \
  --env-command 'node /absolute/path/reward/env.mjs' \
  --reward-mode round-progress+terminal-boss/v1
```

原 `rl/env.mjs` 本身只支持 solo；并行层不会让它凭空支持 coop。外部 coop/reward wrapper 必须实现相同候选及特征协议，每个 actor 每次 reset 都执行 schema、rules、特征维度校验。主 learner 在 optimizer 更新前重算当前版本 logprob，仅用于断言；PPO 分母始终使用 actor 采样时保存的 old logprob。

## GAE、种子及统计约定

- 每个 worker 独立计算 GAE。普通 rollout 末端不是终局，使用该 worker 同版本冻结模型的 `V(next_state)` bootstrap。真正 terminal 的 mask 为零；绝不跨 worker 或跨 reset 传播优势。
- 若 wrapper 有环境时间限制，应返回 `truncated=true` 以及 reset **之前**的最终状态特征。该边界 bootstrap，但切断 GAE 向下一局传播；单独列入 `truncations`，不计作完整胜负。未显式区分时遵循原协议 `done=true` 表示真实终局。不支持自动 reset 且只返回下一局初态的 wrapper。
- 合并后做一次全 batch advantage normalization（单样本保留信号），然后随机 minibatch、多 epoch PPO ratio clipping、value MSE、entropy 和梯度 norm clipping。沿用原 learner 的 value MSE，不增加 value clipping。
- 游戏 seed 为 `base + worker * 4096 + episode`，episode 从 0 开始。`--episodes-per-worker` 默认 4096，限制在 1..4096。启动前验证所有预留区间在 uint32 范围内、不交叠且避开冻结 val `90001..90016` / test `100001..100064`。禁止段耗尽后 wrap 或越界。
- learner Torch seed 是 base，actor Torch seed 是 `base + worker + 1`，创建模型后持续使用各自 RNG。相同 seed、worker 数、配置、checkpoint 和环境实现下按固定顺序采样/训练；改变 worker 数不保证同轨迹。
- 每 batch 记录真实环境 steps、每 worker 步数、完整 episodes（含胜负和完整训练 return）、显式 truncations、已启动局数、尚未完成的局内 steps。未完局继续到下一 update；训练停止时不伪造完整 episode。

## 产物与生命周期

`--out run.pt` 必须是新路径，同时产生 `run.pt.manifest.json` 和 `run.pt.jsonl`。每 update 使用临时文件、fsync、原子 rename 写 checkpoint，包含模型、optimizer、主 Torch RNG、完整来源 manifest 和当前 report；只覆盖该运行的上一个 update，避免无限累计 checkpoint。JSONL 是逐 update 日志，sidecar manifest 是最终运行状态。checkpoint 内 manifest 是保存当时的 running 快照；sidecar 给出最终 complete/failed 状态和输出 checkpoint SHA256。崩溃可能使日志/sidecar 落后一个原子 checkpoint，三者不是跨文件事务。

来源记录包含输入 checkpoint/config SHA256、本地 `rl/`、`reward/`、`parallel-rl/` 源文件 SHA256、源码 HEAD、schema/rules、命令、配置、Torch seeds、游戏 seed ranges、已知 pool 环境变量、显式奖励标签及环境报告的实际 reward mode。环境入口及相邻源码/JSON、相邻共享 `rl/`、reward wrapper 的 `ARK_ROOT/experiment-code/rl` 也会哈希。任意外部包装器的动态依赖无法自动穷举；使用重复的 `--provenance-file /path/to/file` 补充远处配置/依赖。完整外部环境继承，但 manifest 只列已知非敏感配置键。未知 wrapper 未报告奖励模式时保留 `--reward-mode` 标签，不猜测。

父进程退出/异常会终止并 join 所有 actor，并清理其进程组内的 Node 后代；actor 的 finally 关闭并 wait Node。Linux parent-death signal 也保护已启动 actor。包装器不得自行脱离进程组运行 daemon。SIGKILL 无法保证最终 manifest 写出；RSS/时间监控器单独记录这种失败。加载 checkpoint 是 **warm start**，不会恢复游戏、worker RNG、optimizer 或对局游标；不是精确断点续训，重新运行须选择合适的新训练 seed 段。

## 有限验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 parallel-rl/test_numeric.py
PYTHONDONTWRITEBYTECODE=1 python3 parallel-rl/smoke.py
# 已有 hidden=8 小 BC checkpoint 时，跳过真实 teacher demo 准备阶段：
PYTHONDONTWRITEBYTECODE=1 python3 parallel-rl/smoke.py --checkpoint /path/to/small-bc.pt
```

smoke supervisor 只用标准库，监控整个进程树 RSS（含自身），在 1900 MiB 阈值停止以给 2 GiB 上限留余量，默认聚合时限 25 秒，产物限制 20 MB，全部放 `/tmp/parallel-rl-smoke-*`。不缩 Node 堆。数值测试、BC 准备、workers=1、workers=2、保存重载依次执行，任何失败即停止并保留日志。`--seconds` 可进一步限制本次预算，`--skip-numeric` 仅用于同一次任务已测过数值项的情况；多次运行需自行累计预算。实际结果见 `VALIDATION.md`。本地资源不足不视为并行端到端通过，也不证明服务器吞吐提升。
