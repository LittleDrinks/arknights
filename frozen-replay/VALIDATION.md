# 验证记录

base `4b6069b1e2000f6e4b62195c488a3e78c285dd2c`，仅 `coop-replay/` 向后兼容扩展与
新 `frozen-replay/`。未修改旧测试及其他模块。日期 2026-10-18。

命令：`python3 -B frozen-replay/check.py`。

- 旧 4 项 + 新 5 项合同测试，9 pass / 0 fail / 0 skipped。
- 首次通过：1.519s，峰值进程树 RSS 321,458,176 bytes，采样临时产物峰值 416,993 bytes。
  watchdog 总预算 20s、停工阈值 19s、RSS 1 GiB、临时产物 5 MB。
  最终代码复核仍为 9/9：1.356s，峰值 RSS 325,005,312 bytes，临时产物峰值 416,993 bytes。
  `git diff --check` 通过；rl/coop/prep-timing/model-replay 和旧合同测试相对 base 无差异。
- 真 trace 416,577 bytes / 80 actions，SHA
  `d1fdb2b3ad671f0e342cc0229fd52c1500ee0a546e2574463d71da973d3265c7`；
  receipt 23,233 bytes，SHA `6b01ecc49b507b3b320118c454b644cc46059a936bcfad70ab3ac80a7d1c9922`。
  只静态读取并校验，没有重放其中任何游戏动作。
- 固定 profile、checkpoint、伙伴配置和三席身份；错误 kind/controller/SHA、个人胜利误标、
  summary/terminal/partner SHA/候选参数漂移均拒绝；receipt 篡改、错误关联或失败比较拒绝。
- 使用生产 workerLaunch 的原 loader 参数执行既有 deferred/off 合成测试，检查真实继承
  controller、exact URL/source SHA、ready 顺序、temp fallback、forced 分支等。
  只使用假 player/bot/scheduler，没有真实 Match/Battle 或 create/reset。
- 新 CLI 缺显式 SHA、错误 kind/hash、record prefix、错误 heap/RSS 均在模拟前失败；
  旧 CLI 的路径/symlink/hash 拒绝测试继续通过。
- 合成 64 场：54 个席位场 + 10 个联防场；原页面函数遍历所有 episode 入口和四席，
  64/64 战场可达，学习席最后 R12，AI2/3/4 可选 R14，seek 和真实个人失败/团队胜利
  结算数据可消费。下载 terminal 与输入完整一致，metadata 无 training/PPO 残留。
  所谓“完整一致”此处是合成包结构测试，不是游戏推演一致的证明。

未执行：server 部署、真实 80 步 full terminal verification、真实全场录像录制、
native 浏览器/视觉/音效验收、checkpoint 推理、训练、2048 评估、SSH 或发布。
main receipt 的 Python 原 ActorCritic 复现为外部来源证据；本 Node exporter 的
`inferenceRerun` 始终 false。没有对整体泛化提高作出结论。

保持 native 38181，不写 live indexes。main 应按 README 在隔离代码/输出目录中先 verify，
再 record；仅 full terminal exact 才能生成真实播放包。本提交不包含伪造成功 bundle。
