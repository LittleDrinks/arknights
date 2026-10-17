#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze.py — 上游 botbench 对局记录的实验分析 CLI（Python 标准库，无第三方依赖）。

输入
----
- 一份 baseline JSON：``tools/botbench.mjs --json`` 产出的对局记录数组；
- 零或多份 candidate JSON：``--candidate 标签=路径`` 可重复；
- 可选实验 manifest（如 ``run-manifest.json``），用于读取 jobs、engine_commit、
  trained_model、seed 覆盖等运行条件。

输出（写入 ``--out-dir``）
----------------------
- ``report.json``：完整机器可读报告（输入含 sha256，除时间戳外可复算、可 diff）；
- ``report.md``：人类可读 Markdown 报告。

唯一主标准（primary endpoint）
------------------------------
完整 ABYSS 终极模拟对局的 ``victory == true``。roundsPassed、层数、道中存活
（reachedBoss）、hidden 隐藏终局达成一律是**描述性**指标，绝不当作通关；
``hidden == true`` 而 ``victory == false`` 仍记为败局。

内建诚实性约束（不可关闭）
------------------------
1. 缺失 seed、重复 (config, seed)、两臂 stage/boss 不一致、errors>0 或 errors
   字段缺失的对局**不会默默进入配对**，全部计入 data quality 问题并从配对中剔除。
2. 两臂全 0 胜时结论为“主标准无任何成功、比较无信息量”（absence of evidence ≠
   equivalence），禁止表述为“等效 / 无差异”。
3. McNemar 精确检验只由不一致对（discordant pairs）决定；b+c=0 时检验无定义，
   显式说明而不是给 p=1。
4. jobs>1（来自 manifest 或 --jobs）时墙时数字必须带“并行负载膨胀”警示；
   jobs 未知时给出保守提示。

用法
----
    python3 analyze.py --baseline r0.json \
        --candidate r1=r1.json --candidate r3=r3.json \
        --manifest run-manifest.json \
        --out-dir observed-results/planner-ablation-2026-10-18
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import shlex
import statistics
import sys

VERSION = "1.0.0"
TOOL_NAME = "analyze.py"
Z95 = 1.959963984540054  # 标准正态 97.5% 分位

# ---------------------------------------------------------------------------
# 统计原语
# ---------------------------------------------------------------------------


def wilson_ci(k: int, n: int, z: float = Z95):
    """Wilson score 区间（95% 默认）。k=成功数, n=样本数。n<=0 时返回 None。"""
    if n <= 0:
        return None
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return [max(0.0, center - half), min(1.0, center + half)]


def mcnemar_exact(b: int, c: int) -> dict:
    """配对二元结果的精确 McNemar（不一致对二项）检验，双侧。

    b = (baseline 败, candidate 胜) 数；c = (baseline 胜, candidate 败) 数。
    在 H0: p_b = p_c 下，给定 m = b+c，b ~ Binomial(m, 0.5)。
    p_two = min(1, 2 * P(X <= min(b,c)))；m == 0 时检验无定义（返回 None）。
    """
    m = b + c
    if m == 0:
        return {
            "b": b,
            "c": c,
            "m": 0,
            "p_two_sided": None,
            "note": "no discordant pairs; exact test undefined",
        }
    lo = min(b, c)
    tail_num = sum(math.comb(m, k) for k in range(0, lo + 1))  # 精确整数
    tail = tail_num / (2 ** m)
    p = min(1.0, 2.0 * tail)
    return {
        "b": b,
        "c": c,
        "m": m,
        "p_two_sided": p,
        "tail_probability": tail,
        "note": "two-sided exact binomial over discordant pairs; unadjusted exploratory p-value",
    }


def min_flips_for_alpha(zero_reverse: bool = True, alpha: float = 0.05) -> int:
    """反向翻转为 0 时，达到双侧 p<alpha 所需的最少净翻转数。2*0.5^d < alpha。"""
    d = 1
    while 2.0 * (0.5 ** d) >= alpha:
        d += 1
    return d


def parse_seed_spec(spec: str) -> list[int]:
    """解析 "1-8,12,15" 形式的种子集合，返回有序去重列表。"""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, bpart = part.partition("-")
            lo, hi = int(a.strip()), int(bpart.strip())
            if hi < lo:
                lo, hi = hi, lo
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    return sorted(out)


# ---------------------------------------------------------------------------
# 记录装载与校验
# ---------------------------------------------------------------------------


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_record(raw, idx: int, src: str):
    """校验并规范化一条 botbench 记录。返回 (record|None, issues)。"""
    where = f"{src}[{idx}]"
    if not isinstance(raw, dict):
        return None, [f"{where}: not a JSON object"]

    issues: list[str] = []
    cfg = raw.get("config")
    seed = raw.get("seed")
    victory = raw.get("victory")

    if not isinstance(cfg, str) or not cfg.strip():
        return None, [f"{where}: missing/invalid 'config'"]
    if isinstance(seed, bool):
        return None, [f"{where}: invalid 'seed' (bool)"]
    if isinstance(seed, float) and seed.is_integer():
        issues.append(f"{where}: seed {seed} is float, coerced to int")
        seed = int(seed)
    if not isinstance(seed, int):
        return None, [f"{where}: missing/invalid 'seed'"]
    if not isinstance(victory, bool):
        return None, [f"{where}: missing/non-bool 'victory' — outcome unknowable, record dropped"]

    def _int_field(name):
        v = raw.get(name)
        if v is None:
            return None
        if isinstance(v, bool):
            issues.append(f"{where}: '{name}' is bool, ignored")
            return None
        if isinstance(v, (int, float)) and v < 0:
            issues.append(f"{where}: negative {name}, ignored")
            return None
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, int):
            return v
        issues.append(f"{where}: '{name}' not numeric, ignored")
        return None

    rounds_passed = _int_field("roundsPassed")
    boss_round = _int_field("bossRound")
    errors = _int_field("errors")
    lp_left = _int_field("lpLeft")

    hidden = raw.get("hidden")
    if hidden is None:
        issues.append(f"{where}: 'hidden' missing, reported unknown")
    elif not isinstance(hidden, bool):
        issues.append(f"{where}: 'hidden' not bool, reported unknown")
        hidden = None

    stage = raw.get("stageId")
    boss = raw.get("bossId")
    if not isinstance(stage, str) or not stage:
        issues.append(f"{where}: 'stageId' missing — pairing unverifiable for this seed")
        stage = None
    if not isinstance(boss, str) or not boss:
        issues.append(f"{where}: 'bossId' missing — pairing unverifiable for this seed")
        boss = None

    ms = raw.get("ms")
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms < 0 or not math.isfinite(ms):
        issues.append(f"{where}: 'ms' missing/invalid — excluded from wall-time mean")
        ms = None

    if errors is None:
        issues.append(f"{where}: 'errors' missing — pairing unverifiable for this seed")

    elif errors != 0:
        issues.append(f"{where}: nonzero errors={errors} — excluded from pairing")

    if not cfg.endswith(":ABYSS"):
        issues.append(f"{where}: non-ABYSS config; victory is not ABYSS completion evidence")

    # 一致性核验：胜局必须打到 boss 轮（results.js: 胜 ⇒ roundsPassed >= bossRound）。
    if victory and rounds_passed is not None and boss_round is not None:
        if rounds_passed < boss_round:
            issues.append(
                f"{where}: ANOMALY victory=true but roundsPassed={rounds_passed} < bossRound={boss_round}"
            )

    rec = {
        "config": cfg,
        "seed": seed,
        "victory": victory,
        "hidden": hidden,
        "rounds_passed": rounds_passed,
        "boss_round": boss_round,
        "stage": stage,
        "boss": boss,
        "errors": errors,
        "lp_left": lp_left,
        "ms": ms,
    }
    return rec, issues


def load_arm(label: str, path: str) -> dict:
    """读入一份 botbench JSON 数组，校验、查重，返回 arm 结构。"""
    if not os.path.isfile(path):
        raise SystemExit(f"error: input file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"error: {path}: invalid JSON: {e}")
    if not isinstance(raw, list):
        raise SystemExit(f"error: {path}: expected a JSON array of match records")

    issues: list[str] = []
    records = []
    for i, item in enumerate(raw):
        rec, rec_issues = normalize_record(item, i, label)
        issues.extend(rec_issues)
        if rec is not None:
            records.append(rec)

    by_config: dict[str, list[dict]] = {}
    for rec in records:
        by_config.setdefault(rec["config"], []).append(rec)

    # Count raw keys too: an invalid-outcome duplicate must not leave a valid copy pairable.
    raw_by_config = {}
    for item in raw:
        if (isinstance(item, dict) and isinstance(item.get("config"), str)
                and isinstance(item.get("seed"), (int, float))
                and not isinstance(item.get("seed"), bool)
                and math.isfinite(item["seed"]) and int(item["seed"]) == item["seed"]):
            raw_by_config.setdefault(item["config"], []).append(item)
    # 重复 (config, seed)：配对时整键剔除（不能默默挑一条），并大声报告。
    duplicate_keys = []
    for cfg, recs in raw_by_config.items():
        seen: dict[tuple, int] = {}
        for r in recs:
            k = (r["config"], r["seed"])
            seen[k] = seen.get(k, 0) + 1
        for k, n in seen.items():
            if n > 1:
                duplicate_keys.append({"config": k[0], "seed": k[1], "count": n})
                issues.append(
                    f"{label}: DUPLICATE (config={k[0]}, seed={k[1]}) appears {n}x — "
                    "all copies excluded from pairing"
                )
    duplicate_keys.sort(key=lambda d: (d["config"], d["seed"]))

    dup_key_set = {(d["config"], d["seed"]) for d in duplicate_keys}
    # 配对视图：只含无重复、stage/boss/errors 齐全可核验的记录。
    pairable: dict[str, dict[int, dict]] = {}
    for cfg, recs in by_config.items():
        m = {}
        for r in recs:
            if (r["config"], r["seed"]) in dup_key_set:
                continue
            m[r["seed"]] = r
        pairable[cfg] = m

    return {
        "label": label,
        "raw_records": len(raw),
        "dropped_records": len(raw) - len(records),
        "path": path,
        "sha256": sha256_file(path),
        "bytes": os.path.getsize(path),
        "records": records,
        "by_config": by_config,
        "pairable": pairable,
        "duplicate_keys": duplicate_keys,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# arm 汇总
# ---------------------------------------------------------------------------


def summarize_records(recs: list[dict]) -> dict:
    n = len(recs)
    wins = sum(1 for r in recs if r["victory"])
    losses = n - wins
    hidden_reached = sum(1 for r in recs if r["hidden"])
    hidden_win = sum(1 for r in recs if r["hidden"] and r["victory"])
    hidden_reached_loss = sum(1 for r in recs if r["hidden"] and not r["victory"])
    rp_vals = [r["rounds_passed"] for r in recs if r["rounds_passed"] is not None]
    # 失败轮 = roundsPassed + 1（results.js: 第 k 轮被淘汰 ⇒ roundsPassed=k-1；
    # 决战失败 ⇒ roundsPassed=bossRound-1，失败轮=bossRound）。仅统计败局。
    fail_vals = [r["rounds_passed"] + 1 for r in recs if not r["victory"] and r["rounds_passed"] is not None]
    reached_boss = sum(
        1
        for r in recs
        if r["rounds_passed"] is not None and r["boss_round"] is not None
        and r["rounds_passed"] >= r["boss_round"] - 1
    )
    errors_total = sum(r["errors"] for r in recs if r["errors"] is not None)
    games_with_errors = sum(1 for r in recs if (r["errors"] or 0) > 0)
    ms_vals = [r["ms"] for r in recs if r["ms"] is not None]
    lp_vals = [r["lp_left"] for r in recs if r["lp_left"] is not None]
    return {
        "games": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) if n else None,
        "wilson95": wilson_ci(wins, n),
        "hidden_unknown": sum(r["hidden"] is None for r in recs),
        "hidden_reached": hidden_reached,
        "hidden_and_win": hidden_win,
        "hidden_reached_but_loss": hidden_reached_loss,
        "avg_rounds_passed": (statistics.fmean(rp_vals) if rp_vals else None),
        "loss_games_with_round_info": len(fail_vals),
        "avg_failure_round": (statistics.fmean(fail_vals) if fail_vals else None),
        "reached_final_assault_descriptive": reached_boss,
        "errors_unknown_games": sum(r["errors"] is None for r in recs),
        "errors_total": errors_total,
        "games_with_errors": games_with_errors,
        "avg_wall_ms": (statistics.fmean(ms_vals) if ms_vals else None),
        "median_wall_ms": (statistics.median(ms_vals) if ms_vals else None),
        "wall_samples": len(ms_vals),
        "avg_lp_left": (statistics.fmean(lp_vals) if lp_vals else None),
    }


def arm_report(arm: dict, seed_groups: dict) -> dict:
    by_config = {
        cfg: summarize_records(recs) for cfg, recs in sorted(arm["by_config"].items())
    }
    all_stats = summarize_records(arm["records"]) if arm["records"] else summarize_records([])
    per_seed = [
        {
            "config": r["config"],
            "seed": r["seed"],
            "stage": r["stage"],
            "boss": r["boss"],
            "victory": r["victory"],
            "hidden": r["hidden"],
            "rounds_passed": r["rounds_passed"],
            "boss_round": r["boss_round"],
            "failure_round": (None if r["victory"] or r["rounds_passed"] is None else r["rounds_passed"] + 1),
            "errors": r["errors"],
            "ms": (None if r["ms"] is None else r["ms"]),
            "lp_left": r["lp_left"],
        }
        for r in sorted(arm["records"], key=lambda x: (x["config"], x["seed"]))
    ]
    return {"by_config": by_config, "all_configs_pooled": all_stats, "per_seed": per_seed,
            "seed_groups": {name: {cfg: summarize_records([r for r in recs if seeds is None or r["seed"] in seeds])
                                  for cfg, recs in sorted(arm["by_config"].items())}
                            for name, seeds in seed_groups.items()}}


def coverage_audit(arm: dict, expected_configs: list[str], seed_start: int, seed_count: int) -> list[dict]:
    """对照 manifest 声明的 (config, seed) 覆盖，报告缺失/多余/重复。"""
    out = []
    expected_seeds = set(range(seed_start, seed_start + seed_count))
    for cfg in expected_configs:
        actual = [r["seed"] for r in arm["by_config"].get(cfg, [])]
        missing = sorted(expected_seeds - set(actual))
        extra = sorted(set(actual) - expected_seeds)
        if missing or extra:
            out.append(
                {
                    "config": cfg,
                    "expected_seeds": sorted(expected_seeds),
                    "missing_seeds": missing,
                    "unexpected_seeds": extra,
                }
            )
    for cfg in arm["by_config"]:
        if cfg not in expected_configs:
            out.append({"config": cfg, "note": "config not listed in manifest", "seeds": sorted({r['seed'] for r in arm['by_config'][cfg]})})
    return out


# ---------------------------------------------------------------------------
# 配对比较
# ---------------------------------------------------------------------------


def _pair_flip_entry(b: dict, c: dict) -> dict:
    return {
        "config": b["config"],
        "seed": b["seed"],
        "stage": b["stage"],
        "boss": b["boss"],
        "baseline": {
            "victory": b["victory"],
            "rounds_passed": b["rounds_passed"],
            "failure_round": (None if b["victory"] or b["rounds_passed"] is None else b["rounds_passed"] + 1),
            "hidden": b["hidden"],
            "lp_left": b["lp_left"],
        },
        "candidate": {
            "victory": c["victory"],
            "rounds_passed": c["rounds_passed"],
            "failure_round": (None if c["victory"] or c["rounds_passed"] is None else c["rounds_passed"] + 1),
            "hidden": c["hidden"],
            "lp_left": c["lp_left"],
        },
    }


def pair_analysis(base_arm: dict, cand_arm: dict, seed_groups: dict[str, list[int]]) -> dict:
    """按 (config, seed) 配对比较 baseline 与 candidate。

    剔除规则（全部显式报告，绝不静默）：
    - 任一臂该 (config, seed) 重复 → 双方剔除；
    - 只在一臂出现 → 记 missing_in_{baseline,candidate}；
    - 两臂 stage/boss 不一致（或缺一侧）→ 剔除（局面不可比）；
    - 任一臂 errors>0 或 errors 未知 → 剔除（对局完整性存疑）。
    """
    issues: list[str] = []
    configs = sorted(set(base_arm["pairable"]) | set(cand_arm["pairable"]))
    result: dict = {"by_config": {}, "issues": issues}

    for cfg in configs:
        bmap = base_arm["pairable"].get(cfg, {})
        cmap = cand_arm["pairable"].get(cfg, {})
        if cfg not in base_arm["by_config"]:
            issues.append(f"config '{cfg}' absent in baseline '{base_arm['label']}' — no pairing for it")
        if cfg not in cand_arm["by_config"]:
            issues.append(f"config '{cfg}' absent in candidate '{cand_arm['label']}' — no pairing for it")

        excluded = {
            "missing_in_baseline": [],
            "missing_in_candidate": [],
            "duplicate_key_baseline": sorted(
                d["seed"] for d in base_arm["duplicate_keys"] if d["config"] == cfg
            ),
            "duplicate_key_candidate": sorted(
                d["seed"] for d in cand_arm["duplicate_keys"] if d["config"] == cfg
            ),
            "stage_boss_mismatch": [],
            "nonzero_or_unknown_errors": [],
        }
        pairs: list[tuple[dict, dict]] = []
        duplicate_seeds = set(excluded["duplicate_key_baseline"] + excluded["duplicate_key_candidate"])
        for seed in sorted(set(bmap) | set(cmap)):
            if seed in duplicate_seeds:
                continue
            b = bmap.get(seed)
            c = cmap.get(seed)
            if b is None:
                excluded["missing_in_baseline"].append(seed)
                issues.append(f"{cfg} seed {seed}: missing in baseline — excluded")
                continue
            if c is None:
                excluded["missing_in_candidate"].append(seed)
                issues.append(f"{cfg} seed {seed}: missing in candidate — excluded")
                continue
            if (not b["stage"] or not c["stage"] or not b["boss"] or not c["boss"]
                    or b["stage"] != c["stage"] or b["boss"] != c["boss"]):
                excluded["stage_boss_mismatch"].append(
                    {
                        "seed": seed,
                        "baseline_stage": b["stage"],
                        "baseline_boss": b["boss"],
                        "candidate_stage": c["stage"],
                        "candidate_boss": c["boss"],
                    }
                )
                issues.append(
                    f"{cfg} seed {seed}: stage/boss mismatch "
                    f"({b['stage']}/{b['boss']} vs {c['stage']}/{c['boss']}) — pair excluded"
                )
                continue
            if b["errors"] != 0 or c["errors"] != 0:
                excluded["nonzero_or_unknown_errors"].append(
                    {"seed": seed, "baseline_errors": b["errors"], "candidate_errors": c["errors"]}
                )
                issues.append(
                    f"{cfg} seed {seed}: errors baseline={b['errors']} candidate={c['errors']} — pair excluded"
                )
                continue
            pairs.append((b, c))

        k = len(pairs)
        a = sum(1 for b, c in pairs if b["victory"] and c["victory"])       # 双胜
        bw = sum(1 for b, c in pairs if b["victory"] and not c["victory"])  # win→loss (c)
        cw = sum(1 for b, c in pairs if not b["victory"] and c["victory"])  # loss→win (b)
        d = sum(1 for b, c in pairs if not b["victory"] and not c["victory"])  # 双败
        base_wins = a + bw
        cand_wins = a + cw

        mc = mcnemar_exact(cw, bw)
        diff = ((cw - bw) / k) if k else None
        diff_ci = None  # No marginal difference CI: conditional discordance CI is not one.
        min_flips = min_flips_for_alpha()

        if k == 0:
            code = "no_eligible_pairs"
            verdict = "没有任何可配对样本 —— 无法比较（见剔除明细）。"
        elif cw + bw == 0:
            if base_wins + cand_wins == 0:
                code = "uninformative_zero_wins"
                verdict = (
                    "主标准（完整对局 victory）两臂全为 0 胜：本比较无信息量，"
                    "属于“没有成功证据”，不是“两臂等效”——禁止把 0/0 说成无差异或等效；"
                    "任何提升结论至少需要 1 个 victory。roundsPassed/存活/层数是描述性指标，"
                    "不得当作通关。"
                )
            else:
                code = "no_discordant_pairs"
                verdict = (
                    f"无不一致对（b=c=0）：{k} 个配对种子上两臂胜场完全一致"
                    f"（各 {base_wins} 胜）。McNemar 精确检验无定义；样本量不足以分辨差异，"
                    "也不能据此声称等效。"
                )
        else:
            p = mc["p_two_sided"]
            if p < 0.05:
                if cw > bw:
                    code = "significant_improvement"
                    verdict = (
                        f"显著提升：loss→win {cw} 个、win→loss {bw} 个，"
                        f"双侧精确 McNemar p={p:.4g} < 0.05（仅 {k} 个配对，仍属小样本，须看翻转 seed 明细）。"
                    )
                else:
                    code = "significant_regression"
                    verdict = (
                        f"显著退化：win→loss {bw} 个、loss→win {cw} 个，"
                        f"双侧精确 McNemar p={p:.4g} < 0.05。"
                    )
            else:
                code = "not_significant"
                verdict = (
                    f"未达显著：loss→win {cw}、win→loss {bw}，双侧精确 McNemar p={p:.4g} ≥ 0.05"
                    f"（不一致对 m={cw + bw}；反向为 0 时至少需要 {min_flips} 个净翻转才可能 p<0.05）。"
                    "这不构成等效证明。"
                )

        warn = sum(len(v) if isinstance(v, list) else 0 for v in excluded.values())
        if warn:
            verdict = f"[{warn} 个配对被剔除，见 excluded——结论仅适用于合格配对子集，剔除可能引入选择偏差] {verdict}"

        group_stats = {}
        for gname, seeds in seed_groups.items():
            gset = set(seeds) if seeds is not None else None
            gpairs = [pc for pc in pairs if gset is None or pc[0]["seed"] in gset]
            gk = len(gpairs)
            if gk:
                ga = sum(1 for b, c in gpairs if b["victory"] and c["victory"])
                gbw = sum(1 for b, c in gpairs if b["victory"] and not c["victory"])
                gcw = sum(1 for b, c in gpairs if not b["victory"] and c["victory"])
                gd = gk - ga - gbw - gcw
                gmc = mcnemar_exact(gcw, gbw)
            else:
                ga = gbw = gcw = gd = 0
                gmc = mcnemar_exact(0, 0)
            group_stats[gname] = {
                "seeds": (None if seeds is None else sorted(seeds)),
                "paired": gk,
                "both_win": ga,
                "win_to_loss": gbw,
                "loss_to_win": gcw,
                "both_loss": gd,
                "baseline_wins": ga + gbw,
                "candidate_wins": ga + gcw,
                "baseline_win_rate": (ga + gbw) / gk if gk else None,
                "candidate_win_rate": (ga + gcw) / gk if gk else None,
                "win_rate_difference": (gcw - gbw) / gk if gk else None,
                "baseline_wilson95": wilson_ci(ga + gbw, gk),
                "candidate_wilson95": wilson_ci(ga + gcw, gk),
                "mcnemar_p_two_sided": gmc["p_two_sided"],
            }

        result["by_config"][cfg] = {
            "config": cfg,
            "baseline_records": len(base_arm["by_config"].get(cfg, [])),
            "candidate_records": len(cand_arm["by_config"].get(cfg, [])),
            "n_eligible_pairs": k,
            "outcomes": {"both_win": a, "loss_to_win": cw, "win_to_loss": bw, "both_loss": d},
            "baseline_wins": base_wins,
            "candidate_wins": cand_wins,
            "baseline_win_rate": (base_wins / k) if k else None,
            "candidate_win_rate": (cand_wins / k) if k else None,
            "wilson95_paired_subset": {
                "baseline": wilson_ci(base_wins, k),
                "candidate": wilson_ci(cand_wins, k),
            },
            "win_rate_difference": diff,
            "difference_ci": diff_ci,
            "mcnemar_exact": mc,
            "min_net_flips_for_p05_with_zero_reverse": min_flips,
            "loss_to_win": [_pair_flip_entry(b, c) for b, c in pairs if not b["victory"] and c["victory"]],
            "win_to_loss": [_pair_flip_entry(b, c) for b, c in pairs if b["victory"] and not c["victory"]],
            "excluded": excluded,
            "seed_groups": group_stats,
            "verdict_code": code,
            "verdict": verdict,
        }
    return result


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------


def _pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def _ci(ci):
    if ci is None:
        return "—"
    return f"[{_pct(ci[0])}, {_pct(ci[1])}]"


def _num(x, nd=2):
    return "—" if x is None else f"{x:.{nd}f}"


def _pval(p):
    if p is None:
        return "undefined (m=0)"
    if p < 1e-4:
        return f"{p:.3g}"
    return f"{p:.4f}"


def render_markdown(rep: dict) -> str:
    L: list[str] = []
    A = L.append
    A(f"# {rep['title']}")
    A("")
    A(f"- 生成时间：{rep['generated_at']}（UTC）")
    A(f"- 工具：`{TOOL_NAME}` v{VERSION}（sha256 `{rep['tool']['sha256'][:12]}…`）")
    man = rep["manifest"]
    if man["present"]:
        A(
            f"- 引擎提交（manifest）：`{man.get('engine_commit') or '未知'}`；"
            f"trained_model：{man.get('trained_model')}"
        )
    else:
        A("- 未提供实验 manifest：jobs/引擎提交/训练状态未知（见注意事项）。")
    A("- **唯一主标准：完整对局 `victory`（ABYSS 终极模拟通关）**；rounds/层数/道中存活/hidden 均为描述性指标。")
    A("")

    # 输入
    A("## 输入")
    A("")
    A("| 角色 | 标签 | 路径 | 记录数 | sha256（前 16） |")
    A("|---|---|---|---:|---|")
    for role, arm in rep["input_arms"]:
        A(f"| {role} | {arm['label']} | `{arm['path']}` | {arm['records']} | `{arm['sha256'][:16]}` |")
    A("")

    # 数据质量
    dq = rep["data_quality"]
    A("## 数据质量（全部问题，未静默处理）")
    A("")
    total = dq["total_issues"]
    if total == 0:
        A("未发现问题：无重复 (config, seed)、无坏记录；两臂 seed/stage/boss/errors 配对前均逐条核验通过。")
    else:
        A(f"共 {total} 条问题/剔除记录：")
        A("")
        for arm_label, iss in dq["by_arm"]:
            for s in iss:
                A(f"- {s}")
        for cand_label, iss in dq["pairing_issues"].items():
            for s in iss:
                A(f"- {s}")
        for label, covs in dq["coverage_vs_manifest"].items():
            for c in covs:
                A(f"- coverage[{label}]: {json.dumps(c, ensure_ascii=False)}")
    A("")

    # arm 汇总表
    A("## 按 arm × config 汇总（主标准 = victory）")
    A("")
    caveat = rep["run_conditions"]["wall_time_caveat"]
    header = (
        "| arm | config | 局数 | 胜场 | 胜率 | Wilson 95% CI | hidden 达成 | "
        "平均失败轮（败局） | 平均通过轮 | 决战到达（描述） | 错误 | 平均整局墙时 ms |"
    )
    A(header)
    A("|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|")
    for label, armr in rep["arms"].items():
        for cfg, st in armr["by_config"].items():
            A(
                f"| {label} | {cfg} | {st['games']} | {st['wins']} | {_pct(st['win_rate'])} "
                f"| {_ci(st['wilson95'])} | {st['hidden_reached']}"
                f"（胜 {st['hidden_and_win']}；未知 {st['hidden_unknown']}） | {_num(st['avg_failure_round'])} "
                f"| {_num(st['avg_rounds_passed'])} | {st['reached_final_assault_descriptive']} "
                f"| {st['errors_total']}（{st['games_with_errors']} 局；未知 {st['errors_unknown_games']} 局） | {_num(st['avg_wall_ms'], 0)} |"
            )
        st = armr["all_configs_pooled"]
        A(
            f"| {label} | **全部 config 合并** | {st['games']} | {st['wins']} | {_pct(st['win_rate'])} "
            f"| {_ci(st['wilson95'])} | {st['hidden_reached']}（胜 {st['hidden_and_win']}） "
            f"| {_num(st['avg_failure_round'])} | {_num(st['avg_rounds_passed'])} "
            f"| {st['reached_final_assault_descriptive']} | {st['errors_total']}（{st['games_with_errors']} 局） "
            f"| {_num(st['avg_wall_ms'], 0)} |"
        )
    A("")
    A(
        "注：“平均失败轮”仅按败局计算 = roundsPassed + 1（第 k 轮被淘汰 ⇒ roundsPassed=k−1；"
        "决战失败 ⇒ 失败轮 = bossRound）。“决战到达”= roundsPassed ≥ bossRound−1，**不是胜场**。"
        "hidden 达成而未 victory 仍计为败局。"
    )
    if caveat:
        A("")
        A(f"> ⚠️ 墙时警示：{caveat}")
    A("")

    if any(g != "all" for g in rep["seed_groups"]):
        A("## 声明的 seed 分组（未验证未见性；全部有效记录）")
        A("")
        A("| arm | group | config | 胜/局 | 胜率 | Wilson 95% |")
        A("|---|---|---|---:|---:|---|")
        for label, armr in rep["arms"].items():
            for group, configs in armr["seed_groups"].items():
                if group == "all":
                    continue
                for cfg, st in configs.items():
                    A(f"| {label} | {group} | {cfg} | {st['wins']}/{st['games']} | {_pct(st['win_rate'])} | {_ci(st['wilson95'])} |")
        A("")

    # 配对比较
    A("## 配对比较（baseline vs candidate，按 config/seed）")
    A("")
    if not rep["pairs"]:
        A("未提供 candidate（或未提供 baseline）：跳过配对比较。")
    for cand_label, pres in rep["pairs"].items():
        A(f"### baseline `{rep['baseline_label']}` vs candidate `{cand_label}`")
        A("")
        pooled = pres.get("all_configs_pooled")
        for cfg, pr in pres["by_config"].items():
            A(f"#### config `{cfg}`")
            A("")
            A(f"- 配对样本：**{pr['n_eligible_pairs']}**（baseline {pr['baseline_records']} 局 / candidate {pr['candidate_records']} 局）")
            ex = pr["excluded"]
            ex_parts = []
            if ex["missing_in_baseline"]:
                ex_parts.append(f"baseline 缺 seed {ex['missing_in_baseline']}")
            if ex["missing_in_candidate"]:
                ex_parts.append(f"candidate 缺 seed {ex['missing_in_candidate']}")
            if ex["duplicate_key_baseline"]:
                ex_parts.append(f"baseline 重复 seed {ex['duplicate_key_baseline']}")
            if ex["duplicate_key_candidate"]:
                ex_parts.append(f"candidate 重复 seed {ex['duplicate_key_candidate']}")
            if ex["stage_boss_mismatch"]:
                ex_parts.append(f"stage/boss 不一致 {len(ex['stage_boss_mismatch'])} 个 seed：" + ", ".join(
                    f"{m['seed']}({m['baseline_stage']}/{m['baseline_boss']}→{m['candidate_stage']}/{m['candidate_boss']})"
                    for m in ex["stage_boss_mismatch"]))
            if ex["nonzero_or_unknown_errors"]:
                ex_parts.append("errors>0/未知 " + ", ".join(
                    f"seed {m['seed']}(b={m['baseline_errors']},c={m['candidate_errors']})"
                    for m in ex["nonzero_or_unknown_errors"]))
            if ex_parts:
                A("- **剔除（未静默配对）**：" + "；".join(ex_parts))
            else:
                A("- 剔除：无")
            o = pr["outcomes"]
            A(
                f"- 结局矩阵：双胜 {o['both_win']}，**loss→win {o['loss_to_win']}**，"
                f"**win→loss {o['win_to_loss']}**，双败 {o['both_loss']}"
            )
            A(
                f"- 配对胜率：baseline {pr['baseline_wins']}/{pr['n_eligible_pairs']}"
                f"（{_pct(pr['baseline_win_rate'])}，CI {_ci(pr['wilson95_paired_subset']['baseline'])}）vs "
                f"candidate {pr['candidate_wins']}/{pr['n_eligible_pairs']}"
                f"（{_pct(pr['candidate_win_rate'])}，CI {_ci(pr['wilson95_paired_subset']['candidate'])}）；"
                f"**差值（candidate−baseline）= {_pct(pr['win_rate_difference'])}**"
            )
            dci = pr["difference_ci"]
            if dci:
                A(f"  - 差值区间（不一致对条件化近似）：[{_pct(dci['low'])}, {_pct(dci['high'])}]")
            mc = pr["mcnemar_exact"]
            A(
                f"- 精确 McNemar（不一致对二项）：b={mc['b']}，c={mc['c']}，m={mc['m']}，"
                f"双侧 p = {_pval(mc['p_two_sided'])}"
            )
            if pr["loss_to_win"]:
                A("- **loss→win 明细（原先失败转胜的 seed）**：")
                A("  - " + "; ".join(
                    f"seed {e['seed']}（{e['stage']}/{e['boss']}，失败轮 {e['baseline']['failure_round']} → 胜，"
                    f"roundsPassed {e['candidate']['rounds_passed']}）"
                    for e in pr["loss_to_win"]))
            else:
                A("- loss→win：**无**（没有原先失败的 seed 转胜）")
            if pr["win_to_loss"]:
                A("- **win→loss 明细**：")
                A("  - " + "; ".join(
                    f"seed {e['seed']}（{e['stage']}/{e['boss']}，胜 → 失败轮 {e['candidate']['failure_round']}）"
                    for e in pr["win_to_loss"]))
            else:
                A("- win→loss：无")
            gs = pr.get("seed_groups") or {}
            if len(gs) > 1 or (len(gs) == 1 and "all" not in gs):
                A("- 按 seed 分组：" + "；".join(
                    f"{g}：配对 {v['paired']}，base {v['baseline_wins']} 胜 / cand {v['candidate_wins']} 胜，"
                    f"loss→win {v['loss_to_win']}，p={_pval(v['mcnemar_p_two_sided'])}"
                    for g, v in gs.items()))
            A(f"- **结论（{pr['verdict_code']}）**：{pr['verdict']}")
            A("")
        if pooled:
            A(f"#### 全部 config 合并（pooled，仅作参考）")
            A("")
            o = pooled["outcomes"]
            mc = pooled["mcnemar_exact"]
            A(
                f"- 配对 {pooled['n_eligible_pairs']}：双胜 {o['both_win']}，loss→win {o['loss_to_win']}，"
                f"win→loss {o['win_to_loss']}，双败 {o['both_loss']}；"
                f"差值 = {_pct(pooled['win_rate_difference'])}；合并不做检验"
            )
            A(f"- **结论（{pooled['verdict_code']}）**：{pooled['verdict']}")
            A("")

    # 注意事项
    A("## 注意事项与诚实性声明")
    A("")
    for note in rep["notes"]:
        A(f"- {note}")
    A("")
    A("## 复现")
    A("")
    A("```bash")
    A(rep["tool"]["command"])
    A("```")
    A("")
    A(
        "同一命令、工具和 manifest 下，报告除 `generated_at` 外可复算；输入附 sha256；"
        "`report.json` 使用排序键、固定精度策略，重跑可直接 diff。"
    )
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_report(args) -> dict:
    # seed 分组
    seed_groups: dict[str, list[int] | None] = {"all": None}
    if args.train_seeds:
        seed_groups["train"] = parse_seed_spec(args.train_seeds)
    if args.eval_seeds:
        seed_groups["eval"] = parse_seed_spec(args.eval_seeds)

    manifest_raw = None
    jobs = args.jobs
    if args.manifest:
        if not os.path.isfile(args.manifest):
            raise SystemExit(f"error: manifest not found: {args.manifest}")
        with open(args.manifest, "r", encoding="utf-8") as f:
            manifest_raw = json.load(f)
        if not isinstance(manifest_raw, dict):
            raise SystemExit("error: manifest must be an object")
        if jobs is None and isinstance(manifest_raw.get("jobs"), int):
            jobs = manifest_raw["jobs"]

    if jobs is not None and (type(jobs) is not int or jobs < 1):
        raise SystemExit("error: jobs must be a positive integer")
    if set(seed_groups.get("train", [])) & set(seed_groups.get("eval", [])):
        raise SystemExit("error: train/eval seed overlap; cannot claim unseen evaluation")

    base_arm = load_arm(args.baseline_label, args.baseline)
    cand_arms = []
    for label, path in args.candidates:
        cand_arms.append(load_arm(label, path))
    labels = [base_arm["label"]] + [a["label"] for a in cand_arms]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"error: duplicate arm labels: {labels}")

    # jobs / 墙时警示
    if jobs is not None and jobs > 1:
        wall_caveat = (
            f"本批数据 jobs={jobs}（多 worker 并行）：墙时受并行负载影响，可能膨胀，"
            "只能相对比较同批、同负载的运行；跨批绝对耗时不可比，决策耗时测量须 jobs=1。"
        )
    elif jobs == 1:
        wall_caveat = None
    else:
        wall_caveat = (
            "输入未记录有效 jobs：若实际以多 worker 运行，"
            "下列墙时同样被负载膨胀，须谨慎比较。"
        )

    arms_report = {base_arm["label"]: arm_report(base_arm, seed_groups)}
    for a in cand_arms:
        arms_report[a["label"]] = arm_report(a, seed_groups)

    pairs_report = {}
    for ca in cand_arms:
        pairs_report[ca["label"]] = pair_analysis(base_arm, ca, seed_groups)

    # pooled 视图：把所有 config 的配对合并成一个伪 config（*pooled*），
    # 配对键变成 (config, seed)，避免不同 config 的同 seed 互相覆盖。
    def merged_arm(base: dict, cand: dict):
        def mk(arm: dict) -> dict:
            m = {k: v for k, v in arm.items() if k not in ("by_config", "pairable", "duplicate_keys")}
            m["by_config"] = {"*pooled*": [r for rs in arm["by_config"].values() for r in rs]}
            m["pairable"] = {"*pooled*": {(cfg, s): r for cfg, seeds in arm["pairable"].items() for s, r in seeds.items()}}
            m["duplicate_keys"] = [dict(d, config="*pooled*", seed=(d["config"], d["seed"])) for d in arm["duplicate_keys"]]
            return m

        res = pair_analysis(mk(base), mk(cand), seed_groups)
        pooled = res["by_config"].get("*pooled*")
        if pooled:
            pooled["mcnemar_exact"] = {"b": pooled["outcomes"]["loss_to_win"],
                                        "c": pooled["outcomes"]["win_to_loss"],
                                        "m": None, "p_two_sided": None,
                                        "note": "not tested: shared seeds across configs may be dependent"}
            pooled["wilson95_paired_subset"] = None
            pooled["seed_groups"] = {}
            pooled["verdict_code"] = "descriptive_only"
            pooled["verdict"] = "跨 config 共享 seed，合并仅描述计数和差值，不做独立配对推断；全零胜不证明等效。"
        return pooled

    for ca in cand_arms:
        pairs_report[ca["label"]]["all_configs_pooled"] = merged_arm(base_arm, ca)

    # manifest 摘要
    man_summary = {"present": manifest_raw is not None, "path": args.manifest}
    if manifest_raw is not None:
        man_summary["sha256"] = sha256_file(args.manifest)
        man_summary["jobs"] = manifest_raw.get("jobs")
        man_summary["engine_commit"] = manifest_raw.get("engine_commit")
        man_summary["trained_model"] = manifest_raw.get("trained_model")
        man_summary["raw"] = manifest_raw

    # 覆盖审计
    coverage = {}
    expected = None
    if manifest_raw and isinstance(manifest_raw.get("seed_start"), int) and isinstance(
        manifest_raw.get("seed_count_per_mode_per_arm"), int
    ):
        expected = (
            list(manifest_raw.get("configs") or []),
            manifest_raw["seed_start"],
            manifest_raw["seed_count_per_mode_per_arm"],
        )
    coverage_issues = []
    for a in [base_arm] + cand_arms:
        if expected:
            cov = coverage_audit(a, expected[0], expected[1], expected[2])
            if cov:
                coverage[f"{a['label']}"] = cov
                for c in cov:
                    coverage_issues.append(f"{a['label']}: coverage {json.dumps(c, ensure_ascii=False)}")

    # 数据质量汇总
    dq_by_arm = [(base_arm["label"], base_arm["issues"])]
    for a in cand_arms:
        dq_by_arm.append((a["label"], a["issues"]))
    pair_issue_count = 0
    for ca in cand_arms:
        iss = pairs_report[ca["label"]]["issues"]
        pair_issue_count += len(iss)
    # 同文件复用警告
    reuse = []
    for ca in cand_arms:
        if os.path.abspath(ca["path"]) == os.path.abspath(base_arm["path"]):
            msg = f"candidate '{ca['label']}' 与 baseline 是同一文件——self-compare 无意义，结果全为一致对。"
            reuse.append(msg)
    for msg in reuse:
        dq_by_arm.append(("tool", [msg]))

    total_issues = sum(len(i) for _, i in dq_by_arm) + pair_issue_count + len(coverage_issues)

    notes = [
        "Wilson 区间按 config 的独立 Bernoulli seed 假设计算；同 seed 跨 config/arm 不独立，合并 CI 仅名义参考，不代表泛化保证。",
        "逐 config 精确检验的 p 未做多重比较校正；显著性仅为探索性。配对胜率差不报告条件化伪边际 CI。",
        "hidden 是上游 hiddenReached 标志，不是 hiddenCleared；未知标志不按 false 填补。",
        "平均失败轮是上游 roundsPassed+1 的推算；隐藏终局失败会被上游截到 bossRound，不能据此恢复真实隐藏终局轮次。",
        "arm 汇总保留有错误/重复的有效结果行，配对严格剔除；须结合质量报告，不能把这些行当作独立有效试验。",
        "未声明独立 eval seeds 与队友留出集时，不声称未见 seed 或合作队友泛化。",
        "唯一主标准是完整对局 `victory`（ABYSS 终极模拟通关）；roundsPassed、层数、道中存活、"
        "hidden 达成只是描述性指标，不得在结论中冒充通关。",
        "配对剔除规则：缺失/重复 seed、两臂 stage/boss 不一致、errors>0 或 errors 未知的对局"
        "绝不进入配对，全部显式列出。",
        "两臂全 0 胜 ⇒ “比较无信息量”，不是“等效”；0/0 不可写成无差异。",
        "McNemar 精确检验只由不一致对决定；m=0 时 p 无定义，报告中写 undefined 而非 1。",
    ]
    if wall_caveat:
        notes.append(f"墙时：{wall_caveat}")
    if manifest_raw is not None and manifest_raw.get("trained_model") is False:
        notes.append(
            "manifest 标注 trained_model=false：所有 arm 均为未训练的规则 bot / 引擎参数消融，"
            "不存在训练成绩，不得解读为学习策略效果。"
        )
    if seed_groups.get("eval"):
        notes.append(
            f"eval seed 组（{len(seed_groups['eval'])} 个）用于“未见 seed 通关率”；"
            "该分组只有在训练/评测分割真实存在时才有泛化含义，工具本身不验证分割是否泄漏。"
        )

    cmd = shlex.join([sys.executable, TOOL_NAME] + args.command_argv)
    rep = {
        "title": args.title,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tool": {
            "name": TOOL_NAME,
            "version": VERSION,
            "sha256": sha256_file(os.path.abspath(__file__)),
            "command": cmd,
        },
        "baseline_label": base_arm["label"],
        "input_arms": [
            (
                "baseline",
                {
                    "label": base_arm["label"],
                    "path": base_arm["path"],
                    "sha256": base_arm["sha256"],
                    "bytes": base_arm["bytes"],
                    "records": len(base_arm["records"]),
                    "raw_records": base_arm["raw_records"],
                    "dropped_records": base_arm["dropped_records"],
                },
            )
        ]
        + [
            (
                "candidate",
                {
                    "label": a["label"],
                    "path": a["path"],
                    "sha256": a["sha256"],
                    "bytes": a["bytes"],
                    "records": len(a["records"]),
                    "raw_records": a["raw_records"],
                    "dropped_records": a["dropped_records"],
                },
            )
            for a in cand_arms
        ],
        "manifest": man_summary,
        "run_conditions": {"jobs": jobs, "wall_time_caveat": wall_caveat},
        "seed_groups": seed_groups,
        "data_quality": {
            "total_issues": total_issues,
            "by_arm": dq_by_arm,
            "pairing_issues": {
                ca["label"]: pairs_report[ca["label"]]["issues"] for ca in cand_arms
            },
            "coverage_vs_manifest": coverage,
            "pairing_issue_count": pair_issue_count,
        },
        "arms": arms_report,
        "pairs": {ca["label"]: {"by_config": pairs_report[ca["label"]]["by_config"],
                                "all_configs_pooled": pairs_report[ca["label"]]["all_configs_pooled"]}
                  for ca in cand_arms},
        "notes": notes,
    }
    return rep


def parse_candidate(spec: str):
    label, _, path = spec.partition("=")
    if not path:
        # 没有 '='：整个串是路径，标签取文件名去扩展名
        path = spec
        label = os.path.splitext(os.path.basename(spec))[0]
    label = label.strip()
    if not label:
        raise SystemExit(f"error: empty candidate label in '{spec}'")
    return label, path


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="botbench 对局结果分析：按 config/arm 胜率 + Wilson CI + 配对 McNemar 精确检验。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 analyze.py --baseline r0.json --candidate r1=r1.json --candidate r3=r3.json \\\n"
            "      --manifest run-manifest.json --out-dir observed-results/planner-ablation-2026-10-18\n"
            "\n"
            "主标准只有完整对局 victory；rounds/层数/存活只是描述性指标。"
        ),
    )
    ap.add_argument("--baseline", required=True, help="baseline JSON 文件（可写 label=path）")
    ap.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="candidate JSON，可重复（可只写 PATH，标签默认文件名）",
    )
    ap.add_argument("--manifest", default=None, help="可选实验 manifest（run-manifest.json）")
    ap.add_argument("--out-dir", default="analysis-out", help="输出目录（report.json + report.md）")
    ap.add_argument("--title", default="botbench 实验分析报告", help="报告标题")
    ap.add_argument("--jobs", type=int, default=None, help="覆盖 manifest 的 jobs 值（>1 触发墙时警示）")
    ap.add_argument("--train-seeds", default=None, help="训练 seed 集合，如 \"1-64,70\"（可选）")
    ap.add_argument("--eval-seeds", default=None, help="评测/未见 seed 集合，如 \"65-69,80\"（可选）")
    args = ap.parse_args(argv)
    args.command_argv = list(sys.argv[1:] if argv is None else argv)

    base_label, base_path = parse_candidate(args.baseline)
    args.baseline_label = base_label
    args.baseline = base_path
    args.candidates = [parse_candidate(s) for s in args.candidate]

    try:
        rep = build_report(args)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))

    os.makedirs(args.out_dir, exist_ok=True)
    json_path = os.path.join(args.out_dir, "report.json")
    md_path = os.path.join(args.out_dir, "report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(rep))

    # 控制台摘要
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print()
    print(f"baseline: {rep['baseline_label']}")
    for label, pres in rep["pairs"].items():
        print(f"candidate: {label}")
        for cfg, pr in pres["by_config"].items():
            o = pr["outcomes"]
            p = pr["mcnemar_exact"]["p_two_sided"]
            print(
                f"  {cfg}: paired={pr['n_eligible_pairs']} "
                f"base {pr['baseline_wins']}W vs cand {pr['candidate_wins']}W "
                f"(loss->win {o['loss_to_win']}, win->loss {o['win_to_loss']}, "
                f"p={'undef' if p is None else f'{p:.4g}'}) [{pr['verdict_code']}]"
            )
        pv = pres["all_configs_pooled"]
        print(f"  pooled: [{pv['verdict_code']}]" if pv else "  pooled: n/a")
    print(f"data quality issues: {rep['data_quality']['total_issues']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
