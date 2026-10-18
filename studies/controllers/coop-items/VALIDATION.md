# 本地合成验证

在基线 `fbd7c2bd0232949cb1fa8f94c02d44c4790b1233` 上，仅新增本目录。

执行 `python3 -B coop-items/check.py`：12 项测试全部通过。监控报告：

```json
{"ok":true,"seconds":0.7249434170007589,"peakAggregateRSSBytes":59662336,"peakArtifactBytes":968939,"exitCode":0,"error":null,"limits":{"seconds":20,"rssBytes":1073741824,"artifactBytes":5242880}}
```

包括 128 个 fakeProtocol 游戏、固定分母/个人团队分离、错误控制器/规则/schema/步数/诊断、stopped/runtime/unknown/nonfinite/EOF/timeout/close 失败、checkpoint 缺失/hash/hidden128/schema 准入、metadata 版本独立、CLI no-overwrite、raw/action/terminal/result/manifest 的增删改与文件集合篡改、最终 guard 期间篡改、全局撤销成功信用。小 Python pipe 测试复用真实 Protocol；静态 Node 测试只加载 loader，不加载游戏。

首次测试监控器误将 1 GiB RSS 上限实现为虚拟地址空间上限，V8 的 CodeRange 保留地址失败；已改为进程树总 RSS 监控并关闭 core dump。修正后的完整测试结果如上。两轮合成测试合计不足 3 秒。

另用 `ast.parse(..., feature_version=(3, 10))` 检查本目录全部 Python 文件的 Python 3.10 语法。没有安装依赖、下载数据、SSH、子 agent、训练、真实 checkpoint forward、Match/Battle 或真实 128 局评测；测试不是游戏效果或合作场景胜率证据。server 命令及解释边界见 README。
