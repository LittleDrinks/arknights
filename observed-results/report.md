# botbench 实验分析报告

- 生成时间：2026-10-17T17:05:12+00:00（UTC）
- 工具：`analyze.py` v1.0.0（sha256 `2b3d382799db…`）
- 引擎提交（manifest）：`6ab2468279e926d107788f63d7fe219b85f67b4a`；trained_model：False
- **唯一主标准：完整对局 `victory`（ABYSS 终极模拟通关）**；rounds/层数/道中存活/hidden 均为描述性指标。

## 输入

| 角色 | 标签 | 路径 | 记录数 | sha256（前 16） |
|---|---|---|---:|---|
| baseline | r0 | `../experiments/planner-ablation-2026-10-18/r0.json` | 32 | `7b62b7cd3bc0c7a9` |
| candidate | r1 | `../experiments/planner-ablation-2026-10-18/r1.json` | 32 | `dd477f9c33791b83` |
| candidate | r3 | `../experiments/planner-ablation-2026-10-18/r3.json` | 32 | `1e39e0cf36a21dc9` |

## 数据质量（全部问题，未静默处理）

未发现问题：无重复 (config, seed)、无坏记录；两臂 seed/stage/boss/errors 配对前均逐条核验通过。

## 按 arm × config 汇总（主标准 = victory）

| arm | config | 局数 | 胜场 | 胜率 | Wilson 95% CI | hidden 达成 | 平均失败轮（败局） | 平均通过轮 | 决战到达（描述） | 错误 | 平均整局墙时 ms |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| r0 | coop4:ABYSS | 16 | 0 | 0.0% | [0.0%, 19.4%] | 0（胜 0；未知 0） | 12.06 | 11.06 | 3 | 0（0 局；未知 0 局） | 5886 |
| r0 | solo:ABYSS | 16 | 0 | 0.0% | [0.0%, 19.4%] | 0（胜 0；未知 0） | 12.38 | 11.38 | 6 | 0（0 局；未知 0 局） | 1929 |
| r0 | **全部 config 合并** | 32 | 0 | 0.0% | [0.0%, 10.7%] | 0（胜 0） | 12.22 | 11.22 | 9 | 0（0 局） | 3908 |
| r1 | coop4:ABYSS | 16 | 0 | 0.0% | [0.0%, 19.4%] | 0（胜 0；未知 0） | 12.00 | 11.00 | 5 | 0（0 局；未知 0 局） | 10288 |
| r1 | solo:ABYSS | 16 | 0 | 0.0% | [0.0%, 19.4%] | 0（胜 0；未知 0） | 12.25 | 11.25 | 6 | 0（0 局；未知 0 局） | 3117 |
| r1 | **全部 config 合并** | 32 | 0 | 0.0% | [0.0%, 10.7%] | 0（胜 0） | 12.12 | 11.12 | 11 | 0（0 局） | 6702 |
| r3 | coop4:ABYSS | 16 | 0 | 0.0% | [0.0%, 19.4%] | 0（胜 0；未知 0） | 12.56 | 11.56 | 7 | 0（0 局；未知 0 局） | 19426 |
| r3 | solo:ABYSS | 16 | 0 | 0.0% | [0.0%, 19.4%] | 0（胜 0；未知 0） | 13.00 | 12.00 | 10 | 0（0 局；未知 0 局） | 5328 |
| r3 | **全部 config 合并** | 32 | 0 | 0.0% | [0.0%, 10.7%] | 0（胜 0） | 12.78 | 11.78 | 17 | 0（0 局） | 12377 |

注：“平均失败轮”仅按败局计算 = roundsPassed + 1（第 k 轮被淘汰 ⇒ roundsPassed=k−1；决战失败 ⇒ 失败轮 = bossRound）。“决战到达”= roundsPassed ≥ bossRound−1，**不是胜场**。hidden 达成而未 victory 仍计为败局。

> ⚠️ 墙时警示：本批数据 jobs=4（多 worker 并行）：墙时受并行负载影响，可能膨胀，只能相对比较同批、同负载的运行；跨批绝对耗时不可比，决策耗时测量须 jobs=1。

## 配对比较（baseline vs candidate，按 config/seed）

### baseline `r0` vs candidate `r1`

#### config `coop4:ABYSS`

- 配对样本：**16**（baseline 16 局 / candidate 16 局）
- 剔除：无
- 结局矩阵：双胜 0，**loss→win 0**，**win→loss 0**，双败 16
- 配对胜率：baseline 0/16（0.0%，CI [0.0%, 19.4%]）vs candidate 0/16（0.0%，CI [0.0%, 19.4%]）；**差值（candidate−baseline）= 0.0%**
- 精确 McNemar（不一致对二项）：b=0，c=0，m=0，双侧 p = undefined (m=0)
- loss→win：**无**（没有原先失败的 seed 转胜）
- win→loss：无
- **结论（uninformative_zero_wins）**：主标准（完整对局 victory）两臂全为 0 胜：本比较无信息量，属于“没有成功证据”，不是“两臂等效”——禁止把 0/0 说成无差异或等效；任何提升结论至少需要 1 个 victory。roundsPassed/存活/层数是描述性指标，不得当作通关。

#### config `solo:ABYSS`

- 配对样本：**16**（baseline 16 局 / candidate 16 局）
- 剔除：无
- 结局矩阵：双胜 0，**loss→win 0**，**win→loss 0**，双败 16
- 配对胜率：baseline 0/16（0.0%，CI [0.0%, 19.4%]）vs candidate 0/16（0.0%，CI [0.0%, 19.4%]）；**差值（candidate−baseline）= 0.0%**
- 精确 McNemar（不一致对二项）：b=0，c=0，m=0，双侧 p = undefined (m=0)
- loss→win：**无**（没有原先失败的 seed 转胜）
- win→loss：无
- **结论（uninformative_zero_wins）**：主标准（完整对局 victory）两臂全为 0 胜：本比较无信息量，属于“没有成功证据”，不是“两臂等效”——禁止把 0/0 说成无差异或等效；任何提升结论至少需要 1 个 victory。roundsPassed/存活/层数是描述性指标，不得当作通关。

#### 全部 config 合并（pooled，仅作参考）

- 配对 32：双胜 0，loss→win 0，win→loss 0，双败 32；差值 = 0.0%；合并不做检验
- **结论（descriptive_only）**：跨 config 共享 seed，合并仅描述计数和差值，不做独立配对推断；全零胜不证明等效。

### baseline `r0` vs candidate `r3`

#### config `coop4:ABYSS`

- 配对样本：**16**（baseline 16 局 / candidate 16 局）
- 剔除：无
- 结局矩阵：双胜 0，**loss→win 0**，**win→loss 0**，双败 16
- 配对胜率：baseline 0/16（0.0%，CI [0.0%, 19.4%]）vs candidate 0/16（0.0%，CI [0.0%, 19.4%]）；**差值（candidate−baseline）= 0.0%**
- 精确 McNemar（不一致对二项）：b=0，c=0，m=0，双侧 p = undefined (m=0)
- loss→win：**无**（没有原先失败的 seed 转胜）
- win→loss：无
- **结论（uninformative_zero_wins）**：主标准（完整对局 victory）两臂全为 0 胜：本比较无信息量，属于“没有成功证据”，不是“两臂等效”——禁止把 0/0 说成无差异或等效；任何提升结论至少需要 1 个 victory。roundsPassed/存活/层数是描述性指标，不得当作通关。

#### config `solo:ABYSS`

- 配对样本：**16**（baseline 16 局 / candidate 16 局）
- 剔除：无
- 结局矩阵：双胜 0，**loss→win 0**，**win→loss 0**，双败 16
- 配对胜率：baseline 0/16（0.0%，CI [0.0%, 19.4%]）vs candidate 0/16（0.0%，CI [0.0%, 19.4%]）；**差值（candidate−baseline）= 0.0%**
- 精确 McNemar（不一致对二项）：b=0，c=0，m=0，双侧 p = undefined (m=0)
- loss→win：**无**（没有原先失败的 seed 转胜）
- win→loss：无
- **结论（uninformative_zero_wins）**：主标准（完整对局 victory）两臂全为 0 胜：本比较无信息量，属于“没有成功证据”，不是“两臂等效”——禁止把 0/0 说成无差异或等效；任何提升结论至少需要 1 个 victory。roundsPassed/存活/层数是描述性指标，不得当作通关。

#### 全部 config 合并（pooled，仅作参考）

- 配对 32：双胜 0，loss→win 0，win→loss 0，双败 32；差值 = 0.0%；合并不做检验
- **结论（descriptive_only）**：跨 config 共享 seed，合并仅描述计数和差值，不做独立配对推断；全零胜不证明等效。

## 注意事项与诚实性声明

- Wilson 区间按 config 的独立 Bernoulli seed 假设计算；同 seed 跨 config/arm 不独立，合并 CI 仅名义参考，不代表泛化保证。
- 逐 config 精确检验的 p 未做多重比较校正；显著性仅为探索性。配对胜率差不报告条件化伪边际 CI。
- hidden 是上游 hiddenReached 标志，不是 hiddenCleared；未知标志不按 false 填补。
- 平均失败轮是上游 roundsPassed+1 的推算；隐藏终局失败会被上游截到 bossRound，不能据此恢复真实隐藏终局轮次。
- arm 汇总保留有错误/重复的有效结果行，配对严格剔除；须结合质量报告，不能把这些行当作独立有效试验。
- 未声明独立 eval seeds 与队友留出集时，不声称未见 seed 或合作队友泛化。
- 唯一主标准是完整对局 `victory`（ABYSS 终极模拟通关）；roundsPassed、层数、道中存活、hidden 达成只是描述性指标，不得在结论中冒充通关。
- 配对剔除规则：缺失/重复 seed、两臂 stage/boss 不一致、errors>0 或 errors 未知的对局绝不进入配对，全部显式列出。
- 两臂全 0 胜 ⇒ “比较无信息量”，不是“等效”；0/0 不可写成无差异。
- McNemar 精确检验只由不一致对决定；m=0 时 p 无定义，报告中写 undefined 而非 1。
- 墙时：本批数据 jobs=4（多 worker 并行）：墙时受并行负载影响，可能膨胀，只能相对比较同批、同负载的运行；跨批绝对耗时不可比，决策耗时测量须 jobs=1。
- manifest 标注 trained_model=false：所有 arm 均为未训练的规则 bot / 引擎参数消融，不存在训练成绩，不得解读为学习策略效果。

## 复现

```bash
/usr/bin/python3 analyze.py --baseline ../experiments/planner-ablation-2026-10-18/r0.json --candidate r1=../experiments/planner-ablation-2026-10-18/r1.json --candidate r3=../experiments/planner-ablation-2026-10-18/r3.json --manifest ../experiments/planner-ablation-2026-10-18/run-manifest.json --out-dir observed-results
```

同一命令、工具和 manifest 下，报告除 `generated_at` 外可复算；输入附 sha256；`report.json` 使用排序键、固定精度策略，重跑可直接 diff。
