# 合作训练真实胜局 → 原页面离线回放

本页为原 training profile；默认 CLI/API 语义保持。冻结 BC 开发 seed65 使用独立命令和
显式 profile，见 [frozen-replay/README.md](../frozen-replay/README.md)，不能套用本页的训练来源说明。

仅新增 `coop-replay/`，基线 `53bc0f1a9f451b61bde7c209c8bf013b81540517`。复用原 `CoopEnv.create/reset/step`、规则 pin、动作合法性和健康检查；没有修改游戏、coop、model-replay 或 rl，没有恢复 snapshot 来模拟，也不加载模型、训练、安装或联网。

输入为项目根 `experiments/coop-training-win-50003.json`（本 checkout 相邻 `../experiments/`），默认必须匹配 SHA256：

```text
a1b9970568b885fb48b4a9c6797ee455635f47f5471b57cff7de83adb8e0e651
```

其 `kind` 必须为 `actual-training-trajectory-not-frozen-model-evaluation`。这是跨多个 PPO 行为版本的实际训练动作轨迹，不绑定单 checkpoint，不是冻结模型评估成功，不是泛化证据。主线程提供的精确重训证据（前 3 updates 的行为 SHA、更新参数 SHA、episodes 相同）没有由本模块重新执行；provenance 明确记录这一边界。此前冻结 normal/mixed+BC 贪心与 crossplay128 全输是主线程提供的背景，本模块不改变这些结论。

输入 terminal 表明：normal/train 队友池、poolSeed=313、fixed=null，seed=50003，ABYSS、团队 victory、14 轮、teamLp=4；learner 与 partner-3 第 11 轮淘汰，个体 victory=false。**团队胜利不能改写为 learner 存活通关**。

## 最小本地操作

需要现有 Node、Linux `/proc` 和相邻已安装依赖的 `../Stronghold-Protocol`，规则 HEAD 必须为 `6ab2468279e926d107788f63d7fe219b85f67b4a`，tracked server/shared/data 规则必须干净。可用 `--game` 显式指定路径。

```bash
# 默认只执行动作，不录帧、不生成播放包；成功终止后才输出完整核对报告。
node coop-replay/cli.mjs \
  --trace ../experiments/coop-training-win-50003.json \
  --out /tmp/coop-win-verify-NEW

# 本地完整重放超时时，可诊断有限动作前缀。绝不把前缀称为胜局核对通过。
node coop-replay/cli.mjs \
  --trace ../experiments/coop-training-win-50003.json \
  --out /tmp/coop-win-prefix-NEW --prefix 12

node --test coop-replay/contracts.test.mjs
```

默认 wall limit=12 秒（包括加载、模拟和导出），worker heap=512 MiB、RSS=768 MiB，输出总额≤12,000,000 字节。父进程每 20ms 读取 worker RSS/HWM 并可 SIGKILL，同步方法和战斗 tick 也检查预算并锁存失败；这是采样与边界检查，不是 cgroup 硬内存限制。看门狗失败输出最后收到的动作进度。`--seconds`、`--rss-mb`、`--heap-mb`、`--max-output-mb` 可显式配置；heap 必须小于 RSS。MB 为十进制文件字节，内存 MiB 为 1024² 字节。

`--prefix N` 仅用于 verify 模式（N 小于总动作数时不允许 record）；报告 mode=`prefix-diagnostic`，`victory=null`，完整 terminal/summary/partnerAudit 核对为 false，不生成 agent/index 或录像。N 等于动作数时必须通过完整终局核对。每次使用不存在的新输出目录；父目录必须存在。空目录、普通文件、有效/悬空 symlink 都拒绝覆盖。输出禁止位于真实 game 目录内。成功前不建目录；写入失败可能留下不完整的新目录，保留并换路径重试。工具不会清理、覆盖或发布 live indexes。

## 留给 server 的完整导出（本地未运行）

以下命令供主线程在 server 本地运行；本次没有 SSH 或 server 操作。先核对完整动作，再使用另一新目录录制：

```bash
node coop-replay/cli.mjs --mode verify \
  --trace /path/to/experiments/coop-training-win-50003.json \
  --game /path/to/Stronghold-Protocol --out /path/to/exports/coop-verify-NEW \
  --seconds 90 --rss-mb 2048 --heap-mb 1536 --max-output-mb 256

node coop-replay/cli.mjs --mode record \
  --trace /path/to/experiments/coop-training-win-50003.json \
  --game /path/to/Stronghold-Protocol --out /path/to/exports/coop-record-NEW \
  --seconds 90 --rss-mb 2048 --heap-mb 1536 --max-output-mb 256
```

保留所有真实战场（各存活席位普通场、联防场、boss 配对场），不只录 primary field。默认保留 t=0、每 3 ticks、最终帧，**不是逐 tick 全帧**；每个真实事件均保留到下一录制帧，未采样丢弃事件。`--frame-every 1` 可录每 tick，预算可能不足；不会自动降采样或删战场来绕过预算。配置、实际 battle/frame/event 数量写入 verification 与 episode。预算不足直接失败，不能称为完整录像成功。

```text
<newoutdir>/verification.json
<newoutdir>/agent/index.json
<newoutdir>/agent/episodes/<id>[-viewN].json
<newoutdir>/agent/downloads/<id>[-viewN].jsonl.gz
<newoutdir>/recordings/index.json
<newoutdir>/recordings/<battle-name>.json
```

`verification.json` 保存每个 payload 的大小与 SHA、输入 SHA、规则 pin、执行源文件 SHA、来源边界、资源预算与测量。完整终局核对完成后才生成包，写入均使用 `wx`，agent index 最后写；磁盘/进程失败不是跨目录原子事务。CLI stdout 另含实际总文件字节数（包括 verification）与结束后的资源测量。

## 验证和归属

- 队友配置仅从 `terminal.episode.partners` 还原，不使用进程的 COOP_* 环境变量；校验 manifest 内容 hash、pool/split/seed/fixed、按 seed 重新选出的三席身份。
- 每一步比较 round、candidateIndex 及整个 candidate 对象（slot/UID/id/price/tier/golden/bonds），调用原 `env.step(index)`；同时观察实际 learner 顶层方法调用，要求恰好一次、方法/参数匹配且 accepted=true。不搜索替代动作。
- 每步规则、配置、controller 不可漂移，engine/action/controller errors 必须为零，原 CoopEnv 的 fatal rejection/健康检查保留。完整结束时深比较 **整个 summary、整个 partnerAudit（含 actionSHA256）、整个 terminal**，唯一排除项是 CLI 传输包装的 `ok`。缺/多动作、淘汰状态、LP、诊断或其他 terminal 字段漂移都会失败。
- 依赖注入到 `CoopEnv.create` 返回实例的 `Match`，保持同一个 CoopEnv/bot/controller；ObservedMatch 在 `super` 后、`m.start()` 前安装钩子，因此 reset draft 也捕捉。继承原 Battle，仅观察原方法和事件，不消费或替换引擎队列。
- 方法包装支持原 env 再包装、临时禁售替换和不同方法嵌套。source 根据被调用席位分配：learner policy、partner-controller、fixed-draft-controller、budget-controller、engine。调度队友期间 env.source 即使仍为 policy，也绝不将队友方法标成模型动作。
- 默认 verify 不保存任何四席私有 observation；record 仅在离线终局回放包中记录每席自身 private+public 视图，未接入模型输入。输出副本映射 learner→ai_0、partner-i→ai_i，同时映射 fieldId 后缀、perPlayer/dps 键；引擎 ID、种子、动作 UID 不改。JSONL transitions 与 terminal 保留原引擎 ID。
- 已淘汰席位的最后一个真实决策可以早于最后一轮。每席末次决策附真实终局 observation，执行后的视图另存 methodObservation，原执行前视图另存 beforeObservation；没有伪造最后一轮 ready。

## 原页面与索引合入

沿用 `arknights.bot-transitions.v1`、原 episode/recording schema 和原 `mergeIndexes`。原 `agentReplay.js` 的 `showBattle` 仅按 round 找第一条 battle，**不随席位选择战场，也无法在同轮直接选第二场**。因此 exporter 创建共享录像文件的多个原生 episode 入口（`-viewN`），把相应战场排在每轮首位；每个入口仍包含四席真实动作/私有视图，所有战场至少在一个入口可访问。没有删减其他战场。代价是 episode JSON 与下载副本有重复，也计入输出预算。

主线程 review 后可将新文件合入 `public/dev/agent/` 与 `public/dev/recordings/`，通过纯函数生成新索引，不能拿导出的 index 覆盖现有 index：

```js
import { mergeIndexes } from './coop-replay/export.mjs';
const merged = mergeIndexes(existingAgent, existingRecordings, addedAgent, addedRecordings);
// 不写文件：发布方持有锁/CAS 后自行合入 merged.agent / merged.recordings。
```

该函数拒绝重复 ID/name/file、危险路径、schema 不符及悬空 battle 引用，保留旧条目并重算 totals。**本次不发布**。以后通过原页面 `/?replay=<episode id>` 播放；想看完整第 14 轮胜局，请选存活的 **AI 2 或 AI 3**（partner-1/2）观看 R14，learner 的真实决策止于其淘汰回合。同轮其他战场从 episode 下拉中的战场入口选择，私有席位另由 player 下拉选择。

本地实际验证结果和未测范围见 [VALIDATION.md](VALIDATION.md)。原 UI 函数 mock 测试不是 native browser 视觉验证；server 真实完整录制尚未测试。
