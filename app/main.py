from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.database import close_connection, connection, init_db
from app.schemas import JobCreate, JobFinish, LoginRequest, MemberCreate, ProjectCreate, UserCreate
from app.service import ResearchService, ServiceError
from app.chronology_routes import router as chronology_router
from app.chronology_service import ChronologyService


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    init_db()
    yield
    close_connection()


app = FastAPI(title="考古研究协作基础服务", version="1.1.0", lifespan=lifespan)
app.include_router(chronology_router)


@app.exception_handler(ServiceError)
async def handle_service_error(request, exc: ServiceError):
    del request
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


def current_user(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer 会话")
    return ResearchService().authenticate(authorization[7:])


@app.get("/")
def root():
    return {"service": "考古研究协作基础服务", "version": "1.1.0", "modules": ["foundation", "regional-chronology"]}


@app.get("/api/system/health")
def health():
    db = connection()
    return {"status": "ok", "foreign_keys": db.execute("PRAGMA foreign_keys").fetchone()[0], "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0]}


@app.post("/api/users", status_code=201)
def create_user(payload: UserCreate):
    return ResearchService().create_user(payload.model_dump())


@app.post("/api/sessions")
def login(payload: LoginRequest):
    return ResearchService().login(payload.username, payload.password)


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreate, user=Depends(current_user), idempotency_key: str = Header(default="")):
    return ResearchService().create_project(payload.model_dump(), user["id"], idempotency_key)


@app.post("/api/projects/{project_id}/members")
def add_member(project_id: int, payload: MemberCreate, user=Depends(current_user)):
    return ResearchService().add_member(project_id, user["id"], payload.user_id, payload.role)


@app.get("/api/audit")
def list_audit(project_id: int | None = Query(default=None), user=Depends(current_user)):
    service = ResearchService()
    if project_id is not None:
        service.require_role(project_id, user["id"], {"owner", "researcher", "reviewer", "viewer"})
        rows = connection().execute("SELECT * FROM audit_events WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    else:
        rows = connection().execute("SELECT * FROM audit_events WHERE actor_id=? ORDER BY id", (user["id"],)).fetchall()
    return {"data": [dict(row) for row in rows]}


@app.post("/api/jobs", status_code=202)
def enqueue_job(payload: JobCreate, user=Depends(current_user)):
    return ResearchService().enqueue(payload.model_dump(), user["id"])


@app.post("/api/jobs/claim")
def claim_job(worker_id: str = Query(..., min_length=1)):
    return {"job": ResearchService().claim(worker_id)}


@app.post("/api/jobs/{job_id}/finish")
def finish_job(job_id: int, payload: JobFinish):
    return ResearchService().finish(job_id, payload.worker_id, payload.result)


# ---------- 区域年代序列：离线导入 / 导出 ----------

@app.get("/api/projects/{project_id}/chron/export")
def export_chronology(project_id: int, user=Depends(current_user)):
    service = ChronologyService()
    service.require(project_id, user["id"], {"owner", "researcher", "reviewer", "viewer"})
    bundle = service.export_project(project_id)
    service.audit("chron.project.export", project_id, user["id"], "project", str(project_id),
                  {"evidence": len(bundle["evidence"]), "snapshots": len(bundle["snapshots"])})
    return bundle


@app.post("/api/projects/chron/import")
def import_chronology(payload: dict, project_code: str | None = Query(default=None), user=Depends(current_user)):
    # 仅项目负责人可向新项目导入
    return ChronologyService().import_project(payload, actor_id=user["id"], project_code=project_code)
