"""Phase 20 — 사용자 결정(2026-09-29) 8건 골든셋.

외부 사례 리서치(갭 1·갭 2)와 오픈
이슈 목록을 종합 정리해 사용자에게 물은 뒤, 한 번에 결정을 받은 8건이다:
(1) 갭 1 — MCP 도구 등록 admin 전용, (2) 갭 2 — Human Pool 비용/성과
admin 전용, (3) 매칭 점수 breakdown 본인 공개, (4) 보관기간 1년,
(5) 팀원 셀프서비스 배정 요청, (6) CrewAI 위임 캡처 방어 로직,
(7) skill_pool.augmentation_mode='always' 실제 구현, (8) VectorDB
Ollama 임베딩 지원.

갭 1·갭 2의 RBAC 축소는 기존 골든셋(test_phase14_pool_registry.py,
test_phase7_review_fixes.py, test_v4_pipeline.py)을 새 동작에 맞게
직접 갱신했다 — 이 파일은 그 나머지(3~8)와
갭 1·갭 2의 "성공 경로"만 추가로 검증한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from hair_platform import crew_runtime, pool_registry, retention, vectorstore


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


# ---------- (5) 팀원 셀프서비스 배정 요청 ----------


def test_gc200_member_can_request_assignment_for_own_human_pool_record(
    client_and_conn,
):
    """member1의 human_pool 레코드로 본인이 직접 배정을 요청하면 성공해야
    한다 — 다른 사람 자원 요청은 여전히 403인 테스트와 짝이 되는 성공 경로."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    resp = client.post(
        "/api/human-pool",
        json={"name": "본인", "role": "백엔드 개발자", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member_id, human_id)
    )
    # "타인" 레코드도 매니저 권한이 있을 때 미리 만들어둔다(human-pool
    # 생성 자체는 member 권한 밖이라 이후엔 못 만든다).
    resp = client.post(
        "/api/human-pool",
        json={"name": "타인", "role": "백엔드 개발자", "skill_tags": []},
    )
    other_human_id = resp.json()["id"]
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    assert resp.status_code == 201
    # 로드맵 7번(2026-11-05, "셀프배정 결재 단순화") — 본인 셀프배정은
    # 결재 체인 없이 즉시 confirmed된다(명세 변경 — 회귀 아님). 결재
    # 단계의 전체 상세 검증은 test_phase64_self_assignment.py에 있다.
    assert resp.json()["status"] == "confirmed"

    # 다른 사람 대신 요청하는 건 여전히 막힌다.
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": other_human_id},
    )
    assert resp.status_code == 403

    # Agent를 요청하는 것도 막힌다(self-service는 human 본인만).
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('백엔드 개발자', '[]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "agent", "resource_id": agent_id},
    )
    assert resp.status_code == 403


# ---------- (3) 매칭 점수 breakdown 본인 공개 ----------


def test_gc201_member_sees_only_own_breakdown_in_match_results(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={
            "tasks": [
                {
                    "role_name": "백엔드 개발자",
                    "required_skills": ["python"],
                }
            ]
        },
    )
    task_id = resp.json()["tasks"][0]["id"]

    resp = client.post(
        "/api/human-pool",
        json={"name": "본인", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    my_human_id = resp.json()["id"]
    resp = client.post(
        "/api/human-pool",
        json={"name": "타인", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    other_human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member_id, my_human_id)
    )
    conn.commit()

    # admin/manager는 이전처럼 전체 후보를 다 본다.
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200
    ids = {t["candidate"]["id"] for t in resp.json()["top"]}
    assert {my_human_id, other_human_id} <= ids

    # member는 자기 자신에 해당하는 항목만 본다.
    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200
    top = resp.json()["top"]
    assert [t["candidate"]["id"] for t in top] == [my_human_id]

    # viewer는 여전히 이 엔드포인트에 접근할 수 없다(RBAC 매트릭스 원칙 유지).
    _login(client, "viewer1", "viewerpass")
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 403


# ---------- (4) 보관기간 1년 ----------


def test_gc202_purge_expired_deletes_only_records_older_than_retention_days(
    client_and_conn,
):
    """`assignment_history`는 평범히 지워지고, `audit_log`는 append-only
    트리거를 잠깐 내렸다가 반드시 다시 세운 채로 지워져야 한다 — 지운 뒤
    다시 일반 INSERT/DELETE로 append-only가 여전히 걸려 있는지까지
    확인한다."""
    client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    old = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        "INSERT INTO audit_log (actor_id, action, subject_type, created_at) "
        "VALUES (?, 'old_action', 'system', ?)",
        (admin_id, old),
    )
    conn.execute(
        "INSERT INTO audit_log (actor_id, action, subject_type, created_at) "
        "VALUES (?, 'recent_action', 'system', datetime('now'))",
        (admin_id,),
    )

    # assignment_history는 실제 assignment를 거쳐서 만든다(API로).
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    resp = client.post(
        "/api/human-pool", json={"name": "후보", "role": "백엔드 개발자"}
    )
    resource_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assignment_id = resp.json()["assignment_id"]
    # 방금 생긴 유일한 assignment_history 행을 1년보다 오래된 것으로 되돌린다.
    conn.execute(
        "UPDATE assignment_history SET changed_at = ? WHERE assignment_id = ?",
        (old, assignment_id),
    )
    conn.commit()

    result = retention.purge_expired(conn)
    assert result == {"assignment_history_deleted": 1, "audit_log_deleted": 1}

    remaining_actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "old_action" not in remaining_actions
    assert "recent_action" in remaining_actions
    assert "retention_purge_executed" in remaining_actions

    remaining_history = conn.execute(
        "SELECT to_status FROM assignment_history"
    ).fetchall()
    assert remaining_history == []

    # append-only 트리거가 다시 걸려 있어야 한다.
    import sqlite3

    try:
        conn.execute("DELETE FROM audit_log WHERE action = 'recent_action'")
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised


def test_gc203_purge_expired_is_noop_when_nothing_is_old(client_and_conn):
    """지울 게 없으면 감사로그에 `retention_purge_executed` 자체가 남지
    않아야 한다(불필요한 잡음 방지) — `create_app()` 기동마다 매번 호출
    돼도 신선한 DB에서는 아무 흔적을 안 남긴다는 뜻이다."""
    _client, conn = client_and_conn
    result = retention.purge_expired(conn)
    assert result == {"assignment_history_deleted": 0, "audit_log_deleted": 0}
    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "retention_purge_executed" not in actions


# ---------- (6) CrewAI 위임 캡처 방어 로직 ----------


def test_gc204_delegation_capture_tolerates_renamed_tool_strings():
    """CrewAI가 정확한 문자열("Delegate work to coworker" 등)을 바꿔도
    "delegate"/"coworker" 계열 키워드가 들어간 도구 이름이면 위임으로
    잡아야 한다(관련 리스크에 대한 방어 로직)."""
    assert crew_runtime._looks_like_delegation("Delegate work to coworker")
    assert crew_runtime._looks_like_delegation("Ask question to coworker")
    # 버전업으로 표기가 바뀐 상황을 가정한 변형들
    assert crew_runtime._looks_like_delegation("Delegate work to co-worker")
    assert crew_runtime._looks_like_delegation("delegate task to teammate")
    assert crew_runtime._looks_like_delegation("Ask Question To Coworker")
    # 위임과 무관한 도구는 여전히 아니라고 판단해야 한다(오탐 방지 확인).
    assert not crew_runtime._looks_like_delegation("knowledge_base_search")
    assert not crew_runtime._looks_like_delegation("org_relationship_query")


# ---------- (7) skill_pool.augmentation_mode='always' 실제 구현 ----------


def test_gc205_always_mode_forces_static_injection_in_run_crew(client_and_conn):
    """`augmentation_mode='always'`로 등록한 스킬은 `run_crew()`에서
    Agent 판단 없이 Task description에 강제로 검색 결과가 끼워 들어가야
    한다 — 기존(`'tool'` 모드는 description 원문 유지)과 대비되는
    새 동작이다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    vectordb_tool_id = conn.execute(
        "SELECT id FROM tool_pool WHERE name = 'VectorDB 지식검색'"
    ).fetchone()["id"]
    pool_registry.create_skill(
        conn,
        admin_id,
        "긴급검색",
        "긴급 상황엔 판단 없이 항상 과거 산출물을 검색해 참고한다",
        default_tool_pool_ids=[vectordb_tool_id],
        augmentation_mode="always",
    )

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('긴급대응 Agent', '[\"긴급검색\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("완료", ["완료"])

    with (
        patch(
            "hair_platform.vectorstore.search",
            return_value=[{"id": 1, "text": "과거 장애 대응 기록", "score": 0.9}],
        ),
        patch("crewai.Crew.kickoff", new=_kickoff_capture),
    ):
        crew_runtime.run_crew(
            conn,
            [agent_id],
            ["지금 장애 대응 방안을 정리해줘"],
            admin_id,
            provider="openai",
        )

    description = captured["crew"].tasks[0].description
    assert "과거 장애 대응 기록" in description
    assert "지금 장애 대응 방안을 정리해줘" in description
    # 'always'는 강제 주입이 목적이라 Tool도 같이 붙일 필요가 없다 — 이미
    # 컨텍스트를 손에 쥐여줬는데 또 호출하게 하면 중복이다.
    assert captured["crew"].agents[0].tools == []


def test_gc206_tool_mode_skill_still_unaugmented_in_run_crew(client_and_conn):
    """`'always'` 구현이 기존 `'tool'` 모드 스킬(기본 시드된 rag 등)의
    동작을 바꾸지 않는지 확인한다 — 회귀 방지 버전. (원래 "vectordb"
    별칭으로도 됐으나 자동완성 중복 정리로 2026-10-06에 "지식검색"로 통합됨.)"""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('지식검색 Agent', '[\"지식검색\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("완료", ["완료"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent_id], ["결제 모듈 설계 사례 있어?"], admin_id, provider="openai"
        )

    assert captured["crew"].tasks[0].description == "결제 모듈 설계 사례 있어?"
    tool_names = [t.name for t in captured["crew"].agents[0].tools]
    assert "knowledge_base_search" in tool_names


def test_gc207_always_mode_also_augments_single_shot_execute_agent(client_and_conn):
    """단일 실행 경로(`execute_agent()`)는 도구 호출 루프가 없어 `'tool'`
    이든 `'always'`든 정적 주입이 유일한 선택지다 — 새로 만든 `'always'`
    스킬도(기본 시드된 rag/graphdb/실시간웹검색 태그가 전혀 아니어도)
    실제로 주입되는지 확인해, 하드코딩된 태그 집합이 아니라 skill_pool
    데이터로 판별한다는 것을 증명한다."""
    from hair_platform import agent_runtime

    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    vectordb_tool_id = conn.execute(
        "SELECT id FROM tool_pool WHERE name = 'VectorDB 지식검색'"
    ).fetchone()["id"]
    pool_registry.create_skill(
        conn,
        admin_id,
        "커스텀검색",
        "테스트 전용 커스텀 스킬",
        default_tool_pool_ids=[vectordb_tool_id],
        augmentation_mode="always",
    )
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('커스텀 Agent', '[\"커스텀검색\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    with (
        patch(
            "hair_platform.vectorstore.search",
            return_value=[{"id": 1, "text": "커스텀 근거", "score": 0.9}],
        ),
        patch("hair_platform.llm.call_llm", return_value="답변") as llm_mock,
    ):
        output = agent_runtime.execute_agent(
            conn, agent_id, admin_id, "질문", provider="openai"
        )

    assert output == "답변"
    augmented_prompt = llm_mock.call_args[0][3]
    assert "커스텀 근거" in augmented_prompt


# ---------- (8) VectorDB Ollama 임베딩 지원 ----------


def test_gc_embed_text_defaults_to_ollama_without_openai_api_key(monkeypatch):
    """실측으로 발견한 버그(2026-10-26, "외부 자료 조사가" Agent가
    대화창 `@멘션`에서 "Error code: 401 - ... Missing credentials"로
    크래시) — `embed_text()`가 provider 생략 시 무조건 "openai"로
    하드코딩돼 있어, Agent 자신의 LLM provider가 ollama라도 OPENAI_API_KEY
    가 없으면 즉시 실패했다. 이제 키가 없으면 로컬 Ollama로 자동
    전환해야 한다(이 파일의 "로컬/온프레미스 우선 원칙")."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"embedding": [0.1, 0.2]}
    fake_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=fake_resp) as post_mock:
        provider, model, _embedding = vectorstore.embed_text("텍스트")

    assert provider == "ollama"
    assert model == "mxbai-embed-large:latest"
    post_mock.assert_called_once()


def test_gc_embed_text_defaults_to_openai_when_api_key_present(monkeypatch):
    """반대 사례 — OPENAI_API_KEY가 있으면 기존처럼 openai를 기본값으로
    쓴다(하위 호환, 기존 "openai가 기본값" 가정을 쓰던 호출부가 안
    깨진다)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    item = MagicMock()
    item.embedding = [0.1, 0.2]
    resp = MagicMock()
    resp.data = [item]
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = resp

    with patch("openai.OpenAI", return_value=fake_client):
        provider, model, _embedding = vectorstore.embed_text("텍스트")

    assert provider == "openai"
    assert model == "text-embedding-3-small"


def test_gc208_embed_text_supports_ollama_via_local_http_api():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
    fake_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=fake_resp) as post_mock:
        provider, model, embedding = vectorstore.embed_text(
            "테스트 텍스트", provider="ollama"
        )

    assert provider == "ollama"
    assert model == "mxbai-embed-large:latest"
    assert embedding == [0.1, 0.2, 0.3]
    url = post_mock.call_args[0][0]
    assert url == "http://localhost:11434/api/embeddings"
    assert post_mock.call_args.kwargs["json"] == {
        "model": "mxbai-embed-large:latest",
        "prompt": "테스트 텍스트",
    }


def test_gc209_embed_text_ollama_respects_base_url_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom-host:11434")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"embedding": [0.4]}
    fake_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=fake_resp) as post_mock:
        vectorstore.embed_text("텍스트", provider="ollama", model="mxbai-embed-large")

    assert post_mock.call_args[0][0] == "http://custom-host:11434/api/embeddings"


# ---------- 임베딩 모델 설정(사용자 요청, 2026-10-26, "embedding 모델
# 설정 기능을 구현해줘 (하드코딩 하지않도록)") ----------


def test_gc_embedding_config_defaults_to_none_until_explicitly_set():
    conn = __import__("sqlite3").connect(":memory:")
    conn.row_factory = __import__("sqlite3").Row
    from hair_platform import db

    db.init_schema(conn)
    assert vectorstore.get_embedding_config(conn) is None


def test_gc_set_embedding_config_persists_and_overrides_autodetect(monkeypatch):
    conn = __import__("sqlite3").connect(":memory:")
    conn.row_factory = __import__("sqlite3").Row
    from hair_platform import db

    db.init_schema(conn)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 설정 전 — 자동판별(ollama) 결과를 쓴다.
    provider, model = vectorstore.resolve_default_provider_and_model(conn)
    assert provider == "ollama"

    vectorstore.set_embedding_config(conn, 1, "openai", "text-embedding-3-large")
    config = vectorstore.get_embedding_config(conn)
    assert config == {"provider": "openai", "model": "text-embedding-3-large"}

    # 설정 후 — OPENAI_API_KEY가 없어도(자동판별이면 ollama였을 상황)
    # 명시적 설정이 우선한다.
    provider, model = vectorstore.resolve_default_provider_and_model(conn)
    assert (provider, model) == ("openai", "text-embedding-3-large")

    # 재설정(ON CONFLICT UPDATE)도 덮어써야 한다.
    vectorstore.set_embedding_config(conn, 1, "ollama", "mxbai-embed-large:latest")
    assert vectorstore.get_embedding_config(conn) == {
        "provider": "ollama",
        "model": "mxbai-embed-large:latest",
    }


def test_gc_set_embedding_config_rejects_bad_input():
    conn = __import__("sqlite3").connect(":memory:")
    conn.row_factory = __import__("sqlite3").Row
    from hair_platform import db

    db.init_schema(conn)
    with pytest.raises(ValueError):
        vectorstore.set_embedding_config(conn, 1, "anthropic", "claude-something")
    with pytest.raises(ValueError):
        vectorstore.set_embedding_config(conn, 1, "openai", "  ")


def test_gc_fetch_available_embedding_models_ollama_returns_sorted_names():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "models": [{"name": "nomic-embed-text"}, {"name": "mxbai-embed-large:latest"}]
    }
    fake_resp.raise_for_status.return_value = None
    with patch("httpx.get", return_value=fake_resp) as get_mock:
        models = vectorstore.fetch_available_embedding_models("ollama")
    assert models == ["mxbai-embed-large:latest", "nomic-embed-text"]
    assert get_mock.call_args[0][0] == "http://localhost:11434/api/tags"


def test_gc_fetch_available_embedding_models_openai_keeps_only_embedding_models():
    """사용자 요청(2026-11-10, "ollama, openai 선택시 가용한 모델이 자동
    표시되도록") — `model_registry.fetch_available_models()`는 정반대로
    "embedding" 포함 모델을 **제외**하는데(채팅 모델 등록 화면이라),
    여기는 임베딩 설정 화면이라 "embedding" 포함 모델만 **포함**해야
    한다 — 두 함수가 서로 거울상 필터라는 핵심 동작을 직접 검증."""
    fake_client = MagicMock()
    fake_client.models.list.return_value = [
        MagicMock(id="gpt-4o-mini"),
        MagicMock(id="text-embedding-3-small"),
        MagicMock(id="text-embedding-3-large"),
        MagicMock(id="whisper-1"),
    ]
    with patch("openai.OpenAI", return_value=fake_client):
        models = vectorstore.fetch_available_embedding_models("openai")
    assert models == ["text-embedding-3-large", "text-embedding-3-small"]


def test_gc_fetch_available_embedding_models_rejects_unsupported_provider():
    with pytest.raises(ValueError):
        vectorstore.fetch_available_embedding_models("anthropic")


def test_gc_fetch_embedding_config_candidates_endpoint_does_not_persist(
    client_and_conn,
):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    with patch(
        "hair_platform.vectorstore.fetch_available_embedding_models",
        return_value=["text-embedding-3-small", "text-embedding-3-large"],
    ) as m:
        resp = client.get(
            "/api/embedding-config/fetch", params={"provider": "openai"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "provider": "openai",
        "models": ["text-embedding-3-small", "text-embedding-3-large"],
    }
    m.assert_called_once_with("openai")
    # 조회만 했을 뿐 embedding_config는 그대로(미설정) — 자동완성일 뿐
    # 저장을 트리거하지 않는다.
    assert client.get("/api/embedding-config").json()["is_explicit"] is False


def test_gc_fetch_embedding_config_candidates_wraps_provider_errors_as_400(
    client_and_conn,
):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    with patch(
        "hair_platform.vectorstore.fetch_available_embedding_models",
        side_effect=RuntimeError("connection refused"),
    ):
        resp = client.get(
            "/api/embedding-config/fetch", params={"provider": "ollama"}
        )
    assert resp.status_code == 400
    assert "connection refused" in resp.json()["detail"]

    resp = client.get(
        "/api/embedding-config/fetch", params={"provider": "anthropic"}
    )
    assert resp.status_code == 400


def test_gc_fetch_embedding_config_candidates_rbac_blocks_member_and_viewer(
    client_and_conn,
):
    client, _conn = client_and_conn
    for username, password in (("member1", "memberpass"), ("viewer1", "viewerpass")):
        _login(client, username, password)
        resp = client.get(
            "/api/embedding-config/fetch", params={"provider": "ollama"}
        )
        assert resp.status_code == 403, (username, resp.text)


def test_gc_search_and_index_text_use_configured_embedding_without_explicit_args():
    """`provider`/`model`을 안 넘기면 `embedding_config`를 따라야 한다 —
    색인할 때와 검색할 때 서로 다른 기본값을 쓰면 매칭되는 청크가 하나도
    없어 보이므로, 이 둘이 같은 해석 경로를 타는지가 핵심이다."""
    conn = __import__("sqlite3").connect(":memory:")
    conn.row_factory = __import__("sqlite3").Row
    from hair_platform import db

    db.init_schema(conn)
    vectorstore.set_embedding_config(conn, 1, "ollama", "mxbai-embed-large:latest")

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"embedding": [1.0, 0.0]}
    fake_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=fake_resp) as post_mock:
        vectorstore.index_text(conn, None, "과거 산출물 텍스트")
        hits = vectorstore.search(conn, "질의")

    assert len(hits) == 1
    assert hits[0]["text"] == "과거 산출물 텍스트"
    for call in post_mock.call_args_list:
        assert call.kwargs["json"]["model"] == "mxbai-embed-large:latest"


def test_gc_embedding_config_endpoints_rbac_and_roundtrip(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    assert client.get("/api/embedding-config").status_code == 403
    assert (
        client.put(
            "/api/embedding-config", json={"provider": "ollama", "model": "x"}
        ).status_code
        == 403
    )

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/embedding-config")
    assert resp.status_code == 200
    assert resp.json()["is_explicit"] is False  # 아직 설정 안 함 — 자동판별

    resp = client.put(
        "/api/embedding-config",
        json={"provider": "ollama", "model": "mxbai-embed-large:latest"},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/embedding-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "provider": "ollama",
        "model": "mxbai-embed-large:latest",
        "is_explicit": True,
    }

    resp = client.put(
        "/api/embedding-config", json={"provider": "anthropic", "model": "x"}
    )
    assert resp.status_code == 422  # anthropic은 Literal["openai","ollama"] 밖

    resp = client.put(
        "/api/embedding-config", json={"provider": "openai", "model": "  "}
    )
    assert resp.status_code == 400  # 공백만 있는 모델명은 거부(set_embedding_config)
