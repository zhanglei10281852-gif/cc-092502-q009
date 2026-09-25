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

-- 区域年代序列证据模块：观测证据 / 推断关系 / 暂定标签严格分表分字段
CREATE TABLE IF NOT EXISTS ch_sites (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 site_key TEXT NOT NULL,
 name TEXT NOT NULL,
 region TEXT NOT NULL DEFAULT '',
 latitude REAL,
 longitude REAL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,site_key)
);
CREATE TABLE IF NOT EXISTS ch_layers (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 site_id INTEGER NOT NULL REFERENCES ch_sites(id) ON DELETE CASCADE,
 layer_key TEXT NOT NULL,
 name TEXT NOT NULL,
 sequence_no INTEGER,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,layer_key)
);
CREATE TABLE IF NOT EXISTS ch_sources (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 source_key TEXT NOT NULL,
 title TEXT NOT NULL,
 authors TEXT NOT NULL DEFAULT '',
 citation TEXT NOT NULL DEFAULT '',
 year_pub INTEGER,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retracted')),
 retracted_at TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,source_key)
);
CREATE TABLE IF NOT EXISTS ch_evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 evidence_key TEXT NOT NULL,
 rev INTEGER NOT NULL DEFAULT 1,
 supersedes_id INTEGER REFERENCES ch_evidence(id) ON DELETE SET NULL,
 kind TEXT NOT NULL CHECK(kind IN ('dating','typology','ordering','label')),
 category TEXT NOT NULL CHECK(category IN ('observation','inference','tentative_label')),
 basis TEXT NOT NULL DEFAULT '',
 source_id INTEGER REFERENCES ch_sources(id) ON DELETE SET NULL,
 layer_id INTEGER REFERENCES ch_layers(id) ON DELETE CASCADE,
 other_layer_id INTEGER REFERENCES ch_layers(id) ON DELETE CASCADE,
 artifact_type TEXT NOT NULL DEFAULT '',
 sample_code TEXT NOT NULL DEFAULT '',
 method TEXT NOT NULL DEFAULT '',
 probability TEXT NOT NULL DEFAULT '',
 bound_lo INTEGER,
 bound_hi INTEGER,
 ranges_json TEXT NOT NULL DEFAULT '[]',
 gap_years INTEGER NOT NULL DEFAULT 0,
 label_text TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded','retracted')),
 content_hash TEXT NOT NULL,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 UNIQUE(project_id,evidence_key,rev)
);
CREATE INDEX IF NOT EXISTS idx_ch_evidence_layer ON ch_evidence(project_id,layer_id);
CREATE INDEX IF NOT EXISTS idx_ch_evidence_source ON ch_evidence(project_id,source_id);
CREATE INDEX IF NOT EXISTS idx_ch_evidence_key ON ch_evidence(project_id,evidence_key,rev);
CREATE TABLE IF NOT EXISTS ch_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 snapshot_key TEXT NOT NULL,
 title TEXT NOT NULL,
 algorithm_version TEXT NOT NULL,
 spec_json TEXT NOT NULL,
 evidence_hash TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 UNIQUE(project_id,snapshot_key)
);
CREATE TABLE IF NOT EXISTS ch_computations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 snapshot_id INTEGER NOT NULL UNIQUE REFERENCES ch_snapshots(id) ON DELETE CASCADE,
 result_hash TEXT NOT NULL,
 result_json TEXT NOT NULL,
 computed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ch_schemes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 scheme_key TEXT NOT NULL,
 title TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_id,scheme_key)
);
CREATE TABLE IF NOT EXISTS ch_scheme_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 scheme_id INTEGER NOT NULL REFERENCES ch_schemes(id) ON DELETE CASCADE,
 version_no INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('proposed','published','withdrawn')),
 snapshot_id INTEGER NOT NULL REFERENCES ch_snapshots(id) ON DELETE RESTRICT,
 content_json TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 parent_version_id INTEGER REFERENCES ch_scheme_versions(id) ON DELETE SET NULL,
 diff_json TEXT,
 approvals_required INTEGER NOT NULL DEFAULT 2,
 superseded_by_version_id INTEGER,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL,
 published_at TEXT,
 published_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 UNIQUE(scheme_id,version_no)
);
CREATE INDEX IF NOT EXISTS idx_ch_versions_project ON ch_scheme_versions(project_id,status);
CREATE TABLE IF NOT EXISTS ch_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 version_id INTEGER NOT NULL REFERENCES ch_scheme_versions(id) ON DELETE CASCADE,
 reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approve','request_changes','comment')),
 comment TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(version_id,reviewer_id)
);
CREATE TABLE IF NOT EXISTS ch_conclusion_flags (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 version_id INTEGER NOT NULL REFERENCES ch_scheme_versions(id) ON DELETE CASCADE,
 layer_id INTEGER NOT NULL REFERENCES ch_layers(id) ON DELETE CASCADE,
 stage_code TEXT NOT NULL DEFAULT '',
 reason TEXT NOT NULL CHECK(reason IN ('source_retracted')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','resolved')),
 detail_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 resolved_at TEXT NOT NULL DEFAULT '',
 UNIQUE(version_id,layer_id,reason)
);
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
