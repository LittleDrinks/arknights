# 合作场景冻结模型 × 控制器比较

只新增 `coop-items/`，基线 `fbd7c2bd0232949cb1fa8f94c02d44c4790b1233`。复用原 `rl/learner.py` 的 `L.load_checkpoint` / `L.ActorCritic`、`survivor-study.evaluate.Protocol` / `state_contract` 和 `common.terminal_contract`。不训练、不复制模型实现、不改导入模块全局、不改原源码。原 `controller-v2/` 的单人独立 PASS 不代表本合作场景已通过真实评测。

合并到 main 后，在已有依赖的 Linux server checkout 中运行（Python 3.10+；代码使用当前脚本所在 checkout，实际模型和游戏均在 server）：

```bash
python3 -B coop-items/run.py \
  --root /data1/zsm/arknights \
  --parent experiments/bc-expansion-v1/all.pt \
  --parent-sha256 f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a \
  --candidate experiments/natural-win-bc-v3/naturalWins.pt \
  --candidate-sha256 397e8780202bbcdb37ad0f4a29e38df3e78ed6c3369d03cab6748aab5a5c2dc3 \
  --out newdir
```

`--out` 相对 `--root`，也可用绝对路径；目录必须不存在，不支持覆盖、续跑或写入代码模块/游戏/输入文件。脚本不创建 checkpoint、不调用训练 API。输出只写入新目录。找不到 candidate、SHA 不符、schema 不符时不启动任何 game job；准入失败也保存固定 128 分母的 manifest/summary。

两个文件先检查存在、大小和 actual file SHA256，再分别用原 loader 严格加载为 `L.ActorCritic`。要求原始 `shop-v1`、完全相同词表、规则 pin 和参数 shape/dtype、维度 `[5267,419,128]`，拒绝 hidden64 和非有限参数。`eval()`、`requires_grad_(False)`，每次 `act(features, greedy=True)`；只传原 features，不把 checkpoint metadata/controller 版本喂给 actor。允许两个 checkpoint 的 metadata 版本不同。每模型保存文件 SHA、参数 SHA、参数 schema、完整 feature schema。

固定计划顺序为 `parent/candidate × off/v2 × original/mixed × seed 1..16`，共 128 局，全部 `coop`、`ABYSS`、learner seat 0、prep budget 128、`teacher=False`。每格分母 16；失败、停止、未执行都在分母。无 90001、无封存种子。固定队友：

| 标签 | COOP_POOL | COOP_TEAM |
| --- | --- | --- |
| original | normal | original-v1,original-v1,original-v1 |
| mixed | mixed | original-v1,buy2-v1,saver-v1 |

所有 childenv 显式设置 `ARK_ITEM_CONTROLLER=off` 或 `v2`、`COOP_SPLIT=eval`、`COOP_POOL_SEED=313` 和 TEAM；清除继承的 ARK/COOP/NODE loader 配置。唯一游戏命令：

```text
node --no-warnings --loader CODE/controller-v2/loader.mjs CODE/coop/env.mjs --game ROOT/game
```

原 loader 的 off 分支原样返回 source；v2 仅改精确 `rl/env.mjs` 的 control import 和 controller version。`CoopEnv` 保留原 capacity/preflight bot 包装。逐 response 强制检查精确版本：

- off：`coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1`
- v2：`coop/move-preflight-v3+upstream-drafts+no-sale-arrange+visible-items+free-rewards/v2`

仅启用已审控制器的原方法路径；没有额外奖励、棋子、层数、Boss 改动或游戏 source 补丁。原状态契约检查 schema/rules、步数连续、身份/队友及合法候选；原终局契约默认 `teacher=False`，核对实际四人终局、个人存活/胜利、团队胜利和诊断拒绝计数。`CONTROLLER_V2_STOP` 为 `stopped`，未知 response、非有限 JSON、runtime failure、超时、错误 EOF 为 `failed`，均不得当正常输赢。只有完整契约、close ack、干净 EOF、零退出码/无 stderr 都通过才 `complete`。

默认顺序执行，一次一个游戏。CPU affinity 最多 4 核，torch/BLAS 单线程，继承到子进程；不是 4 个并行游戏。可降低的有限参数：`--cpus 1..4`、`--timeout (0,120]` 秒、`--total-timeout (0,1800]` 秒、`--output-mib 1..64`。SIGALRM 覆盖准入/推理/游戏和最终 guards；Protocol 使用绝对 deadline 并清理私有进程组。超限后只做有限的进程回收和失败记录（原 Protocol 回收最多等待 2 秒），不再启动游戏。总 deadline 包含准入和审计；若没有剩余时间完成审计，撤销全部成功信用。

64 MiB 是**每局完整输出预算，不是游戏内存上限**，包括 verbatim raw、stderr、actions、terminal 副本和 result；预留 128 KiB 给 result。总输出上限 `128 × --output-mib MiB + 8 MiB`（默认 8 GiB + 8 MiB），末尾 8 MiB 是 manifest/summary 合计预算。每输入 checkpoint 上限 256 MiB。server 运行需要正常的 torch/Node 内存；本地验证另受 1 GiB RSS 上限。

证据：

- `manifest.json`：启动 game jobs 前写入固定 schedule、实际模型身份、资源/信息边界、source 和 rules 身份。
- 每局目录：`protocol.jsonl` 为收到的原始 stdout bytes；`stderr.log`、带完整 candidates/mask 的 `actions.jsonl`；若收到 done，保存**原始完整 response** 为 `terminal.json`，即使后续校验失败也保留。失败 raw 是截至停止时已收到并写入的前缀；达到输出预算的超限 chunk 不落盘，不冒称完整 EOF。
- `result.json`：complete/failed/stopped、请求步数、EOF、诊断计数、个人/团队分离结果及逐文件大小/SHA。终局完整细节在 raw/terminal 中，不以精简结果替代。
- `summary.json`：权威本轮结论，每格固定分母，包含每局 result 本身及其他证据的大小/SHA。逐局 result 不自我包含 SHA；由 summary 的外层 seal 记录。最终 summary 不做不可能的自引用 SHA。

`source-pins.json` 固定基线的全部 `rl/`、`coop/`、`controller-v2/` 文件和使用的 common/evaluate/static/rules helpers。运行开始记录本次 `coop-items/` 完整文件集合和 SHA、当前 code commit；最终再次比较。游戏首末核对全部 486 个 rules 文件集合/SHA、rules commit 和静态 schema/data SHA；两个 actual checkpoint 文件与内存参数也首末核对。

这些 guards 后，逐一重读所有已记录 raw/actions/terminal/result metadata，核对文件集合、大小、SHA 及 result metadata 与内存快照的一致性，也拒绝新增文件/目录、缺失、符号链接或 manifest 漂移。任何一处漂移或审计超时都令本轮 `integrity=rejected`，**所有 complete 信用一并撤销、胜利计数归零**；原始逐局证据不覆写。只看单局 result、不看最终 summary 不能宣称成功。运行中断而未产生 summary 的目录同样不能视为成功评测。

解释边界：不同 controller 可以通过 item/arrange 改变 RNG 消耗与后续轨迹，比较的是控制器整个模块，不能纯归因于单件物品。新增 item 决策使用 own/static 信息；保留的 native arrange/draft 仍有 pool 依赖，不宣称端到端只读可见信息。128 局复用 16 个 dev seeds，**不是 128 个独立样本**。original/mixed 均为已知队友家族，未知队友家族未测。团队胜利不等于 learner 个人胜利。

本地只运行：

```bash
python3 -B coop-items/check.py
```

它监控所有测试后代进程，总计最多 20 秒、1 GiB aggregate RSS、5 MiB 临时产物（20 ms 采样）；退出删除临时数据。测试包含 fakeProtocol 全 128 schedule、真实契约、有限失败/停止/计数、缺失 candidate 的 CLI 拒绝、metadata 版本独立性、末尾证据篡改撤销，以及小 Python JSONL pipe 的实际 EOF/超时/预算检查。静态 Node fixture 仅导入原 loader 并检查两处替换/off 原样返回，绝不导入游戏。没有真实 checkpoint forward、Match、Battle、训练或下载。Windows/WSL 只保留这些小代码；此实现的资源管控及 server CLI 需要 Linux/WSL。
