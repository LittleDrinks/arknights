# 已校验数据的 opt-in Tensor BC 缓存

起点 `ce9c4c6f624b1093872b3192db95a122aafaf0e0`，仅新增 `tensor-bc/`。
不改 interaction、rl、game、parallel；不 SSH、不安装、不启动子 agent、不创建 goal。
原 server paired 16,014 labels / 24 epochs / hidden=128 训练仍由主线程管理，本任务没有接触、打断或重跑它。
新增入口只能在**新进程、新输出目录**中使用，完成提交后等待独立 review。

用户提供的主 server profile：3 次 batch32 forward 共约 0.625s，`validate_features` 约
0.555s（89%），`torch.tensor` 约 0.057s，真实神经算子约 0.006s；报告路径
`serverexperiments/interaction-forward-profile-v1.json`。本 checkout 没有该报告、实际游戏或生产数据，
以上只是任务背景，未在本机独立核实。下方本机合成计时不能替代 server 端实测。

## 数据与 ownership

`cache.load_cache` 调用未修改的 `interaction.dataset.load_dataset`，完整读至 EOF，保留
schema/rules、seed、192 局、16,014 标签、split、mask、label、终局、文件 SHA 的全部准入条件。
前后额外核对输入 SHA；没有截断、子集或快捷准入。legacy 仍要求显式 opt-in；其终局与 split
来源仍继承原 manifest 的信任限制。原 raw dataset/admission 不变。

准入成功后 `_TensorCache` 私有构造器再核对 labels/features，创建独占的 CPU float32 global、
紧凑拼接的 candidate Tensor 和 bool mask Tensor。mask 保留原实现的 bool 语义。
只转换一次；不按整个数据集最大候选数分配巨大 padding。每 batch 依原宽度分配零 padding，
复制对应候选和 mask，原候选顺序不变。

训练样本是 tuple，sample 和 feature handle 是 `MappingProxyType`，mask 是 tuple；
只暴露 `bc_update` 所需的 immutable action/mask。handle 与原 features dict 分离，缓存保留
每个 handle 的强引用并检查对象身份，避免 id 重用；重复原 feature refs 也各自获得固定快照。
修改原 dict 不会改变缓存。张量及其 view 不通过公共 API 暴露，batch 都是新拷贝。
这是可信 Python 训练代码的 ownership 约定，并非防御刻意访问私有属性的进程内恶意代码。
不得修改私有 Tensor 或跨线程共享这个 context。

仅 `training_models()` context 中新建的模型实例有临时 forward。已知 handle 仅在 train 模式且
启用梯度时使用缓存；混合私有 handle/外来输入拒绝。全为外来 features 时调用原 forward，
所有原校验照常执行，包括原 dict 被改坏的情况。eval/no_grad 下的 handle 拒绝；普通合法
features 的 eval 保持原行为。退出或异常后恢复实例 forward 和 `train.make_model`。
模型类、原有模型实例、dataset loader、checkpoint loader 始终没有被 patch。

## 算法与产物

训练直接调用未修改的 `interaction.train.train_pair` 和 `rl.learner.bc_update`。
原 init seed、共享 state/critic 初值、order seed、shuffle、epoch、batch、Adam、lr 和 clip=0.5
全部复用。concat-baseline 的 concat/Tanh/actor，以及 interaction 的 candidate/Tanh/逐元素乘积/
product_score、critic、mask -inf、Categorical 归一化均按原类的运算表达式执行。
本改动没有重新实现优化器或训练循环，也没有比较两个不同架构的参数是否相等。

checkpoint 仍由原 `save_checkpoint` 写出，使用同一 `interaction-bc/v1`、架构、schema/rules
和 state_dict；不存在缓存版推理模型、持久化 forward、skip-validation 标记或 loader 分支。
用原 `interaction/model.py` loader 加载和原 `interaction/eval.py` 评估；推理继续完整校验。

`train.json` 与 checkpoint 的 `metadata.source` 包含：

- 当前完整源码 `files`/`sourceSHA256`，包含 `tensor-bc/*.py`，而非只继承 interaction 摘要。
- `tensorcacheFiles`/`tensorcacheSourceSHA256`，明确覆盖 wrapper、入口及测试；
  `originalSource` 单独保留原 interaction/rl 文件 SHA、聚合 SHA、原 base/HEAD。
- `tensorCache.inputSHA256` 的实际数据与 manifest SHA、固定快照 ownership、算法复用说明。
- 数值阈值、CPU 合成验证范围、非生产数据证明、非跨设备证明、非 bitwise 声明；
  `performedInThisTrainingRun=false` 明示训练入口不自动重跑数值实验。
- Torch 版本、缓存字节数、准入加建缓存耗时；原每 arm 时间仍是 BC 循环区间。

`run.py` 在导入生产模块前记录源码文件摘要，进入训练、准入结束、每次保存前后及训练完成后
都检查源码/HEAD 一致性。漂移或训练异常时恢复所有临时绑定，删除本次创建的 checkpoint，
输出 failed；不会留带 complete 标记的可用权重。测试模拟了保存前、保存中和第二 arm
保存后发生漂移。此边界检测不防御恶意修改后又在检测前恢复的文件系统攻击。

输出必须在**当前 checkout 的 `tensor-bc/`** 内，目录必须不存在；checkpoint 用 exclusive
create。输出目录解析真实路径，不能通过 symlink 指向其他项目目录。

## 服务器运行安排

由主线程在 server 审查并安排新进程，不要求重跑或中断现有 baseline。以下是待执行示例；
`--data` 和 `--game` 必须按现场实际绝对路径核对，manifest 使用本项目已完整列出 192 局的
`experiments/interaction-input-v1.json`，不要生成替代子集 manifest。

```bash
python3 -B tensor-bc/run.py \
  --tensor-cache \
  --data /data1/zsm/arknights/experiments/bc-expansion-v1/all.jsonl \
  --manifest experiments/interaction-input-v1.json \
  --game /data1/zsm/arknights/game \
  --allow-legacy-metadata \
  --epochs 24 --hidden 128 --lr .0005 --batch-size 64 \
  --init-seed 101 --order-seed 101 \
  --out tensor-bc/artifacts/paired-16014-cache-v1
```

生产 CLI 强制显式 `--game`，复用 `game_schema` 校验真实游戏 HEAD 为
`6ab2468279e926d107788f63d7fe219b85f67b4a`、server/shared/data 无 tracked 修改，并由其
实际 data 派生 schema 与 manifest 精确比对。`--tensor-cache` 缺失即拒绝。
synthetic 模式独立限制空 vocabulary、原 synthetic teacher、≤128 标签、≤16 局、hidden≤32、
epochs≤200；不能借此运行生产数据。

server 应记录相同 config、输入 SHA、源码 SHA、机器/Torch 信息，以及建缓存时间、两 arm
BC 时间和总耗时；考虑原训练并发造成的 CPU 竞争。只有主 server 测量后才可声称 server 性能提升。
本任务没有执行该命令，没有训练效果或实际 server 提速结论。

## 本地有界验证

```bash
python3 -B tensor-bc/tests.py --out tensor-bc/artifacts/verify-new
```

默认入口是轻量监督进程，只启动一个 Torch 测试子进程，无真实 sim。约每 10ms 抽样
监督器及全部后代的 RSS 求和，超过 1GiB 或总输出接近 5MB 即终止；19 秒停止以为清理
留出 20 秒总预算。`--child` 是内部参数，不用于独立运行。RSS 是抽样峰值，不是内核瞬时
硬上限；报告保留最大采样间隔。输出目录存在时拒绝覆盖。

6 个测试覆盖：

- 独立执行原 raw `train_pair` 和缓存 `train_pair`，两 arm、相同 init/order seed、8 个样本、
  hidden=8、3 epochs、batch=3，各 9 次 Adam。hook 记录实际输入顺序、global、候选、宽度、
  padding、mask、logits/logprob、value、loss、每个参数裁剪前后梯度及每步更新后参数。
  包含变长、false mask 和不足 batch-size 的尾 batch；critic 没有 BC 梯度。
- 重复 feature refs、不可变句柄/labels、原 features 损坏、外来合法 fallback/非法拒绝、混合批次
  拒绝、确实绕过训练热路径校验、eval/no_grad、异常恢复及 context 内 loader 不受影响。
- 坏 schema/mask/seed、缺终局、重复终局、额外 EOF 行、末行截断、生产规模、legacy opt-in、
  输入 SHA 漂移的拒收。
- 实际 CLI、metadata SHA、专用 checkpoint 重载、与独立 raw 训练的最终参数比较、推理校验、
  nooverwrite/路径约束、实际 game pin 调用；源码保存前后漂移/异常清理。
- 本机两 arm 各 20 次 batch32 forward 计时；不含准入、建缓存、backward 或 Adam。

比较阈值为 `atol=1e-7, rtol=1e-5`，不声称 bitwise 等价或在更大数据/架构/平台上已经证明。
最终 6 项契约通过：494 组 Tensor 比较的观测最大绝对差为 0；整棵测试进程树耗时
2.524s，抽样 RSS 峰值 791,011,328 B，最大采样间隔 21.9ms，资源报告前输出 331,024 B。
本机各 20 次 batch32 forward：concat 原实现 0.02030s / 缓存 0.00813s，interaction
原实现 0.02030s / 缓存 0.00846s。该 hidden=8 微测只用于检查缓存路径，不是生产提速结论。
最终运行原始报告保留在 `evidence/numerics.json`、`evidence/resources.json`、`evidence/tests.log`；
源码内容 SHA 对应实测版本，HEAD 可能是提交前的父 commit。checkpoint 和临时拒收 fixtures
保留在被忽略的 `artifacts/`，不提交生产数据或合成权重。
