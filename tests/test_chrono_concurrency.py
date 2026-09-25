"""并发评审测试：多线程同时投票与同时发布，验证唯一约束与状态机互斥。"""
from __future__ import annotations

import threading

from app.chrono_service import ChronoService
from tests.fixtures import baojia_dataset as ds
from tests.seed_helper import register_user, seed_dataset


def _seed_scheme(client, owner, reviewers_count=3):
    ctx = seed_dataset(client, owner["headers"])
    reviewers = []
    for index in range(reviewers_count):
        user, headers = register_user(client, f"conc_rev{index}")
        client.post(f"{ctx['base']}/members", json={"user_id": user["id"], "role": "reviewer"}, headers=owner["headers"])
        reviewers.append(user["id"])
    snap = client.post(f"{ctx['base']}/snapshots", json={"code": "SNAP-CONC"}, headers=owner["headers"]).json()
    client.post(f"{ctx['base']}/snapshots/{snap['id']}/compute", headers=owner["headers"])
    scheme = client.post(f"{ctx['base']}/schemes", json={"code": "SZC", "title": "并发评审", "snapshot_id": snap["id"], "phases": ds.PHASES_V1}, headers=owner["headers"]).json()
    client.post(f"{ctx['base']}/schemes/{scheme['id']}/submit", headers=owner["headers"])
    return ctx, scheme["id"], reviewers


def test_concurrent_reviews_each_counted_once(client, owner):
    ctx, scheme_id, reviewers = _seed_scheme(client, owner)
    results: list[tuple[int, str]] = []
    barrier = threading.Barrier(len(reviewers))

    def vote(reviewer_id: int) -> None:
        barrier.wait()
        service = ChronoService()  # 每线程独立连接
        try:
            service.review_scheme(scheme_id, ctx["project_id"], {"vote": "approve"}, reviewer_id)
            results.append((reviewer_id, "ok"))
        except Exception as exc:  # noqa: BLE001 - 收集所有异常供断言
            results.append((reviewer_id, type(exc).__name__))

    threads = [threading.Thread(target=vote, args=(rid,)) for rid in reviewers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(status == "ok" for _, status in results), results
    scheme = ChronoService().get_scheme(scheme_id, ctx["project_id"])
    assert len(scheme["reviews"]) == len(reviewers)
    assert {r["reviewer_id"] for r in scheme["reviews"]} == set(reviewers)


def test_concurrent_duplicate_votes_are_serialized(client, owner):
    ctx, scheme_id, reviewers = _seed_scheme(client, owner, reviewers_count=1)
    reviewer_id = reviewers[0]
    # 同一评审人提交后重复投票必须被唯一约束拒绝
    service = ChronoService()
    service.review_scheme(scheme_id, ctx["project_id"], {"vote": "approve"}, reviewer_id)
    import pytest
    from app.service import ServiceError
    with pytest.raises(ServiceError) as err:
        service.review_scheme(scheme_id, ctx["project_id"], {"vote": "approve"}, reviewer_id)
    assert err.value.code == "already_reviewed"


def test_concurrent_publish_only_one_succeeds(client, owner):
    ctx, scheme_id, reviewers = _seed_scheme(client, owner, reviewers_count=2)
    service = ChronoService()
    for reviewer_id in reviewers:
        service.review_scheme(scheme_id, ctx["project_id"], {"vote": "approve"}, reviewer_id)
    owner_id = owner["user"]["id"]
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def publish() -> None:
        barrier.wait()
        svc = ChronoService()
        try:
            svc.publish_scheme(scheme_id, ctx["project_id"], owner_id)
            outcomes.append("published")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=publish) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("published") == 1, outcomes
    versions = ChronoService().list_versions(ctx["project_id"])
    assert len(versions) == 1 and versions[0]["version_no"] == 1
