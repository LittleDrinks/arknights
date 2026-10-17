# 本地验证记录 · 2026-10-18

仅修改 `parallel-rl/`，基线 `0de8ae5cc1b876dcd5c929231208c78ffe180cef`。没有 SSH、新依赖、子 agent、大训练或 goal。验证未达到“两 actor 端到端通过”，不据此声称吞吐提升。

## 已执行

| 检查 | 实际结果 |
| --- | --- |
| 独立数值测试 | 6/6 通过；含 Torch 启动 2.992 秒 |
| 新 teacher BC fixture | 真实 Node `demo(seed=7)` 请求超过 5 秒，失败；含加载/清理 9.543 秒 |
| workers=1，hidden=8 | 真实 4 steps、2 次同步 PPO update；7.608 秒；完整 episodes=0，未完局持续 4 steps |
| workers=2，hidden=8 | RSS guard 停止，5.079 秒；无已提交 update，不算通过 |
| checkpoint 重载验证阶段 | 因两 actor 失败未执行 |

数值测试独立手算 GAE：不同 worker 不串链，terminal 忽略 bootstrap，显式 truncation bootstrap 后断链，普通采样截断保留 bootstrap，非平凡 lambda 递推；还检查 padding/false mask 概率严格为零、old logprob 在更新后不变、旧轨迹遇到已变化策略时在更新前失败、冻结种子区间和 schema/rules/维度不匹配拒绝。

单 actor 的两次参数 L2 变化分别为 `0.007050016429275274`、`0.005351829808205366`，两轮行为 logprob 最大误差均为 `0.0`。第二轮行为摘要等于第一轮更新后的参数摘要；同一 actor/Node 跨两个 update 存活，正常结束后两个 PID 均已消失。单 actor 运行树峰值 RSS `1,394,352,128` bytes。

单 actor 最终参数 SHA256：

```text
78ec2a0fd6bb988cafd27f6908208977db3f2bfff142994263b2656a6de2412f
```

第二轮检查复用已有 `/tmp/coop-check-p_scw_8h/solo-bc.pt`，不重新跑 teacher。该输入由其他实验的真实 BC smoke 生成，本轮实际成功加载，并在真实 solo reset 上通过 schema/rules 校验。输入文件 SHA256：

```text
65266f0e39691322b833461238e1da8280d2025a01afdf73d7d7743f294b5e21
```

## 预算、失败与证据

两次 supervisor 分别耗时 `12.536` 和 `12.696` 秒，累计 `25.232` 秒（含 Torch 加载、数值检查和进程清理）；另有一次约 1.96 秒的现有 Torch 包信息查询和不足一秒的语法/差异检查，合计小于 30 秒。两 actor 运行观测峰值 RSS `1,996,042,240` bytes（约 1.86 GiB），触及 1900 MiB guard，仍低于 2 GiB。没有调整 Node 堆。

guard 先发 SIGTERM，再兜底 SIGKILL；两 actor learner 的最终退出码为 `-9`。因此它的 sidecar 停在 `running/totalSteps=0`，权威失败记录是 supervisor 的 `report.json` 中 `status=failed`、`reason=RSS guard reached; no Node heap modification`。未将此次中断伪装为正常训练完成。检查后未发现残留本实验 trainer/Node 活进程。

原始产物位于：

- `/tmp/parallel-rl-smoke-sidyiwzr/`：数值输出、真实 demo 超时 traceback、报告。
- `/tmp/parallel-rl-smoke-x9gw799y/`：复用 BC、小配置、单 actor 两轮 JSONL、原子 checkpoint/manifest、两 actor RSS 失败报告。

两目录合计约 `819,960` bytes，远小于 20 MB。产物不进 git。

## 仍需主服务器验证

1. 使用已有 hidden=8 BC checkpoint 和同一小配置，直接运行 `train.py --workers 2 --steps 17 --rollout 4 ...`，避开本地 smoke 的 1900 MiB guard；检查 3 次 update、每轮参数变化、版本串联、最终精确 17 steps，以及末批零步 worker。随后加载最终 checkpoint 校验参数摘要。
2. 本地真实 smoke 没有完成 episode；完整终局统计、显式 truncation wrapper 的实际接线、coop/reward wrapper 的并行端到端尚未实测。数学上的 terminal/truncation 处理已由独立数值测试验证。
3. 8 actor CPU 利用率、采样速度、超时/异常注入及异常退出清理需要服务器补测。现阶段只提交最小实现，不运行大训练。

提交后停止写入，等待独立 review。
