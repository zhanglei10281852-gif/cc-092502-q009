"""区域年代序列纯函数引擎。

建模约定（与存储/接口层共享，变更须同步文档）：

* 每个文化层 S 是一个时间区间，起点变量 ``x_S``、终点变量 ``y_S``，恒有 ``x_S <= y_S``。
* 年份使用有符号整数日历年：公元前为负（如 -3000 表示公元前 3001 年的近似日历年），
  开放边界用 ``None`` 表示"未知"，引擎在任何情况下都不会补出点估计。
* 测年概率区间与类型学出现范围按"可相交"解释：证据只断言文化层形成时间与该区间
  存在交点，不断言整个文化层被区间覆盖，也不依据概率值进一步收窄边界。
* 层位先后（推断关系）：A 严格早于 B（至少间隔 gap 年，缺省按 1 年）等价于
  ``y_A + max(gap,1) <= x_B``；gap=0 表示"确有先后但不知间隔"，不允许同时成立反向关系。

所有约束均为差分约束 ``u + c <= v``，在整数界上做不动点传播；不相容时按证据来源
枚举包含极小冲突核（inclusion-minimal unsat cores）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# 超过该数量后停止枚举更多冲突核，避免病态输入下组合爆炸
MAX_CONFLICT_CORES = 24

X = "x"  # 文化层起点
Y = "y"  # 文化层终点
Z = "Z"  # 恒为 0 的常量节点


@dataclass(frozen=True)
class Constraint:
    u: str  # 变量名
    c: int  # 常量偏移
    v: str  # 变量名，语义：u + c <= v
    origin: str | None  # 证据编码；结构性约束为 None


def _v(kind: str, stratum: str) -> str:
    return f"{kind}:{stratum}"


class ChronologyModel:
    """根据普通字典记录构建并求解差分约束系统。"""

    def __init__(self, records: Iterable[dict[str, Any]]):
        self.strata: set[str] = set()
        self.constraints: list[Constraint] = []
        # artifact_type -> (lower, upper, range_evidence_code)
        self.type_ranges: dict[str, tuple[int | None, int | None, str]] = {}
        # 出土证据编码 -> 支撑其时间含义的类型学范围证据编码
        self.find_dependencies: dict[str, str] = {}
        self.dating_by_stratum: dict[str, list[str]] = {}
        self.finds_by_stratum: dict[str, list[str]] = {}
        self.orders: list[dict[str, Any]] = []
        self.labels: dict[str, list[dict[str, str]]] = {}
        # 两遍摄取：类型学范围可能与出土事实以任意顺序登记，必须先建立全部范围再处理出土，
        # 否则求解结果会依赖输入顺序（破坏可复现性）。
        records = list(records)
        for record in records:
            if record["kind"] == "typology_range":
                self.type_ranges[record["artifact_type"]] = (
                    record.get("lower_year"), record.get("upper_year"), record["evidence"],
                )
        for record in records:
            self._ingest(record)
        self._freeze_structure()

    def _touch(self, stratum: str) -> None:
        self.strata.add(stratum)

    def _ingest(self, record: dict[str, Any]) -> None:
        kind = record["kind"]
        if kind == "dating":
            stratum = record["stratum"]
            self._touch(stratum)
            self.dating_by_stratum.setdefault(stratum, []).append(record["evidence"])
            lo, hi = record.get("lower_year"), record.get("upper_year")
            if lo is not None:
                # Z + lo <= y_S：样品区间下界不晚于文化层结束
                self.constraints.append(Constraint(Z, int(lo), _v(Y, stratum), record["evidence"]))
            if hi is not None:
                # x_S - hi <= Z：文化层开始不晚于样品区间上界
                self.constraints.append(Constraint(_v(X, stratum), -int(hi), Z, record["evidence"]))
        elif kind == "typology_range":
            self.type_ranges[record["artifact_type"]] = (
                record.get("lower_year"),
                record.get("upper_year"),
                record["evidence"],
            )
        elif kind == "artifact_find":
            stratum = record["stratum"]
            self._touch(stratum)
            self.finds_by_stratum.setdefault(stratum, []).append(record["evidence"])
            bounds = self.type_ranges.get(record["artifact_type"])
            if bounds is not None:
                lo, hi, range_evidence = bounds
                # 约束由出土事实触发；类型学范围是其知识依赖（见 find_dependencies）
                self.find_dependencies[record["evidence"]] = range_evidence
                if lo is not None:
                    self.constraints.append(Constraint(Z, int(lo), _v(Y, stratum), record["evidence"]))
                if hi is not None:
                    self.constraints.append(Constraint(_v(X, stratum), -int(hi), Z, record["evidence"]))
        elif kind == "ordering":
            before, after = record["before"], record["after"]
            self._touch(before)
            self._touch(after)
            raw_gap = int(record.get("gap_years", 0))
            gap = max(raw_gap, 1)
            self.constraints.append(Constraint(_v(Y, before), gap, _v(X, after), record["evidence"]))
            self.orders.append({"evidence": record["evidence"], "before": before, "after": after, "gap_years": raw_gap, "strict": True})
        elif kind == "label_assignment":
            stratum = record["stratum"]
            self._touch(stratum)
            self.labels.setdefault(stratum, []).append({"evidence": record["evidence"], "label": record["label_text"]})

    def _freeze_structure(self) -> None:
        # 结构性约束：每个文化层 x_S <= y_S，始终存在，不计入证据冲突
        structural = [Constraint(_v(X, s), 0, _v(Y, s), None) for s in sorted(self.strata)]
        self.constraints = structural + self.constraints
        self.variables = sorted({Z, *(c.u for c in self.constraints), *(c.v for c in self.constraints)})
        self.origins = sorted({c.origin for c in self.constraints if c.origin is not None})

    def _feasible(self, active_origins: frozenset[str] | None) -> bool:
        """差分约束可行性：隐式超级源（所有节点距离初值 0）上的最长路 Bellman-Ford。

        约束 u+c<=v 即可行图上权为 c 的边 u→v；存在正环即不相容。
        该判定不依赖任何年代锚点，因此"无测年但层位关系自相矛盾"也能检出。
        """
        edges = [c for c in self.constraints if active_origins is None or c.origin is None or c.origin in active_origins]
        dist = {name: 0 for name in self.variables}
        for _ in range(len(self.variables)):
            changed = False
            for con in edges:
                candidate = dist[con.u] + con.c
                if candidate > dist[con.v]:
                    dist[con.v] = candidate
                    changed = True
            if not changed:
                return True
        for con in edges:
            if dist[con.u] + con.c > dist[con.v]:
                return False
        return True

    def _bounds(self) -> dict[str, tuple[int | None, int | None]]:
        # 可行系统中无正环，前向最长路（下界）与反向最长路（上界）都存在不动点。
        lower: dict[str, int | None] = {name: None for name in self.variables}
        upper: dict[str, int | None] = {name: None for name in self.variables}
        lower[Z] = upper[Z] = 0
        for _ in range(len(self.variables)):
            changed = False
            for con in self.constraints:
                if lower[con.u] is not None:
                    candidate = lower[con.u] + con.c
                    if lower[con.v] is None or candidate > lower[con.v]:
                        lower[con.v], changed = candidate, True
                if upper[con.v] is not None:
                    candidate = upper[con.v] - con.c
                    if upper[con.u] is None or candidate < upper[con.u]:
                        upper[con.u], changed = candidate, True
            if not changed:
                break
        return {name: (lower[name], upper[name]) for name in self.variables}

    def _solve(self, active_origins: frozenset[str] | None = None) -> tuple[bool, dict[str, tuple[int | None, int | None]]]:
        if not self._feasible(active_origins):
            return False, {name: (None, None) for name in self.variables}
        return True, self._bounds()

    def consistent(self, active_origins: frozenset[str] | None = None) -> bool:
        return self._solve(active_origins)[0]

    def _minimal_core(self, active: list[str]) -> frozenset[str]:
        """删除过滤：遍历证据，删后仍不相容则永久删除，剩下的是极小冲突核。"""
        current = set(active)
        for origin in active:
            if origin not in current:
                continue
            trial = frozenset(current - {origin})
            if not self.consistent(trial):
                current.discard(origin)
        return frozenset(current)

    def all_minimal_conflicts(self) -> list[list[str]]:
        if self.consistent(frozenset(self.origins)):
            return []
        found: set[frozenset[str]] = set()

        def search(active: frozenset[str]) -> None:
            if len(found) >= MAX_CONFLICT_CORES or self.consistent(active):
                return
            if any(core <= active for core in found):
                return
            core = self._minimal_core(sorted(active))
            found.add(core)
            # 任何其他极小核都不可能包含本核全部元素；按核中元素逐个排除后递归
            for origin in sorted(core):
                search(active - {origin})

        search(frozenset(self.origins))
        return [sorted(core) for core in sorted(found, key=lambda item: sorted(item))]

    def solve(self) -> dict[str, Any]:
        consistent, bounds = self._solve(frozenset(self.origins))
        conflicts = [] if consistent else self.all_minimal_conflicts()
        strata_out: dict[str, Any] = {}
        for stratum in sorted(self.strata):
            xlo, xhi = bounds[_v(X, stratum)]
            ylo, yhi = bounds[_v(Y, stratum)]
            strata_out[stratum] = {
                "start": {"earliest": xlo, "latest": xhi},
                "end": {"earliest": ylo, "latest": yhi},
                "dating_evidence": sorted(self.dating_by_stratum.get(stratum, [])),
                "find_evidence": sorted(self.finds_by_stratum.get(stratum, [])),
                "provisional_labels": sorted(self.labels.get(stratum, []), key=lambda item: item["evidence"]),
            }
        return {
            "consistent": consistent,
            "calendar": "signed integer calendar year (BCE negative); null means unknown",
            "strata": strata_out,
            "ordering": sorted(self.orders, key=lambda item: item["evidence"]),
            "type_ranges": {
                name: {"lower_year": lo, "upper_year": hi, "source_evidence": code}
                for name, (lo, hi, code) in sorted(self.type_ranges.items())
            },
            "find_dependencies": dict(sorted(self.find_dependencies.items())),
            "conflicts": conflicts,
        }


def _union_endpoint(values: list[int | None], *, choose) -> int | None:
    """阶段边界是其文化层时间可行域并集的包络：任一成员边界未知，则包络未知。"""
    if any(v is None for v in values):
        return None
    return choose(values)


def phase_boundaries(phases: list[dict[str, Any]], strata_bounds: dict[str, Any]) -> dict[str, Any]:
    """汇总阶段的并集跨度：起点取最早成员起点、终点取最晚成员终点。

    即 X=min_s x_s，Y=max_s y_s；任一成员对应端点未知（None），则该包络端点未知，
    绝不以其他成员的已知值替代。
    """
    out: dict[str, Any] = {}
    for phase in phases:
        members = [strata_bounds[s] for s in phase["stratum_codes"] if s in strata_bounds]
        out[phase["label"]] = {
            "start": {
                "earliest": _union_endpoint([b["start"]["earliest"] for b in members], choose=min),
                "latest": _union_endpoint([b["start"]["latest"] for b in members], choose=min),
            },
            "end": {
                "earliest": _union_endpoint([b["end"]["earliest"] for b in members], choose=max),
                "latest": _union_endpoint([b["end"]["latest"] for b in members], choose=max),
            },
        }
    return out


def envelope_overlaps(envelope: dict[str, Any], from_year: int | None, to_year: int | None) -> bool:
    """未知边界视为不能排除相交（保守保留），绝不因未知而剔除遗址。"""
    lo = envelope["start"]["earliest"]
    hi = envelope["end"]["latest"]
    if from_year is not None and hi is not None and hi < from_year:
        return False
    if to_year is not None and lo is not None and lo > to_year:
        return False
    return True
