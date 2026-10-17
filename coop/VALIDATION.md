# 验证记录（2026-10-18）

工作树 `experiment-coop` / 分支 `exp/coop`，父提交 `4f23f97`。游戏 HEAD `6ab2468279e926d107788f63d7fe219b85f67b4a`，已跟踪规则文件未修改。所有新文件位于 `coop/`。

## 通过的真实 engine 验证

命令：`python3 -B coop/check.py --seconds 32`。其中 Node engine 测试 **2/2 通过**，用时 14.466 秒；后续 learner 首次加载因原 768 MiB 软预算被监督进程终止，不能把整条命令称为全通过。日志 `/tmp/coop-check-7ouzw7si/check-0.log` 保留完整 engine 结果。资源预算更新后没有重复这 4 局。

真实 game seed 11、ABYSS、primary 每轮 4 步预算；每行均到原引擎 RESULT / 完整 onEnd，4 个玩家 summary。不是仿真快照、代理规则或截断结果。

| 池 | victory | roundsPassed | 实查 PREP 次数 | primary steps | 三席成功方法调用次数 |
| --- | --- | --- | --- | --- | --- |
| normal | false | 13 | 14 | 34 | 274 / 267 / 180 |
| weak（buy2/saver/buy2） | false | 9 | 10 | 31 | 146 / 23 / 116 |
| heldout（random 三席） | false | 4 | 5 | 13 | 26 / 27 / 15 |
| weak 完整重放 | false | 9 | 10 | 31 | 146 / 23 / 116 |

四局 engineErrors/actionErrors/controllerErrors/summary.errors 和三席 rejected 均为 **0**。玩家淘汰后不再要求其执行 PREP；测试在每次真实 endPrep 前检查所有仍存活队友 scheduled=1、ready=1。三池实际动作 hash/计数不同；weak 的完整 primary 动作序列、summary、逐轮队友审计完全一致。

测试还验证：primary 第一笔真实购买改变自身 shop.sold，并记录 policy 来源；全部 primary 买/卖/刷新/升本均来自 policy；父 features schema 相等；读取 observation 时禁止调用其他玩家 privateView；多次伙伴选择前后所有引擎 rng.state 不变；非法索引不改变局面；训练池拒绝 heldout/未知身份/无效种子/无效固定组合。

## 最小 learner 兼容性

资源补充后命令：`python3 -B coop/check.py --only learner --seconds 37`，**通过**。证据 `/tmp/coop-check-p_scw_8h/check-0.log`、同目录 `resources.json`。

- 真 solo teacher 的 2 个合法标签、1 次 BC 更新：参数 L2 改变量 `0.0072156209498643875`；保存/加载后，原 `validate_checkpoint` 对 coop schema/rules/dimensions 校验成功。
- 未修改的 `rl/learner.py demo`：固定 mixed original/buy2/saver，seed 7 NORMAL，95 个合法标签、rejectedLabels=0；完整 summary.victory=true，roundsPassed=14。
- 未修改的 `rl/learner.py ppo`：从上述 solo BC 热启动，4 个真实 coop transitions、1 次更新，参数 L2 改变量 `0.008320867083966732`。0 个 terminal transitions；第 3 轮课程截断明确标注为非终局，使用 bootstrap，不计胜负。
- 未修改的 `rl/learner.py eval`：冻结更新后的权重，heldout random 三席，seed 7 NORMAL、budget 2；真实完整 summary.victory=false，roundsPassed=6，14 个 primary steps。动作 hash `5e9256cdd2d8ddfb26cd78196deb424209307bb47b3ac1f3c71af346b2c26259`。
- demo/eval 均为 0 engine/action/controller errors。`eval` 内硬编码 `mode:solo` 确实通过兼容适配变成真实 coop。

这不是批量训练，不代表 PPO 改善性能；NORMAL teacher 的胜利也不是训练后模型的胜利。当前未运行 normal-vs-mixed 正式训练或跨多种 seed 的统计评测。

## 诊断、资源和边界

早期真实 NORMAL 测试在 round 11 遇到原无出售布阵控制器提议超容量 move，实际返回 BOARD_FULL。新增的可见容量过滤在调用 engine 方法前拒绝该提议，并保留单独过滤计数；没有改游戏规则或吞掉实际错误。修复后 NORMAL original 和 weak 的真实终局成功完成，随后测试中错误地要求 heldout 必须触发 primary 强制 ready 导致断言失败；已移除该不成立的假设，最终使用上述 4 局完整 suite 验证。

原 768 MiB 软预算下的两次 learner 尝试分别在采样 RSS 775.68 / 746.25 MiB 时中止（后一轮提前保护阈值 736 MiB）；前一轮 50ms 采样存在超限检测延迟。用户随后明确将本地合计 RSS 上限提升到 1.2 GiB、累计 sim 到 90 秒。最终 learner worker 全进程组峰值 **826.61 MiB**，耗时 **33.443 秒**，产生 **4,690,538 bytes**（含 resources 记录）。所有产物只在 `/tmp`。

包含失败诊断和启动开销的所有 simulation/check 调用累计墙钟 **82.384 秒**，这是累计 sim 的保守上界；六次检查目录合计 **4,706,394 bytes**。没有 SSH、后台批量训练、模型下载或依赖安装。

| `/tmp/coop-check-` 后缀 | 检查墙钟秒 | 结果 |
| --- | --- | --- |
| hrffmoi3 | 5.041 | 定位前的控制器错误 |
| n_t1qi1n | 5.658 | 精确记录 BOARD_FULL |
| fmm_el74 | 16.166 | 修复后终局完成，测试预算假设不成立 |
| 7ouzw7si | 18.054 | 4 局 engine 通过，learner 旧 RSS 预算中止 |
| thrqiwdp | 4.021 | learner 旧 RSS 提前保护中止 |
| p_scw_8h | 33.443 | 最小 learner 全通过 |

最后只进行不启动 Match 的容量过滤单测、manifest 检查及静态语法/diff/scope 检查。容量单测覆盖满部署位替补提议被过滤、合法替换仍传给 engine，以及异常时恢复 ps.move；它不替代上述真实终局证据。提交后停止写入，等待独立 review。
