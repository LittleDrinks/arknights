# 学习模型 eval → 原游戏页面离线重放

只读取现有 learner eval trace；通过当前 worktree 的原 `ShopEnv.reset/step` 重新执行其中的模型商店动作。没有重新跑原 bot 的商店策略，也没有把 observation/snapshot 恢复为模拟器状态。不加载 Torch、不安装依赖、不连接服务器、不改游戏页面或任何现有索引。

## CLI

从 `experiment-replay` 执行，要求本机 Node（验证版本 v24.21.0）、Linux `/proc`，游戏依赖已经可用。`--out` 必填，必须是系统临时目录下**尚不存在**的目录，父目录必须存在；已存在的空目录也拒绝。

```bash
node model-replay/cli.mjs \
  --trace /tmp/rl-independent-review-zf2m0il0/eval/ABYSS-1.json \
  --checkpoint /tmp/rl-independent-review-zf2m0il0/cli-ppo.pt \
  --out /tmp/model-replay-my-new-export
```

可选 `--game /path/to/Stronghold-Protocol`，默认使用相邻 `../Stronghold-Protocol`；可选 `--checkpoint-sha256 <hex>` 校验提供的 checkpoint 文件。输出 stdout 为一条 JSON 验证报告，失败退出非零。每次 CLI 最长 25 秒（含加载和导出），worker heap 上限 512 MiB、RSS 上限 768 MiB。输出默认上限 20,000,000 字节；完整14轮录像可能超过，可显式用 `--max-output-mb 128`（整数1–256），实际预算写入 verification。输入 trace 上限仍为20 MB。服务器可设置 `TMPDIR=/data1/zsm/arknights/tmp` 将新产物限制在项目目录。父进程每 20 ms 检查 RSS，并强制终止超时 worker；记录器也在真实方法和战斗 tick 边界检查。RSS 监测是采样，并非操作系统 cgroup 硬上限。

生成目录：

```text
<out>/
  verification.json
  agent/index.json
  agent/episodes/<id>.json
  agent/downloads/<id>.jsonl.gz
  recordings/index.json
  recordings/<battle-name>.json
```

完整终局、候选和格式验证全部通过之后才创建输出目录，写文件使用 `wx`，`agent/index.json` 最后发布。磁盘故障/强制终止可能留下不完整的新目录，应保留供排查并换新目录重试；CLI 不清理或覆盖任何已有目录。

## 验证边界与来源

- 支持 `learner.py evaluate(..., trace_path)` 当前实际格式：`rules/controller/budget/result/actions`。每步比较 round、candidateIndex 对应的**整个候选对象**，包括 UID、slot、id、price、tier、golden、bonds，再调用原 `env.step(index)`。不通过 type/UID 搜索另一个候选代替错配。
- 每步比较规则 SHA、控制器标识、seed/mode/difficulty/budget；要求引擎、动作、控制器错误为零。末尾必须完整终止，逐字段比较 trace 保存的胜负、通过轮数、动作数、错误数、预算强制 ready 数、控制器阻止卖出数；多余/缺失动作都失败。原 trace 没有完整 summary，因此新增捕获的 summary 不能声称与原 summary 做过逐字段比较。
- 保存 trace 原始文件 SHA-256、checkpoint 文件 SHA-256、环境/控制器/features/learner 源文件 hash、规则 SHA、预算及 Node 版本。checkpoint hash 是**整个文件**的 hash，不是训练脚本的参数张量 hash。
- 原 trace 没有 checkpoint 标识。`--checkpoint` 是调用者提供的来源声明，适配器不会推断或伪称已证明该文件产生了这些动作；`provenance.checkpoint.binding` 明确保留这个限制，也不重新运行模型推理。独立提供的 `--checkpoint-sha256` 仅验证文件身份。
- 没有胜例时明确标注“失败重放，不是通关录像”。当前 smoke 是失败，不构成模型能力或泛化证据。

## 捕捉与原页面格式

依赖注入 `ObservedMatch`，在 `super(opts)` 完成后、ShopEnv 的 `m.start()` 之前安装钩子与 `BattleClass`。因此首次 reset 的 BAND_DRAFT 也被记录。原环境之后安装的历史 recorder 继续正常执行。

捕捉实际调用的 buy/sell/refresh/levelUp/move/equip/useArt/destroy/pickReward/freeze/setReady/resolveTemp，以及 `_applyBand/_applyCard/pickPersonalChoice`，保存调用前后原始 observation、返回值、成功状态、source、嵌套 parentSeq。方法 accessor 保留原环境的包装和控制器临时 `sell` 禁售替换，记录拒绝调用；同一方法的多层包装不会重复计数。控制器仍由原 env 调用，没有复制它的决策逻辑。

普通引擎 `resolveTemp` 与强制预算 `resolveTemp` 分开标记：前者保留于 JSONL 和 episode.engineCalls；后者作为预算动作进入 steps。页面的 steps 保留所有策略/控制器调用，避免把异步的引擎清理当作 ready 之后的新决策。seq 保留原始全局调用序号，允许有空隙。最后一次决策的 observation 按原 exporter 约定设为真实终局，原方法执行后的 observation 仍保留在 methodObservation，执行前完整视图在 beforeObservation。

真实 `RecordedBattle extends Battle`：启用 recordEvents，观察 `_ev`（包括 constructor/start 的事件），t=0、每 3 ticks、结束时捕捉原 `fieldMeta/snapshot`，结束记录原 `result/duration`。不消费引擎自己的事件队列，不替换模拟逻辑；显式 preview 和 `r:` rehearsal 不录，真实零 spawns 战斗不会被丢弃。这些快照仅供展示，绝不用作状态 clone。

格式以 `../preview-tools/export-trajectories.mjs` 与 `../Stronghold-Protocol/public/js/agentReplay.js` 为依据：episode `{meta,steps}`，manifest `episodes[].file`，recordings 数组索引，录像 `{version,name,title,kind,stageId,modeId,round,tick,field,frames,duration,result}`，帧 `{snap,ev}`。固定 viewer 座位是 `ai_0`；只在导出副本中将 `learner`、对应 fieldId 和 perPlayer/dps key 映射为 `ai_0`，引擎和动作 UID 不变。JSONL 保留原引擎 ID。

## 交给主线程合入

本工具不发布页面。由主线程将 `agent/episodes`、`agent/downloads` 对应文件放入游戏 `public/dev/agent/`，将录像 JSON 放入 `public/dev/recordings/`。已有两个 index **不能用导出 index 覆盖**；可调用纯函数生成新索引：

```js
import { mergeIndexes } from './model-replay/index.mjs';
const { agent, recordings } = mergeIndexes(
  existingAgentIndex, existingRecordingIndex,
  exportedAgentIndex, exportedRecordingIndex,
);
```

函数不写文件、不修改传入对象，拒绝相同 ID/name、相同 file、危险路径、schema 不同及缺失战斗引用，并重算总条目体积/步数。主线程需在自己持有的写锁或 CAS 下发布两个索引及对应新文件；此函数不承担跨进程事务。合入后访问原页面 `/?replay=<verification.json 的 id>`，必须带此 ID，因为页面默认仍选择原 bot 示例。

## 测试

```bash
REPLAY_TRACE=/tmp/rl-independent-review-zf2m0il0/eval/ABYSS-1.json \
REPLAY_CHECKPOINT=/tmp/rl-independent-review-zf2m0il0/cli-ppo.pt \
node --test model-replay/replay.test.mjs
```

未提供这两个环境变量时，仅跑 4 个 contract 测试，真实重放测试明确 skip。真实测试创建新临时目录并保留，输出路径；逐文件验证 hash、检查 JSONL，并在 Node VM 内执行**原 agentReplay.js 的函数**（仅替换 DOM/network/store 依赖），验证 URL/座位筛选、动作、战斗播放、拖动恢复和最终结果。不是浏览器截图或视觉验收。
