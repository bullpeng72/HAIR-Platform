"""모델별 동시 호출 상한 + 우선순위(2026-11-27, 개선 계획 3-1 — `llm_gate.py`).

실측(Ornith 35B, Ollama는 한 번에 하나씩 처리): 긴 생성 3개가 줄 서 있을 때 대화 응답이 게이트 없이 106.7·119.0초(셋 다 끝날
때까지 기다림), 게이트를 켜면 42.0·38.9초(지금 도는 하나만 기다림)."""

from __future__ import annotations

import threading
import time

import pytest

from hair_platform import db, job_queue, llm_gate, model_options, model_registry


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("HAIR_OLLAMA_MAX_CONCURRENT", raising=False)
    llm_gate.set_limits({})
    yield
    llm_gate.set_limits({})


def _hold(key, started: threading.Event, release: threading.Event, prio=llm_gate.BACKGROUND):
    with llm_gate.priority(prio), llm_gate.slot(*key):
        started.set()
        release.wait(5)


def test_interactive_call_gets_the_next_slot_before_queued_background_calls():
    key = ("ollama", "m")
    order: list[str] = []
    started, release = threading.Event(), threading.Event()
    running = threading.Thread(target=_hold, args=(key, started, release))
    running.start()
    assert started.wait(2)

    def call(name, prio):
        with llm_gate.priority(prio), llm_gate.slot(*key):
            order.append(name)

    waiters = [threading.Thread(target=call, args=("pipeline-1", llm_gate.BACKGROUND)),
               threading.Thread(target=call, args=("pipeline-2", llm_gate.BACKGROUND))]
    for t in waiters:
        t.start()
        time.sleep(0.05)
    chat = threading.Thread(target=call, args=("chat", llm_gate.INTERACTIVE))
    chat.start()  # 가장 늦게 왔지만
    time.sleep(0.1)
    assert llm_gate.snapshot()["ollama/m"] == {"running": 1, "waiting": 3, "limit": 1}
    release.set()
    for t in [running, *waiters, chat]:
        t.join(5)
    assert order == ["chat", "pipeline-1", "pipeline-2"]  # 대화가 먼저, 나머지는 온 순서
    assert llm_gate.snapshot() == {}


def test_cloud_has_no_limit_and_registry_or_env_changes_it(monkeypatch):
    assert llm_gate.limit_for("anthropic", "claude-x") is None
    assert llm_gate.limit_for("ollama", "m") == 1
    monkeypatch.setenv("HAIR_OLLAMA_MAX_CONCURRENT", "4")  # OLLAMA_NUM_PARALLEL을 올린 서버
    assert llm_gate.limit_for("ollama", "m") == 4
    monkeypatch.setenv("HAIR_OLLAMA_MAX_CONCURRENT", "0")  # 0 = 상한 없음(예전 동작)
    assert llm_gate.limit_for("ollama", "m") is None
    llm_gate.set_limits({("ollama", "m"): 2, ("openai", "gpt"): 3})
    assert llm_gate.limit_for("ollama", "m") == 2 and llm_gate.limit_for("openai", "gpt") == 3


def test_nested_call_in_same_flow_does_not_deadlock():
    with llm_gate.slot("ollama", "m"):
        with llm_gate.slot("ollama", "m"):  # 도구 루프 안에서 도구가 같은 모델을 다시 불러도
            assert llm_gate.snapshot()["ollama/m"]["running"] == 1


def test_waiting_call_reports_progress(monkeypatch):
    from hair_platform import progress

    key = ("ollama", "m")
    monkeypatch.setattr(llm_gate, "REPORT_SECONDS", 0.05)
    started, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold, args=(key, started, release))
    holder.start()
    assert started.wait(2)
    seen: list[str] = []

    def waiter():
        token = progress._REPORTER.set(seen.append)
        try:
            with llm_gate.slot(*key):
                pass
        finally:
            progress._REPORTER.reset(token)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.2)
    release.set()
    t.join(5)
    holder.join(5)
    assert seen and "모델 대기" in seen[0] and "앞에 1건" in seen[0]


def test_kind_priorities():
    assert llm_gate.kind_priority("conversation.agent_mention") == llm_gate.INTERACTIVE
    assert llm_gate.kind_priority("conversation.default_ai") == llm_gate.INTERACTIVE
    assert llm_gate.kind_priority("conversation.team_mention") == llm_gate.BACKGROUND  # Crew — 오래 걸림
    assert llm_gate.kind_priority("pipeline.stage") == llm_gate.BACKGROUND
    assert llm_gate.kind_priority("knowledge.register_submission") == llm_gate.BACKGROUND
    assert llm_gate.kind_priority("deliverable.agent_execution") == llm_gate.NORMAL


def test_registry_limit_reaches_gate_and_api_validates(client_and_conn):
    client, conn = client_and_conn
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    mid = client.post("/api/model-registry", json={"provider": "ollama", "name": "gate-model"}).json()["id"]
    assert client.patch(f"/api/model-registry/{mid}/max-concurrent", json={"max_concurrent": 3}).status_code == 200
    assert llm_gate.limit_for("ollama", "gate-model") == 3
    row = next(m for m in client.get("/api/model-registry").json() if m["id"] == mid)
    assert row["max_concurrent"] == 3
    assert client.patch(f"/api/model-registry/{mid}/max-concurrent", json={"max_concurrent": 0}).status_code == 400
    assert client.patch(f"/api/model-registry/{mid}/max-concurrent", json={"max_concurrent": None}).status_code == 200
    assert llm_gate.limit_for("ollama", "gate-model") == 1  # 기본값으로
    model_registry.set_max_concurrent(conn, 1, mid, 2)
    model_options.refresh(conn)
    assert llm_gate.limit_for("ollama", "gate-model") == 2


def test_litellm_completion_is_gated(monkeypatch):
    import litellm

    calls: list[dict] = []

    def fake_completion(*args, **kwargs):
        calls.append(llm_gate.snapshot())
        return "ok"

    monkeypatch.setattr(litellm, "completion", fake_completion)
    llm_gate.install_litellm_gate()
    llm_gate.install_litellm_gate()  # 두 번 불러도 한 번만 감싼다
    assert litellm.completion(model="ollama_chat/m", messages=[]) == "ok"
    assert calls[0]["ollama/m"]["running"] == 1  # 호출 중 자리를 잡고
    assert llm_gate.snapshot() == {}  # 끝나면 돌려준다
    assert litellm.completion(model="anthropic/claude-x", messages=[]) == "ok"
    assert calls[1] == {}  # 클라우드는 상한 없음


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "hair.db")
    conn = db.get_connection(path)
    db.init_schema(conn)
    job_queue.ensure_schema(conn)
    yield path, conn
    conn.close()


def test_job_queue_records_priority_and_worker_takes_chat_first_keeping_a_slot(file_db, monkeypatch):
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    path, conn = file_db
    ran: list[tuple[str, int]] = []
    gate = threading.Event()

    def slow(conn_, name):
        ran.append((name, llm_gate._PRIORITY.get()))
        gate.wait(5)

    kinds = {"pipeline.stage": slow, "conversation.agent_mention": slow}
    monkeypatch.setattr(job_queue, "_resolve", lambda kind: kinds[kind])
    p1 = job_queue.enqueue(conn, "pipeline.stage", "p1")
    p2 = job_queue.enqueue(conn, "pipeline.stage", "p2")
    chat = job_queue.enqueue(conn, "conversation.agent_mention", "chat")
    prio = dict(conn.execute("SELECT id, priority FROM background_jobs").fetchall())
    assert prio == {p1: 2, p2: 2, chat: 0}
    worker = job_queue.Worker(path, max_concurrent=2, poll_seconds=0.05)
    worker.start()
    try:
        assert worker.tick() == 2  # 대화 먼저, 그리고 파이프라인 하나(나머지 한 자리는 대화용으로 남김)
        deadline = time.monotonic() + 2
        while len(ran) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ("chat", llm_gate.INTERACTIVE) in ran and ("p1", llm_gate.BACKGROUND) in ran
        assert worker.tick() == 0  # 자리가 없다
    finally:
        gate.set()
        worker.stop(wait_seconds=2)
    assert job_queue.status(conn)["models"] == {}


def test_worker_reserves_a_slot_for_chat_when_only_pipelines_wait(file_db, monkeypatch):
    monkeypatch.setenv("HAIR_WORKER_MODE", "external")
    path, conn = file_db
    gate = threading.Event()
    monkeypatch.setattr(job_queue, "_resolve", lambda kind: lambda conn_, *a: gate.wait(5))
    for i in range(3):
        job_queue.enqueue(conn, "pipeline.stage", i)
    worker = job_queue.Worker(path, max_concurrent=3, poll_seconds=0.05)
    worker.start()
    try:
        assert worker.tick() == 2  # 파이프라인은 3자리 중 2자리까지만
        job_queue.enqueue(conn, "conversation.default_ai", 9)
        assert worker.tick() == 1  # 남겨 둔 자리로 대화가 바로 들어간다
    finally:
        gate.set()
        worker.stop(wait_seconds=2)
