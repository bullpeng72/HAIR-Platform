"""Tool/Skill 호출 제약 해소 — 사용자 요청(2026-11-18, "대화에서 업무
처리시 Tool, skill 활용, 메시지 길이 관리에 제약사항이 많아... Skill,
tool 사용 제약을 없애고... 엔터프라이즈 환경 적용이 가능한 수준의
확실한 개선 방안을 도출해줘" 개선축 A — B(토큰 예산)·C(메모리) 다음
세 번째이자 마지막 축). 적용 범위는 사용자 확인(2026-11-18, "Ollama
포함 전체 provider 적용")에 따라 Anthropic/OpenAI/Ollama 전부.

이번 세션 내내 실측으로 확인한 구조적 제약 — "기본 AI"(`run_default_
ai_job()`)와 "단일 Agent 멘션"(`agent_runtime.execute_agent()`) 2개
경로는 CrewAI의 Agent/Task/Tool 루프가 아예 없어서, custom_python/mcp
도구를 아무리 skill_pool에 연결해둬도 실제로 호출할 수 없었다(native
도구만 `agent_runtime.retrieve_context()`로 "정적 주입"될 뿐). 실제
도구가 호출되는 건 `@Team 멘션`(`crew_runtime.run_crew()`)뿐이었다.

이 모듈은 CrewAI를 거치지 않고, Anthropic/OpenAI/Ollama가 각자 지원
하는 네이티브 함수 호출(tool-calling) API를 직접 써서 "기본 AI"/
"단일 Agent 멘션" 양쪽에 같은 역량을 준다. 도구 **실행**은
`pool_registry.resolve_pool_tools()`가 이미 만들어주는 CrewAI Tool
객체를 그대로 재사용한다(`.run(**kwargs)`로 호출 — 실행 로직을
새로 만들지 않는다, `.args_schema.model_json_schema()`로 각 도구의
실제 파라미터 스키마를 그대로 가져와 provider별 함수 스키마를
만든다 — 모든 도구가 `query: str` 하나뿐이라고 가정하지 않는다).

**범위를 `custom_python`으로만 좁힌 이유**(구현 중 실측으로 발견,
2026-11-18) — 처음엔 native(vectordb/graphdb/tavily)도 포함했으나,
native 도구는 이미 `agent_runtime.retrieve_context()`/`augment_with_
retrieval()`이 **정적 주입**(LLM 호출 전에 검색을 미리 실행해 결과를
system prompt에 강제로 끼워 넣음, 항상 보장됨)으로 잘 동작하고 있었다
— 여기에 동적 함수 호출까지 추가로 열어주면 같은 검색이 "이미 주입된
결과"와 "모델이 다시 선택적으로 호출하는 도구" 이중 경로로 겹쳐,
정적 주입에 의존하던 기존 동작(`NO_TOOL_LOOP_GUARD` 모순 정정 로직
등)과 충돌한다(실제로 기존 테스트 다수가 `llm.call_llm()`이 불리는
것을 전제해 깨졌다). `custom_python`은 지금까지 **아무 메커니즘도
없었던** 진짜 공백이라 이 축의 가치가 전부 여기 있다 — native는
기존 정적 주입 경로를 그대로 둔다(건드리지 않음).

**범위에서 의도적으로 뺀 것**: `mcp_stdio`/`mcp_remote` 도구는 이
라운드에서 제외한다 — ExitStack으로 접속을 유지한 채 여러 번의
LLM 왕복에 걸쳐 재사용해야 하는데, 그 수명 관리를 이 1회성 호출
경로에 안전하게 통합하는 건 별도 검토가 필요한 더 큰 작업이다(admin
전용 신뢰 경계이기도 하다). `pool_registry.unsupported_realtime_
skill_names()`가 여전히 이 두 종류만 "호출 불가"로 판정하도록
남겨둔다 — `custom_python`은 이제 이 모듈을 통해 실제로 호출되므로
그 판정에서 빠진다(`conversations.py`에서 함께 조정)."""

from __future__ import annotations

import contextlib
import contextvars
import json
import sqlite3

from hair_platform import pool_registry

MAX_TOOL_CALL_ITERATIONS = 5


def _supported_tool_ids(conn: sqlite3.Connection, skills: list[str]) -> set[int]:
    import json

    tool_pool_ids: set[int] = set()
    skill_set = set(skills)
    for row in conn.execute(
        "SELECT * FROM skill_pool WHERE augmentation_mode = 'tool'"
    ).fetchall():
        if row["name"] in skill_set:
            tool_pool_ids.update(json.loads(row["default_tool_pool_ids"]))
    if not tool_pool_ids:
        return set()
    placeholders = ",".join("?" * len(tool_pool_ids))
    rows = conn.execute(
        f"SELECT id FROM tool_pool WHERE id IN ({placeholders}) "
        f"AND kind = 'custom_python' AND is_active = 1",
        tuple(tool_pool_ids),
    ).fetchall()
    return {r["id"] for r in rows}


def resolve_callable_tools(
    conn: sqlite3.Connection,
    skills: list[str],
    agent_id: int | None = None,
) -> list:
    """이 경로(기본 AI/단일 Agent 멘션)가 실제로 호출할 수 있는 도구만
    돌려준다 — `custom_python`만(위 모듈 독스트링 참고 — native는 기존
    정적 주입 경로를 그대로 쓰고, mcp는 이번 라운드에서 제외).
    `pool_registry._custom_python_crewai_tool()`을 그대로 재사용해
    실제 CrewAI Tool 객체를 만든다(`resolve_pool_tools()`와 같은 실행
    로직 — 중복 구현 없음). `agent_id`를 넘기면(단일 Agent 멘션) 명시적
    연결(`agent_tool_links`)도 같은 kind 필터로 합친다."""
    tool_ids = _supported_tool_ids(conn, skills)
    if agent_id is not None:
        linked_ids = set(pool_registry.list_agent_tool_ids(conn, agent_id))
        if linked_ids:
            placeholders = ",".join("?" * len(linked_ids))
            rows = conn.execute(
                f"SELECT id FROM tool_pool WHERE id IN ({placeholders}) "
                f"AND kind = 'custom_python' AND is_active = 1",
                tuple(linked_ids),
            ).fetchall()
            tool_ids.update(r["id"] for r in rows)
    if not tool_ids:
        return []

    tools: list = []
    for tool_id in tool_ids:
        row = conn.execute(
            "SELECT * FROM tool_pool WHERE id = ? AND is_active = 1", (tool_id,)
        ).fetchone()
        if row is None:
            continue
        tools.append(pool_registry._custom_python_crewai_tool(row))
    return tools


# 실측 확인 — CrewAI `Tool.description`은 `"Tool Name: ...\nTool
# Arguments: ...\nTool Description: ..."` 형태로 이름/파라미터까지
# 전부 합쳐 CrewAI 자신의 ReAct 프롬프트용으로 자동 생성한 문자열이다
# (name/parameters는 이미 스키마의 다른 필드로 따로 들어가므로 여기
# 또 넣으면 중복). 원래의 깨끗한 설명은 `tool.func.__doc__`에 그대로
# 남아있어 그쪽을 우선 쓰고, 없으면(드문 경우) 그 자동 생성 문자열로
# 폴백한다.
def _clean_description(tool) -> str:
    doc = getattr(getattr(tool, "func", None), "__doc__", None)
    return doc.strip() if doc else tool.description


def tool_schema_openai(tool) -> dict:
    """OpenAI/Ollama가 공유하는 함수 스키마 형식(`tools=[{"type":
    "function", "function": {...}}]`)."""
    schema = dict(tool.args_schema.model_json_schema())
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": _clean_description(tool),
            "parameters": schema,
        },
    }


def tool_schema_anthropic(tool) -> dict:
    schema = dict(tool.args_schema.model_json_schema())
    schema.pop("title", None)
    return {
        "name": tool.name,
        "description": _clean_description(tool),
        "input_schema": schema,
    }


# 호출 기록(2026-11-22, "스킬, 툴, 에이전트 각각의 실행 품질을 점검해줘" 실측) — 실적 보고서
# 작성자가 차트 생성기를 불러 설정을 받고도 최종 답에 ```chart를 빼먹어, 화면에 그래프가
# 안 나왔다. provider별 도구 루프 3개의 시그니처를 바꾸지 않고, 호출부가 `record_calls()`
# 안에서 실행하면 이번 실행의 (도구 이름, 결과)를 모은다.
_CALL_LOG: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "tool_call_log", default=None
)


@contextlib.contextmanager
def record_calls():
    log: list[tuple[str, str]] = []
    token = _CALL_LOG.set(log)
    try:
        yield log
    finally:
        _CALL_LOG.reset(token)


_CHART_TYPES = ("bar", "line", "pie", "doughnut", "radar")


def _chart_config(result: str) -> str | None:
    """도구 결과가 Chart.js 설정(차트 생성기의 성공 결과)이면 그 JSON 문자열을 돌려준다."""
    try:
        cfg = json.loads(result)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if (
        isinstance(cfg, dict)
        and cfg.get("type") in _CHART_TYPES
        and isinstance(cfg.get("data"), dict)
        and isinstance(cfg["data"].get("datasets"), list)
    ):
        return result
    return None


def ensure_charts_included(output: str, calls: list[tuple[str, str]]) -> str:
    """차트 생성기가 설정을 만들어 줬는데 최종 답에 차트 펜스가 하나도 없으면, 만든 차트를
    답 끝에 붙인다(같은 설정은 한 번만). 답에 이미 ```chart가 있으면 모델이 넣은 것을 존중한다."""
    if not output or "```chart" in output:
        return output
    charts: list[str] = []
    for _name, result in calls:
        cfg = _chart_config(result)
        if cfg and cfg not in charts:
            charts.append(cfg)
    if not charts:
        return output
    blocks = "\n\n".join(f"```chart\n{cfg}\n```" for cfg in charts)
    return f"{output.rstrip()}\n\n{blocks}\n"


# 도구 호출 턴의 본문 보존(2026-11-22 재실행 실측) — 스펙 작성자는 첫 턴에 Spec 본문(1,283자)을
# 쓰면서 동시에 린터·실패모드 도구를 불렀고, 최종 턴에는 검증 결과만 썼다. provider별 도구
# 루프 3개가 모두 "도구를 부르지 않은 마지막 턴의 텍스트"만 돌려줘, 요청한 Spec 본문이 결과에서
# 사라졌다(프롬프트 지침을 추가해도 재현). 도구 호출 턴에 쓴 본문을 최종 답 앞에 살린다.
_INTERIM_MIN_CHARS = 300
_INTERIM_COVERED_RATIO = 0.5


def merge_interim_text(interim: list[str], final: str) -> str:
    """도구 호출 턴에 쓴 텍스트 중 실제 본문만 최종 답 앞에 붙인다.

    - 제목(`#`)이 있으면 첫 제목 앞의 진행 안내("먼저 ~하겠습니다")는 버린다.
    - 제목이 없고 짧으면(`_INTERIM_MIN_CHARS` 미만) 진행 안내로 보고 버린다.
    - 최종 답이 이미 그 본문을 다시 실었으면(내용 줄의 절반 이상이 최종 답에 있음) 붙이지 않는다.
    """
    kept: list[str] = []
    for text in interim:
        text = (text or "").strip()
        lines = text.splitlines()
        heading = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("#")), None)
        if heading is not None:
            text = "\n".join(lines[heading:]).strip()
        elif len(text) < _INTERIM_MIN_CHARS:
            continue
        if not text:
            continue
        body_lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 20]
        if body_lines:
            covered = sum(1 for ln in body_lines if ln in final)
            if covered / len(body_lines) >= _INTERIM_COVERED_RATIO:
                continue
        kept.append(text)
    if not kept:
        return final
    return "\n\n".join([*kept, final]) if final.strip() else "\n\n".join(kept)


def run_tool(tools_by_name: dict, name: str, arguments: dict) -> str:
    """`arguments`가 도구의 실제 파라미터와 안 맞거나(모델이 틀린 인자를
    지어냄) 도구 실행 자체가 실패해도 예외를 밖으로 내보내지 않는다 —
    도구 호출 실패가 전체 응답을 막으면 안 된다는 기존 원칙
    (`agent_runtime.retrieve_context()`의 vectordb 조회와 동일)을 여기도
    적용한다. 실패 사유를 문자열로 그대로 돌려줘 모델이 다음 턴에
    스스로 재시도하거나 포기할 수 있게 한다."""
    from hair_platform import progress  # 실행 중 단계 표시(2026-11-23, P0-2)

    progress.report(f"도구 실행 중: {progress.TOOL_LABELS.get(name, name)}")
    tool = tools_by_name.get(name)
    if tool is None:
        result = f"알 수 없는 도구입니다: {name}"
    else:
        try:
            result = str(tool.run(**arguments))
        except Exception as exc:  # noqa: BLE001
            result = f"도구 실행 중 오류가 발생했습니다: {exc}"
    log = _CALL_LOG.get()
    if log is not None:
        log.append((name, result))
    return result
