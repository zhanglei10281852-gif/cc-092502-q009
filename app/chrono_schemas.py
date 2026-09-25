"""区域年代序列模块的请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RegionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=120)


class SiteCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=120)
    region_id: int | None = None
    location_note: str = Field("", max_length=400)


class StratumCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=120)
    note: str = Field("", max_length=800)


class CitationCreate(BaseModel):
    cite_key: str = Field(..., min_length=1, max_length=80)
    title: str = Field("", max_length=300)
    author: str = Field("", max_length=200)
    published_year: int | None = Field(None, ge=-10000, le=2100)


class _EvidenceBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=60)
    citation_id: int | None = None
    supersedes_code: str = Field("", max_length=60, description="被本证据更正的旧证据编码；旧证据不删除")
    note: str = Field("", max_length=1000)


class DatingEvidence(_EvidenceBase):
    kind: Literal["dating"] = "dating"
    stratum_id: int
    method: str = Field(..., min_length=1, max_length=60)
    probability: float | None = Field(None, ge=0.0, le=1.0)
    lower_year: int | None = Field(None, ge=-100000, le=2100)
    upper_year: int | None = Field(None, ge=-100000, le=2100)

    @model_validator(mode="after")
    def _check(self):
        if self.lower_year is not None and self.upper_year is not None and self.lower_year > self.upper_year:
            raise ValueError("lower_year 不得晚于 upper_year")
        return self


class TypologyRangeEvidence(_EvidenceBase):
    kind: Literal["typology_range"] = "typology_range"
    artifact_type: str = Field(..., min_length=1, max_length=120)
    lower_year: int | None = Field(None, ge=-100000, le=2100)
    upper_year: int | None = Field(None, ge=-100000, le=2100)

    @model_validator(mode="after")
    def _check(self):
        if self.lower_year is not None and self.upper_year is not None and self.lower_year > self.upper_year:
            raise ValueError("lower_year 不得晚于 upper_year")
        return self


class ArtifactFindEvidence(_EvidenceBase):
    kind: Literal["artifact_find"] = "artifact_find"
    stratum_id: int
    artifact_type: str = Field(..., min_length=1, max_length=120)


class OrderingEvidence(_EvidenceBase):
    kind: Literal["ordering"] = "ordering"
    site_id: int | None = None
    subject_stratum_id: int  # 较早文化层
    object_stratum_id: int  # 较晚文化层
    gap_years: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _check(self):
        if self.subject_stratum_id == self.object_stratum_id:
            raise ValueError("先后关系的两个文化层不能相同")
        return self


class LabelEvidence(_EvidenceBase):
    kind: Literal["label_assignment"] = "label_assignment"
    stratum_id: int
    label_text: str = Field(..., min_length=1, max_length=120)


class SnapshotCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=60)
    note: str = Field("", max_length=800)
    evidence_ids: list[int] | None = None
    region_id: int | None = None
    include_classes: list[str] | None = None


class PhaseIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    stratum_codes: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)


class SchemeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=200)
    snapshot_id: int
    phases: list[PhaseIn] = Field(..., min_length=1)
    required_reviews: int = Field(2, ge=1, le=5)


class RevisionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=200)
    snapshot_id: int
    phases: list[PhaseIn] = Field(..., min_length=1)
    required_reviews: int = Field(2, ge=1, le=5)


class ReviewIn(BaseModel):
    vote: str = Field(..., pattern="^(approve|request_changes|reject)$")
    comment: str = Field("", max_length=2000)
    expected_lock_version: int | None = None
