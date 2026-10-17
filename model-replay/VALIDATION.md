# 离线适配器实测（2026-10-18）

验证版本：Node v24.21.0，worktree `experiment-replay` / `exp/model-replay`，实现基于 `4f23f97`。只新增 `model-replay/`，无依赖安装、SSH、子 agent 或 goal。产物写入新的 `/tmp` 目录，未发布到游戏页面。

## 实际 learner eval trace

- 输入：`/tmp/rl-independent-review-zf2m0il0/eval/ABYSS-1.json`
- trace SHA-256：`3ac27ea128b2df2cb1e4cb5498f9ef52f537659b6bfa3f603f78201be6307e01`
- 调用者提供 checkpoint：`/tmp/rl-independent-review-zf2m0il0/cli-ppo.pt`
- checkpoint 文件 SHA-256：`99038e3e142ee24ac7d635bbd0990474683efa4cde553e0341ea8dc34beeeece`
- 规则：`6ab2468279e926d107788f63d7fe219b85f67b4a`
- 控制器：`upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1`
- solo / ABYSS / seed 1 / budget 8。

checkpoint 与 trace 的生成绑定未被独立证明：现有 trace 不包含 checkpoint 身份；只核实上述输入文件的 hash，没有执行 Torch 或重跑模型推理。未采用 `experiment-rl` 中已经被后续 smoke 覆盖的 checkpoint 作为旧 eval 的已证实来源。

## 完整终局与产物

最终测试产物：`/tmp/model-replay-verified-DlQ6aR/bundle`。

| 项目 | 实测 |
|---|---:|
| 自主商店动作与完整候选逐项匹配 | 20 / 20 |
| 实际方法调用（含草稿、部署及引擎清理） | 30 |
| 页面策略/控制器 steps | 26 |
| 单独保留的 engineCalls | 4 |
| 真实战斗 | 4（R1–R4） |
| 真实帧 | 1,873 |
| 真实事件 tuple | 88 |
| 通过轮数 | 3，R4 结束后失败 |
| engine/action/controller errors | 0 / 0 / 0 |
| budgetForcedReady / controllerBlockedSales | 0 / 0 |
| reset → step → 完整终局及战斗收尾 | 261.13 ms |
| worker 峰值 RSS | 189,136,896 字节，约 180.38 MiB |
| 所有导出文件合计 | 871,676 字节（含 verification.json） |

**这是失败重放，不是通关录像。** 不是截断数据，也没有胜例或泛化结论。实现期间同一 trace 共重放三次，累计实际仿真约 0.78 秒；最后一次对应最终实现。未开展训练或额外策略搜索。

## 验证结果

```bash
REPLAY_TRACE=/tmp/rl-independent-review-zf2m0il0/eval/ABYSS-1.json \
REPLAY_CHECKPOINT=/tmp/rl-independent-review-zf2m0il0/cli-ppo.pt \
node --test model-replay/replay.test.mjs
```

5 tests / 5 pass / 0 fail / 0 skipped，1.230 秒。覆盖：

- trace 规则/控制器/预算/seed/终局字段的拒绝校验；UID、slot、id、price 和索引错配；过早及过晚终止。
- reset 之前已安装的草稿钩子、环境历史包装去重、控制器临时禁售替换、预算与引擎 resolveTemp 来源、嵌套方法关系。
- 构造期/start 事件、每 3 ticks 与最终非整 3 tick 帧、真实零 spawns 战斗与 preview/rehearsal 排除。此项使用明确标注的 contract fixture；上表战斗则全部来自真实引擎。
- 纯索引合并不改变输入，拒绝重复 ID/name/file、危险路径及断开的录像引用。
- 现有 smoke trace 真实完整重放；JSONL 终局和全方法数；导出每个 payload 文件的 SHA-256；再次 CLI 写入同一目录被拒绝且原文件 hash 不变。

读取原 `export-trajectories.mjs`、`agentReplay.js`、BattleEvents 与生命周期实现，并在 Node VM 内执行**原 agentReplay.js 源码函数**，只替换 DOM、网络与 store 依赖：原固定 URL 加载成功，`ai_0` 筛选得到全部 26 个决策，4 场录像均进入原帧事件通道，seek 用原 spawn 逻辑恢复，最后一场播放结束写入真实失败 summary。没有修改 UI 源码，也没有宣称浏览器渲染/视觉验收。

`node --check` 检查 CLI、worker、replay、record、index 均通过。源文件 hash 前后复核一致：以排序后的相对路径及文件内容拼接计算 SHA-256。

| 只读范围 | 文件数 | 前后相同的 SHA-256 |
|---|---:|---|
| `rl/` 全部文件 | 10 | `0af19df7f443847e70e868c33410aab7807862038a1af89bb166125691350502` |
| `macros/` 全部文件 | 7 | `d22a7dafabe82d05d2296f5611d2609c7fe3e654963a21ec13eb9f0e3424d45e` |
| `../Stronghold-Protocol` tracked + 未忽略的 untracked 文件 | 1,810 | `cb489bb59a1b2585a7c4c1f54ee11126cc7dbfeaece200e64ad73450a53314a8` |

游戏中原有 dirty 页面、翻译、录像索引及未跟踪 agentReplay/录像文件保持原样。忽略的资源与依赖未纳入上述 hash 清单，适配器没有向游戏目录写入任何文件。主线程合入现有页面/索引及浏览器视觉验收留待 review 后处理。
