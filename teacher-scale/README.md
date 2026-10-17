# 有限自然教师采集

仅新增 `teacher-scale/`，基线 `ffbe8cc6b994f0676cb386b7c9f88d9e8281333f`。
本 CLI 只采集，不导入 learner/torch、不训练、不评测、不读取 dev/val/sealed 数据。
旧 `preview-tools/collect-natural-coop-teachers.py` 仅作只读参考；其身份保留在 pins 中，
服务器无需部署或执行旧 collector。本次开发仅运行合成 Python line server，没有运行真实
CoopEnv、Match、Battle 或真实数据采样，没有 SSH、安装或子 agent。

## main 在服务器执行

使用服务器现有 Linux、Python 3.10+、游戏所需 Node 环境。代码目录须完整部署本提交，
`--root` 下恰有一个 `game/` 或 `Stronghold-Protocol/`，输出父目录须已存在。

```bash
taskset -c 32-63 python3 -B /data1/zsm/arknights/experiment-code/teacher-scale/run.py \
  --root /data1/zsm/arknights \
  --out experiments/natural-coop-teacher-scale-v1
```

`32-63` 由 main 按服务器授权指定。CLI 只收窄继承 affinity：最多取其中 32 个可用 CPU，
不扩展到其他 CPU；更窄的继承 mask 也不会扩展。32 条持久 lane（normal/mixed 各16）
继承该 mask，OMP/MKL/OpenBLAS/UV 线程数设1。不会每局新建进程或重启失败 lane。
除了 root/out 没有生产参数：无小计划、改 seed、改 pool/split、改预算、增 worker、续跑开关。
输出已存在（包括悬空 symlink）或任一输出路径祖先是 symlink 均拒绝；不覆盖旧批次。

固定计划顺序：`seed=32001..36096 × (normal,mixed)`，8192 行、4096 个 paired training seeds。
lane 为 `(seed-32001)%16`，每 lane 256 个计划项。固定请求：
`{cmd:demo, seed, mode:coop, difficulty:ABYSS, budget:128}`。
原 CoopEnv demo 使用原 bot/rehearsal=0；环境固定 `COOP_SPLIT=train`、poolSeed313，
normal 三席 original-v1，mixed 为 original-v1/buy2-v1/saver-v1，对应原 pool families。
同 seed 两个 pool 属于同一 episode group，不是两个独立地图样本。

## 时间、wire 和磁盘边界

- 从入口开始计 3600 秒工作截止；每 case（响应、落盘、合同、prefix fsync）最多120秒，
  且不能越过工作截止。截止前不再开始新 case；已开始超时会记失败，不记 not-run。
- 最多再180秒用于 lane 结束、源校验、独立冻结与失败报告。每 lane close 最多5秒，
  kill 后 wait 最多2秒；主调度在工作截止后8秒仍有 worker 时强制清理。
  静态 guard 子进程也有私有进程组、5/10秒期限及 kill/wait 清理。
  主线程总期限覆盖静态检查/冻结；异常时保留 failed/unfrozen，不伪造冻结成功。
  最后的失败 manifest 落盘/内核不可中断 I/O 不属于硬实时保证。
- 单请求 stdout+stderr 在 `pump/charge` 按 delta 计数，读取长度先受剩余配额约束，
  到64MiB即停止，先于 JSON 解析。没有无界 readline、无界 Queue：最多固定8192条
  started和8192条completed事件及32条lane事件。每条只有 metadata，不携带 raw 对象。
  JSON 的 Python 对象内存可能大于 wire 大小；没有声称生产总 RSS 是64MiB。
- 所有 lane wire（包括 close 响应）共享 `8192×64MiB=512GiB` **未压缩**上限。
  它可大于磁盘占用；这不意味着允许512GiB落盘，也不是预分配。
- 64GiB **文件内容字节**全局磁盘上限由线程锁保护：gzip headers/trailers、lane stdout、
  case gzip、stderr、请求日志、journals、源证据、plan、manifest、stage 和原子快照新旧重叠
  都计入。普通数据预留256MiB给metadata/失败记录；磁盘预算不足立即失败，未读的超限
  输出不会声称已完整保留。目录/文件系统分配粒度与外部文件不属于内容字节计数。

## 持久化、P2修复与 EOF

`plan.json` 在采集前写出全8192行。每个 case 的文件名含唯一 index/pool/seed：

1. `journals/<case>.started.json` 独占发布并 fsync，包含 index/seed/pool/lane、startedAt、
   finishedAt=null、status=started、stdout/stderr 起点，随后逐条 Queue 通知 main。
2. lane `actions.jsonl` 持久化带 index/seed/pool 的请求意图，然后才发 demo。
3. 每个收到换行的完整 frame 原字节写 `cases/<case>.json.gz`，包括 ok:false 和 malformed
   JSON。stage gzip 完成 trailer、文件 fsync、独占 link 发布、目录 fsync 后才解析/验合同。
4. lane gzip prefix、stderr 和请求日志 fsync 后，独占发布 `completed.json`，记录结束时间、
   状态、raw 压缩/展开 SHA与大小、wire 区间、started journal SHA，再逐条 Queue 通知 main。
   不把整条 lane 的结果暂存到结束才登记。main 至少每2秒尝试原子 manifest 快照。
5. lane 结束使用原 `E.Protocol.finish` 检查 close ACK、stdout/stderr 真 EOF、正常退出及空
   尾随 buffer，再清理私有进程组。失败情况下仍有限尝试 close，失败事实保留。

`LaneProtocol` 是私有 `E.Protocol` subclass；只替换实例 stream 和有界 pump/receive，
不 monkeypatch 旧模块 globals。stdout 从首次读取起流式 gzip，无大型未压缩临时 wire。
`lanes/<lane>/stdout.gz` 保留实际读取的 malformed、partial 和错误字节；stderr 原字节另存，
不把截断 repr 当完整错误。部分响应没有 case JSON 文件，证据留在 lane wire 和失败 journal。

**每 case 的 EOF 指单个 JSON/newline 与独立 case gzip 的完整结束，不是子进程 EOF。**
case 的 `processEOF=false`；lane gzip 仅在 lane 结束才有最终 trailer，case完成时只保证其
prefix已持久化。意外断电/SIGKILL可能留下无trailer的lane gzip或stage，整批不能通过冻结。
可捕获的 SIGINT/SIGTERM 会停止后续请求并进入有限清理；不提供恢复/重新标记成功功能。

## 合同和结果口径

完整复用 `A.complete_contract`，**不调用 `A.frozen`、旧 PLAN 或 admit/train/eval**。
保留完整 terminal、summary、samples、负例、拒绝诊断及错误响应。`teacher=True` 的原拒绝
计数合同不改；不清零或重写 diagnostics。无效 demo/协议/清理导致批次 globalFailed。

每个完整有效 case 分别记录 teamVictory、learnerVictory、learnerAlive、learnerRounds、
rejectedLabels、samples。eligiblePositive 必须同时团队胜、个人胜、alive、个人14轮、零
拒绝标签且完整合同通过。被队友 carry 的死亡 learner、15轮胜利、有拒绝标签的胜局和负例
都保留，但不混称14轮正例。eligiblePositive 只是数据分类，不是训练许可或评测指标。

前后验证 pins 内原 rl/coop 与复用源码哈希/文件集合、collector自有文件集合/哈希、
游戏 HEAD `6ab2468279e926d107788f63d7fe219b85f67b4a`、486 rules文件（含package与lock）、
静态 data SHA及 schema `[5267,419]`。静态检查运行原 pinned `static.mjs`，不构造 Match。
哈希证明身份，不替代 main 对数据来源的独立验收。

## 独立冻结和后续准入

worker全退出后，从磁盘独立扫描所有journals、请求日志和所有raw/stage，不使用 Queue
或快照推断执行集合。验证唯一计划、started/completed身份及SHA绑定、命令与started集合、
case完整gzip/payload SHA、lane文件SHA/集合；成功lane另流式对比每case payload与stdout
字节区间，并验证最终ACK/gzip EOF。缺失、孤立、重复、未知路径、SHA漂移或任何lane失败
使整批失败。失败wire仍独立计算压缩文件SHA；不声称失败lane通过成功lane的EOF/区间合同。

最终manifest始终保留8192个计划位置。started缺completed为interrupted；raw或请求证明已执行
也不能记not-run。缺失lane/无法归属的证据使用unknown。只有完整对账证明未开始的行才能
是not-run。未能完成冻结时保留 `frozen=false` 和失败原因，缺乏独立依据的未开始状态改为
unknown，不靠补造completed掩盖异常。

`files`/`filesSHA256` 绑定 plan、源证据、started/completed journals、lane journals及全部raw
文件集合；manifest不自包含自己的SHA，CLI在退出时打印其SHA。`frozen=true`表示本次独立
inventory扫描结束，仍须检查globalFailed/status/errors；失败证据也可以冻结，但不得训练。
任何 globalFailed 一律拒绝整批；`trainingAllowed` 永远为false，交由 main 独立来源验收、
冻结后另实现新的8192计划准入。旧2048准入器不能直接读取此批次。

退出码：全8192项完成且无全局错误为0；deadline留下not-run的incomplete、failed或unfrozen
均非零。进度快照的running状态也不能入场。实际ENOSPC/断电可能阻止最终manifest写入；
之前的running快照和已落盘journals仍不得当作成功批次。

## 本地合成验证

```bash
python3 -B teacher-scale/check.py
```

监督器限制整个合成测试子树 <20秒、合计采样RSS≤1GiB、临时输出≤5MB；临时文件自动清理。
使用小型内部fixture和Python假协议进程，没有生产放松开关。覆盖持久lane逐条登记、完整
错误/负例、malformed/partial、读前字节上限、超时进程组清理、坏ACK/尾随/stderr、落盘后
journal故障、缺失/孤立/重复/SHA篡改、磁盘预算并发、cleanup异常、CPU不扩展、8192行
无执行冻结、source drift、symlink/覆盖拒绝以及原teacher诊断合同。
兼容语法按Python3.10 AST检查；没有声称服务器runtime或真实采集已验收。

本次本地结果：17项测试通过，监督器耗时4.824秒，合计采样峰值RSS 62.35MiB，临时输出
峰值674023字节；未执行生产采集命令。
