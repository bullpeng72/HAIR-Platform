"""모델 레지스트리 — 사용자 요청(2026-10-06, "운영 관리에 사용 가능한
ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을
불러오는 방식으로 UI/UX를 개선").

지금까지 "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"의
"모델명" 입력란이 전부 자유 텍스트였다 — 실제로 쓸 수 있는 모델이 뭔지
모른 채 타이핑해야 했고, 실제로 ollama 기본값("llama3.1")이 로컬에
설치돼 있지 않아 404가 난 사고가 있었다. `skill_pool`/
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

import json
import os
import sqlite3
from collections.abc import Sequence

from hair_platform import audit, model_benchmark

PROVIDERS = ("anthropic", "openai", "ollama")

# 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화 각 tier에
# 적용되는 모델을 매핑하는 기능을 제공... 향후 새로운 모델 출시되거나
# 모델 프로바이더 변경시에도 각 Tier별 모델 맵핑 변경하여 관리") —
# `agent_optimizer.py`의 역할 분류 tier 3종과 반드시 같은 문자열이어야
# 한다(`agent_optimizer.TIER_DEEP_REASONING` 등). agent_optimizer가
# model_registry를 import하므로(역방향 import는 순환 참조가 되어 불가),
# 여기선 독립적으로 같은 문자열 리터럴을 든다.
OPTIMIZATION_TIERS = ("deep_reasoning", "precision_fast", "standard")

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


def _refresh_model_cache(conn: sqlite3.Connection) -> None:
    """레지스트리가 바뀌면 호출 지점이 쓰는 캐시(생성 옵션·기본 모델)를 다시 읽는다
    (`model_options.refresh`) — 모델명 없이 호출할 때 고르는 기본 모델이 레지스트리와
    어긋나지 않게 한다(2026-11-21, 하드코딩 점검 1번)."""
    from hair_platform import model_options

    model_options.refresh(conn)


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
    _refresh_model_cache(conn)
    return model_id


def list_models(conn: sqlite3.Connection, provider: str | None = None) -> list[dict]:
    # 사용자 요청(2026-11-12, "성능 Test하고 결과 표시") — 모델마다 또
    # 쿼리하지 않고 `model_benchmark.latest_benchmarks_by_model()`로
    # 한 번에 모아와 각 모델 응답에 끼워 넣는다(모델이 아직 테스트
    # 안 됐으면 None).
    query = "SELECT * FROM model_registry"
    params: tuple = ()
    if provider is not None:
        query += " WHERE provider = ?"
        params = (provider,)
    query += " ORDER BY provider, name"
    rows = conn.execute(query, params).fetchall()
    latest_by_model = model_benchmark.latest_benchmarks_by_model(conn)
    return [
        {
            "id": r["id"],
            "provider": r["provider"],
            "name": r["name"],
            "is_active": bool(r["is_active"]),
            "optimization_tiers": json.loads(r["optimization_tiers"] or "[]"),
            "context_window": r["context_window"],
            "sampling_options": json.loads(r["sampling_options"] or "{}"),
            "price_input_per_1m": r["price_input_per_1m"],
            "price_output_per_1m": r["price_output_per_1m"],
            "max_concurrent": r["max_concurrent"],
            "latest_benchmark": latest_by_model.get(r["id"]),
        }
        for r in rows
    ]


def set_optimization_tiers(
    conn: sqlite3.Connection,
    actor_id: int,
    model_id: int,
    optimization_tiers: Sequence[str],
) -> None:
    """이 모델이 "LLM 설정 최적화"의 어떤 tier(deep_reasoning/
    precision_fast/standard)에 직접 적용되는지 admin이 지정·변경·
    초기화(`[]`)한다 — 지정하면 `agent_optimizer.recommend_model()`이
    그 tier를 추천할 때 이름 패턴 추정보다 이 모델을 항상 먼저 후보로
    올린다. 새 모델 출시·provider 교체 때 코드를 고치지 않고 이 매핑만
    바꾸면 되고, 몇 번이든 다시 바꿀 수 있다(한 번 정하면 못 바꾸는
    제약 없음)."""
    invalid = sorted(set(optimization_tiers) - set(OPTIMIZATION_TIERS))
    if invalid:
        raise ValueError(f"invalid_optimization_tier:{','.join(invalid)}")
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    normalized = sorted(set(optimization_tiers))
    conn.execute(
        "UPDATE model_registry SET optimization_tiers = ? WHERE id = ?",
        (json.dumps(normalized, ensure_ascii=False), model_id),
    )
    audit.record(
        conn,
        actor_id,
        "model_registry_optimization_tiers_changed",
        "model_registry",
        model_id,
        before={"optimization_tiers": json.loads(row["optimization_tiers"] or "[]")},
        after={"optimization_tiers": normalized},
    )
    conn.commit()
    _refresh_model_cache(conn)


def set_context_window(
    conn: sqlite3.Connection, actor_id: int, model_id: int, context_window: int | None
) -> None:
    """사용자 요청(2026-11-18, "...엔터프라이즈 환경 적용이 가능한 수준의
    확실한 개선 방안을 도출해줘" 개선축 B) — 이 모델이 실제로 받아들이는
    토큰 한도를 admin이 등록한다. `token_budget.resolve_context_window()`
    가 `_build_transcript()`에서 이 값을 읽어 프롬프트가 한도를 넘기기
    **전에** 미리 잘라낸다(지금까지는 모델이 빈 응답/오류를 내고서야
    알 수 있었다 — 2026-11-18 실측: 첨부파일 19KB 하나만으로 로컬 모델
    32,768토큰 한도를 넘겨 15분을 돌고도 빈 응답). `None`(초기화)이면
    `token_budget.DEFAULT_CONTEXT_WINDOW_BY_PROVIDER`의 provider별
    보수적 기본값으로 폴백한다."""
    if context_window is not None and context_window <= 0:
        raise ValueError("context_window_must_be_positive")
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    conn.execute(
        "UPDATE model_registry SET context_window = ? WHERE id = ?",
        (context_window, model_id),
    )
    audit.record(
        conn,
        actor_id,
        "model_registry_context_window_changed",
        "model_registry",
        model_id,
        before={"context_window": row["context_window"]},
        after={"context_window": context_window},
    )
    conn.commit()
    _refresh_model_cache(conn)


def set_max_concurrent(conn: sqlite3.Connection, actor_id: int, model_id: int, value: int | None) -> None:
    """모델별 동시 실행 상한(2026-11-27, 개선 계획 3-1 — `llm_gate`). None이면 기본값(Ollama 1, 클라우드 상한 없음)."""
    if value is not None and not 1 <= value <= 64:
        raise ValueError("max_concurrent_out_of_range")
    row = conn.execute("SELECT * FROM model_registry WHERE id = ?", (model_id,)).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    conn.execute("UPDATE model_registry SET max_concurrent = ? WHERE id = ?", (value, model_id))
    audit.record(conn, actor_id, "model_registry_max_concurrent_changed", "model_registry", model_id,
                 before={"max_concurrent": row["max_concurrent"]}, after={"max_concurrent": value})
    conn.commit()
    _refresh_model_cache(conn)


def set_sampling_options(
    conn: sqlite3.Connection, actor_id: int, model_id: int, options: dict | None
) -> dict:
    """사용자 요청(2026-11-21, "특정 모델만을 위한 하드코딩은 없도록 해줘") —
    모델별 생성 옵션(top_k/top_p)을 admin이 등록한다. 모든 provider 호출
    (`llm.py`)과 CrewAI(`crew_runtime._crewai_llm`)가 `model_options` 캐시로
    이 값을 읽어 provider 형식에 맞게 보낸다. 빈 값이면 아무것도 보내지 않는다
    (그 모델의 기본값). 반환값은 저장된(정규화된) 옵션."""
    from hair_platform import model_options

    cleaned = model_options.validate(options)
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    conn.execute(
        "UPDATE model_registry SET sampling_options = ? WHERE id = ?",
        (json.dumps(cleaned), model_id),
    )
    audit.record(
        conn,
        actor_id,
        "model_registry_sampling_options_changed",
        "model_registry",
        model_id,
        before={"sampling_options": json.loads(row["sampling_options"] or "{}")},
        after={"sampling_options": cleaned},
    )
    conn.commit()
    model_options.set_cached(row["provider"], row["name"], cleaned)
    return cleaned


def set_pricing(
    conn: sqlite3.Connection,
    actor_id: int,
    model_id: int,
    price_input_per_1m: float | None,
    price_output_per_1m: float | None,
) -> dict:
    """모델별 단가(100만 토큰당, 입력/출력) — admin 입력(2026-11-22, 하드코딩·스텁 점검 3순위).
    `llm_usage.report()`가 이 값이 있는 모델만 금액을 계산한다(없으면 토큰 수만). 단가표를
    코드에 넣지 않는다(provider·계약마다 다르고 자주 바뀐다). 로컬 모델은 0으로 둘 수 있다."""
    for value in (price_input_per_1m, price_output_per_1m):
        if value is not None and value < 0:
            raise ValueError("negative_price")
    row = conn.execute("SELECT * FROM model_registry WHERE id = ?", (model_id,)).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    conn.execute(
        "UPDATE model_registry SET price_input_per_1m = ?, price_output_per_1m = ? WHERE id = ?",
        (price_input_per_1m, price_output_per_1m, model_id),
    )
    audit.record(
        conn,
        actor_id,
        "model_registry_pricing_changed",
        "model_registry",
        model_id,
        before={"input": row["price_input_per_1m"], "output": row["price_output_per_1m"]},
        after={"input": price_input_per_1m, "output": price_output_per_1m},
    )
    conn.commit()
    return {"price_input_per_1m": price_input_per_1m, "price_output_per_1m": price_output_per_1m}


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
    _refresh_model_cache(conn)


def delete_model(conn: sqlite3.Connection, actor_id: int, model_id: int) -> None:
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    # 버그 수정(2026-11-12, 사용자 보고 — "모델 삭제 시 500 Internal
    # Server Error") — `model_benchmarks.model_registry_id`가 이
    # 테이블을 FK로 참조하는데(`PRAGMA foreign_keys=ON`이 전역으로
    # 걸려 있다, `db.py` 참고), 그 모델을 "성능 테스트"로 한 번이라도
    # 테스트해 이력이 쌓인 뒤엔 바로 지우려 하면 FK 제약 위반으로 DB가
    # 그 자리에서 예외를 던졌다(처리 안 된 500). DB 레벨 `ON DELETE
    # CASCADE` 대신, 이 코드베이스가 이미 `data_admin.py` 전반에서
    # 쓰는 "앱 레벨에서 먼저 자식 행을 지운다" 관례를 그대로 따른다.
    conn.execute(
        "DELETE FROM model_benchmarks WHERE model_registry_id = ?", (model_id,)
    )
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
    _refresh_model_cache(conn)


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
