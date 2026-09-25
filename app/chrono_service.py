"""区域年代序列业务服务。

信息层级在代码中强制分离：

* ``observation``：测年概率区间、类型学出现范围、出土遗物——研究人员登记的观测；
* ``inference``：带出处的层位先后等推断关系；
* ``label``：文化阶段暂定标签，仅作标注，永不参与数值求解。

证据与发布版本均不可变；更正只能新增证据（``supersedes_code``）并据此提出候选修订。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any

from app.chrono import ChronologyModel, envelope_overlaps, phase_boundaries
from app.database import connection, now, transaction
from app.security import request_hash, stable_json
from app.service import ResearchService, ServiceError

OBSERVATION_KINDS = {"dating", "typology_range", "artifact_find"}
INFERENCE_KINDS = {"ordering"}
LABEL_KINDS = {"label_assignment"}


class ChronoService:
    def __init__(self, db: sqlite3.Connection | None = None):
        self.db = db or connection()
        self.base = ResearchService(self.db)

    # ---- 通用辅助 -------------------------------------------------------

    def audit(self, action: str, project_id: int, resource_id: str, payload: dict[str, Any], actor_id: int | None) -> None:
        self.base.audit(action, "chronology", resource_id, payload, project_id=project_id, actor_id=actor_id)

    def require(self, project_id: int, user_id: int, roles: set[str]) -> str:
        return self.base.require_role(project_id, user_id, roles)

    def _project(self, project_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ServiceError("project_not_found", "项目不存在", 404)
        return row

    def _get(self, table: str, row_id: int, project_id: int) -> sqlite3.Row:
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        if row is None or row["project_id"] != project_id:
            raise ServiceError("not_found", f"{table} 不存在或不属于该项目", 404)
        return row

    @staticmethod
    def _j(value: str) -> Any:
        return json.loads(value) if value else None

    # ---- 区域 / 遗址 / 文化层 / 出处 ------------------------------------

    def create_region(self, project_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher", "recorder"})
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute("INSERT INTO regions(project_id,code,name,created_at,updated_at) VALUES(?,?,?,?,?)", (project_id, payload["code"], payload["name"], stamp, stamp))
                self.audit("region.create", project_id, str(cur.lastrowid), payload, actor_id)
                return dict(db.execute("SELECT * FROM regions WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("region_exists", "区域编码已存在", 409) from exc

    def list_regions(self, project_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM regions WHERE project_id=? ORDER BY code", (project_id,)).fetchall()]

    def create_site(self, project_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher", "recorder"})
        if payload.get("region_id") is not None:
            self._get("regions", payload["region_id"], project_id)
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute("INSERT INTO sites(project_id,region_id,code,name,location_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (project_id, payload.get("region_id"), payload["code"], payload["name"], payload.get("location_note", ""), stamp, stamp))
                self.audit("site.create", project_id, str(cur.lastrowid), payload, actor_id)
                return dict(db.execute("SELECT * FROM sites WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("site_exists", "遗址编码已存在", 409) from exc

    def list_sites(self, project_id: int, region_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sites WHERE project_id=?"
        args: list[Any] = [project_id]
        if region_id is not None:
            sql += " AND region_id=?"
            args.append(region_id)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY code", args).fetchall()]

    def create_stratum(self, project_id: int, site_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher", "recorder"})
        self._get("sites", site_id, project_id)
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute("INSERT INTO strata(project_id,site_id,code,name,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (project_id, site_id, payload["code"], payload["name"], payload.get("note", ""), stamp, stamp))
                self.audit("stratum.create", project_id, str(cur.lastrowid), {"site_id": site_id, **payload}, actor_id)
                return dict(db.execute("SELECT * FROM strata WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("stratum_exists", "该遗址下文化层编码已存在", 409) from exc

    def list_strata(self, project_id: int, site_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT t.* FROM strata t WHERE t.project_id=?"
        args: list[Any] = [project_id]
        if site_id is not None:
            sql += " AND t.site_id=?"
            args.append(site_id)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY t.site_id,t.code", args).fetchall()]

    def create_citation(self, project_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher", "recorder", "reviewer"})
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(
                    "INSERT INTO citations(project_id,cite_key,title,author,published_year,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (project_id, payload["cite_key"], payload.get("title", ""), payload.get("author", ""), payload.get("published_year"), stamp, stamp),
                )
                self.audit("citation.create", project_id, str(cur.lastrowid), payload, actor_id)
                return dict(db.execute("SELECT * FROM citations WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("citation_exists", "出处键已存在", 409) from exc

    def list_citations(self, project_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM citations WHERE project_id=? ORDER BY cite_key", (project_id,)).fetchall()]

    # ---- 证据登记 -------------------------------------------------------

    def _evidence_class(self, kind: str) -> str:
        if kind in OBSERVATION_KINDS:
            return "observation"
        if kind in INFERENCE_KINDS:
            return "inference"
        if kind in LABEL_KINDS:
            return "label"
        raise ServiceError("invalid_evidence", f"未知证据类型 {kind}", 422)

    def _canonical_evidence(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """内容哈希只依赖语义编码（文化层/出处用 code），与数据库自增 id 无关。"""
        def scode(sid: int | None) -> str | None:
            if sid is None:
                return None
            row = self.db.execute("SELECT code FROM strata WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise ServiceError("stratum_not_found", "文化层不存在", 404)
            return row["code"]

        cite_key = None
        if payload.get("citation_id") is not None:
            cite_key = self._get("citations", payload["citation_id"], payload["_project_id"])["cite_key"]
        base = {"kind": kind, "citation": cite_key}
        if kind == "dating":
            base.update(stratum=scode(payload["stratum_id"]), method=payload["method"], probability=payload.get("probability"),
                        lower_year=payload.get("lower_year"), upper_year=payload.get("upper_year"))
        elif kind == "typology_range":
            base.update(artifact_type=payload["artifact_type"], lower_year=payload.get("lower_year"), upper_year=payload.get("upper_year"))
        elif kind == "artifact_find":
            base.update(stratum=scode(payload["stratum_id"]), artifact_type=payload["artifact_type"])
        elif kind == "ordering":
            base.update(before=scode(payload["subject_stratum_id"]), after=scode(payload["object_stratum_id"]), gap_years=payload.get("gap_years", 0))
        elif kind == "label_assignment":
            base.update(stratum=scode(payload["stratum_id"]), label=payload["label_text"])
        return base

    def create_evidence(self, project_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        kind = payload["kind"]
        self.require(project_id, actor_id, {"owner", "researcher", "recorder"})
        self._evidence_class(kind)
        payload = {**payload, "_project_id": project_id}
        canonical = self._canonical_evidence(kind, payload)
        digest = request_hash(canonical)

        def sid(name: str) -> int | None:
            value = payload.get(name)
            if value is not None:
                self._get("strata", value, project_id)
            return value

        stratum_id = sid("stratum_id")
        subject_id = sid("subject_stratum_id")
        object_id = sid("object_stratum_id")
        site_id = payload.get("site_id")
        if site_id is not None:
            self._get("sites", site_id, project_id)
        elif stratum_id is not None:
            site_id = self.db.execute("SELECT site_id FROM strata WHERE id=?", (stratum_id,)).fetchone()[0]
        citation_id = payload.get("citation_id")
        if citation_id is not None:
            self._get("citations", citation_id, project_id)
        stamp = now()
        columns = (
            "project_id,code,evidence_class,kind,stratum_id,site_id,method,probability,lower_year,upper_year,"
            "artifact_type,relation,subject_stratum_id,object_stratum_id,gap_years,label_text,citation_id,note,"
            "content_hash,supersedes_code,created_by,created_at,updated_at"
        )
        values = (
            project_id, payload["code"], self._evidence_class(kind), kind, stratum_id, site_id,
            payload.get("method", ""), payload.get("probability"), payload.get("lower_year"), payload.get("upper_year"),
            payload.get("artifact_type", ""), "before" if kind == "ordering" else "", subject_id, object_id,
            payload.get("gap_years", 0) or 0, payload.get("label_text", ""), citation_id, payload.get("note", ""),
            digest, payload.get("supersedes_code", ""), actor_id, stamp, stamp,
        )
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(f"INSERT INTO evidence({columns}) VALUES ({','.join('?' * len(values))})", values)
                self.audit("evidence.create", project_id, str(cur.lastrowid), {"code": payload["code"], "kind": kind, "class": self._evidence_class(kind)}, actor_id)
                return dict(db.execute("SELECT * FROM evidence WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("evidence_exists", "证据编码已存在或引用无效", 409) from exc

    def list_evidence(self, project_id: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        sql = "SELECT * FROM evidence WHERE project_id=?"
        args: list[Any] = [project_id]
        for key, column in (("evidence_class", "evidence_class"), ("kind", "kind"), ("stratum_id", "stratum_id"), ("site_id", "site_id"), ("status", "status")):
            if filters.get(key):
                sql += f" AND {column}=?"
                args.append(filters[key])
        if filters.get("region_id"):
            sql += " AND site_id IN (SELECT id FROM sites WHERE region_id=?)"
            args.append(filters["region_id"])
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args).fetchall()]

    # ---- 证据快照与求解 -------------------------------------------------

    def _snapshot_candidate_rows(self, project_id: int, payload: dict[str, Any]) -> list[sqlite3.Row]:
        if payload.get("evidence_ids") is not None:
            marks = ",".join("?" * len(payload["evidence_ids"]))
            rows = self.db.execute(
                f"SELECT * FROM evidence WHERE project_id=? AND status='active' AND id IN ({marks}) ORDER BY id",
                [project_id, *payload["evidence_ids"]],
            ).fetchall()
            if len(rows) != len(set(payload["evidence_ids"])):
                raise ServiceError("evidence_not_found", "部分证据不存在、已撤回或不属于该项目", 404)
            return list(rows)
        sql = "SELECT * FROM evidence WHERE project_id=? AND status='active'"
        args: list[Any] = [project_id]
        if payload.get("include_classes"):
            marks = ",".join("?" * len(payload["include_classes"]))
            sql += f" AND evidence_class IN ({marks})"
            args.extend(payload["include_classes"])
        if payload.get("region_id"):
            # 区域快照：遗址相关证据按区域过滤；类型学范围是全项目共享知识，仍然纳入；
            # 层位先后证据两端文化层均落在该区域遗址内时纳入
            sql += """ AND (
                kind='typology_range'
                OR site_id IN (SELECT id FROM sites WHERE region_id=?)
                OR (kind='ordering' AND subject_stratum_id IN (SELECT t.id FROM strata t JOIN sites s ON s.id=t.site_id WHERE s.region_id=?)
                                 AND object_stratum_id  IN (SELECT t.id FROM strata t JOIN sites s ON s.id=t.site_id WHERE s.region_id=?))
            )"""
            args.extend([payload["region_id"], payload["region_id"], payload["region_id"]])
        return list(self.db.execute(sql + " ORDER BY id", args).fetchall())

    def create_snapshot(self, project_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher", "recorder", "reviewer"})
        if payload.get("region_id") is not None:
            self._get("regions", payload["region_id"], project_id)
        rows = self._snapshot_candidate_rows(project_id, payload)
        if not rows:
            raise ServiceError("empty_snapshot", "快照至少需要包含一条证据", 422)
        digest = request_hash([[r["id"], r["content_hash"]] for r in rows])
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(
                    "INSERT INTO snapshots(project_id,code,note,region_id,evidence_hash,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                    (project_id, payload["code"], payload.get("note", ""), payload.get("region_id"), digest, actor_id, stamp),
                )
                sid = cur.lastrowid
                db.executemany("INSERT INTO snapshot_items(snapshot_id,evidence_id,content_hash) VALUES(?,?,?)", [(sid, r["id"], r["content_hash"]) for r in rows])
                self.audit("snapshot.create", project_id, str(sid), {"code": payload["code"], "evidence_count": len(rows), "evidence_hash": digest}, actor_id)
                snapshot = dict(db.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("snapshot_exists", "快照编码已存在", 409) from exc
        snapshot["evidence_count"] = len(rows)
        return snapshot

    def _snapshot_rows(self, snapshot_id: int, project_id: int) -> sqlite3.Row:
        snapshot = self._get("snapshots", snapshot_id, project_id)
        return snapshot

    def _engine_records(self, snapshot_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT e.*, st.code AS stratum_code, sbj.code AS before_code, obj.code AS after_code,
                   c.cite_key AS cite_key
            FROM snapshot_items si
            JOIN evidence e ON e.id = si.evidence_id
            LEFT JOIN strata st ON st.id = e.stratum_id
            LEFT JOIN strata sbj ON sbj.id = e.subject_stratum_id
            LEFT JOIN strata obj ON obj.id = e.object_stratum_id
            LEFT JOIN citations c ON c.id = e.citation_id
            WHERE si.snapshot_id=?
            ORDER BY CASE e.kind WHEN 'typology_range' THEN 0 ELSE 1 END, e.id
            """,
            (snapshot_id,),
        ).fetchall()
        records: list[dict[str, Any]] = []
        for r in rows:
            common = {"evidence": r["code"]}
            if r["kind"] == "dating":
                records.append({**common, "kind": "dating", "stratum": r["stratum_code"], "lower_year": r["lower_year"], "upper_year": r["upper_year"]})
            elif r["kind"] == "typology_range":
                records.append({**common, "kind": "typology_range", "artifact_type": r["artifact_type"], "lower_year": r["lower_year"], "upper_year": r["upper_year"]})
            elif r["kind"] == "artifact_find":
                records.append({**common, "kind": "artifact_find", "stratum": r["stratum_code"], "artifact_type": r["artifact_type"]})
            elif r["kind"] == "ordering":
                records.append({**common, "kind": "ordering", "before": r["before_code"], "after": r["after_code"], "gap_years": r["gap_years"]})
            elif r["kind"] == "label_assignment":
                records.append({**common, "kind": "label_assignment", "stratum": r["stratum_code"], "label_text": r["label_text"]})
        return records

    def compute_snapshot(self, snapshot_id: int, project_id: int) -> dict[str, Any]:
        snapshot = self._snapshot_rows(snapshot_id, project_id)
        cached = self.db.execute("SELECT * FROM computations WHERE evidence_hash=?", (snapshot["evidence_hash"],)).fetchone()
        if cached is not None:
            return {"snapshot_id": snapshot_id, "evidence_hash": snapshot["evidence_hash"], "result_hash": cached["result_hash"], "cached": True, **self._j(cached["result_json"])}
        records = self._engine_records(snapshot_id)
        result = ChronologyModel(records).solve()
        result_hash = request_hash(result)
        stamp = now()
        with self._write_scope() as db:
            # 可能在发布/撤回等外层事务内被调用：相同证据集若已被并发计算则复用
            db.execute(
                "INSERT OR IGNORE INTO computations(snapshot_id,evidence_hash,result_hash,consistent,result_json,created_at) VALUES(?,?,?,?,?,?)",
                (snapshot_id, snapshot["evidence_hash"], result_hash, 1 if result["consistent"] else 0, stable_json(result), stamp),
            )
        stored = self.db.execute("SELECT result_hash FROM computations WHERE evidence_hash=?", (snapshot["evidence_hash"],)).fetchone()
        return {"snapshot_id": snapshot_id, "evidence_hash": snapshot["evidence_hash"], "result_hash": stored["result_hash"], "cached": False, **result}

    @contextmanager
    def _write_scope(self):
        """已在事务中则复用当前连接（不嵌套 BEGIN）；否则开即时事务。"""
        if self.db.in_transaction:
            yield self.db
        else:
            with transaction(immediate=True) as db:
                yield db

    def get_snapshot(self, snapshot_id: int, project_id: int) -> dict[str, Any]:
        snapshot = dict(self._snapshot_rows(snapshot_id, project_id))
        items = self.db.execute(
            """SELECT e.id,e.code,e.kind,e.evidence_class,e.content_hash,si.content_hash AS snapshot_hash
               FROM snapshot_items si JOIN evidence e ON e.id=si.evidence_id
               WHERE si.snapshot_id=? ORDER BY e.id""",
            (snapshot_id,),
        ).fetchall()
        snapshot["items"] = [dict(r) for r in items]
        snapshot["tampered"] = any(r["content_hash"] != r["snapshot_hash"] for r in items)
        return snapshot

    def list_snapshots(self, project_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM snapshots WHERE project_id=? ORDER BY id", (project_id,)).fetchall()]

    # ---- 文化阶段方案工作流 ---------------------------------------------

    def _snapshot_strata(self, snapshot_id: int) -> dict[str, str]:
        """文化层 code -> 遗址 code（基于快照内证据引用，仅用于差异与结论展示）。"""
        rows = self.db.execute(
            """SELECT DISTINCT t.code AS stratum_code, s.code AS site_code
               FROM snapshot_items si
               JOIN evidence e ON e.id=si.evidence_id
               JOIN strata t ON t.id IN (e.stratum_id, e.subject_stratum_id, e.object_stratum_id)
               JOIN sites s ON s.id=t.site_id
               WHERE si.snapshot_id=?""",
            (snapshot_id,),
        ).fetchall()
        return {r["stratum_code"]: r["site_code"] for r in rows}

    def _validate_phases(self, project_id: int, snapshot_id: int, phases: list[dict[str, Any]]) -> dict[str, Any]:
        result = self.compute_snapshot(snapshot_id, project_id)
        known = set(result["strata"])
        evidence_codes = {item["code"] for item in self.get_snapshot(snapshot_id, project_id)["items"]}
        labels: set[str] = set()
        for phase in phases:
            if phase["label"] in labels:
                raise ServiceError("duplicate_phase", f"阶段标签重复：{phase['label']}", 422)
            labels.add(phase["label"])
            unknown = [c for c in phase["stratum_codes"] if c not in known]
            if unknown:
                raise ServiceError("unknown_stratum", f"阶段 {phase['label']} 引用了快照之外的文化层：{unknown}", 422)
            bad = [c for c in phase.get("supporting_evidence", []) if c not in evidence_codes]
            if bad:
                raise ServiceError("unknown_evidence", f"阶段 {phase['label']} 引用了快照之外的证据：{bad}", 422)
        return result

    def propose_scheme(self, project_id: int, payload: dict[str, Any], actor_id: int, *, revision_of: int | None = None) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher"})
        snapshot = self._get("snapshots", payload["snapshot_id"], project_id)
        result = self._validate_phases(project_id, snapshot["id"], payload["phases"])
        if not result["consistent"]:
            raise ServiceError("inconsistent_evidence", "快照证据不相容，不能提议阶段方案", 422)
        parent_version_id = None
        version_no = 1
        diff_json = ""
        if revision_of is not None:
            parent_scheme = self._get("phase_schemes", revision_of, project_id)
            if parent_scheme["code"] != payload["code"]:
                raise ServiceError("code_mismatch", "候选修订必须沿用原方案编码", 422)
            if parent_scheme["status"] != "published":
                raise ServiceError("not_published", "只能针对已发布方案创建候选修订", 422)
            newer = self.db.execute(
                "SELECT 1 FROM scheme_versions v JOIN phase_schemes p ON p.id=v.scheme_id WHERE p.project_id=? AND p.code=? AND v.version_no>?",
                (project_id, payload["code"], parent_scheme["version_no"]),
            ).fetchone()
            if newer:
                raise ServiceError("parent_superseded", "已有更新的发布版本，请针对最新版本修订", 409)
            parent_version = self.db.execute("SELECT * FROM scheme_versions WHERE scheme_id=? AND version_no=?", (revision_of, parent_scheme["version_no"])).fetchone()
            parent_version_id = parent_version["id"]
            version_no = parent_scheme["version_no"] + 1
            diff_json = stable_json(self._diff_against_parent(parent_version, payload, result))
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(
                    """INSERT INTO phase_schemes(project_id,code,title,version_no,parent_version_id,snapshot_id,status,
                       required_reviews,content_json,candidate_diff_json,created_by,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (project_id, payload["code"], payload["title"], version_no, parent_version_id, snapshot["id"], "proposed",
                     payload.get("required_reviews", 2), stable_json(payload["phases"]), diff_json, actor_id, stamp, stamp),
                )
                self.audit("scheme.propose" if revision_of is None else "scheme.revise", project_id, str(cur.lastrowid),
                           {"code": payload["code"], "version_no": version_no, "parent_version_id": parent_version_id}, actor_id)
                return dict(db.execute("SELECT * FROM phase_schemes WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("scheme_exists", "该方案编码与版本已存在", 409) from exc

    def _diff_against_parent(self, parent_version: sqlite3.Row, candidate: dict[str, Any], new_result: dict[str, Any]) -> dict[str, Any]:
        parent_phases = self._j(parent_version["content_json"])
        parent_result = self.compute_snapshot(parent_version["snapshot_id"], parent_version["project_id"])
        old_map = {p["label"]: p for p in parent_phases}
        new_map = {p["label"]: p for p in candidate["phases"]}
        strata_sites = self._snapshot_strata(candidate["snapshot_id"])
        phase_changes: dict[str, Any] = {}
        affected_strata: set[str] = set()
        for label in sorted(set(old_map) | set(new_map)):
            old, new = old_map.get(label), new_map.get(label)
            if old is None:
                phase_changes[label] = {"change": "added", "strata_added": sorted(new["stratum_codes"]), "evidence_added": sorted(new.get("supporting_evidence", []))}
                affected_strata.update(new["stratum_codes"])
                continue
            if new is None:
                phase_changes[label] = {"change": "removed", "strata_removed": sorted(old["stratum_codes"])}
                affected_strata.update(old["stratum_codes"])
                continue
            sa = sorted(set(new["stratum_codes"]) - set(old["stratum_codes"]))
            sr = sorted(set(old["stratum_codes"]) - set(new["stratum_codes"]))
            ea = sorted(set(new.get("supporting_evidence", [])) - set(old.get("supporting_evidence", [])))
            er = sorted(set(old.get("supporting_evidence", [])) - set(new.get("supporting_evidence", [])))
            if sa or sr or ea or er:
                phase_changes[label] = {"change": "modified", "strata_added": sa, "strata_removed": sr, "evidence_added": ea, "evidence_removed": er}
                affected_strata.update(sa + sr)
        old_bounds = phase_boundaries(parent_phases, parent_result["strata"])
        new_bounds = phase_boundaries(candidate["phases"], new_result["strata"])
        boundary_changes: dict[str, Any] = {}
        for label in sorted(new_bounds):
            if label in old_bounds and old_bounds[label] != new_bounds[label]:
                boundary_changes[label] = {"before": old_bounds[label], "after": new_bounds[label]}
                affected_strata.update(new_map[label]["stratum_codes"])
        old_codes = {item["code"] for item in self.get_snapshot(parent_version["snapshot_id"], parent_version["project_id"])["items"]}
        new_codes = {item["code"] for item in self.get_snapshot(candidate["snapshot_id"], parent_version["project_id"])["items"]}
        return {
            "parent_version_no": parent_version["version_no"],
            "candidate_version_no": parent_version["version_no"] + 1,
            "evidence_added": sorted(new_codes - old_codes),
            "evidence_removed": sorted(old_codes - new_codes),
            "phase_changes": phase_changes,
            "boundary_changes": boundary_changes,
            "affected_strata": sorted(affected_strata),
            "affected_sites": sorted({strata_sites.get(s, "?") for s in affected_strata}),
        }

    def submit_scheme(self, scheme_id: int, project_id: int, actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher"})
        with transaction(immediate=True) as db:
            row = db.execute("SELECT * FROM phase_schemes WHERE id=?", (scheme_id,)).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ServiceError("scheme_not_found", "方案不存在", 404)
            if row["status"] != "proposed":
                raise ServiceError("invalid_transition", "只有提议中的方案可以提交评审", 409)
            # 仅创建者或项目负责人可提交
            role = db.execute("SELECT role FROM project_members WHERE project_id=? AND user_id=?", (project_id, actor_id)).fetchone()
            if row["created_by"] != actor_id and (role is None or role["role"] != "owner"):
                raise ServiceError("forbidden", "只有提案人或负责人可以提交评审", 403)
            stamp = now()
            db.execute("UPDATE phase_schemes SET status='in_review',submitted_at=?,updated_at=?,lock_version=lock_version+1 WHERE id=?", (stamp, stamp, scheme_id))
            self.audit("scheme.submit", project_id, str(scheme_id), {"code": row["code"], "version_no": row["version_no"]}, actor_id)
            return dict(db.execute("SELECT * FROM phase_schemes WHERE id=?", (scheme_id,)).fetchone())

    def review_scheme(self, scheme_id: int, project_id: int, payload: dict[str, Any], actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "reviewer"})
        stamp = now()
        with transaction(immediate=True) as db:
            row = db.execute("SELECT * FROM phase_schemes WHERE id=?", (scheme_id,)).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ServiceError("scheme_not_found", "方案不存在", 404)
            if row["status"] != "in_review":
                raise ServiceError("not_in_review", "方案不在评审中", 409)
            if payload.get("expected_lock_version") is not None and payload["expected_lock_version"] != row["lock_version"]:
                raise ServiceError("stale_version", "方案状态已变化，请刷新后重试", 409)
            if row["created_by"] == actor_id:
                raise ServiceError("self_review", "提案人不能评审自己的方案", 403)
            try:
                db.execute("INSERT INTO scheme_reviews(scheme_id,reviewer_id,vote,comment,created_at) VALUES(?,?,?,?,?)", (scheme_id, actor_id, payload["vote"], payload.get("comment", ""), stamp))
            except sqlite3.IntegrityError as exc:
                raise ServiceError("already_reviewed", "该评审人已投票，不能重复评审", 409) from exc
            db.execute("UPDATE phase_schemes SET updated_at=?,lock_version=lock_version+1 WHERE id=?", (stamp, scheme_id))
            self.audit("scheme.review", project_id, str(scheme_id), {"vote": payload["vote"]}, actor_id)
            votes = db.execute("SELECT vote, COUNT(*) AS n FROM scheme_reviews WHERE scheme_id=? GROUP BY vote", (scheme_id,)).fetchall()
            return {"scheme_id": scheme_id, "votes": {r["vote"]: r["n"] for r in votes}, "required": row["required_reviews"]}

    def publish_scheme(self, scheme_id: int, project_id: int, actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner"})
        stamp = now()
        with transaction(immediate=True) as db:
            row = db.execute("SELECT * FROM phase_schemes WHERE id=?", (scheme_id,)).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ServiceError("scheme_not_found", "方案不存在", 404)
            if row["status"] != "in_review":
                raise ServiceError("not_in_review", "只有评审中的方案可以发布", 409)
            votes = {r["vote"]: r["n"] for r in db.execute("SELECT vote, COUNT(*) AS n FROM scheme_reviews WHERE scheme_id=? GROUP BY vote", (scheme_id,)).fetchall()}
            if votes.get("reject", 0) > 0:
                raise ServiceError("rejected_vote", "存在反对票，不能发布", 409)
            if votes.get("approve", 0) < row["required_reviews"]:
                raise ServiceError("reviews_pending", f"批准票不足，需要 {row['required_reviews']} 票", 409)
            result = self.compute_snapshot(row["snapshot_id"], project_id)
            phases = self._j(row["content_json"])
            bounds = phase_boundaries(phases, result["strata"])
            content_hash = request_hash({"snapshot": row["snapshot_id"], "phases": phases})
            # 计算缓存按证据集哈希寻址，本快照可能复用了其他快照的计算行
            result_hash = db.execute("SELECT result_hash FROM computations WHERE evidence_hash=?", (result["evidence_hash"],)).fetchone()["result_hash"]
            cur = db.execute(
                """INSERT INTO scheme_versions(scheme_id,project_id,version_no,snapshot_id,content_hash,content_json,
                   result_hash,published_by,published_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (scheme_id, project_id, row["version_no"], row["snapshot_id"], content_hash, stable_json(phases), result_hash, actor_id, stamp),
            )
            version_id = cur.lastrowid
            strata_sites = self._snapshot_strata(row["snapshot_id"])
            for index, phase in enumerate(phases):
                db.execute(
                    "INSERT INTO scheme_conclusions(version_id,phase_index,label,site_codes_json,stratum_codes_json,boundary_json,supporting_evidence_json) VALUES(?,?,?,?,?,?,?)",
                    (version_id, index, phase["label"], stable_json(sorted({strata_sites.get(s, "?") for s in phase["stratum_codes"]})),
                     stable_json(phase["stratum_codes"]), stable_json(bounds[phase["label"]]), stable_json(phase.get("supporting_evidence", []))),
                )
            db.execute("UPDATE phase_schemes SET status='published',published_at=?,updated_at=?,lock_version=lock_version+1 WHERE id=?", (stamp, stamp, scheme_id))
            self.audit("scheme.publish", project_id, str(scheme_id), {"code": row["code"], "version_no": row["version_no"], "version_id": version_id}, actor_id)
            return self._version_payload(db, version_id)

    def _version_payload(self, db: sqlite3.Connection, version_id: int) -> dict[str, Any]:
        version = dict(db.execute("SELECT * FROM scheme_versions WHERE id=?", (version_id,)).fetchone())
        conclusions = [dict(r) for r in db.execute("SELECT * FROM scheme_conclusions WHERE version_id=? ORDER BY phase_index", (version_id,)).fetchall()]
        for conclusion in conclusions:
            for key in ("site_codes_json", "stratum_codes_json", "supporting_evidence_json"):
                conclusion[key.replace("_json", "")] = self._j(conclusion[key])
            conclusion["boundary"] = self._j(conclusion["boundary_json"])
        version["conclusions"] = conclusions
        version["phases"] = self._j(version["content_json"])
        return version

    def list_schemes(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM phase_schemes WHERE project_id=? ORDER BY code,version_no", (project_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["phases"] = self._j(row["content_json"])
            item["reviews"] = [dict(r) for r in self.db.execute("SELECT reviewer_id,vote,comment,created_at FROM scheme_reviews WHERE scheme_id=? ORDER BY id", (row["id"],)).fetchall()]
            out.append(item)
        return out

    def get_scheme(self, scheme_id: int, project_id: int) -> dict[str, Any]:
        row = self._get("phase_schemes", scheme_id, project_id)
        item = dict(row)
        item["phases"] = self._j(row["content_json"])
        item["candidate_diff"] = self._j(row["candidate_diff_json"])
        item["reviews"] = [dict(r) for r in self.db.execute("SELECT * FROM scheme_reviews WHERE scheme_id=? ORDER BY id", (scheme_id,)).fetchall()]
        return item

    def list_versions(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT v.*, p.code AS scheme_code FROM scheme_versions v
               JOIN phase_schemes p ON p.id=v.scheme_id WHERE v.project_id=? ORDER BY p.code,v.version_no""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, version_id: int, project_id: int) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM scheme_versions WHERE id=?", (version_id,)).fetchone()
        if row is None or row["project_id"] != project_id:
            raise ServiceError("version_not_found", "发布版本不存在", 404)
        return self._version_payload(self.db, version_id)

    # ---- 引用撤回与待复核联动 -------------------------------------------

    def retract_citation(self, citation_id: int, project_id: int, actor_id: int, reason: str) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher"})
        citation = self._get("citations", citation_id, project_id)
        stamp = now()
        with transaction(immediate=True) as db:
            if citation["status"] == "retracted":
                raise ServiceError("already_retracted", "该引用已撤回", 409)
            db.execute("UPDATE citations SET status='retracted',retracted_at=?,updated_at=? WHERE id=?", (stamp, stamp, citation_id))
            evidence_rows = db.execute("SELECT id,code FROM evidence WHERE project_id=? AND citation_id=? AND status='active'", (project_id, citation_id)).fetchall()
            retracted_codes = {r["code"] for r in evidence_rows}
            flagged: list[dict[str, Any]] = []

            def open_flag_exists(**criteria: Any) -> bool:
                where = " AND ".join(f"{k}=?" for k in criteria)
                return db.execute(f"SELECT 1 FROM review_flags WHERE status='open' AND {where}", tuple(criteria.values())).fetchone() is not None

            def add_flag(**fields: Any) -> None:
                if open_flag_exists(version_id=fields.get("version_id"), conclusion_id=fields.get("conclusion_id"), scheme_id=fields.get("scheme_id"), citation_id=citation_id):
                    return
                db.execute(
                    """INSERT INTO review_flags(project_id,version_id,scheme_id,conclusion_id,citation_id,evidence_id,reason,created_by,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (project_id, fields.get("version_id"), fields.get("scheme_id"), fields.get("conclusion_id"), citation_id, fields.get("evidence_id"), fields["reason"], actor_id, stamp),
                )
                flagged.append(fields["reason"])

            # 已发布结论：直接引用或通过"类型学范围 <- 出土事实"知识依赖间接引用
            versions = db.execute("SELECT * FROM scheme_versions WHERE project_id=?", (project_id,)).fetchall()
            for version in versions:
                result = self.compute_snapshot(version["snapshot_id"], project_id)
                dependent_finds = {find for find, range_code in result.get("find_dependencies", {}).items() if range_code in retracted_codes}
                conclusions = db.execute("SELECT * FROM scheme_conclusions WHERE version_id=?", (version["id"],)).fetchall()
                for conclusion in conclusions:
                    supporting = set(self._j(conclusion["supporting_evidence_json"]))
                    hit = supporting & retracted_codes or supporting & dependent_finds
                    if hit:
                        add_flag(version_id=version["id"], conclusion_id=conclusion["id"], evidence_id=None,
                                reason=f"引用 {citation['cite_key']} 已撤回，结论 {conclusion['label']} 的支撑证据 {sorted(hit)} 待复核")
            # 在途候选：快照包含受影响证据则挂方案级标记
            candidates = db.execute("SELECT * FROM phase_schemes WHERE project_id=? AND status IN ('proposed','in_review')", (project_id,)).fetchall()
            for scheme in candidates:
                items = {r["code"] for r in db.execute(
                    """SELECT e.code FROM snapshot_items si JOIN evidence e ON e.id=si.evidence_id
                       WHERE si.snapshot_id=? AND e.citation_id=?""", (scheme["snapshot_id"], citation_id)).fetchall()}
                if items:
                    add_flag(scheme_id=scheme["id"], reason=f"引用 {citation['cite_key']} 已撤回，候选方案 {scheme['code']} v{scheme['version_no']} 含证据 {sorted(items)}，待复核")
            self.audit("citation.retract", project_id, str(citation_id), {"cite_key": citation["cite_key"], "flags": len(flagged)}, actor_id)
            return {"citation_id": citation_id, "cite_key": citation["cite_key"], "status": "retracted", "affected_evidence": sorted(retracted_codes), "flags_created": len(flagged)}

    def list_flags(self, project_id: int, status_filter: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM review_flags WHERE project_id=?"
        args: list[Any] = [project_id]
        if status_filter:
            sql += " AND status=?"
            args.append(status_filter)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args).fetchall()]

    def resolve_flag(self, flag_id: int, project_id: int, actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, {"owner", "researcher", "reviewer"})
        with transaction(immediate=True) as db:
            row = db.execute("SELECT * FROM review_flags WHERE id=?", (flag_id,)).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ServiceError("flag_not_found", "复核标记不存在", 404)
            db.execute("UPDATE review_flags SET status='resolved',resolved_at=? WHERE id=?", (now(), flag_id))
            self.audit("flag.resolve", project_id, str(flag_id), {}, actor_id)
            return dict(db.execute("SELECT * FROM review_flags WHERE id=?", (flag_id,)).fetchone())

    # ---- 按时间与区域过滤的查询 -----------------------------------------

    def chronology_view(self, project_id: int, snapshot_id: int | None, region_id: int | None, from_year: int | None, to_year: int | None) -> dict[str, Any]:
        if snapshot_id is None:
            latest = self.db.execute("SELECT id FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
            if latest is None:
                raise ServiceError("no_snapshot", "项目尚无证据快照，请先创建", 404)
            snapshot_id = latest[0]
        result = self.compute_snapshot(snapshot_id, project_id)
        rows = self.db.execute(
            """SELECT t.code AS stratum_code, t.name AS stratum_name, s.id AS site_id, s.code AS site_code, s.name AS site_name, r.id AS region_id, r.code AS region_code
               FROM strata t JOIN sites s ON s.id=t.site_id LEFT JOIN regions r ON r.id=s.region_id
               WHERE t.project_id=?""",
            (project_id,),
        ).fetchall()
        sites_out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if region_id is not None and row["region_id"] != region_id:
                continue
            bounds = result["strata"].get(row["stratum_code"])
            if bounds is None:
                continue
            envelope = {"start": bounds["start"], "end": bounds["end"]}
            if (from_year is not None or to_year is not None) and not envelope_overlaps(envelope, from_year, to_year):
                continue
            site = sites_out.setdefault(row["site_code"], {"site_id": row["site_id"], "site_code": row["site_code"], "name": row["site_name"], "region_code": row["region_code"], "strata": []})
            site["strata"].append({"code": row["stratum_code"], "name": row["stratum_name"], **bounds})
        return {
            "snapshot_id": snapshot_id,
            "filter": {"region_id": region_id, "from_year": from_year, "to_year": to_year,
                       "unknown_boundary_policy": "open boundaries are never excluded and never filled with point estimates"},
            "consistent": result["consistent"],
            "conflicts": result["conflicts"],
            "sites": sorted(sites_out.values(), key=lambda item: item["site_code"]),
        }

    # ---- 离线导入导出 ---------------------------------------------------

    EXPORT_TABLES = ("regions", "sites", "strata", "citations", "evidence", "snapshots",
                     "phase_schemes", "scheme_versions", "review_flags")

    def export_bundle(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        bundle: dict[str, Any] = {"format": "chrono-bundle/1", "exported_at": now(), "project": dict(self._project(project_id))}
        for table in self.EXPORT_TABLES:
            bundle[table] = [dict(r) for r in self.db.execute(f"SELECT * FROM {table} WHERE project_id=?", (project_id,)).fetchall()]
        # computations 无 project_id 列，按项目快照集合导出
        bundle["computations"] = [dict(r) for r in self.db.execute(
            "SELECT c.* FROM computations c JOIN snapshots s ON s.id=c.snapshot_id WHERE s.project_id=? ORDER BY c.id",
            (project_id,)).fetchall()]
        # 以下表无 project_id 列，按所属快照/方案/版本集合导出
        bundle["snapshot_items"] = [dict(r) for r in self.db.execute(
            "SELECT si.* FROM snapshot_items si JOIN snapshots s ON s.id=si.snapshot_id WHERE s.project_id=?", (project_id,)).fetchall()]
        bundle["scheme_reviews"] = [dict(r) for r in self.db.execute(
            "SELECT r.* FROM scheme_reviews r JOIN phase_schemes p ON p.id=r.scheme_id WHERE p.project_id=?", (project_id,)).fetchall()]
        bundle["scheme_conclusions"] = [dict(r) for r in self.db.execute(
            "SELECT c.* FROM scheme_conclusions c JOIN scheme_versions v ON v.id=c.version_id WHERE v.project_id=?", (project_id,)).fetchall()]
        return bundle

    def import_bundle(self, bundle: dict[str, Any], *, new_code: str | None = None, actor_id: int | None = None) -> dict[str, Any]:
        if bundle.get("format") != "chrono-bundle/1":
            raise ServiceError("bad_bundle", "不支持的导入格式", 422)
        project = dict(bundle["project"])
        if new_code:
            project["code"] = new_code
        stamp = now()
        id_map: dict[str, dict[int, int]] = {}
        skipped_reviews = 0
        with transaction(immediate=True) as db:
            cur = db.execute("INSERT INTO projects(code,name,site_name,created_at,updated_at) VALUES(?,?,?,?,?)",
                             (project["code"], project["name"], project["site_name"], project.get("created_at", stamp), stamp))
            project_id = cur.lastrowid
            if actor_id is not None:
                db.execute("INSERT INTO project_members(project_id,user_id,role,joined_at) VALUES(?,?,?,?)", (project_id, actor_id, "owner", stamp))

            def load(table: str, rows: list[dict[str, Any]], columns: list[str], convert) -> dict[int, int]:
                mapping: dict[int, int] = {}
                marks = ",".join("?" * len(columns))
                for row in rows:
                    if not convert.allowed(row):
                        continue
                    cursor = db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({marks})", convert.values(row))
                    mapping[row["id"]] = cursor.lastrowid
                return mapping

            class M:
                """逐行转换：固定列、外键 id 映射与可空处理。"""

                def __init__(self, columns: list[str], *, const: dict[str, Any] | None = None, maps: dict[str, str] | None = None):
                    self.columns, self.const, self.maps = columns, const or {}, maps or {}

                def allowed(self, row: dict[str, Any]) -> bool:
                    return True

                def values(self, row: dict[str, Any]) -> list[Any]:
                    out = []
                    for column in self.columns:
                        if column in self.const:
                            out.append(self.const[column])
                        elif column in self.maps:
                            old = row[column]
                            out.append(id_map[self.maps[column]].get(old) if old is not None else None)
                        else:
                            out.append(row[column])
                    return out

            id_map["regions"] = load("regions", bundle["regions"],
                                     ["project_id", "code", "name", "created_at", "updated_at"],
                                     M(["project_id", "code", "name", "created_at", "updated_at"], const={"project_id": project_id}))
            id_map["sites"] = load("sites", bundle["sites"],
                                   ["project_id", "region_id", "code", "name", "location_note", "created_at", "updated_at"],
                                   M(["project_id", "region_id", "code", "name", "location_note", "created_at", "updated_at"],
                                     const={"project_id": project_id}, maps={"region_id": "regions"}))
            id_map["strata"] = load("strata", bundle["strata"],
                                    ["project_id", "site_id", "code", "name", "note", "created_at", "updated_at"],
                                    M(["project_id", "site_id", "code", "name", "note", "created_at", "updated_at"],
                                      const={"project_id": project_id}, maps={"site_id": "sites"}))
            id_map["citations"] = load("citations", bundle["citations"],
                                       ["project_id", "cite_key", "title", "author", "published_year", "status", "retracted_at", "created_at", "updated_at"],
                                       M(["project_id", "cite_key", "title", "author", "published_year", "status", "retracted_at", "created_at", "updated_at"], const={"project_id": project_id}))
            id_map["evidence"] = load("evidence", bundle["evidence"],
                                      ["project_id", "code", "evidence_class", "kind", "stratum_id", "site_id", "method", "probability",
                                       "lower_year", "upper_year", "artifact_type", "relation", "subject_stratum_id", "object_stratum_id",
                                       "gap_years", "label_text", "citation_id", "note", "content_hash", "supersedes_code", "status",
                                       "created_by", "created_at", "updated_at"],
                                      M(["project_id", "code", "evidence_class", "kind", "stratum_id", "site_id", "method", "probability",
                                         "lower_year", "upper_year", "artifact_type", "relation", "subject_stratum_id", "object_stratum_id",
                                         "gap_years", "label_text", "citation_id", "note", "content_hash", "supersedes_code", "status",
                                         "created_by", "created_at", "updated_at"],
                                        const={"project_id": project_id, "created_by": actor_id},
                                        maps={"stratum_id": "strata", "site_id": "sites", "subject_stratum_id": "strata",
                                              "object_stratum_id": "strata", "citation_id": "citations"}))
            id_map["snapshots"] = load("snapshots", bundle["snapshots"],
                                       ["project_id", "code", "note", "region_id", "evidence_hash", "created_by", "created_at"],
                                       M(["project_id", "code", "note", "region_id", "evidence_hash", "created_by", "created_at"],
                                         const={"project_id": project_id, "created_by": actor_id}, maps={"region_id": "regions"}))
            for row in bundle["snapshot_items"]:
                db.execute("INSERT INTO snapshot_items(snapshot_id,evidence_id,content_hash) VALUES(?,?,?)",
                           (id_map["snapshots"][row["snapshot_id"]], id_map["evidence"][row["evidence_id"]], row["content_hash"]))
            for row in bundle["computations"]:
                # computations 按证据集哈希全局去重：相同证据集的缓存跨项目复用
                db.execute(
                    "INSERT OR IGNORE INTO computations(snapshot_id,evidence_hash,result_hash,consistent,result_json,created_at) VALUES(?,?,?,?,?,?)",
                    (id_map["snapshots"][row["snapshot_id"]], row["evidence_hash"], row["result_hash"], row["consistent"], row["result_json"], row["created_at"]))
            id_map["phase_schemes"] = {}
            for row in bundle["phase_schemes"]:
                cursor = db.execute(
                    """INSERT INTO phase_schemes(project_id,code,title,version_no,parent_version_id,snapshot_id,status,
                       required_reviews,content_json,candidate_diff_json,lock_version,created_by,created_at,updated_at,
                       submitted_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (project_id, row["code"], row["title"], row["version_no"], None, id_map["snapshots"][row["snapshot_id"]],
                     row["status"], row["required_reviews"], row["content_json"], row["candidate_diff_json"], row["lock_version"],
                     actor_id, row["created_at"], row["updated_at"], row["submitted_at"], row["published_at"]))
                id_map["phase_schemes"][row["id"]] = cursor.lastrowid
            # 评审投票引用源库用户，目标库无对应用户则不迁入（投票属工作流历史；发布版本内容本身完整保留）
            valid_users = {r[0] for r in db.execute("SELECT id FROM users").fetchall()}
            for row in bundle["scheme_reviews"]:
                if row["reviewer_id"] not in valid_users:
                    skipped_reviews += 1
                    continue
                db.execute("INSERT INTO scheme_reviews(scheme_id,reviewer_id,vote,comment,created_at) VALUES(?,?,?,?,?)",
                           (id_map["phase_schemes"][row["scheme_id"]], row["reviewer_id"], row["vote"], row["comment"], row["created_at"]))
            id_map["scheme_versions"] = {}
            for row in bundle["scheme_versions"]:
                new_scheme_id = id_map["phase_schemes"][row["scheme_id"]]
                cursor = db.execute(
                    """INSERT INTO scheme_versions(scheme_id,project_id,version_no,snapshot_id,content_hash,content_json,
                       result_hash,published_by,published_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (new_scheme_id, project_id, row["version_no"], id_map["snapshots"][row["snapshot_id"]],
                     row["content_hash"], row["content_json"], row["result_hash"], actor_id, row["published_at"]))
                id_map["scheme_versions"][row["id"]] = cursor.lastrowid
            for row in bundle["phase_schemes"]:
                if row["parent_version_id"]:
                    db.execute("UPDATE phase_schemes SET parent_version_id=? WHERE id=?",
                               (id_map["scheme_versions"][row["parent_version_id"]], id_map["phase_schemes"][row["id"]]))
            id_map["scheme_conclusions"] = {}
            for row in bundle["scheme_conclusions"]:
                cursor = db.execute(
                    "INSERT INTO scheme_conclusions(version_id,phase_index,label,site_codes_json,stratum_codes_json,boundary_json,supporting_evidence_json) VALUES(?,?,?,?,?,?,?)",
                    (id_map["scheme_versions"][row["version_id"]], row["phase_index"], row["label"], row["site_codes_json"],
                     row["stratum_codes_json"], row["boundary_json"], row["supporting_evidence_json"]))
                id_map["scheme_conclusions"][row["id"]] = cursor.lastrowid
            for row in bundle.get("review_flags", []):
                db.execute(
                    """INSERT INTO review_flags(project_id,version_id,scheme_id,conclusion_id,citation_id,evidence_id,reason,status,created_by,created_at,resolved_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (project_id, id_map["scheme_versions"].get(row["version_id"]), id_map["phase_schemes"].get(row["scheme_id"]),
                     id_map["scheme_conclusions"].get(row["conclusion_id"]), id_map["citations"].get(row["citation_id"]),
                     id_map["evidence"].get(row["evidence_id"]), row["reason"], row["status"], actor_id, row["created_at"], row["resolved_at"]))
            self.audit("bundle.import", project_id, str(project_id),
                       {"code": project["code"], "source_exported_at": bundle.get("exported_at"), "skipped_reviews": skipped_reviews}, actor_id)

        # 复算校验：相同证据集必须得到相同结果哈希（可复现性）
        verified: list[dict[str, Any]] = []
        for old_snapshot in bundle["snapshots"]:
            new_snapshot_id = id_map["snapshots"][old_snapshot["id"]]
            fresh = self.compute_snapshot(new_snapshot_id, project_id)
            stored = self.db.execute("SELECT result_hash FROM computations WHERE evidence_hash=?", (fresh["evidence_hash"],)).fetchone()
            verified.append({"snapshot": old_snapshot["code"], "result_hash_matches": fresh.get("result_hash") == stored["result_hash"]})
        return {"project_id": project_id, "code": project["code"], "skipped_reviews": skipped_reviews, "verified": verified}
