# 自走棋训练项目对《卫戍协议》开源复刻版的参考

核查日期：2026-10-17。范围：TFT／自走棋／金铲铲的训练实现与一手证据；未调查 sganggs/Stronghold-Protocol 或 Bilibili 实战。已浏览项目、读取关键源码和 Riot 原始幻灯片；**本次没有训练模型或独立复现任何强度指标**。目标按最新说明理解为：先训练开源复刻版，最终兼顾单人及合作；目前没有官方客户端。

## 可直接用于决策的结论

- **TFTMuZeroAgent 最值得借鉴的是环境拆分和结构化接口，不能当成已验证的强 MuZero 玩家。** 当前主分支是 Set 4 模拟器；训练代码要看历史提交。作者公开承认规划方法尚未得到有意义的结果。[当前仓库][T0]、[历史版本][T1]、[作者两周年报告][T2]
- **utilForever/AlphaTFT 不适合选作基座。** 名称与简介提及 RL，但实际业务代码只有整数加法函数及测试，没有可核实的 TFT 环境或训练器。[源码][A1]、[测试][A2]
- **有真实工业技术依据的是 Riot 的分阶段 agent＋学习式战斗结算器。** 常见转述误写为 GDC 2024；此场演讲实际是 **GDC 2023**。公开材料可支持方法参考，不能支持拿到开放权重、复现段位或照搬训练预算。[会议页][R0]、[原始幻灯片][R1]
- **短期路线建议：规则／搜索基线 → 动作掩码 PPO → 必要时分层与合作 MAPPO。** 先测复刻版采样吞吐和规则覆盖，再决定是否需要 MuZero 或战斗代理模型；以下建议是工程判断，不是已证明适用于卫戍协议的实验结论。

## 项目证据对照

| 项目 | 实际交付物与算法 | 强度证据及限制 | 迁移价值 |
|---|---|---|---|
| [TFTMuZeroAgent][T0]，当前 `116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4` | Set 4、PettingZoo 多人＋Gymnasium 单人／站位／装备子环境；当前要求自带模型 | 当前有规则 `Default_Agent` 和采集器，不能把它们算作训练策略；没有本次独立评测 | 高：环境合同、合法动作、任务拆分；权重不可迁移 |
| [TFTMuZeroAgent 历史版][T1]，`061d7f7525c3efe1a544f7f347386ab5aeb5bc8f` | PyTorch、Ray、C++/Cython MCTS；Sampled/Gumbel MuZero，另有 PPO／表示学习／模仿路径 | 作者 2024 年报告：简化站位上 value-based 模型有进展；planning 无有意义结果，未突破人类表现 | 中：研究实现参考；集成和调试成本高 |
| [AlphaTFT][A0]，`0c39b277e67978114e3377defcf362793afa58f0` | C++/CMake 工程脚手架；`Add(a,b)` | 无训练器、权重或游戏结果；最后推送 2020-04-06 | 低：排除 |
| [TFT_GOAT][G0]，`d5358e9402569f745bea81b61c5aed0500c57d66` | 自称 Set 17；PettingZoo、自定义 PPO、自对弈；规则／神经／逐 tick 三种战斗后端 | 作者报告胜随机、对脚本仍弱（top-4 约 40%）；不是已验证高水平 TFT agent | 中高：现代 PPO 管线、可替换结算接口；必须检查抽象损失 |
| [super-ml-pets][S0]＋[sapai-gym][S2] | Super Auto Pets；MaskablePPO、模拟器训练，部署另接视觉操作 | 有公开模型 release；2022 年 v0.0.6 自报 47.4% win rate，不能自动解释成十胜通关率，未复现 | 高：比空壳更完整的“环境→训练→权重→推理”参考；旧规则需固定 |

表中数字均为项目作者报告，不是本次测得。TFT_GOAT 的战斗分类准确率也不能换算成整局 agent 胜率。[项目状态][G0]、[SAP release][S1]

## TFTMuZeroAgent：该学什么，不能误读什么

**版本。** 2026-08-29 的提交明确将仓库转为 simulator-only；本次固定当前与 2024 历史快照，避免用旧 README 描述今天的主分支。仍然是 **TFT Set 4**，不是当前 TFT，更不等于金铲铲规则集。[转换提交][T3]、[当前 README][T0]

**观测与动作。** 环境直接提供棋盘、备战席、商店、装备、羁绊及经济标量，而不是从像素训练。当前 `ObservationToken` 源码含单位 ID、装备 ID、羁绊、属性、星级／天选等信息，分自己信息与公开信息。全局动作默认 `Discrete(55*38)=2090`，另有 1296 动作压缩编码和七维 `MultiDiscrete`；后者采用首个非零字段解码，**不能误认为同时完成七种动作，也不是自动学出的层级策略**。[观测实现][T4]、[动作说明][T5]

**任务拆分。** 站位子环境只调整己方位置并结算一场；装备子环境用分配前后战斗结果之差作奖励；单人环境保留买卖、刷新、升级等决策，对阵生成的敌人。这种拆分适合先研究短期站位／装备，再研究长期构筑。但**多个子环境存在，不代表完整分层 agent 已被训练或验证**；单人默认观测还不包含敌方 tensor，照搬会限制针对敌人的决策。[站位][T6]、[装备][T7]、[单人][T8]

**训练和算力。** 历史源码确有 MuZero 初始／递归推理、policy/value/reward 损失和优化更新，不只是鼠标脚本；也包含模仿规则 bot 的模式，不能统称“纯自我发现”。配置默认 batch 1024、unroll 5、每步 50 次 MCTS、采样 30 个候选、CUDA 和 Ray；**这些是默认设置，不是成功训练预算或最低硬件要求**。[历史网络][T9]、[训练器][T10]、[配置][T11]。作者两周年报告只展示无装备、1 费单位随机站位等受限任务上的进展，并提到旧 GPU 与管线调试困难；不应宣传完整构筑能力或超人类表现。[作者报告][T2]

## 替代实现的关键取舍

**TFT_GOAT 可参考接口，不能直接借其表征解决叠层。** 其 actor 把商店／棋盘／备战席嵌入作均值池化，单位附加特征主要是星级和装备数量，输出 43 个离散动作；源码中的 `EQUIP` 会合成前两个组件并交给最高费用可装备单位。这意味着某些装备与目标选择由规则固定，策略没有自由选择任意装备构筑；池化也会丢掉位置／槽位对应关系。对卫戍的装备触发、叠层来源、持续时间和站位不能直接复用。[网络][G1]、[动作实现][G2]。其路线图部分描述与当前实现有差异，采用源码作为判断依据。

**Super Auto Pets 是更小但完整的参考。** 购买、售卖、刷新、排列、升级及长期属性成长，与构筑／叠层问题相关；有 MaskablePPO 训练入口和公开权重。`sapai` 本身是环境，`super-ml-pets` 才把训练和部署串起来；可借鉴合法动作掩码、对手池和模型导出。原始 Gym README 给出的观测是类别 one-hot＋归一化数值，基础动作 63 个（不含冻结／重排）；部署所用 fork 的 v0.1.1 wheel 则为 213 动作，须锁定具体依赖，不能只读旧 README。[发布依赖][S3]。公开权重来自旧版本，不能把发布页的胜率扩展到当前规则或卫戍。[训练／部署][S0]、[权重及作者指标][S1]、[环境][S2]

**金铲铲检索结果应严格排除“看见深度学习就算策略学习”。** 例如 `jusway/CV_JinChanChan` 的实际功能是按用户配置阵容自动拿牌，预训练视觉模型服务于识别；不是已证明学会长期构筑的 RL agent。本次未找到可同时核实公开规则模拟器、策略训练、权重和评测的金铲铲完整方案；这是检索边界，不是证明不存在。[项目原文][J0]。腾讯专利 CN117883788B 提及自走棋／金铲铲适用性，但专利方案不能替代专门实测或开放实现。[专利原文][J1]

## 工业与学术证据

**Riot，GDC 2023。** Ran Cao 的原始幻灯片使用 Set 1 介绍方法：战斗代理预测 **damage distribution**，比较 CNN／Transformer／组合表示；不是仅给棋盘打一个强度分。训练将商店 agent 的整局目标与部署 agent 的本轮战斗目标拆开，并逐步增加局长、部署、站位、8 人对局和装备复杂度。幻灯片讨论长局带来的反馈延迟、信用分配及策略滞后。会议摘要称训练出较高水平 agent，但本次来源没有足够依据给出天梯段位、硬件规模或可复现超人类结论。[会议页][R0]、[幻灯片 pp.20–23、31、34–38][R1]

本次核实到的论文不支持“已有开放、强度可复现的完整 TFT 自对弈 agent”这一判断；工业报告、开源实现和同行评审结论需分开看。

**Ludus，AAAI/EAAI 2022，DOI 10.1609/aaai.v36i11.21550。** 真实同行评审论文，研究自创 auto battler 的自动平衡：自动对战采样＋全局／遗传搜索，优化卡牌参数和 metagame 指标。**不是 TFT RL 玩家论文**，也不证明端到端构筑智能。它对枚举构筑、评估协同／克制及先建立搜索基线很有价值。[论文正式页面][L0]、[全文][L1]

## 对本项目的建议：先单人可检验，再扩展合作

以下为基于上述资料的设计推断；没有阅读复刻版源码，也不预设其规则已完整实现。

1. **表征先保真。** 使用结构化单位／装备／技能实体；显式保留叠层数量、来源、剩余时间、上限、触发次数、继承／清零条件及当前局内阶段。经济／构筑层与战斗层分别观察与行动。若每次决策的状态已充分，无需先上 RNN；只有部分可观测或状态遗漏无法消除时才引入记忆。不要只给总面板战力或装备数量。[可参考的结构化环境][T4]；字段选择是本项目建议。
2. **先规则与已知模拟器搜索，再动作掩码 PPO。** 规则、随机合法动作、固定构筑和短视战斗搜索提供可解释底线。能够精确克隆状态时，先尝试受预算限制的 beam／随机射击／MCTS 搜索；随机规则要多 seed 比较。PPO 实现较直接，适合离散／条件动作和批量采样，但样本消耗可能很高。构筑类型→目标→装备／位置可做带条件掩码的自回归动作，避免对所有组合平铺；是否拆两个训练 agent，应通过消融验证。[已有环境拆分][T6]、[PPO 项目][G0]、[SAP 训练闭环][S0]
3. **DQN／MuZero 的位置。** 简化到固定小动作表的站位／装备任务，可比较带 replay 的 DQN 类方法以复用昂贵样本；动态组合动作与合作场景更难处理。MuZero 的学习模型可能减少深层真实模拟调用，但每步增加树搜索／网络评估，并引入模型偏差、replay 和目标构造的调试成本。已有准确复刻版时，不能仅因“四张 GPU”就跳过直接规划和 PPO 基线。[TFT 作者的受限成功与失败经验][T2]、[历史实现][T9]
4. **合作先定义控制权。** 若一个程序拥有双方全部状态和行动权，可先用集中式队伍策略作为上界／基线；若希望每个玩家独立观测和行动，再比较参数共享 IPPO 与 **MAPPO：训练时集中 critic、执行时各自 actor**。合作目标、资源转移和 teammate identity 必须进接口；8 人 FFA 自对弈不是合作训练的替代品。MAPPO 有真实合作基准论文与官方实现，但没有在这里证明适合卫戍。[MAPPO 官方实现及论文入口][M0]

## 结合 smYuHangLab3 的算力判断

机器信息来自父任务实测转述：128 logical CPU、约 503 GiB RAM、4×RTX A6000 48 GB，GPU 占用变化，Docker 可用；**本侧车没有连接服务器或跑 benchmark**。

父任务最新转述：远端 4 worker 的平均完整 episode 耗时，未 rehearsal 时单人 2.24 s／合作 5.75 s；3 候选 rehearsal 时分别 5.49 s／19.84 s。32 局 bot 全败、0 errors。**这些是父任务实测，不是本侧车复现；零错误只支持本批运行稳定，不支持 bot 强度或规则保真。** 当前可确认候选试演已有明显采样成本；仍需 steps/s、CPU/GPU 利用率等指标判断实际瓶颈，不能据此推算 PPO 收敛时间。先建立可战胜 bot／固定场景的基线和评测，再考虑增加候选数或上复杂 MuZero。

| 方案 | CPU 模拟采样与 GPU 学习的关系 | 当前选择依据 |
|---|---|---|
| 规则／搜索 | 主要吃模拟器吞吐；搜索分支会增加每次决策的结算次数 | 建立收益／模拟次数曲线，不能仅报搜索深度 |
| Masked PPO／IPPO／MAPPO | CPU rollout → 批量推理 → GPU learner；小策略网络可能根本喂不满一张卡 | 先单 learner 和多进程采样，再按实测扩容 |
| DQN 类 | replay 能重复使用已有样本，但行为覆盖和目标偏差仍需检查 | 先限定短程、小动作任务，比较相同真实模拟预算 |
| MuZero | 可在 latent 中搜索，但有大量额外推理与模型训练，非免费加速 | 只有 PPO／直接搜索的瓶颈和收益已明确时再投入 |
| 学习式战斗代理 | 将昂贵战斗转为批量神经预测；需先生成有代表性的真模拟标签 | 若战斗结算确为瓶颈，再做；叠层极值／稀有组合需单独测试 |

**不要把 128 logical CPU 当作 128 倍采样，或把 4 张 A6000 当作 4 倍训练。** 父任务 benchmark 应给出 env steps/s、combat/s、完整局长、reset 成本、进程扩展曲线、序列化／IPC、推理批次等待、learner 更新耗时与 GPU 利用率。瓶颈若在模拟器，可优先减少日志／渲染、进程隔离绕开 GIL、限制 BLAS 线程过量并发、复用静态规则数据；这些是待验证的优化方向，不是本次已测瓶颈。也不建议一开始做四卡 DDP：剩余 GPU 可先用于独立 seed／超参数／评测，按可用资源安排。

战斗代理尤其要输出与下游有关的伤害／存活／终局状态，而非仅胜负；若局内叠层影响后续回合，只预测本轮胜负会破坏长期状态转移。必须用真实复刻引擎作保留评测，检查策略是否在利用代理误差。[Riot 的结算分布思路][R1]；长期状态要求是针对本项目的推断。

**验收口径：** 固定复刻版本、规则覆盖、seed 和敌人／队友池，报告多 seed 的完整跑局成功率、终局分数／资源／存活、相同真实模拟预算下的基线差异；单列叠层和稀有联动测试。当前只能说方法可借鉴，不能承诺训练时长、最终强度或向官方客户端的迁移效果。

[T0]: https://github.com/silverlight6/TFTMuZeroAgent/blob/116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4/readme.md
[T1]: https://github.com/silverlight6/TFTMuZeroAgent/tree/061d7f7525c3efe1a544f7f347386ab5aeb5bc8f
[T2]: https://www.reddit.com/r/TeamfightTactics/comments/1gymgr5/ai_learns_to_play_tft_2_years_later/
[T3]: https://github.com/silverlight6/TFTMuZeroAgent/commit/cfcc5db9c1a5b750138a9be681e498007e73a4bc
[T4]: https://github.com/silverlight6/TFTMuZeroAgent/blob/116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4/Simulator/encoding/token/observation.py
[T5]: https://github.com/silverlight6/TFTMuZeroAgent/blob/116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4/markdown/full_game.md
[T6]: https://github.com/silverlight6/TFTMuZeroAgent/blob/116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4/markdown/position.md
[T7]: https://github.com/silverlight6/TFTMuZeroAgent/blob/116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4/markdown/item.md
[T8]: https://github.com/silverlight6/TFTMuZeroAgent/blob/116aeedc2e6aa043ba6ecb1c84c9a5cdb94779e4/markdown/single_player.md
[T9]: https://github.com/silverlight6/TFTMuZeroAgent/blob/061d7f7525c3efe1a544f7f347386ab5aeb5bc8f/Models/MuZero_torch_agent.py
[T10]: https://github.com/silverlight6/TFTMuZeroAgent/blob/061d7f7525c3efe1a544f7f347386ab5aeb5bc8f/Models/MuZero_torch_trainer.py
[T11]: https://github.com/silverlight6/TFTMuZeroAgent/blob/061d7f7525c3efe1a544f7f347386ab5aeb5bc8f/config.py
[A0]: https://github.com/utilForever/AlphaTFT/tree/0c39b277e67978114e3377defcf362793afa58f0
[A1]: https://github.com/utilForever/AlphaTFT/blob/0c39b277e67978114e3377defcf362793afa58f0/Sources/AlphaTFT/Test.cpp
[A2]: https://github.com/utilForever/AlphaTFT/blob/0c39b277e67978114e3377defcf362793afa58f0/Tests/UnitTests/SimpleTest.cpp
[G0]: https://github.com/MielPopsssssss/TFT_GOAT/blob/d5358e9402569f745bea81b61c5aed0500c57d66/README.md
[G1]: https://github.com/MielPopsssssss/TFT_GOAT/blob/d5358e9402569f745bea81b61c5aed0500c57d66/src/tft_goat/agent/network.py
[G2]: https://github.com/MielPopsssssss/TFT_GOAT/blob/d5358e9402569f745bea81b61c5aed0500c57d66/src/tft_goat/env/actions.py
[S0]: https://github.com/andreped/super-ml-pets
[S1]: https://github.com/andreped/super-ml-pets/releases/tag/v0.0.6
[S2]: https://github.com/alexdriedger/sapai-gym
[J0]: https://github.com/jusway/CV_JinChanChan/tree/b53437a8080db64bd9f15084e03b151fb3754535
[J1]: https://patents.google.com/patent/CN117883788B/zh
[R0]: https://www.gdcvault.com/play/1028851/Machine-Learning-Simulating-Teamfight
[R1]: https://media.gdcvault.com/gdc2023/Slides/Simulating++Teamfight+Tactics_Cao_Ran.pdf
[L0]: https://ojs.aaai.org/index.php/AAAI/article/view/21550
[L1]: https://ojs.aaai.org/index.php/AAAI/article/download/21550/21299
[M0]: https://github.com/marlbenchmark/on-policy

[S3]: https://github.com/andreped/sapai-gym/releases/tag/v0.1.1
