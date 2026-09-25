"""区域年代序列路由：观测证据 / 推断关系 / 暂定标签、快照求解、方案评审、导入导出。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import Field

from app.chrono_schemas import (
    ArtifactFindEvidence,
    CitationCreate,
    DatingEvidence,
    LabelEvidence,
    OrderingEvidence,
    RegionCreate,
    ReviewIn,
    RevisionCreate,
    SchemeCreate,
    SiteCreate,
    SnapshotCreate,
    StratumCreate,
    TypologyRangeEvidence,
)
from app.chrono_service import ChronoService
from app.service import ResearchService, ServiceError

router = APIRouter(prefix="/api/projects/{project_id}")

EvidencePayload = Annotated[
    DatingEvidence | TypologyRangeEvidence | ArtifactFindEvidence | OrderingEvidence | LabelEvidence,
    Field(discriminator="kind"),
]


def current_user(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer 会话")
    return ResearchService().authenticate(authorization[7:])


def service() -> ChronoService:
    return ChronoService()


def member(user, project_id: int) -> None:
    """所有读写至少要求项目成员身份；写操作的具体角色由服务层校验。"""
    ResearchService().require_role(project_id, user["id"], {"owner", "researcher", "recorder", "reviewer", "viewer"})


# ---- 基础名录 -------------------------------------------------------------

@router.post("/regions", status_code=201)
def create_region(project_id: int, payload: RegionCreate, user=Depends(current_user)):
    return service().create_region(project_id, payload.model_dump(), user["id"])


@router.get("/regions")
def list_regions(project_id: int, user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_regions(project_id)}


@router.post("/sites", status_code=201)
def create_site(project_id: int, payload: SiteCreate, user=Depends(current_user)):
    return service().create_site(project_id, payload.model_dump(), user["id"])


@router.get("/sites")
def list_sites(project_id: int, region_id: int | None = Query(None), user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_sites(project_id, region_id)}


@router.post("/sites/{site_id}/strata", status_code=201)
def create_stratum(project_id: int, site_id: int, payload: StratumCreate, user=Depends(current_user)):
    return service().create_stratum(project_id, site_id, payload.model_dump(), user["id"])


@router.get("/strata")
def list_strata(project_id: int, site_id: int | None = Query(None), user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_strata(project_id, site_id)}


@router.post("/citations", status_code=201)
def create_citation(project_id: int, payload: CitationCreate, user=Depends(current_user)):
    return service().create_citation(project_id, payload.model_dump(), user["id"])


@router.get("/citations")
def list_citations(project_id: int, user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_citations(project_id)}


@router.post("/citations/{citation_id}/retract")
def retract_citation(project_id: int, citation_id: int, reason: str = Query(..., min_length=1), user=Depends(current_user)):
    return service().retract_citation(citation_id, project_id, user["id"], reason)


# ---- 证据：三类信息分离 ---------------------------------------------------

@router.post("/evidence", status_code=201)
def create_evidence(project_id: int, payload: EvidencePayload, user=Depends(current_user)):
    return service().create_evidence(project_id, payload.model_dump(), user["id"])


@router.get("/evidence")
def list_evidence(
    project_id: int,
    evidence_class: str | None = Query(None, pattern="^(observation|inference|label)$"),
    kind: str | None = Query(None),
    stratum_id: int | None = Query(None),
    site_id: int | None = Query(None),
    region_id: int | None = Query(None),
    status: str | None = Query(None, pattern="^(active|retracted)$"),
    user=Depends(current_user),
):
    member(user, project_id)
    filters = {"evidence_class": evidence_class, "kind": kind, "stratum_id": stratum_id,
               "site_id": site_id, "region_id": region_id, "status": status}
    return {"data": service().list_evidence(project_id, filters)}


# ---- 快照与求解 -----------------------------------------------------------

@router.post("/snapshots", status_code=201)
def create_snapshot(project_id: int, payload: SnapshotCreate, user=Depends(current_user)):
    return service().create_snapshot(project_id, payload.model_dump(), user["id"])


@router.get("/snapshots")
def list_snapshots(project_id: int, user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_snapshots(project_id)}


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(project_id: int, snapshot_id: int, user=Depends(current_user)):
    member(user, project_id)
    return service().get_snapshot(snapshot_id, project_id)


@router.post("/snapshots/{snapshot_id}/compute")
def compute_snapshot(project_id: int, snapshot_id: int, user=Depends(current_user)):
    member(user, project_id)
    return service().compute_snapshot(snapshot_id, project_id)


@router.get("/chronology")
def chronology_view(
    project_id: int,
    snapshot_id: int | None = Query(None),
    region_id: int | None = Query(None),
    from_year: int | None = Query(None, description="有符号日历年，公元前为负"),
    to_year: int | None = Query(None),
    user=Depends(current_user),
):
    member(user, project_id)
    return service().chronology_view(project_id, snapshot_id, region_id, from_year, to_year)


# ---- 文化阶段方案：提议、评审、发布、修订 ---------------------------------

@router.post("/schemes", status_code=201)
def propose_scheme(project_id: int, payload: SchemeCreate, user=Depends(current_user)):
    return service().propose_scheme(project_id, payload.model_dump(), user["id"])


@router.get("/schemes")
def list_schemes(project_id: int, user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_schemes(project_id)}


@router.get("/schemes/{scheme_id}")
def get_scheme(project_id: int, scheme_id: int, user=Depends(current_user)):
    member(user, project_id)
    return service().get_scheme(scheme_id, project_id)


@router.post("/schemes/{scheme_id}/submit")
def submit_scheme(project_id: int, scheme_id: int, user=Depends(current_user)):
    return service().submit_scheme(scheme_id, project_id, user["id"])


@router.post("/schemes/{scheme_id}/reviews")
def review_scheme(project_id: int, scheme_id: int, payload: ReviewIn, user=Depends(current_user)):
    return service().review_scheme(scheme_id, project_id, payload.model_dump(), user["id"])


@router.post("/schemes/{scheme_id}/publish")
def publish_scheme(project_id: int, scheme_id: int, user=Depends(current_user)):
    return service().publish_scheme(scheme_id, project_id, user["id"])


@router.post("/schemes/{scheme_id}/revisions", status_code=201)
def revise_scheme(project_id: int, scheme_id: int, payload: RevisionCreate, user=Depends(current_user)):
    return service().propose_scheme(project_id, payload.model_dump(), user["id"], revision_of=scheme_id)


@router.get("/versions")
def list_versions(project_id: int, user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_versions(project_id)}


@router.get("/versions/{version_id}")
def get_version(project_id: int, version_id: int, user=Depends(current_user)):
    member(user, project_id)
    return service().get_version(version_id, project_id)


# ---- 复核标记 -------------------------------------------------------------

@router.get("/review-flags")
def list_flags(project_id: int, status: str | None = Query(None, pattern="^(open|resolved)$"), user=Depends(current_user)):
    member(user, project_id)
    return {"data": service().list_flags(project_id, status)}


@router.post("/review-flags/{flag_id}/resolve")
def resolve_flag(project_id: int, flag_id: int, user=Depends(current_user)):
    return service().resolve_flag(flag_id, project_id, user["id"])


# ---- 离线导入导出 ---------------------------------------------------------

@router.get("/export")
def export_bundle(project_id: int, user=Depends(current_user)):
    member(user, project_id)
    return service().export_bundle(project_id)


@router.post("/import-bundle", status_code=201)
def import_bundle(payload: dict, new_code: str | None = Query(None), user=Depends(current_user)):
    return service().import_bundle(payload, new_code=new_code, actor_id=user["id"])
