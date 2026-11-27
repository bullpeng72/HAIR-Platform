"""Agent별 LLM provider/model/tuning_config 최적화 추천(admin 기능, 사용자
요청 2026-10-18, "Agent의 목표, 기능, 입출력 등을 고려해 가용한 LLM 모델에서
종류 및 설정 등을 최적화 setting 하는 admin 기능을 추가해줘").

이 세션에서 22개 역할 전원에 대해 실측(실패 재현·타임아웃·앵커링 등)을 거쳐
이미 `reference_pipeline_seeds.AGENT_SPECS`에 박아 둔 `tuning_config`가
가장 신뢰할 수 있는 1차 추천 소스다(예: "자율 개발 크루" 12명 전원
`reasoning` 끄기 — delegation 체인이 길어지면 빈 응답→JSONDecodeError로
전체가 실패하는 걸 반복 재현해서 내린 결정, `reference_pipeline_seeds.py`의
"개발자" 항목 주석 참고). 이 모듈은 그 지식을 "알려진 역할" 추천으로 그대로
재사용하고, 새 역할(미리 정의되지 않은 role_name)에는 skill_tags 키워드
기반 보수적 추정으로 대체한다.

이 모듈은 전부 읽기 전용이다 — DB에 아무것도 쓰지 않는다. 실제 저장은
기존 `PATCH /api/agent-pool/{id}/model`·`/tuning` 엔드포인트가 그대로
담당한다(새 쓰기 경로를 만들지 않음). `refine_with_llm()`만 실제 LLM
API를 호출해 비용이 발생한다 — 명시적으로 호출했을 때만.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3

from hair_platform import llm, model_registry
from hair_platform.models import AgentTuningConfig
from hair_platform.reference_pipeline_seeds import AGENT_SPECS

# role_name -> tuning_config, AGENT_SPECS에서 그대로 가져온 "알려진 역할"의
# 1차 추천 소스(중복 정의하지 않고 재사용).
_KNOWN_ROLE_TUNING: dict[str, dict] = {
    role_name: dict(tuning) for role_name, _skills, tuning in AGENT_SPECS
}

TIER_DEEP_REASONING = "deep_reasoning"
TIER_PRECISION_FAST = "precision_fast"
TIER_STANDARD = "standard"

# 알려지지 않은 role_name에만 쓰는 tier별 기본 tuning_config(알려진 역할은
# 항상 AGENT_SPECS의 실제 값을 그대로 추천한다).
_TIER_DEFAULT_TUNING: dict[str, dict] = {
    TIER_DEEP_REASONING: {
        "reasoning": True,
        "max_reasoning_attempts": 2,
        "temperature": 0.3,
    },
    TIER_PRECISION_FAST: {"temperature": 0.2, "max_retry_limit": 3},
    TIER_STANDARD: {"temperature": 0.3},
}

# 미지의 역할을 skill_tags 키워드로 분류하는 규칙 — 순서가 우선순위다(위에서
# 먼저 매칭되면 그걸로 확정). 리뷰/품질 계열을 "정밀하지만 reasoning은 끈다"로
# 분류하는 이유는 _TIER_DEFAULT_TUNING과 같은 실측 근거(위 docstring)다.
_SKILL_KEYWORD_RULES: list[tuple[str, str]] = [
    ("review", TIER_PRECISION_FAST),
    ("qa", TIER_PRECISION_FAST),
    ("quality", TIER_PRECISION_FAST),
    ("test", TIER_PRECISION_FAST),
    ("gate", TIER_PRECISION_FAST),
    ("lint", TIER_PRECISION_FAST),
    ("spec", TIER_DEEP_REASONING),
    ("analysis", TIER_DEEP_REASONING),
    ("architecture", TIER_DEEP_REASONING),
    ("strategy", TIER_DEEP_REASONING),
    ("swot", TIER_DEEP_REASONING),
    ("requirement", TIER_DEEP_REASONING),
    ("design-guide", TIER_DEEP_REASONING),
    ("writing", TIER_STANDARD),
    ("report", TIER_STANDARD),
    ("presentation", TIER_STANDARD),
    ("coordination", TIER_STANDARD),
    ("planning-guide", TIER_STANDARD),
]


def _tier_for_known_tuning(tuning: dict) -> str:
    if tuning.get("reasoning") is True:
        return TIER_DEEP_REASONING
    if (tuning.get("temperature") or 1.0) <= 0.2:
        return TIER_PRECISION_FAST
    return TIER_STANDARD


# 한글 키워드(2026-11-21) — 스킬·역할 이름이 대부분 한글이다. 영문 규칙과 같은 기준:
# 분석·설계·전략처럼 깊게 생각해야 하는 일 → 심층추론, 구현·검증처럼 정확하고 빠르게
# 반복하는 일 → 정밀·신속, 작성·정리·조율 → 표준.
_KOREAN_KEYWORD_RULES: list[tuple[str, str]] = [
    ("분석", TIER_DEEP_REASONING),
    ("전략", TIER_DEEP_REASONING),
    ("설계", TIER_DEEP_REASONING),
    ("기획", TIER_DEEP_REASONING),
    ("요구사항", TIER_DEEP_REASONING),
    ("아키텍처", TIER_DEEP_REASONING),
    ("리서치", TIER_DEEP_REASONING),
    ("조사", TIER_DEEP_REASONING),
    ("사업성", TIER_DEEP_REASONING),
    ("프레임워크", TIER_DEEP_REASONING),
    ("스펙", TIER_DEEP_REASONING),
    ("구현", TIER_PRECISION_FAST),
    ("개발", TIER_PRECISION_FAST),
    ("코드", TIER_PRECISION_FAST),
    ("테스트", TIER_PRECISION_FAST),
    ("검증", TIER_PRECISION_FAST),
    ("리뷰", TIER_PRECISION_FAST),
    ("점검", TIER_PRECISION_FAST),
    ("품질", TIER_PRECISION_FAST),
    ("커밋", TIER_PRECISION_FAST),
    ("보안", TIER_PRECISION_FAST),
    ("작성", TIER_STANDARD),
    ("보고", TIER_STANDARD),
    ("문서", TIER_STANDARD),
    ("발표", TIER_STANDARD),
    ("카피", TIER_STANDARD),
    ("정리", TIER_STANDARD),
    ("조율", TIER_STANDARD),
    ("회의록", TIER_STANDARD),
]
# 표가 같으면 더 신중한 쪽(심층추론 > 정밀·신속 > 표준)을 고른다 — 과소 배정보다
# 과대 배정이 결과 품질 면에서 안전하다.
_TIER_TIEBREAK = [TIER_STANDARD, TIER_PRECISION_FAST, TIER_DEEP_REASONING]


def _tier_votes(role_name: str, skill_tags: list[str]) -> dict[str, int]:
    votes: dict[str, int] = {}
    for text in [role_name, *skill_tags]:
        lower = text.lower()
        for keyword, tier in (*_SKILL_KEYWORD_RULES, *_KOREAN_KEYWORD_RULES):
            if keyword in lower:
                votes[tier] = votes.get(tier, 0) + 1
    return votes


def classify_role(role_name: str, skill_tags: list[str]) -> tuple[str, dict, str]:
    """(tier, 추천 tuning_config, 분류 근거) — 근거는 "known_role"/
    "skill_heuristic"/"default_fallback" 중 하나."""
    known = _KNOWN_ROLE_TUNING.get(role_name)
    if known is not None:
        return _tier_for_known_tuning(known), dict(known), "known_role"

    # 하드코딩 점검 2번(2026-11-21) — 예전엔 영문 키워드(`_SKILL_KEYWORD_RULES`)만
    # 있어, 이름표(`_KNOWN_ROLE_TUNING`)에 없는 새 역할은 한글 스킬명이 하나도 걸리지
    # 않아 전부 "표준"으로 떨어졌다. 역할명·스킬명 전체에서 한글·영문 키워드를
    # 세어 가장 많이 걸린 tier로 고른다(첫 매칭 하나로 정하지 않는다).
    votes = _tier_votes(role_name, skill_tags)
    if votes:
        tier = max(votes, key=lambda t: (votes[t], _TIER_TIEBREAK.index(t)))
        return tier, dict(_TIER_DEFAULT_TUNING[tier]), "skill_heuristic"

    # 구현 계열 키워드(implementation/workflow/checklist 등)나 아무 매칭도
    # 없는 경우 — 보수적으로 "정밀하되 빠르게"를 기본값으로 둔다(이 플랫폼의
    # Agent 대다수가 결국 구현/산출물 작성 역할이라는 실제 분포를 반영).
    for tag in skill_tags:
        tag_lower = tag.lower()
        if any(
            k in tag_lower
            for k in ("implementation", "workflow", "checklist", "commit")
        ):
            return (
                TIER_PRECISION_FAST,
                dict(_TIER_DEFAULT_TUNING[TIER_PRECISION_FAST]),
                "skill_heuristic",
            )

    return TIER_STANDARD, dict(_TIER_DEFAULT_TUNING[TIER_STANDARD]), "default_fallback"


_CAPABILITY_PREMIUM = "premium"
_CAPABILITY_BALANCED = "balanced"
_CAPABILITY_ECONOMY = "economy"

_TIER_MODEL_PREFERENCE: dict[str, list[str]] = {
    TIER_DEEP_REASONING: [
        _CAPABILITY_PREMIUM,
        _CAPABILITY_BALANCED,
        _CAPABILITY_ECONOMY,
    ],
    TIER_PRECISION_FAST: [
        _CAPABILITY_BALANCED,
        _CAPABILITY_PREMIUM,
        _CAPABILITY_ECONOMY,
    ],
    TIER_STANDARD: [_CAPABILITY_ECONOMY, _CAPABILITY_BALANCED, _CAPABILITY_PREMIUM],
}

# precision_fast(구현/리뷰) 역할은 비용 등급이 같을 때 "code" 태그가 붙은
# 모델(예: qwen3-coder)을 선호한다 — 등급만 보면 같은 ollama 모델들이
# 전부 동일하므로, 이게 없으면 이름의 알파벳 순서로만 동점이 깨져(의미
# 없는 우연) 코드 특화 모델을 오히려 밀어낼 수 있다(실측으로 발견 —
# 실제 hair.db에서 "개발자"(qwen3-coder 사용 중)가 아무 관련 없는
# "ornith-1.5:35b"로 바뀌는 추천이 나왔었다).
_TIER_PREFERRED_TAG: dict[str, str] = {TIER_PRECISION_FAST: "code"}


def _pattern_sort_key(
    provider: str, name: str, tier: str, preference: list[str]
) -> tuple[int, int]:
    """이름 패턴 추정(`model_capability()`) 기반 랭킹 — admin이 이 tier에
    명시적으로 매핑한 모델이 하나도 없을 때의 폴백 전용(기존 동작)."""
    cap, tags = model_capability(provider, name)
    primary = preference.index(cap) if cap in preference else len(preference)
    preferred_tag = _TIER_PREFERRED_TAG.get(tier)
    secondary = 1 if (preferred_tag and preferred_tag not in tags) else 0
    return primary, secondary


def model_capability(provider: str, name: str) -> tuple[str, set[str]]:
    """모델 이름 패턴으로 비용/성능 등급을 추정한다 — model_registry에
    실제 메타데이터가 전혀 없어(provider+name+is_active뿐) 규칙 기반 추정이
    최선이다. 실제 벤치마크 데이터가 아니라는 점을 추천 rationale에 항상
    명시한다."""
    n = name.lower()
    tags: set[str] = set()
    if "coder" in n or "code" in n:
        tags.add("code")

    if provider == "anthropic":
        if "opus" in n:
            return _CAPABILITY_PREMIUM, tags
        if "haiku" in n:
            return _CAPABILITY_ECONOMY, tags
        return _CAPABILITY_BALANCED, tags  # sonnet 등

    if provider == "openai":
        if (
            re.search(r"\bo[134]\b", n)
            or "gpt-4.1" in n
            or ("gpt-4o" in n and "mini" not in n)
        ):
            return _CAPABILITY_PREMIUM, tags
        if "mini" in n or "nano" in n or "3.5" in n:
            return _CAPABILITY_ECONOMY, tags
        return _CAPABILITY_BALANCED, tags

    # ollama(로컬) — API 비용은 없지만 일반적으로 프론티어 클라우드 모델보다
    # 약하다고 보수적으로 가정한다.
    return _CAPABILITY_ECONOMY, tags


def _provider_available(provider: str) -> bool:
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return True  # ollama — API 키 개념 없음


# ---- 성능 테스트 결과 반영(2026-11-27, "LLM 설정 최적화에 개선된 모델 추천 결과를 반영 — 도구 호출 안 되는 모델 등") ----
# 모델 레지스트리의 성능 테스트(`model_benchmark.py`)가 모델마다 능력(도구 호출·수치 보존·한국어·생각 끄기)과 단계 1회 예상
# 시간을 남긴다. 추천할 때 Agent에게 필요한 것과 맞춰 본다.
# - **제외(hard)**: Agent가 도구를 쓰는데(스킬 연결 도구·직접 연결 도구·파이프라인 단계의 필수 도구) 모델이 도구 호출을
#   못 한다(능력 점검 실패, 또는 Ollama 모델 정보에 tools 없음). 그 모델로는 단계가 매번 실패한다(필수 도구 미호출).
# - **감점(soft)**: 수치 보존·한국어 점검 실패, 추론을 끄는 Agent인데 생각 끄기를 무시하는 모델.
# - 성능 테스트를 하지 않은 모델은 판단하지 않는다(표시만 "능력 점검 전").


def agent_needs(conn: sqlite3.Connection, agent_row, tuning: dict | None = None) -> dict:
    """Agent가 실행에 필요로 하는 것 — 쓰는 도구 이름과 추론(생각) 끔 여부."""
    skills = set(json.loads(agent_row["skill_tags"] or "[]"))
    tool_ids: set[int] = set()
    for row in conn.execute("SELECT name, default_tool_pool_ids FROM skill_pool WHERE augmentation_mode = 'tool'"):
        if row["name"] in skills:
            tool_ids.update(json.loads(row["default_tool_pool_ids"] or "[]"))
    tool_ids.update(r[0] for r in conn.execute("SELECT tool_pool_id FROM agent_tool_links WHERE agent_id = ?", (agent_row["id"],)))
    names = {r[0] for r in conn.execute(
        f"SELECT name FROM tool_pool WHERE is_active = 1 AND id IN ({','.join('?' * len(tool_ids)) or 'NULL'})", tuple(tool_ids))}
    stage_required: set[str] = set()
    for r in conn.execute(
        "SELECT ps.required_tools FROM pipeline_stages ps JOIN pipeline_stage_agents psa ON psa.stage_id = ps.id "
        "WHERE psa.role_name = ? AND ps.required_tools IS NOT NULL AND ps.required_tools != '[]'", (agent_row["role_name"],)):
        try:
            stage_required.update(n for n in json.loads(r[0]) if isinstance(n, str))
        except ValueError:
            pass
    effective: dict = tuning if tuning is not None else json.loads(agent_row["tuning_config"] or "{}")
    return {"tools": sorted(names | stage_required), "required_tools": sorted(stage_required),
            "reasoning_off": effective.get("reasoning") is False}


def model_fitness(m: dict, needs: dict | None) -> dict:
    """모델 하나가 이 Agent에 맞는지 — 성능 테스트 결과로. `excluded`면 추천하지 않는다."""
    bench = m.get("latest_benchmark") or {}
    details = bench.get("details") or {}
    full = details if details.get("checks") else ((bench.get("latest_full") or {}).get("details") or {})
    checks = full.get("checks") or {}
    caps = details.get("capabilities") or full.get("capabilities")
    out: dict = {"checked": bool(checks), "excluded": False, "reason": "", "issues": [],
                 "estimated_stage_s": full.get("estimated_stage_s", details.get("estimated_stage_s")),
                 "recommended_tiers": full.get("recommended_tiers") or details.get("recommended_tiers")
                 or ([bench["recommended_tier"]] if bench.get("recommended_tier") else [])}
    needs = needs or {}
    tool_check = checks.get("tools")
    cannot_call_tools = (tool_check is not None and not tool_check.get("passed")) or (
        isinstance(caps, list) and m.get("provider") == "ollama" and "tools" not in caps)
    if needs.get("tools") and cannot_call_tools:
        out["excluded"] = True
        out["reason"] = "도구 호출 불가 — 이 Agent는 도구를 씀(" + ", ".join(needs["tools"][:3]) + \
            (" 외" if len(needs["tools"]) > 3 else "") + ")"
    if checks.get("numbers") and not checks["numbers"].get("passed"):
        out["issues"].append("입력 수치를 바꿈")
    if checks.get("korean") and not checks["korean"].get("passed"):
        out["issues"].append("한국어에 영어·한자 섞임")
    if needs.get("reasoning_off") and checks.get("think_off") and not checks["think_off"].get("passed"):
        out["issues"].append("추론 끔 설정을 무시함")
    return out


def recommend_model(
    conn: sqlite3.Connection,
    tier: str,
    current_provider: str | None,
    current_model: str | None,
    needs: dict | None = None,
) -> dict:
    active = [m for m in model_registry.list_models(conn) if m["is_active"]]
    if not active:
        return {
            "provider": current_provider,
            "model": current_model,
            "changed": False,
            "note": "모델 레지스트리에 등록된 활성 모델이 없어 추천할 수 없습니다 — "
            "먼저 '모델 레지스트리'에서 사용할 모델을 등록하세요.",
            "candidates": [],
        }

    preference = _TIER_MODEL_PREFERENCE[tier]

    # 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화 각
    # tier에 적용되는 모델을 매핑하는 기능을 제공") — admin이 이 tier에
    # 직접 매핑해둔 모델이 있으면(`optimization_tiers`에 이 tier가
    # 포함) 이름 패턴 추정을 거치지 않고 항상 최우선 후보가 된다. 매핑된
    # 모델이 하나도 없으면(기존 동작 100% 유지) 이름 패턴 추정+tier
    # 선호 등급(`_pattern_sort_key`)으로 폴백한다.
    fitness = {m["id"]: model_fitness(m, needs) for m in active}

    def rank_key(m: dict) -> tuple:
        fit = fitness[m["id"]]
        is_unmapped = 0 if tier in (m.get("optimization_tiers") or []) else 1
        # 하드코딩 점검 2번(2026-11-21) — 이름 패턴 추정보다 실제 측정값을 먼저
        # 본다: 성능 테스트가 이 tier를 추천한 모델을 앞에 둔다(admin 매핑 다음 순위).
        # 2026-11-27 — 새 성능 테스트는 추천 tier가 여러 개(`recommended_tiers`).
        benchmark_mismatch = 0 if tier in (fit["recommended_tiers"] or []) else 1
        pattern_primary, pattern_secondary = _pattern_sort_key(
            m["provider"], m["name"], tier, preference
        )
        # 이 Agent에 쓸 수 없는 모델(도구 호출 불가)은 맨 뒤, 점검에서 문제가 나온 모델은 같은 순위 안에서 뒤로.
        return (1 if fit["excluded"] else 0, is_unmapped, len(fit["issues"]), benchmark_mismatch,
                pattern_primary, pattern_secondary)

    # 사용자 요청(2026-10-26, "모델 추천의 경우 모델 레지스트리에 등록된
    # 모델을 선택할 수 있도록 해줘") — "가장 적합한 모델 1개"만이 아니라
    # API 키가 있는 provider의 활성 레지스트리 모델 전체를 이 tier
    # 기준으로 순위 매겨 후보 목록으로 같이 반환한다 — admin이 1위를
    # 그대로 받거나 2위/3위 등 다른 등록 모델을 직접 골라 적용할 수
    # 있게 한다(등록 안 된 모델은 후보에 아예 나타나지 않는다 — 이
    # 함수가 보는 소스가 model_registry뿐이라 애초에 레지스트리 밖
    # 모델은 추천 대상이 될 수 없다).
    candidates = sorted(
        (m for m in active if _provider_available(m["provider"])),
        key=lambda m: (
            rank_key(m),
            m["provider"] != current_provider,
            m["provider"],
            m["name"],
        ),
    )
    candidate_list = [
        {
            "provider": m["provider"],
            "model": m["name"],
            "tier_mapped": tier in (m.get("optimization_tiers") or []),
            "excluded": fitness[m["id"]]["excluded"],
            "excluded_reason": fitness[m["id"]]["reason"],
            "issues": fitness[m["id"]]["issues"],
            "checked": fitness[m["id"]]["checked"],
            "estimated_stage_s": fitness[m["id"]]["estimated_stage_s"],
        }
        for m in candidates
    ]
    current_fit = next((c for c in candidate_list if c["provider"] == current_provider and c["model"] == current_model), None)
    usable = [c for c in candidate_list if not c["excluded"]]
    if candidate_list and not usable:
        return {
            "provider": current_provider,
            "model": current_model,
            "changed": False,
            "note": "등록된 활성 모델이 모두 이 Agent에 맞지 않습니다(" + candidate_list[0]["excluded_reason"] +
            ") — 도구 호출이 되는 모델을 모델 레지스트리에 등록하고 전체 점검을 돌리세요.",
            "candidates": candidate_list,
            "current_fitness": current_fit,
        }

    if current_provider and current_model and candidates:
        top = candidates[0]
        if top["provider"] == current_provider and top["name"] == current_model:
            return {
                "provider": current_provider,
                "model": current_model,
                "changed": False,
                "note": "현재 설정이 이미 이 역할의 특성에 적합합니다.",
                "candidates": candidate_list,
                "current_fitness": current_fit,
            }

    if not candidate_list:
        return {
            "provider": current_provider,
            "model": current_model,
            "changed": False,
            "note": "API 키가 설정된 provider 중 활성 모델을 찾지 못해 현재 설정을 유지합니다.",
            "candidates": [],
        }

    best = candidate_list[0]
    changed = best["provider"] != current_provider or best["model"] != current_model
    if not changed:
        note = "현재 설정을 유지합니다."
    elif current_fit and current_fit["excluded"]:
        note = f"현재 모델({current_model})은 이 Agent에 쓸 수 없어({current_fit['excluded_reason']}) 바꿔야 합니다."
    elif best["tier_mapped"]:
        note = f"관리자가 '{_TIER_LABELS[tier]}' tier에 직접 매핑한 모델로 전환을 추천합니다."
    elif best["checked"]:
        note = f"{tier} 역할 특성에 더 적합한 모델로 전환을 추천합니다(성능 테스트 결과 기준)."
    else:
        note = f"{tier} 역할 특성에 더 적합한 모델로 전환을 추천합니다(이름 패턴 기반 추정)."
    if best["issues"]:
        note += " ⚠ 추천 모델 점검 결과: " + ", ".join(best["issues"]) + "."
    return {
        "provider": best["provider"],
        "model": best["model"],
        "changed": changed,
        "note": note,
        "candidates": candidate_list,
        "current_fitness": current_fit,
    }


_TIER_LABELS = {
    TIER_DEEP_REASONING: "심층 추론(기획/분석/설계)",
    TIER_PRECISION_FAST: "정밀·신속(구현/리뷰/QA — reasoning 오버헤드 회피)",
    TIER_STANDARD: "표준(작성/조정 — 비용 절감 우선)",
}
_SOURCE_LABELS = {
    "known_role": "이 플랫폼에서 실측으로 이미 튜닝된 알려진 역할",
    "skill_heuristic": "스킬 태그 키워드 기반 추정",
    "default_fallback": "역할을 식별하지 못해 보수적 기본값 적용",
}


def _diff_tuning(current: dict, recommended: dict) -> dict:
    keys = set(current) | set(recommended)
    return {
        k: {"current": current.get(k), "recommended": recommended.get(k)}
        for k in keys
        if current.get(k) != recommended.get(k)
    }


def recommend_for_agent(conn: sqlite3.Connection, agent_id: int) -> dict:
    row = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise ValueError("agent_not_found")

    role_name = row["role_name"]
    skill_tags = json.loads(row["skill_tags"])
    current_tuning = json.loads(row["tuning_config"])

    tier, recommended_tuning, source = classify_role(role_name, skill_tags)
    # 추론 끔 여부는 적용될 설정(현재 값 위에 추천 값)으로 본다.
    needs = agent_needs(conn, row, {**current_tuning, **recommended_tuning})
    model_rec = recommend_model(conn, tier, row["llm_provider"], row["llm_model"], needs)

    tuning_diff = _diff_tuning(current_tuning, recommended_tuning)
    changed = bool(tuning_diff) or model_rec["changed"]

    checked = any(c.get("checked") for c in model_rec["candidates"])
    rationale = (
        f"'{role_name}' 역할 분류: {_TIER_LABELS[tier]} ({_SOURCE_LABELS[source]}). "
        f"{model_rec['note']}"
        + (f" 이 Agent가 쓰는 도구: {', '.join(needs['tools'])}." if needs["tools"] else "")
        + ("" if checked else " ⚠ 성능 테스트(전체 점검)를 한 모델이 없어 모델명 패턴으로 추정했습니다.")
    )
    current_fit = model_rec.get("current_fitness")
    if current_fit and current_fit["excluded"]:
        changed = True  # 쓸 수 없는 현재 모델은 "변경 필요"

    return {
        "agent_id": agent_id,
        "role_name": role_name,
        "tier": tier,
        "classification_source": source,
        "current": {
            "provider": row["llm_provider"],
            "model": row["llm_model"],
            "tuning_config": current_tuning,
        },
        "recommended": {
            "provider": model_rec["provider"],
            "model": model_rec["model"],
            "tuning_config": recommended_tuning,
            "candidates": model_rec["candidates"],
        },
        "tuning_diff": tuning_diff,
        "changed": changed,
        "rationale": rationale,
        "needs": needs,
        "current_fitness": current_fit,
    }


def recommend_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM agent_pool WHERE status != 'deprecated' ORDER BY id"
    ).fetchall()
    return [recommend_for_agent(conn, r["id"]) for r in rows]


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def refine_with_llm(conn: sqlite3.Connection, agent_id: int) -> dict:
    """규칙 기반 추천을 LLM에게 보여주고 보정받는다(실제 비용 발생 —
    명시적으로 호출됐을 때만 실행). LLM이 레지스트리에 없는 모델을
    환각하거나 JSON을 깨뜨리면 전부 규칙 기반 결과로 안전하게 되돌아간다
    (절대 사용할 수 없는 모델을 추천하지 않는다)."""
    rule_based = recommend_for_agent(conn, agent_id)

    row = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    skill_tags = json.loads(row["skill_tags"]) if row else []
    skill_descriptions = []
    if skill_tags:
        placeholders = ",".join("?" for _ in skill_tags)
        for s in conn.execute(
            f"SELECT name, description FROM skill_pool WHERE name IN ({placeholders})",
            skill_tags,
        ).fetchall():
            skill_descriptions.append(f"- {s['name']}: {s['description']}")

    # 이 Agent에 쓸 수 없는 모델(도구 호출 불가 등, 2026-11-27)은 AI에게도 보여 주지 않는다.
    excluded = {(c["provider"], c["model"]) for c in rule_based["recommended"]["candidates"] if c.get("excluded")}
    active_by_provider: dict[str, list[str]] = {}
    for m in model_registry.list_models(conn):
        if m["is_active"] and (m["provider"], m["name"]) not in excluded:
            active_by_provider.setdefault(m["provider"], []).append(m["name"])

    system = (
        "당신은 AI 에이전트 플랫폼의 LLM 설정 최적화 보좌관입니다. 규칙 기반 "
        "1차 추천을 검토해 필요하면 보정하세요. 반드시 JSON 객체 하나만 "
        '출력하세요: {"provider": ..., "model": ..., "tuning_config": '
        '{...}, "rationale": "..."}. provider/model은 아래 \'사용 가능한 '
        "활성 모델' 목록에 실제로 있는 값만 쓸 수 있습니다 — 목록에 없는 "
        "모델은 절대 추천하지 마세요. tuning_config는 다음 키만 선택적으로 "
        "포함하세요: max_iter, max_rpm, max_execution_time, max_retry_limit, "
        "reasoning, max_reasoning_attempts, allow_code_execution, "
        "respect_context_window, temperature."
    )
    prompt = (
        f"역할명: {rule_based['role_name']}\n"
        f"보유 스킬:\n" + ("\n".join(skill_descriptions) or "(없음)") + "\n\n"
        f"규칙 기반 1차 추천: {json.dumps(rule_based['recommended'], ensure_ascii=False)}\n"
        f"분류 근거: {rule_based['rationale']}\n\n"
        f"사용 가능한 활성 모델(provider별): {json.dumps(active_by_provider, ensure_ascii=False)}\n\n"
        "이 역할의 목표·기능·입출력 특성을 고려해 위 1차 추천을 그대로 쓸지, "
        "보정할지 JSON으로만 답하세요."
    )

    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
        raw = llm.call_llm(provider, model, system, prompt)
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError("no_json_block")
        parsed = json.loads(match.group(0))

        llm_provider = parsed["provider"]
        llm_model = parsed["model"]
        if llm_model not in active_by_provider.get(llm_provider, []):
            raise ValueError(f"model_not_active:{llm_provider}:{llm_model}")

        tuning = AgentTuningConfig(**(parsed.get("tuning_config") or {})).model_dump(
            exclude_none=True
        )
    except Exception as exc:  # noqa: BLE001
        # 어떤 이유든 실패하면 안전하게 규칙 기반으로 되돌아간다.
        return {
            **rule_based,
            "source": "rule_based_fallback",
            "llm_error": str(exc),
        }

    return {
        **rule_based,
        "source": "llm_refined",
        "rule_based": rule_based["recommended"],
        "llm_refined": {
            "provider": llm_provider,
            "model": llm_model,
            "tuning_config": tuning,
        },
        "llm_rationale": parsed.get("rationale", ""),
    }
