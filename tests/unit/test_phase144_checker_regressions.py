"""키워드 점검기 회귀 사례(2026-11-26, 개선 계획 2-4) — 채용 공고 점검기·계약 조항 점검기.

인사 실측에서 "기본적인"이 출신 지역 "본적"으로 잡혔다. 실제 공고·계약서에 나올 문장으로 **잡아야 할 것**(차별 소지·
위험 조항)과 **잡으면 안 되는 것**(복리후생 안내·부정문·다른 뜻의 낱말)을 사례로 쌓아, 점검기를 고칠 때 되돌아가지
않게 한다. 사례를 더할 때는 출처(실측·검토 의견)를 주석으로 남긴다."""

from __future__ import annotations

import json
import types

import pytest

from hair_platform import domain_packs


def _tool(source):
    mod = types.ModuleType("checker")
    eval(compile(source, "<tool>", "exec"), mod.__dict__)  # noqa: S307 — 저장소 안의 고정 소스(플랫폼도 같은 방식)
    return mod.tool_fn


posting = _tool(domain_packs.JOB_POSTING_CHECK_SOURCE)
contract = _tool(domain_packs.CONTRACT_CHECK_SOURCE)


def _posting_flags(line: str) -> list[str]:
    return [f["확인 사항"] for f in json.loads(posting(line))["주의_문구"]]


def _contract_flags(line: str) -> list[str]:
    return [f["확인 사항"] for f in json.loads(contract(line))["주의_문구"]]


@pytest.mark.parametrize("line, law", [
    ("여성 지원자 우대", "남녀고용평등법"),
    ("남자만 지원 가능합니다", "남녀고용평등법"),
    ("성별: 남", "남녀고용평등법"),
    ("만 35세 이하", "연령차별금지법"),
    ("20~30대 환영", "연령차별금지법"),
    ("나이 제한: 40세 미만", "연령차별금지법"),
    ("키 170cm 이상", "제4조의3"),
    ("용모 단정하신 분", "제4조의3"),
    ("기혼자 우대", "남녀고용평등법"),
    ("결혼 계획이 없는 분", "남녀고용평등법"),
    ("임신 계획이 없는 분", "남녀고용평등법"),
    ("부모님 직업을 기재해 주세요", "제4조의3"),  # "부모님" — 존칭이 붙어도
    ("증명사진 부착 필수", "직무와 무관한 정보"),
    ("종교: ", "직무와 무관한 정보"),
    ("고향이 경상도인 분 우대", "제4조의3"),
    ("본적지 기재", "제4조의3"),
])
def test_posting_checker_catches_discriminatory_phrases(line, law):
    flags = _posting_flags(line)
    assert flags and any(law in f for f in flags), (line, flags)


@pytest.mark.parametrize("line", [
    "기본적인 협업 태도를 봅니다",            # 2026-11-26 인사 실측 — "본적" 오탐
    "성별·나이·학력 무관하게 채용합니다",
    "사진 편집 툴(Photoshop) 경험 우대",       # 디자인 직무
    "키보드 단축키 1000개 이상 익숙한 분",     # "단축키 100…" — 키·신장 아님
    "정당한 사유 없이 결근하지 않는 분",       # "정당" — 정치 정당 아님
    "임신·출산 휴가와 육아휴직을 지원합니다",  # 복리후생 안내
    "결혼 축하금·자녀 학자금 지원",            # 복리후생 안내
    "고향 방문 휴가 제공",                     # 복리후생 안내
    "기혼자 사택 제공",                        # 복리후생 안내(조건이 아님)
    "30대 고객 대상 마케팅 경험",              # 고객층 — 지원자 연령 조건 아님
    "만 18세 이상(법정 근로 가능 연령)",       # 법정 연령 요건
    "종교 단체 대상 서비스 기획 경험",          # 직무 내용
])
def test_posting_checker_does_not_flag_benign_phrases(line):
    assert _posting_flags(line) == [], line


@pytest.mark.parametrize("line, reason", [
    ("을은 갑의 사전 통지 없이 계약을 해지할 수 있다.", "통지"),
    ("본 계약은 1년 단위로 자동 갱신된다.", "자동 갱신"),
    ("을의 손해배상 책임은 무제한으로 한다.", "무제한"),
    ("갑은 일방적으로 대금을 변경할 수 있다.", "한쪽만"),
])
def test_contract_checker_catches_risky_clauses(line, reason):
    flags = _contract_flags(line)
    assert flags and any(reason in f for f in flags), (line, flags)


@pytest.mark.parametrize("line", [
    "본 계약은 자동으로 갱신되지 않으며, 연장은 서면 합의로 한다.",  # 부정문
    "어느 당사자도 일방적으로 계약 내용을 변경할 수 없다.",          # 금지 조항(오히려 좋은 조항)
    "손해배상 책임은 계약 금액을 한도로 하며 무제한으로 하지 않는다.",  # 부정문
])
def test_contract_checker_ignores_negated_risks(line):
    assert _contract_flags(line) == [], line
