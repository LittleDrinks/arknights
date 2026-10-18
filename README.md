# 卫戍协议 Agent

完整玩家接口、统一策略测评、原游戏页面回放。固定复刻规则 `6ab2468`（盟约 `act2autochess`）；这不代表官方跨赛季通关能力。代码及问题跟踪：[LittleDrinks/arknights](https://github.com/LittleDrinks/arknights)，本次重构见 [#1](https://github.com/LittleDrinks/arknights/issues/1)。

```text
arknights/
  adapter/       玩家能看什么、能做什么、能查什么
  evaluate/      创建对局、运行策略、记录轨迹、统一结果
  replay/        核对保存的操作、录制战斗、原页面展示
game/            固定且干净的上游 submodule
experiments/     策略和实验入口
tests/          公开接口及故障回归
results/         对局、日志、模型、资料与回放；忽略提交
runtime/         依赖、环境、素材和缓存；忽略提交
```

## 开始使用

需要 Python 3.10+、Node 22+。新测评框架和示例启发式不需要 PyTorch。

```bash
git clone --recurse-submodules https://github.com/LittleDrinks/arknights.git
cd arknights
npm ci --ignore-scripts
python3 -m pip install -e .
ark check
ark vendor                 # 为网页生成外部 runtime/web/vendor，不改 game
ark evaluate experiments/heuristic.py --suite smoke --plan
ark evaluate experiments/heuristic.py --suite smoke
```

也可以在仓库根目录用 `python3 -m arknights` 代替 `ark`。示例是简单的接口基线，不是强策略；真实败局也是正常完成，不能当作框架故障。

`smoke` 是 seed17、原版伙伴、合作终极模拟的一局；`abyss-dev` 是开发 seed1–16 × 原版/混合伙伴，共32局。`--seeds 1:4` 是包含两端的范围；支持 `--mode solo`、`--partners original mixed weak`、`--workers`、`--timeout`。100001–100064继续封存。每次执行创建新 run，不覆盖或冒称精确续跑。

命令返回 `results/runs/<run-id>` 和汇总。失败/超时计为技术失败，正常败局单列；团队和学习席个人结果分开。全部计划项保留在分母中。同一个种子的不同伙伴配置不自动视为独立随机样本。

## 写一个策略

唯一入口是同步 `play(game)`；使用字典观测和参数化动作：

```python
def play(game):
    state = game.observe()
    while not state['terminal']:
        if not state['can_act']:
            state = game.wait()
            continue
        action = choose_action(state)  # 你的启发式或模型
        reply = game.act(action)
        state = reply['state']
```

完整示例见 [experiments/heuristic.py](experiments/heuristic.py)。它读取开局信息、查说明、选策略、购买、布阵、装备、领奖并准备；不使用自动布阵或装备控制器。

- `observe()`：返回 `phase/round/terminal/can_act/own/public/actions`、接口版本、固定规则和赛季。`actions` 是参数域和约束说明，并非承诺所有参数组合合法的扁平候选表。
- `act(action)`：例如 `{'type':'move','uid':42,'to':{'area':'board','row':9,'col':3},'dir':'RIGHT'}`。返回 `accepted/result/state`。原生协议字段名保持一致，也接受完整 `t`；可附 `revision` 拒绝过期动作。游戏最终校验，拒绝也原样记录。
- `wait()`：显式推进虚拟时间。开局、策略、机变和休整都可以由策略自己决策；作战分段推进，保留观察/暂停能力。等待可能使游戏原生倒计时到期，相应自动行为会被记录。
- `lookup({'kind':'bonds','id':'lateranoShip'})`：返回带赛季和来源的公开说明，支持 `chess/items/bonds/bands/garrisons/bosses` 与文本查询。默认不返回黑板、隐藏数值表或未来随机结果。查资料和 observe 不推进游戏时间。

购物、冻结、升级、售出、部署/换位/朝向、装备/替换、道具、奖励、选择、准备与游戏允许的观察控制均可调用。`confirm` 对应本局情报确认；`band` 选择策略。受控席是真实玩家席，不会自动帮你完成开局。局前持有/自选配置通过 `Session` 的初始参数提供，技能/模组配置遵守上游允许的阶段。客户端战斗结果上报不提供给策略。

策略是受信任的研究代码，子进程用于故障隔离及资源收尾，不是安全沙箱。框架隐藏命令、进程和文件管理；训练算法可直接使用 `from arknights.adapter import Session` 创建对局。原有训练实现保存在 `arknights/evaluate/_legacy/`，尚未改造成新的训练入口，正式训练保持暂停。

## 看结果和回放

每局保留 requests、原始协议、stderr、终局和结果；运行保存完整配置、代码/规则身份、逐局状态、汇总和文件校验。`summary.json` 中的 `games[].id` 是 episode ID。

```bash
ark replay <run-id> --episode <episode-id>
ark preview
```

回放会核对原产物，重新执行保存的操作并逐响应比较，再录制真实战斗、追加到回放索引，返回 `http://127.0.0.1:38181/?replay=...`。这是保存操作的重放，不是重新运行模型推理。端口38181在服务器保持运行，使用VS Code端口转发。首次部署需要网页依赖和角色素材；服务器已有素材留在 `runtime/web`。旧45个入口/169场录像保留，新录像追加发布。

资料和规则分别固定版本；当前仅实现这个复刻赛季，不宣称任意赛季可直接运行，也不宣称镜像数值和官方逻辑完全一致。

## 历史实验与检查

历史实现及说明集中在 `arknights/evaluate/_legacy/`，VS Code默认隐藏。源代码按原SHA保留，旧数据仍在results；不复制旧目录到根目录。服务器上可用以下兼容入口生成历史运行目录（依赖保留的本地Git历史）：

```bash
python3 -m arknights legacy prepare
python3 -m arknights legacy ppo -- --help
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
```

公开代码不包含服务器的数据、模型、素材或依赖；新clone以当前测评和回放入口为准。历史重现实例需要相应历史源码、结果和环境。游戏更新时更换adapter实现及契约测试；不得原地修改冻结game再将结果当成旧版本复现。
