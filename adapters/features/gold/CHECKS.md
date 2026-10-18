# 有限本地验证记录

2026-10-18，base `d8805de9ccc1d4d861fe4a60f67197442078e6d9`。只运行静态/小维度合成与 mock，未 SSH、安装、调用子 agent、创建 goal、构造真实 Match/Battle、采真实 demo 或执行 optimizer step。

Node `v24.21.0`：

```text
timeout 15s node --max-old-space-size=128 gold-features/tests.mjs
PASS: prefix bytes, own area/kind/golden counts, modes, before-action recorder, exact loader URL/SHA; synthetic only
exit 0; tool wall time 0.237s
```

包含原 encoder global 前缀 Float64 bytes 相等、candidates/mask 引用与值保留、完整词表、board/hand/temp 多件计数及 kind/golden 过滤、离线 snapshot 时机约束；实际 Node loader subprocess 只导入原 ShopEnv 并使用合成 player/match-shaped 普通对象，未调用 create/reset。原 recorder 在合成动作把 golden 改为 false **之前**记录正确的 counts。未启用 loader mode 时原结果 JSON 字节一致。未知 env/features SHA、非法 mode 均拒绝；其他 URL 不改。

Python `3.10.20`，已有 `/home/q2635/venvs/logics-v3/bin/python`：

```text
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES=''
/usr/bin/time -f 'elapsed=%e maxRSS_KiB=%M' timeout 15s <python3.10> gold-features/tests.py
......
Ran 6 tests in 0.217s
OK
```

最终 6-test suite 实测 elapsed=1.42s、maxRSS=645144 KiB（约 630 MiB，低于 1 GiB）；完整命令输出小于 1 KiB。测试数据及临时 checkpoint/gzip 均为小维度合成并自动删除，目录内源码和文档不到 100 KiB，测试输出远低于 5 MB。

覆盖初始 logits/value 数值等价、原参数保留、新权重零、两 arm 全参数相同、全局/传入 generator 不消耗、标准 checkpoint reload/validate、OFF 新列 BC 梯度全零与 ON 非零（仅 backward，无 optimizer）；递归对比拒绝 sample 额外字段/action/candidate/global、terminal summary/observation/partnerAudit 和顶层其他字段的变化；bool 与 number 不混同。mock CLI 验证冻结且整体 incomplete 的 manifest 中合格行可用、相同请求 OFF/ON、exclusive/nooverwrite、完整新 raw gzip 的 SHA/来源、失败状态/有限错误证据、拒收 raw 也完整保存并明确标记、gzip 解压上限、超时前不启动 child。所有 Python 文件通过 3.10 grammar 检查。

一次主动加 `prlimit --as=1073741824` 的尝试在 Torch 导入阶段失败：

```text
ImportError: libtorch_cpu.so: failed to map segment from shared object
exit 1; elapsed=1.20s; maxRSS_KiB=256848
```

这是 1 GiB **虚拟地址空间**限制与已有 Torch 共享库映射不兼容；之后用实际 maxRSS 记录内存，没有安装/替换依赖。该失败不是模型或 warm-start 测试通过证据。

`rl/env.mjs`、`rl/features.mjs`、`rl/learner.py` SHA 分别保持：

```text
723e397fabeea4e473b65523aaadbadba7b08af7fea27ec4f29fa38c3cefc4b5
78f6c7a8bf99d35b6feb858536dc2d33fc50f36e6626ef9ab55d8903de9499da
a14e014f082fc0bca8bc7eec5eab76c80d06972ae64f493f359bb24db7f4e726
```

真实服务器 recapture、真实全轨迹一致性、训练和实际终极通关率均**未执行/未验证**。此记录仅支持 seam 与拒收机制的合成验证；不支持胜率、收益或合法状态表示不可能性结论。


Main 修复7a53037复审3P2：整体failed拒绝/前后raw集合；固定486游戏树+package+manifestSHA前后校验并写provenance；CPU float32显式构造。7项Python3.10合成通过，实际本地486文件身份函数核对通过，无真实游戏。新dtype回归最初在保持global float64时调用旧forward失败，定位为原padding默认dtype限制；现测试在float64下验证返回参数/保存状态均CPU float32、全局dtype及RNG不变，再恢复原正常float32环境执行forward；限制在README明示。
