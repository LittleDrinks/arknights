# 本次验证记录（2026-10-18）

基线 `53bc0f1a9f451b61bde7c209c8bf013b81540517`，仅新增 `coop-replay/`。输入文件实际 SHA 与预期 `a1b9970568b885fb48b4a9c6797ee455635f47f5471b57cff7de83adb8e0e651` 相同。

## 本地真实 sim

1. 默认 verify（不 record），目标 `/tmp/coop-replay-50003-verify-20261018-a`：**12 秒 wall watchdog 终止，未通过完整终局核对**。未生成输出目录/录像；当时尚未加入失败进度 IPC，不能断言终止发生在哪一步。没有提高本地预算重试完整局。
2. `--prefix 12`，目标 `/tmp/coop-replay-50003-prefix-20261018-a`：**12 次完整候选+round+真实 accepted learner 方法调用匹配，推进到 R4**。模拟 1527.448234ms；进程内总耗时 2136.960546ms；峰值 RSS 323645440 bytes（308.65 MiB）；全部输出 18902 bytes。engineErrors/actionErrors/controllerErrors 均 0；battle/frame/event 录制数均 0。`completeTerminal/completeSummary/completePartnerAudit=false`、`victory=null`，没有胜局通过声明。

prefix 的 verification.json 留在上述临时路径，包含运行当时的源文件 SHA；此后只修改了录制模式的 preview 事件排除条件并加入合同测试与文档，未再次运行真实 sim。它是有界诊断证据，不是当前提交的完整终局证据。

## 合同测试

命令：`node --test coop-replay/contracts.test.mjs`

结果：4 tests / 4 pass / 0 fail / 0 skipped。

- 配置还原与规则/manifest/seat/hash/错误拒绝；完整 candidate（含 bonds 顺序）与 round 绑定；整个 summary/terminal/partner SHA 的漂移拒绝。
- 四席 source 归属；wrapper 不重复计数；临时禁售与嵌套 engine 调用；constructor/start 事件、每 3 ticks 与最终帧；显式 preview 在构造后继续 step 也不捕获事件；预算失败锁存。
- 用 mock Battle 产生小型多场包，在 Node VM 执行**原游戏 agentReplay.js 函数**，仅 mock DOM/network/store：全部四席筛选、全部 6 个测试战场原生访问、frame/event、拖动及含已淘汰席位的团队胜利结算；下载 JSONL、index merge、碰撞/危险路径/缺战场拒绝。没有运行真实14轮录像。
- CLI 在模拟前拒绝已有目录、悬空 symlink、错误输入 SHA 和不合法 heap/RSS 预算；不覆盖、不生成错误包。

## 尚未验证

- 68 步真实动作的完整 terminal/summary/partnerActionSHA 核对；代码强制这些检查，但本地全局尝试超时。
- server 预算下的四席所有真实战场完整录制、导出大小/耗时/RSS，以及实际完整包的原 UI 函数重放。
- native browser、视觉/音效/美术加载、真实页面端到端播放。
- 主线程提供的训练精确重训证据、冻结模型失败和 crossplay128 失败没有在本次重复运行。

以上须由后续 server 执行与 review 确认，不能由合同测试或 prefix 通过替代。未安装、未 SSH、未使用子 agent/goal，未发布 live indexes。
