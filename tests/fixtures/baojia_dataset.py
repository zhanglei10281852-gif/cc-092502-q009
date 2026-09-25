"""固定区域数据集：鲍家遗址及相邻遗址的年代序列证据。

该数据集同时用于：

* 纯引擎测试（``engine_records``）；
* 端到端 API/服务测试（按顺序登记名录与证据）。

年份约定：有符号日历年，公元前为负；``None`` 表示开放（未知）边界。
三类信息分离：observation（测年/类型学范围/出土）、inference（层位先后）、label（暂定标签）。
"""
from __future__ import annotations

from typing import Any

REGION = {"code": "TAIHU-W", "name": "太湖西部"}
SITES = [
    {"code": "BJ", "name": "鲍家遗址"},
    {"code": "SD", "name": "神墩遗址"},
    {"code": "TW", "name": "塘湾遗址"},
]
STRATA: dict[str, list[dict[str, str]]] = {
    "BJ": [
        {"code": "BJ-H4", "name": "鲍家第4层（下文化层）"},
        {"code": "BJ-H3", "name": "鲍家第3层（上文化层）"},
    ],
    "SD": [
        {"code": "SD-H2", "name": "神墩第2层"},
    ],
    "TW": [
        {"code": "TW-H2", "name": "塘湾第2层（未测年）"},
        {"code": "TW-H1", "name": "塘湾第1层（未测年）"},
    ],
}
CITATIONS = [
    {"cite_key": "BAOJIA-2014", "title": "溧阳鲍家遗址发掘简报", "author": "南京博物院", "published_year": 2014},
    {"cite_key": "TAIHU-TYPO-2009", "title": "太湖西部新石器时代陶器类型学研究", "author": "区域考古课题组", "published_year": 2009},
]

# 固定、相容的基线证据集
EVIDENCE: list[dict[str, Any]] = [
    {"kind": "dating", "code": "D-C14-BJH4", "stratum": "BJ-H4", "method": "碳十四（炭化稻）",
     "probability": 0.95, "lower_year": -5200, "upper_year": -4800, "citation": "BAOJIA-2014"},
    {"kind": "dating", "code": "D-C14-BJH3", "stratum": "BJ-H3", "method": "碳十四（炭化木屑）",
     "probability": 0.95, "lower_year": -4300, "upper_year": -3900, "citation": "BAOJIA-2014"},
    {"kind": "typology_range", "code": "T-DINGGELEI", "artifact_type": "鼎式鬲",
     "lower_year": -4500, "upper_year": -4000, "citation": "TAIHU-TYPO-2009"},
    {"kind": "artifact_find", "code": "F-BJH3-DGL", "stratum": "BJ-H3", "artifact_type": "鼎式鬲",
     "citation": "BAOJIA-2014"},
    {"kind": "ordering", "code": "O-BJH4-BJH3", "before": "BJ-H4", "after": "BJ-H3", "gap_years": 0,
     "citation": "BAOJIA-2014"},
    {"kind": "label_assignment", "code": "L-BJH4-SZ-E", "stratum": "BJ-H4", "label_text": "崧泽文化早期（暂定）",
     "citation": "TAIHU-TYPO-2009"},
    {"kind": "label_assignment", "code": "L-BJH3-SZ-L", "stratum": "BJ-H3", "label_text": "崧泽文化晚期（暂定）",
     "citation": "TAIHU-TYPO-2009"},
    # 神墩遗址通过层位链接入鲍家测年网络：边界由传播得出，但本身不直接测年
    {"kind": "ordering", "code": "O-SDH2-BJH3", "before": "SD-H2", "after": "BJ-H3", "gap_years": 0,
     "citation": "BAOJIA-2014"},
    # 塘湾遗址两条文化层只有彼此先后、完全没有年代锚点：四个外边界必须全部保持未知
    {"kind": "ordering", "code": "O-TWH2-TWH1", "before": "TW-H2", "after": "TW-H1", "gap_years": 0,
     "citation": "BAOJIA-2014"},
]

# 追加后使材料不相容的反向层位关系（错误录入/争议材料）
CONTRADICTION_EVIDENCE = {
    "kind": "ordering", "code": "O-REV-BJH3-BJH4", "before": "BJ-H3", "after": "BJ-H4",
    "gap_years": 0, "citation": "BAOJIA-2014",
}

# 候选修订使用的新证据：BJ-H3 新测年，把上界收窄
REVISION_EVIDENCE = {
    "kind": "dating", "code": "D-C14-BJH3-NEW", "stratum": "BJ-H3", "method": "碳十四（炭化种子，新实验室）",
    "probability": 0.95, "lower_year": -4200, "upper_year": -4100, "citation": "BAOJIA-2014",
}

PHASES_V1 = [
    {"label": "崧泽早期", "stratum_codes": ["BJ-H4"], "supporting_evidence": ["D-C14-BJH4", "L-BJH4-SZ-E"]},
    {"label": "崧泽晚期", "stratum_codes": ["BJ-H3", "SD-H2"], "supporting_evidence": ["D-C14-BJH3", "F-BJH3-DGL", "L-BJH3-SZ-L"]},
]
PHASES_V2 = [
    {"label": "崧泽早期", "stratum_codes": ["BJ-H4"], "supporting_evidence": ["D-C14-BJH4", "L-BJH4-SZ-E"]},
    {"label": "崧泽晚期", "stratum_codes": ["BJ-H3", "SD-H2"],
     "supporting_evidence": ["D-C14-BJH3", "D-C14-BJH3-NEW", "F-BJH3-DGL", "L-BJH3-SZ-L"]},
]


def engine_records(*, with_contradiction: bool = False, with_revision: bool = False) -> list[dict[str, Any]]:
    """供纯引擎测试使用的记录（code 即标识，无需数据库）。"""
    def convert(item: dict[str, Any]) -> dict[str, Any]:
        record = {k: v for k, v in item.items() if k != "citation"}
        record["evidence"] = record.pop("code")
        return record

    records = [convert(item) for item in EVIDENCE]
    if with_contradiction:
        records.append(convert(CONTRADICTION_EVIDENCE))
    if with_revision:
        records.append(convert(REVISION_EVIDENCE))
    return records
