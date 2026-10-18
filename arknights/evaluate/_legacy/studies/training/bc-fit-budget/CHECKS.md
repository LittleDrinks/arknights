# Synthetic validation

2026-10-18，本地 base `d69260ca15ee13b4cf150e1a89ecfd0d66dcb69b`。

```text
/home/q2635/venvs/logics-v3/bin/python -B bc-fit-budget/check.py

Ran 10 tests in 7.237s
OK
Synthetic only: seconds=7.696; peakRSSMiB=632.48; peakOutputBytes=67869
```

整个测试子树由监督器约束：19s kill 阈值、合计 RSS≤1GiB、临时输出≤5,000,000 bytes。

已执行：

- 原 `L.ActorCritic(2,2,2)` + 原 `L.bc_update` + 原 `T.Draws(6800)`，498条微型合成标签，两 arm 分别5/15 epochs、1000/3000更新。核对34000/102000次访问、seed101连续排列、前五轮相等、新 Adam 各参数 step、共同初始父模型、父模型未变、critic head未变、标准 checkpoint回读和动作accuracy。
- 合成381 ids/23 bonds/40 bands的6410→5267 OFF投影；candidate419/mask原对象保留，拒绝非零OFF尾列。不加载游戏词表或真实样本。
- 原 artifact 函数的 gzip CRC/EOF、raw SHA、重复JSON/垃圾尾/截断拒绝。
- 输入字节与raw文件集合漂移、祖先/父模型symlink、错误父模型字节拒绝。
- 96项模拟调度、6个技术失败、完全未运行、每cell16分母、团队与个人胜分列。
- payload/terminal/actions/stderr/result篡改、result正文与内存记录不一致、未知额外文件、game目录替换symlink拒绝；全局invalidator撤销成功信用。
- 原 `E.Protocol` 对Python假子进程的close ack、正常EOF、额外stdout、stderr、异常exit、绝对超时和cleanup。
- 实际eval CLI在合成空ROOT下准入失败，PATH无Node，仍落盘96项manifest及96 unrun失败summary；未到达Torch/game/static loader。
- 当前源码SHA守卫、Python3.10 AST语法检查，以及评测直接调用原 `E.game_job` 的连接检查。

未执行：真实6+12样本准入、真实hidden128父模型加载、真实训练、Node/game/static loader/Match/Battle、96局开发评估、服务器操作。微型合成结果不能证明真实数据的训练耗时、准确率或胜率；3.10 AST检查不是3.10 runtime验收。真实运行结果由main按README命令取得。

服务器首末会核对：固定父模型SHA、固定recapture manifest SHA、完整486规则文件、静态词表、全部复用源码/本目录文件、原输入与训练证据、checkpoint与内存参数，以及payload/result/目录非symlink。最终身份写入 `finalChecks`；本文件不是这些服务器检查已经通过的声明。
