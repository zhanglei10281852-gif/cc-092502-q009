"""纯引擎固定数据集测试：区间传播、开放边界、冲突诊断与可复现性。"""
from __future__ import annotations

from app.chrono import ChronologyModel, envelope_overlaps, phase_boundaries
from tests.fixtures import baojia_dataset as ds


def test_baseline_is_consistent_and_bounds_propagate():
    result = ChronologyModel(ds.engine_records()).solve()
    assert result["consistent"] is True
    assert result["conflicts"] == []
    h4, h3 = result["strata"]["BJ-H4"], result["strata"]["BJ-H3"]
    # 直接测年：x<=上界；下界<=y
    assert h4["start"] == {"earliest": None, "latest": -4800}
    assert h4["end"]["earliest"] == -5200
    # H3 起点上界同时受测年(-3900)与类型学出土(-4000)约束，取更紧的 -4000
    assert h3["start"]["latest"] == -4000
    # 层位传播：H4 严格早于 H3 ⇒ H3 的起点下界不早于 H4 终点下界 +1
    assert h3["start"]["earliest"] == -5199
    assert h4["end"]["latest"] == -4001


def test_open_boundaries_remain_unknown_without_point_estimates():
    result = ChronologyModel(ds.engine_records()).solve()
    # 塘湾 H2/H1 只有彼此先后、完全无年代锚点：四个外边界全部未知
    assert result["strata"]["TW-H2"]["start"] == {"earliest": None, "latest": None}
    assert result["strata"]["TW-H2"]["end"] == {"earliest": None, "latest": None}
    assert result["strata"]["TW-H1"]["start"] == {"earliest": None, "latest": None}
    assert result["strata"]["TW-H1"]["end"] == {"earliest": None, "latest": None}
    # 神墩 H2 通过层位链传播获得部分边界，但无下界
    h2 = result["strata"]["SD-H2"]
    assert h2["start"] == {"earliest": None, "latest": -4001}
    assert h2["end"] == {"earliest": None, "latest": -4001}


def test_probability_value_does_not_narrow_bounds():
    # 概率只登记、不参与收窄：修改概率值后边界完全不变
    records_a = ds.engine_records()
    records_b = [dict(r, probability=0.5) if r["kind"] == "dating" else r for r in records_a]
    assert ChronologyModel(records_a).solve()["strata"] == ChronologyModel(records_b).solve()["strata"]


def test_contradiction_returns_inclusion_minimal_conflict_sets():
    records = ds.engine_records(with_contradiction=True)
    model = ChronologyModel(records)
    result = model.solve()
    assert result["consistent"] is False
    # 反向层位与正向层位构成最直接的极小冲突核
    assert ["O-BJH4-BJH3", "O-REV-BJH3-BJH4"] in result["conflicts"]
    for core in result["conflicts"]:
        active_core = frozenset(core)
        # 核本身不相容
        assert model.consistent(active_core) is False
        # 包含极小性：删掉核内任一证据后，该核即相容
        for member in core:
            assert model.consistent(active_core - {member}) is True


def test_ordering_cycle_without_dates_is_detected():
    records = [
        {"kind": "ordering", "evidence": "O1", "before": "A", "after": "B", "gap_years": 0},
        {"kind": "ordering", "evidence": "O2", "before": "B", "after": "C", "gap_years": 0},
        {"kind": "ordering", "evidence": "O3", "before": "C", "after": "A", "gap_years": 0},
    ]
    result = ChronologyModel(records).solve()
    assert result["consistent"] is False
    assert sorted(result["conflicts"][0]) == ["O1", "O2", "O3"]


def test_reproducible_results_are_input_order_independent():
    import json
    records = ds.engine_records()
    first = json.dumps(ChronologyModel(records).solve(), sort_keys=True, ensure_ascii=False)
    second = json.dumps(ChronologyModel(list(reversed(records))).solve(), sort_keys=True, ensure_ascii=False)
    assert first == second


def test_phase_boundaries_aggregate_unknowns_safely():
    result = ChronologyModel(ds.engine_records()).solve()
    bounds = phase_boundaries(ds.PHASES_V1, result["strata"])
    early, late = bounds["崧泽早期"], bounds["崧泽晚期"]
    # 早期仅 H4：start=[None,-4800]，end=[-5200, 传播上界 -4001]
    assert early["start"] == {"earliest": None, "latest": -4800}
    assert early["end"] == {"earliest": -5200, "latest": -4001}
    # 晚期含未充分锚定的 SD-H2：下界未知使并集包络下界未知，绝不以 H3 数值替代
    assert late["start"] == {"earliest": None, "latest": -4001}
    assert late["end"] == {"earliest": None, "latest": None}
    # 一端已知仍可排除明显不相交：H4 终点上界 -4001 < -3000
    assert envelope_overlaps({"start": early["start"], "end": early["end"]}, -3000, None) is False
    # 任一端点未知时保守保留（不虚构、不武断剔除）
    assert envelope_overlaps(late, -3000, None) is True
    unknown = {"start": {"earliest": None, "latest": None}, "end": {"earliest": None, "latest": None}}
    assert envelope_overlaps(unknown, -100, 100) is True


def test_labels_never_enter_constraints():
    result = ChronologyModel(ds.engine_records()).solve()
    labeled = result["strata"]["BJ-H4"]
    stripped_records = [r for r in ds.engine_records() if r["kind"] != "label_assignment"]
    stripped = ChronologyModel(stripped_records).solve()
    assert stripped["strata"]["BJ-H4"]["start"] == labeled["start"]
    assert stripped["strata"]["BJ-H4"]["end"] == labeled["end"]
