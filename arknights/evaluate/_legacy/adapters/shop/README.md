# 真实 solo 商店策略：BC → masked PPO

规则固定为 `6ab2468279e926d107788f63d7fe219b85f67b4a`。只导入外部游戏的 `Match`、`PlayerState`、`Battle` 和 `VirtualScheduler`；不复制战斗规则，不构造金币、棋子或商店，不修改游戏文件。启动校验游戏 HEAD 和 `server/shared/data` 已跟踪文件的干净状态。需要已有 Node 22+、Python 3、PyTorch（不需要 gymnasium、SB3、下载模型或安装依赖）。默认游戏路径按本文件位置解析到同级工作树外的 `../../Stronghold-Protocol`。

这是商店策略模型加明确的固定部署模块，不是全栈模型，也没有 ABYSS 通关证明。最终指标只能用引擎完整终局 `summary.victory`。`rounds`、层数、PPO loss 和权重变化都不是胜利。

## 本地运行

在 `experiment-rl` 工作树根目录：

```bash
# 固定线程；环境常驻，无网络/浏览器/服务端进程安装需求。
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
node --test rl/env.test.mjs
python3 rl/smoke.py

# 教师仍然是弱原 bot；保留失败局、难度、seed 和完整终局结果。
python3 rl/learner.py --seed 7 demo --episodes 1 --difficulty NORMAL --out rl/artifacts/demo.jsonl
python3 rl/learner.py --seed 101 bc --data rl/artifacts/demo.jsonl --epochs 2 --out rl/artifacts/bc.pt
python3 rl/learner.py --seed 1001 ppo --checkpoint rl/artifacts/bc.pt --out rl/artifacts/ppo.pt --steps-per-stage 1024 --curriculum NORMAL,HARD,ABYSS
python3 rl/learner.py --seed 60001 eval --checkpoint rl/artifacts/ppo.pt --episodes 10 --difficulty ABYSS --trace-dir rl/artifacts/eval
```

后两条是父线程扩展采样用命令，不属于本地 smoke 预算。配置在 `config.json`；`--config`、`--seed`、`--env-command` 放在子命令之前。BC 默认最多读取 1024 条正标签，可通过 `--max-samples` 改变；数据按整局划分，不能把同一局片段当独立验证集。已有旧版 11556 条轨迹没有自动转换：当前 `demo` 在真实方法调用前用同一候选编码采标签，仅方法成功且候选包含该动作时保留。其他动作、失败动作不当正标签。原 bot 的冻结/部署/消耗品行为可能导致 BC 与部署环境的分布差异。

持续子进程接口支持任何只在 stdout 输出 JSONL 的命令；stderr 透传诊断，参数使用 `shlex.split`，不经过 shell。如下只是配置示例，本分支未执行 SSH：

```bash
python3 rl/learner.py --env-command 'ssh server node /absolute/path/rl/env.mjs --game /absolute/path/Stronghold-Protocol' --seed 1001 ppo --checkpoint rl/artifacts/bc.pt --out rl/artifacts/ppo.pt --steps-per-stage 1024
```

Python/PyTorch 运行在启动训练 CLI 的机器；Node 引擎可独立在远端。单个环境常驻、同步采样，当前没有并行 worker。`STRONGHOLD_GAME` 或 Node `--game` 可覆盖游戏目录。模型、轨迹和动作重放文件放 `rl/artifacts/`（git 忽略）。建议逐条运行，避免多个 Torch 进程占用本地内存。

## JSONL 环境契约

每行请求对应一行响应，成功 `ok:true`，失败 `ok:false,error`。所有动作都作用于上一次响应所处的真实状态；禁止在两个 `step` 之间推进外部计时器。

```json
{"cmd":"reset","seed":7,"mode":"solo","difficulty":"NORMAL","budget":128}
{"cmd":"step","candidateIndex":2}
{"cmd":"close"}
```

`reset` 自动完成原游戏的分队/机变阶段，停在学习席位可以操作的 PREP。返回：

- `observation={own,public,history}`：自己的 `privateView`、原游戏 `publicView`、已观察的动作和盟约/效果历史。没有 RNG 状态、未来商店、其他玩家私有状态或隐藏内容计数器。seed、胜负只在请求/终局元数据中，不进入模型输入。
- `candidates`：变长合法动作。`buy` 绑定当前 `slot` 和 `id`；`sell` 绑定当前 `uid/id`，只允许棋子；另有 `refresh`、`levelUp`、`ready`。合法性逐条对应 `player/economy.js` 和 `player/prep.js`，执行仍调用原始方法。
- `features={global,candidates,mask}`：纯 `features.mjs` 编码。所有实际候选的 mask 为 true；Python 批次补齐处为 false，Categorical 对补齐动作概率严格为零。不是固定动作全集中的非法动作重试。
- `reward,done,victory,rounds,steps,engineErrors,actionErrors,budgetForcedReady` 及 `controllerErrors/controllerBlockedSales`。`done` 仅由 `onEnd` 完整终局触发；非终局 `rounds` 是当前轮，终局为 `summary.roundsPassed`。

`step` 成功执行后运行固定控制器，必要时通过 VirtualScheduler 推进到下一 PREP 或完整终局。索引非法或真实方法拒绝会报错并计 `actionErrors`，不会随机换动作或按空操作继续训练。错误局不进入 PPO；引擎错误会中止采样。`close` 释放 match 和 scheduler。

`{"cmd":"demo","seed":7,"difficulty":"NORMAL"}` 运行一个完整原 bot 教师局，返回 `samples`（同一特征函数、调用前标签）、`terminal`、`rejectedLabels`。这是独立示范采集，不是让原 bot 代理模型的训练回合。

## 学习与部署边界

`controller.mjs` 每个可控点固定调用原 bot `arrange`，但是其顺带卖棋路径被明确拒绝并计数。不会调用 `botPrep`，不会买卖、刷新、升本或冻结。原 bot 的分队和机变选择仍由引擎调度。装备只处理普通装备：选首个有装备位的已部署棋子；奖励只选首个合法免费候选；临时区未安置的物品会走真实 `destroy`。这些行为记录为 `fixed-controller`。

每个 PREP 默认最多 128 个自主动作。到预算时执行布局、个人选择和原引擎 `resolveTemp`（未处理的临时棋子/物品可能丢弃，记录具体 uid/id），再走真实 `setReady`。该限制是训练防循环措施，不是游戏规则；每次触发增加 `budgetForcedReady`。普通自主 ready 仅在原始 `canReady` 为 true 时出现。模型仍可在 ready 前自主出售临时棋子。尚未覆盖所有罕见的临时区阻塞组合；无候选会显式报错。

不支持 coop、自选编队、模型控制站位/装备/术式/消耗品/机变、冻结商店、跨机器恢复中途对局、原游戏页面的帧级战斗录像。评测 `--trace-dir` 只保存规则 SHA、控制器版本、seed、预算与逐动作 slot/uid 绑定，以便后续重新从 seed 运行并接入页面录像；它不是可恢复 clone，也不是直接可播放的浏览器录像。

## 特征和优化

全局向量保留每个盟约的 count/active/tier/layers/harmony/off/countsHand/禁用状态，完整策略 ID one-hot；每种棋子/物品的所有权位置、商店出现、买卖累计与本轮计数、按副本推导的本轮刷新暴露分布。候选向量含动作类型、成本、tier、身份 one-hot、盟约多热、持有位置与副本刷新暴露。可见效果、当前预告敌人、地图和 loadout 用额外固定 hash sketch；它可能碰撞，不是无损状态编码。

`history` 保留完整已见动作、位置变化、各轮循环计数、盟约/可见效果变化；不读取 `piece.meta` 或私有 `ps.counters`。刷新暴露是玩家可从自己的操作推导的历史，不冒称所有特质的实际触发/兑奖计数。未在原 view 暴露的里程碑可由历史研究改进，当前 MLP 只消费明确的统计摘要，尚未实现历史序列推理。

同一个 `encode()` 用于示范、训练和评测。MLP 对全局向量编码，再与每个候选共享打分；critic 只读同一可见状态。特征身份词表及规则 SHA 保存进 checkpoint，加载评测强制校验。原始 uid 只作动作绑定和历史索引，不作为数值输入。

BC 使用合法动作的交叉熵。PPO 真正执行环境采样，保存行为策略 old logprob/value，以 `done` 隔断 GAE，计算 clipped surrogate、value MSE、entropy，并做 Adam 更新和梯度裁剪。rollout 截断用当前 critic bootstrap，真实终局 bootstrap 为零。NORMAL→HARD→ABYSS 按环境步数切换课程，阶段预算用尽未结束的局显式报告 `curriculumTruncation`，不计为胜负。checkpoint 每次 PPO 更新落盘，包含模型、优化器状态、Torch RNG、配置与步数；CLI 的 `--checkpoint` 是权重热启动，不恢复未完的环境或优化器/RNG 连续状态。

奖励固定为非终局 0、完整胜利 +1、完整失败 -1，没有层数、循环或存活塑形。评测默认 greedy，固定 seed 和控制器可复现；统计仅使用真实终局。默认 gamma=0.99 按商店决策步折扣，长运营序列信用分配仍需实验。

## 验证

`env.test.mjs` 用真实 reset 独立验证初始每个候选，检查非法索引不改变游戏，运行多步直至真实终局，再从相同 seed 重放比对，并检查固定控制器没有代做商店决策。没有用展示快照 clone 或构造资金/商店。

`smoke.py` 实采一个原 bot 完整 NORMAL 局，取少量合法样本做 BC；再实采 PPO 步并更新 actor 和 critic；检查 GAE 终局/截断公式与 padding mask；保存并加载 checkpoint，以同一 seed 两次运行完整评测，逐动作 hash 和终局指标一致。实测结果见 `SMOKE.md`，原始 JSON 保存在忽略的 `rl/artifacts/smoke.json`。这些只证明闭环可执行，不证明性能提升或未见 seed 通关率。
