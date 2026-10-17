# BC fit budget

仅新增 `bc-fit-budget/`，base `d69260ca15ee13b4cf150e1a89ecfd0d66dcb69b`。
这是交给 main 的服务器入口；本次不 SSH、不安装、不读取真实样本、不训练真实数据、不运行 game/Match/Battle。

动机来自已有结果（本次未重跑）：同父模型的 natural-win-bc-v3 使用六个个人胜局、498 条标签、6800 draws / 200 Adam updates，训练命中442/498，dev64+seen12 全0。原样编码剩余56条各自 vector 不同。这里用增加训练步数检验 underfit；训练命中提高并不证明赢局或泛化提高。

## main 在服务器执行

Linux、Python 3.10+、已有 PyTorch、Node **>=22**；已提供的服务器版本为22.23.2，不要求24。直接使用已有 Python 环境，不安装依赖。将本提交合入服务器的代码 checkout；命令里的 CODE 必须就是运行本 CLI 的 checkout。所有相对路径相对 ROOT。输出目录必须全新，拒绝符号链接及含 `..` 的路径。

```sh
ROOT=/data1/zsm/arknights
CODE="$ROOT/experiment-code"
PYTHON=/absolute/path/to/existing/python
COLLECTOR=/absolute/path/to/collect-natural-coop-teachers.py

"$PYTHON" -B "$CODE/bc-fit-budget/run.py" train \
  --root "$ROOT" --experiment-code "$CODE" \
  --input-dir experiments/natural-gold-recapture-v1 \
  --collector "$COLLECTOR" \
  --out experiments/bc-fit-budget-train-v1 \
  --total-timeout 1100 --output-mib 512

# 仅在 train.json status=complete 后，由 main 显式执行。
"$PYTHON" -B "$CODE/bc-fit-budget/run.py" eval --dev-only \
  --root "$ROOT" --experiment-code "$CODE" \
  --trained experiments/bc-fit-budget-train-v1 \
  --out experiments/bc-fit-budget-dev-v1 \
  --workers 4 --timeout 120 --total-timeout 1100 --output-mib 64
```

父模型和 manifest SHA **固化在入口中，没有可放宽参数**：

| 资产 | 固定身份 |
| --- | --- |
| 父模型 | `ROOT/experiments/bc-expansion-v1/all.pt` |
| 父模型 SHA256 | `f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a` |
| recapture manifest SHA256 | `f96bfe2e7b39e35e0de0f26f60f2115c0211ef39b1b20f32d9a0c5c1afcae992` |
| recapture 默认位置 | `ROOT/experiments/natural-gold-recapture-v1/manifest.json` |

原 manifest 内的旧数据、selection 和 collector 源码的绝对路径必须仍可读。保留原输入与训练目录，eval 首末都要重读。`--input-dir` 允许指定同一冻结输入的实际位置，不改变 SHA。

## 复用与训练合同

直接调用已审 `gold-study/data.admit`，准入六旧 gzip + 十二 OFF/ON recapture；完整保留压缩/展开 SHA、gzip CRC/EOF、manifest/selection/source bindings、整个旧新 response exact 验证、个人成功与终局合同。没有重写准入或复制模型架构。继承的 `inputReviewStatus` 仍是 `collector-source-trust-pending-main-independent-acceptance`：该字段是旧合同的来源治理标记，不能凭哈希升级批准；独立审查记录应由 main 保留。

准入后仅取 OFF 的每条 `features.global[:5267]`，schema 恢复 `shop-v1`，candidate419 与 mask/action 原样保留。ON 只参与原准入与证据保存，不参与训练。actor 输入没有终局或标签来源 metadata。强制原 `L.ActorCritic`、CPU float32、dimensions `[5267,419,128]`。

| arm | epochs | 固定 Draws 长度 | 实际 indexed 访问 | Adam steps |
| --- | ---: | ---: | ---: | ---: |
| steps1000 | 5 | 6800 | 34000 | 1000 |
| steps3000 | 15 | 6800 | 102000 | 3000 |

每 arm 从原父模型完整复制后独立启动新 Adam，调用一次原 `L.bc_update`；绝不从 steps1000 接着训练 steps3000。直接使用原 `T.Draws`，batch34、lr0.0003、grad clip0.5。模型构造后重置 torch seed101，每 epoch 使用原连续 RNG 的一次 randperm；不是每轮重置同一排列。steps1000 的访问序列等于 steps3000 的前五轮。`training.samples=6800` 是原方法报告的容器长度；累计访问单独记录。

保存 `steps1000.pt`、`steps3000.pt`，均为原标准 `model/dimensions/schema/rules/metadata/optimizer/torch_rng` 格式，原 loader/validator 回读验证。没有 cache、新架构、warm_start 或既有模块全局 monkeypatch。critic **head** 保持逐 tensor 相等并记录首末 SHA；共享 encoder 会变，不能称 value function 不变。

证据包括两 arm 初末全参数 SHA、父模型 SHA/metadata、critic head SHA、每参数 Adam step、逐次实际访问/标签索引、每标签/episodeGroup 访问计数、before/after greedy action accuracy 与 NLL、完整 draw-plan、6+12 gzip、原/recapture manifests、selection、逐标签来源 metadata、输入路径与 SHA、代码依赖 pins 和本目录全部文件 SHA。首末核对486份游戏规则文件集合/SHA/HEAD及静态词表；`gameIdentity` 与完整规则 manifest 的固定 SHA 绑定，末尾身份和验证项单独记录在 `finalChecks`。

## 有限评估与信用

计划固定为 `parent / steps1000 / steps3000 × original / mixed × dev1..16 = 96`，每 cell16局、共享16个 seed groups。只用 dev，无 seen/heldout/validation/sealed；没有按开发结果选 checkpoint。coop、ABYSS、seat0、budget128、greedy、poolSeed313、teacher=False。

三模型冻结参数，单局**原样调用 `E.game_job`**，所以直接复用原 `E.Protocol`、`E.state_contract` 和 `C.terminal_contract` 的 teacher=False 路径。运行原 `coop/env.mjs`，不用 gold loader。保存完整 protocol raw、actions、stderr、验证前原始 terminal、close ack/EOF状态、result；失败无 terminal 时不补造终局。

开局前保存不可变96项 manifest。技术失败和未运行项都留在96分母中，团队胜与个人胜分别统计；进度报告 `creditGranted=false`。只有末尾全部 guard 通过且96局 complete，最终报告才 `status=complete, creditGranted=true`；部分执行失败时已通过审计的完成局仍单列统计，整批返回非零。

末尾核对原 source/parent/rules/input、训练目录全文件集合/大小/SHA、每模型内存参数 SHA，以及每局 payload/终局/result 的文件集合/大小/SHA和 result 正文。输出、游戏子目录、输入、模型、源码及各级目录拒绝 symlink；未知新增文件也拒绝。漂移会把全部已完成行改为 failed，清空其团队/个人胜信用。保留原始异常文件，不改写每局历史 result；最终 summary 是审计后的信用结论。最终报告通过最初打开的目录 descriptor 发布，即使输出路径被替换也不会跟随新 symlink 写成功报告。

Linux affinity 将整个继承进程树限制在最多4个可用 CPU；Torch intra/inter-op 各1线程，最多4个并发游戏。train 整条命令预算1100s、总证据512MiB（预留1MiB报告）；eval 整条命令1100s、每局120s/64MiB（给 result 预留16KiB，超出即技术失败），最多96×64MiB游戏证据，控制 manifest/admission/summary 另计，最终 summary 上限4MiB。这里 MiB 是**产物字节上限，不是生产进程 RSS 限额**。准入沿用每压缩/展开文件64MiB、累计展开512MiB上限。超时覆盖准入、训练及首末 guard，eval 最多预留30s做清理和末尾检查；操作系统调度、进程组清理和最终失败报告有少量额外开销。无无限重试、无自动续跑。

## 本地合成检查

```sh
/home/q2635/venvs/logics-v3/bin/python -B bc-fit-budget/check.py
```

监督器沿用已审 natural-win-bc 的 Linux `/proc` 资源监控方式，19s kill 阈值、测试进程树合计 RSS≤1GiB、临时输出≤5,000,000 bytes，结束清理。仅微型原 ActorCritic 的两 arm 合成更新、合成完整维度 OFF 投影、gzip/CRC/EOF、Python 假协议进程、模拟96项调度与文件漂移测试；不运行 Node/game/static loader，不导入 Match/Battle，不访问真实训练输入。Python3.10 AST 检查不是3.10 runtime 验收。执行记录见 `CHECKS.md`。
