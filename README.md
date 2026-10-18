# 卫戍协议 Agent

完整玩家接口、统一策略测评、原游戏页面回放。固定复刻规则 `6ab2468`（盟约 `act2autochess`）；这不代表官方跨赛季通关能力。代码及问题跟踪：[LittleDrinks/arknights](https://github.com/LittleDrinks/arknights)，本次重构见 [#1](https://github.com/LittleDrinks/arknights/issues/1)。

```text
arknights/
  adapter/       玩家能看什么、能做什么、能查什么
  evaluate/      创建对局、运行策略、记录轨迹、统一结果
  replay/        核对保存的操作、录制战斗、原页面展示
  collect/       真人联机采集、片段标注和示范集导出
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

- `observe()`：返回 `phase/round/terminal/can_act/own/public/actions`、接口版本、固定规则和赛季。`own.deployment` 提供当前部署场地的公开地面/高台坐标，跟随地形变化。`actions` 是参数域和约束说明，并非承诺所有参数组合合法的扁平候选表。
- `act(action)`：例如 `{'type':'move','uid':42,'to':{'area':'board','row':9,'col':3},'dir':'RIGHT'}`。返回 `accepted/result/state`。原生协议字段名保持一致，也接受完整 `t`；可附 `revision` 拒绝过期动作。游戏最终校验，拒绝也原样记录。
- `wait()`：显式推进虚拟时间。开局、策略、机变和休整都可以由策略自己决策；作战分段推进，保留观察/暂停能力。等待可能使游戏原生倒计时到期，相应自动行为会被记录。
- `lookup({'kind':'bonds','id':'lateranoShip'})`：返回带赛季和来源的公开说明，支持 `chess/items/bonds/bands/garrisons/bosses` 与文本查询。干员条目包含 `bonds/garrisonIds` 标识列表，可继续查询对应盟约与特质。默认不返回黑板、隐藏数值表或未来随机结果。查资料和 observe 不推进游戏时间。

购物、冻结、升级、售出、部署/换位/朝向、装备/替换、道具、奖励、选择、准备与游戏允许的观察控制均可调用。`confirm` 对应本局情报确认；`band` 选择策略。受控席是真实玩家席，不会自动帮你完成开局。局前持有/自选配置通过 `Session` 的初始参数提供，技能/模组配置遵守上游允许的阶段。客户端战斗结果上报不提供给策略。

策略是受信任的研究代码，子进程用于故障隔离及资源收尾，不是安全沙箱。框架隐藏命令、进程和文件管理；训练算法可直接使用 `from arknights.adapter import Session` 创建对局。当前独立策略及 RL 实验放在 `experiments/`；原有训练实现保存在 `arknights/evaluate/_legacy/`。

## 看结果和回放

`ark preview` 后打开 **http://127.0.0.1:38181/evaluations**。面板自动发现正在运行的测评，显示进度、逐局胜负和盟约层数；评测结束后后台自动准备原游戏回放，准备好即可播放。服务器使用 VS Code 转发 **38181**。已有测评也会补回放，按局串行生成；准备状态与测评结果分开。

需要排队运行时执行 `ark evaluate experiments/sol/agent.py --seeds 1:8 --queue --label sol-dev`，也可以在面板提交。普通 `ark evaluate` 直接执行，并自动出现在面板中。队列按提交顺序运行，代码在排队期间改变会拒绝执行，避免错用策略版本。

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

## 真人示范采集（Collect V1）

```sh
ark collect serve --port 38181
# 打开 /collect，创建批次，向玩家分享本页邀请链接。
ark collect export <collection-id> --profile human-reviewed
```

每位玩家在邀请页明确同意记录后进入原游戏，再正常建房、加入同一房间并开局。支持 1–4 位玩家与原版 AI 席位；所有真人必须在开局前加入同一批次。采集局使用原版服务器战斗和 RealScheduler。gameplay/script/modded 房间明确拒绝。普通游戏和评测回放继续使用原有入口。

组织者的访问密钥只在创建响应中返回，由本浏览器保存；服务器只保存其哈希。邀请链接不携带组织者密钥。组织者可查看全部已同意席位，参与者只可查看和标注本席；保留原参与标签页以保留访问权限。本席回看在同一标签页打开。清除浏览器存储会丢失相应访问密钥。此功能不提供账户找回。

在批次详情选择席位查看操作、回合与阶段，选择事件区间，填写核心/附加盟约、阶段、质量及可选说明，点击保存。修改会追加修订，原始记录保持不变。赛后“原生页面回看”显示该席实际观察与保存的战斗帧；来源固定为 `recorded-events`，不重执行命令，不宣称虚拟时钟 exact replay。回执未确认、录制故障和未结算局保留 unknown/technical-failure/partial 标签，不认证为完整胜局。团队胜负与该席胜负、存活、淘汰回合和 boss 贡献分别导出。

数据位于 ignored `results/collections/`：逐席玩家日志与服务器核验日志分开保存，战斗录像、追加标注和按内容版本生成的 datasets 各自存放。导出返回 DatasetRef，包含样本/源日志哈希、规则、采集/投影版本和质量报告；日志有顺序哈希链，已有数据集遭改写会拒绝复用。`human-reviewed` 只导出已审阅为精彩/可用的已确认人工操作，`all-human` 用于检查全部已确认人工操作。原始文件只供本机可信组织者核验，不是训练输入。

点击/请求调用时即冻结 `decisionSeq`，离线队列发送时的 `flushSeq` 只供诊断。只有原客户端探针已处理的回执才作为已确认；断线丢失的回执保持 unknown。训练导出仅重建点击前本席已见的 public/own-private 投影，移除玩家显示名及凭证、IP、seed、隐藏 spec/数值。战斗录像 HTTP 接口不参与训练导出。

这是 **collect 专用投影**，不是完整 adapter 兼容数据集：保留人工移动/装备的 UID、位置、朝向及替换目标，以及策略、奖励、购买等参数；单位详情、侦察字段、地图几何、adapter descriptors/shortlist 覆盖尚未完整对齐。质量报告逐动作统计保留量，候选覆盖未测时明确为 unknown，不静默丢弃复杂动作后宣称覆盖完整。训练不属于 V1。后续划分训练/验证集须按房间整局及参与者分组，不能按帧或同局不同席拆分。

Python 的最小接口为 `arknights.collect.serve({"port": 38181}) -> CaptureHandle` 和 `arknights.collect.export(id, profile="human-reviewed") -> DatasetRef`。`handle.close()` 只关闭本句柄新启动的服务；复用的活动主机不会被关闭。`serve` 会核对 `/healthz` 的 collect capability，旧版本或其他占用者会明确报错，不能热挂录制、抢端口或重启活动对局。升级现有 38181 需等活动房间结束，由部署者安排。

实测命令（URL 必须指向独立测试主机）：

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
npm test
ARK_COLLECT_URL=http://127.0.0.1:48191 node --test tests/collect-protocol.test.mjs
ARK_COLLECT_URL=http://127.0.0.1:48191 ARK_CHROME=/path/to/chrome node --test tests/collect-browser.test.mjs
```

浏览器测试使用四个独立 context，经原游戏 UI/Net 自动操作并自然结算，覆盖录像、重连、实际页面刷新/rid 复用、权限、标注与导出；这是协议/产品证据，不是专家真人示范或训练收益证据。报告和截图保存到 ignored `results/tests/collect-browser/`。
