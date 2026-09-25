# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼.
[AgentForge](https://github.com/) 실습서 저장소의 자매 프로젝트로, AgentForge가 검증한
파이프라인(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·Web UI 패턴)을
재사용하되 "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다.

**상태**: Phase 0 부트스트랩(빈 골격) — 설계·개발 시작 전 단계. 명세는
`docs/SPEC.md`(DRAFT, `spec_review` 승인 대기) 참고.

## 구조

```
src/hair_platform/   # 애플리케이션 코드(Phase 2 설계 이후 채워짐)
docs/SPEC.md         # 명세(DRAFT)
tests/unit/          # 오프라인 단위 테스트
```

## 로드맵

`docs/SPEC.md` §7(Autopilot 활용 계획) 참고. 다음 단계는 Phase 1 SPEC 확정(`spec_review`
승인)이며, 이후 Phase 2에서 AgentForge 컴포넌트(Spec Interpreter/Code Generator/
Verifier/Pool) 추출·공용패키지화와 신규 모듈(Human Pool·Assignment Engine·Approval
Workflow·Job Queue·Auth+RBAC·Audit Log) ADR을 진행한다.

## 라이선스

MIT
