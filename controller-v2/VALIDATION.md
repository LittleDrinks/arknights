# 本次有限验证

2026-10-18，本地 CPU，未 SSH、装依赖、启动子 agent 或创建 goal。只有 `controller-v2/` 新文件；`rl/` 和游戏源码未修改。运行的是现有 `../experiment-rl/rl/artifacts/bc.pt`，没有重新训练或造 checkpoint。

配对使用 NORMAL、seed 60001、prep budget 128、greedy，同一个权重 SHA256：
`ca20165cf3cba2d240de04bc31c2c3556ed7feb86fca356371e12d7336c693bb`。

| 完整自然局 | 胜利 | 引擎 roundsPassed | policy steps | engine/action/controller errors | 非 policy 购物经济动作 |
| --- | --- | --- | --- | --- | --- |
| loader off / v1 | false | 7 | 16 | 0 / 0 / 0 | 0 |
| loader v2 | false | 7 | 16 | 0 / 0 / 0 | 0 |

两局都执行到真正 `summary` 终局，购物动作 hash 都是 `0f85248cfb7e27253bd23da74fccf3be50d94b8718cf0fc85a665911455d9f1a`。v2 正确返回新 controller 版本；checkpoint 的 rules/schema 在第一步动作前校验通过。未跑 HARD/ABYSS，也没有追加其他完整局。

v2 自然局审计只有 8 次原 arrange move 成功和 3 条跨轮忽略信标的记录，没有实际 equip/useArt 成功：本局自然获得的物品是信标，已被保留。自然证据证明这个 seed 没有代做购物、没有错误或未知物品静默清除；**未覆盖自然消耗品/Art 使用，不能证明 v2 提升胜率或降低策略天花板，更不是通关承诺**。

真实接口 fixture 人工授予了棋子/物品（含 displayed item grants），通过原 PlayerState 方法验证资金增加、商店免费获得、画卷复制、金投影晋升、“神秘顾客”销毁奖励、教鞭保留，以及填满所有载体后消耗品不能替换旧装备。其余 fixture 覆盖角色/盟约选择、普通对精锐的晋升筛选、Art 上限/手牌容量、未知与危险物品保留、错误 UID 不再尝试、预算、禁止购物、强制溢出显式 stop、方法恢复、免费 offer 筛选、loader off 原对象返回、SHA 漂移拒绝、仅两处内存替换。最终 6 tests / 6 pass；fixture 不是自然对局，不计胜率。

配对评测含 Python/模型加载总耗时 9.707 秒，监控到的 Python + Node + loader 进程树与监控父进程合计峰值 RSS 760557568 bytes（725.3 MiB），低于 768 MiB。每 50ms 采样一次 RSS，不能把采样峰值冒称内核精确高水位。watchdog 限额为 40 秒（为 fixture 留出余量）、768 MiB、目录产物 20 MiB；触发即杀掉评测进程组并记录原因。本次 stopReason=null。fixture 调试共五次短测试执行，合计约 3.6 秒，无完整战斗对局，连同两局评测低于 45 秒 sim 预算。

- [results/off.json](results/off.json)、[results/v2.json](results/v2.json)：policy 动作、引擎 history、终局 summary；没有人工注入。
- [results/summary.json](results/summary.json)：两局指标和 checkpoint hash。
- [results/resources.json](results/resources.json)：资源监控结果。
- [results/tests.txt](results/tests.txt)：最终测试输出。
- [results/provenance.json](results/provenance.json)：控制器、loader、固定 env/游戏与 checkpoint 的 hash。

待独立 review：原 arrange 的继承信息边界；保守停机与上游强制 Ready 的兼容性；自然物品使用覆盖不足；同盟约获得可能无产出的取舍；目标评分和预算是否适合扩大评测。此提交不扩大训练或评测规模。
