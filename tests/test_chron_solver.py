"""纯求解器测试：区间传播、开放边界、最小冲突诊断、可复现性。"""
from __future__ import annotations

import json

from app.chronology.solver import (
    ALGORITHM_VERSION,
    build_constraints,
    is_satisfiable,
    minimal_conflict,
    result_hash,
    solve,
)


def E(key, kind, layer, other=None, lo=None, hi=None, ranges=None, gap=0):
    return {"key": key, "kind": kind, "category": {"dating": "observation", "typology": "observation",
            "ordering": "inference", "label": "tentative_label"}[kind],
            "layer_key": layer, "other_layer_key": other,
            "bound_lo": lo, "bound_hi": hi, "ranges": ranges or [], "gap_years": gap}


def test_open_boundaries_stay_null():
    result = solve([E("o1", "ordering", "A", "B")], ["A", "B"])
    assert result["satisfiable"]
    for layer in result["layers"]:
        assert layer["start_bp"] == {"youngest": None, "oldest": None}
        assert layer["end_bp"] == {"youngest": None, "oldest": None}


def test_missing_dates_never_become_point_estimates():
    # 只有 A 有测年；顺序约束只能传播一侧边界，B 的另一侧保持未知
    result = solve([E("dA", "dating", "A", lo=4800, hi=5000), E("o", "ordering", "A", "B")], ["A", "B"])
    b = result["layers"][1]
    assert b["start_bp"]["youngest"] is None
    assert b["start_bp"]["oldest"] == 4800
    assert b["end_bp"] == {"youngest": None, "oldest": 4800}


def test_interval_propagation_through_chain():
    evidence = [
        E("dA", "dating", "A", lo=4800, hi=5000),
        E("dB", "dating", "B", lo=4400, hi=4600),
        E("oAB", "ordering", "A", "B"),
    ]
    result = solve(evidence, ["A", "B"])
    a, b = result["layers"]
    assert a["start_bp"] == {"youngest": 5000, "oldest": None}
    assert a["end_bp"] == {"youngest": 4600, "oldest": 4800}
    assert b["start_bp"] == {"youngest": 4600, "oldest": 4800}
    assert b["end_bp"] == {"youngest": None, "oldest": 4400}


def test_gap_tightens_bounds():
    evidence = [
        E("dA", "dating", "A", lo=4800, hi=5000),
        E("dB", "dating", "B", lo=4400, hi=4600),
        E("oAB", "ordering", "A", "B", gap=200),
    ]
    result = solve(evidence, ["A", "B"])
    a, b = result["layers"]
    # A 结束至少 4600+200；B 开始至多 4800-200
    assert a["end_bp"]["youngest"] == 4800
    assert b["start_bp"]["oldest"] == 4600


def test_probability_ranges_envelope_used():
    ev = E("dA", "dating", "A", ranges=[{"lo": 5900, "hi": 6150}, {"lo": 6200, "hi": 6300}])
    result = solve([ev], ["A"])
    layer = result["layers"][0]
    assert layer["start_bp"]["youngest"] == 6300
    assert layer["end_bp"]["oldest"] == 5900


def test_conflict_returns_minimal_set():
    evidence = [
        E("dA", "dating", "A", lo=4800, hi=5000),
        E("dB", "dating", "B", lo=4400, hi=4600),
        E("oBA", "ordering", "B", "A"),  # 与测年方向相反
    ]
    result = solve(evidence, ["A", "B"])
    assert result["satisfiable"] is False
    conflict = result["conflicts"][0]
    assert conflict["evidence_keys"] == ["dA", "dB", "oBA"]
    # 最小性：整体不相容；删去任一证据即相容
    constraints = build_constraints(evidence, ["A", "B"])
    assert not is_satisfiable(constraints)
    for key in conflict["evidence_keys"]:
        subset = [c for c in constraints if c.evidence_key != key]
        assert is_satisfiable(subset), key


def test_minimal_core_excludes_irrelevant_evidence():
    base = [
        E("dA", "dating", "A", lo=4800, hi=5000),
        E("dB", "dating", "B", lo=4400, hi=4600),
        E("oBA", "ordering", "B", "A"),
        E("dX", "dating", "A", lo=4900, hi=4950),  # 可替代 dA 参与另一极小冲突
        E("dC", "dating", "C", lo=3000, hi=3200),  # 完全无关
    ]
    result = solve(base, ["A", "B", "C"])
    core = set(result["conflicts"][0]["evidence_keys"])
    assert "dC" not in core
    constraints = build_constraints(base, ["A", "B", "C"])

    def sat(keys):
        return is_satisfiable([c for c in constraints
                               if c.evidence_key == "__intrinsic__" or c.evidence_key in keys])

    assert not sat(core)                       # 冲突集本身不相容
    for key in core:
        assert sat(core - {key}), key         # 删任一证据即相容 -> 包含最小


def test_labels_do_not_propagate():
    without = solve([E("dA", "dating", "A", lo=4800, hi=5000)], ["A"])
    with_label = solve([
        E("dA", "dating", "A", lo=4800, hi=5000),
        E("lbl", "label", "A"),
    ], ["A"])
    assert with_label["result_hash"] == without["result_hash"]


def test_result_is_deterministic():
    evidence = [
        E("oAB", "ordering", "A", "B"),
        E("dB", "dating", "B", lo=4400, hi=4600),
        E("dA", "dating", "A", lo=4800, hi=5000),
    ]
    # 证据给出顺序不同，但快照固定层序一致 -> 结果逐位相同
    first = solve(evidence, ["A", "B"])
    second = solve(list(reversed(evidence)), ["A", "B"])
    assert first == second
    # 同一算法版本与输入，哈希可独立复算
    payload = {k: v for k, v in first.items() if k != "result_hash"}
    assert result_hash(payload) == first["result_hash"]
    assert first["algorithm"] == ALGORITHM_VERSION
    # 结果 JSON 可稳定序列化
    json.dumps(first, ensure_ascii=False)
