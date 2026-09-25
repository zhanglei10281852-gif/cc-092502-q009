"""区域年代序列证据服务：登记、快照求解、文化阶段方案工作流、撤回联动与离线导入导出。

设计要点：
- 证据行只追加：更正通过新 rev 实现，旧行置 superseded，绝不就地覆盖；
- 快照固定证据版本（id+rev+content_hash），计算结果缓存且带 result_hash，可复现；
- 文化阶段方案 proposed -> 评审 -> published，发布行不可变，新证据只能产生候选修订；
- 引用撤回不删除任何数据，只令依赖结论挂起待复核标记；
- 一切写操作在 IMMEDIATE 事务内完成并写审计。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.database import connection, now, transaction
from app.security import request_hash, stable_json
from app.service import ResearchService, ServiceError
from app.chronology.solver import ALGORITHM_VERSION, solve

EVIDENCE_KIND_CATEGORY = {
    "dating": "observation",
    "typology": "observation",
    "ordering": "inference",
    "label": "tentative_label",
}

ROLE_READ = {"owner", "researcher", "recorder", "reviewer", "viewer"}
ROLE_WRITE = {"owner", "researcher", "recorder"}
ROLE_PROPOSE = {"owner", "researcher"}
ROLE_REVIEW = {"owner", "reviewer"}
ROLE_PUBLISH = {"owner"}
ROLE_RETRACT = {"owner", "researcher"}
ROLE_RESOLVE = {"owner", "researcher", "reviewer"}

CONTENT_FIELDS = {
    "dating": ("sample_code", "method", "probability", "bound_lo", "bound_hi", "ranges", "basis", "note"),
    "typology": ("artifact_type", "bound_lo", "bound_hi", "ranges", "basis", "note"),
    "ordering": ("gap_years", "basis", "note"),
    "label": ("label_text", "basis", "note"),
}


class ChronologyService:
    def __init__(self, db: sqlite3.Connection | None = None):
        self.db = db or connection()
        self.base = ResearchService(self.db)

    # ---------- 通用辅助 ----------

    def require(self, project_id: int, user_id: int, allowed: set[str]) -> str:
        return self.base.require_role(project_id, user_id, allowed)

    def audit(self, action: str, project_id: int, actor_id: int | None, resource_type: str, resource_id: str, payload: dict[str, Any]) -> None:
        self.base.audit(action, resource_type, resource_id, payload, project_id=project_id, actor_id=actor_id)

    def _project(self, project_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ServiceError("project_not_found", "项目不存在", 404)
        return row

    def _site(self, project_id: int, site_key: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM ch_sites WHERE project_id=? AND site_key=?", (project_id, site_key)).fetchone()
        if row is None:
            raise ServiceError("site_not_found", f"遗址 {site_key} 不存在", 404)
        return row

    def _layer_row(self, project_id: int, layer_key: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM ch_layers WHERE project_id=? AND layer_key=?", (project_id, layer_key)).fetchone()
        if row is None:
            raise ServiceError("layer_not_found", f"文化层 {layer_key} 不存在", 404)
        return row

    def _source_row(self, project_id: int, source_key: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM ch_sources WHERE project_id=? AND source_key=?", (project_id, source_key)).fetchone()
        if row is None:
            raise ServiceError("source_not_found", f"出处 {source_key} 不存在", 404)
        return row

    def _source_id(self, project_id: int, source_key: str | None, *, must_be_active: bool = True) -> int | None:
        if not source_key:
            return None
        row = self._source_row(project_id, source_key)
        if must_be_active and row["status"] != "active":
            raise ServiceError("source_retracted", f"出处 {source_key} 已撤回，不能用于新证据", 422)
        return row["id"]

    def _snapshot(self, project_id: int, snapshot_key: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM ch_snapshots WHERE project_id=? AND snapshot_key=?", (project_id, snapshot_key)).fetchone()
        if row is None:
            raise ServiceError("snapshot_not_found", f"证据快照 {snapshot_key} 不存在", 404)
        return row

    def _layer_order(self, project_id: int) -> list[str]:
        rows = self.db.execute(
            "SELECT layer_key FROM ch_layers WHERE project_id=? "
            "ORDER BY site_id, sequence_no IS NULL, sequence_no, id",
            (project_id,),
        ).fetchall()
        return [r["layer_key"] for r in rows]

    # ---------- 登记：遗址 / 文化层 / 出处 ----------

    def create_site(self, project_id: int, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_WRITE)
        self._project(project_id)
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(
                    "INSERT INTO ch_sites(project_id,site_key,name,region,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (project_id, payload["site_key"], payload["name"], payload.get("region", ""),
                     payload.get("latitude"), payload.get("longitude"), stamp, stamp),
                )
                self.audit("chron.site.create", project_id, actor_id, "ch_site", payload["site_key"], payload)
                return dict(db.execute("SELECT * FROM ch_sites WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("site_exists", "遗址键已存在", 409) from exc

    def create_layer(self, project_id: int, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_WRITE)
        site = self._site(project_id, payload["site_key"])
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(
                    "INSERT INTO ch_layers(project_id,site_id,layer_key,name,sequence_no,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (project_id, site["id"], payload["layer_key"], payload["name"], payload.get("sequence_no"), stamp, stamp),
                )
                self.audit("chron.layer.create", project_id, actor_id, "ch_layer", payload["layer_key"], payload)
                return dict(db.execute("SELECT * FROM ch_layers WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("layer_exists", "文化层键已存在", 409) from exc

    def create_source(self, project_id: int, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_WRITE)
        stamp = now()
        try:
            with transaction(immediate=True) as db:
                cur = db.execute(
                    "INSERT INTO ch_sources(project_id,source_key,title,authors,citation,year_pub,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (project_id, payload["source_key"], payload["title"], payload.get("authors", ""),
                     payload.get("citation", ""), payload.get("year_pub"), payload.get("note", ""), stamp, stamp),
                )
                self.audit("chron.source.create", project_id, actor_id, "ch_source", payload["source_key"], payload)
                return dict(db.execute("SELECT * FROM ch_sources WHERE id=?", (cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ServiceError("source_exists", "出处键已存在", 409) from exc

    # ---------- 登记：证据 ----------

    def _evidence_content(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {field: payload.get(field) for field in CONTENT_FIELDS[kind]}

    def _insert_evidence(self, db: sqlite3.Connection, project_id: int, kind: str, payload: dict[str, Any],
                         actor_id: int, *, rev: int, supersedes_id: int | None) -> int:
        layer = self._layer_row(project_id, payload["layer_key"])
        other_id = None
        if kind == "ordering":
            if payload["other_layer_key"] == payload["layer_key"]:
                raise ServiceError("self_ordering", "先后约束的两个文化层不能相同", 422)
            other_id = self._layer_row(project_id, payload["other_layer_key"])["id"]
        source_id = self._source_id(project_id, payload.get("source_key"))
        content = self._evidence_content(kind, payload)
        if kind in ("dating", "typology"):
            lo, hi = content.get("bound_lo"), content.get("bound_hi")
            if lo is None and hi is None and not content.get("ranges"):
                raise ServiceError("empty_span", "测年/类型学证据至少需要一个边界或概率段", 422)
            if lo is not None and hi is not None and lo > hi:
                raise ServiceError("inverted_span", "较年轻边界（bound_lo）不得老于较老边界（bound_hi），单位 cal BP", 422)
            for rng in content.get("ranges") or []:
                if rng["hi"] < rng["lo"]:
                    raise ServiceError("inverted_range", "概率段方向倒置，单位 cal BP", 422)
        material = {
            "kind": kind, "layer_key": payload["layer_key"],
            "other_layer_key": payload.get("other_layer_key"), "source_key": payload.get("source_key"),
            "content": content,
        }
        digest = request_hash(material)
        cur = db.execute(
            "INSERT INTO ch_evidence(project_id,evidence_key,rev,supersedes_id,kind,category,basis,source_id,"
            "layer_id,other_layer_id,artifact_type,sample_code,method,probability,bound_lo,bound_hi,"
            "ranges_json,gap_years,label_text,note,status,content_hash,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, payload["evidence_key"], rev, supersedes_id, kind, EVIDENCE_KIND_CATEGORY[kind],
             content.get("basis", "") or "", source_id, layer["id"], other_id,
             content.get("artifact_type", "") or "", content.get("sample_code", "") or "",
             content.get("method", "") or "", content.get("probability", "") or "",
             content.get("bound_lo"), content.get("bound_hi"),
             stable_json(content.get("ranges") or []), int(content.get("gap_years") or 0),
             content.get("label_text", "") or "", content.get("note", "") or "",
             "active", digest, actor_id, now()),
        )
        return cur.lastrowid

    def add_evidence(self, project_id: int, actor_id: int, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_WRITE)
        if kind not in EVIDENCE_KIND_CATEGORY:
            raise ServiceError("unknown_kind", "未知证据类型", 400)
        with transaction(immediate=True) as db:
            exists = db.execute(
                "SELECT id FROM ch_evidence WHERE project_id=? AND evidence_key=? AND status='active'",
                (project_id, payload["evidence_key"]),
            ).fetchone()
            if exists:
                raise ServiceError("evidence_exists", "证据键已有生效版本，请用修订接口更正", 409)
            new_id = self._insert_evidence(db, project_id, kind, payload, actor_id, rev=1, supersedes_id=None)
            self.audit(f"chron.evidence.{kind}.create", project_id, actor_id, "ch_evidence", payload["evidence_key"], payload)
            return self._serialize_evidence(db.execute("SELECT * FROM ch_evidence WHERE id=?", (new_id,)).fetchone())

    def revise_evidence(self, project_id: int, actor_id: int, kind: str, evidence_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_WRITE)
        with transaction(immediate=True) as db:
            old = db.execute(
                "SELECT * FROM ch_evidence WHERE project_id=? AND evidence_key=? ORDER BY rev DESC LIMIT 1",
                (project_id, evidence_key),
            ).fetchone()
            if old is None:
                raise ServiceError("evidence_not_found", "证据不存在，应使用登记接口", 404)
            if old["kind"] != kind:
                raise ServiceError("kind_mismatch", f"修订必须保持证据类型 {old['kind']}", 422)
            old_layer = self._layer_key_by_id(project_id, old["layer_id"])
            if payload["layer_key"] != old_layer:
                raise ServiceError("layer_locked", "证据修订不能更换所属文化层；请登记新证据", 422)
            if kind == "ordering":
                old_other = self._layer_key_by_id(project_id, old["other_layer_id"])
                if payload["other_layer_key"] != old_other:
                    raise ServiceError("layer_locked", "先后约束修订不能更换相对文化层", 422)
            next_rev = old["rev"] + 1
            new_id = self._insert_evidence(db, project_id, kind, {**payload, "evidence_key": evidence_key},
                                           actor_id, rev=next_rev, supersedes_id=old["id"])
            db.execute("UPDATE ch_evidence SET status='superseded' WHERE id=?", (old["id"],))
            self.audit("chron.evidence.revise", project_id, actor_id, "ch_evidence", evidence_key,
                       {"evidence_key": evidence_key, "old_rev": old["rev"], "new_rev": next_rev})
            return self._serialize_evidence(db.execute("SELECT * FROM ch_evidence WHERE id=?", (new_id,)).fetchone())

    def _layer_key_by_id(self, project_id: int, layer_id: int | None) -> str | None:
        if layer_id is None:
            return None
        row = self.db.execute("SELECT layer_key FROM ch_layers WHERE project_id=? AND id=?", (project_id, layer_id)).fetchone()
        return row["layer_key"] if row else None

    def _serialize_evidence(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["ranges"] = json.loads(out.pop("ranges_json"))
        out.pop("content_hash", None)
        return out

    def list_evidence(self, project_id: int, actor_id: int, *, kind: str | None = None, layer_key: str | None = None,
                      site_key: str | None = None, region: str | None = None,
                      younger_than: int | None = None, older_than: int | None = None,
                      include_superseded: bool = False) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        sql = ("SELECT e.*, s.site_key, s.region AS site_region, src.source_key AS ref_source_key, "
               "src.status AS source_status, l.layer_key AS ref_layer_key "
               "FROM ch_evidence e "
               "JOIN ch_layers l ON l.id=e.layer_id "
               "JOIN ch_sites s ON s.id=l.site_id "
               "LEFT JOIN ch_sources src ON src.id=e.source_id WHERE e.project_id=?")
        args: list[Any] = [project_id]
        if not include_superseded:
            sql += " AND e.status='active'"
        if kind:
            sql += " AND e.kind=?"
            args.append(kind)
        if layer_key:
            sql += " AND l.layer_key=?"
            args.append(layer_key)
        if site_key:
            sql += " AND s.site_key=?"
            args.append(site_key)
        if region:
            sql += " AND s.region=?"
            args.append(region)
        if younger_than is not None:
            # 与 [0, younger_than] 有重叠：最轻上界缺失或 >=0；简化为 bound_lo <= younger_than
            sql += " AND (e.bound_lo IS NULL OR e.bound_lo <= ?)"
            args.append(younger_than)
        if older_than is not None:
            sql += " AND (e.bound_hi IS NULL OR e.bound_hi >= ?)"
            args.append(older_than)
        sql += " ORDER BY s.site_key, l.sequence_no IS NULL, l.sequence_no, e.kind, e.evidence_key, e.rev"
        rows = self.db.execute(sql, args).fetchall()
        return {"data": [self._serialize_evidence(r) for r in rows]}

    # ---------- 证据快照 ----------

    def create_snapshot(self, project_id: int, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_WRITE)
        with transaction(immediate=True) as db:
            wanted = payload.get("evidence_keys")
            sql = "SELECT * FROM ch_evidence WHERE project_id=? AND status='active'"
            args: list[Any] = [project_id]
            if wanted is not None:
                if not wanted:
                    raise ServiceError("empty_snapshot", "快照至少需要一条证据，或省略 evidence_keys 纳入全部", 422)
                marks = ",".join("?" for _ in wanted)
                sql += f" AND evidence_key IN ({marks})"
                args.extend(wanted)
            sql += " ORDER BY evidence_key"
            rows = db.execute(sql, args).fetchall()
            found = {r["evidence_key"] for r in rows}
            if wanted is not None:
                missing = sorted(set(wanted) - found)
                if missing:
                    raise ServiceError("evidence_missing", f"证据不存在或已失效：{', '.join(missing)}", 422)
            pinned = [{"id": r["id"], "key": r["evidence_key"], "rev": r["rev"], "content_hash": r["content_hash"]} for r in rows]
            spec = {"layer_order": self._layer_order(project_id), "evidence": pinned}
            # 身份哈希只基于业务键（证据键/版本/内容哈希）与层序，不依赖数据库行 id，
            # 这样离线导入重映射行 id 后，快照身份与结果仍可逐位复核
            identity = {"layer_order": spec["layer_order"],
                        "evidence": sorted(({"key": p["key"], "rev": p["rev"], "content_hash": p["content_hash"]}
                                           for p in pinned), key=lambda p: p["key"])}
            digest = request_hash({"algorithm": ALGORITHM_VERSION, "spec": identity})
            stamp = now()
            try:
                cur = db.execute(
                    "INSERT INTO ch_snapshots(project_id,snapshot_key,title,algorithm_version,spec_json,evidence_hash,note,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (project_id, payload["snapshot_key"], payload["title"], ALGORITHM_VERSION,
                     stable_json(spec), digest, payload.get("note", ""), actor_id, stamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ServiceError("snapshot_exists", "快照键已存在，快照不可覆盖", 409) from exc
            self.audit("chron.snapshot.create", project_id, actor_id, "ch_snapshot", payload["snapshot_key"],
                       {"snapshot_key": payload["snapshot_key"], "evidence_count": len(pinned), "evidence_hash": digest})
            return dict(db.execute("SELECT * FROM ch_snapshots WHERE id=?", (cur.lastrowid,)).fetchone())

    def list_snapshots(self, project_id: int, actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        rows = self.db.execute("SELECT * FROM ch_snapshots WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        return {"data": [dict(r) for r in rows]}

    def _solver_evidence(self, snapshot: sqlite3.Row) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        spec = json.loads(snapshot["spec_json"])
        evidence: list[dict[str, Any]] = []
        retracted: list[str] = []
        for item in spec["evidence"]:
            row = self.db.execute(
                "SELECT e.*, src.status AS source_status FROM ch_evidence e "
                "LEFT JOIN ch_sources src ON src.id=e.source_id WHERE e.id=?",
                (item["id"],),
            ).fetchone()
            if row is None:
                raise ServiceError("snapshot_corrupt", f"快照固定的证据 {item['key']} 已不存在", 409)
            if row["content_hash"] != item["content_hash"] or row["rev"] != item["rev"]:
                raise ServiceError("snapshot_corrupt", "证据内容与快照固定值不一致", 409)
            if row["source_status"] == "retracted":
                retracted.append(row["evidence_key"])
            layer_key = self._layer_key_by_id(snapshot["project_id"], row["layer_id"])
            other_key = self._layer_key_by_id(snapshot["project_id"], row["other_layer_id"])
            evidence.append({
                "key": row["evidence_key"], "kind": row["kind"], "category": row["category"],
                "layer_key": layer_key, "other_layer_key": other_key,
                "bound_lo": row["bound_lo"], "bound_hi": row["bound_hi"],
                "ranges": json.loads(row["ranges_json"]), "gap_years": row["gap_years"],
            })
        return evidence, spec["layer_order"], sorted(set(retracted))

    def compute_snapshot(self, project_id: int, actor_id: int, snapshot_key: str, *, force: bool = False) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        snapshot = self._snapshot(project_id, snapshot_key)
        with transaction(immediate=True) as db:
            existing = db.execute("SELECT * FROM ch_computations WHERE snapshot_id=?", (snapshot["id"],)).fetchone()
            if existing is not None and not force:
                result = json.loads(existing["result_json"])
            else:
                evidence, layer_order, retracted = self._solver_evidence(snapshot)
                result = solve(evidence, layer_order)
                retracted_now = retracted
                if existing is not None:
                    old_hash = json.loads(existing["result_json"]).get("result_hash")
                    if old_hash != result["result_hash"]:
                        # 固定证据上结果必须恒定；不一致说明算法版本被更换
                        raise ServiceError("result_not_reproducible",
                                           f"重算结果哈希 {result['result_hash']} 与缓存 {old_hash} 不一致", 409)
                    db.execute("UPDATE ch_computations SET result_json=?, computed_by=?, created_at=? WHERE snapshot_id=?",
                               (stable_json(result), actor_id, now(), snapshot["id"]))
                else:
                    db.execute(
                        "INSERT INTO ch_computations(snapshot_id,result_hash,result_json,computed_by,created_at) VALUES(?,?,?,?,?)",
                        (snapshot["id"], result["result_hash"], stable_json(result), actor_id, now()),
                    )
                self.audit("chron.snapshot.compute", project_id, actor_id, "ch_snapshot", snapshot_key,
                           {"snapshot_key": snapshot_key, "result_hash": result["result_hash"],
                            "satisfiable": result["satisfiable"], "retracted_evidence": retracted_now})
                return {"snapshot_key": snapshot_key, "algorithm": ALGORITHM_VERSION,
                        "evidence_hash": snapshot["evidence_hash"], "retracted_evidence_keys": retracted_now, "result": result}
            retracted = self._solver_evidence(snapshot)[2]
            self.audit("chron.snapshot.compute", project_id, actor_id, "ch_snapshot", snapshot_key,
                       {"snapshot_key": snapshot_key, "result_hash": existing["result_hash"], "cached": True})
            return {"snapshot_key": snapshot_key, "algorithm": ALGORITHM_VERSION,
                    "evidence_hash": snapshot["evidence_hash"], "retracted_evidence_keys": retracted, "result": result}

    def get_computation(self, project_id: int, snapshot_key: str) -> dict[str, Any] | None:
        snapshot = self._snapshot(project_id, snapshot_key)
        row = self.db.execute("SELECT * FROM ch_computations WHERE snapshot_id=?", (snapshot["id"],)).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])

    # ---------- 文化阶段方案：提议 / 评审 / 发布 / 修订 ----------

    def _stage_layer_ids(self, project_id: int, stages: dict[str, str]) -> dict[str, int]:
        ids: dict[str, int] = {}
        for layer_key in stages:
            ids[layer_key] = self._layer_row(project_id, layer_key)["id"]
        return ids

    def propose_scheme(self, project_id: int, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_PROPOSE)
        snapshot = self._snapshot(project_id, payload["snapshot_key"])
        self._stage_layer_ids(project_id, payload["stages"])
        result = self.get_computation(project_id, payload["snapshot_key"])
        if result is None:
            result = self.compute_snapshot(project_id, actor_id, payload["snapshot_key"])["result"]
        if not result["satisfiable"]:
            raise ServiceError("snapshot_in_conflict", "快照存在不相容证据，请先解决冲突后再提议方案", 422)
        stamp = now()
        content = {"snapshot_key": payload["snapshot_key"], "stages": payload["stages"],
                   "result_hash": result["result_hash"]}
        digest = request_hash(content)
        with transaction(immediate=True) as db:
            scheme = db.execute("SELECT * FROM ch_schemes WHERE project_id=? AND scheme_key=?",
                                (project_id, payload["scheme_key"])).fetchone()
            if scheme is not None:
                raise ServiceError("scheme_exists", "方案键已存在；新版本请用候选修订接口", 409)
            cur_s = db.execute("INSERT INTO ch_schemes(project_id,scheme_key,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                               (project_id, payload["scheme_key"], payload["title"], stamp, stamp))
            cur = db.execute(
                "INSERT INTO ch_scheme_versions(project_id,scheme_id,version_no,status,snapshot_id,content_json,"
                "content_hash,approvals_required,created_by,created_at) VALUES(?,?,1,'proposed',?,?,?,?,?,?)",
                (project_id, cur_s.lastrowid, snapshot["id"], stable_json(content), digest,
                 payload.get("approvals_required", 2), actor_id, stamp),
            )
            version_id = cur.lastrowid
            self._flag_retracted(db, project_id, version_id, payload["stages"], snapshot["id"])
            self.audit("chron.scheme.propose", project_id, actor_id, "ch_scheme_version", str(version_id),
                       {"scheme_key": payload["scheme_key"], "snapshot_key": payload["snapshot_key"],
                        "stages": payload["stages"], "note": payload.get("note", "")})
            return self._version_detail(db, version_id)

    def _flag_retracted(self, db: sqlite3.Connection, project_id: int, version_id: int,
                        stages: dict[str, str], snapshot_id: int) -> int:
        spec = json.loads(db.execute("SELECT spec_json FROM ch_snapshots WHERE id=?", (snapshot_id,)).fetchone()["spec_json"])
        pinned_ids = [item["id"] for item in spec["evidence"]]
        count = 0
        for layer_key, stage_code in stages.items():
            layer_id = self._layer_row(project_id, layer_key)["id"]
            rows = db.execute(
                f"SELECT DISTINCT e.evidence_key, src.source_key FROM ch_evidence e "
                f"JOIN ch_sources src ON src.id=e.source_id "
                f"WHERE e.project_id=? AND e.layer_id=? AND src.status='retracted' "
                f"AND e.id IN ({','.join('?' for _ in pinned_ids)})",
                [project_id, layer_id, *pinned_ids],
            ).fetchall() if pinned_ids else []
            if not rows:
                continue
            db.execute(
                "INSERT OR IGNORE INTO ch_conclusion_flags(version_id,layer_id,stage_code,reason,detail_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (version_id, layer_id, stage_code, "source_retracted",
                 stable_json({"sources": sorted({r["source_key"] for r in rows}),
                              "evidence": sorted({r["evidence_key"] for r in rows})}), now()),
            )
            count += 1
        return count

    def publish_scheme(self, project_id: int, actor_id: int, scheme_key: str, version_no: int) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_PUBLISH)
        with transaction(immediate=True) as db:
            version = self._version_row(db, project_id, scheme_key, version_no)
            if version["status"] != "proposed":
                raise ServiceError("version_not_proposed", "只有候选版本可以发布，已发布版本不可覆盖", 409)
            approvals = db.execute("SELECT COUNT(*) AS c FROM ch_reviews WHERE version_id=? AND decision='approve'",
                                   (version["id"],)).fetchone()["c"]
            if approvals < version["approvals_required"]:
                raise ServiceError("approvals_insufficient",
                                   f"还需 {version['approvals_required'] - approvals} 个同行批准", 409)
            pending = db.execute("SELECT COUNT(*) AS c FROM ch_conclusion_flags WHERE version_id=? AND status='pending'",
                                 (version["id"],)).fetchone()["c"]
            if pending:
                raise ServiceError("flags_pending", f"存在 {pending} 条待复核标记，处理后才能发布", 409)
            db.execute("UPDATE ch_scheme_versions SET status='published',published_at=?,published_by=? WHERE id=?",
                       (now(), actor_id, version["id"]))
            db.execute("UPDATE ch_schemes SET updated_at=? WHERE id=?", (now(), version["scheme_id"]))
            self.audit("chron.scheme.publish", project_id, actor_id, "ch_scheme_version", str(version["id"]),
                       {"scheme_key": scheme_key, "version_no": version_no})
            return self._version_detail(db, version["id"])

    def review_scheme(self, project_id: int, actor_id: int, scheme_key: str, version_no: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_REVIEW)
        with transaction(immediate=True) as db:
            version = self._version_row(db, project_id, scheme_key, version_no)
            if version["status"] != "proposed":
                raise ServiceError("version_closed", "评审只能针对候选版本", 409)
            if version["created_by"] == actor_id and payload["decision"] == "approve":
                raise ServiceError("self_review", "提议人不能批准自己的方案，请由其他同行评审", 403)
            stamp = now()
            try:
                db.execute(
                    "INSERT INTO ch_reviews(version_id,reviewer_id,decision,comment,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (version["id"], actor_id, payload["decision"], payload.get("comment", ""), stamp, stamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ServiceError("review_exists", "该评审人已提交意见，并发评审请使用不同账号", 409) from exc
            approvals = db.execute("SELECT COUNT(*) AS c FROM ch_reviews WHERE version_id=? AND decision='approve'",
                                   (version["id"],)).fetchone()["c"]
            self.audit("chron.scheme.review", project_id, actor_id, "ch_review", str(version["id"]),
                       {"scheme_key": scheme_key, "version_no": version_no,
                        "decision": payload["decision"], "approvals": approvals})
            return self._version_detail(db, version["id"])

    def withdraw_scheme(self, project_id: int, actor_id: int, scheme_key: str, version_no: int, note: str) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_PROPOSE)
        with transaction(immediate=True) as db:
            version = self._version_row(db, project_id, scheme_key, version_no)
            if version["status"] != "proposed":
                raise ServiceError("version_not_proposed", "只有候选版本可以撤回；已发布版本永久保留", 409)
            db.execute("UPDATE ch_scheme_versions SET status='withdrawn' WHERE id=?", (version["id"],))
            self.audit("chron.scheme.withdraw", project_id, actor_id, "ch_scheme_version", str(version["id"]),
                       {"scheme_key": scheme_key, "version_no": version_no, "note": note})
            return self._version_detail(db, version["id"])

    def revise_scheme(self, project_id: int, actor_id: int, scheme_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_PROPOSE)
        # 快照校验与求解在写事务之外完成（compute 自身开事务，不能嵌套）
        snapshot = self._snapshot(project_id, payload["snapshot_key"])
        self._stage_layer_ids(project_id, payload["stages"])
        result = self.get_computation(project_id, payload["snapshot_key"])
        if result is None:
            result = self.compute_snapshot(project_id, actor_id, payload["snapshot_key"])["result"]
        if not result["satisfiable"]:
            raise ServiceError("snapshot_in_conflict", "新快照存在不相容证据，候选修订不能基于冲突快照", 422)
        with transaction(immediate=True) as db:
            scheme = db.execute("SELECT * FROM ch_schemes WHERE project_id=? AND scheme_key=?",
                                (project_id, scheme_key)).fetchone()
            if scheme is None:
                raise ServiceError("scheme_not_found", "方案不存在", 404)
            tip = db.execute("SELECT * FROM ch_scheme_versions WHERE scheme_id=? ORDER BY version_no DESC LIMIT 1",
                             (scheme["id"],)).fetchone()
            if tip is None:
                raise ServiceError("version_not_found", "方案尚无版本", 404)
            # 基线取最近的非撤回版本（候选迭代或已发布版本）；新版本号始终在顶端递增
            last = tip if tip["status"] != "withdrawn" else db.execute(
                "SELECT * FROM ch_scheme_versions WHERE scheme_id=? AND status!='withdrawn' "
                "ORDER BY version_no DESC LIMIT 1", (scheme["id"],)).fetchone()
            if last is None:
                raise ServiceError("version_withdrawn", "方案所有版本均已撤回，不能继续修订", 409)
            base_kind = "published" if last["status"] == "published" else "proposed"
            old_content = json.loads(last["content_json"])
            old_result = self.get_computation_by_snapshot_id(db, last["snapshot_id"])
            new_content = {"snapshot_key": payload["snapshot_key"], "stages": payload["stages"],
                           "result_hash": result["result_hash"]}
            diff = self._diff(project_id, old_content, old_result, new_content, result)
            diff["base_version_status"] = base_kind
            stamp = now()
            cur = db.execute(
                "INSERT INTO ch_scheme_versions(project_id,scheme_id,version_no,status,snapshot_id,content_json,"
                "content_hash,parent_version_id,diff_json,approvals_required,created_by,created_at) "
                "VALUES(?,?,?,'proposed',?,?,?,?,?,?,?,?)",
                (project_id, scheme["id"], tip["version_no"] + 1, snapshot["id"],
                 stable_json(new_content), request_hash(new_content), last["id"], stable_json(diff),
                 payload.get("approvals_required", 2), actor_id, stamp),
            )
            self._flag_retracted(db, project_id, cur.lastrowid, payload["stages"], snapshot["id"])
            db.execute("UPDATE ch_schemes SET updated_at=? WHERE id=?", (stamp, scheme["id"]))
            self.audit("chron.scheme.revise", project_id, actor_id, "ch_scheme_version", str(cur.lastrowid),
                       {"scheme_key": scheme_key, "base_version_no": last["version_no"],
                        "snapshot_key": payload["snapshot_key"], "diff": diff})
            return self._version_detail(db, cur.lastrowid)

    def _diff(self, project_id: int, old_content: dict[str, Any], old_result: dict[str, Any],
              new_content: dict[str, Any], new_result: dict[str, Any]) -> dict[str, Any]:
        old_stages, new_stages = old_content["stages"], new_content["stages"]
        stage_changes = []
        for key in sorted(set(old_stages) | set(new_stages)):
            if old_stages.get(key) != new_stages.get(key):
                stage_changes.append({"layer_key": key, "old": old_stages.get(key), "new": new_stages.get(key)})

        def bounds_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
            return {L["layer_key"]: {"start_bp": L["start_bp"], "end_bp": L["end_bp"]} for L in result["layers"]}

        old_bounds, new_bounds = bounds_map(old_result), bounds_map(new_result)
        boundary_changes = []
        for key in sorted(set(old_bounds) | set(new_bounds)):
            if old_bounds.get(key) != new_bounds.get(key):
                boundary_changes.append({"layer_key": key, "old": old_bounds.get(key), "new": new_bounds.get(key)})

        old_ev = {e["key"] for e in self._snapshot_evidence_keys(project_id, old_content["snapshot_key"])}
        new_ev = {e["key"] for e in self._snapshot_evidence_keys(project_id, new_content["snapshot_key"])}
        affected_layers = sorted({c["layer_key"] for c in stage_changes} | {c["layer_key"] for c in boundary_changes})
        sites = self.db.execute(
            f"SELECT l.layer_key, s.site_key, s.name AS site_name FROM ch_layers l JOIN ch_sites s ON s.id=l.site_id "
            f"WHERE l.project_id=? AND l.layer_key IN ({','.join('?' for _ in affected_layers)})",
            [project_id, *affected_layers],
        ).fetchall() if affected_layers else []
        return {
            "base_snapshot_key": old_content["snapshot_key"],
            "candidate_snapshot_key": new_content["snapshot_key"],
            "evidence_added": sorted(new_ev - old_ev),
            "evidence_removed": sorted(old_ev - new_ev),
            "stage_changes": stage_changes,
            "boundary_changes": boundary_changes,
            "affected_layers": affected_layers,
            "affected_sites": [{"site_key": r["site_key"], "site_name": r["site_name"],
                                "layer_key": r["layer_key"]} for r in sites],
            "affected_conclusions": affected_layers,
        }

    def _snapshot_evidence_keys(self, project_id: int, snapshot_key: str) -> list[dict[str, str]]:
        snapshot = self._snapshot(project_id, snapshot_key)
        spec = json.loads(snapshot["spec_json"])
        return [{"key": e["key"]} for e in spec["evidence"]]

    def get_computation_by_snapshot_id(self, db: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
        row = db.execute("SELECT result_json FROM ch_computations WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise ServiceError("snapshot_not_computed", "差异摘要要求基线快照已有计算结果", 422)
        return json.loads(row["result_json"])

    def _version_row(self, db: sqlite3.Connection, project_id: int, scheme_key: str, version_no: int) -> sqlite3.Row:
        row = db.execute(
            "SELECT v.* FROM ch_scheme_versions v JOIN ch_schemes s ON s.id=v.scheme_id "
            "WHERE v.project_id=? AND s.scheme_key=? AND v.version_no=?",
            (project_id, scheme_key, version_no),
        ).fetchone()
        if row is None:
            raise ServiceError("version_not_found", f"方案 {scheme_key} 版本 {version_no} 不存在", 404)
        return row

    def _version_detail(self, db: sqlite3.Connection, version_id: int) -> dict[str, Any]:
        version = db.execute("SELECT * FROM ch_scheme_versions WHERE id=?", (version_id,)).fetchone()
        scheme = db.execute("SELECT * FROM ch_schemes WHERE id=?", (version["scheme_id"],)).fetchone()
        reviews = db.execute(
            "SELECT r.*, u.username FROM ch_reviews r LEFT JOIN users u ON u.id=r.reviewer_id "
            "WHERE r.version_id=? ORDER BY r.id", (version_id,),
        ).fetchall()
        flags = db.execute(
            "SELECT f.*, l.layer_key FROM ch_conclusion_flags f JOIN ch_layers l ON l.id=f.layer_id "
            "WHERE f.version_id=? ORDER BY f.id", (version_id,),
        ).fetchall()
        out = dict(version)
        out["scheme_key"] = scheme["scheme_key"]
        out["title"] = scheme["title"]
        out["content"] = json.loads(out.pop("content_json"))
        out["diff"] = json.loads(out["diff_json"]) if out["diff_json"] else None
        out["content_hash"] = out["content_hash"][:12]
        out["reviews"] = [dict(r) for r in reviews]
        out["approvals"] = sum(1 for r in reviews if r["decision"] == "approve")
        out["flags"] = [dict(f) | {"detail": json.loads(f["detail_json"])} for f in flags]
        return out

    def list_schemes(self, project_id: int, actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        schemes = self.db.execute("SELECT * FROM ch_schemes WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        data = []
        for scheme in schemes:
            versions = self.db.execute(
                "SELECT v.id,v.version_no,v.status,v.created_at,v.published_at,v.snapshot_id FROM ch_scheme_versions v "
                "WHERE v.scheme_id=? ORDER BY v.version_no", (scheme["id"],),
            ).fetchall()
            data.append({**dict(scheme), "versions": [dict(v) for v in versions]})
        return {"data": data}

    def get_version(self, project_id: int, actor_id: int, scheme_key: str, version_no: int) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        return self._version_detail(self.db, self._version_row(self.db, project_id, scheme_key, version_no)["id"])

    # ---------- 引用撤回与待复核 ----------

    def retract_source(self, project_id: int, actor_id: int, source_key: str, reason: str) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_RETRACT)
        with transaction(immediate=True) as db:
            source = self._source_row(project_id, source_key)
            if source["status"] == "retracted":
                raise ServiceError("already_retracted", "出处已处于撤回状态", 409)
            db.execute("UPDATE ch_sources SET status='retracted',retracted_at=?,updated_at=?,note=? WHERE id=?",
                       (now(), now(), (source["note"] + f"\n[撤回] {reason}").strip(), source["id"]))
            flagged = []
            versions = db.execute(
                "SELECT * FROM ch_scheme_versions WHERE project_id=? AND status IN ('proposed','published')",
                (project_id,),
            ).fetchall()
            for version in versions:
                stages = json.loads(version["content_json"])["stages"]
                spec = json.loads(db.execute("SELECT spec_json FROM ch_snapshots WHERE id=?",
                                             (version["snapshot_id"],)).fetchone()["spec_json"])
                pinned = [item["id"] for item in spec["evidence"]]
                if not pinned:
                    continue
                # 只标记该版本快照内固定、且层位属于阶段结论的撤回依赖
                placeholders = ",".join("?" for _ in pinned)
                hit_layers = db.execute(
                    f"SELECT DISTINCT layer_id FROM ch_evidence "
                    f"WHERE project_id=? AND source_id=? AND id IN ({placeholders})",
                    [project_id, source["id"], *pinned],
                ).fetchall()
                for row in hit_layers:
                    layer_key = self._layer_key_by_id(project_id, row["layer_id"])
                    if layer_key not in stages:
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO ch_conclusion_flags(version_id,layer_id,stage_code,reason,detail_json,created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (version["id"], row["layer_id"], stages[layer_key], "source_retracted",
                         stable_json({"source_key": source_key, "reason": reason}), now()),
                    )
                    flagged.append({"version_id": version["id"], "layer_key": layer_key})
            self.audit("chron.source.retract", project_id, actor_id, "ch_source", source_key,
                       {"source_key": source_key, "reason": reason, "flagged_conclusions": flagged})
            return {"source_key": source_key, "status": "retracted", "flagged_conclusions": flagged}

    def list_flags(self, project_id: int, actor_id: int, *, status: str | None = None) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        sql = ("SELECT f.*, l.layer_key, s.scheme_key, v.version_no FROM ch_conclusion_flags f "
               "JOIN ch_layers l ON l.id=f.layer_id "
               "JOIN ch_scheme_versions v ON v.id=f.version_id "
               "JOIN ch_schemes s ON s.id=v.scheme_id WHERE v.project_id=?")
        args: list[Any] = [project_id]
        if status:
            sql += " AND f.status=?"
            args.append(status)
        sql += " ORDER BY f.id"
        rows = self.db.execute(sql, args).fetchall()
        return {"data": [dict(r) | {"detail": json.loads(r["detail_json"])} for r in rows]}

    def resolve_flag(self, project_id: int, actor_id: int, flag_id: int, note: str) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_RESOLVE)
        with transaction(immediate=True) as db:
            row = db.execute(
                "SELECT f.* FROM ch_conclusion_flags f JOIN ch_scheme_versions v ON v.id=f.version_id "
                "WHERE f.id=? AND v.project_id=?", (flag_id, project_id),
            ).fetchone()
            if row is None:
                raise ServiceError("flag_not_found", "待复核标记不存在", 404)
            if row["status"] == "resolved":
                raise ServiceError("flag_resolved", "该标记已处理", 409)
            db.execute("UPDATE ch_conclusion_flags SET status='resolved',resolved_at=?,detail_json=? WHERE id=?",
                       (now(), stable_json({**json.loads(row["detail_json"]), "resolution_note": note}), flag_id))
            self.audit("chron.flag.resolve", project_id, actor_id, "ch_conclusion_flag", str(flag_id),
                       {"flag_id": flag_id, "note": note})
            return dict(db.execute("SELECT * FROM ch_conclusion_flags WHERE id=?", (flag_id,)).fetchone())

    # ---------- 查询 ----------

    def list_sites(self, project_id: int, actor_id: int, *, region: str | None = None) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        sql = "SELECT * FROM ch_sites WHERE project_id=?"
        args: list[Any] = [project_id]
        if region:
            sql += " AND region=?"
            args.append(region)
        sql += " ORDER BY region, site_key"
        return {"data": [dict(r) for r in self.db.execute(sql, args).fetchall()]}

    def list_layers(self, project_id: int, actor_id: int, *, site_key: str | None = None) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        sql = ("SELECT l.*, s.site_key FROM ch_layers l JOIN ch_sites s ON s.id=l.site_id "
               "WHERE l.project_id=?")
        args: list[Any] = [project_id]
        if site_key:
            sql += " AND s.site_key=?"
            args.append(site_key)
        sql += " ORDER BY s.site_key, l.sequence_no IS NULL, l.sequence_no, l.id"
        return {"data": [dict(r) for r in self.db.execute(sql, args).fetchall()]}

    def list_sources(self, project_id: int, actor_id: int) -> dict[str, Any]:
        self.require(project_id, actor_id, ROLE_READ)
        rows = self.db.execute("SELECT * FROM ch_sources WHERE project_id=? ORDER BY source_key", (project_id,)).fetchall()
        return {"data": [dict(r) for r in rows]}

    def timeline(self, project_id: int, actor_id: int, *, region: str | None = None,
                 younger_than: int | None = None, older_than: int | None = None) -> dict[str, Any]:
        """每个遗址当前发布版本的阶段-时间对应（未知边界保持 null）。"""
        self.require(project_id, actor_id, ROLE_READ)
        schemes = self.db.execute("SELECT * FROM ch_schemes WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        current: dict[int, sqlite3.Row] = {}
        for scheme in schemes:
            row = self.db.execute(
                "SELECT * FROM ch_scheme_versions WHERE scheme_id=? AND status='published' ORDER BY version_no DESC LIMIT 1",
                (scheme["id"],),
            ).fetchone()
            if row:
                current[scheme["id"]] = row
        items: list[dict[str, Any]] = []
        for scheme_id, version in current.items():
            result = self.get_computation_by_snapshot_id(self.db, version["snapshot_id"])
            bounds = {L["layer_key"]: L for L in result["layers"]}
            stages = json.loads(version["content_json"])["stages"]
            snap = self.db.execute("SELECT snapshot_key FROM ch_snapshots WHERE id=?", (version["snapshot_id"],)).fetchone()
            for layer_key, stage in stages.items():
                layer = self.db.execute("SELECT l.*, s.site_key, s.name AS site_name, s.region FROM ch_layers l "
                                        "JOIN ch_sites s ON s.id=l.site_id WHERE l.project_id=? AND l.layer_key=?",
                                        (project_id, layer_key)).fetchone()
                if region and layer["region"] != region:
                    continue
                L = bounds.get(layer_key)
                span = {"start_bp": (L or {}).get("start_bp"), "end_bp": (L or {}).get("end_bp")}
                if younger_than is not None:
                    # 层位可能比 X 更年轻：其最晚边缘（BP 最小值一侧）开放或 <= X
                    youngest = (span["end_bp"] or {}).get("youngest")
                    if youngest is not None and youngest > younger_than:
                        continue
                if older_than is not None:
                    # 层位可能比 X 更老：其最早边缘（BP 最大值一侧）开放或 >= X
                    oldest = (span["start_bp"] or {}).get("oldest")
                    if oldest is not None and oldest < older_than:
                        continue
                flags = self.db.execute("SELECT COUNT(*) AS c FROM ch_conclusion_flags WHERE version_id=? AND layer_id=? AND status='pending'",
                                        (version["id"], layer["id"])).fetchone()["c"]
                items.append({
                    "site_key": layer["site_key"], "site_name": layer["site_name"], "region": layer["region"],
                    "layer_key": layer_key, "stage": stage,
                    "start_bp": span["start_bp"], "end_bp": span["end_bp"],
                    "scheme_id": scheme_id, "version_no": version["version_no"],
                    "snapshot_key": snap["snapshot_key"], "review_pending": bool(flags),
                })
        items.sort(key=lambda x: (x["region"], x["site_key"], x["layer_key"]))
        return {"data": items}

    # ---------- 离线导入 / 导出 ----------

    def export_project(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)

        def rows(table: str, order: str) -> list[dict[str, Any]]:
            return [dict(r) for r in self.db.execute(
                f"SELECT * FROM {table} WHERE project_id=? ORDER BY {order}", (project_id,)).fetchall()]

        site_rows = rows("ch_sites", "id")
        layer_rows = rows("ch_layers", "id")
        source_rows = rows("ch_sources", "id")
        evidence_rows = rows("ch_evidence", "id")
        snapshot_rows = rows("ch_snapshots", "id")
        comp_rows = [dict(r) for r in self.db.execute(
            "SELECT c.* FROM ch_computations c JOIN ch_snapshots s ON s.id=c.snapshot_id WHERE s.project_id=? ORDER BY c.id",
            (project_id,)).fetchall()]
        schemes = rows("ch_schemes", "id")
        version_rows = [dict(r) for r in self.db.execute(
            "SELECT v.* FROM ch_scheme_versions v WHERE v.project_id=? ORDER BY v.id", (project_id,)).fetchall()]
        review_rows = [dict(r) for r in self.db.execute(
            "SELECT r.*, u.username AS reviewer_username FROM ch_reviews r "
            "LEFT JOIN users u ON u.id=r.reviewer_id "
            "JOIN ch_scheme_versions v ON v.id=r.version_id WHERE v.project_id=? ORDER BY r.id",
            (project_id,)).fetchall()]
        project = dict(self._project(project_id))
        return {
            "format": "regional-chronology-export/v1",
            "exported_at": now(),
            "project": {"code": project["code"], "name": project["name"], "site_name": project["site_name"]},
            "sites": site_rows, "layers": layer_rows, "sources": source_rows, "evidence": evidence_rows,
            "snapshots": snapshot_rows, "computations": comp_rows,
            "schemes": schemes, "scheme_versions": version_rows, "reviews": review_rows,
        }

    def import_project(self, bundle: dict[str, Any], *, actor_id: int | None, project_code: str | None = None) -> dict[str, Any]:
        if bundle.get("format") != "regional-chronology-export/v1":
            raise ServiceError("bad_bundle", "无法识别的导入格式", 400)
        proj = bundle["project"]
        code = (project_code or proj["code"]).upper()
        with transaction(immediate=True) as db:
            existing = db.execute("SELECT * FROM projects WHERE code=?", (code,)).fetchone()
            if existing is None:
                stamp = now()
                cur = db.execute("INSERT INTO projects(code,name,site_name,created_at,updated_at) VALUES(?,?,?,?,?)",
                                 (code, proj["name"], proj["site_name"], stamp, stamp))
                project_id = cur.lastrowid
                if actor_id is not None:
                    db.execute("INSERT INTO project_members(project_id,user_id,role,joined_at) VALUES(?,?, 'owner',?)",
                               (project_id, actor_id, stamp))
            else:
                project_id = existing["id"]
                used = db.execute("SELECT COUNT(*) AS c FROM ch_sites WHERE project_id=?", (project_id,)).fetchone()["c"]
                if used:
                    raise ServiceError("target_not_empty", "目标项目已有年代学数据，请选择空项目导入", 409)

            site_id_map: dict[int, int] = {}
            for r in bundle["sites"]:
                cur = db.execute(
                    "INSERT INTO ch_sites(project_id,site_key,name,region,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (project_id, r["site_key"], r["name"], r["region"], r["latitude"], r["longitude"], r["created_at"], r["updated_at"]),
                )
                site_id_map[r["id"]] = cur.lastrowid
            layer_id_map: dict[int, int] = {}
            for r in bundle["layers"]:
                cur = db.execute(
                    "INSERT INTO ch_layers(project_id,site_id,layer_key,name,sequence_no,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (project_id, site_id_map[r["site_id"]], r["layer_key"], r["name"], r["sequence_no"], r["created_at"], r["updated_at"]),
                )
                layer_id_map[r["id"]] = cur.lastrowid
            source_id_map: dict[int, int] = {}
            for r in bundle["sources"]:
                cur = db.execute(
                    "INSERT INTO ch_sources(project_id,source_key,title,authors,citation,year_pub,status,retracted_at,note,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, r["source_key"], r["title"], r["authors"], r["citation"], r["year_pub"],
                     r["status"], r["retracted_at"], r["note"], r["created_at"], r["updated_at"]),
                )
                source_id_map[r["id"]] = cur.lastrowid
            evidence_id_map: dict[int, int] = {}
            for r in bundle["evidence"]:
                cur = db.execute(
                    "INSERT INTO ch_evidence(project_id,evidence_key,rev,supersedes_id,kind,category,basis,source_id,"
                    "layer_id,other_layer_id,artifact_type,sample_code,method,probability,bound_lo,bound_hi,"
                    "ranges_json,gap_years,label_text,note,status,content_hash,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, r["evidence_key"], r["rev"], None, r["kind"], r["category"], r["basis"],
                     source_id_map.get(r["source_id"]), layer_id_map.get(r["layer_id"]),
                     layer_id_map.get(r["other_layer_id"]), r["artifact_type"], r["sample_code"], r["method"],
                     r["probability"], r["bound_lo"], r["bound_hi"], r["ranges_json"], r["gap_years"],
                     r["label_text"], r["note"], r["status"], r["content_hash"], actor_id, r["created_at"]),
                )
                evidence_id_map[r["id"]] = cur.lastrowid
            for old_id, new_id in evidence_id_map.items():
                old_sup = next(r["supersedes_id"] for r in bundle["evidence"] if r["id"] == old_id)
                if old_sup:
                    db.execute("UPDATE ch_evidence SET supersedes_id=? WHERE id=?", (evidence_id_map[old_sup], new_id))

            snapshot_id_map: dict[int, int] = {}
            for r in bundle["snapshots"]:
                # spec 中固定的证据行 id 必须重映射到新库
                spec = json.loads(r["spec_json"])
                for pin in spec["evidence"]:
                    pin["id"] = evidence_id_map[pin["id"]]
                spec_json = stable_json(spec)
                cur = db.execute(
                    "INSERT INTO ch_snapshots(project_id,snapshot_key,title,algorithm_version,spec_json,evidence_hash,note,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (project_id, r["snapshot_key"], r["title"], r["algorithm_version"], spec_json,
                     r["evidence_hash"], r["note"], actor_id, r["created_at"]),
                )
                snapshot_id_map[r["id"]] = cur.lastrowid
            for r in bundle["computations"]:
                db.execute(
                    "INSERT INTO ch_computations(snapshot_id,result_hash,result_json,computed_by,created_at) VALUES(?,?,?,?,?)",
                    (snapshot_id_map[r["snapshot_id"]], r["result_hash"], r["result_json"], actor_id, r["created_at"]),
                )
            scheme_id_map: dict[int, int] = {}
            for r in bundle["schemes"]:
                cur = db.execute("INSERT INTO ch_schemes(project_id,scheme_key,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                                 (project_id, r["scheme_key"], r["title"], r["created_at"], r["updated_at"]))
                scheme_id_map[r["id"]] = cur.lastrowid
            version_id_map: dict[int, int] = {}
            for r in bundle["scheme_versions"]:
                cur = db.execute(
                    "INSERT INTO ch_scheme_versions(project_id,scheme_id,version_no,status,snapshot_id,content_json,"
                    "content_hash,parent_version_id,diff_json,approvals_required,superseded_by_version_id,created_by,"
                    "created_at,published_at,published_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, scheme_id_map[r["scheme_id"]], r["version_no"], r["status"],
                     snapshot_id_map[r["snapshot_id"]], r["content_json"], r["content_hash"], None, r["diff_json"],
                     r["approvals_required"], None, actor_id, r["created_at"], r["published_at"], actor_id if r["published_by"] else None),
                )
                version_id_map[r["id"]] = cur.lastrowid
            for r in bundle["scheme_versions"]:
                if r["parent_version_id"]:
                    db.execute("UPDATE ch_scheme_versions SET parent_version_id=? WHERE id=?",
                               (version_id_map[r["parent_version_id"]], version_id_map[r["id"]]))
            for r in bundle["reviews"]:
                reviewer = db.execute("SELECT id FROM users WHERE username=?", (r["reviewer_username"],)).fetchone() if r["reviewer_username"] else None
                db.execute(
                    "INSERT INTO ch_reviews(version_id,reviewer_id,decision,comment,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (version_id_map[r["version_id"]], reviewer["id"] if reviewer else None,
                     r["decision"], r["comment"], r["created_at"], r["updated_at"]),
                )
            # 待复核标记不直接导出：依据撤回出处、仅按各版本快照内固定的证据重建
            retracted_source_ids = {source_id_map[r["id"]] for r in bundle["sources"] if r["status"] == "retracted"}
            versions = self.db.execute(
                "SELECT * FROM ch_scheme_versions WHERE project_id=? AND status IN ('proposed','published')",
                (project_id,)).fetchall()
            for version in versions:
                stages = json.loads(version["content_json"])["stages"]
                spec = json.loads(self.db.execute("SELECT spec_json FROM ch_snapshots WHERE id=?",
                                                  (version["snapshot_id"],)).fetchone()["spec_json"])
                pinned = [item["id"] for item in spec["evidence"]]
                if not pinned or not retracted_source_ids:
                    continue
                placeholders = ",".join("?" for _ in pinned)
                src_marks = ",".join("?" for _ in retracted_source_ids)
                hits = self.db.execute(
                    f"SELECT e.layer_id, so.source_key FROM ch_evidence e "
                    f"JOIN ch_sources so ON so.id=e.source_id "
                    f"WHERE e.project_id=? AND e.id IN ({placeholders}) AND e.source_id IN ({src_marks})",
                    [project_id, *pinned, *sorted(retracted_source_ids)],
                ).fetchall()
                for row in hits:
                    lk = self._layer_key_by_id(project_id, row["layer_id"])
                    if lk in stages:
                        self.db.execute(
                            "INSERT OR IGNORE INTO ch_conclusion_flags(version_id,layer_id,stage_code,reason,detail_json,created_at) "
                            "VALUES(?,?,?,?,?,?)",
                            (version["id"], row["layer_id"], stages[lk], "source_retracted",
                             stable_json({"source_key": row["source_key"], "rebuilt_on_import": True}), now()),
                        )
            self.audit("chron.project.import", project_id, actor_id, "project", code,
                       {"code": code, "sites": len(site_id_map), "layers": len(layer_id_map),
                        "evidence": len(evidence_id_map), "snapshots": len(snapshot_id_map),
                        "versions": len(version_id_map)})
            return {"project_id": project_id, "code": code,
                    "sites": len(site_id_map), "layers": len(layer_id_map), "sources": len(source_id_map),
                    "evidence": len(evidence_id_map), "snapshots": len(snapshot_id_map),
                    "schemes": len(scheme_id_map), "versions": len(version_id_map)}
