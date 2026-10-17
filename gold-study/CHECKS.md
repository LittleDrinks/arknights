# 本地有限验证（2026-10-18）

基线 `2f4842a844aa17adea9360b4f85c6285e6a97319`。只新增 `gold-study/`。未 SSH、安装、子 agent、goal、下载大型数据、运行真实采样/Match/Battle/真实训练。

Python 使用已有 `/home/q2635/venvs/logics-v3/bin/python`。命令：

```sh
env PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES='' \
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  /usr/bin/time -f 'elapsed=%e maxRSS_KiB=%M' timeout 18s \
  /home/q2635/venvs/logics-v3/bin/python gold-study/tests.py
```

最终结果：

```text
Ran 9 tests in 2.876s
OK
maxRSS_KiB=635756; synthetic only
elapsed=3.32 maxRSS_KiB=760784
```

外部 time 记录的峰值约743 MiB，低于1 GiB；总时间低于20s。测试还断言 tiny 原 learner 两 arm 各200次更新合计<5s，以及各自有临时目录在清理前合计<5,000,000 bytes。没有并发遍历临时目录的监控器。stdout/stderr 输出不足1 KiB。

覆盖：

- 六组 ×83个合成标签、12个完整配对 gzip，真实准入函数验证 source manifest/selection、完整终局、全部旧新字段、raw/compressed SHA/bytes、CRC/EOF、状态与配对数。fixture 仅 mock collector 的实际 source binding，生产未提供跳过开关。保留完整 raw 副本；输入变动 guard 拒绝。
- 小词表106/16维、原 `L.ActorCritic` hidden4，使用同498标签、两 arm各6800 cyclic draws/200 updates。实际访问顺序一致，前326标签各14次、其余各13次，critic head未变，所有初始参数一致。三个 checkpoint 通过原 loader/validate roundtrip；baseline OFF/ON logits/value 容差等价。
- 实际 v2 schema/维度/tail 首先校验；未知 schema/mode、OFF非零、负值/NaN、维度不符拒绝。真实旧 state/terminal 合同在浅副本运行，default teacher=False 接受正确诊断记账；原 state/raw 不变。
- fakeProtocol 检查 loader 命令/child env、actor 收到原完整106维对象、动作候选/mask完整、终局 raw 和协议逐字节不改。缺少 EOF 时即使终局成功也判失败并保留 raw。
- 原 Protocol 仅运行三个极小 Python mock child；标准 close ack+EOF 接受，额外字节和 stderr 拒绝。没有 Node 环境子进程或游戏载入。
- mock 96局调度、90 complete/6 failed 的固定分母及个人/团队分离、期限已过时96 unrun、输入漂移拒绝、CLI 准入失败预写96计划、末尾 guard 失败撤销胜场计数、输出预算/nooverwrite。Python3.10语法与依赖 pins 检查。

第一轮有一项测试调用漏传必需的 `--trained`，因此 argparse 拒绝；这是测试调用错误，已修复。该轮 elapsed4.07s / maxRSS758212KiB。第二轮全部通过，elapsed3.33s / maxRSS756912KiB；随后补训练发布物末尾核验、每局结果预算预留及同498合成标签的次数断言，最终结果如上。三轮合计约10.72s，无扩大到真实数据的验证。

未验证：服务器实际六个自然胜局重采、全量真实训练性能/耗时、真实96局结果、胜率改善和泛化。main 必须先完成真实 paired recapture 并独立接受 source，再按 README 顺序运行。

Main rebase: moved isolated module onto fbd7c2b to use the reviewed parent-width fix and race-safe monitor; refreshed exact dependency hashes. Original game/rl/coop and gold feature code unchanged. Worker source commit7814f0b preserved; final review uses integration ref.
