# 叠层教师宏动作

这是独立教师/控制模块，不是神经模型训练成果。规则基线为 Stronghold-Protocol
`6ab2468279e926d107788f63d7fe219b85f67b4a`。只执行真实 `ps.buy/sell/refresh`；不改游戏文件、
资金、层数、商店、计数器或 RNG。不读别人的状态、共享池存量、未来随机数或未展示商店。

## 接口

```js
import { runStacking, runStackingSteps } from './macros/stacking.mjs';

// m 是当前 Match，ps 是当前玩家。默认不启用任何宏。
const opts = {
  enabled: { refresh: true, acquire: true, lappland: true },
  reserveFunds: 6, maxActions: 24, maxPurchases: 4,
  maxNetCost: 8, maxCostPerLayer: 1, maxBuyPrice: 2,
  sellNew: true,
  coreIds: [], // 额外保护的棋子 ID（会归一为 base ID）
  coreUids: [],
};
const report = runStacking(m, ps, opts);
// 分步版本：每尝试一个动作 yield 一条结果，最终 return 完整 report。
// 二者选一个调用，不要重复花费预算。
// const report = yield* runStackingSteps(m, ps, opts);
```

`opts.enabled` 省略/false 为 off；true 为全开；对象中未列出的机制为 off。
`report` 包含 `before/after`（资金、免费刷新、回合历史、层数、场上 UID）、
`attempted/accepted`、`spent/credited/netCost`、`triggeredModules`、`actions`、
`modules.*.stopReason` 和总 `stopReason`。`spent` 读取真实当回合支出增量；
`credited` 包含售回及真实动作副作用收入；`netCost=before.funds-after.funds`。
`observedLayerDelta` 是调用期间真实层数变化，`projectedLayerGain` 是所支持特质的**条件收益估计**，
要求当前发动机和盟约保持到整备结束；它不是已经到账的层数，也不包括所有其他特质收益。
模块不自行结算 `onPrepEnd`。

三种独立机制，执行顺序为拉普 → 刷新补足 → 获得干员：

| 开关 | 条件与行为 | 停止/保护 |
|---|---|---|
| `refresh` | 在场阿罗玛/安洁莉娜对至少一个已激活目标盟约有边际收益，补到本回合 3 次主动刷新 | 读取真实 `ps.round`（即引擎 `ctx.roundStats()` 的来源）；已满 3 次、未知历史、层数封顶、预算不足则停。接受适配器 `ps.roundStats` 对象；计数缺失时只能用调用方实际累计的 `observedRefreshes`，绝不默认 0 |
| `acquire` | 在场夕/小满与对应激活盟约产生收益，买当前可见便宜普通牌 | 保留已有所有牌；新购牌只有身份唯一可确认、无装备、在手牌、非核心且购买前没有同名牌时才可售回。已有配对保留；合成/身份变化立即保留并停止 |
| `lappland` | 叙拉古激活且当前商店有廉价拉普：买 → 刷新 → 可选卖新副本 | 只刷新本宏刚买到的副本，不猜测老副本私有计数。已有普通配对会合成则不买。若这是唯一拉普则保留；已有原核心时可售回新普通副本。发生合成、赠送、转换等身份不确定性不卖；分步期间刷新历史变化则不重复刷新 |

普通/精锐收益从当前玩家 `gd` 的公开 garrison 数据读取；DIY 无对应特质则不产生收益估计。
不主动搜店追拉普、不移动阵容、不追阵容发动机、不拆防线。每次循环都受动作预算限制，
实际动作拒绝或抛错立即终止整个调用。每轮最多购入 4 张（两种购买机制共享），
硬上限 256 动作/32 次购入；调用方还应保留适合当前波次的战力预算。

成本筛选保守：购买/刷新全价必须可支付且保留 `reserveFunds`，按全价检查每层成本和净成本剩余额度，
**不预支售回收入、不假设正收益循环**。真实售回后才释放净成本预算。
免费刷新不扣资金，仍受收益上限和动作预算约束。层数总上限按固定规则 999 检查。
若分步外部还执行动作，报告资金前后会包含那些变化；建议完整 `yield*`，不要交错执行其他宏。

## 可选 loader：默认 off、只在内存修改 bot.js

`stacking-loader.mjs` 验证输入 `server/match/bot.js` 原始 SHA256：

```text
bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b
```

只替换两个唯一 anchor：

1. `botPrepBeginSteps` 的 `sellJunk(m, ps); yield;` 之后、`fillOnly` 之前。
2. `botPrepEndSteps` 存活/ready 检查之后、应用最终布阵及 Ready 之前。

第二处仅在 `ARK_STACKING_TAIL=1` 时执行；与 begin 共享本轮剩余动作数、购买数和净成本预算，
沿用资金保留值。begin 动作失败则本轮 tail 不再执行。运行报告用模块自有 WeakMap 保存，
不向 Match/PlayerState 附加属性；`stackingReports(ps)` 返回当前回合最多两份报告。

环境变量：

```bash
export ARK_STACKING_GAME_ROOT=/home/q2635/research/stronghold-agent-2026-10-17/Stronghold-Protocol
ARK_STACKING=refresh,acquire,lappland ARK_STACKING_TAIL=1 \
  node --max-old-space-size=384 --loader ./macros/stacking-loader.mjs macros/smoke.mjs
```

`ARK_STACKING=all` / `1` 为全开；`refresh`、`acquire`、`lappland` 可用逗号组合。
省略变量或 `off/0/false` 时 loader 原样返回源文件，不做变换。选项通过
`ARK_STACKING_OPTIONS='{"reserveFunds":6,"maxNetCost":8,"maxActions":24}'` 传入。
未知模块名会报错；原始源文件 hash/anchor 不匹配会报错，不静默错接。
默认游戏路径为本 worktree 的兄弟目录 `Stronghold-Protocol`。

主线程自己的参数 loader 若也重写 bot.js，不能直接假定两个 loader 可链用：此 loader 会拒绝
被前一个 loader 改过的内容。应由主线程在统一、审核过的变换中接入 `stackingPrepSteps`，
保留原始文件校验及上述插入顺序；本提交不改动其他 worker 的 loader。

## 验证命令与边界

无 npm 安装、无游戏编译；fixture 直接 import 游戏原始 `test/match/harness.js`，核对固定 HEAD
及 `server/shared/data/test/match` 相对 HEAD 无改动。fixture 的棋盘、资金、商店由测试构造，
断言真实动作的经济、层数结算和游戏 invariants，**不能据此声称自然通关**。

```bash
timeout 20s node --max-old-space-size=384 --test macros/fixture.test.mjs
ARK_STACKING=all ARK_STACKING_TAIL=1 timeout 35s \
  node --max-old-space-size=384 --loader ./macros/stacking-loader.mjs macros/smoke.mjs
git diff --check
```

`smoke.mjs` 是自包含自然调用示例：固定种子 1、2，solo ABYSS，真实战斗，rehearsal=0，
不构造局面、不修改规则、不重采样，打印调用次数、真实动作数、资金与结局。
它只检查接入，不替代主线程大样本自然评估。已测结果见 [VALIDATION.md](./VALIDATION.md)。

证据输入：`knowledge/distilled/pilot-strategies.json`、`mechanism-checks.json`、
`preview-tools/verify-stacking.mjs`、`knowledge/corpus/REPORT.md`、`corpus/distilled/conflicts.json`；
最终规则以固定源码 `server/sim/content/garrisons/meta.js`、`server/match/player/economy.js`、
`acquire.js`、`round.js` 和 `effectsMeta.js` 为准。
