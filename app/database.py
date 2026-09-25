from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL,
 site_name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed','archived')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL,
 password_hash TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK(role IN ('owner','researcher','recorder','reviewer','viewer')),
 joined_at TEXT NOT NULL,
 PRIMARY KEY(project_id,user_id)
);
CREATE TABLE IF NOT EXISTS sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL,
 revoked_at TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
 actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 action TEXT NOT NULL,
 resource_type TEXT NOT NULL,
 resource_id TEXT NOT NULL,
 payload_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_records (
 scope TEXT NOT NULL,
 request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 response_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(scope,request_key)
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
 job_type TEXT NOT NULL,
 job_key TEXT NOT NULL UNIQUE,
 input_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','leased','retry','done','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT NOT NULL DEFAULT '',
 lease_until TEXT NOT NULL DEFAULT '',
 result_json TEXT NOT NULL DEFAULT '{}',
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,created_at,id);
CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_events(project_id,created_at,id);

-- 区域年代序列模块：区域、遗址、文化层、出处
CREATE TABLE IF NOT EXISTS regions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 code TEXT NOT NULL,
 name TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,code)
);
CREATE TABLE IF NOT EXISTS sites (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 region_id INTEGER REFERENCES regions(id) ON DELETE SET NULL,
 code TEXT NOT NULL,
 name TEXT NOT NULL,
 location_note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,code)
);
CREATE TABLE IF NOT EXISTS strata (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
 code TEXT NOT NULL,
 name TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(site_id,code)
);
CREATE TABLE IF NOT EXISTS citations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 cite_key TEXT NOT NULL,
 title TEXT NOT NULL DEFAULT '',
 author TEXT NOT NULL DEFAULT '',
 published_year INTEGER,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retracted')),
 retracted_at TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,cite_key)
);
-- 统一证据表：class 区分观测/推断/暂定标签，kind 区分具体证据形态
CREATE TABLE IF NOT EXISTS evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 code TEXT NOT NULL,
 evidence_class TEXT NOT NULL CHECK(evidence_class IN ('observation','inference','label')),
 kind TEXT NOT NULL CHECK(kind IN ('dating','typology_range','artifact_find','ordering','label_assignment')),
 stratum_id INTEGER REFERENCES strata(id) ON DELETE CASCADE,
 site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
 method TEXT NOT NULL DEFAULT '',
 probability REAL,
 lower_year INTEGER,
 upper_year INTEGER,
 artifact_type TEXT NOT NULL DEFAULT '',
 relation TEXT NOT NULL DEFAULT '',
 subject_stratum_id INTEGER REFERENCES strata(id) ON DELETE CASCADE,
 object_stratum_id INTEGER REFERENCES strata(id) ON DELETE CASCADE,
 gap_years INTEGER NOT NULL DEFAULT 0,
 label_text TEXT NOT NULL DEFAULT '',
 citation_id INTEGER REFERENCES citations(id) ON DELETE SET NULL,
 note TEXT NOT NULL DEFAULT '',
 content_hash TEXT NOT NULL,
 supersedes_code TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retracted')),
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,code)
);
CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id,status,evidence_class,kind);
CREATE INDEX IF NOT EXISTS idx_evidence_stratum ON evidence(stratum_id);
-- 证据快照：内容寻址，只记录选定时刻的证据及其内容哈希
CREATE TABLE IF NOT EXISTS snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 code TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 region_id INTEGER REFERENCES regions(id) ON DELETE SET NULL,
 evidence_hash TEXT NOT NULL,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 UNIQUE(project_id,code)
);
CREATE TABLE IF NOT EXISTS snapshot_items (
 snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
 content_hash TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,evidence_id)
);
-- 计算结果按证据集哈希缓存：同一证据集永远得到同一结果
CREATE TABLE IF NOT EXISTS computations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 evidence_hash TEXT NOT NULL UNIQUE,
 result_hash TEXT NOT NULL,
 consistent INTEGER NOT NULL,
 result_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
-- 文化阶段方案：提议 -> 同行评审 -> 发布；发布内容固化到 scheme_versions
CREATE TABLE IF NOT EXISTS phase_schemes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 code TEXT NOT NULL,
 title TEXT NOT NULL,
 version_no INTEGER NOT NULL DEFAULT 1,
 parent_version_id INTEGER REFERENCES scheme_versions(id) ON DELETE SET NULL,
 snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN ('proposed','in_review','published','rejected')),
 required_reviews INTEGER NOT NULL DEFAULT 2,
 content_json TEXT NOT NULL,
 candidate_diff_json TEXT NOT NULL DEFAULT '',
 lock_version INTEGER NOT NULL DEFAULT 0,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 submitted_at TEXT NOT NULL DEFAULT '',
 published_at TEXT NOT NULL DEFAULT '',
 UNIQUE(project_id,code,version_no)
);
CREATE TABLE IF NOT EXISTS scheme_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scheme_id INTEGER NOT NULL REFERENCES phase_schemes(id) ON DELETE CASCADE,
 reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 vote TEXT NOT NULL CHECK(vote IN ('approve','request_changes','reject')),
 comment TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 UNIQUE(scheme_id,reviewer_id)
);
CREATE TABLE IF NOT EXISTS scheme_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scheme_id INTEGER NOT NULL REFERENCES phase_schemes(id) ON DELETE CASCADE,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 version_no INTEGER NOT NULL,
 snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 content_hash TEXT NOT NULL,
 content_json TEXT NOT NULL,
 result_hash TEXT NOT NULL,
 published_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 published_at TEXT NOT NULL,
 UNIQUE(scheme_id,version_no)
);
CREATE TABLE IF NOT EXISTS scheme_conclusions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 version_id INTEGER NOT NULL REFERENCES scheme_versions(id) ON DELETE CASCADE,
 phase_index INTEGER NOT NULL,
 label TEXT NOT NULL,
 site_codes_json TEXT NOT NULL DEFAULT '[]',
 stratum_codes_json TEXT NOT NULL DEFAULT '[]',
 boundary_json TEXT NOT NULL DEFAULT '{}',
 supporting_evidence_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_conclusions_version ON scheme_conclusions(version_id);
-- 复核标记侧车表：发布版本不可改，撤回等事件只追加标记
CREATE TABLE IF NOT EXISTS review_flags (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 version_id INTEGER REFERENCES scheme_versions(id) ON DELETE CASCADE,
 scheme_id INTEGER REFERENCES phase_schemes(id) ON DELETE CASCADE,
 conclusion_id INTEGER REFERENCES scheme_conclusions(id) ON DELETE CASCADE,
 citation_id INTEGER REFERENCES citations(id) ON DELETE SET NULL,
 evidence_id INTEGER REFERENCES evidence(id) ON DELETE SET NULL,
 reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_flags_status ON review_flags(project_id,status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _create() -> sqlite3.Connection:
    path = settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def connection() -> sqlite3.Connection:
    value = getattr(_local, "connection", None)
    if value is None:
        value = _create()
        _local.connection = value
    return value


def close_connection() -> None:
    value = getattr(_local, "connection", None)
    if value is not None:
        value.close()
        _local.connection = None


def init_db() -> None:
    connection().executescript(SCHEMA)


@contextmanager
def transaction(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    db = connection()
    db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()
