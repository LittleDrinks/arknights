# 合作泛化代码核验：五个固定版本

建议先借用 **FCP式多水平冻结检查点池 + MAPPO训练接口 + ZSC-Eval陌生伙伴评测协议**；再单独比较E3T式动作噪声和伙伴预测。以下均是源代码静态核验，未安装依赖、下载权重、启动训练或复现论文结果。仓库不是空壳，但“有代码”不等于“可直接接卫戍环境”。本地浅克隆在`literature/repos/`，机器可读版本在`repos/repo-manifest.json`。

| 官方项目 | 固定commit | 直接用途 | 核验结论 |
|---|---|---|---|
| [MAPPO/on-policy](https://github.com/marlbenchmark/on-policy) | `de66d7a4b23fac2513f56f96f73b3f5cb96695ac` | PPO、中心critic、动作mask、循环buffer | 可作算法参考；默认共享策略不是陌生伙伴泛化方案 |
| [MEP作者实现](https://github.com/ruizhaogit/maximum_entropy_population_based_training) | `518ea6a2f9ccc75c1c252bafaf946de62dc96e05` | 种群熵、冻结伙伴、优先采样 | 真实差量代码包，不能独立安装运行 |
| [ZSC-Eval](https://github.com/sjtu-marl/ZSC-Eval) | `f940869afc42b688332a385892d8dbb57a190f95` | FCP多水平池、行为伙伴选择、跨配合评测 | 最值得优先移植协议；内含FCP/E3T等重实现 |
| [COLE-Platform/COLE_training](https://github.com/liyang619/COLE-Platform/tree/33e89fab71fa1932fbee173c61d9d96eadbd9c2a) | `33e89fab71fa1932fbee173c61d9d96eadbd9c2a` | 历史策略图、Shapley伙伴选择、交叉评测 | 原作者训练分支，避免误看main网页平台 |
| [E3T作者实现](https://github.com/yanxue7/E3T-Overcooked) | `b2e35dc538b8fbbd51d769bc67c5a3d865236c45` | ego/random混合伙伴、历史动作预测器 | 两个核心模块确实存在；差量包与旧依赖需移植 |

COLE的附加基线分支也已固定：`Baseline_training` → `3e388e005a8ffe53fc41e902548990fa832cd8c2`，保存在同一克隆的`origin/Baseline_training`，没有另算第六库。项目网页的分支名大小写与live分支不同，实际使用以上大小写。

## 1. MAPPO：提供优化器与动作约束接口，不解决伙伴分布

论文为Yu等的 *The Surprising Effectiveness of PPO in Cooperative Multi-Agent Games*，NeurIPS 2022 Datasets and Benchmarks。仓库README明确为论文实现并链接[论文](https://arxiv.org/abs/2103.01955)。

- 训练入口：`onpolicy/scripts/train/train_mpe.py:64`；环境实例化后按`share_policy`选择shared/separated runner。环境实现和脚本并不包含卫戍。
- 核心更新：[r_mappo.py:91](https://github.com/marlbenchmark/on-policy/blob/de66d7a4b23fac2513f56f96f73b3f5cb96695ac/onpolicy/algorithms/r_mappo/r_mappo.py#L91)的`ppo_update`；`onpolicy/runner/shared/base_runner.py:73`选择中心价值输入；`onpolicy/utils/shared_buffer.py`保存rollout并生成训练batch。
- 合法动作：[rMAPPOPolicy.py:48](https://github.com/marlbenchmark/on-policy/blob/de66d7a4b23fac2513f56f96f73b3f5cb96695ac/onpolicy/algorithms/r_mappo/algorithm/rMAPPOPolicy.py#L48)传入`available_actions`，`onpolicy/algorithms/utils/act.py`送入动作分布；卫戍需自行建立动作编码、mask与变长目标选择。
- 评测：`onpolicy/runner/shared/mpe_runner.py:142`的eval仍是本环境政策回放，没有已验证的冻结陌生队友池划分。不能把默认self-play的回报当跨队友泛化。

许可为根目录MIT；README的旧安装示例使用Python 3.6.1/PyTorch 1.5.1，requirements包含Gym 0.17.2等大量旧依赖。可借鉴actor/critic接口、GAE/buffer与masked分布，不建议照搬整个旧环境。`--share_policy`和`--use_centralized_V`在config里用`store_false`，命令行出现该flag会关闭默认真值，消融脚本必须核对。

## 2. MEP：种群多样性和难配合伙伴采样

Rui Zhao等的 *Maximum Entropy Population-Based Training for Zero-Shot Human-AI Coordination*，AAAI 2023；[论文](https://arxiv.org/abs/2112.11701)与作者仓库相互对应。不要误用同一作者的`ruizhaogit/mep`：那个是ICML 2019的多目标机器人RL，已查其README并排除。

- 阶段一入口：[pbt_model_pool_entropy_parallel.py](https://github.com/ruizhaogit/maximum_entropy_population_based_training/blob/518ea6a2f9ccc75c1c252bafaf946de62dc96e05/human_aware_rl/pbt/pbt_model_pool_entropy_parallel.py)，由`ENTROPY_POOL`、`ENT_VERSION`控制人口熵项。`human_aware_rl/baselines_utils.py:315`将人口送入PPO，`baselines/baselines/ppo2/runner.py:80`起聚合人口动作概率，95–100行计算熵变化；这与只加单策略entropy bonus不同。
- 阶段二入口：[pbt_model_pool.py:418](https://github.com/ruizhaogit/maximum_entropy_population_based_training/blob/518ea6a2f9ccc75c1c252bafaf946de62dc96e05/human_aware_rl/pbt/pbt_model_pool.py#L418)从`LOAD_FOLDER_LST`加载伙伴，agent0新建，其余模型加载；505–550行依合作收益排名与`ALPHA`抽伙伴，再设置`gym_env.other_agent`，更新ego。
- 伙伴采样：`PRIORITIZED_SAMPLING=False/True`是现成开关；源码使用当前收益的倒数排名及幂权重，不能把它笼统宣传成测得的“学习进度优先”而不读具体实现。`ALPHA=0`使排名幂项均匀。
- 评测：同文件双位置收益统计支持发现配合缺口，但不是天然独立的held-out部署分布，仍需冻结独立测试池。

README明确要求合并到`HumanCompatibleAI/human_aware_rl`的`neurips2019`基座，本仓库仅11个源/说明文件，包含真实训练逻辑但不是独立可运行发行包。TensorFlow 1式`tf.Session/placeholder`、Sacred、Gym、Baselines/Overcooked接口是主要依赖。README声明MIT，但当前快照没有独立根LICENSE，依赖与差量文件的许可来源应分别保留。迁移可借用采样器与人口熵思想，不复用Overcooked权重或六动作分布。

## 3. ZSC-Eval：优先用于陌生队友与跨流派评测

Xihuai Wang等的 *ZSC-Eval: An Evaluation Toolkit and Benchmark for Multi-agent Zero-shot Coordination*，NeurIPS 2024 Datasets and Benchmarks，[论文](https://arxiv.org/abs/2310.05208)直接指向该库。仓库明确称FCP、MEP、COLE、E3T是**本项目重实现基线**，不能称作每篇论文原作者实现。

- FCP训练：`zsceval/scripts/overcooked/shell/train_sp.sh` → [extract_sp_models.py:68](https://github.com/sjtu-marl/ZSC-Eval/blob/f940869afc42b688332a385892d8dbb57a190f95/zsceval/scripts/extract_models/extract_sp_models.py#L68) → `prep/gen_S2_yml.py` → `shell/train_fcp_stage_2.sh`。提取`init/mid/final`；**mid按最终得分的一半挑接近检查点，不是训练步数的一半**。脚本从W&B取曲线/权重，卫戍可改为本地run registry。
- 冻结伙伴：[policy_pool.py:101](https://github.com/sjtu-marl/ZSC-Eval/blob/f940869afc42b688332a385892d8dbb57a190f95/zsceval/algorithms/population/policy_pool.py#L101)加载YAML模型，默认`policy_train=False`，仅显式`train`的政策可训练；`trainer_pool.py:18`记录`(env_id,agent_id)→trainer`及按政策buffer。可借这一分离，加入弱启发式、随机合法动作、不同流派及不同训练成熟度。
- 行为候选与选择：`shell/train_bias_agents.sh`有行为奖励权重；[select_bias_agent_br.py:54](https://github.com/sjtu-marl/ZSC-Eval/blob/f940869afc42b688332a385892d8dbb57a190f95/zsceval/scripts/prep/select_bias_agent_br.py#L54)的DPP选择与后续搜索选择多样伙伴。这里的**BR-Div是ZSC-Eval评测伙伴选择指标，不自动等同其他名为BRDiv的训练算法**。
- 最终评测：`shell/eval_with_bias_agents.sh` → [extract_results.py:175](https://github.com/sjtu-marl/ZSC-Eval/blob/f940869afc42b688332a385892d8dbb57a190f95/zsceval/scripts/overcooked/eval/extract_results.py#L175)；按位置与伙伴组合除以对应best-response收益，截到1并用IQM汇总。best response为零的分支源码记1，卫戍报告应另外列无效/不可解伙伴组合，不能把此约定当成功通关。
- E3T重实现入口：`shell/train_e3t.sh` → `train/train_e3t.py` → separated runner；`base_runner.py:166`给非ego伙伴epsilon，`rMAPPOPolicy_epsilon.py:35`混随机动作，`r_mappo_target.py:29`对actor/critic做软复制。本次追踪此路径**未定位原论文伙伴动作预测器及预测损失**，故不能声称它等价完整E3T。

根MIT；environment.yml指定Python 3.9、PyTorch/CUDA11.8，requirements固定Gym0.22、NumPy1.22等，脚本还依赖policy_pool文件、W&B配置和原环境事件特征。它支持2人Overcooked和3人GRF实例，不等于已经覆盖卫戍单人/4人商店运营。最适合复用的是冻结评测池/交叉组合/分层指标，而非环境编码或已发布权重。

迁移风险：E3T epsilon分支直接`act_space.sample()`，没有按传入`available_actions`过滤；原Overcooked动作大多合法，卫戍不能照搬。这里只指出静态迁移风险，未运行非法动作复现。

## 4. COLE：需要时再引入配合图课程

Yang Li等的ICML2023 COLE及JAIR2024扩展 *Tackling Cooperative Incompatibility for Zero-Shot Human-AI Coordination*（[DOI](https://doi.org/10.1613/jair.1.15884)）由[作者项目页](https://sites.google.com/view/cole-2023)链接此仓库，并明确区分训练与网页分支。

- 训练入口：[game_shapley_pbt.py:609](https://github.com/liyang619/COLE-Platform/blob/33e89fab71fa1932fbee173c61d9d96eadbd9c2a/human_aware_rl/pbt/game_shapley_pbt.py#L609)；`RATIO`在616行解析，决定自身/历史伙伴采样次数。685–703行生成不同位置伙伴，历史策略依`inversed_ucb_shapley_value`抽取。
- 图和课程：`pbt/game_graph.py:32`构图，`pbt/shapley_value.py:25`计算近似Shapley，`pbt/utils.py:38`提供逆Shapley加探索项；`game_shapley_pbt.py:726`更新历史池。可以参考“把配合失败的伙伴加入课程”，但不需要首版就承担全图交叉评测成本。
- 跨配合：`human_aware_rl/test/cross_evaluation.py:93`起加载多算法伙伴并双位置评测；BC proxy权重需另取，路径有占位符，不是拿到库就有真实人类分布。
- FCP作者基线：[Baseline_training/fcp.py:459](https://github.com/liyang619/COLE-Platform/blob/3e388e005a8ffe53fc41e902548990fa832cd8c2/human_aware_rl/fcp/fcp.py#L459)读取best、接近best/2、早期三个检查点，523行前后依次入池，599行抽伙伴。它是COLE作者给的FCP基线，**不是已确认的DeepMind原始FCP仓库**。原始方法归属Strouse等，[NeurIPS2021论文](https://arxiv.org/abs/2110.08176)。

训练README用Python3.7，requirements有`tensorflow-gpu==1.15.0`、Gym0.17.2、Sacred0.7.4、MPI等。GitHub main元数据是MIT；本次固定`COLE_training`快照未见根LICENSE/README许可节，vendored Baselines/Overcooked分别有MIT文件，故不能将main标签当已核清整个训练分支授权。没有改装、执行其安装脚本。

## 5. 原始E3T：噪声伙伴与预测器确有代码，但不是一键运行包

Xue Yan等，[NeurIPS2023论文](https://papers.neurips.cc/paper_files/paper/2023/hash/07a363fd2263091c2063998e0034999c-Abstract-Conference.html)提出混合伙伴和伙伴动作预测；[官方项目页](https://sites.google.com/view/e3t-overcooked)直接链接`yanxue7/E3T-Overcooked`，作者归属证据明确。

- 训练链：`human_aware_rl/experiments/ppo_sp_experiments.sh` → `encoder/encoder_ppo.py` → `baselines/ppo2/encoder_ppo2.py`。参数`RAND/COPY/LENGTH`在`encoder_ppo.py:41`附近，实验shell给出具体值；这些是示例设置，未验证其最优。
- 噪声伙伴：[encoder_runner.py:148](https://github.com/yanxue7/E3T-Overcooked/blob/b2e35dc538b8fbbd51d769bc67c5a3d865236c45/baselines/ppo2/encoder_runner.py#L148)在`addrand`分支混合ego动作分布与均匀六动作；另一分支按概率替换随机动作；`encoder_ppo2.py:267`软复制政策权重。实验名字符串会选择路径，不能只记参数而漏掉`EX_NAME`。
- 预测器：[encoder_policies.py:262](https://github.com/yanxue7/E3T-Overcooked/blob/b2e35dc538b8fbbd51d769bc67c5a3d865236c45/baselines/common/encoder_policies.py#L262)用历史观测和动作context，decoder输出伙伴动作logits，279行的softmax分布进入ego策略；`encoder_model.py:150`建立六动作CE目标，`encoder_ppo2.py:199`起每轮执行预测训练。这证实两个模块均存在。
- 评测：`human_aware_rl/experiments/encoder_ppo_sp_experiments_lstm.py`读取人类代理模型并保存历史动作/观测；没有取得权重或运行。该文件18行引用`human_aware_rl.latent.encoder_ppo`，而本快照目录为`encoder`，此外大量基座文件不在包内，需手工合并/核清导入路径。

README明确只发布修改/新增文件，要求合并原`human_aware_rl`；environment.yml实际上是pip列表，包括TensorFlow GPU1.15.5、Gym0.17.2、PyTorch1.12。固定快照未见根LICENSE及README许可声明，许可状态未确认。代码存在硬编码`(30,6)`和六动作监督标签，不能直接用于卫戍变长合法动作与多阶段决策。

## 可直接定位的未来消融入口

| 模块 | 已有开关/位置 | 公平消融应保持什么 | 当前状态 |
|---|---|---|---|
| 弱伙伴动作噪声 | 原E3T `RAND=0`；ZSC-Eval `--epsilon 0` | 同ego初始化、伙伴池/合法动作空间和训练预算 | 可配置，未执行 |
| 伙伴权重滞后 | 原E3T `COPY`；ZSC-Eval `--weights_copy_factor` | 不把COPY=0误当纯self-play；0是冻结初始化伙伴，1是快速跟随 | 可配置，未执行 |
| 伙伴动作预测 | 原E3T `encoder_policies.py:262/279`、`encoder_ppo2.py:199` | 同时去除预测条件输入与CE更新；只关loss仍有随机条件输入 | **没有确认现成bool，需实现独立实验开关** |
| 历史成熟度池 | ZSC-Eval `extract_sp_models.py:68`、生成的S2 YAML | 固定种子数量/池大小或报告变化；比较final-only与init/mid/final | 配置文件/提取脚本选择，未执行 |
| 伙伴优先采样 | MEP `PRIORITIZED_SAMPLING`、`ALPHA` | 固定同一冻结池、采样次数和位置分布 | 可配置，未执行 |
| 人口熵 | MEP `ENTROPY_POOL=0` | 保持普通政策entropy系数不变，别把两者同时关 | 可配置，未执行 |
| Shapley课程 | COLE `RATIO`与`inversed_ucb_shapley_value` | 区分纯self-play/均匀历史采样/图课程，固定交互预算 | 部分配置，uniform对照需改采样逻辑 |
| 测试伙伴选择 | ZSC-Eval选择脚本及benchmark YAML | 训练/验证/测试池隔离；测试分层与seed固定 | 协议/配置修改，未执行 |

卫戍迁移时，这些比较应额外固定官方复刻commit、ban位、敌人/商店种子、终极难度、流派标签和弱AI等级；分别报告团队胜率、自己漏怪/贡献及最差伙伴分位。以上是研究建议，不是这些仓库已经提供的卫戍功能。

## 未确认/未选择的来源

- *Improving Zero-Shot Coordination with Diversely Rewarded Partner Agents*，IJCNN2024，DOI`10.1109/IJCNN60899.2024.10649945`：本轮未找到可由论文/作者页面直接认证的官方代码，不能把搜索摘要中相邻论文的GitLab链接算给它。理论相关，代码状态仍未确认。
- 未额外克隆JaxMARL或单独BRDiv，以五库范围完成核验；ZSC-Eval含GRF三人示例也不能替代卫戍四人的组合评测。
- FCP原始DeepMind发行代码本轮未确认；可用实现来源已经清楚标为ZSC-Eval/COLE作者基线。

所有克隆保持原始工作树，未执行仓库入口。服务器镜像只写`/data1/zsm/arknights/literature/repos/`和同目录`CODE-REFERENCES.md`，不覆盖主线程OpenAlex文件。
