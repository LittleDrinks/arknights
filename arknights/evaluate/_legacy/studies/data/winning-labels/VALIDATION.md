# 验证记录

基线 `ce9c4c6f624b1093872b3192db95a122aafaf0e0`，规则 `6ab2468279e926d107788f63d7fe219b85f67b4a`。2026-10-18 本地 Node v24.21.0。仅本目录新增文件，无游戏运行、训练、SSH、安装、子 agent 或其他模块修改。

执行：

```sh
/usr/bin/time -v timeout 20s node --max-old-space-size=512 --test --test-concurrency=1 winning-labels/core.test.mjs winning-labels/integration.test.mjs
/usr/bin/time -v timeout 20s node --max-old-space-size=512 winning-labels/extract.mjs
```

8/8 测试通过，测试整套 wall time 3.78 秒，time 报告最大单进程 RSS 482,644 KiB（约 471.3 MiB）。默认新目录提取 wall time 1.21 秒，峰值 RSS 483,292 KiB（471.96 MiB）。提取器内部记录 1,108 ms；它不包含后续 gzip/落盘，外层 time 包含。默认输出目录随后仅在本目录内重命名为 `evidence/`，用于本次 commit 审阅。没有覆盖任何已有目录或修改输入。

审计输出：[evidence/audit.json](evidence/audit.json)。整组输出 164,168 字节，小于 5 MB。四份 gzip 内容及 SHA-256 在 audit 中；完整 raw terminal、schema、rules、来源 hash、四席对照、计数与限制在 metadata 中。gzip 只是存储格式，`samples` 解压每行仍是原 `bc_update` 所需的纯 features/action 对象。

| 席位 | buy | sell | refresh | levelUp | ready | positive 总数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| partner-1 | 39 | 9 | 26 | 5 | 14 | 93 |
| partner-2 | 50 | 14 | 31 | 5 | 14 | 114 |
| learner | — | — | — | — | — | 0 |
| partner-3 | — | — | — | — | — | 0 |

完整 raw 的 1,066 条 transition 全部在 dispositions：207 条 positive、134 条失败席位的合法决策（68 primary + 66 partner-3）、725 条其他 method。实际 raw 无 rejected/unmapped 或 parent-child 嵌套；这些分支用合成测试覆盖。所有标签均来自实际 accepted 调用且对应当前合法 mask，candidate 全字段绑定。两个 positive 席位均保留到 R14，没有剪掉尾部。**独立对局数是 1。**

primary 全部 68 条逐字段 exact：每个 global 标量、候选向量标量、mask、action，没有浮点容差。global 5,267 维、candidate 419 维。另验证原 policy 的 candidate 全字段/index/count 与最终 primary 的 totals/byId/rounds/pieces/prepSteps/prepBudget。

覆盖的失败路径与边界：

- parent 在 child 完成后更新；刷新仅暴露 parent 调用前的棋子；firstRound 保留、跨轮 exposure/prepSteps 重置。
- 原 observe 的独立对照；teacher 与 actual policy 在 after-observe 两侧计步；reward/controller/resolveTemp 不冒充 policy 步，清理 sell 仍计出售历史。
- 五类候选及顺序、满手、无资金、freeRefreshes、无效/DIY/sold 槽位、golden、非 PREP、dead/ready、mask；全字段篡改被拒绝。
- 拒绝与不可映射事件保留单列、不生成标签；非法调用树失败。
- reference 第 0 条 global[0] 人为改错时，报告 `primary step=0 seq=4` 与首差路径/actual/expected，退出非零且输出目录不存在。
- 完整 raw 缺终局、终局 error、最后一条异常、失败席位假胜利、错席 private 均失败。正常样本读取不影响输入 SHA。
- 已有输出拒绝且内容哈希不变；跨目录和父目录符号链接逃逸拒绝；audit 不落盘。

输入压缩字节 SHA-256：

```text
raw       8452bcb433452dbbf9b7aaed6a96f40630f7e5ff154bd95b7d94380374e6917a
reference 53cccec88914f077e55e6aaee0679084695c8190c840962ec92455f7954387f2
```

未执行：真实队友 teacher 环境对照、任何 Match/Battle、训练、评估或新轨迹泛化。队友历史的可观察口径和与 actual primary 的区别见 [README.md](README.md)。这些验证不是“专家泛化”或冻结模型胜率证明。

## 独立审查后的终局准入修复

17225e4 的独立审查复算确认 primary68 全字段一致、207 survivor 样本及历史一致，但发现终局拒绝路径遗漏：相等非零 controller counters、错误 reward/controller、同改 partnerAudit 两份别名时未完整拒绝。已只在本目录修复；依据原 coop/env.mjs recorder、已精确复现的固定 raw 文件和完整终局作为信任起点。显式校验该 capture 的零错误/过滤计数、reward=1、已知 controller、partnerAudit 别名，再固定完整 terminal 规范 JSON 与解压 raw 内容 SHA。只针对该已核验输入，不泛化接受任意新轨迹，不从终局生成特征。

新增故障回归覆盖上述四类，8/8 契约测试通过，3.79秒。修复期间两次测试失败为测试代码问题（变量重名、测试未同步第三份 provenance 别名）；修正后重跑通过，未改准入条件迎合故障。新提取产物单独写 verified-v2/，不覆盖旧 evidence/；samples/labels/dispositions 内容不变，新 metadata 记录修复后实现 SHA。旧 evidence 保留其原始 provenance。仍未训练/模拟。
