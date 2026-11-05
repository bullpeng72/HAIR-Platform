"""`content_type`별로 산출물 구문 검사를 분기하는 pluggable 레지스트리
— 사용자 요청(2026-11-05, "업무 배정, 파이프라인, crew 팀 구성 등을
이용해 로드러너 수준의 게임... 개선 계획을 수립해줘" → 로드맵 5번
"콘텐츠 타입별 pluggable 검증기 구조... 다음 개선 진행해줘").

`content_type`(`models.py`의 `ContentType = Literal["text","markdown",
"html","code"]`, DB `CHECK` 제약까지 걸린 진짜 검증된 4값 필드)이
요청자가 명시한 값일 때는 신뢰할 수 있다 — `deliverables.py`의
`run_adhoc_crew_job()`/`run_team_crew_job()`/`_persist_partial_crew_
outputs()`가 그 경우다(이 모듈이 와이어링되는 유일한 대상, 아래
"적용 범위" 참고).

**이 모듈이 메우는 공백**: `content_type="html"`로 명시 요청하면
`deliverables._CONTENT_TYPE_INSTRUCTIONS["html"]`이 LLM에게 "완전한
HTML 문서로 작성하세요(`<!DOCTYPE html>`부터 `</html>`까지)"라고
지시한다 — 마크다운 펜스로 감싸라는 말이 전혀 없다. 그래서
`agent_evaluation.compute_code_syntax_check()`(펜스 코드 블록만
정규식으로 스캔)는 이 경우 거의 항상 `checked_blocks=0`(미검사)으로
빠져 검증 기회를 통째로 놓친다 — `content_type="code"`(지시문이
"전체를 ``` 코드 블록 안에 넣고"라 펜스를 명시적으로 요구)와
`"text"`/`"markdown"`은 기존 펜스 스캔만으로 이미 충분해 손대지
않는다.

**적용 범위**: `pipeline_runtime.py`의 파이프라인 단계 산출물은
`content_type`이 `'text'`로 하드코딩돼 신뢰할 수 없으므로(별개의,
이미 알려진, 범위 밖 이슈) 이 모듈을 쓰지 않는다 — 그 경로는 기존
`agent_evaluation.compute_code_syntax_check()` 직접 호출을 그대로
유지한다(단, 그 함수 자체의 html 버그 수정은 공유 헬퍼라 자동으로
혜택을 받는다)."""

from __future__ import annotations

from collections.abc import Callable

from hair_platform import agent_evaluation

_SUPPLEMENTARY_VALIDATORS: dict[str, Callable[[str], dict]] = {
    "html": agent_evaluation.compute_raw_html_syntax_check,
}


def validate_by_content_type(content_type: str, content: str) -> dict:
    """`gate_scores["code_syntax"]`에 들어갈 결과를 반환한다. 먼저
    기존 펜스 스캔(`compute_code_syntax_check`)을 항상 시도하고
    (`"code"`/`"text"`/`"markdown"`/미등록 값은 이게 전부 — 로드맵
    4번 동작과 100% 동일, 회귀 없음), 그게 아무것도 못 찾았을 때만
    (`checked_blocks == 0`) `content_type`에 등록된 보조 검증기를
    시도한다 — 순수 덧셈이라 이미 측정된 결과를 덮어쓰지 않는다."""
    base = agent_evaluation.compute_code_syntax_check(content)
    if base["checked_blocks"] > 0:
        return base
    supplementary = _SUPPLEMENTARY_VALIDATORS.get(content_type)
    if supplementary is None:
        return base
    return supplementary(content)
