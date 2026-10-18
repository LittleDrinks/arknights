# 冻结 BC seed65 → 原页面离线回放

基线 `4b6069b1e2000f6e4b62195c488a3e78c285dd2c`。只新增 `frozen-replay/`，并在
`coop-replay/` 增加必要的显式 profile 扩展。旧 training CLI、默认 API、kind/controller、
默认 trace SHA、Recorder 和 bundle 文案语义不变，旧测试文件不改。
不修改 rl/coop/prep-timing/game/前端及其他模块，不训练、不安装、不 SSH、不使用子 agent。

本例是**开发集团队胜局**：seed65、ABYSS、80 个学习席动作，团队胜利 R14；
学习席 R12 淘汰、个人 `victory=false`、`roundsPassed=11`，三名原版队友存活 14 轮。
冻结 checkpoint SHA256：

```text
af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b
```

main 提供的独立原 ActorCritic Python 推理复现记录在
`experiments/wide-seed65-reproduction-v1.json`：baseline-deferred/原版队友失败 R13，
steps3000 新模型团队胜利 R14；两项各自的 protocol/actions/terminal 与原结果逐字节一致。
本 exporter 不重复执行或独立认证这份 Python 推理，而是验证 receipt 文件 SHA、内部引用
和结果契约，并将其作为有归属的外部证据记录。Node **只做 saved-action replay**，
自身 `inferenceRerun=false`；嵌套的 `reportedInferenceRerun=true` 仅来自该 receipt。
2048 整宽开发评估在该 receipt 中尚未完成，本例不证明整体泛化提高。

## 输入与 profile

固定 profile：`frozen-eval-seed65/v1`。仅接受：

```text
kind = frozen-policy-evaluation-trajectory/v1
controller = coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1+prep-timing/deferred-v1
```

真实小 trace 为 416,577 bytes / 80 actions：

| 文件 | SHA256 |
| --- | --- |
| `wide-seed65-frozen-trace-v1.json` | `d1fdb2b3ad671f0e342cc0229fd52c1500ee0a546e2574463d71da973d3265c7` |
| `wide-seed65-reproduction-v1.json` | `6b01ecc49b507b3b320118c454b644cc46059a936bcfad70ab3ac80a7d1c9922` |

新命令**必须显式传入两份 SHA**，不回退到 training SHA。receipt ≤1 MB、trace ≤12 MB，
先核对文件 SHA 再解析；receipt 必须绑定 trace SHA、reproduction manifest SHA、原 loader SHA，
两项完整复现比较及 evidence 文件摘要必须相符。它不读取大 protocol、checkpoint 或训练数据。

队友配置从真实 `terminal.episode.partners` 还原并交给 `CoopEnv.create`，逐项核验
manifest 内容 hash、pool/split/poolSeed/fixed 和重选后的三席身份。
此 profile 固定要求 `eval/normal/313/[original-v1,original-v1,original-v1]`，
不继承 COOP 环境，不用字符串替换伪装旧 training kind。

worker 启动参数在任何 `CoopEnv` 导入前安装现有 **`prep-timing/loader.mjs`**
（wide-dev 所用原 prep 模块的 loader 来源），并设 `ARK_PREP_TIMING=deferred`。
父进程可加载未变换模块；实际模拟只在独立 worker 中执行。清除 worker 继承的
ARK/COOP/STRONGHOLD/NODE 配置，不修改旧 Python 模块 globals 或磁盘源码。
loader SHA 固定为 `e45798398eaac83f408d849a614caa5db63c7fe7321712b0265a2607345ef44b`；
其原生 exact URL + 完整 `rl/env.mjs` SHA 检查继续生效。

## main 在 server 隔离部署后执行

本地 worker 只运行下一节小 fixture。以下真实 verify/record 命令由 main 在 server 执行，
本次未执行。需要已有 Node、Linux `/proc`、已安装依赖的游戏 checkout。
游戏规则 pin 为 `6ab2468279e926d107788f63d7fe219b85f67b4a`，tracked server/shared/data
必须干净。先将本提交放入独立代码目录；输出父目录须已存在且位于 game 树外。

在代码 checkout 根目录，先 verify；完整终局通过后，另起 record：

```bash
node frozen-replay/cli.mjs --mode verify \
  --trace /path/to/experiments/wide-seed65-frozen-trace-v1.json \
  --trace-sha256 d1fdb2b3ad671f0e342cc0229fd52c1500ee0a546e2574463d71da973d3265c7 \
  --receipt /path/to/experiments/wide-seed65-reproduction-v1.json \
  --receipt-sha256 6b01ecc49b507b3b320118c454b644cc46059a936bcfad70ab3ac80a7d1c9922 \
  --game /path/to/game --out /path/to/isolated-exports/frozen65-verify-NEW \
  --seconds 90 --rss-mb 2048 --heap-mb 1536 --max-output-mb 256

node frozen-replay/cli.mjs --mode record \
  --trace /path/to/experiments/wide-seed65-frozen-trace-v1.json \
  --trace-sha256 d1fdb2b3ad671f0e342cc0229fd52c1500ee0a546e2574463d71da973d3265c7 \
  --receipt /path/to/experiments/wide-seed65-reproduction-v1.json \
  --receipt-sha256 6b01ecc49b507b3b320118c454b644cc46059a936bcfad70ab3ac80a7d1c9922 \
  --game /path/to/game --out /path/to/isolated-exports/frozen65-record-NEW \
  --seconds 90 --rss-mb 2048 --heap-mb 1536 --max-output-mb 256 --frame-every 3
```

默认 `--mode verify`。原预算实现保持：默认 wall 12s、worker heap 512 MiB、
RSS 768 MiB、输出 12,000,000 bytes；父进程 20ms 采样 RSS/HWM、wall watchdog SIGKILL，
worker 同步动作和战斗 tick 检查并锁存预算失败。没有增加并行 worker、CPU 线程或自动放宽预算。
RSS 是采样/边界保护，不是 cgroup 硬内存隔离；输出含全部副本及 verification。
命令中的较大预算只供 server main 显式选择。MB 为十进制文件字节，内存为 MiB。

server 诊断可在 verify 命令中增加 `--prefix N`。N 小于 80 时只生成诊断报告，
`completeTerminal/completeSummary/completePartnerAudit=false`、stdout `victory=null`，
不生成 agent/index、下载或录像；record prefix 直接拒绝。N=80 必须通过完整终局验证。
record 本身也重新执行全部核对，不信任先前 verify 的成功声明。

逐步核对 round、candidateIndex、整个候选对象，实际调用原 `env.step`，确认恰好一次
learner 顶层 policy 方法、正确参数且 accepted=true；不找替代动作。
每步检查规则、controller、episode、诊断计数；终局深比较整个 summary、整个 partnerAudit
（含动作 SHA）、整个 terminal，唯一排除项是传输包装 `ok`。缺/多动作或任意终局漂移均失败。
只有 full terminal exact 后才构建播放包。不会修改 receipt 来使不匹配通过。

## 原生 bundle 与观看

复用现有 Recorder / all-real-fields / exports / `arknights.bot-transitions.v1`，
输出 ID `frozen-bc-eval-s65-d1fdb2b3ad67`，默认标签：
**冻结BC开发团队胜局(学习席R12淘汰)**。episode label、recording title、source、note、
下载 metadata、provenance 均使用冻结 eval 含义，不保留跨 PPO 行为版本说明。

```text
<out>/verification.json
<out>/agent/index.json
<out>/agent/episodes/<id>[-viewN].json
<out>/agent/downloads/<id>[-viewN].jsonl.gz
<out>/recordings/index.json
<out>/recordings/<battle-name>.json
```

保留各席全部真实普通/联防/boss 战场，不只学习席；战场总数由实际录制报告决定，
不硬编码为 64。默认 t=0、每 3 ticks、最终帧，全部真实事件保留至下一帧；
`--frame-every 1` 才是逐 tick。不自动删战场或降采样规避预算。

原页面 `showBattle` 仅按 round 取第一场，不随 player 下拉切换战场；多个 `-viewN`
episode 入口让每场至少一次排在该轮第一位，录像文件共享，episode/下载副本计入预算。
学习席 **AI 1** 的最后真实决策在 R12；选 **AI 2、AI 3 或 AI 4** 均可选择 R14。
同轮另一战场通过 episode 入口选择，private 视角通过 player 选择。
末次真实决策附真实终局 observation，保留 before/methodObservation，不伪造已淘汰席位 R14 ready。
下载保留完整原引擎 terminal/IDs；viewer 副本才映射为 ai_0..ai_3。

输出目录必须全新，拒绝文件/目录/有效或悬空 symlink，全部文件 `wx`，agent index 最后写。
进程/磁盘失败可能留下不完整新目录，不清理、不覆盖，不是跨目录原子发布。
`verification.json` 记录 payload SHA/大小、来源 SHA、trace/receipt、profile、预算与录制计数。

**不发布 index，不触碰 native 38181。** main 后续审查真实导出与原页面后，若决定发布，
沿用 `coop-replay/export.mjs` 的纯函数 `mergeIndexes`，持锁/CAS 合入旧索引；
不能以导出 index 替换现有 index。本 CLI 不复制到 public，也不启动页面或服务。

## 本地受限验证

```bash
python3 -B frozen-replay/check.py
```

总预算 ≤20s、进程树 RSS ≤1 GiB、临时产物 ≤5,000,000 bytes，最多 4 核 affinity；
19s 停工保留清理余量。包含**未修改的旧 coop-replay tests**，真实 trace/receipt 仅静态读取，
原 deferred/off loader 配合假 player/scheduler/bot，64 个合成战场与原 `agentReplay.js`
函数在 VM 中运行（mock DOM/network/store）。不调用真实 create/reset/Match/Battle，
不执行真实 80 步，不加载 checkpoint，不训练。测试临时文件退出清理。

9 项合同测试已通过；具体测量见 [VALIDATION.md](VALIDATION.md)。这不等于 server 完整
verify/record 或 native 浏览器视觉验收。真实录制大小、帧数、耗时、RSS 和原页面播放仍待 main。

API 扩展：`validateTrace(trace, profile)`、`verifyState(state, trace, profile)`、
`Recorder({..., profile})`、`bundle({..., profile})`、`replay({...options, profile})`；
profile 必须为固定名字，未传时仍为 `training/v1`。冻结 metadata 由 SHA 绑定的
`receiptMetadata` 产生，不接受 CLI 随意覆盖 kind/controller/label 的入口。
直接调用冻结 replay API 的宿主同样必须在进程启动时安装原 loader；推荐始终使用受监督的新 CLI。
