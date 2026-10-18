# Local synthetic checks and source review

2026-10-18，base `ffbe8cc6b994f0676cb386b7c9f88d9e8281333f`。仅 `wide-dev/` 新增文件；没有改动原 F、P、model、loader、game 或训练代码。

检查命令：

```sh
python3 -B wide-dev/check.py
```

监督器为独立测试进程树设置 19s kill 阈值、合计 RSS≤1GiB、临时产物≤5,000,000 bytes，另设每测试进程 1GiB address-space 和单文件 5MB hard limit。临时证据结束后清理。最近结果：

```text
Ran 13 tests in 0.869s
OK
Synthetic only: seconds=0.990; peakRSSMiB=65.54; peakOutputBytes=1105901
```

## 已审源码接线

| 合同 | 实际接线/检查 |
| --- | --- |
| F 完成训练 | `admission_wide.report_contract/admit` 对照原 `bc-fit-budget/training.py:fit` 和 `run.py` 的真实报告结构：完整 finalChecks、28 附件、checkpoint metadata、访问索引、Adam state、父参数/critic；不调用 F.evaluate 或训练函数 |
| 原模型 | 父进程和 worker 均用原 `C.learner(CODE)`、`L.load_checkpoint`、`F.model_guard`；5267/419/128、标准 checkpoint、CPU float32；无模型副本实现 |
| 实际推理 | `workers.perform_job` 直接调用 `prep-timing/run.py` 的 P.game_job，8 个位置参数，无 protocol override；worker 每局前后检查实际模型并回传 proof，父进程验证后才封存 |
| 原协议语义 | 原 P 的每-response controller version 校验、E.state_contract 无 model-name 白名单、C.terminal_contract 默认 teacher=False、E.Protocol close ack/EOF/exit/stderr/killpg 均保留 |
| 源码与部署 | pins 是 base 的实际文件 SHA；bootstrap 导入前检查；F source 单独匹配训练报告，P source/实际 CODE HEAD 和新增代码首末匹配；actual CLI 与 `--experiment-code` 一致 |
| 进程池 | `spawn`、initializer 一次加载两模型、最多16 worker、继承≤16CPU affinity、Torch intra/inter1；进程失败按 unsealed 处理，subreaper 回收遗留独立子进程组 |
| 调度 | 2048 行先写 manifest；pending 预留整局额度，封存后实际结算；整批64GiB包括 metadata；异常停止派发并收取 pending 后 global failure；绝对截止终止卡死 worker |
| 最终信用 | source/parent/candidate/input/training/game486/model/proof/payload/result 全部 guard；inode/symlink、集合和 SHA/正文检查；漂移撤销全部胜局信用，原逐局 result 保留；最终报告由原目录 fd 原子发布 |

只做本会话源码自审，未调用子 agent，也不称独立 reviewer 验收。

## 合成覆盖

- 实际 CLI 在无 Node PATH、缺父 checkpoint 的合成 ROOT 下失败，仍保留完整 2048 行 manifest 和 2048 unrun；允许 collector 文件直接位于 ROOT，不错误禁止 sibling 输出目录。
- 2048 唯一行、1..256、两模型/两控制器/两 pool、256 paired groups、每 cell256；团队/个人胜分列，完整 baseline未胜→candidate胜与 baseline技术失败→candidate胜分开。
- 全2048条内存模拟调度、资源预留不足、截止前未运行；future exception 停新派发、pending 收取、unsealed 标记和全部信用撤销。
- **原 P.game_job + 原 E.Protocol**，通过 P 已有的显式 protocol 参数连接 Python 假子进程；无 Node/game。验证任意 model name、off/deferred版本、实际 CLI loader路径/env、actor仅接收features、默认teacher=False允许的诊断拒绝、团队胜但个人败、close ack/EOF；错误version/event/STOP/额外stdout/stderr/exit/timeout均失败并留证。
- 真正的 spawn 进程池运行纯合成 worker，两次任务同 PID、initializer一次、继承限定 affinity、无 Torch；worker 崩溃遗留独立 Python peer 组可由 subreaper 清理；hard shutdown 同时终止卡住的 worker 和其独立子进程组。
- 实际参数/identity proof 的前后值、冻结、线程数、source/checkpoint/job 任一不匹配都在父封存前拒绝；proof文件漂移拒绝。此项使用小型身份字典，不冒充真实模型加载验收。
- payload/actions/terminal/stderr/result 任意字节变化，result正文与内存记录不符、episode目录同内容替换inode或symlink，受保护文件/祖先目录inode与空目录集合变化均拒绝。
- 只有 complete/3000updates/102000draws 的伪报告，即使传入其自身 SHA，也不能通过完整 F 报告合同，不会到 checkpoint loader。
- 实际 source pin检查、原 P默认Protocol绑定、实际 P调用AST（不覆写protocol）、Python3.10语法AST、无训练调用。AST不是Python3.10 runtime验证。

## 未执行

**生产未测。**没有读取实际 checkpoint/真实训练输入，没有初始化实际 Torch learner、没有真实 F 准入或 worker 模型加载验证，没有训练/BCfit/P2，没有 Node/static loader/game/Match/Battle，没有2048局生产评测、SSH、下载、安装或服务器 HEAD 调整。

真实吞吐、2400s能完成的局数、真实模型/数据能否通过准入、游戏证据体积和胜率全部未知。server 的 96局464s / 串行128局1699s 是用户提供的已有观测，本次没有复测。进程池解除跨worker的Python GIL竞争不等于保证预算内完成；不足时保留固定分母并失败。
