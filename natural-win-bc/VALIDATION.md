# Synthetic validation only

Base: `d8805de9ccc1d4d861fe4a60f67197442078e6d9`.

Command: `python3 -B natural-win-bc/check.py`.

Final run: **7 tests passed**, supervisor wall time **2.574s**, aggregate child peak RSS
**710.33 MiB**, peak temporary output **510005 bytes**. An earlier passing run took 2.530s;
the final run followed stricter teacher error admission and per-group draw accounting changes.
Both runs together were below 20s. Temporary artifacts were removed by the supervisor.

The tests exercised synthetic 2048-row manifests with complete/failed/not-run rows, same-seed
normal/mixed positives, a dead-primary team carry, full terminal/row checks, bad hashes,
truncated gzip/trailing bytes/multiple JSON/duplicate keys/nonfinite values, rejected and
illegal labels, source guards, output refusal/caps, the original 200-update BC path and
checkpoint roundtrip, exact draw order and unchanged critic head, a mocked 64-game scheduler,
and the unchanged game_job retaining a synthetic terminal when its EOF check fails.

The syntax check used Python 3.10 grammar; execution used the installed Python 3.12 interpreter
and existing torch. Python 3.10 runtime compatibility was not separately executed.
The test harness substitutes synthetic protocol objects, never starts the real game loader,
and never constructs Environment, Match or Battle. Actual protocol/engine behavior remains
the responsibility of the pinned, previously reviewed game_job and main's later server run.

No SSH, installation, subagent, goal, production download, production training or real game
was performed. No results for the currently collecting teacher batch are claimed here.
Collector source trust remains pending main's independent acceptance. Eval supports only
original/mixed, 64 scheduled dev games; no heldout families and no sealed seeds.


Main 接手后针对采集器独立审计 P2 增加整体 failed 拒绝及 raw 文件集合对账，防止执行未知的 not-run 被误当冻结来源。新增整批失败/孤立产物拒绝回归，合成契约重跑通过；未修改采集器、游戏或现有 learner，未运行生产训练。

复审7531ad6的3P2/1P3已针对固定规则修复：纯validation原生无ok拒绝格式与诊断计数保留；15轮数据审计但14轮selector排除；死亡轮次/通过轮次/团队与个人结果、primary observation alive/LP一致性；末尾raw集合再核对。共享终局合同因此修改，并更新依赖pin。9项合成契约与9项旧survivor契约通过；另静态读取2真实positive+3真实HAND_FULL负例，前者均selected、后者均不selected，全部raw逐字节序列化内容未改。未重新运行游戏/训练；批次已自然截止1486complete/8timeoutfailed/554notrun，不是2048有效局。

生产首次导入 v1 在第261个审计记录 normal-30131 遇到 Controller rejection accounting 而停止，未创建模型：teacher 的 primary pickReward 有纯validation拒绝，但原 recorder 在 teacher 模式不计 controllerErrors。新增显式 teacher=True 仅用于 demo 终局校验，模型 eval 默认False保留原严格计数；所有拒绝仍逐条检查和保留。9项合成回归通过；正对全部1486complete记录做只读并行检查，不在首错处停止或拟合模型。实际服务器原监控器还有临时目录删除竞态，原失败保留，外部 tools/validate-natural-server.py 使用 os.walk 监控同9项测试通过3.21s/292MiB；不在运行中改源文件。
