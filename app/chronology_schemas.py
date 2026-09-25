from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 年份使用 cal BP（距 1950 年），允许负无穷方向开放：用 None 表达未知
YearBP = int | None


class SiteCreate(BaseModel):
    site_key: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=120)
    region: str = Field("", max_length=120)
    latitude: float | None = None
    longitude: float | None = None


class LayerCreate(BaseModel):
    layer_key: str = Field(..., min_length=1, max_length=60)
    site_key: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=120)
    sequence_no: int | None = None


class SourceCreate(BaseModel):
    source_key: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=200)
    authors: str = Field("", max_length=200)
    citation: str = Field("", max_length=400)
    year_pub: int | None = Field(None, ge=1500, le=2100)
    note: str = Field("", max_length=1000)


class ProbabilityRange(BaseModel):
    lo: int = Field(..., ge=0, le=100000)
    hi: int = Field(..., ge=0, le=100000)
    probability: float | None = Field(None, ge=0.0, le=1.0)

    @field_validator("hi")
    @classmethod
    def _order(cls, hi: int, info) -> int:
        lo = info.data.get("lo")
        if lo is not None and hi < lo:
            raise ValueError("概率段 hi（较老）不得小于 lo（较年轻），单位 cal BP")
        return hi


class DatingEvidence(BaseModel):
    evidence_key: str = Field(..., min_length=1, max_length=60)
    layer_key: str = Field(..., min_length=1, max_length=60)
    source_key: str | None = None
    sample_code: str = Field("", max_length=80)
    method: str = Field("", max_length=40)  # AMS / OSL / typology ...
    probability: str = Field("", max_length=40)  # 如 '95.4%'，仅展示
    bound_lo: YearBP = Field(None, ge=0, le=100000)  # 较年轻边缘
    bound_hi: YearBP = Field(None, ge=0, le=100000)  # 较老边缘
    ranges: list[ProbabilityRange] = Field(default_factory=list)
    basis: str = Field("", max_length=400)
    note: str = Field("", max_length=1000)


class TypologyEvidence(BaseModel):
    evidence_key: str = Field(..., min_length=1, max_length=60)
    layer_key: str = Field(..., min_length=1, max_length=60)
    source_key: str | None = None
    artifact_type: str = Field(..., min_length=1, max_length=120)
    bound_lo: YearBP = Field(None, ge=0, le=100000)
    bound_hi: YearBP = Field(None, ge=0, le=100000)
    ranges: list[ProbabilityRange] = Field(default_factory=list)
    basis: str = Field("", max_length=400)
    note: str = Field("", max_length=1000)


class OrderingEvidence(BaseModel):
    evidence_key: str = Field(..., min_length=1, max_length=60)
    # layer_key 早于 other_layer_key（BP 轴上更老）
    layer_key: str = Field(..., min_length=1, max_length=60)
    other_layer_key: str = Field(..., min_length=1, max_length=60)
    source_key: str | None = None
    gap_years: int = Field(0, ge=0, le=100000)
    basis: str = Field("", max_length=400)
    note: str = Field("", max_length=1000)


class LabelEvidence(BaseModel):
    """暂定文化阶段标签：研究者判断，不参与数值传播。"""

    evidence_key: str = Field(..., min_length=1, max_length=60)
    layer_key: str = Field(..., min_length=1, max_length=60)
    source_key: str | None = None
    label_text: str = Field(..., min_length=1, max_length=120)
    basis: str = Field("", max_length=400)
    note: str = Field("", max_length=1000)


class SnapshotCreate(BaseModel):
    snapshot_key: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=160)
    # 为 None 时纳入项目下全部 active 证据；否则只纳入指定证据键
    evidence_keys: list[str] | None = None
    note: str = Field("", max_length=1000)


class SnapshotComputeQuery(BaseModel):
    force: bool = False


class SchemePropose(BaseModel):
    scheme_key: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=160)
    snapshot_key: str = Field(..., min_length=1, max_length=60)
    # 阶段分配：layer_key -> 暂定文化阶段代码（来自标签或研究者提议）
    stages: dict[str, str]
    approvals_required: int = Field(2, ge=1, le=6)
    note: str = Field("", max_length=1000)


class SchemeRevisionPropose(BaseModel):
    """对已发布方案创建候选修订（基于新快照）。"""

    snapshot_key: str = Field(..., min_length=1, max_length=60)
    stages: dict[str, str]
    approvals_required: int = Field(2, ge=1, le=6)
    note: str = Field("", max_length=1000)


class ReviewDecision(BaseModel):
    decision: Literal["approve", "request_changes", "comment"]
    comment: str = Field("", max_length=2000)


class SourceRetract(BaseModel):
    reason: str = Field(..., min_length=1, max_length=400)
