# 本地合成验证

Base: `25c6af63cbb819bb4a4ed9682bed2824f27a4ec5`。仅新增 `prep-timing/`。
执行命令：`python3 -B prep-timing/check.py`，退出码 0，15 项测试全部通过。

```json
{
  "ok": true,
  "seconds": 0.9045065050013363,
  "peakAggregateRSSBytes": 110399488,
  "peakArtifactBytes": 976118,
  "exitCode": 0,
  "error": null,
  "limits": {"seconds": 20, "rssBytes": 1073741824, "artifactBytes": 5000000}
}
```

只运行一次合成 suite；测试子进程树共用上述预算，临时目录已清理。RSS/产物按 20ms 采样，
不是逐分配跟踪。未执行真实游戏、Match/Battle、checkpoint forward、训练、SSH 或安装。
Python 3.10 只做 AST 语法兼容检查，不宣称 Python 3.10 runtime 验收。

验证内容：

- 真实 Node loader 分别以 off/deferred 加载实际 rl/coop 类，使用假 player/bot/scheduler。
  从 inherited `view()` 核对实际 controller 字符串；off source 对象/bytes 恒等，
  exact URL、SHA 漂移和非法 timing 拒绝。源码仅三个唯一 anchor 变换，forced 分支不变。
- 普通 advance/buy 延后 arrange；auto-pick 新产生的当前 temp 触发完整 v1 fallback；
  空 temp 跳过 equipment，temp item 保留 v1 处理；ready 前 control；control 后 canReady=false
  不调用 setReady、不计成功 policy step；候选仍原生；forced 和 capacity/preflight 路径保留。
- 全 128 fakeProtocol 调度、8 格固定 16 分母、原终局契约、team victory 与 dead learner
  区分；deferred 报旧 v1 拒绝，非法 ready 为 failed；EOF、超时、输出限额、非有限 JSON、
  stderr、close ack 失败等小管道实测；全程没有 Match/Battle。
- raw/actions/terminal/result/manifest 同大小篡改、缺失、新增、子目录、symlink 均撤销信用；
  source/model/rules guard 异常及审计 deadline 同样撤销；固定模型 SHA、准入失败保留分母，
  原 helper 的 VERSIONS/HERE/BASE 未修改。

尚未执行：server 的冻结双模型 dev128、6 个已见自然教师 seed 诊断。main 负责真实验证；
本结果仅证明合成边界，不证明游戏胜率或与原 teacher 完全等价。
