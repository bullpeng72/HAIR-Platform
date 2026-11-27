"""프로세스 성과 측정(2026-11-27, AI Native 분석 1단계 — 리드타임 분해·한 번에 완료·재작업·자동 처리율·기준 시간·
단계별 흐름/병목·결재 대기 목록). 실행 기록을 시각까지 직접 넣어 숫자를 손으로 검산한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hair_platform import process_metrics


def _t(minutes_ago: float) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _uid(conn, name="admin1"):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _template(conn, name="계약 검토", stages=("초안", "검토", "최종 승인")):
    tid = conn.execute("INSERT INTO pipeline_templates (name, domain) VALUES (?, 'legal')", (name,)).lastrowid
    sids = []
    for i, s in enumerate(stages, start=1):
        sids.append(conn.execute(
            "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, requires_human_approval) "
            "VALUES (?, ?, ?, ?)", (tid, i, s, 1 if i == len(stages) else 0)).lastrowid)
    return tid, sids


def _task(conn):
    uid = _uid(conn)
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (uid,)).lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'r')", (bid,)).lastrowid


def _run(conn, tid, status, start_ago, end_ago=None, seq=1):
    return conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq, "
        "created_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, _task(conn), _uid(conn), status, seq, _t(start_ago), _t(end_ago) if end_ago is not None else None),
    ).lastrowid


def _stage(conn, run_id, stage_id, status, started_ago, ended_ago, attempt=1):
    return conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, started_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (run_id, stage_id, attempt, status, _t(started_ago), _t(ended_ago)),
    ).lastrowid


def _human(conn, stage_row_id, decided_ago=None, approve=True):
    uid = _uid(conn)
    aid = conn.execute(
        "INSERT INTO approvals (requested_by, subject_type, subject_id, status) VALUES (?, 'pipeline_run_stage', ?, ?)",
        (uid, stage_row_id, "pending" if decided_ago is None else ("approved" if approve else "rejected")),
    ).lastrowid
    if decided_ago is not None:
        conn.execute(
            "INSERT INTO audit_log (actor_id, action, subject_type, subject_id, created_at) VALUES (?, ?, 'approval', ?, ?)",
            (uid, "approval_approved" if approve else "approval_rejected", aid, _t(decided_ago)),
        )


@pytest.fixture
def seeded(client_and_conn):
    """계약 검토 파이프라인 실행 4건.
    run1: 한 번에 완료 — 초안 AI 10분, 검토 AI 10분, 최종 AI 10분 + 사람 대기 30분, 리드타임 70분(그 밖 10분).
    run2: 검토에서 한 번 재작업 후 완료 — AI 40분, 사람 대기 10분, 리드타임 60분.
    run3: 실패(검토 단계에서 사람 검토로 넘어감 → 반려).
    run4: 지금 최종 승인 대기 중(20분째)."""
    client, conn = client_and_conn
    tid, (s1, s2, s3) = _template(conn)
    r1 = _run(conn, tid, "completed", 200, 130)
    _stage(conn, r1, s1, "passed", 200, 190)
    _stage(conn, r1, s2, "passed", 190, 180)
    _human(conn, _stage(conn, r1, s3, "approved", 180, 170), decided_ago=140)
    r2 = _run(conn, tid, "completed", 120, 60)
    _stage(conn, r2, s1, "passed", 120, 110)
    _stage(conn, r2, s2, "failed_retrying", 110, 100)
    _stage(conn, r2, s2, "passed", 100, 90, attempt=2)
    _human(conn, _stage(conn, r2, s3, "approved", 90, 80), decided_ago=70)
    r3 = _run(conn, tid, "failed", 300, 250)
    _stage(conn, r3, s1, "passed", 300, 290)
    _human(conn, _stage(conn, r3, s2, "escalated", 290, 280), decided_ago=250, approve=False)
    r4 = _run(conn, tid, "paused_for_approval", 50, seq=3)
    _stage(conn, r4, s1, "passed", 50, 45)
    _stage(conn, r4, s2, "passed", 45, 30)
    _human(conn, _stage(conn, r4, s3, "passed", 30, 20))
    conn.commit()
    return client, conn, tid, (s1, s2, s3), (r1, r2, r3, r4)


def test_template_summary_numbers(seeded):
    _client, conn, tid, _s, _r = seeded
    d = process_metrics.template_detail(conn, tid, 30)
    s = d["summary"]
    assert (s["runs"], s["completed"], s["failed"], s["waiting_for_human"]) == (4, 2, 1, 1)
    assert s["completion_rate"] == pytest.approx(2 / 3)
    assert s["lead_median_seconds"] == pytest.approx(65 * 60, abs=2)  # 70분·60분
    assert s["ai_mean_seconds"] == pytest.approx(35 * 60, abs=2)  # 30분·40분
    assert s["wait_mean_seconds"] == pytest.approx(20 * 60, abs=2)  # 30분·10분
    assert s["other_mean_seconds"] == pytest.approx(10 * 60, abs=2)  # 10분·10분
    assert s["first_pass_rate"] == pytest.approx(0.5)  # run1만
    assert s["rework_per_run"] == pytest.approx(1 / 3)  # 끝난 3건 중 재작업 1번
    # 통과한 단계 결과 10개(run1 3·run2 3·run3 1·run4 3) 중 사람 결재 없이 넘어간 7개(최종 승인 단계 3개는 사람)
    assert s["automation_rate"] == pytest.approx(7 / 10)
    assert s["human_decisions"] == 3


def test_stage_flow_shows_where_time_goes_and_what_is_stuck(seeded):
    _client, conn, tid, (s1, s2, s3), _r = seeded
    flow = {f["stage_id"]: f for f in process_metrics.template_detail(conn, tid, 30)["stages"]}
    assert flow[s2]["attempts"] == 5 and flow[s2]["rework"] == 1 and flow[s2]["escalated"] == 1
    assert flow[s3]["now_waiting"] == 1 and flow[s1]["now_waiting"] == 0
    # 최종 승인 단계: AI 10분×3 + 사람 대기 30·10·(진행 중 20)분 — 시간의 대부분을 쓰는 병목
    assert flow[s3]["bottleneck"] is True and flow[s1]["bottleneck"] is False
    assert flow[s3]["wait_mean_seconds"] == pytest.approx(20 * 60, abs=5)


def test_overview_lists_waiting_runs_longest_first_and_trend(seeded):
    _client, conn, tid, _s, (_r1, _r2, _r3, r4) = seeded
    o = process_metrics.overview(conn, 30)
    t = next(x for x in o["templates"] if x["template_id"] == tid)
    assert t["runs"] == 4
    assert o["waiting"][0]["run_id"] == r4 and o["waiting"][0]["stage_name"] == "최종 승인"
    assert o["waiting"][0]["waiting_seconds"] == pytest.approx(20 * 60, abs=5)
    assert len(o["trend"]) == process_metrics.TREND_WEEKS and sum(w["completed"] for w in o["trend"]) == 2


def test_baseline_gives_covered_hours_and_slower_signal(seeded):
    _client, conn, tid, _s, _r = seeded
    process_metrics.set_baseline(conn, _uid(conn), tid, 30)  # 예전엔 30분이면 끝났다 → 지금(65분)이 더 느림
    s = process_metrics.template_detail(conn, tid, 30)["summary"]
    assert s["baseline_hours_covered"] == pytest.approx(1.0)  # 30분 × 완료 2건
    assert s["lead_vs_baseline"] == pytest.approx(65 / 30, rel=0.01)
    assert any(x["kind"] == "slower_than_baseline" for x in s["signals"])
    row = conn.execute("SELECT after_value FROM audit_log WHERE action = 'pipeline_baseline_set'").fetchone()
    assert row is not None and "30" in row[0]
    with pytest.raises(ValueError):
        process_metrics.set_baseline(conn, _uid(conn), tid, 0)
    process_metrics.set_baseline(conn, _uid(conn), tid, None)
    assert process_metrics.template_detail(conn, tid, 30)["summary"]["baseline_minutes"] is None


def test_signals_need_enough_samples(client_and_conn):
    _client, conn = client_and_conn
    tid, (s1,) = _template(conn, "보고서", ("작성",))
    r = _run(conn, tid, "completed", 30, 20)
    _stage(conn, r, s1, "failed_retrying", 30, 25)
    _stage(conn, r, s1, "passed", 25, 20, attempt=2)
    conn.commit()
    s = process_metrics.template_detail(conn, tid, 30)["summary"]
    assert s["first_pass_rate"] == 0.0 and s["signals"] == []  # 1건으로는 판단하지 않는다
    for i in range(3):
        r = _run(conn, tid, "completed", 60 + i, 50 + i)
        _stage(conn, r, s1, "failed_retrying", 60 + i, 55 + i)
        _stage(conn, r, s1, "passed", 55 + i, 50 + i, attempt=2)
    conn.commit()
    kinds = {x["kind"] for x in process_metrics.template_detail(conn, tid, 30)["summary"]["signals"]}
    assert {"first_pass", "rework"} <= kinds


def test_period_filter_and_empty_db(client_and_conn):
    _client, conn = client_and_conn
    o = process_metrics.overview(conn, 30)
    assert o["total"]["runs"] == 0 and o["total"]["lead_median_seconds"] is None and o["waiting"] == []
    tid, (s1,) = _template(conn, "오래된 일", ("작성",))
    r = _run(conn, tid, "completed", 60 * 24 * 40, 60 * 24 * 40 - 10)  # 40일 전
    _stage(conn, r, s1, "passed", 60 * 24 * 40, 60 * 24 * 40 - 10)
    conn.commit()
    assert process_metrics.template_detail(conn, tid, 30)["summary"]["runs"] == 0
    assert process_metrics.template_detail(conn, tid, 90)["summary"]["runs"] == 1
    assert process_metrics.template_detail(conn, tid, 0)["summary"]["completed"] == 1
    with pytest.raises(ValueError):
        process_metrics.overview(conn, 45)


def test_api_permissions_and_baseline_validation(seeded):
    client, _conn, tid, _s, _r = seeded
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/process-metrics").status_code == 403
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    r = client.get("/api/process-metrics?days=90")
    assert r.status_code == 200 and r.json()["period_days"] == 90
    assert client.get("/api/process-metrics?days=45").status_code == 400
    assert client.get(f"/api/process-metrics/templates/{tid}").json()["summary"]["runs"] == 4
    assert client.get("/api/process-metrics/templates/99999").status_code == 404
    url = f"/api/process-metrics/templates/{tid}/baseline"
    assert client.put(url, json={"minutes": "abc"}).status_code == 400
    assert client.put(url, json={"minutes": -5}).status_code == 400
    assert client.put(url, json={"minutes": 90}).json()["baseline_minutes"] == 90
    assert client.put(url, json={"minutes": None}).json()["baseline_minutes"] is None
