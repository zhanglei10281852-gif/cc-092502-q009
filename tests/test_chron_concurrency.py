"""真实多线程并发：同一评审人对同一候选版本重复批准，恰好一次成功。"""
from __future__ import annotations

import threading

from tests.chron_fixtures import BASE_EVIDENCE, STAGES, compute, make_snapshot, register_evidence, world

from app.chronology_service import ChronologyService
from app.database import close_connection
from app.service import ServiceError


def test_concurrent_review_uniqueness(world):
    c, pid, h = world["client"], world["pid"], world["owner"]["headers"]
    register_evidence(c, pid, h, BASE_EVIDENCE)
    make_snapshot(c, pid, h, "snap-1")
    compute(c, pid, h, "snap-1")
    proposed = c.post(f"/api/projects/{pid}/chron/schemes", headers=h, json={
        "scheme_key": "conc", "title": "并发方案", "snapshot_key": "snap-1",
        "stages": STAGES, "approvals_required": 5, "note": ""}).json()
    version_no = proposed["version_no"]
    reviewer_id = world["reviewer_a"]["id"]

    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def vote():
        # 工作线程使用独立的线程局部连接，模拟真实并发会话
        try:
            barrier.wait()
            ChronologyService().review_scheme(
                pid, reviewer_id, "conc", version_no, {"decision": "approve", "comment": "并发批准"})
            with lock:
                results.append(True)
        except ServiceError as exc:
            assert exc.code == "review_exists", exc.code
            with lock:
                results.append(False)
        finally:
            close_connection()

    threads = [threading.Thread(target=vote) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert results.count(True) == 1
    detail = c.get(f"/api/projects/{pid}/chron/schemes/conc/versions/1", headers=h).json()
    assert detail["approvals"] == 1
    assert len(detail["reviews"]) == 1
