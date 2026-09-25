"""年代约束传播与冲突诊断（纯函数、确定性）。

时间单位为 cal BP 整数年（距 1950 年），数值越大越早。
每个文化层有两个变量：
- start：层位起始（较早，较大值）；
- end：层位结束（较晚，较小值）。

所有约束均为 x + c <= y 形式的差分约束或单变量边界约束，
用边界传播求不动点。传播只表达"由证据必然推出的范围"，
开放边界保持 None（未知），绝不生成点估计。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

ALGORITHM_VERSION = "bounds-v1"

# 层位内部约束的虚拟证据键，不参与对外冲突集合
INTRINSIC_KEY = "__intrinsic__"


@dataclass(frozen=True)
class Unary:
    evidence_key: str
    var: str
    bound: str  # 'lo' 表示下界（>= value），'hi' 表示上界（<= value）
    value: int


@dataclass(frozen=True)
class Diff:
    evidence_key: str
    x: str
    y: str
    c: int  # x + c <= y


@dataclass
class SolverInput:
    # var -> [lo, hi]，None 表示该方向开放
    constraints: list[Unary | Diff] = field(default_factory=list)


def vars_for_layer(layer_key: str) -> tuple[str, str]:
    return (f"{layer_key}#end", f"{layer_key}#start")


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def propagate(constraints: list[Unary | Diff]) -> tuple[dict[str, list[int | None]], dict[str, list[list[str]]] | None, str | None]:
    """边界传播到不动点。

    返回 (bounds, provenance, contradiction_var)：
    每个变量 bounds[var] = [lo, hi]（None 为开放）；
    provenance 记录每条边界推导链路上的证据键（有序去重）；
    若发现某变量 lo > hi，返回该变量名，调用方可据此诊断。
    """
    bounds: dict[str, list[int | None]] = {}
    prov: dict[str, list[list[str]]] = {}

    def touch(var: str) -> None:
        if var not in bounds:
            bounds[var] = [None, None]
            prov[var] = [[], []]

    for cons in constraints:
        if isinstance(cons, Unary):
            touch(cons.var)
        else:
            touch(cons.x)
            touch(cons.y)

    unaries = [c for c in constraints if isinstance(c, Unary)]
    diffs = [c for c in constraints if isinstance(c, Diff)]

    def set_bound(var: str, idx: int, value: int, chain: list[str]) -> bool:
        current = bounds[var][idx]
        tighter = current is None or (idx == 0 and value > current) or (idx == 1 and value < current)
        if tighter:
            bounds[var][idx] = value
            merged = list(dict.fromkeys([*prov[var][idx], *chain]))
            prov[var][idx] = merged
            return True
        return False

    changed = True
    while changed:
        changed = False
        for cons in unaries:
            if set_bound(cons.var, 0 if cons.bound == "lo" else 1, cons.value, [cons.evidence_key]):
                changed = True
        for cons in diffs:
            # x + c <= y
            lo_x = bounds[cons.x][0]
            if lo_x is not None:
                # lo_y >= lo_x + c
                if set_bound(cons.y, 0, lo_x + cons.c, [*prov[cons.x][0], cons.evidence_key]):
                    changed = True
            hi_y = bounds[cons.y][1]
            if hi_y is not None:
                # hi_x <= hi_y - c
                if set_bound(cons.x, 1, hi_y - cons.c, [*prov[cons.y][1], cons.evidence_key]):
                    changed = True

    contradiction = None
    for var, (lo, hi) in bounds.items():
        if lo is not None and hi is not None and lo > hi:
            contradiction = var
            break
    return bounds, prov, contradiction


def is_satisfiable(constraints: list[Unary | Diff]) -> bool:
    _, _, bad = propagate(constraints)
    return bad is None


def minimal_conflict(constraints: list[Unary | Diff]) -> list[str]:
    """返回关于证据键的包含最小冲突集合（删除法）。

    层位内部约束（__intrinsic__）始终保留、不出现在结果中。
    """
    _, _, bad = propagate(constraints)
    if bad is None:
        return []
    evidence_keys = list(dict.fromkeys(
        c.evidence_key for c in constraints if c.evidence_key != INTRINSIC_KEY
    ))
    core = set(evidence_keys)
    for key in evidence_keys:
        trial = {e for e in core if e != key}
        subset = [c for c in constraints if c.evidence_key == INTRINSIC_KEY or c.evidence_key in trial]
        if not is_satisfiable(subset):
            core.discard(key)
    return sorted(k for k in core if k != INTRINSIC_KEY)


def build_constraints(evidence: list[dict[str, Any]], layer_keys: list[str] | None = None) -> list[Unary | Diff]:
    """把规范化后的证据字典转成约束。

    证据字典字段（由服务层从数据库行装配）：
    key, kind, category, layer_key, other_layer_key,
    bound_lo, bound_hi, ranges_json(list), gap_years
    """
    constraints: list[Unary | Diff] = []
    known = set(layer_keys or [])
    for ev in evidence:
        known.add(ev["layer_key"])
        if ev.get("other_layer_key"):
            known.add(ev["other_layer_key"])
    for layer_key in sorted(known):
        end_v, start_v = vars_for_layer(layer_key)
        constraints.append(Diff(INTRINSIC_KEY, end_v, start_v, 0))  # end <= start

    for ev in sorted(evidence, key=lambda e: e["key"]):
        key = ev["key"]
        kind = ev["kind"]
        if kind == "label":
            continue  # 暂定标签不参与数值传播
        end_v, start_v = vars_for_layer(ev["layer_key"])
        if kind in ("dating", "typology"):
            younger, older = envelope(ev)
            if older is not None:
                # 层位起始（较早边缘）不得年轻于证据最老值
                constraints.append(Unary(key, start_v, "lo", older))
            if younger is not None:
                # 层位结束（较晚边缘）不得老于证据最轻值
                constraints.append(Unary(key, end_v, "hi", younger))
        elif kind == "ordering":
            other_end, other_start = vars_for_layer(ev["other_layer_key"])
            gap = int(ev.get("gap_years") or 0)
            # layer_key 早于 other_layer_key（BP 轴）：
            # 较晚层的起始 + gap <= 较早层的结束
            constraints.append(Diff(key, other_start, end_v, gap))
    return constraints


def envelope(ev: dict[str, Any]) -> tuple[int | None, int | None]:
    """取一条测年/类型学证据的外包区间，返回 (youngest, oldest) cal BP。

    优先使用显式 bound_lo/bound_hi，并与 ranges_json 各概率段取外包：
    youngest = 所有下界中最小（最轻）者；oldest = 所有上界中最大（最老）者。
    概率字段仅用于展示与加权解释，不改变硬约束传播。
    """
    lowers: list[int] = []
    uppers: list[int] = []
    if ev.get("bound_lo") is not None:
        lowers.append(int(ev["bound_lo"]))
    if ev.get("bound_hi") is not None:
        uppers.append(int(ev["bound_hi"]))
    for rng in ev.get("ranges") or []:
        if rng.get("lo") is not None:
            lowers.append(int(rng["lo"]))
        if rng.get("hi") is not None:
            uppers.append(int(rng["hi"]))
    youngest = min(lowers) if lowers else None
    oldest = max(uppers) if uppers else None
    return youngest, oldest


def solve(evidence: list[dict[str, Any]], layer_order: list[str]) -> dict[str, Any]:
    """对一份固定证据快照求解，返回可复现的结果字典。"""
    constraints = build_constraints(evidence, layer_order)
    bounds, prov, bad = propagate(constraints)

    def interval(var: str) -> dict[str, int | None]:
        lo, hi = bounds.get(var, [None, None])
        # BP 轴：数值小=年轻（youngest），数值大=老（oldest）；None 表示开放/未知
        return {"youngest": lo, "oldest": hi}

    layers_out: list[dict[str, Any]] = []
    for layer_key in layer_order:
        end_v, start_v = vars_for_layer(layer_key)
        layers_out.append({
            "layer_key": layer_key,
            "start_bp": interval(start_v),  # 较早边缘
            "end_bp": interval(end_v),      # 较晚边缘
            "known": start_v in bounds or end_v in bounds,
        })

    result: dict[str, Any] = {
        "algorithm": ALGORITHM_VERSION,
        "satisfiable": bad is None,
        "layers": layers_out,
        "conflicts": [],
    }
    if bad is not None:
        lo, hi = bounds[bad]
        core = minimal_conflict(constraints)
        chain = sorted((set(prov[bad][0]) | set(prov[bad][1])) - {INTRINSIC_KEY})
        result["conflicts"] = [{
            "variable": bad,
            "youngest": lo,
            "oldest": hi,
            "evidence_keys": core,
            "evidence_chain": chain,
            "message": f"变量 {bad} 要求 >= {lo} 且 <= {hi} cal BP，区间倒置，相关证据无法同时成立",
        }]
    result["result_hash"] = result_hash(result)
    return result


def result_hash(result: dict[str, Any]) -> str:
    payload = {k: v for k, v in result.items() if k != "result_hash"}
    return hashlib.sha256(_stable(payload).encode("utf-8")).hexdigest()[:16]
