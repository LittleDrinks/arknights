# Own gold：真实数据 OFF/ON 训练与 dev 评估入口

基线 `fbd7c2bd0232949cb1fa8f94c02d44c4790b1233`；只新增 `gold-study/`。复用已审 `gold-features/recapture.py`、`warm_start.py`、原 `rl/learner.py`、自然个人胜局合同，以及 `survivor-study/evaluate.py` 的原 `Protocol` / `state_contract`。不改游戏、旧模块或模块全局来切换 schema。

这是可执行入口，**本次没有重采真实 demo、训练真实数据或运行 Match/Battle**。服务器执行由 main 负责。hash/接口验收不自动批准 source，所有报告继续标记 `collector-source-trust-pending-main-independent-acceptance`。

## 服务器执行顺序

Python 3.10+、已有 PyTorch、Node **>=22**（固定游戏 package 要求；服务器实际 v22.23.2）。不安装依赖。`--root` 示例为 `/data1/zsm/arknights`，规则目录固定为 `ROOT/game`；代码位于 `ROOT/experiment-code` 或显式 `--experiment-code`。CLI 必须来自该代码 checkout。原父模型路径限定为 `ROOT/experiments/bc-expansion-v1/all.pt`，路径和 SHA 均须显式传入。

以下命令仅供 main 在服务器执行。先把变量填成实际冻结路径/SHA；不得把一个仍在运行或整体 failed 的采集批次作为输入。`SELECTION` 是冻结源 manifest 下六个自然个人成功 gzip 路径的 JSON 列表，顺序也会被冻结。`COLLECTOR` 必须指向实际原 collector 源码。

```sh
ROOT=/data1/zsm/arknights
CODE="$ROOT/experiment-code"
PYTHON=/absolute/existing/python3.10
SOURCE_MANIFEST=/absolute/frozen-natural-search/manifest.json
SOURCE_MANIFEST_SHA256=MAIN_FROZEN_SOURCE_SHA256
SELECTION=/absolute/six-personal-wins.json
COLLECTOR=/absolute/collect-natural-coop-teachers.py
RECAPTURE="$ROOT/experiments/gold-recapture-v1"
PARENT_SHA256=MAIN_EXPLICIT_BC_EXPANSION_ALL_PT_SHA256

"$PYTHON" "$CODE/gold-features/recapture.py" \
  --root "$CODE" --game "$ROOT/game" --collector "$COLLECTOR" \
  --manifest "$SOURCE_MANIFEST" --manifest-sha256 "$SOURCE_MANIFEST_SHA256" \
  --selection "$SELECTION" --out "$RECAPTURE" --seconds 1100 \
  --max-bytes 67108864 --output-bytes 268435456
```

**重采成功后**，main 核对 `manifest.json` 的 `status=complete` 和 12 条 `verified` 结果，再冻结其 SHA。训练命令不会启动采样，也不会训练部分成功的目录。

```sh
RECAPTURE_MANIFEST_SHA256=MAIN_FROZEN_COMPLETE_RECAPTURE_SHA256
"$PYTHON" "$CODE/gold-study/run.py" train \
  --root "$ROOT" --experiment-code "$CODE" \
  --parent experiments/bc-expansion-v1/all.pt --parent-sha256 "$PARENT_SHA256" \
  --input-dir "$RECAPTURE" --manifest-sha256 "$RECAPTURE_MANIFEST_SHA256" \
  --collector "$COLLECTOR" --out experiments/gold-study-train-v1 \
  --total-timeout 1100 --output-mib 512

"$PYTHON" "$CODE/gold-study/run.py" eval \
  --root "$ROOT" --experiment-code "$CODE" \
  --parent experiments/bc-expansion-v1/all.pt --parent-sha256 "$PARENT_SHA256" \
  --trained experiments/gold-study-train-v1 --out experiments/gold-study-dev-v1 \
  --dev-only --workers 4 --timeout 120 --total-timeout 1100 --output-mib 64
```

两个 CLI 独立；训练失败不会触发评估。输出目录必须不存在，拒绝已有目录/符号链接。证据、raw 副本、checkpoint 和最终 train report 均 exclusive create；只有新 eval 目录内的进度 `summary.json` 会原子更新。失败产物保留并明确标失败，不能作为 complete 训练输入。train 的总证据上限 512 MiB（含 1 MiB 最终报告预留）；输入每项/解压至多 64 MiB，18 个 demo 的展开总量至多 512 MiB。超时覆盖准入、训练和首末 guard。

## 准入与训练合同

- 重读实际原 manifest、selection、collector、六个旧 gzip、十二个新 gzip。验证完整 2048 项源计划、冻结状态、实际源码绑定、两份 manifest/selection 副本一致、gzip CRC/EOF、compressed/raw SHA 和字节数、raw 文件集合；不扫描/解压整个大型旧数据集。
- 每个 seed/pool 必须恰有唯一 verified OFF 和 ON，共六个不同 seed episodeGroup、每 arm 498 个相同标签。使用原自然胜局完整合同验证旧样本和个人成功；对新 demo 调原 `verify_recapture`，仅撤销 schema version/global 追加列后整个 response 必须 exact，包含额外字段、终局、诊断和 observation。新 features 另外检查完整维度、有限数值、ON 非负 / OFF 尾列全零。训练不能从 terminal 回填 gold；ON 来自已审 loader 在原动作前 encode 的实际 own。
- 完整词表推导原 global 5267、新 global **6410**、candidate **419**、追加 **1143**；生产 CLI 同时核对规则静态词表与该固定尺寸。合成测试可用小词表，生产 CLI 没有绕过开关。
- `warm_start` 保留原参数，仅将第一层输入拓宽、追加权重置零。warmBaseline、trainedOff、trainedOn 的所有初始参数逐 tensor 严格相同；原 CPU float32 模型、默认 dtype32。全局默认 float64 会拒绝，避免原 forward 的 padding dtype 不兼容。
- 两 arm 使用同一顺序的 498 个标签、6800 个 cyclic draws、同 seed 101 的 randperm、1 epoch、batch34、lr0.0003、原 Adam / grad clip0.5、恰好200 updates。记录真正的索引访问，检查顺序/Adam step，并记录实际每标签/每 episodeGroup drawcounts。每个标签 13 或14次，前326个14次；无再采样、隐藏筛选或终局特征进入 actor。
- critic **head 参数**保持不变；共享 state encoder 会参与 BC，不能据此声称整个 value 函数不变。OFF 新列权重保持零。训练 loss/标签准确率仅是拟合指标，不能当胜率。

保存 `warmBaseline.pt`、`trainedOff.pt`、`trainedOn.pt`，均为原 `L.load_checkpoint` / `validate_checkpoint` 可读的标准 `model/dimensions/schema/rules/metadata/optimizer/torch_rng` 格式。写文件用有限缓冲 + exclusive writer；没有平行模型架构。metadata 记录具体 mode、超参、可见训练标签/episodeGroup、实际次数、parent 全 metadata/SHA、旧新来源、schema、源码和证据哈希。baseline 无训练，计数为0。初始 OFF/ON 输出只声称容差内数值等价，不声称真实全量数据与原 v1 逐位等价。

目录同时保存完整旧/新 gzip 副本、冻结 recapture/source manifests、原 selection、逐标签来源 `input-metadata.json`、实际使用的 `draw-plan.json` 和最终 `train.json`。原输入也必须保留：eval 会在首末重新校验它们，而非只相信训练副本。

## 评估合同

固定 `warmBaseline(off)` / `trainedOff(off)` / `trainedOn(on)` × `original` / `mixed` × dev seed1..16，ABYSS、coop、seat0、budget128、greedy，**96 局、16 个配对 seed groups**。同 seed 在 pool/arm 间复用，不能作为96个独立 seed；不用 heldout/validation/sealed。个人胜与团队胜分别计数，失败和 unrun 都保留在预写的96项计划和分母中。

最多4并发 CPU worker，原 Torch intra/inter-op 各1线程；每局最多120s、输出64 MiB（为 result 预留16 KiB），整批 CLI 至多1100s，调度提前留出清理/末尾 guard 时间。每局原 `Protocol` 管理独立进程组、stdout/stderr、逐字节 raw、绝对时限、close ack 及严格 EOF。`--no-warnings` 防止 Node loader 的提示污染严格 stderr 合同。子进程命令是：

```text
node --no-warnings --loader CODE/gold-features/loader.mjs CODE/coop/env.mjs --game ROOT/game
```

child env 清掉继承的注入项，显式给 `GOLD_FEATURES_ROOT/MODE`、`COOP_POOL/SPLIT=eval/POOL_SEED=313/TEAM`。验证首先核对**实际**新 schema、完整尺寸和新 tail/OFF 零，再建立只供验证的浅 copy：schema.version 改回 shop-v1、global 取旧 prefix。随后原 `E.state_contract` 和默认 `teacher=False` 的 `C.terminal_contract` 验证规则、身份、诊断和完整终局。投影不传模型、不覆盖 raw。actor 仅 `model.act(actual_state['features'])`；actions 保存全部候选、mask、index，实际 features 已完整保存在 protocol raw。终局 raw 在验证前原样保存，验证失败仍保留。

首末检查 source、明确父模型、486项完整游戏规则/包文件集合与 SHA/HEAD、静态数据、输入、训练证据和模型参数。guard 失败会撤销本轮全部已完成结果的胜场信用；计划仍保留。checkpoints 与 eval gate 都核对具体 arm/mode/schema/provenance。

## 本地合成测试

```sh
env PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES='' \
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  /usr/bin/time -f 'elapsed=%e maxRSS_KiB=%M' timeout 18s \
  /home/q2635/venvs/logics-v3/bin/python gold-study/tests.py
```

只用 tiny vocabulary + 498个合成标签、原 learner 每 arm200次更新、fakeProtocol、Python mock children；测试不导入游戏/static loader，不运行真实采样。全套外部18s timecap、RSS检查<1GiB，每测试仅在完成后检查其自有临时目录<5MB并清理；不复用自然研究的 rglob 并发 monitor。验证记录见 `CHECKS.md`。

最终评估发布前重新核对每局协议、动作、终局和结果元数据的文件集合、大小与SHA；与外层resultFile摘要绑定的result.json正文亦须一致。完整局缺证据、既有文件漂移、未知新增文件均进入全局失败和胜场撤销路径，保留原始异常文件。
