# Implementation validation — 2026-10-18

Base: `23bdfc3051a5059b41c3ffb7dcb9ddf421b3a697`. Changes confined to `survivor-study/`.
Original learner/coop/features/controllers are unchanged and SHA-pinned in `source-pins.json`.

Final synthetic verification:

```text
$ timeout 20s python3 -B survivor-study/check.py
Ran 9 tests in 3.343s
OK
Synthetic contracts: exit=0; seconds=3.835; aggregatePeakRSSMiB=639.20
```

The monitor covers the test process and its descendants, enforcing <20 seconds and <=1 GiB
aggregate resident memory. Earlier iterations also passed within those limits; no real games
were involved. The final run exercised:

- Complete synthetic 207/68 admission, 93/114 split, fixed seat/round selection and all-207 coverage.
- Wrong output hash, truncated gzip, garbage tail, concatenated members, duplicate JSON keys,
  missing samples/dispositions, forged seat/round/reference/static-data/group/terminal metadata,
  and extra future-data fields rejected.
- Original `L.ActorCritic(90, 15, 4)` / `L.bc_update`: three synthetic arms, each 6800 actual indexed
  draws and 200 Adam updates, identical full initialization and order, unchanged critic head,
  standard checkpoint save/load, parent file unchanged. Temporary artifacts are removed.
- Actual posttrain admission and 128-job scheduler with synthetic replacement jobs: eight injected
  failures retained in denominator, 120 completed team wins, zero learner wins; original immutable
  manifest remains unrun. Wrong source admission starts no games; admission failure retains 128 unrun.
- Fake JSONL subprocesses: full candidates/actions, verbatim terminal, individual/team distinction,
  successful close/EOF, extra tail, stderr error, output budget and absolute timeout.
- Terminal engine/fatal/controller errors, wrong seed/seat/steps, pure-validation diagnostic allowance
  and non-diagnostic rejection, nooverwrite, code discovery, source guard.

Both CLI help commands and `node --check survivor-study/static.mjs` pass. No dependencies installed,
SSH, subagent, goal, real game, production-data training, production-data admission run or production
evaluation was performed. Neighbor evidence was inspected read-only to implement its interface;
`17225e4` inputs remain explicitly unapproved pending their independent review.

Not validated here: server deployment, designated bc-expansion `all.pt` provenance, live static-data
admission, real-size training runtime/memory, or the real 128-game dev evaluation. Main must execute
the documented train then eval commands after integration. Passing synthetic contracts makes no
claim about expert quality, win rate, heldout families or unseen-seed generalization.


Main 部署适配（实施者已停止后）：server venv 是 Python 3.10，替换 3.11 才引入的 hashlib.file_digest 为等价流式 SHA256；规则目录支持授权服务器的 game/ 与本地 Stronghold-Protocol/，须恰好存在一个。inputReviewStatus 改为中性 interface-checked-independent-review-record-external，独立批准记录单独保存，不把源码内旧待审状态当当前事实。原训练/评估/输入严格性不变；再次9项合成契约通过，实际服务器训练尚未执行。
