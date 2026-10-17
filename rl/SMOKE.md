# 实测记录（2026-10-18）

规则 HEAD：`6ab2468279e926d107788f63d7fe219b85f67b4a`。本地 CPU，PyTorch `2.11.0+cu130`，实际使用 CPU/单线程。没有安装依赖、下载模型、SSH、浏览器、子 agent 或新 goal。

## 最终代码验证

- `node --test rl/env.test.mjs`：**2/2 通过**，3.539 秒。真实初始候选逐一执行通过；非法索引不改变游戏。seed 11、budget 4 两次完整重放一致：49 自主动作、7 次预算强制 ready、`roundsPassed=13`、**失败**、引擎错误 0、动作错误 0。
- `python3 rl/smoke.py`：核心流程 3.943 秒（不含 Torch import）。197506 参数的早期试跑已被最终 **368258 参数**版本替代，下面数值全部来自最终版本。
- `python3 -m py_compile rl/learner.py rl/smoke.py`；`node --check` 检查 env/features/controller：通过。
- `git diff --check`、暂存文件的 `git diff --cached --check`：通过。

### 最终 BC → PPO → 保存加载

教师原 bot：NORMAL seed 7，完整终局失败，13 轮，89 个合法商店标签，拒绝标签 0，引擎错误 0。BC 使用其中 32 条，4 个 batch；PPO 使用真实 NORMAL 采样 64 步，8 个 batch，prep budget=8。

| 项目 | 实测 |
|---|---:|
| BC 参数总 L2 变化 | 0.1199134514 |
| BC actor L2 变化 | 0.0746200979 |
| PPO 参数总 L2 变化 | 0.1707672775 |
| PPO actor L2 变化 | 0.0795498118 |
| PPO critic L2 变化 | 0.0107173007 |
| PPO 实际终局转移 | 2 |
| PPO 最后截断 bootstrap value | -0.0467444360 |

PPO 的两个完整终局：seed 21，36 步、7 轮、失败；seed 22，20 步、5 轮、失败。两局引擎错误、动作错误、固定控制器执行错误均为 0。剩余 8 步属于 seed 23 的未完局，正确 bootstrap，不计作完整胜负。采样前策略与更新前重算 logprob 的误差通过 `atol=2e-6` 校验。

NORMAL seed 4001 的 greedy 完整评测：7 步、4 轮、失败；引擎/动作/控制器错误均 0。保存前模型与加载后的模型张量完全相等；两次完整评测结果、逐动作 hash 完全一致：

`4e47d427d98597c44dae0ee1f0d2ac3c6dfb4e1ac297ab1d49d6ff7b8c4a6954`

最终模型参数 SHA256（张量字节拼接，非 checkpoint 文件 hash）：

- BC：`66f35852832fea0b87a4f8da4e1661c3ec0b9be0e55c8d66dbfa4cd462712def`
- PPO：`d46d54532dc391cbda1ed8a3fa5dd370861345e348f263c2c5229915e647d575`

原始结果在 `rl/artifacts/smoke.json`；`bc.pt` / `ppo.pt` 是这次最终 smoke 的模型，均被 git 忽略。

### CLI 实际执行

以下命令均执行成功。为了限制本地采样，课程 CLI 只各跑 16 步；它们是接口验证，不是完成课程训练。

```bash
python3 rl/learner.py --seed 7 demo --episodes 1 --difficulty NORMAL --out rl/artifacts/demo.jsonl
python3 rl/learner.py --seed 101 bc --data rl/artifacts/demo.jsonl --epochs 1 --max-samples 32 --out rl/artifacts/bc.pt
python3 rl/learner.py --seed 1001 ppo --checkpoint rl/artifacts/bc.pt --out rl/artifacts/ppo.pt --steps-per-stage 16 --curriculum NORMAL,HARD,ABYSS
python3 rl/learner.py --seed 50001 eval --checkpoint rl/artifacts/ppo.pt --episodes 1 --difficulty ABYSS --trace-dir rl/artifacts/eval
```

课程合计 48 个真实步，三个阶段都打印非零权重变化及 `curriculumTruncation`；没有把截断局伪装成胜负。CLI 核心 PPO 流程 2.141 秒。该 CLI checkpoint 的 ABYSS seed 50001 完整评测：**0/1 胜**，41 步、3 轮、引擎/动作/控制器错误均 0。之后最终 smoke 重新写入了 `bc.pt/ppo.pt`，因此这个 ABYSS 结果不属于当前本地 smoke checkpoint。

本地已用 seeds：7、11、21–23、1001–1003、4001、50001；父线程未见 seed 评测应排除这些 seed。现有独立 worktree 包含忽略文件在内约 13 MiB，没有提交 checkpoint 或大轨迹。包含早期试跑的本地训练/仿真仅约数十秒量级；没有开展大规模采样。

## 尚未证明

没有 ABYSS 胜例、性能提升或泛化证据；NORMAL 小批次模型也失败。支持 solo 商店决策，不支持 coop 和全栈模型。固定部署、保守装备/奖励和丢弃溢出行为限制了策略上限；历史摘要并非完整特质计数状态；默认 action-step 折扣的长程信用分配仍需验证。未覆盖全部罕见 temp 阻塞组合；未完成局不能从 checkpoint 精确恢复；远程 stdio 命令只提供接口支持，未进行网络验证。页面录像接线留给父线程。
