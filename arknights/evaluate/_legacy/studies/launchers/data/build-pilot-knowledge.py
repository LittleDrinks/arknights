"""Produce two version-pinned strategy cards from locally verified mechanisms.

This is knowledge distillation, not behaviour cloning or an expert dataset.
"""
import hashlib
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--game", type=Path, default=ROOT / "Stronghold-Protocol")
parser.add_argument("--out", type=Path, default=ROOT / "knowledge" / "distilled")
args = parser.parse_args()
GAME = args.game.resolve()
OUT = args.out.resolve()
COMMIT = "6ab2468279e926d107788f63d7fe219b85f67b4a"
VIDEO = "BV1a9wbzdE2S"


def load(path):
    return json.loads(path.read_text())


garrisons = load(GAME / "data/garrisons.json")
chess = load(GAME / "data/chess.json")
checks = load(OUT / "mechanism-checks.json")
assert checks["commit"] == COMMIT
assert len(checks["examples"]) == 5


def unit(name):
    candidates = [c for c in chess.values()
                  if c["name"] == name and c.get("visible") and not c["isGolden"]]
    assert len(candidates) == 1, name
    c = candidates[0]
    return {"name": name, "id": c["chessId"], "tier": c["tier"],
            "bonds": c["bonds"], "traits": [garrisons[t] for t in c["garrisonIds"]]}


def source(start, end):
    return {"bvid": VIDEO, "url": f"https://www.bilibili.com/video/{VIDEO}?t={start}",
            "start_seconds": start, "end_seconds": end,
            "evidence": "timestamped subtitle; full audiovisual sequence not annotated",
            "raw": f"../raw/{VIDEO}.subtitles.json"}


cards = [
    {
        "id": "pilot.siracusa-arcane.refresh",
        "name": "叙拉古＋奥术：刷新次数转层数",
        "status": "constructed_engine_fixture_verified",
        "sources": [source(79, 143)],
        "units": [unit(n) for n in ["拉普兰德", "阿罗玛", "安洁莉娜", "莫斯提马"]],
        "preconditions": [
            "叙拉古、奥术处于激活状态；阿罗玛、安洁莉娜在场上且未失效",
            "这是示例组合，不要求实际对局一定收齐四人；替换后必须重新计算激活和收益",
            "合法获得免费刷新或支付真实刷新成本；不通过设置计数器伪造训练动作",
        ],
        "phase": "PREP",
        "events": ["SERVER_REFRESH_SHOP", "SERVER_PREP_FIN"],
        "beneficiaries": ["siracusaShip", "arcaneShip"],
        "action_template": [{"t": "g.refresh"}],
        "payoff": {
            "siracusa": "4 * I(n >= 1) + min(2*n, 6) + min(4*n, 12)",
            "arcane": "min(2*n, 6)",
            "n": "本回合实际主动刷新次数；此公式仅含列出的普通干员特质",
            "examples": [{"refreshes": 1, "siracusa": 10, "arcane": 2},
                         {"refreshes": 3, "siracusa": 22, "arcane": 6},
                         {"refreshes": 6, "siracusa": 22, "arcane": 6}],
        },
        "stop_conditions": [
            "三次刷新后，上述阿罗玛和安洁莉娜的本回合收益封顶；额外刷新须以拿牌或其他收益解释",
            "钱不足、准备动作预算耗尽、核心输出/阻挡/治疗短缺时停止该宏动作",
            "叙拉古或奥术未激活时，禁用依赖相应层数收益的候选",
        ],
        "fallback": "按商店实际可用牌补当前波次战力；缺件时不强制追整套阵容",
        "version_conflicts": [
            {"claim": "视频字幕将近卫拉普兰德描述为每次主动刷新加层",
             "resolved": "每个普通副本仅在该回合自己首次见证主动刷新时加4；整备区也有效。新购或合成副本有独立计数，不能误当玩家全局只触发一次",
             "source_seconds": [104, 112], "trait": "garrison_123_a"},
        ],
        "runtime_checks": [e["name"] for e in checks["examples"] if e["name"].startswith("mixed-refresh-")] + ["lapland-per-copy-first-refresh"],
    },
    {
        "id": "pilot.yan-arcane.acquire",
        "name": "炎＋奥术：获得干员计数转层数",
        "status": "constructed_engine_fixture_verified",
        "sources": [source(410, 447)],
        "units": [unit(n) for n in ["夕", "小满", "诗怀雅", "阿罗玛"]],
        "preconditions": [
            "炎、奥术处于激活状态；夕、小满在场上且未失效",
            "有空整备位置、可购干员和真实资金；检查合成、副作用、输出需求",
            "fixture 的初始棋盘和商店是构造的，未证明自然运营可取得",
        ],
        "phase": "PREP",
        "events": ["SERVER_GAIN", "SERVER_PREP_FIN"],
        "beneficiaries": ["yanShip", "arcaneShip"],
        "action_template": [{"t": "g.buy", "slot": "legal_offer"},
                            {"t": "g.sell", "uid": "new_unit_if_not_needed"}],
        "payoff": {"yan": "3*n", "arcane": "2*n",
                   "n": "满足条件的本休整期获得干员数，仅计算夕和小满的列出特质",
                   "fixture_example": {"acquisitions": 2, "yan": 6, "arcane": 4,
                                       "funds_before": 20, "funds_after": 18}},
        "stop_conditions": [
            "每次买卖检查真实净资金；示例每次亏1资金，不能标注为电表倒转",
            "达到本波次所需战力后，再比较额外层数与升级、装备、购置核心的收益",
            "必须保留核心干员；不能为了计数卖掉正在使用的唯一关键牌",
        ],
        "fallback": "保留有战力的购入牌，或停止循环补足防线；不把所有购入牌一律卖掉",
        "version_conflicts": [
            {"claim": "自动字幕中的希、盐、奥数",
             "resolved": "按当前棋子与盟约表归一为夕、炎、奥术；只用于本条上下文"},
        ],
        "runtime_checks": ["mixed-acquire-yan-arcane"],
    },
]

result = {
    "schema_version": 1,
    "kind": "source-backed strategy knowledge; not policy weights or expert action trajectories",
    "engine_commit": COMMIT,
    "data_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [GAME / "data/chess.json", GAME / "data/garrisons.json"]},
    "evidence_levels": ["search_metadata_only", "subtitle_extracted", "source_static_checked",
                        "constructed_engine_fixture_verified", "natural_rollout_verified",
                        "heldout_abyss_success"],
    "cards": cards,
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "pilot-strategies.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"cards": len(cards), "checks": len(checks["examples"]),
                  "output": str(OUT / "pilot-strategies.json")}, ensure_ascii=False))
