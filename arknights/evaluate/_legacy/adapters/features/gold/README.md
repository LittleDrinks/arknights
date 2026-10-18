# Own golden count 消融 seam

基线 `d8805de9ccc1d4d861fe4a60f67197442078e6d9`。仅新增本目录，原 `rl/coop/game`、controller、候选和动作时序不改。这里没有训练/eval 框架，也没有真实终极通关率结果。

v1 global 每个 id 的 13 维没有显式分区域 golden 数量；但 sell candidate 已有 golden 位，原 features 的其他部分也可能相关。因此这是**可测表示缺口**，不是完整 features 看不见 gold，也不是合法可达状态不可区分的证明。合成 fixtures 不声称可达。

`schema.mjs` 定义共同的 `shop-gold-v2`。保留原 schema 完整 ids/bonds/bands 词表和顺序；只换 version。global 在原始前缀后按 `schema.ids` 顺序，为每 id 追加 board、hand、temp 三个计数的 `Math.log1p(count)`，与原 scale 对非负计数一致。只统计当前 `observation.own` 中 `kind === 'chess' && golden === true` 的 piece；item、token、未知 id 不进入计数。维度为旧 G + 3×ids，candidate 维度不变。

- `off`：同长全零附加列；`on`：真实 own counts。二者 schema/dims 相同，mode 不进入模型。
- `wrapper.mjs` 提供 `makeSchema/encode/pieces`；调用原 encode 时使用局部 `shop-v1` schema copy，不改调用者。原 global 数值前缀、candidates、mask 保留；后两者在 wrapper 中甚至保持原引用。
- `loader.mjs` 仅对精确 `rl/env.mjs` URL、固定源码 SHA256 替换唯一 features import；原 features 源码也做 SHA 校验。其他 URL 原样传递，未知源码拒绝。没有 mode 时 loader 返回原结果；非法 mode 拒绝。每 arm 使用独立进程，不在运行中切 mode。
- 没启用 loader 时，原环境完全不受本目录影响。`off` **不是**取消 loader：它仍生成扩展维度，只把附加值置零。

Node 24 启动接口（以下真实游戏命令仅供 main 后续服务器执行，本次未执行）：

```sh
GOLD_FEATURES_ROOT=/absolute/experiment-code GOLD_FEATURES_MODE=off \
  node --loader /absolute/experiment-code/gold-features/loader.mjs \
  /absolute/experiment-code/coop/env.mjs --game /absolute/Stronghold-Protocol
```

改 `GOLD_FEATURES_MODE=on` 即另一 arm。ROOT 默认本目录的父目录，指定时用真实绝对路径；`--game` 可指向服务器 `root/game` 或本地 Stronghold 目录。原环境继续执行其规则 SHA/clean 校验。不能叠加其他改变源码的 loader。

`convertBeforeAction(features, oldSchema, {timing: 'before-action', own}, mode)` 是纯离线接口，返回 `{schema, features}`。只能传该 sample **实际动作前**捕获的 own；timing 标签不能认证快照真实性。不能传终局 own、其他时刻 own，或用终局 gold 回填。旧自然 teacher demos 只有编码/action/round，没有逐 sample own 快照；history 的 `visiblePiecesAfter` 也不是替代品。旧数据必须重采样。

`recapture.py` 是 Python 3.10 标准库 CLI，导入不运行游戏。接受 `collect-natural-coop-teachers.py` 已结束并冻结的 manifest（显式 SHA256、finishedAt，允许整体 complete/incomplete，拒绝 failed，但所选行必须 complete 且是合格正例）及 JSON gzip 路径列表；相对路径相对于 manifest 目录，文件名不作为胜例证据。核验完整 2048 seed/pool 计划身份、所有原 `rl/coop` 源码和 collector SHA、所选 gzip SHA/长度、原终局和正例资格。源码路径通过 `--root` 和 `--collector` 映射，旧 artifact 路径仍须在 manifest 所指位置。

```sh
python3.10 /absolute/experiment-code/gold-features/recapture.py \
  --root /absolute/experiment-code --game /absolute/game \
  --collector /absolute/tools/collect-natural-coop-teachers.py \
  --manifest /absolute/natural-teachers/manifest.json \
  --manifest-sha256 FROZEN_MANIFEST_SHA256 \
  --selection /absolute/selected-positive-paths.json \
  --out /absolute/new-gold-recapture --seconds 120 \
  --max-bytes 5000000 --output-bytes 5000000
```

每个所选 demo 分别通过原 `CoopEnv` CLI + loader 重跑 OFF/ON；相同 seed、pool、train split、pool seed 313、固定 team、ABYSS、budget 128。清除继承的 `COOP_/ARK_/GOLD_FEATURES_/NODE_` 注入参数，不改 teacher 决策或 RNG。唯一新增输入来自原 encode 调用中的 own；不读 Match、pool、future、terminal 来生成 gold。

比较会只还原顶层/terminal schema 和 sample/terminal global 前缀，然后递归比较**整个旧 response**：全部 samples（含其他字段）、完整 terminal（含原 features 前缀、observation、summary、partner audit 等）及其他顶层字段均必须一致，不忽略时间戳等“噪声”。差异报第一个字段路径，拒收，不尝试修补。在输出预算内先保存全 raw，再比较并标记每行 verified/rejected。`complete` 才是可消费数据集；失败目录可能包含已核对的部分 raw 和被拒收 raw，禁止当完整配对输入。

新 `.json.gz` 保留 Node 输出的整行原始 bytes（包括换行），记录 compressed SHA、raw SHA/长度、旧 gzip 路径/SHA、冻结 manifest/selection 副本及 SHA、源码和实现 SHA、命令及时间。输出目录必须不存在，每文件 exclusive create，manifest 只写一次。默认总时间 120s、单输入/解压/child stdout 5MB、总输出 5MB，最多选 32 个；stderr 最多 16KiB。显式上限为 1800s、单体 64MiB、总输出 256MiB，超限 kill/reject，不截断 raw。大样本由 main 在服务器显式给有限预算；这里没有执行真实重采样。

`warm_start.py` 提供 `warm_start(parent_checkpoint, ActorCritic, generator=None)`，返回 `(model, checkpoint)`，调用者从已配置 root 导入原 `rl.learner.ActorCritic`。只支持原 CPU float32 标准 checkpoint。`state.0.weight` 拓宽，旧列及其他参数逐位 copy，新增列为零；完整词表保留、schema 改成共同 v2，清除旧 optimizer state（原 `bc_update` 自建 Adam）。返回标准 `model/dimensions/schema/rules/metadata/optimizer/torch_rng` checkpoint，可用原 loader/validate_checkpoint 和 `bc_update`。

```python
from rl.learner import ActorCritic, bc_update
from warm_start import warm_start  # 将 gold-features 目录加入 sys.path
off_model, off_checkpoint = warm_start(parent_checkpoint, ActorCritic)
on_model, on_checkpoint = warm_start(parent_checkpoint, ActorCritic)
# 后续 main 保存时使用 exclusive create；本 helper 不写文件、不调用 bc_update。
```

两 arm 必须用同一个 parent，得到相同初始完整参数；可选 CPU generator 和全局 Torch RNG 都不被 helper 消耗。原 `bc_update` 的 randperm 使用全局 RNG，main 仍需在每 arm 训练开始恢复相同 RNG、使用相同 sample 顺序/超参，不能连续训练两 arm 后宣称 data order 相同。新增零列保证初始函数数学等价，矩阵形状变化可能有浮点舍入差，测试按 `rtol=1e-6, atol=1e-7` 比 logits/value；参数复制本身严格相等。OFF 输入全零使新列 BC 梯度为零，ON 可产生非零梯度；这不是训练收益证据。

下一步由 main 接回已审 `natural-win-bc`：先真实 paired recapture 全字段验收，再用同一 warm start、冻结训练数据/超参和配对评测计划做消融，报告实际终极通关率及分母。当前没有接入/运行该框架，没有训练、真实 Match/Battle、胜率或泛化结论。

本地验证：`timeout 15s node --max-old-space-size=128 gold-features/tests.mjs`；`PYTHONDONTWRITEBYTECODE=1 timeout 15s /path/to/python3.10 gold-features/tests.py`。全为小维度合成或 mock；Python 只做 forward/backward，未执行 optimizer step。有限实测记录见 `CHECKS.md`。


独立复审后的准入修复：只接收 complete/incomplete 冻结批次，拒绝整体 failed；前后对全部原始 .json.gz 文件集合对账。重采前后核对固定 486 个游戏规则/包文件的完整集合和每项 SHA，以及规则仓库 HEAD；规则清单本身绑定 SHA 236708b9…3270，gameIdentity 写入结果。warm-start 显式构造 CPU float32 参数，即使调用时全局 default dtype 为 float64，也不修改该默认设置或消费 RNG。原 ActorCritic forward 的 padding 仍依赖默认 dtype，其正常执行环境应为 float32；本模块不改原模型代码来支持全局 float64 推理。
