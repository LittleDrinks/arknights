# 固定物品控制器 v2：独立消融

只新增 `controller-v2/`。接口为 `control(env, { forced = false })`；不改 `rl/`、游戏、训练器、schema、规则或 checkpoint。规则仍是 `6ab2468279e926d107788f63d7fe219b85f67b4a`。本次仅做实现与有限验证，提交后等待独立 review。

启用同一个 loader，仅切换环境变量：

```bash
# 默认关闭，包括未设置、off 以及任何非 v2 值；完全返回 nextLoad 的原结果。
ARK_ITEM_CONTROLLER=off node --loader ./controller-v2/loader.mjs rl/env.mjs
ARK_ITEM_CONTROLLER=v2 node --loader ./controller-v2/loader.mjs rl/env.mjs

# 同一现有权重、同一 seed，各一局 CPU；内置进程树资源监控，无训练。
python3 -B controller-v2/evaluate.py

# 人工 fixture + 真实引擎接口验证，不是自然胜率。
node --test controller-v2/control.test.mjs
```

原评测器也可直接使用，不必修改它：

```bash
ARK_ITEM_CONTROLLER=v2 python3 -B rl/learner.py \
  --env-command 'node --loader ./controller-v2/loader.mjs rl/env.mjs' \
  --seed 60001 eval --checkpoint ../experiment-rl/rl/artifacts/bc.pt \
  --episodes 1 --difficulty NORMAL --trace-dir controller-v2/results/manual-v2
```

上述最后一条是复现用法，本次未额外执行。切为 `off` 即 v1。原 CLI 的 eval 会额外 reset 一次校验 schema；本目录的 paired runner 每个版本只 reset 一次并立即校验，然后走到终局，最多两局。

Loader 仅命中**本工作树**的 `rl/env.mjs` 精确 file URL，不劫持邻居 `experiment-rl/rl/env.mjs`。启用时校验 SHA256 `723e397fabeea4e473b65523aaadbadba7b08af7fea27ec4f29fa38c3cefc4b5`，检查两个 anchor 各出现一次，仅替换 control import 和返回的 controller 版本字符串。任何源码漂移均拒绝加载；不落盘改写，不叠加 teacher/stacking loader。默认 off 不做 SHA 检查、不导入 v2，不改变旧版行为。

| 类型 | v2 的选择与保护 |
| --- | --- |
| 普通装备 | 只支持固定规则中列出的 family；优先已部署、有空装备槽的棋子。按静态攻击、生命、防御、近战阻挡、治疗/输出角色、签名盟约和可见搭配打分。不覆盖旧装备。 |
| 资金类消耗品 | 原 `ps.equip` 处理即刻/下轮/每轮收益；任何已有棋子均须有空装备槽。 |
| 身份牌、通讯机、寻呼模块 | 依据自身可见盟约计数/激活状态选择；忽略已禁用盟约。获得类预留手牌空间，实际抽取完全交回引擎。 |
| 紧急调度券 | 只在当前可见商店有未售、可用的棋子时使用。没有 buy/refresh 调用，免费获取及商店变化全由原物品效果完成。 |
| 拟态物质 | 优先自身已有两张的普通棋子，随后按可见盟约/静态强度；不查池剩余。当前引擎 `builtinMeta.js` 的该效果支持超库存授予，原 bot 中“池空则没收益”的注释/判断已不匹配实现。 |
| 博士投影 | 普通版本和金色版本都只给能晋升、未持有投影且有空槽的普通棋子；原方法完成延迟或即时晋升。 |
| 人事部文档 | 仅当前可见部署上限小于物品目标上限时使用。 |
| 画卷 | 原 `ps.useArt(uid,row,col,'RIGHT')`；选择实际在场的强棋子，验证固定范围首格是本格，保留棋子/复制装备/潜在合并退装的手牌余量，遵守每轮 Art 上限。 |
| “神秘顾客” | 仅此已知 family 调用原 `ps.destroy`，使用已核实的 `onDestroy(reason='player')` 资金奖励；不激活加敌人的 bounty。 |
| 教鞭 | 明确保留，原因 `bounty-risk-retained`。 |
| 信标、突变细胞 | 本版不支持消耗/变换载体的策略取舍，明确保留，原因 `destructive-transform-retained`。 |
| 未知/未支持物品 | 明确保留，记录原因；不任意销毁，不记作成功。 |
| 奖励 | 手牌有空位时，仅选当前展示的未售、有限数值价格 ≤0、记录存在的合法 offer；排除未配置 DIY。沿用首张合法牌，不引入奖励优化策略。 |

所有实际游戏收益由 `ps.equip/useArt/pickReward/move/destroy` 或保留的原 draft/arrange 路径执行。控制器不赋值 funds/layers/board/shop，不调用 acquire/promote/grant、不选择购物经济动作。运行期间在方法边界封锁 buy/sell/refresh/levelUp，阻止原 arrange 在满手时卖棋子，最后恢复原方法描述符与 source。所有装备（消耗品也一样）要求目标空槽；不会依赖引擎默认的“第三件替换并销毁最旧装备”。

新增目标选择函数只接收 `privateView()`、自身 Art 成功次数以及静态 `gd.item/chess/equipPerChess/maxArtsPerRound`。未调用 `bot.itemTarget`：它的默认 context 经 bondPlan/bondPoolStats 读取 `m.pool.entries`，拟态分支另读 `pool.left`，这些不在当前 own/public observation 中。新增逻辑也不读随机数、未来商店/波次或他人私有状态。

**保留路径的边界**：用户指定的原 `bot.arrange(m, ps)` 自身仍调用 context，读取共享池与原 field model，并使用布局随机数；原 draft/personal-choice 自动化也保留。这不是一个“整个系统已证明只读 own/public”的改造。物品逻辑没有新增这些依赖，fixture 用抛错的 pool/wave/rng/lastResults getter 验证新增路径；原 arrange 在这个 fixture 中显式 stub，不能把该测试解释为原 arrange 的信息隔离证明。

预算固定、可审阅：每次 control 至多 24 次物品调用，每轮 32 次、每局 256 次；每轮至多 8 次奖励、128 次 arrange、512 次实际 move 调用。同 UID 每轮最多尝试一次，原方法拒绝的 UID 在本局不再重试。成功装备在以后轮次被引擎退回时可以再装备。无目标的物品可随自身可见状态变化重新评估，忽略日志按轮去重。审计保存在 `observation.history.itemController`：版本、预算、success/failure/ignored/stop、原因、UID、方法与参数，至多 2048 条，超出有 `droppedEvents`。`success` 仅指原方法返回 `ok`，不是对随机获得、隐含收益或通关的保证。沿用 recorder 的 `fixed-controller` source 保持 controllerErrors 统计正确，版本由外层 controller 和 itemController.version 标识。

普通调用保留未处理溢出，让 policy 继续操作。强制预算调用仍有 temp 时，记录 `overflow-retained` 并抛出 `CONTROLLER_V2_STOP`，阻止旧 env 的后续强制 Ready。**不调用 `resolveTemp()`**，它会绕过逐物品语义直接清掉未知物品/棋子。调用者应将此归类为 stopped，不应填充胜利或失败局。本目录 runner 正确区分完整终局与停止；原 CLI 会报错退出。需要完整终局不等于可以静默清空资源。

获得类不看隐藏池，因此通讯机/寻呼模块等可能被原方法接受但没有随机收益；目标评分也没有模拟技能、装备收益或完整盟约机制。手牌余量是保守条件，并非防止所有连锁溢出的证明。两次 arrange（进入时，以及本次物品/奖励改变状态后）可能影响布局与后续轨迹；这属于此固定控制器的消融范围。

验证与证据见 [VALIDATION.md](VALIDATION.md)。
