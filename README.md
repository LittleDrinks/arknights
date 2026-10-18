# Arknights 卫戍协议 Agent

固定开源游戏规则下的策略、训练、评测和回放。**训练目标暂停中**；目录整理不恢复训练。当前尚无未见种子通关率提升，详见 `docs/research/PAUSE-HANDOFF-2026-10-18.md`。

## 目录

| 目录 | 内容 |
|---|---|
| `game/` | 上游 submodule，锁定 `6ab2468279e926d107788f63d7fe219b85f67b4a`；保持干净，不放补丁、依赖或结果 |
| `adapters/` | 游戏接口、合作伙伴、控制器、特征、奖励和网页外部覆盖层 |
| `strategies/` | `heuristic/`、`stacking/`、`builds/`、`teacher/`、`bc/`、`ppo/`、`cem/`、`dagger/`、`interaction/`、`tensor-bc/` |
| `studies/` | 按 data/training/features/controllers 分类的消融；历史启动器与未验收代码单列 |
| `evaluation/` | 统计分析、构造战斗审计、宽开发评测 |
| `replay/` | 模型、合作训练及冻结策略的离线回放导出 |
| `configs/` | 固定版本、目录映射、训练及实验配置 |
| `scripts/` | 统一命令、布局核查和一次性迁移工具 |
| `docs/` | 调研、攻略、文献和实验交接说明 |
| `results/` | 所有运行结果、benchmark、测试报告、日志、轨迹、回放、历史源码快照；只提交 `.gitkeep` |
| `runtime/` | Python环境、Node依赖实体、下载素材、缓存、临时文件；忽略提交 |

## 常用命令

```bash
git submodule update --init game
npm ci --ignore-scripts
npm run vendor                         # 仅写runtime/web/vendor
python3 scripts/ark.py check
python3 scripts/check_layout.py
python3 scripts/ark.py prepare            # 仅整理导入工作目录，不跑训练/对局
bash scripts/start-preview.sh              # 保持38181，供VS Code转发
```

已部署服务器的Python环境：`runtime/venvs/learner-20261018/bin/python`。Node依赖实体保存在 `runtime/node_modules/`，根目录的忽略链接 `node_modules` 用于标准Node包解析。全新安装也可直接用根目录 `npm ci --ignore-scripts` 创建忽略的 `node_modules/`。不要在game里运行安装或vendor生成。

原页面回放：`/?replay=frozen-bc-eval-s65-d1fdb2b3ad67`，选择AI3、R14、本回合作战。网页适配文件在 `adapters/web/overlay/`，录像在 `results/replays/public/dev/`，素材在 `runtime/web/`；服务从外部合并请求并转发到干净的上游服务器，支持WebSocket。

## 策略入口与历史兼容

历史模块用相对路径互相导入，并有完整文件SHA锁；直接改这些源码的导入语句，会使过去的规则/源码证明失效。因此本次只移动源文件，**251个源/配置/说明文件逐字节保持**，不把旧策略重写成另一算法。

`configs/layout.json` 记录新源码位置与原虚拟路径。统一入口在 `results/workspaces/<项目版本>-<映射哈希>/` 生成隔离运行目录，以Git提交记录实际内容；旧固定import和SHA守卫正常工作。153个旧生成文件从当前Git工作树移除，必要的历史fixture从原Git提交恢复到忽略的运行目录。不要直接运行策略子目录中的旧相对路径脚本。

```bash
python3 scripts/ark.py learner -- --help
python3 scripts/ark.py ppo -- --help
python3 scripts/ark.py analyze -- --help
python3 scripts/ark.py replay -- --help
# 只验证接口，不启动训练；环境从stdin接受JSONL：
python3 scripts/ark.py env -- --coop
```

`learner/ppo/replay/analyze` 的 `--out/--out-dir/--trace-dir` 必须位于 `results/`。已有SHA锁定的研究入口需要在生成目录内按原契约显式调用；历史launchers保留取证用途，含旧绝对路径，不作为当前启动入口。

修改既有策略时，应显式更新layout身份和该模块的版本/源锁，生成新运行目录；不覆盖历史快照。新增策略直接放在 `strategies/<策略名>/`，公共游戏接入放在adapters，输出全部指向results。

## 数据与迁移

迁移没有删除原始数据。`results/migrations/layout-20261018/`保存移动日志、逐文件旧新路径表和inode/大小/mtime身份核验；只有虚拟环境的嵌入路径脚本在保存原件后被改写。旧实验报告和模型metadata不重写。

- 旧 `experiments/` → `results/runs/`
- 旧根目录benchmark → `results/benchmarks/legacy-root/`
- 旧 `logs/`、`trajectories/` → `results/logs/`、`results/trajectories/`
- 旧 `code-*` → `results/archive/snapshots/`
- 旧服务端散装代码及修改过的game → `results/archive/source/`
- 原始攻略/文献数据 → `results/datasets/`；精简阅读文档 → `docs/`

```bash
python3 scripts/resolve_legacy.py /data1/zsm/arknights/experiments/wide-dev-v1/summary.json
```

`studies/pending/scale-bc/`为已保存、尚未独立审查的实现，不表示数据已验收或新训练已完成。停止时的新教师数据仍是未冻结状态，不能因为目录整理而变成可训练数据。
