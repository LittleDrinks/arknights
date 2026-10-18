# Prep timing dev128

仅新增 `prep-timing/`，base `25c6af63cbb819bb4a4ed9682bed2824f27a4ec5`。
冻结模型推理，不训练、不修改游戏或已有模块源码、不 monkeypatch 导入模块全局。
动机来自 main 提供的 6 个自然教师成功种子的离线诊断：新 BC 首次 global/candidates
与教师相同、首买正确，但第二次决策输入均偏离。该观察不等于本模块已经复现或证明原因。
本 CLI 最小只支持 dev128；6 个已见 teacher seeds 的诊断由 main 单独运行。

## Server 命令

合并后，在已有 Python 3.10+、torch、Node 的 Linux server checkout 执行：

```bash
python3 -B prep-timing/run.py \
  --root /data1/zsm/arknights \
  --parent experiments/bc-expansion-v1/all.pt \
  --parent-sha256 f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a \
  --candidate experiments/natural-win-bc-v3/naturalWins.pt \
  --candidate-sha256 397e8780202bbcdb37ad0f4a29e38df3e78ed6c3369d03cab6748aab5a5c2dc3 \
  --out experiments/prep-timing-dev128-v1 \
  --cpus 4 --timeout 120 --output-mib 64 --total-timeout 1800
```

代码从本脚本 checkout 加载；游戏必须为 `ROOT/game`。相对模型路径和输出路径相对 root。
输出目录必须不存在，不覆盖、不续跑；拒绝写入受保护源码/游戏/输入路径。
两份 SHA 为代码固定常量（CLI 可省略 SHA，不能换其他值）；文件名可以不同，实际文件 SHA
必须精确相同。两份文件都在反序列化前验证，单份最多 256 MiB。准入失败也保存 128 分母。

唯一计划为 **parent/candidate × off/deferred × original/mixed × dev seed 1..16 = 128**。
每格 16，固定 coop/ABYSS、seat 0、budget 128、teacher=False、greedy=True。
无自定义 seed、90001、封存种子或未知队友家族入口。

| pool | COOP_POOL | 固定三队友 |
| --- | --- | --- |
| original | normal | original-v1,original-v1,original-v1 |
| mixed | mixed | original-v1,buy2-v1,saver-v1 |

显式设 `COOP_SPLIT=eval`、`COOP_POOL_SEED=313`、`COOP_TEAM` 和 `ARK_PREP_TIMING`，
清除继承的 ARK/COOP/STRONGHOLD/NODE loader 配置。唯一游戏子命令：

```text
node --no-warnings --loader CODE/prep-timing/loader.mjs CODE/coop/env.mjs --game ROOT/game
```

## 可审变换与 ready 边界

loader 仅匹配 `new URL('../rl/env.mjs', import.meta.url).href` 的**完整 URL**，不匹配
query/hash/同名异路径。先验证原始完整源码 SHA256
`723e397fabeea4e473b65523aaadbadba7b08af7fea27ec4f29fa38c3cefc4b5`；拒绝漂移及非法 timing。
off 返回 nextLoad 的同一个对象、同一 source bytes，byte-for-byte 原样，不重写版本。
deferred 对三个唯一 anchor 做内存替换，游戏和旧文件都不落盘修改：

1. `advance()` 的 observe 后，先按原 v1 的 PREP/alive/ready gate 调用
   `m.autoPickPersonalChoice(ps, 'bot')`。通常跳过 arrange/equipment/free rewards；
   仅 **auto-pick 后当前 `ps.temp.some(Boolean)`** 时调用原 `control(this)` 处理临时区。
   fallback 因复用完整 v1 会再次 auto-pick，并仍会重排。
2. `step()` 原生 candidates 和 `a` 已确定后，若 a.type 为 ready，先调用原
   `control(this)`，再读 `ps.privateView().canReady`。false 计 actionErrors 并抛出
   `PREP_TIMING_READY_INVALID`，Python 将该局判 failed；不会调用 setReady、不会重新选动作、
   不改 canReady 或 candidates。为 true 才沿原路径执行 `ps.setReady(true)`。
3. 改实际 `view().controller` 为显式 timing 版本。每个真实 response（包括终局）精确核对：

| timing | 实际 controller 字符串 |
| --- | --- |
| off | `coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1` |
| deferred | `coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1+prep-timing/deferred-v1` |

deferred 报告旧 v1 即失败，不能只在 manifest 标 timing。off 的旧字符串与原样返回一致。
原 forced budget 分支字节不变，仍 full v1 `{forced:true}`、resolveTemp、setReady。
原 CoopEnv constructor 的 capacityFilteredBot/preflight 保留；无替代 controller，
不代买卖，不增加 deploy/discard 等模型学不到的动作。

## 身份、证据与分母

复用 `coop-items/run.py` 的文件/模型准入、参数 schema、486 rules 身份辅助函数；
本目录自行持有 schedule、VERSIONS、game_job 和编排，绝不改辅助模块全局。
使用原 `survivor-study.evaluate.Protocol/state_contract` 与默认 teacher=False 的
`common.terminal_contract`。模型用原 `L.load_checkpoint/L.ActorCritic`，要求
shop-v1、相同词表/schema/rules、维度 `[5267,419,128]`、参数 shape/dtype 相同且有限；
eval、禁梯度、greedy 推理。actor **只见当前真实 `state['features']`**，不见教师标签、
outcome、timing ID、模型 metadata 或额外输入。

- 启动前 manifest 保存固定 128 项、模型文件/参数 SHA、schema、源码 identity、游戏 identity
  与边界。`source-pins.json` 固定 base 的完整 rl/coop 文件集合与实际使用的 helper 文件。
  本目录完整文件集合/SHA 和 code HEAD 首末相等，要求 base 为祖先。
- 游戏首末验证原规则 commit、全部 **486** rules 文件集合/SHA、静态 data/schema；
  两份 actual checkpoint 文件、内存参数/shape/schema、eval/禁梯度状态首末核对。
- 每局 verbatim `protocol.jsonl`、`stderr.log`、含候选/mask/所选 action 的
  `actions.jsonl`；收到 done 时保留完整原始 `terminal.json`，即使随后终局验证失败。
  原始完整 features 在 protocol 中。失败只保留已收到且在预算内的前缀，不冒称完整 EOF。
- `result.json` 保存状态、步数、EOF、诊断计数、个人结果与团队胜利、其他文件大小/SHA。
  只有 state/terminal 契约、close ack、干净 EOF、零退出码且无 stderr 均通过才 complete。
  错误版本、非法 ready、runtime、EOF/timeout/非有限 JSON 均 failed；显式
  `PREP_TIMING_STOP` 预留为 stopped（当前 loader 不主动产生它）。
- 首封存后，末尾重读 manifest 和全部 raw/actions/terminal/result，核对集合、大小、SHA
  与内存 metadata；拒绝新增/缺失文件、子目录、symlink 和篡改。summary 外层 seal 包含
  result 自身 SHA，避免自引用。任何 guard 漂移/超时撤销所有 complete 信用、胜利归零，
  保留原始逐局证据；只有最终 `summary.json` 是权威结论。
- summary 总分母恒 128，每 model/timing/pool 格恒 16，分别计 complete/failed/stopped/unrun、
  teamWins 与 learnerWins，胜率以计划分母计算。中断且没有 summary 的目录不能宣称成功。

## 资源和解释边界

顺序执行，一次一局；CPU affinity 最多 4 核，torch/BLAS 单线程。参数只能降低：
每局 ≤120s，完整输出（raw/stderr/actions/terminal/result）≤64 MiB，总 deadline ≤1800s，
每局 policy steps ≤4096。SIGALRM 覆盖准入、推理、游戏、guards；Protocol 使用绝对 deadline，
清理私有进程组。超限后不再启动游戏，只做有限回收/失败记录（原 Protocol 回收最多等 2s）。
审计无剩余时间同样撤销信用。64 MiB 是输出上限，**不是游戏内存上限**。
预留每局 128 KiB result；总输出上限 `128 × output_mib MiB + 8 MiB`，后者供 manifest/summary。

这是**时机模块近似**，不是完全原 teacher：free reward/equipment 策略仍是 v1，
临时区 fallback 仍重排；候选 ready 在 control 前确定，可能仍存在输入分布差异。
controller 会改变 RNG 消耗及后续路径，不能把差异纯归因于某一 arrange 动作。
保留的 native arrange/draft 可依赖 pool，不声称端到端仅可见信息。
128 局复用 16 个 dev seeds，不是 128 个独立样本；original/mixed 都是已知家族；
团队胜利与 learner 个人存活/胜利分开报告。

## 本地合成验证

```bash
python3 -B prep-timing/check.py
```

仅合成 fixture、小 Python pipe 和真实 Node loader + 假 player/bot/scheduler。
整个测试进程树 ≤20s、aggregate RSS ≤1 GiB、临时产物 ≤5,000,000 bytes（20ms 采样），
使用 `os.walk` 监控临时目录并容忍清理 race，退出删除产物。不导入游戏、不调用
create/reset/Match/Battle，不加载真实 checkpoint、不训练、不 SSH、不安装。
验证实际 inherited controller 字符串、off identity、exact URL/hash 漂移拒绝、普通经济动作延后、
auto-pick 后 temp fallback、ready 顺序与非法 ready、forced 路径、capacity/preflight，
全 128 fakeProtocol 分母、终局/EOF/诊断/版本失败、证据篡改撤销及旧模块全局未变。
真实 dev128 与 6 教师 seed 诊断均由 main 负责，本地通过不代表真实游戏通过。
