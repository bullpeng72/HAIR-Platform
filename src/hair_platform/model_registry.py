"""모델 레지스트리 — 사용자 요청(2026-10-06, "운영 관리에 사용 가능한
ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을
불러오는 방식으로 UI/UX를 개선").

지금까지 "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"의
"모델명" 입력란이 전부 자유 텍스트였다 — 실제로 쓸 수 있는 모델이 뭔지
모른 채 타이핑해야 했고, 실제로 ollama 기본값("llama3.1")이 로컬에
설치돼 있지 않아 404가 난 사고가 있었다(docs/LIMITS.md 참고). `skill_pool`/
`tool_pool`과 같은 패턴 — "등록 → 드롭다운에서 선택"으로 바꾼다.

`fetch_available_models()`는 각 provider가 실제로 제공하는 모델 목록
조회 API를 쓴다(전부 토큰 생성이 없는 메타데이터 조회라 비용이 없거나
무시할 수준이다 — `llm.call_llm()`과는 다른 경계):
- ollama: 로컬 서버의 `GET /api/tags`(완전 무료, 키 불필요)
- openai: `openai.OpenAI().models.list()`(API 키 필요, 과금 없는 카탈로그
  조회) — whisper/tts/dall-e/embedding/moderation 등 채팅에 못 쓰는
  모델은 제외한다.
- anthropic: `anthropic.Anthropic().models.list()`(API 키 필요, 과금
  없는 카탈로그 조회)
"""

from __future__ import annotations

import os
import sqlite3

from hair_platform import audit

PROVIDERS = ("anthropic", "openai", "ollama")

# openai.models.list()가 돌려주는 것 중 채팅에 못 쓰는 모델(음성/이미지/
# 임베딩/검열)을 걸러내는 키워드 — 정확한 allow-list 대신 덜 공격적인
# deny-list를 쓴다(신규 채팅 모델이 나와도 이름에 아래 키워드가 없으면
# 자동으로 보이게 하기 위함).
_OPENAI_NON_CHAT_KEYWORDS = (
    "whisper",
    "tts",
    "dall-e",
    "embedding",
    "moderation",
    "davinci-002",
    "babbage",
)


def create_model(
    conn: sqlite3.Connection, actor_id: int, provider: str, name: str
) -> int:
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported_provider:{provider}")
    name = name.strip()
    if not name:
        raise ValueError("empty_name")
    cur = conn.execute(
        "INSERT INTO model_registry (provider, name) VALUES (?, ?)",
        (provider, name),
    )
    model_id = cur.lastrowid
    assert model_id is not None
    audit.record(
        conn,
        actor_id,
        "model_registry_created",
        "model_registry",
        model_id,
        after={"provider": provider, "name": name},
    )
    conn.commit()
    return model_id


def list_models(conn: sqlite3.Connection, provider: str | None = None) -> list[dict]:
    query = "SELECT * FROM model_registry"
    params: tuple = ()
    if provider is not None:
        query += " WHERE provider = ?"
        params = (provider,)
    query += " ORDER BY provider, name"
    rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r["id"],
            "provider": r["provider"],
            "name": r["name"],
            "is_active": bool(r["is_active"]),
        }
        for r in rows
    ]


def set_model_active(
    conn: sqlite3.Connection, actor_id: int, model_id: int, is_active: bool
) -> None:
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    conn.execute(
        "UPDATE model_registry SET is_active = ? WHERE id = ?",
        (int(is_active), model_id),
    )
    audit.record(
        conn,
        actor_id,
        "model_registry_active_changed",
        "model_registry",
        model_id,
        before={"is_active": bool(row["is_active"])},
        after={"is_active": is_active},
    )
    conn.commit()


def delete_model(conn: sqlite3.Connection, actor_id: int, model_id: int) -> None:
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    conn.execute("DELETE FROM model_registry WHERE id = ?", (model_id,))
    audit.record(
        conn,
        actor_id,
        "model_registry_deleted",
        "model_registry",
        model_id,
        before={"provider": row["provider"], "name": row["name"]},
        after=None,
    )
    conn.commit()


def fetch_available_models(provider: str) -> list[str]:
    """해당 provider가 실제로 제공/보유한 모델 이름을 라이브로 조회한다
    (등록 화면의 "불러오기" 버튼 전용 — 결과를 그대로 저장하지 않고
    화면에 후보로 보여준 뒤 사용자가 고른 것만 `create_model()`로
    등록한다). 접속 실패·키 미설정 시 그대로 예외를 던진다 — 호출자
    (app.py)가 사람이 읽을 수 있는 메시지로 바꿔 보여준다."""
    if provider == "ollama":
        import httpx  # 지연 import — llm._call_ollama()와 같은 경계

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        resp = httpx.get(f"{base_url}/api/tags", timeout=10)
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))

    if provider == "openai":
        import openai  # 지연 import — llm._call_openai()와 같은 경계

        client = openai.OpenAI()
        # `.data`만 읽으면 첫 페이지만 보인다 — SDK의 자동 페이지네이션
        # (`__iter__`)을 그대로 돌려 전체 모델을 받는다.
        names = [m.id for m in client.models.list()]
        return sorted(
            n
            for n in names
            if not any(kw in n.lower() for kw in _OPENAI_NON_CHAT_KEYWORDS)
        )

    if provider == "anthropic":
        import anthropic  # 지연 import — llm._call_anthropic()와 같은 경계

        client = anthropic.Anthropic()
        return sorted(m.id for m in client.models.list())

    raise ValueError(f"unsupported_provider:{provider}")
