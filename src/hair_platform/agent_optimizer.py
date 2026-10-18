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
    TIER_DEEP_REASONING: {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
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


def classify_role(role_name: str, skill_tags: list[str]) -> tuple[str, dict, str]:
    """(tier, 추천 tuning_config, 분류 근거) — 근거는 "known_role"/
    "skill_heuristic"/"default_fallback" 중 하나."""
    known = _KNOWN_ROLE_TUNING.get(role_name)
    if known is not None:
        return _tier_for_known_tuning(known), dict(known), "known_role"

    for tag in skill_tags:
        tag_lower = tag.lower()
        for keyword, tier in _SKILL_KEYWORD_RULES:
            if keyword in tag_lower:
                return tier, dict(_TIER_DEFAULT_TUNING[tier]), "skill_heuristic"

    # 구현 계열 키워드(implementation/workflow/checklist 등)나 아무 매칭도
    # 없는 경우 — 보수적으로 "정밀하되 빠르게"를 기본값으로 둔다(이 플랫폼의
    # Agent 대다수가 결국 구현/산출물 작성 역할이라는 실제 분포를 반영).
    for tag in skill_tags:
        tag_lower = tag.lower()
        if any(k in tag_lower for k in ("implementation", "workflow", "checklist", "commit")):
            return TIER_PRECISION_FAST, dict(_TIER_DEFAULT_TUNING[TIER_PRECISION_FAST]), "skill_heuristic"

    return TIER_STANDARD, dict(_TIER_DEFAULT_TUNING[TIER_STANDARD]), "default_fallback"


_CAPABILITY_PREMIUM = "premium"
_CAPABILITY_BALANCED = "balanced"
_CAPABILITY_ECONOMY = "economy"

_TIER_MODEL_PREFERENCE: dict[str, list[str]] = {
    TIER_DEEP_REASONING: [_CAPABILITY_PREMIUM, _CAPABILITY_BALANCED, _CAPABILITY_ECONOMY],
    TIER_PRECISION_FAST: [_CAPABILITY_BALANCED, _CAPABILITY_PREMIUM, _CAPABILITY_ECONOMY],
    TIER_STANDARD: [_CAPABILITY_ECONOMY, _CAPABILITY_BALANCED, _CAPABILITY_PREMIUM],
}

# precision_fast(구현/리뷰) 역할은 비용 등급이 같을 때 "code" 태그가 붙은
# 모델(예: qwen3-coder)을 선호한다 — 등급만 보면 같은 ollama 모델들이
# 전부 동일하므로, 이게 없으면 이름의 알파벳 순서로만 동점이 깨져(의미
# 없는 우연) 코드 특화 모델을 오히려 밀어낼 수 있다(실측으로 발견 —
# 실제 hair.db에서 "개발자"(qwen3-coder 사용 중)가 아무 관련 없는
# "ornith-1.5:35b"로 바뀌는 추천이 나왔었다).
_TIER_PREFERRED_TAG: dict[str, str] = {TIER_PRECISION_FAST: "code"}


def _capability_sort_key(provider: str, name: str, tier: str, preference: list[str]) -> tuple[int, int]:
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
        if re.search(r"\bo[134]\b", n) or "gpt-4.1" in n or ("gpt-4o" in n and "mini" not in n):
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


def recommend_model(
    conn: sqlite3.Connection,
    tier: str,
    current_provider: str | None,
    current_model: str | None,
) -> dict:
    active = [m for m in model_registry.list_models(conn) if m["is_active"]]
    if not active:
        return {
            "provider": current_provider,
            "model": current_model,
            "changed": False,
            "note": "모델 레지스트리에 등록된 활성 모델이 없어 추천할 수 없습니다 — "
            "먼저 '모델 레지스트리'에서 사용할 모델을 등록하세요.",
        }

    preference = _TIER_MODEL_PREFERENCE[tier]

    def rank_key(provider: str, name: str) -> tuple[int, int]:
        return _capability_sort_key(provider, name, tier, preference)

    if current_provider and current_model:
        current_active = any(
            m["provider"] == current_provider and m["name"] == current_model for m in active
        )
        if current_active and rank_key(current_provider, current_model) == (0, 0):
            return {
                "provider": current_provider,
                "model": current_model,
                "changed": False,
                "note": "현재 설정이 이미 이 역할의 특성에 적합합니다.",
            }

    provider_order = [current_provider] if current_provider else []
    provider_order += [p for p in ("anthropic", "openai", "ollama") if p != current_provider]

    best: dict | None = None
    best_key = (len(preference) + 1, 1)
    for provider in provider_order:
        if not _provider_available(provider):
            continue
        candidates = sorted(
            (m for m in active if m["provider"] == provider), key=lambda m: m["name"]
        )
        for m in candidates:
            key = rank_key(provider, m["name"])
            if key < best_key:
                best = {"provider": provider, "model": m["name"]}
                best_key = key
        if best is not None and best_key == (0, 0):
            break  # 이 provider에서 최선(비용등급+태그 모두 만족)을 찾았으면 더 볼 필요 없음

    if best is None:
        return {
            "provider": current_provider,
            "model": current_model,
            "changed": False,
            "note": "API 키가 설정된 provider 중 활성 모델을 찾지 못해 현재 설정을 유지합니다.",
        }

    changed = best["provider"] != current_provider or best["model"] != current_model
    return {
        "provider": best["provider"],
        "model": best["model"],
        "changed": changed,
        "note": (
            "현재 설정을 유지합니다." if not changed
            else f"{tier} 역할 특성에 더 적합한 모델로 전환을 추천합니다(이름 패턴 기반 추정)."
        ),
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
    model_rec = recommend_model(conn, tier, row["llm_provider"], row["llm_model"])

    tuning_diff = _diff_tuning(current_tuning, recommended_tuning)
    changed = bool(tuning_diff) or model_rec["changed"]

    rationale = (
        f"'{role_name}' 역할 분류: {_TIER_LABELS[tier]} ({_SOURCE_LABELS[source]}). "
        f"{model_rec['note']} ⚠ 모델 등급 추정은 모델명 패턴 기반이며 실제 벤치마크 "
        "데이터가 아닙니다."
    )

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
        },
        "tuning_diff": tuning_diff,
        "changed": changed,
        "rationale": rationale,
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

    row = conn.execute("SELECT skill_tags FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    skill_tags = json.loads(row["skill_tags"]) if row else []
    skill_descriptions = []
    if skill_tags:
        placeholders = ",".join("?" for _ in skill_tags)
        for s in conn.execute(
            f"SELECT name, description FROM skill_pool WHERE name IN ({placeholders})",
            skill_tags,
        ).fetchall():
            skill_descriptions.append(f"- {s['name']}: {s['description']}")

    active_by_provider: dict[str, list[str]] = {}
    for m in model_registry.list_models(conn):
        if m["is_active"]:
            active_by_provider.setdefault(m["provider"], []).append(m["name"])

    system = (
        "당신은 AI 에이전트 플랫폼의 LLM 설정 최적화 보좌관입니다. 규칙 기반 "
        "1차 추천을 검토해 필요하면 보정하세요. 반드시 JSON 객체 하나만 "
        "출력하세요: {\"provider\": ..., \"model\": ..., \"tuning_config\": "
        "{...}, \"rationale\": \"...\"}. provider/model은 아래 '사용 가능한 "
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
        provider, model = llm.resolve_provider_and_model(None, None)
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
    except Exception as exc:  # noqa: BLE001 — 어떤 이유든 실패하면 안전하게 규칙 기반으로.
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
