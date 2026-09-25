from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
import sqlite3

from app.chronology_schemas import (
    DatingEvidence,
    LabelEvidence,
    OrderingEvidence,
    ReviewDecision,
    SchemePropose,
    SchemeRevisionPropose,
    SiteCreate,
    LayerCreate,
    SnapshotCreate,
    SourceCreate,
    SourceRetract,
    TypologyEvidence,
)
from app.chronology_service import ChronologyService
from app.service import ResearchService, ServiceError

router = APIRouter(prefix="/api/projects/{project_id}/chron", tags=["chronology"])


def current_user(authorization: str = Header(...)) -> sqlite3.Row:
    if not authorization.startswith("Bearer "):
        raise ServiceError("unauthorized", "缺少 Bearer 会话", 401)
    return ResearchService().authenticate(authorization[7:])


def svc() -> ChronologyService:
    return ChronologyService()


# ---------- 基础登记 ----------

@router.post("/sites", status_code=201)
def create_site(project_id: int, payload: SiteCreate, user=Depends(current_user)):
    return svc().create_site(project_id, user["id"], payload.model_dump())


@router.get("/sites")
def list_sites(project_id: int, region: str | None = Query(None), user=Depends(current_user)):
    return svc().list_sites(project_id, user["id"], region=region)


@router.post("/layers", status_code=201)
def create_layer(project_id: int, payload: LayerCreate, user=Depends(current_user)):
    return svc().create_layer(project_id, user["id"], payload.model_dump())


@router.get("/layers")
def list_layers(project_id: int, site_key: str | None = Query(None), user=Depends(current_user)):
    return svc().list_layers(project_id, user["id"], site_key=site_key)


@router.post("/sources", status_code=201)
def create_source(project_id: int, payload: SourceCreate, user=Depends(current_user)):
    return svc().create_source(project_id, user["id"], payload.model_dump())


@router.get("/sources")
def list_sources(project_id: int, user=Depends(current_user)):
    return svc().list_sources(project_id, user["id"])


@router.post("/sources/{source_key}/retract")
def retract_source(project_id: int, source_key: str, payload: SourceRetract, user=Depends(current_user)):
    return svc().retract_source(project_id, user["id"], source_key, payload.reason)


# ---------- 证据：观测 / 推断 / 暂定标签 ----------

@router.post("/evidence/dating", status_code=201)
def add_dating(project_id: int, payload: DatingEvidence, user=Depends(current_user)):
    return svc().add_evidence(project_id, user["id"], "dating", payload.model_dump())


@router.put("/evidence/dating/{evidence_key}", status_code=201)
def revise_dating(project_id: int, evidence_key: str, payload: DatingEvidence, user=Depends(current_user)):
    return svc().revise_evidence(project_id, user["id"], "dating", evidence_key, payload.model_dump())


@router.post("/evidence/typology", status_code=201)
def add_typology(project_id: int, payload: TypologyEvidence, user=Depends(current_user)):
    return svc().add_evidence(project_id, user["id"], "typology", payload.model_dump())


@router.put("/evidence/typology/{evidence_key}", status_code=201)
def revise_typology(project_id: int, evidence_key: str, payload: TypologyEvidence, user=Depends(current_user)):
    return svc().revise_evidence(project_id, user["id"], "typology", evidence_key, payload.model_dump())


@router.post("/evidence/ordering", status_code=201)
def add_ordering(project_id: int, payload: OrderingEvidence, user=Depends(current_user)):
    return svc().add_evidence(project_id, user["id"], "ordering", payload.model_dump())


@router.put("/evidence/ordering/{evidence_key}", status_code=201)
def revise_ordering(project_id: int, evidence_key: str, payload: OrderingEvidence, user=Depends(current_user)):
    return svc().revise_evidence(project_id, user["id"], "ordering", evidence_key, payload.model_dump())


@router.post("/evidence/label", status_code=201)
def add_label(project_id: int, payload: LabelEvidence, user=Depends(current_user)):
    return svc().add_evidence(project_id, user["id"], "label", payload.model_dump())


@router.put("/evidence/label/{evidence_key}", status_code=201)
def revise_label(project_id: int, evidence_key: str, payload: LabelEvidence, user=Depends(current_user)):
    return svc().revise_evidence(project_id, user["id"], "label", evidence_key, payload.model_dump())


@router.get("/evidence")
def list_evidence(project_id: int,
                  kind: str | None = Query(None, pattern="^(dating|typology|ordering|label)$"),
                  layer_key: str | None = None, site_key: str | None = None, region: str | None = None,
                  younger_than: int | None = Query(None, ge=0),
                  older_than: int | None = Query(None, ge=0),
                  include_superseded: bool = False,
                  user=Depends(current_user)):
    return svc().list_evidence(
        project_id, user["id"], kind=kind, layer_key=layer_key, site_key=site_key, region=region,
        younger_than=younger_than, older_than=older_than, include_superseded=include_superseded,
    )


# ---------- 证据快照与求解 ----------

@router.post("/snapshots", status_code=201)
def create_snapshot(project_id: int, payload: SnapshotCreate, user=Depends(current_user)):
    return svc().create_snapshot(project_id, user["id"], payload.model_dump())


@router.get("/snapshots")
def list_snapshots(project_id: int, user=Depends(current_user)):
    return svc().list_snapshots(project_id, user["id"])


@router.post("/snapshots/{snapshot_key}/compute")
def compute_snapshot(project_id: int, snapshot_key: str, force: bool = Query(False), user=Depends(current_user)):
    return svc().compute_snapshot(project_id, user["id"], snapshot_key, force=force)


# ---------- 文化阶段方案工作流 ----------

@router.post("/schemes", status_code=201)
def propose_scheme(project_id: int, payload: SchemePropose, user=Depends(current_user)):
    return svc().propose_scheme(project_id, user["id"], payload.model_dump())


@router.get("/schemes")
def list_schemes(project_id: int, user=Depends(current_user)):
    return svc().list_schemes(project_id, user["id"])


@router.get("/schemes/{scheme_key}/versions/{version_no}")
def get_version(project_id: int, scheme_key: str, version_no: int, user=Depends(current_user)):
    return svc().get_version(project_id, user["id"], scheme_key, version_no)


@router.post("/schemes/{scheme_key}/versions/{version_no}/reviews")
def review_scheme(project_id: int, scheme_key: str, version_no: int, payload: ReviewDecision, user=Depends(current_user)):
    return svc().review_scheme(project_id, user["id"], scheme_key, version_no, payload.model_dump())


@router.post("/schemes/{scheme_key}/versions/{version_no}/publish")
def publish_scheme(project_id: int, scheme_key: str, version_no: int, user=Depends(current_user)):
    return svc().publish_scheme(project_id, user["id"], scheme_key, version_no)


@router.post("/schemes/{scheme_key}/revisions", status_code=201)
def revise_scheme(project_id: int, scheme_key: str, payload: SchemeRevisionPropose, user=Depends(current_user)):
    return svc().revise_scheme(project_id, user["id"], scheme_key, payload.model_dump())


@router.post("/schemes/{scheme_key}/versions/{version_no}/withdraw")
def withdraw_scheme(project_id: int, scheme_key: str, version_no: int,
                    note: str = Query(..., min_length=1), user=Depends(current_user)):
    return svc().withdraw_scheme(project_id, user["id"], scheme_key, version_no, note)


# ---------- 待复核与时间线 ----------

@router.get("/flags")
def list_flags(project_id: int, status: str | None = Query(None, pattern="^(pending|resolved)$"), user=Depends(current_user)):
    return svc().list_flags(project_id, user["id"], status=status)


@router.post("/flags/{flag_id}/resolve")
def resolve_flag(project_id: int, flag_id: int, note: str = Query(..., min_length=1), user=Depends(current_user)):
    return svc().resolve_flag(project_id, user["id"], flag_id, note)


@router.get("/timeline")
def timeline(project_id: int, region: str | None = None,
             younger_than: int | None = Query(None, ge=0), older_than: int | None = Query(None, ge=0),
             user=Depends(current_user)):
    return svc().timeline(project_id, user["id"], region=region,
                          younger_than=younger_than, older_than=older_than)
