"""백그라운드 작업 큐 + 별도 작업 프로세스(2026-11-26, P1-6 2단계 — `job_queue.py`)."""

from __future__ import annotations

import time

import pytest

from hair_platform import db, job_queue

CALLS: list[tuple] = []


def _ok(conn, *args):
    CALLS.append(("ok", *args))
    conn.execute("CREATE TABLE IF NOT EXISTS t_job_log (n INTEGER)")
    conn.execute("INSERT INTO t_job_log (n) VALUES (?)", (args[0],))
    conn.commit()


def _boom(conn, *args):
    raise RuntimeError("작업 실패 예시")


def _slow(conn, *args):
    time.sleep(0.3)
    CALLS.append(("slow", *args))


@pytest.fixture
def fake_kinds(monkeypatch):
    CALLS.clear()
    funcs = {"t.ok": _ok, "t.boom": _boom, "t.slow": _slow}
    monkeypatch.setattr(job_queue, "KINDS", {**job_queue.KINDS, **{k: "x:y" for k in funcs}})
    monkeypatch.setattr(job_queue, "_resolve", lambda kind: funcs[kind])
    return funcs


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "hair.db")
    conn = db.get_connection(path)
    db.init_schema(conn)
    job_queue.ensure_schema(conn)
    yield path, conn
    conn.close()


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))

    def run(self):
        for func, args, kwargs in self.tasks:
            func(*args, **kwargs)


def _job(conn, job_id):
    return conn.execute("SELECT * FROM background_jobs WHERE id = ?", (job_id,)).fetchone()


def test_embedded_runs_through_background_tasks_and_records_status(file_db, fake_kinds, monkeypatch):
    monkeypatch.delenv("HAIR_WORKER_MODE", raising=False)
    _path, conn = file_db
    bt = FakeBackgroundTasks()
    ok = job_queue.enqueue(conn, "t.ok", 7, {"note": "한글"}, background_tasks=bt)
    boom = job_queue.enqueue(conn, "t.boom", 1, background_tasks=bt)
    assert _job(conn, ok)["status"] == "queued" and not CALLS  # 응답 뒤에 실행(예전 BackgroundTasks와 같은 순서)
    with pytest.raises(RuntimeError):
        bt.run()  # embedded는 실패를 기록한 뒤 예전처럼 예외를 올린다
    assert CALLS == [("ok", 7, {"note": "한글"})]
    assert _job(conn, ok)["status"] == "done" and _job(conn, ok)["worker"].startswith("server-")
    failed = _job(conn, boom)
    assert failed["status"] == "failed" and "작업 실패 예시" in failed["error"]
    assert job_queue.run_job(conn, ok, "server-x") == "skipped"  # 두 번 실행하지 않는다


def test_unknown_kind_and_non_json_args_are_rejected(file_db, fake_kinds):
    _path, conn = file_db
    with pytest.raises(KeyError):
        job_queue.enqueue(conn, "nope")
    with pytest.raises(TypeError):
        job_queue.enqueue(conn, "t.ok", object())
    assert conn.execute("SELECT COUNT(*) FROM background_jobs").fetchone()[0] == 0


def test_external_mode_only_records_and_worker_process_runs_jobs(file_db, fake_kinds, monkeypatch):
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    path, conn = file_db
    bt = FakeBackgroundTasks()
    a = job_queue.enqueue(conn, "t.ok", 1, background_tasks=bt)
    b = job_queue.enqueue(conn, "t.slow", 2)
    assert bt.tasks == [] and _job(conn, a)["status"] == "queued"
    worker = job_queue.Worker(path, max_concurrent=1, poll_seconds=0.05)
    worker.start()
    try:
        assert [w["worker"] for w in job_queue.live_workers(conn)] == [worker.name]
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            job_queue.Worker(path, name="worker-other-2").start()  # 작업 프로세스는 하나만
        assert worker.tick() == 1  # 동시 실행 상한 1 — 하나만 가져간다
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _job(conn, b)["status"] != "done":
            worker.tick()
            time.sleep(0.05)
    finally:
        worker.stop(wait_seconds=2)
    assert _job(conn, a)["status"] == "done" and _job(conn, b)["status"] == "done"
    assert _job(conn, a)["worker"] == worker.name
    assert conn.execute("SELECT COUNT(*) FROM t_job_log").fetchone()[0] == 1  # 다른 연결의 쓰기가 보인다
    assert job_queue.live_workers(conn) == []  # 정상 종료하면 하트비트를 지운다
    st = job_queue.status(conn)
    assert st["mode"] == "external" and st["counts"] == {"done": 2}


def test_worker_start_cleans_up_what_a_dead_worker_left(file_db, fake_kinds, monkeypatch):
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    path, conn = file_db
    conn.execute("INSERT INTO background_jobs (kind, status, worker) VALUES ('t.ok', 'running', 'worker-old-1')")
    conn.execute("INSERT INTO worker_heartbeats (worker, pid, host, beat_at) VALUES ('worker-old-1', 1, 'h', ?)",
                 (time.time() - 120,))  # 오래전에 소식이 끊긴 작업 프로세스
    tpl = conn.execute("SELECT id FROM pipeline_templates LIMIT 1").fetchone()[0]
    from hair_platform import auth

    actor = auth.create_user(conn, "u1", "pw-123456", auth.Role.ADMIN)
    actor_id = actor["id"] if not isinstance(actor, int) else actor
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '테스트 요청')", (actor_id,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'PM')", (brief,)).lastrowid
    conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) VALUES (?, ?, ?, 'running')",
                 (tpl, task, actor_id))
    # 작업 프로세스가 꺼져 있는 동안 들어온 실행 — 대기 작업이 가리키므로 정리하지 않고 이어서 실행한다
    queued_run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) "
                              "VALUES (?, ?, ?, 'running')", (tpl, task, actor_id)).lastrowid
    conn.execute("INSERT INTO background_jobs (kind, args) VALUES ('pipeline.stage', ?)", (f"[{queued_run}, {actor_id}]",))
    conn.commit()
    worker = job_queue.Worker(path)
    cleaned = worker.start()  # 죽은 작업 프로세스는 시작을 막지 않는다
    worker.stop()
    assert cleaned["jobs"] == 1 and cleaned["pipeline_runs"] == 1
    assert [r[0] for r in conn.execute("SELECT status FROM background_jobs ORDER BY id")] == ["interrupted", "queued"]
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (queued_run,)).fetchone()[0] == "running"
    run = conn.execute("SELECT status, interrupted_at FROM pipeline_runs ORDER BY id LIMIT 1").fetchone()
    assert run["status"] == "failed" and run["interrupted_at"]  # 1단계와 같은 "중단됨 → 이어서 실행"


def test_mark_interrupted_only_touches_its_own_process_kind(file_db):
    _path, conn = file_db
    conn.execute("INSERT INTO background_jobs (kind, status, worker) VALUES ('a', 'running', 'server-1')")
    conn.execute("INSERT INTO background_jobs (kind, status, worker) VALUES ('b', 'running', 'worker-h-2')")
    conn.commit()
    assert job_queue.mark_interrupted(conn, "server-") == 1
    assert [r[0] for r in conn.execute("SELECT status FROM background_jobs ORDER BY id")] == ["interrupted", "running"]


def test_crew_job_int_keys_survive_json_and_protected_ids(file_db, monkeypatch):
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    _path, conn = file_db
    seen = {}
    monkeypatch.setattr(job_queue, "_resolve", lambda kind: lambda c, jba, *rest: seen.update(jba=jba, rest=rest))
    jid = job_queue.enqueue(conn, "deliverable.team_crew", {3: 11, 5: 12}, 9, 2, 1, "sequential", None, None)
    assert job_queue.protected_ids(conn)["agent_execution_jobs"] == {11, 12}
    assert job_queue.run_job(conn, jid, "worker-x") == "done"
    assert seen["jba"] == {3: 11, 5: 12} and seen["jba"].get(3) == 11  # 정수 키로 되돌아온다


def test_warnings_when_external_without_worker(file_db, fake_kinds, monkeypatch):
    path, conn = file_db
    monkeypatch.delenv("HAIR_WORKER_MODE", raising=False)
    assert job_queue.warnings(conn) == []  # embedded — 서버가 직접 실행
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    job_queue.enqueue(conn, "t.ok", 1)
    assert "실행되고 있지 않습니다" in job_queue.warnings(conn)[0] and "대기 1건" in job_queue.warnings(conn)[0]
    worker = job_queue.Worker(path)
    worker.start()
    try:
        assert job_queue.warnings(conn) == []
    finally:
        worker.stop()


def test_embedded_startup_runs_queued_jobs_left_by_previous_server(file_db, fake_kinds, monkeypatch):
    monkeypatch.delenv("HAIR_WORKER_MODE", raising=False)
    _path, conn = file_db
    conn.execute("INSERT INTO background_jobs (kind, args) VALUES ('t.ok', '[42]')")
    conn.execute("INSERT INTO background_jobs (kind, args, status, worker) VALUES ('t.ok', '[43]', 'running', 'server-1')")
    conn.commit()
    assert job_queue.mark_interrupted(conn, "server-") == 1
    assert job_queue.resume_queued(conn) == 1
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and ("ok", 42) not in CALLS:
        time.sleep(0.02)
    assert CALLS == [("ok", 42)]


def test_real_kinds_resolve_and_monkeypatched_targets_are_used(client_and_conn, monkeypatch):
    from hair_platform import conversations

    _client, conn = client_and_conn
    for kind in job_queue.KINDS:
        assert callable(job_queue._resolve(kind)), kind
    got = []
    monkeypatch.setattr(conversations, "run_default_ai_job", lambda c, *a, **k: got.append((a, k)))
    bt = FakeBackgroundTasks()
    jid = job_queue.enqueue(conn, "conversation.default_ai", 5, 6, 7, "요약", background_tasks=bt)
    bt.run()
    assert got == [((5, 6, 7, "요약"), {})]
    assert conn.execute("SELECT status FROM background_jobs WHERE id = ?", (jid,)).fetchone()[0] == "done"


def test_worker_status_api_is_admin_only_and_warns(client_and_conn, monkeypatch):
    client, _conn = client_and_conn
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/admin/worker-status").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    body = client.get("/api/admin/worker-status").json()
    assert body["mode"] == "embedded" and body["warnings"] == []
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    body = client.get("/api/admin/worker-status").json()
    assert body["mode"] == "external" and "hair worker" in body["warnings"][0]
