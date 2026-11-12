"""Web UI 셸. AgentForge의 웹 UI 패턴(f-string+html.escape, Jinja2 미사용,
빌드 스텝 없는 순수 HTML/CSS/JS)을 그대로 계승한다.

코드 재점검(2026-09-26, R4)에서 발견: 명세가 요구사항 전부에 "화면"·"버튼"·
"Web UI 폼"을 명시적으로 요구했는데도, v0-v4 개발과 3라운드의 코드 재점검을
거치는 동안 HTML/CSS/JS가 단 한 줄도 없었다 — 지금까지 만든 건 잘 테스트된
JSON REST API뿐, 브라우저에서 조작 가능한 화면은 전무했다("전 기능을 인증된
Web UI에서 조작 가능하게 하는 것이 AgentForge와의 핵심 차이다"라는 원칙).

이 모듈은 그 화면을 채운다 — 단일 HTML 페이지 + 바닐라 JS(프레임워크·빌드
스텝 없음)로, 이미 구현·검증된 JSON API(`app.py`)를 그대로 호출한다. 새 비즈니스
로직은 없다 — 순수 프레젠테이션 레이어다.
"""

from __future__ import annotations

import html
import json

INDEX_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HAIR-Platform</title>
<style>
  /* 페이지 전체가 라이트 팔레트로 하드코딩돼 있고 다크모드 대응 CSS가
     따로 없다 — "light dark"를 두면 OS가 다크모드일 때 브라우저가 버튼
     등 폼 컨트롤에 네이티브 다크 스타일(밝은 글자색)을 자동 적용하는데,
     배경은 이 페이지의 라이트 CSS(흰색)가 그대로 남아 글자가 거의 안
     보이는 상태가 된다(실사용 중 발견). 이 페이지는 라이트 전용임을
     명시한다. */
  /* 색상 토큰화·상태 배지 통일(2026-10-12, "UI 개선 잔여 작업
     진행해줘") — 반복되는 색을 변수로 뽑았다. primary/accent/admin/
     border류는 기존 값 그대로라 화면이 안 바뀐다. 상태색(success/
     warn/danger)만 예외다 — .gate-badge와 .msg가 지금까지 같은
     "성공/경고"인데도 서로 다른 초록/주황 톤을 썼어서, 통일하면서
     .msg 쪽 톤을 .gate-badge 쪽(이미 쓰던 값)에 맞췄다 — 아주 미세한
     색조 변화가 "의도된" 결과다(두 군데가 이제 실제로 같은 색이
     된다는 게 이번 통일의 목적 그 자체). 전체 색상을 다
     뽑진 않았다 — 채팅 버블·코드 하이라이팅처럼 한 자리에서만 쓰는
     색은 이번 1차 작업 범위 밖으로 남긴다(로드맵 단계적 접근). */
  :root {
    color-scheme: light;
    --color-bg: #f5f6f8;
    --color-surface: #fff;
    --color-text: #1b1f24;
    --color-muted: #777;
    --color-border: #e2e4e8;
    --color-border-strong: #ccc;
    --color-primary: #14213d;
    --color-accent: #fca311;
    --color-admin: #a15c00;
    --color-admin-bg: #fff7ec;
    --color-admin-border: #f0c98a;
    /* I3 — 지금까지 .gate-badge/.msg가 각자 따로 고르던 초록/주황/빨강을
       공통 토큰 3종으로 통일한다(success/warn/danger — "info"는 지금
       쓰는 곳이 없어 아직 안 만든다, 쓰지 않는 토큰을 미리 만들지
       않는다는 이 프로젝트 관례와 동일). */
    --status-success-bg: #dcfce7;
    --status-success-fg: #166534;
    --status-warn-bg: #fef3c7;
    --status-warn-fg: #92400e;
    --status-danger-bg: #fde2e2;
    --status-danger-fg: #7a1212;
    /* 사용자 보고(2026-10-19, "B, E Gate가 표시 안 됨 — 평가되지 않은
       Gate는 미평가로 표시 필요") — 통과(success)/실패(warn)만 있고
       "애초에 측정 대상이 아님(not_measured)"을 나타낼 중립 톤이 없어,
       측정 안 된 Gate를 아예 숨겨버렸었다. 세 번째 중립 토큰을 추가한다. */
    --status-muted-bg: #e5e7eb;
    --status-muted-fg: #4b5563;
  }
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; background: var(--color-bg); color: var(--color-text); }
  header { background: var(--color-primary); color: #fff; padding: 0.75rem 1rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  header h1 { font-size: 1.1rem; margin: 0; }
  header .spacer { flex: 1; }
  header button { background: var(--color-accent); border: none; padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer; }
  nav { background: var(--color-surface); border-bottom: 1px solid #ddd; }
  .nav-group { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem; padding: 0.4rem 0.5rem; }
  .nav-group + .nav-group { border-top: 1px dashed var(--color-border); }
  .nav-group.admin-group { background: var(--color-admin-bg); }
  .nav-label { font-size: 0.72rem; font-weight: 600; color: #888; margin-right: 0.4rem; min-width: 72px; }
  .nav-group.admin-group .nav-label { color: var(--color-admin); }
  nav button { background: none; border: 1px solid var(--color-border-strong); padding: 0.35rem 0.7rem; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }
  nav button.active { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
  .nav-group.admin-group button { border-color: var(--color-admin-border); }
  .nav-group.admin-group button.active { background: var(--color-admin); border-color: var(--color-admin); }
  .badge-adminonly { font-size: 0.65rem; background: var(--color-admin); color: #fff; border-radius: 3px; padding: 0 0.3rem; margin-left: 0.3rem; }
  .badge-inbox-count { font-size: 0.7rem; font-weight: 700; background: var(--status-danger-fg); color: #fff; border-radius: 10px; padding: 0.05rem 0.4rem; margin-left: 0.3rem; }
  /* 사용자 요청(2026-11-02, "설정 메뉴 배치를... 4개 그룹으로 조정") —
     "설정" 한 묶음을 4개 그룹(리소스/그라운딩/모델/운영 관리)으로
     나눈다. 그룹 간 구분은 기존 `.nav-group + .nav-group` 점선이
     그대로 해 준다 — 그룹 수만 1개에서 4개로 늘면 된다.
     한 그룹 안에서 특정 버튼만 한 번 더 떼어 보여줄 때는(예:
     "리소스 매칭/배정"을 나머지와 분리 — 아래 `.nav-divider`)
     세로선 하나로 충분해, 한때 쓰던 하위 소제목 묶음(`.nav-sublabel`/
     `.nav-subgroup`, "Agent 관리"라는 라벨을 달았던 것)은 2026-11-02
     "agent 관리 문구 삭제" 요청으로 걷어냈다. */
  /* 사용자 요청(2026-11-02, "'리소스 매칭/배정' 버튼은 가장 먼저
     위치 및 구분선 추가") — 그룹 맨 앞의 버튼 하나를 나머지와
     세로선으로 나눈다. **정정(스크린샷 — "선 모양이 서로 달라")**:
     처음엔 고정 `height`를 준 `background` 블록으로 만들었더니
     형제 요소(버튼 높이에 맞춰 자동으로 늘어남)보다 짧아 보여 선의
     모양이 달랐다 — `border-left` + `align-self: stretch`(행 높이에
     자동으로 맞춰짐)로 통일해 모양을 맞춘다.
     **추가(2026-11-02, 스크린샷 — "사람 인력 관리" 뒤, "Crew 빌더"
     뒤에도 구분선 추가)** — 같은 `.nav-divider`를 `SETTINGS_GROUPS`의
     "리소스 관리" `entries`에 두 번 더 넣어, 리소스 매칭/배정 | 사람
     인력 관리 | (AI 에이전트 생성·관리/Crew 빌더) | 팀 관리 4묶음으로
     나눈다 — CSS는 안 바뀌고 `buildNav()`가 그대로 반복 렌더링한다. */
  .nav-divider { align-self: stretch; width: 0; border-left: 2px solid var(--color-border); margin: 0 0.3rem; }
  .nav-group.admin-group .nav-divider { border-left-color: var(--color-admin-border); }
  /* 사용자 요청(2026-11-02, "그라운딩 관리, 모델관리, 운영관리 3메뉴
     영역은 숨겨져 있다가 펼쳐 보는 방식으로") — `.nav-group`은 이미
     `display: flex`라 네이티브 `<details>`에 그대로 얹어도 펼친 상태의
     레이아웃(라벨+버튼 한 줄)은 안 바뀐다 — 접힌 상태에서 `<summary>`
     말고 나머지 자식을 안 그리는 건 브라우저가 알아서 한다. 여기선
     `<summary>`만 클릭 가능해 보이게 손본다. */
  .nav-group.nav-collapsible { cursor: default; }
  .nav-group.nav-collapsible > summary.nav-label { cursor: pointer; user-select: none; }
  .nav-group.nav-collapsible > summary.nav-label:hover { color: var(--color-admin); }
  /* 사용자 보고(2026-11-02, 스크린샷 — "그라운딩 관리, 모델관리,
     운영관리 메뉴 영역의 각 버튼 배치가 리소스 관리에서처럼 버튼사이에
     여백을 두도록") + "각 메뉴 영역 첫번째 버튼의 세로 정렬이 되도록
     (그라운딩 관리가 리소스 매칭/배정, LLM설정 최적화 버튼과 줄이 안
     맞음)" — 원인은 네이티브 `<summary>`가 브라우저 기본 스타일로
     라벨 텍스트 앞에 세모 마커(disclosure marker)를 그리는데, "리소스
     관리"는 `<details>`가 아니라 평범한 `<div>`+`<span class="nav-
     label">`라 그 마커가 없다 — 그래서 "그라운딩 관리"/"모델 관리"/
     "운영 관리"(전부 `<details>`)만 라벨 폭이 마커만큼 더 넓어져
     첫 버튼이 오른쪽으로 밀리고(세로 정렬 어긋남), 동시에 버튼 사이
     여백도 `gap`이 그 폭 계산에 밀려 상대적으로 좁아 보였다. 마커를
     완전히 지워 `summary.nav-label`을 `span.nav-label`과 같은 폭으로
     맞춘다(펼침/닫힘 자체는 클릭·hover 색 변화로 여전히 동작). */
  .nav-group.nav-collapsible > summary.nav-label { list-style: none; }
  .nav-group.nav-collapsible > summary.nav-label::-webkit-details-marker { display: none; }
  .nav-group.nav-collapsible > summary.nav-label::marker { content: ""; }
  /* 버튼 사이 여백을 `gap`에만 맡기지 않고 각 버튼에도 명시적으로
     줘서, 위 마커 제거 이후에도(또는 줄바꿈 등 다른 레이아웃 변화가
     있어도) "리소스 관리"와 똑같은 간격을 항상 보장한다. */
  .nav-group.nav-collapsible > button { margin: 0 0.25rem 0.25rem 0; }
  /* 사용자 보고(2026-10-18, 스크린샷 — 넓은 화면에서도 "이용 가이드"
     본문이 좁은 고정폭에 갇혀 오른쪽이 텅 비어 보임) — 기존엔
     `max-width: 1100px`로 **고정**돼 있어 화면이 아무리 넓어도 딱
     1100px만 썼다(docs/OVERVIEW.md "알려진 한계"에도 "고정 1100px
     레이아웃이 기본"이라고 명시돼 있던 의도된 설계였다). 뷰포트 폭에
     반응하도록 `width: min(94%, 1400px)`로 바꾼다 — 좁은 화면에서는
     뷰포트의 94%를 쓰고(기존 모바일 미디어쿼리의 "여백 최소화" 의도와
     같은 방향), 넓은 화면에서는 1400px까지 늘어나 공간을 더 쓰되(기존
     1100px보다 넉넉), 읽기 힘들 만큼 줄 길이가 길어지지 않도록 상한은
     그대로 둔다. */
  main { padding: 1rem; width: min(94%, 1400px); margin: 0 auto; box-sizing: border-box; }
  section.card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
  .deliverable-card { background: #f9fafb; border: 1px solid var(--color-border); border-radius: 6px; padding: 0.6rem 0.75rem; margin-bottom: 0.6rem; }
  .deliverable-card + .deliverable-card { margin-top: 0; }
  .deliverable-card.chat-human { margin-right: 12%; border-left: 3px solid #2563eb; }
  .deliverable-card.chat-agent { margin-left: 12%; border-left: 3px solid #7c3aed; background: #f5f3ff; }
  /* 사용자 요청(2026-10-19, "대화 초대자(자율 개발 크루, 기획서 작성자
     등)의 경우 대화 내용과 구분(예: 볼드체, 파란색 등) 해서 가독성을
     높여줘") — 작성자 줄이 지금까지 `.muted`(옅은 회색, 본문과 거의
     구분 안 됨) 하나로만 돼 있었다. 카드 왼쪽 테두리에 이미 쓰던
     색(사람=파랑 #2563eb, Agent/Team=보라 #7c3aed)을 작성자 "이름"
     자체에도 그대로 써서 일관성 있게 누가 말했는지 한눈에 띄게 한다. */
  .msg-author-human { font-weight: 700; color: #2563eb; }
  .msg-author-agent { font-weight: 700; color: #7c3aed; }
  /* 사용자 요청(2026-10-21, "Layer 4 — 대화/채팅 시각 다듬기") —
     이름 텍스트 색만으론 메시지가 길어지면 스캔하기 번거롭다 —
     이니셜 원형 아바타를 이름 앞에 덧붙인다. `.spinner`의 원 그리기
     기법(고정 width/height + border-radius:50%) 재사용, 색은 위
     사람/Agent 토큰과 동일. */
  .msg-avatar { display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%; font-size: 0.68rem; font-weight: 700;
    color: #fff; vertical-align: middle; margin-right: 0.35rem; flex-shrink: 0; }
  .msg-avatar.msg-avatar-human { background: #2563eb; }
  .msg-avatar.msg-avatar-agent { background: #7c3aed; }
  /* 사용자 보고(2026-10-19, 스크린샷 — "@자율 개발 크루 위 기획을
     구현해줘"처럼 멘션 대상과 실제 요청 문장이 구분 없이 붙어 보임) —
     본문 안의 "@대상" 텍스트를 받는 쪽 이름과 같은 색 pill로 감싸
     "누구를 불렀는지"와 "무슨 말을 했는지"가 한눈에 갈리게 한다. */
  .mention-tag { display: inline-block; font-weight: 600; padding: 0 0.35rem; border-radius: 4px; }
  .mention-tag.mention-agent { background: #ede9fe; color: #6d28d9; }
  .mention-tag.mention-human { background: #dbeafe; color: #1d4ed8; }
  /* 사용자 요청(2026-10-22, "호출된 사람,에이전트가 대화 내용과
     구분되도록 해줘 (볼드, 색구분 등) ... 입력창에서도 적용해줘") —
     대화 카드(.mention-tag, 위)는 이미 멘션을 pill로 감싸지만,
     타이핑 중인 input은 plain text라 그 방식(padding 있는 inline-block
     pill)을 그대로 못 쓴다 — input 위에 똑같은 텍스트를 그리는 투명
     오버레이(mirror) div를 겹쳐서, 실제 입력은 투명하게(캐럿만 보이게)
     하고 mirror가 보이는 텍스트를 담당한다. pill처럼 padding/배경을
     넣으면 그 구간만 글자 폭이 늘어나 mirror와 실제 input의 글자 위치가
     어긋나므로(그 뒤로 입력한 글자가 캐럿과 안 맞아 보임), 폭이 안
     바뀌는 font-weight+color만 쓴다(배경/패딩 없음).*/
  #conv-message-highlight { position: absolute; inset: 0; margin: 0; padding: 0.35rem; border: 1px solid transparent; border-radius: 4px; font-size: 0.85rem; font-family: inherit; line-height: normal; white-space: pre; overflow: hidden; box-sizing: border-box; pointer-events: none; background: #fff; z-index: 0; }
  #conv-message-highlight .mention-inline-agent { font-weight: 600; color: #6d28d9; }
  #conv-message-highlight .mention-inline-human { font-weight: 600; color: #1d4ed8; }
  #conv-message-input { position: relative; z-index: 1; background: transparent; color: transparent; caret-color: #111; }
  #conv-message-input::placeholder { color: #888; }
  .mention-dropdown { position: absolute; z-index: 20; top: 100%; left: 0; width: 100%; max-height: 10rem; overflow-y: auto; margin: 2px 0 0; padding: 0.2rem 0; list-style: none; background: #fff; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.12); }
  .mention-dropdown li { padding: 0.3rem 0.6rem; cursor: pointer; font-size: 0.85rem; }
  .mention-dropdown li:hover, .mention-dropdown li.active { background: #eef6ff; }
  /* 사용자 보고(2026-10-14, "진행중임을 알 수 있도록 UI를 개선해줘") —
     대화에서 Agent/Team 응답을 기다리는 동안 보여주는 인라인 표시. */
  .pending-indicator { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.75rem; margin-top: 0.5rem; background: #f5f7fa; border: 1px solid #e2e4e8; border-radius: 6px; color: #555; font-size: 0.9rem; }
  .spinner { width: 14px; height: 14px; border: 2px solid #c7d2e0; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.8s linear infinite; flex-shrink: 0; }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* 사용자 요청(2026-10-21, "Layer 2 — 실행 가시성 강화") — 지금까지
     "N/M 완료" 텍스트만 있던 세 군데(Crew 실행 이력/파이프라인
     타임라인/대화 진행 패널)에 시각적 진행 표시를 더한다. 새 폴링·
     상태추적 인프라는 추가하지 않고 이미 있는 데이터/클래스에
     CSS·마크업만 얹는다 — `.spinner`와 같은 타이밍·색 어휘를 그대로
     따른다(파란 강조 #2563eb, 연한 테두리 #c7d2e0). */
  .progress-bar { height: 6px; border-radius: 3px; background: var(--status-muted-bg); overflow: hidden; }
  .progress-bar-fill { height: 100%; background: #2563eb; transition: width 0.3s ease; }
  .progress-bar-fill.failed { background: var(--status-warn-fg); }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
  .job-status-badge.running { animation: pulse 1.4s ease-in-out infinite; }
  .pipeline-breadcrumb { display: flex; flex-wrap: wrap; align-items: center; gap: 0.15rem; margin: 0.3rem 0; }
  .pipeline-breadcrumb-step { white-space: nowrap; }
  .pipeline-breadcrumb-current { outline: 2px solid #2563eb; outline-offset: 1px; }
  .pipeline-breadcrumb-arrow { color: var(--color-muted); margin: 0 0.1rem; }
  /* 사용자 요청(2026-10-18, "어떤 에이전트가 어떤 동작을 하고 있는지
     알 수 있도록... 펼침/닫힘 UI로 표시") — 메시지 하나로 여러 Job이
     한꺼번에 트리거될 수 있어(복수 @멘션, Team은 멤버 전원을 한
     Job으로 묶어 실행) 그 목록을 펼침/닫힘 패널로 보여준다. CrewAI
     Crew(그룹) 실행은 동기 호출이라 "그룹 안에서 지금 누가 뭘 하는지"
     까지는 실시간으로 못 보여준다(이벤트 스트리밍 없음) — 있는
     그대로 "그룹 전체 실행 중/완료"로만 정직하게 표시한다.
     `_renderConversationPendingPanel()`/`_updateConversationJobStatus()`
     참고. */
  .pending-indicator-panel { margin-top: 0.5rem; background: #f5f7fa; border: 1px solid #e2e4e8; border-radius: 6px; padding: 0.4rem 0.75rem; font-size: 0.9rem; }
  .pending-indicator-panel summary { cursor: pointer; display: flex; align-items: center; gap: 0.5rem; color: #555; padding: 0.15rem 0; }
  .pending-indicator-panel summary::-webkit-details-marker { display: none; }
  /* 사용자 보고(2026-10-18, 스크린샷 — 실패 메시지 노출 시 깨져 보임) —
     실측 원인: 실패 배지(.job-status-badge)에 flex-shrink:0을 건 채로
     긴 에러 안내문 전체를 그 작은 알약(pill) 안에 통째로 넣어서, 줄바꿈
     없이 화면 밖으로 흘러넘쳤다. 배지는 "✗ 실패"처럼 짧게만 유지하고
     (flex-shrink:0 유지해도 안전), 자세한 에러 문구는 flex-basis:100%로
     다음 줄에 떨어뜨려 폭 안에서 자연스럽게 줄바꿈되게 한다. */
  .job-progress-list { list-style: none; margin: 0.4rem 0 0.1rem; padding: 0 0 0 1.4rem; }
  .job-progress-list li { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; padding: 0.15rem 0; }
  .job-status-badge { font-size: 0.8em; border-radius: 3px; padding: 0.05rem 0.4rem; flex-shrink: 0; white-space: nowrap; }
  .job-status-badge.running { background: #eef2ff; color: #4338ca; }
  .job-status-badge.succeeded { background: var(--status-success-bg); color: var(--status-success-fg); }
  .job-status-badge.failed { background: var(--status-warn-bg); color: var(--status-warn-fg); }
  .job-error-detail { flex-basis: 100%; font-size: 0.85em; color: var(--status-warn-fg); margin-left: 1.6rem; word-break: break-word; }
  .gate-badge { display: inline-block; padding: 0.05rem 0.4rem; border-radius: 3px; font-size: 0.85em; }
  .gate-badge.gate-ok { background: var(--status-success-bg); color: var(--status-success-fg); }
  .gate-badge.gate-warn { background: var(--status-warn-bg); color: var(--status-warn-fg); }
  .gate-badge.gate-na { background: var(--status-muted-bg); color: var(--status-muted-fg); }
  /* 사용자 요청(2026-11-05, 로드맵 8번 "Crew 빌더 diff 모드") — jsdiff의
     diffLines() 청크 단위로 추가분/삭제분/변화없음을 구분. 기존
     success(초록)/warn(빨강) 토큰을 그대로 재사용 — 새 색상 도입 없음. */
  .diff-block { white-space: pre-wrap; word-break: break-word; }
  .diff-block .diff-added { background: var(--status-success-bg); color: var(--status-success-fg); }
  .diff-block .diff-removed { background: var(--status-warn-bg); color: var(--status-warn-fg); text-decoration: line-through; }
  .diff-block .diff-unchanged { color: inherit; }
  /* 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — Drawflow
     노드 안쪽 콘텐츠(HTML을 그대로 넣는 자리)의 최소 스타일. Drawflow
     자체 CSS가 노드 박스 테두리/배경을 이미 그려주므로 라벨·버튼
     간격만 보정한다. */
  .team-canvas-node { padding: 0.3rem 0.5rem; text-align: center; }
  .team-canvas-node-label { font-size: 0.82rem; font-weight: 600; margin-bottom: 0.2rem; word-break: break-word; }
  .team-canvas-node-manager .team-canvas-node-label { color: #b45309; }
  .team-canvas-node button { font-size: 0.72rem; padding: 0.1rem 0.35rem; }
  .team-canvas-wrap .drawflow-node { background: #eef1f6; border: 1.5px solid #888; border-radius: 8px; }
  .team-canvas-wrap .drawflow-node.team-canvas-manager { background: #fef3c7; border-color: #b45309; }
  .final-result-card { background: #f0fdf4; border: 1px solid #86efac; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; }
  .dashboard-grid { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .dashboard-grid .card { flex: 1; min-width: 200px; margin-bottom: 0; }
  .dashboard-grid .stat-row { display: flex; justify-content: space-between; padding: 0.15rem 0; border-bottom: 1px solid #eee; }
  .gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.9rem; }
  /* 사용자 보고(2026-11-10, 스크린샷 — "컨텐츠간 경계 구분"이 안 됨) —
     `.gallery-card`는 `<div>`인데 카드 배경/테두리는 `section.card`
     (태그+클래스 조합) 선택자로만 걸려 있어 전혀 안 먹었다. 여기서
     독립적으로 테두리/배경/그림자를 준다. */
  .gallery-card {
    margin-bottom: 0;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    padding: 0.75rem 0.9rem;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
  }
  .gallery-card-head { display: flex; justify-content: space-between; align-items: center; gap: 0.3rem; margin-bottom: 0.4rem; flex-wrap: wrap; }
  .gallery-card p { margin: 0.25rem 0; overflow-wrap: anywhere; }
  .gallery-card-actions { margin-top: 0.5rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }
  th { background: #f0f1f4; position: relative; }
  /* 사용자 요청(2026-10-27, "테이블 컬럼 폭을 마우스로 조정 가능하게
     개선해줘") — 각 th 오른쪽 가장자리의 얇은 드래그 핸들. 드래그를
     시작하는 순간에만(JS 쪽) 그 테이블을 table-layout: fixed로 바꾸고
     현재 폭을 그대로 고정값으로 얼린다 — 평소엔 내용 기준 가변폭
     (`table-layout: auto`, 바로 위 요청)을 유지하다가, 사용자가 실제로
     조정한 열만 그 순간부터 고정폭이 된다. */
  .col-resizer { position: absolute; top: 0; right: -3px; width: 6px; height: 100%; cursor: col-resize; user-select: none; touch-action: none; z-index: 1; }
  .col-resizer:hover, .col-resizer.resizing { background: rgba(20, 33, 61, 0.25); }
  /* 사용자 요청(2026-10-14, "각 메뉴의 목록 기능에 필터/소트 기능을
     추가해줘") — table() 헬퍼(앱 전체 23곳 목록 화면이 공유)에 붙는
     검색창/정렬 가능 헤더 스타일. */
  .table-wrap .table-filter { width: 100%; max-width: 320px; margin-bottom: 0.5rem; box-sizing: border-box; }
  /* 사용자 요청(2026-10-27, "컬럼폭 조정시에도 전체 폭이 영역(예: 목록
     박스)을 넘지 않도록") — 진짜 원인은 열 폭 계산이 아니라 여기였다:
     `.table-wrap`에 overflow 처리가 없어서, 테이블의 최소 내용 폭(버튼
     여러 개가 늘어선 "동작" 열 등, table-layout: auto에서 줄어들 수
     없는 콘텐츠)이 카드 폭보다 크면 테이블이 `.card` 밖으로 그냥
     흘러넘쳤다(열 간 폭을 서로 맞바꾸는 드래그 로직만으로는 애초에
     넘치는 초기 상태 자체는 못 막는다). `.table-wrap`을 가로 스크롤
     컨테이너로 만들면 테이블이 아무리 넓어도 이 박스 밖으로는 못
     나간다 — 넘치는 만큼은 스크롤로 본다. */
  .table-wrap { max-width: 100%; overflow-x: auto; }
  /* 사용자 보고(2026-10-14, 스크린샷 — "생성방식 ▲LM"처럼 헤더 글자가
     옆 칸과 겹쳐 깨져 보임) — 실측으로 확인한 원인: 전역 `table {
     table-layout: fixed }` + `td, th { overflow-wrap: break-word }`가
     이미 있는데, 정렬 헤더에만 `white-space: nowrap`을 얹어 그 줄바꿈
     규칙을 덮어썼다. 좁게 고정된 열(예: "LLM" 3글자)에서 nowrap 텍스트
     + 화살표가 칸 너비를 넘기면 `overflow: visible`(기본값)이라 옆
     칸 위로 그대로 흘러나와 겹쳐 보인다. nowrap을 빼서 기존 줄바꿈
     규칙(그리고 "스킬" 열처럼 폭을 넉넉히 주는 기존 관례)을 그대로
     따르게 한다. */
  th.sortable-col { cursor: pointer; user-select: none; }
  th.sortable-col:hover { background: #e4e6ec; }
  th.sortable-col .sort-indicator { font-size: 0.75em; color: #14213d; margin-left: 0.15rem; }
  input, select, textarea { padding: 0.35rem; border: 1px solid #ccc; border-radius: 4px; font-size: 0.85rem; }
  form.inline { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 0.75rem; }
  button.primary { background: #14213d; color: #fff; border: none; padding: 0.4rem 0.9rem; border-radius: 4px; cursor: pointer; }
  button.danger { background: #d64545; color: #fff; border: none; padding: 0.3rem 0.6rem; border-radius: 4px; cursor: pointer; }
  button.small { padding: 0.25rem 0.5rem; font-size: 0.8rem; cursor: pointer; }
  .msg { padding: 0.5rem 0.75rem; border-radius: 4px; margin-bottom: 0.75rem; font-size: 0.85rem; }
   .msg.error { background: var(--status-danger-bg); color: var(--status-danger-fg); }
   .msg.ok { background: var(--status-success-bg); color: var(--status-success-fg); }
   .msg.warn { background: var(--status-warn-bg); color: var(--status-warn-fg); }
  /* 사용자 요청(2026-11-12, "협업공간에서 진행과정과 결과물 확인이
     어려워(숨겨져 있는 느낌)") — 지금까지 #global-msg가 <main> 맨
     위 일반 플로우에 있어, 긴 목록 아래쪽에서 액션을 하면 결과
     메시지가 화면 밖(스크롤 위)에 떠서 안 보였다. 뷰포트에 고정해
     스크롤 위치와 무관하게 항상 보이게 한다 — z-index는 이 파일에서
     가장 큰 기존 값(.artifact-fullscreen-overlay의 100)보다 위로
     둔다(알림이 전체화면 미리보기보다 더 급한 신호라고 판단).
     비어 있을 때 클릭을 가로채면 안 되므로 바깥 래퍼는 pointer-events:
     none, 실제 메시지 박스만 auto로 되돌린다. */
  #global-msg { position: fixed; top: 1rem; right: 1rem; z-index: 200;
    max-width: min(420px, 90vw); pointer-events: none; }
  #global-msg .msg { pointer-events: auto; box-shadow: 0 4px 14px rgba(0,0,0,0.18);
    animation: global-msg-in 0.15s ease-out; }
  @keyframes global-msg-in { from { transform: translateY(-8px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
   .badge-reused { font-size: 0.65rem; background: var(--color-admin); color: #fff; border-radius: 3px; padding: 0 0.3rem; margin-left: 0.3rem; }
  .muted { color: var(--color-muted); font-size: 0.8rem; }
  .help-list { margin: 0.3rem 0 0.75rem; padding-left: 1.1rem; }
  .help-list li { margin-bottom: 0.35rem; line-height: 1.4; }
  .help-list li:last-child { margin-bottom: 0; }
  /* 사용자 요청(2026-11-03, "도움말이 펼침으로 확인 하도록 UI 개선하고
     기본으로 닫힘으로 헤줘") — 화면 상단 도움말(.help-list)이 기본
     노출돼 화면을 늘 차지하던 걸, 네이티브 <details>로 접어 기본 닫힘
     + 클릭 시 펼침으로 바꾼다. */
  .help-box { margin: 0.3rem 0 0.75rem; }
  .help-box > summary.help-summary { cursor: pointer; color: var(--color-muted); font-size: 0.8rem; list-style: none; user-select: none; }
  .help-box > summary.help-summary::-webkit-details-marker { display: none; }
  .help-box > summary.help-summary::marker { content: ""; }
  .help-box > summary.help-summary::before { content: "▸ "; }
  .help-box[open] > summary.help-summary::before { content: "▾ "; }
  .help-box .help-list { margin-top: 0.4rem; }
  .skill-picker { display: inline-flex; flex-direction: column; gap: 0.3rem; }
  .skill-picker-row { display: flex; gap: 0.3rem; }
  .skill-picker-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .skill-chip { display: inline-flex; align-items: center; gap: 0.25rem; background: #eef1f6; border-radius: 12px; padding: 0.1rem 0.3rem 0.1rem 0.6rem; font-size: 0.8rem; }
  .skill-chip button { border: none; background: none; cursor: pointer; font-size: 0.9rem; line-height: 1; padding: 0 0.2rem; color: #666; }
  /* 사용자 요청(2026-10-27, "컬럼 폭이 고정되어 있어. 가변으로
     개선해줘") — `table-layout: fixed`는 모든 열이 내용과 무관하게
     균등폭을 먼저 가져가, 짧은 열("코드"·"모드" 등)이 긴 열("설명"·
     "기본 도구")만큼 넓게 고정되고 반대로 긴 내용은 눌려 줄바꿈이
     심해지는 원인이었다. `auto`로 바꾸면 각 열이 실제 내용 폭에 맞춰
     커지되, 아래 col-actions/col-skills/col-detail의 width는 여전히
     "최소 이만큼은 확보"하는 힌트로 작동해(auto 레이아웃에서 width는
     강제값이 아니라 최소폭 계산에 반영되는 제안값) 버튼/select가 옆
     칸으로 흘러넘치던 문제(2026-10-14/27 기록)는 그대로 방지된다. */
  table { table-layout: auto; }
  td, th { overflow-wrap: break-word; }
  td.col-actions, th.col-actions { width: 260px; }
  /* select/input+button 한 쌍만 있는 좁은 동작 열("상사 지정" 등) —
     col-actions(여러 버튼이 쌓이는 "동작" 열)보다 좁아도 된다. */
  td.col-actions-compact, th.col-actions-compact { width: 170px; }
  td.col-skills, th.col-skills { width: 180px; }
  td.col-detail, th.col-detail { width: 240px; }
  td.col-detail details > div { margin-top: 0.3rem; }
  td.col-detail details > div > div { margin-bottom: 0.2rem; }
  /* 사용자 보고(2026-10-14, 스크린샷 — 버튼·입력이 전부 풀폭으로
     세로 스택돼 행 하나가 화면을 가득 채움) — 세로 1열 스택 대신
     가로로 흐르다 공간이 부족하면 다음 줄로 넘어가게 바꾼다(버튼 2~3개
     짜리 단순한 행은 가로 한 줄에 다 들어가 오히려 더 작아진다). */
  .actions { display: flex; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
  .actions > button { width: auto; }
  .actions > select, .actions > input { width: auto; min-width: 7rem; box-sizing: border-box; }
  .actions details { width: 100%; }
  /* 사용자 요청(2026-10-27, "테이블 컬럼 조정시 좌우 스크롤 생기지
     않도록") — 칸이 좁아 select/input+button이 한 줄에 다 안 들어가면
     (예: col-actions-compact), 옆 칸을 침범하는 대신 버튼이 다음
     줄로 자연스럽게 내려가게 한다. 공간이 충분한 곳(기존 col-actions
     등)에는 아무 영향 없음 — 꽉 찰 때만 줄바꿈. */
  .actions .pair { display: flex; flex-wrap: wrap; gap: 0.2rem; align-items: center; }
  .actions .pair input, .actions .pair select { width: auto; min-width: 6rem; flex: initial; }
  .actions .pair button { width: auto; flex-shrink: 0; }
  #login-view { display: flex; align-items: center; justify-content: center; height: 100vh; }
  #login-view .card { width: 320px; }
  #login-view input { width: 100%; margin-bottom: 0.5rem; box-sizing: border-box; }
  code { background: #f0f1f4; padding: 0 0.25rem; border-radius: 3px; }

  /* 사용자 요청(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT,
     HTML 등 다양할 수 있을텐데... Tier 2까지 진행해줘") — content_type별
     표시 스타일.
     사용자 요청(2026-10-14, "마크다운 처리 및 다이어그램, 차트, HTML
     렌더링, 코드 표시가 모두 렌더링되어 미려하게 보이도록") — 손수 짠
     정규식 렌더러(토큰 클래스 .tok-*)를 걷어내고 highlight.js로
     교체했다(실제 코드 하이라이팅 CDN 테마 CSS를 <head>에서 불러옴 —
     아래 .code-block은 테마가 못 채우는 여백/모서리만 보정한다). */
  .code-block { border-radius: 6px; overflow-x: auto; margin: 0; }
  .code-block code.hljs { display: block; padding: 0.6rem 0.75rem; border-radius: 6px; }
  .md-content h1, .md-content h2, .md-content h3,
  .md-content h4, .md-content h5, .md-content h6 { margin: 0.7rem 0 0.3rem; line-height: 1.3; }
  .md-content h1:first-child, .md-content h2:first-child, .md-content h3:first-child { margin-top: 0; }
  .md-content p { margin: 0 0 0.6rem; line-height: 1.55; }
  .md-content p:last-child { margin-bottom: 0; }
  .md-content ul, .md-content ol { margin: 0 0 0.6rem; padding-left: 1.4rem; line-height: 1.5; }
  .md-content li { margin: 0.15rem 0; }
  /* marked.js가 실제로 표/인용문/이미지를 만들어내므로(손수 짠 렌더러는
     지원 범위 밖이라 못 만들었다) 그 태그들의 스타일도 새로 추가한다. */
  .md-content table { border-collapse: collapse; width: 100%; margin: 0 0 0.6rem; font-size: 0.92em; }
  .md-content th, .md-content td { border: 1px solid #e2e4e8; padding: 0.35rem 0.6rem; text-align: left; }
  .md-content th { background: #f6f8fa; font-weight: 600; }
  .md-content blockquote { margin: 0 0 0.6rem; padding: 0.2rem 0.8rem; border-left: 3px solid #d0d7de; color: #57606a; }
  .md-content img { max-width: 100%; border-radius: 4px; }
  .mermaid-block { margin: 0.5rem 0; text-align: center; overflow-x: auto; }
  .mermaid-block svg { max-width: 100%; }
  .chart-block { margin: 0.5rem 0; max-width: 560px; }
  .chart-block canvas { max-width: 100%; }
  .md-content a { color: #2563eb; text-decoration: underline; }
  .md-content hr { border: none; border-top: 1px solid #e2e4e8; margin: 0.8rem 0; }
  .md-content .code-block { margin: 0.4rem 0; }
  /* 사용자 요청(2026-11-10, "이용 가이드 가독성을 높여줘") — 이용
     가이드는 다른 `.md-content` 사용처(결과물 미리보기 등, 보통
     카드 폭이 좁음)와 달리 넓은 화면(`main`이 최대 1400px)에서
     한 줄이 지나치게 길어져 읽기 어려웠다. 이 긴 글에만(`.manual-
     content`, renderManual() 참고) 읽기 좋은 줄 길이로 좁히고,
     9개 절을 한눈에 구분할 수 있게 h2마다 구분선+여백을 둔다 —
     다른 `.md-content` 사용처는 전혀 영향받지 않는다.
     사용자 보고(2026-11-10, 스크린샷 — "내용이 표시되는 영역이 중앙에
     좁게 표시되고 있어") — 폭 제한을 글자(`.manual-content`)에만
     걸었더니 카드 테두리(`.manual-card`, 최대 1400px)는 그대로 넓어서
     카드 안에 글자를 둘러싼 큰 빈 여백이 생겼다. 폭 제한을 카드
     자체로 옮겨 카드 크기 자체가 읽기 좋은 폭이 되게 한다. */
  .manual-card { max-width: 900px; margin: 0 auto; }
  .manual-content { font-size: 1.02rem; }
  .manual-content p, .manual-content li { line-height: 1.7; }
  .manual-content h2 { margin-top: 2.2rem; padding-top: 1.1rem; border-top: 1px solid #e2e4e8; }
  .manual-content h2:first-of-type { margin-top: 0; padding-top: 0; border-top: none; }
  .manual-content h3 { margin-top: 1.3rem; color: #1f6feb; }
  .manual-content blockquote { background: #fff8e6; border-left-color: #d9a406; }
  /* 사용자 보고(2026-10-19, 스크린샷 — HTML 결과 미리보기 영역이 너무
     좁아 게임 캔버스가 안 보이고 스크롤바까지 생김) — 기본 240px는
     이 플랫폼에서 실제로 자주 나오는 게임류 캔버스(480~800px 높이)
     에 턱없이 부족했다. 전체화면(.artifact-fullscreen-body)의 60vh
     만큼은 아니어도 인라인에서 바로 알아볼 수 있을 만큼 넉넉히 올린다. */
  .html-preview { width: 100%; min-height: 480px; border: 1px solid #e2e4e8; border-radius: 6px; background: #fff; }
  /* 복사/미리보기·소스 토글(2026-10-12) — Claude Artifact처럼 결과물에
     전용 툴바를 둔다. 새 색상 토큰 없이 기존 .small 버튼 스타일만
     재사용한다. */
  .artifact-toolbar { display: flex; justify-content: flex-end; gap: 0.3rem; margin-bottom: 0.3rem; }
  .artifact-source-pre { max-height: 400px; overflow-y: auto; }
  /* 사용자 요청(2026-10-13, "HAIR 최종 실행 결과물은 claude artifact
     수준으로") — 지금까지 이 블록엔 카드 테두리가 전혀 없어 주변
     텍스트(사람 코멘트 등)와 시각적으로 구분이 안 됐다. 실제 Claude
     Artifact 패널처럼 테두리·배경·둥근 모서리로 "이건 별도 결과물
     덩어리다"를 한눈에 알아보게 한다. */
  .artifact-block { border: 1px solid #e2e4e8; border-radius: 8px; background: #fff; padding: 0.6rem 0.75rem; margin: 0.3rem 0; }
  .artifact-block .artifact-toolbar { margin-bottom: 0.5rem; }
  /* 전체화면 오버레이(2026-10-13) — 단일 재사용 엘리먼트, body에 직접
     붙인다(카드 안에 있으면 overflow:hidden 조상에 잘릴 수 있어서). */
  .artifact-fullscreen-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.55); z-index: 100; display: flex; align-items: center; justify-content: center; padding: 2rem; box-sizing: border-box; }
  .artifact-fullscreen-panel { position: relative; background: #fff; border-radius: 10px; width: 100%; max-width: 900px; max-height: 90vh; overflow: auto; padding: 2.5rem 1.5rem 1.5rem; box-sizing: border-box; }
  .artifact-fullscreen-close { position: absolute; top: 0.6rem; right: 0.6rem; }
  .artifact-fullscreen-body .html-preview { min-height: 60vh; }
  /* 커스텀 확인 모달(2026-11-12) — 네이티브 confirm() 대체. z-index는
     전체화면 오버레이(100)보다 위, 전역 메시지 토스트(200)보다도
     위에 둔다(사용자가 반드시 응답해야 하는 모달이 가장 급하다). */
  .confirm-modal-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.55); z-index: 300; display: flex; align-items: center; justify-content: center; padding: 1rem; box-sizing: border-box; }
  .confirm-modal-box { background: var(--color-surface); color: var(--color-text); border-radius: 8px; width: 100%; max-width: 420px; padding: 1.25rem; box-sizing: border-box; box-shadow: 0 8px 28px rgba(0,0,0,0.3); }
  .confirm-modal-text { margin: 0 0 1rem; font-size: 0.9rem; white-space: pre-wrap; }
  .confirm-modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; }

  /* Phase 5(사용자 요청, 2026-09-28, "반응형만 포함(내 업무/알림 확인용)")
     — 이 앱은 고정 1100px 레이아웃(과거 다크모드 회귀 회피와 같은 이유로
     의도적 단순화)이었다. 전체 반응형 재설계는 스코프 밖으로 명시적으로
     뺐고, 사용자가 고른 범위대로 "인박스"·"내 업무"(승인 알림 확인의
     실질적 진입점)만 좁게 고친다 — 표가 많은 관리자 화면은 건드리지
     않는다(그 화면들까지 좁히면 표가 깨질 위험이 더 크다). */
  @media (max-width: 640px) {
    main { width: 96%; padding: 0.6rem; }
    header { padding: 0.6rem; gap: 0.5rem; }
    header h1 { font-size: 1rem; }
    .deliverable-card.chat-human { margin-right: 4%; }
    .deliverable-card.chat-agent { margin-left: 4%; }
    .dashboard-grid { flex-direction: column; }
    .dashboard-grid .card { min-width: 0; }
  }
</style>
<!-- 사용자 요청(2026-10-14, "마크다운 처리 및 다이어그램, 차트, HTML
     렌더링, 코드 표시가 모두 렌더링되어 미려하게 보이도록") — 이
     플랫폼은 지금까지 외부 CDN 스크립트가 전혀 없는 자체완결형
     단일 HTML이었다(손수 짠 정규식 마크다운/하이라이터만 사용).
     사용자 확인(2026-10-14, "CDN에서 불러오기")을 받고 실제 렌더링
     라이브러리로 교체한다 — marked(마크다운)·highlight.js(코드
     하이라이팅)·DOMPurify(marked가 만든 HTML을 innerHTML에 넣기
     전 필수 살균 — XSS 방지)·mermaid(다이어그램)·Chart.js(차트). -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@13.0.3/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<!-- 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — "팀 관리"
     화면에서 멤버 구조(순차 릴레이/매니저 위임)를 실행 전에도 보고
     드래그로 순서를 바꿀 수 있게 한다. Drawflow: vanilla JS, 의존성
     0개, MIT — 위 5개 CDN 라이브러리와 같은 패턴(런타임 CDN 로드,
     pip 패키징 영향 없음). CDN 로드 실패 시 캔버스만 비고 기존 멤버
     표(CRUD)는 전혀 영향받지 않는다(아래 renderTeams 참고). -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/drawflow@0.0.60/dist/drawflow.min.css">
<script src="https://cdn.jsdelivr.net/npm/drawflow@0.0.60/dist/drawflow.min.js"></script>
<!-- 사용자 요청(2026-11-05, 로드맵 8번 "Crew 빌더 diff 모드") — 로드러너
     데모에서 Crew 재시도마다 기존 기능이 조용히 퇴행해도 매번 전체
     코드를 사람이 다시 읽어 수동 비교해야 했다. jsdiff: vanilla JS,
     의존성 0개, MIT — 위 라이브러리들과 같은 패턴. CDN 로드 실패 시
     diff 없이 두 산출물을 그대로 나열해 보여주는 것으로 대체하고
     나머지 기능은 전혀 영향받지 않는다(아래 renderTextDiff 참고). -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/jsdiff/5.2.0/diff.min.js"></script>
</head>
<body>

<div id="login-view" style="display:none">
  <section class="card">
    <h2>HAIR-Platform 로그인</h2>
    <div id="login-msg"></div>
    <form id="login-form">
      <input id="login-username" placeholder="아이디" autocomplete="username" required>
      <input id="login-password" type="password" placeholder="비밀번호" autocomplete="current-password" required>
      <button class="primary" type="submit" style="width:100%">로그인</button>
    </form>
  </section>
</div>

<div id="app-view" style="display:none">
  <header>
    <h1>HAIR-Platform</h1>
    <span class="spacer"></span>
    <span id="whoami" class="muted"></span>
    <button id="logout-btn">로그아웃</button>
  </header>
  <nav id="tabs"></nav>
  <main>
    <div id="global-msg"></div>
    <div id="content"></div>
  </main>
</div>

<script>
"use strict";

function esc(s) {
  const d = document.createElement("div");
  d.textContent = (s === null || s === undefined) ? "" : String(s);
  return d.innerHTML;
}

// esc()는 엘리먼트 콘텐츠로 쓰기엔 안전하지만 따옴표(")는 안 바꿔준다
// (텍스트 노드엔 필요 없어서) — HTML 속성값(예: iframe srcdoc="...")에
// 그대로 넣으면 따옴표에서 값이 끊길 수 있어 별도로 이스케이프한다.
function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;");
}

// 복사·전체화면·다이어그램 id 발급용 — renderContent()와
// _activateRichBlocks() 양쪽이 공유한다(모두 아래에서 쓴다).
let _artifactSeq = 0;

// 사용자 요청(2026-10-14, "마크다운 처리 및 다이어그램, 차트, HTML
// 렌더링, 코드 표시가 모두 렌더링되어 미려하게 보이도록") — 손수 짠
// 정규식 렌더러(표·블록인용·다이어그램·차트를 의도적으로 지원 범위
// 밖에 뒀던 버전, 아래 stripOuterFence() 위 주석이 그 경위)를 걷어내고
// marked(마크다운 파서)+highlight.js(코드 하이라이팅)+DOMPurify(살균)
// +mermaid(다이어그램)+Chart.js(차트)로 교체한다(사용자 확인, "CDN에서
// 불러오기"). 다만 CDN을 못 불러오는 환경(폐쇄망 등)에서도 **앱 전체가
// 깨지면 안 된다** — 그 경우 리치 렌더링만 평문 폴백으로 내려가고 나머지
// 기능은 그대로 동작해야 한다고 사용자에게 안내했으므로, 아래 모든
// 라이브러리 접근은 `typeof X === "undefined"` 가드를 거친다.
if (typeof marked !== "undefined") {
  // marked는 코드펜스 언어가 "mermaid"/"chart"면 일반 코드블록 대신
  // 전용 플레이스홀더를 내보내도록 커스텀 렌더러를 쓴다 — 실제 그리기는
  // DOM에 붙은 뒤에만 가능해서(마크다운 문자열 생성 시점엔 아직 그릴
  // 대상이 없음) 아래 MutationObserver가 비동기로 처리한다.
  // 사용자 보고(2026-10-14, "대화를 열려고 하니 '불러오는 중…'로
  // 표시되면 열리지 않아") — 실측·재현으로 확인한 원인: marked v5+
  // (여기선 v13.0.3)는 커스텀 렌더러의 code()에 `(code, infostring)`
  // 위치 인자가 아니라 토큰 **객체**(`{type, raw, lang, text}`)를
  // 하나만 넘긴다. 객체를 문자열로 착각해 hljs.highlight()에 그대로
  // 넘기면 "e.replace is not a function"으로 marked.parse() 자체가
  // 예외를 던지고, 그 예외가 renderContent()→
  // _renderConversationMessage()→renderConversationDetail()까지
  // 그대로 전파돼 `detail.innerHTML`을 끝내 "불러오는 중…"에서 못
  // 벗어났다(실제 marked@13.0.3 소스로 재현·확인). `{ text, lang }`로
  // 구조분해해야 한다.
  const _markedRenderer = new marked.Renderer();
  _markedRenderer.code = function (token) {
    const code = token.text;
    const lang = (token.lang || "").trim().split(/\\s+/)[0].toLowerCase();
    if (lang === "mermaid") {
      return '<div class="mermaid-block" data-mermaid-src="' + escAttr(code) + '"><p class="muted">다이어그램 렌더링 중…</p></div>';
    }
    if (lang === "chart") {
      return '<div class="chart-block"><canvas data-chart-config="' + escAttr(code) + '"></canvas></div>';
    }
    return '<pre class="code-block">' + _highlightCode(code, lang) + "</pre>";
  };
  marked.setOptions({ renderer: _markedRenderer, gfm: true, breaks: true });
}
if (typeof mermaid !== "undefined") {
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
}

// highlight.js 호출 지점을 하나로 모은다 — "code" content_type과 marked
// 코드펜스 렌더러 양쪽이 공유한다. 언어를 모르면(또는 지원 안 하면)
// highlightAuto로 추측한다 — content_type='code'엔 언어 필드가 없다는
// 기존 설계(스키마 변경 없이 가는 쪽을 택함)를 그대로 유지한다.
// 사용자 보고(2026-10-14, "대화를 열려고 하니 '불러오는 중…'") 재발
// 방지 — 라이브러리 버전 차이·예상 밖 입력으로 hljs가 던지더라도
// (실제로 marked 렌더러 인자 버그가 바로 이 경로에서 예외를 던져
// 대화 화면 전체를 못 열게 만든 전례가 있다) 호출부(renderContent 등
// 여러 화면)까지 예외가 전파되지 않도록 여기서 막는다 — 실패하면
// 하이라이팅만 포기하고 이스케이프된 평문으로 폴백한다.
function _highlightCode(raw, lang) {
  if (typeof hljs === "undefined") {
    return "<code>" + esc(raw) + "</code>"; // CDN 미로드 폴백 — 최소한 안전하게는 보여준다.
  }
  try {
    const known = lang && hljs.getLanguage(lang) ? lang : null;
    const result = known ? hljs.highlight(raw, { language: known }) : hljs.highlightAuto(raw);
    return '<code class="hljs language-' + (known || result.language || "plaintext") + '">' + result.value + "</code>";
  } catch (e) {
    console.error("hljs highlighting failed, falling back to plain text:", e);
    return "<code>" + esc(raw) + "</code>";
  }
}

// 보안: marked는 입력 마크다운에 섞인 원문 HTML(인라인/블록)을 그대로
// 통과시킨다 — Agent/LLM 산출물처럼 신뢰할 수 없는 텍스트이므로, 매번
// DOMPurify.sanitize()를 거친 결과만 innerHTML에 넣는다(<script>/on*
// 이벤트 핸들러/javascript: 링크 등을 전부 제거) — 기존의 "esc() 먼저,
// 그 뒤엔 신뢰한 태그만 추가"라는 안전장치를, 더 검증된 라이브러리
// 기반 살균으로 교체한 것뿐 안전 수준은 유지(오히려 표·인용문·이미지
// 같은 더 넓은 HTML 표면을 다루면서도 DOMPurify가 전부 검사한다).
function _renderMarkdown(raw) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return "<p>" + esc(raw).replace(/\\n/g, "<br>") + "</p>"; // CDN 미로드 폴백
  }
  // marked.parse()가 던지면(라이브러리 버전 변경·예상 밖 입력 등) 이
  // 함수를 호출한 renderContent()/_renderConversationMessage()/
  // renderConversationDetail() 전체로 예외가 전파돼 화면이 "불러오는
  // 중…"에서 멈춘 전례가 실제로 있었다 — 여기서 잡아 평문 폴백으로
  // 내려가고, 화면 자체는 계속 열리게 한다.
  try {
    return DOMPurify.sanitize(marked.parse(raw));
  } catch (e) {
    console.error("markdown rendering failed, falling back to plain text:", e);
    return "<p>" + esc(raw).replace(/\\n/g, "<br>") + "</p>";
  }
}

// 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 1/2 실행 중) — Agent/사람
// 산출물에 마크다운 코드펜스(```)나 **굵게**가 섞여 있어도 전부 이스케이프
// 후 줄바꿈만 <br>로 바꿔 보여줘, 백틱·별표가 그대로 노출돼 가독성이
// 떨어졌다(코드 패치 설명 같은 산출물일수록 심함).
//
// html/markdown 둘 다 LLM이 응답 전체를 ```html.../```markdown...```
// 한 겹으로 또 감싸는 경우가 흔하다(실측, 2026-10-07, 실제 gpt-4o-mini로
// 시나리오를 돌려보다 발견 — 리뷰 서버의 손수 만든 가짜 응답으로는 한
// 번도 안 드러났다). 응답 "전체"가 코드 펜스 하나로만 감싸여 있을 때만
// 그 바깥 펜스를 벗겨내고 안쪽 내용으로 렌더링한다 — 본문 중간에 섞인
// 펜스(예: 설명+코드 혼합 응답)는 그대로 둔다(그건 의도된 "그 형식을
// 먼저 보여주는" 문서지, 벗겨낼 "감싸기"가 아니다).
function stripOuterFence(raw) {
  const m = raw.match(/^```[a-zA-Z0-9]*\\n([\\s\\S]*?)\\n?```\\s*$/);
  return m ? m[1] : raw;
}

// 사용자 요청(2026-10-14) — 다이어그램/차트는 DOM에 실제로 붙은 뒤에만
// 그릴 수 있는데, renderContent()는 여러 화면(Task 워크스페이스/Crew
// 실행 결과/대화/Agent 관리 등)에서 호출되는 순수 문자열 생성 함수라
// "언제 DOM에 붙는지"를 알 방법이 없다 — 매 호출부마다 "다 그렸으면
// 그려라" 코드를 심는 대신, 복사/전체화면 버튼과 같은 원칙(한 번만
// 전역 연결)으로 MutationObserver 하나만 걸어 새로 추가되는 노드를
// 감시한다. mermaid는 렌더가 끝나야 `data-mermaid-rendering`을 지우지
// 않고 그대로 둔다 — "전체화면" 오버레이가 아직 렌더 중인 블록을
// innerHTML로 복제해가면(드문 경쟁 상태) 그 복제본은 아직 렌더
// 전이므로 옵저버가 다시 잡아 독립적으로 렌더해준다.
// 사용자 보고(2026-10-18, "Syntax error in text / mermaid version 10.9.1"가
// 대화에 반복 노출됨) 원인 분석 — mermaid.js v10은 다이어그램 문법이
// 깨지면 `render()`의 Promise를 **reject하지 않는다**. 대신 내부
// "error diagram" 렌더러가 "Syntax error in text"+버전 문구를 큼직하게
// 그린 SVG를 **resolve**로 돌려준다(실측: mermaid.min.js 안에
// `.text("Syntax error in text")` 렌더러가 그대로 있음) — 그래서 아래
// `.catch()`가 한 번도 안 불리고, 그 못생긴 에러 그림이 `svg` 그대로
// 들어갔다. 메시지 목록을 다시 그릴 때마다(새 메시지 전송 등) 이
// DOM 노드가 통째로 재생성돼 같은 실패를 매번 새로 반복한 것도
// 그래서다. `mermaid.render()` 전에 `mermaid.parse(src, {suppressErrors:
// true})`로 먼저 검사해 — 이 함수는 실패해도 그 보기 흉한 SVG를 만들지
// 않고 그냥 `false`만 돌려준다 — 실패 시 우리가 직접 깔끔한 안내 +
// 원본 소스를 보여준다.
function _mermaidFallbackHtml(src) {
  return '<p class="muted">⚠ 다이어그램 문법 오류로 그릴 수 없습니다 — 원본 코드:</p>' +
    '<pre style="white-space:pre-wrap;text-align:left">' + esc(src) + "</pre>";
}

// 사용자 보고(2026-10-18, "대화 내용에 머메이드 그래프가 보이지 않아")
// 재현으로 확인한 원인 — `root.querySelectorAll(selector)`는 root
// 자신은 절대 대상에 포함하지 않고 **후손만** 검사한다(DOM 표준
// 동작). `detail.innerHTML = ...`로 메시지 목록을 한 번에 갈아끼우면
// MutationObserver의 addedNodes가 그 최상위 형제 노드들을 하나씩
// 넘겨주는데, 마크다운 코드펜스가 문단에 안 감싸인 채 메시지 바로
// 아래 최상위 블록으로 나오면(ADR처럼 코드펜스가 통째로 한 줄만 있는
// 경우) `.mermaid-block` div 자체가 그 "최상위로 추가된 노드"가 된다
// — 그러면 `root`가 바로 그 블록이라 `root.querySelectorAll(...)`가
// 후손에서만 찾느라 자기 자신을 영영 못 찾아 "다이어그램 렌더링
// 중…" 플레이스홀더에 멈춰 있었다(실제 마크다운+DOMPurify+mermaid
// CDN 조합으로 재현해 확인). root 자신이 선택자와 일치하는 경우도
// 포함해야 한다.
function _selfAndDescendants(root, selector) {
  const out = root.matches && root.matches(selector) ? [root] : [];
  return out.concat(Array.from(root.querySelectorAll(selector)));
}
function _activateRichBlocks(root) {
  _selfAndDescendants(root, ".mermaid-block[data-mermaid-src]").forEach(el => {
    if (el.dataset.mermaidRendering === "1") return;
    if (typeof mermaid === "undefined") {
      el.innerHTML = '<p class="muted">다이어그램 라이브러리를 불러오지 못했습니다.</p>';
      return;
    }
    el.dataset.mermaidRendering = "1";
    const src = el.dataset.mermaidSrc;
    const id = "mmd-" + (++_artifactSeq);
    Promise.resolve(mermaid.parse(src, { suppressErrors: true }))
      .then(ok => {
        if (!ok) {
          el.innerHTML = _mermaidFallbackHtml(src);
          return;
        }
        return mermaid.render(id, src).then(({ svg }) => { el.innerHTML = svg; });
      })
      .catch(e => {
        // parse()가 suppressErrors에도 예상 밖으로 던지거나, render()
        // 자체가 실패하는 그 외의 경우(예: 라이브러리 내부 런타임 오류)
        // 를 위한 마지막 방어선 — 이 경로는 실제로는 거의 안 탄다.
        el.innerHTML = _mermaidFallbackHtml(src) +
          '<p class="muted">(' + esc(e.message || String(e)) + ")</p>";
      });
  });
  // Chart.js는 <canvas> DOM 노드 자체에 차트 인스턴스를 붙인다 — innerHTML
  // 복제(전체화면 오버레이)는 마크업만 복사하고 그 붙은 상태는 복사하지
  // 않으므로, data-* 플래그가 아니라 `Chart.getChart(canvas)`로 "이
  // 정확한 노드에 이미 차트가 있는지"를 확인해야 복제본도 올바르게
  // 다시 그려진다.
  _selfAndDescendants(root, "canvas[data-chart-config]").forEach(canvas => {
    if (typeof Chart === "undefined" || Chart.getChart(canvas)) return;
    try {
      new Chart(canvas, JSON.parse(canvas.dataset.chartConfig));
    } catch (e) {
      canvas.insertAdjacentHTML("afterend", '<p class="muted">차트 설정을 해석하지 못했습니다: ' + esc(e.message) + "</p>");
    }
  });
}
new MutationObserver(mutations => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (node.nodeType === 1) _activateRichBlocks(node);
    }
  }
}).observe(document.body, { childList: true, subtree: true });

// 복사 버튼/미리보기·소스 토글(2026-10-12, "Phase 1 (A)(B)(H1)(H2)(I5)
// 부터 구현 시작해줘") — 매 호출마다
// 고유 id를 줘서, 렌더링된 위치가 어디든(Task 워크스페이스/Crew 실행
// 결과/대화 등 renderContent() 호출부 전부) 버튼이 자기 블록만 찾게 한다.
// 콜러마다 버튼 이벤트를 따로 연결할 필요가 없도록 전역 위임
// 리스너(`document.addEventListener("click", ...)`, 아래)로 한 번만
// 연결한다.
// 사용자 요청(2026-10-14, "결과 파일을 복사 및 다운로드 가능하도록") —
// content_type별로 받는 파일 확장자/MIME. 기본(타입 미지정)도 마크다운
// 으로 렌더링하므로 .md로 묶는다.
const _DOWNLOAD_EXT = { html: "html", code: "txt", markdown: "md" };
const _DOWNLOAD_MIME = { html: "text/html", code: "text/plain", md: "text/plain", txt: "text/plain" };

// 사용자 요청(2026-11-05, 로드맵 8번 "Crew 빌더 diff 모드") — 로드러너
// 데모에서 Crew 재시도마다 기존 기능이 조용히 퇴행해도 매번 전체
// 산출물을 사람이 다시 읽어 수동 비교해야 했다. jsdiff(CDN, <head>
// 참고)의 Diff.diffLines()로 줄 단위 추가/삭제를 시각화한다. CDN 로드
// 실패 시(이 플랫폼의 기존 CDN 라이브러리 전부와 같은 원칙) diff 없이
// 두 산출물을 그대로 나열해 보여주는 것으로 대체 — 나머지 기능은
// 전혀 영향받지 않는다.
function renderTextDiff(oldText, newText) {
  if (typeof Diff === "undefined") {
    return '<p class="muted">diff 라이브러리를 불러오지 못했습니다 — 두 산출물을 그대로 비교하세요.</p>' +
      '<p><b>이전 실행</b></p><pre class="code-block">' + esc(oldText || "(없음)") + '</pre>' +
      '<p><b>이번 실행</b></p><pre class="code-block">' + esc(newText || "(없음)") + '</pre>';
  }
  const parts = Diff.diffLines(oldText || "", newText || "");
  const html = parts.map(p =>
    '<span class="' + (p.added ? "diff-added" : p.removed ? "diff-removed" : "diff-unchanged") +
    '">' + esc(p.value) + '</span>'
  ).join("");
  return '<pre class="code-block diff-block">' + html + '</pre>';
}

function renderContent(raw, contentType) {
  if (contentType === "html" || contentType === "code" || contentType === "markdown") {
    raw = stripOuterFence(raw);
  }
  const artifactId = "artifact-" + (++_artifactSeq);
  // 사용자 요청(2026-10-14) — 마크다운도 이제 raw 소스(기호·다이어그램/
  // 차트 코드펜스 원문)를 그대로 확인할 수 있어야 쓸모가 있어 토글
  // 대상에 추가한다.
  const hasToggle = contentType === "html" || contentType === "code" || contentType === "markdown";

  let previewHtml;
  if (contentType === "html") {
    previewHtml = '<iframe class="html-preview" sandbox="allow-scripts" srcdoc="' + escAttr(raw) + '"></iframe>';
  } else if (contentType === "code") {
    previewHtml = '<pre class="code-block">' + _highlightCode(raw) + "</pre>";
  } else {
    // markdown이든 타입 미지정이든 전부 실제 마크다운 렌더러를 거친다
    // (기존에도 타입 미지정은 **굵게**/코드펜스만 가볍게 처리하는
    // "마크다운 라이트" 취급이었다 — 이제 똑같이 풀 렌더링으로 올린다).
    previewHtml = '<div class="md-content">' + _renderMarkdown(raw) + "</div>";
  }

  // 복사 버튼은 렌더링된(하이라이팅/HTML태그 섞인) 결과가 아니라 원본
  // raw 텍스트를 복사해야 한다 — <textarea> 안에 esc()로 이스케이프해
  // 넣으면 브라우저가 파싱 시점에 엔티티를 다시 원문으로 풀어주므로,
  // `.value`를 읽기만 하면 별도 보관 없이 정확한 원본을 얻는다.
  const downloadExt = _DOWNLOAD_EXT[contentType] || "md";
  const toolbarHtml =
    '<div class="artifact-toolbar">' +
    '<button type="button" class="small" data-copy-artifact="' + artifactId + '">복사</button>' +
    '<button type="button" class="small" data-download-artifact="' + artifactId + '" data-download-ext="' + downloadExt + '">다운로드</button>' +
    (hasToggle
      ? '<button type="button" class="small" data-toggle-artifact="' + artifactId + '">소스 보기</button>'
      : "") +
    // 사용자 요청(2026-10-13, "claude artifact 수준으로") — Claude
    // Artifact의 대표적인 특징인 "전체화면으로 보기"를 추가한다.
    '<button type="button" class="small" data-fullscreen-artifact="' + artifactId + '">전체화면</button>' +
    '</div>';
  const sourceHtml = hasToggle
    ? '<div class="artifact-source" id="' + artifactId + '-source" style="display:none">' +
      '<pre class="code-block artifact-source-pre">' +
      _highlightCode(raw, contentType === "html" ? "xml" : contentType === "markdown" ? "markdown" : undefined) +
      "</pre></div>"
    : "";

  return (
    '<div class="artifact-block" data-artifact-id="' + artifactId + '">' +
    toolbarHtml +
    '<div class="artifact-preview" id="' + artifactId + '-preview">' + previewHtml + '</div>' +
    sourceHtml +
    '<textarea class="artifact-raw-store" id="' + artifactId + '-raw" style="display:none" aria-hidden="true">' +
    esc(raw) + '</textarea>' +
    '</div>'
  );
}

// 전역 위임(한 번만 등록) — renderContent()가 어디서 호출되든(Task
// 워크스페이스/Crew 실행 결과/대화 등) 매번 버튼을 다시 연결할 필요가
// 없다.
document.addEventListener("click", async (ev) => {
  // 컬럼 폭 조정 핸들은 정렬 가능한 th 안에 들어있어, 드래그가 끝난 뒤
  // 발생하는 네이티브 click이 그대로 버블되면 정렬(data-sort-col)까지
  // 같이 트리거된다 — 여기서 먼저 걸러낸다.
  if (ev.target.closest(".col-resizer")) return;
  const copyBtn = ev.target.closest("[data-copy-artifact]");
  if (copyBtn) {
    const raw = document.getElementById(copyBtn.dataset.copyArtifact + "-raw");
    if (!raw) return;
    navigator.clipboard.writeText(raw.value).then(() => {
      const original = copyBtn.textContent;
      copyBtn.textContent = "복사됨";
      setTimeout(() => { copyBtn.textContent = original; }, 1200);
    }).catch(() => showMsg("global-msg", "클립보드 복사에 실패했습니다", "error"));
    return;
  }
  const toggleBtn = ev.target.closest("[data-toggle-artifact]");
  if (toggleBtn) {
    const id = toggleBtn.dataset.toggleArtifact;
    const preview = document.getElementById(id + "-preview");
    const source = document.getElementById(id + "-source");
    if (!preview || !source) return;
    const showingSource = source.style.display !== "none";
    source.style.display = showingSource ? "none" : "block";
    preview.style.display = showingSource ? "block" : "none";
    toggleBtn.textContent = showingSource ? "소스 보기" : "미리보기";
    return;
  }
  const fullscreenBtn = ev.target.closest("[data-fullscreen-artifact]");
  if (fullscreenBtn) {
    openArtifactFullscreen(fullscreenBtn.dataset.fullscreenArtifact);
    return;
  }
  // 사용자 요청(2026-10-14, "결과 파일을 복사 및 다운로드 가능하도록") —
  // 복사(클립보드)와 별개로 실제 파일로 저장할 수 있게 한다. 원본 raw
  // 텍스트는 복사 버튼과 똑같이 숨겨진 textarea에서 그대로 읽는다(다시
  // 렌더링된 HTML이 아니라 원본을 받아야 한다).
  const downloadBtn = ev.target.closest("[data-download-artifact]");
  if (downloadBtn) {
    const id = downloadBtn.dataset.downloadArtifact;
    const rawEl = document.getElementById(id + "-raw");
    if (!rawEl) return;
    const ext = downloadBtn.dataset.downloadExt || "txt";
    const mime = _DOWNLOAD_MIME[ext] || "text/plain";
    const blob = new Blob([rawEl.value], { type: mime + ";charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = id + "." + ext;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return;
  }
  // 사용자 요청(2026-10-14, "단위 메시지 삭제 기능") — 메시지 목록은
  // innerHTML로 통째로 다시 그려지므로(`refreshConversationMessages()`),
  // 버튼마다 개별 리스너를 거는 대신 복사/전체화면 버튼과 같은 전역
  // 위임 방식을 쓴다 — 재렌더될 때마다 다시 배선할 필요가 없다.
  const deleteMsgBtn = ev.target.closest("[data-delete-message]");
  if (deleteMsgBtn) {
    const [convId, msgId] = deleteMsgBtn.dataset.deleteMessage.split(":");
    if (!(await confirmModal("이 메시지를 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
    api("DELETE", "/api/conversations/" + convId + "/messages/" + msgId)
      .then(() => _reloadConversationMessagesInPlace(convId))
      .catch((e) => showMsg("global-msg", e.message, "error"));
    return;
  }
  const saveDeliverableBtn = ev.target.closest("[data-save-deliverable]");
  if (saveDeliverableBtn) {
    const [convId, msgId] = saveDeliverableBtn.dataset.saveDeliverable.split(":");
    saveDeliverableBtn.disabled = true;
    api("POST", "/api/conversations/" + convId + "/messages/" + msgId + "/save-as-deliverable")
      .then(() => {
        showMsg("global-msg", "갤러리로 보냈습니다 — '갤러리' 탭에서 확인할 수 있습니다.", "ok");
        saveDeliverableBtn.textContent = "보냄";
      })
      .catch((e) => {
        saveDeliverableBtn.disabled = false;
        showMsg("global-msg", e.message, "error");
      });
    return;
  }
  const saveKnowledgeBtn = ev.target.closest("[data-save-knowledge]");
  if (saveKnowledgeBtn) {
    const [convId, msgId] = saveKnowledgeBtn.dataset.saveKnowledge.split(":");
    withBusy(saveKnowledgeBtn, "보내는 중…", () =>
      api("POST", "/api/conversations/" + convId + "/messages/" + msgId + "/save-as-knowledge")
    )
      .then(() => {
        showMsg("global-msg", "지식베이스로 보냈습니다 — RAG 검색에서 바로 찾을 수 있습니다.", "ok");
        saveKnowledgeBtn.textContent = "보냄";
        saveKnowledgeBtn.disabled = true;
      })
      .catch((e) => showMsg("global-msg", e.message, "error"));
    return;
  }
  // 사용자 요청(2026-10-14, "각 메뉴의 목록 기능에 필터/소트 기능을
  // 추가해줘") — table()이 렌더되는 23곳 전부 이 전역 위임 하나로
  // 정렬 클릭을 받는다(호출부마다 따로 배선할 필요 없음).
  const sortTh = ev.target.closest("[data-sort-col]");
  if (sortTh) {
    _sortTableByColumn(sortTh);
  }
  // LLM 설정 최적화 추천(2026-10-18) — "AI 에이전트 관리" 화면의 버튼은
  // 클릭 시점에 비동기로 결과 패널을 새로 주입하므로(최초 렌더 때는
  // 존재하지 않음), 복사/삭제 버튼과 같은 전역 위임으로 처리한다.
  const recommendOptBtn = ev.target.closest("[data-recommend-optimize]");
  if (recommendOptBtn) {
    const id = recommendOptBtn.dataset.recommendOptimize;
    const resultEl = document.getElementById("optimize-result-" + id);
    if (!resultEl) return;
    resultEl.innerHTML = '<p class="muted">추천 계산 중…</p>';
    api("GET", "/api/agent-pool/" + id + "/optimize-recommendation")
      .then(rec => { resultEl.innerHTML = _renderOptimizeRecommendation(id, rec, "rule"); })
      .catch(e => { resultEl.innerHTML = '<p class="msg error">' + esc(e.message) + "</p>"; });
    return;
  }
  const refineOptBtn = ev.target.closest("[data-refine-optimize]");
  if (refineOptBtn) {
    const id = refineOptBtn.dataset.refineOptimize;
    const resultEl = document.getElementById("optimize-result-" + id);
    if (!resultEl) return;
    if (!(await confirmModal("AI에게 추천을 보정받습니다 — 실제 LLM API 호출로 비용이 발생합니다. 계속할까요?"))) return;
    resultEl.innerHTML = '<p class="muted">AI 의견 요청 중…(실제 LLM 호출)</p>';
    api("POST", "/api/agent-pool/" + id + "/optimize-recommendation/refine", {})
      .then(rec => {
        resultEl.innerHTML = _renderOptimizeRecommendation(id, rec, rec.source === "llm_refined" ? "llm" : "rule");
      })
      .catch(e => { resultEl.innerHTML = '<p class="msg error">' + esc(e.message) + "</p>"; });
    return;
  }
  const fillOptBtn = ev.target.closest("[data-fill-optimize]");
  if (fillOptBtn) {
    const id = fillOptBtn.dataset.fillOptimize;
    const payload = JSON.parse(fillOptBtn.dataset.optimizePayload || "{}");
    // admin이 "등록된 모델 중 선택" 셀렉트에서 1위 추천과 다른 후보를
    // 골랐으면 그 선택을 우선한다(레지스트리 밖 값은 애초에 옵션으로
    // 나타나지 않으므로 여기서 가져온 provider/model은 항상 등록된
    // 활성 모델이다).
    const pickSelId = fillOptBtn.dataset.optimizePickSel;
    if (pickSelId) {
      const pickSel = document.getElementById(pickSelId);
      if (pickSel) {
        const cands = JSON.parse(pickSel.dataset.candidates || "[]");
        const chosen = cands[Number(pickSel.value)];
        if (chosen) {
          payload.provider = chosen.provider;
          payload.model = chosen.model;
        }
      }
    }
    const providerSel = document.getElementById("provider-" + id);
    const modelSel = document.getElementById("model-" + id);
    if (providerSel && payload.provider) {
      providerSel.value = payload.provider;
      providerSel.dispatchEvent(new Event("change"));
    }
    if (modelSel && payload.model) {
      const hasOption = Array.from(modelSel.options).some(o => o.value === payload.model);
      if (hasOption) {
        modelSel.value = payload.model;
      } else {
        modelSel.value = "__custom__";
        const customInput = document.getElementById("model-" + id + "-custom");
        if (customInput) { customInput.style.display = ""; customInput.value = payload.model; }
      }
    }
    const t = payload.tuning_config || {};
    const setVal = (elId, v) => {
      const el = document.getElementById(elId);
      if (el && v !== undefined && v !== null) el.value = String(v);
    };
    setVal("tune-maxiter-" + id, t.max_iter);
    setVal("tune-maxrpm-" + id, t.max_rpm);
    setVal("tune-maxtime-" + id, t.max_execution_time);
    setVal("tune-maxretry-" + id, t.max_retry_limit);
    setVal("tune-temp-" + id, t.temperature);
    setVal("tune-reasoning-" + id, t.reasoning);
    setVal("tune-maxreasonattempts-" + id, t.max_reasoning_attempts);
    setVal("tune-codeexec-" + id, t.allow_code_execution);
    setVal("tune-respectctx-" + id, t.respect_context_window);
    showMsg("global-msg", "추천값을 입력란에 채웠습니다 — '모델 설정'/'파인튜닝 저장' 버튼을 눌러야 실제로 적용됩니다.", "ok");
    return;
  }
});

// 사용자 보고(2026-10-14, "삭제는 되는데 삭제 후 대화 창을 빠져나가
// 다시 대화창을 열어야 해") — 실측으로 확인한 원인: 삭제 직후 전역
// `refresh()`(현재 탭 전체를 `selectTab()`으로 다시 그림)를 불렀는데,
// "대화" 탭의 기본 화면(`tab.render()`)은 대화 목록이라 상세 화면 안에서
// 호출하면 그 목록으로 되돌아가 버린다(Task 워크스페이스에 임베드된
// 경우도 마찬가지로 그 자리가 통째로 다시 그려져 스크롤까지 끊긴다).
// 지금 열려 있는 대화 상세 화면은 그대로 두고 메시지 영역
// (`#conv-messages`)만 다시 불러온다 — `refreshConversationMessages()`와
// 같은 조회를 하지만, 전역 클릭 핸들러에는 그 함수가 쓰는 `conv`/
// `participants` 클로저가 없어 API로 새로 가져온다.
async function _reloadConversationMessagesInPlace(conversationId) {
  const box = document.getElementById("conv-messages");
  if (!box) return;
  try {
    const [conv, participants, messages] = await Promise.all([
      api("GET", "/api/conversations/" + conversationId),
      api("GET", "/api/conversations/" + conversationId + "/participants"),
      api("GET", "/api/conversations/" + conversationId + "/messages"),
    ]);
    box.innerHTML = messages.length
      ? messages.map(m => _renderConversationMessage(m, participants, conv)).join("")
      : '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>';
  } catch (e) {
    // 참가자가 아니게 됐거나 대화 자체가 지워졌으면 조용히 무시
    // (`refreshConversationMessages()`와 같은 처리).
  }
}

// 사용자 요청(2026-10-13, "claude artifact 수준으로") — 이미 렌더링된
// 미리보기 DOM을 그대로 복제해 전체화면 오버레이에 옮겨 붙인다(다시
// 파싱/렌더링할 필요 없음 — html은 iframe이라 복제해도 그대로 동작한다).
// 오버레이 엘리먼트 하나를 재사용한다(열 때마다 새로 만들지 않음).
let _artifactFullscreenEl = null;
function _ensureArtifactFullscreenEl() {
  if (_artifactFullscreenEl) return _artifactFullscreenEl;
  const overlay = document.createElement("div");
  overlay.className = "artifact-fullscreen-overlay";
  overlay.style.display = "none";
  overlay.innerHTML =
    '<div class="artifact-fullscreen-panel">' +
    '<button type="button" class="small artifact-fullscreen-close" aria-label="닫기">✕ 닫기</button>' +
    '<div class="artifact-fullscreen-body"></div>' +
    "</div>";
  document.body.appendChild(overlay);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay || ev.target.closest(".artifact-fullscreen-close")) {
      closeArtifactFullscreen();
    }
  });
  _artifactFullscreenEl = overlay;
  return overlay;
}
function openArtifactFullscreen(artifactId) {
  const preview = document.getElementById(artifactId + "-preview");
  if (!preview) return;
  const overlay = _ensureArtifactFullscreenEl();
  overlay.querySelector(".artifact-fullscreen-body").innerHTML = preview.innerHTML;
  overlay.style.display = "flex";
}
function closeArtifactFullscreen() {
  if (_artifactFullscreenEl) _artifactFullscreenEl.style.display = "none";
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeArtifactFullscreen();
});

// 결과물을 그대로 다운로드(2026-10-07, Tier 1 — "다운로드는 가능한지
// 확인해줘"에 대한 답). content_type별 확장자만 붙이고 Blob으로 바로
// 저장한다 — 서버에 파일로 저장하는 게 아니라 브라우저가 들고 있는
// 문자열을 그 순간 파일로 바꿔주는 것뿐이라 Tier 3(실제 파일 저장소)
// 없이도 가능하다.
const _CONTENT_TYPE_EXTENSIONS = { text: "txt", markdown: "md", html: "html", code: "txt" };
function downloadDeliverable(content, contentType, deliverableId) {
  const ext = _CONTENT_TYPE_EXTENSIONS[contentType] || "txt";
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "deliverable-" + deliverableId + "." + ext;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  // 사용자 요청(2026-10-12, "하나의 브라우저에서 두 개의 계정을 동시에 로그인") —
  // 쿠키는 브라우저 전체가 공유해 탭마다 다른 계정을 유지할 수 없다.
  // sessionStorage는 탭마다 독립이라, 로그인 시 받은 토큰을 여기 저장해두고
  // 매 요청마다 헤더로 보내면 탭 B에서 로그인/로그아웃해도 탭 A는 영향을 안 받는다.
  const token = sessionStorage.getItem("hair_token");
  if (token) opts.headers["Authorization"] = "Bearer " + token;
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* no body */ }
  if (!resp.ok) {
    const detail = (data && data.detail) ? data.detail : (resp.status + " " + resp.statusText);
    throw new Error(detail);
  }
  return data;
}

// 사용자 요청(2026-10-26, 외부 자료 업로드) — `api()`는 항상
// `Content-Type: application/json`을 세팅해 멀티파트 파일 업로드에 못
// 쓴다(서버가 바운더리를 못 찾아 폼 파싱이 깨진다). Content-Type을 직접
// 세팅하지 않아야 브라우저가 멀티파트 바운더리를 자동으로 넣어준다 —
// 그 외(토큰 헤더·에러 처리)는 `api()`와 동일하게 맞춘다.
async function apiUpload(path, formData) {
  const opts = { method: "POST", headers: {}, credentials: "same-origin", body: formData };
  const token = sessionStorage.getItem("hair_token");
  if (token) opts.headers["Authorization"] = "Bearer " + token;
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* no body */ }
  if (!resp.ok) {
    const detail = (data && data.detail) ? data.detail : (resp.status + " " + resp.statusText);
    throw new Error(detail);
  }
  return data;
}

function qs(path, params) {
  const usp = new URLSearchParams();
  for (const k in params) {
    if (params[k] !== undefined && params[k] !== null && params[k] !== "") usp.set(k, params[k]);
  }
  const s = usp.toString();
  return s ? (path + "?" + s) : path;
}

// 사용자 요청(2026-11-12, "꼭 필요한 메시지만 노출되도록") — #global-msg는
// 뷰포트에 고정된 토스트라(위 CSS 참고) 다음 메시지가 올 때까지 안 사라지면
// 화면에 계속 떠서 오히려 방해가 된다. 성공은 짧게, 오류는 좀 더 오래 보여준
// 뒤 자동으로 지운다 — 호출부(274곳)는 아무것도 안 바꿔도 된다. "login-msg"
// (로그인 폼 전용, 뷰포트 고정 아님)는 기존처럼 자동 소멸 없이 그대로 둔다.
let _globalMsgTimer = null;
function showMsg(elId, text, kind) {
  const el = document.getElementById(elId);
  if (elId === "global-msg" && _globalMsgTimer) {
    clearTimeout(_globalMsgTimer);
    _globalMsgTimer = null;
  }
  if (!text) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="msg ' + (kind || "ok") + '">' + esc(text) + "</div>";
  if (elId === "global-msg") {
    _globalMsgTimer = setTimeout(() => { el.innerHTML = ""; }, kind === "error" ? 8000 : 4000);
  }
}

/* 실제 LLM API를 호출해 오래 걸릴 수 있는 버튼(Crew 실행, AI 에이전트
   생성, 개별 실행, 지식베이스로 보내기)에 "처리 중" 표시를 준다 — 요청 중 버튼을
   비활성화해 중복 클릭도 막는다(사용자 요청, 2026-09-27, "실행 중임을
   알려주는 UI를 추가해줘"). */
async function withBusy(btn, busyLabel, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyLabel;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// 사용자 요청(2026-11-12, "UI/UX 개선") — 지금까지 파괴적/비용 발생
// 액션 확인을 전부 브라우저 네이티브 `confirm()`으로 했다(스타일링
// 불가·앱 전체를 막는 모달·나머지 UI와 톤이 다름). 커스텀 오버레이로
// 교체한다 — `.artifact-fullscreen-overlay`(이미 있는 유일한 fixed
// 오버레이)와 같은 시각 톤을 맞춘다. 반환값은 네이티브 confirm()과
// 동일하게 Promise<boolean>이라, 호출부는 `if (!window.confirm(msg))
// return;`을 `if (!(await confirmModal(msg))) return;`로만 바꾸면
// 된다(그 함수가 async가 아니었다면 그 함수 자체도 async로 바꿔야
// 함 — 호출부별로 직접 확인함).
function confirmModal(message, { danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-modal-overlay";
    overlay.innerHTML =
      '<div class="confirm-modal-box">' +
      '<p class="confirm-modal-text">' + esc(message) + '</p>' +
      '<div class="confirm-modal-actions">' +
      '<button type="button" class="small" data-confirm-cancel>취소</button>' +
      '<button type="button" class="small' + (danger ? " danger" : " primary") + '" data-confirm-ok>확인</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    const settle = (result) => { overlay.remove(); resolve(result); };
    overlay.querySelector("[data-confirm-ok]").onclick = () => settle(true);
    overlay.querySelector("[data-confirm-cancel]").onclick = () => settle(false);
    overlay.onclick = (ev) => { if (ev.target === overlay) settle(false); };
    const onKey = (ev) => {
      if (ev.key === "Escape") { settle(false); document.removeEventListener("keydown", onKey); }
    };
    document.addEventListener("keydown", onKey);
    overlay.querySelector("[data-confirm-ok]").focus();
  });
}

const LABELS = {
  human: "사람", agent: "AI 에이전트", team: "팀", pipeline: "파이프라인",
  brief: "업무 요청", assignment: "배정", deliverable: "결과물", human_pool: "사람 인력", agent_pool: "AI 에이전트",
  agent_execution_jobs: "AI 실행 작업",
  job: "생성 작업", job_item: "생성 항목", approval: "결재", user: "사용자",
  pending: "대기중", waiting: "대기(이전 단계)", approved: "승인됨", rejected: "반려됨", skipped: "건너뜀",
  requested: "요청됨", confirmed: "확정됨", superseded: "대체됨", cancelled: "취소됨",
  running: "진행중", succeeded: "성공", failed: "실패", partial: "일부 실패", completed: "완료",
  submitted: "제출됨", reviewed: "검토됨",
  available: "가능", unavailable: "불가", offboarded: "오프보딩됨",
  active: "활성", pending_onboarding: "온보딩 대기", deprecated: "비활성화됨",
  draft: "초안", pending_review: "리뷰 대기", revised: "수정됨",
  paused_for_approval: "승인 대기 중",
  // (2026-10-08, Phase 4) — 할 일 보드 상태/우선순위.
  backlog: "백로그", queued: "대기", in_progress: "진행중", done: "완료",
  low: "낮음", medium: "보통", high: "높음", urgent: "긴급",
  task_dependencies: "의존관계", tasks: "업무",
  // (2026-10-21, "Agent 직접 생성") — agent_pool.framework 표시용.
  manual: "직접 생성",
};
function label(v) { return LABELS[v] || v; }

function headerColClass(h) {
  // 사용자 보고(2026-10-18, 스크린샷 — "사용자 관리" 화면의 "상사 지정"/
  // "비밀번호 재설정"/"계정 상태 변경" 열에서 select·input·button이 서로
  // 겹쳐 보임) — 원인은 "동작" 열과 똑같다: table-layout:fixed에서 폭을
  // 안 정해주면 짧은 열(ID/권한 등)과 균등하게 나눠 가져가 버려, 그 안의
  // select/input/button이 셀 폭을 넘기고(overflow: visible 기본값) 옆
  // 칸 위로 그대로 흘러나온다. "동작"과 같은 폭(col-actions)을 준다.
  // 사용자 보고(2026-10-27, 스크린샷 — "LLM 설정 최적화" 일괄 화면의
  // "추천 Provider/Model" 열(모델 선택 <select>)이 "tuning 변경 필드"
  // 열과 겹쳐 보임) — 바로 위 두 사례와 원인이 같다.
  if (h === "동작" || h === "추천 Provider/Model") return "col-actions";
  // 사용자 보고(2026-10-27, 스크린샷 — "사용자 목록"에서 컬럼들이 너무
  // 넓어 테이블에 좌우 스크롤이 생김) — "상사 지정"/"비밀번호 재설정"/
  // "계정 상태 변경" 세 열은 바로 위 "동작"(col-actions, 260px)과 같은
  // 폭을 썼었는데, 각 칸엔 select/input+button 한 쌍만 있어(여러 버튼이
  // 쌓이는 "동작" 열과 다름) 그렇게 넓을 필요가 없었다 — table-layout:
  // auto로 바뀐 지금은(2026-10-27, 앞선 요청) 겹침 방지용 최소폭만
  // 있으면 되므로 더 좁은 전용 클래스로 줄인다(col-actions 자체는
  // "동작" 열에 그대로 둔다 — 그쪽은 버튼이 여러 개라 여전히 넓어야 함).
  if (h === "상사 지정" || h === "비밀번호 재설정" || h === "계정 상태 변경" || h === "사번 변경") return "col-actions-compact";
  // 사용자 보고(2026-10-14, 스크린샷 — "스킬" 열이 단어 하나씩
  // 줄바꿈될 만큼 좁게 눌려 있음) — table-layout:fixed에서 폭을
  // 안 정해주면 ID/상태처럼 짧은 열과 똑같이 나눠 가져가 버린다.
  if (h === "스킬") return "col-skills";
  // "모델 실행 후 쌓이는 정보"를 접어 놓는 열(AI 에이전트 관리) — 펼쳤을
  // 때 소속팀/비용/LLM/최근 산출물 같은 일반 텍스트가 읽을 수 있게
  // 줄바꿈되려면 col-skills보다 넉넉한 폭이 필요하다.
  if (h === "상세정보") return "col-detail";
  return "";
}

// 사용자 요청(2026-10-14, "각 메뉴의 목록 기능에 필터/소트 기능을
// 추가해줘") — 이 앱의 모든 목록 화면(23곳)이 전부 이 table() 헬퍼
// 하나를 공유한다. 호출부마다 따로 구현하는 대신 여기 한 곳에
// 검색창 + 정렬 가능한 헤더를 붙이면 전체 메뉴에 자동으로 퍼진다.
// 각 호출부는 이미 렌더링된 HTML 문자열(버튼 등)을 셀 값으로 넘기므로
// (구조화된 데이터가 아님), 필터/정렬은 **렌더링된 DOM의 textContent**
// 기준으로 동작한다 — 데이터 모델을 바꾸는 23곳 전면 리팩터링 없이도
// 전부 적용된다는 뜻이다. "동작" 열(버튼만 있는 열)은 정렬 대상에서
// 제외한다(`headerColClass()`가 이미 "동작"을 이름으로 식별하는 기존
// 관례를 그대로 재사용).
let _tableSeq = 0;

// 사용자 요청(2026-11-03, "도움말이 펼침으로 확인 하도록 UI 개선하고
// 기본으로 닫힘으로 헤줘") — 화면마다 있는 "상단 도움말"(.help-list)을
// 네이티브 <details>로 감싼다(기본 닫힘은 `open` 속성을 안 붙이는
// 것만으로 충분 — 별도 JS 상태 관리 불필요).
function helpBox(innerHtml) {
  return '<details class="help-box"><summary class="help-summary">도움말</summary>' + innerHtml + '</details>';
}

function table(headers, rows) {
  const tableId = "tbl-" + (++_tableSeq);
  const hasRows = rows.length > 0;
  let html = '<div class="table-wrap">';
  if (hasRows) {
    html += '<input type="search" class="table-filter" data-table-filter="' + tableId +
      '" placeholder="검색…" aria-label="목록 검색">';
  }
  html += '<table id="' + tableId + '"><thead><tr>';
  headers.forEach((h, i) => {
    const cls = headerColClass(h);
    const sortable = hasRows && h !== "동작";
    const thClass = [cls, sortable ? "sortable-col" : ""].filter(Boolean).join(" ");
    // 사용자 요청(2026-10-27, "컬럼폭 조정시에도 전체 폭이 영역을
    // 넘지 않도록") — 핸들은 "열의 오른쪽 끝"이 아니라 "열 i와 열
    // i+1 사이의 경계"다. 드래그하면 그 경계 양옆 두 열만 폭을
    // 맞바꾸므로(한쪽이 늘면 옆이 그만큼 줄어듦) 테이블 전체 폭은
    // 절대 안 바뀐다 — 그래서 마지막 열에는 핸들을 안 둔다(그 오른쪽엔
    // 맞바꿀 열이 없다).
    const isLast = i === headers.length - 1;
    html += "<th" + (thClass ? ' class="' + thClass + '"' : "") +
      (sortable ? ' data-sort-col="' + i + '" title="클릭해 정렬"' : "") +
      ">" + esc(h) + (sortable ? ' <span class="sort-indicator"></span>' : "") +
      (isLast ? "" : '<span class="col-resizer" title="드래그해 폭 조정"></span>') + "</th>";
  });
  html += "</tr></thead><tbody>";
  if (!hasRows) {
    html += '<tr><td colspan="' + headers.length + '" class="muted">데이터가 없습니다.</td></tr>';
  }
  for (const r of rows) {
    html += "<tr>";
    r.forEach((c, i) => {
      const cls = headerColClass(headers[i]);
      html += "<td" + (cls ? ' class="' + cls + '"' : "") + ">" + c + "</td>";
    });
    html += "</tr>";
  }
  html += "</tbody></table></div>";
  return html;
}

// 정렬 클릭·검색 입력 둘 다 렌더링된 테이블의 textContent만 본다 —
// 숫자처럼 보이는 열(모든 셀이 Number()로 변환되면)은 숫자 비교,
// 아니면 한국어 로케일 문자열 비교로 정렬한다. "데이터가 없습니다"
// 자리표시 행은 hasRows가 false일 때만 생기고 그때는 헤더 자체가
// 정렬 불가능하므로 따로 걸러낼 필요가 없다.
function _sortTableByColumn(th) {
  const tbl = th.closest("table");
  if (!tbl) return;
  const colIndex = Number(th.dataset.sortCol);
  const tbody = tbl.querySelector("tbody");
  // 사용자 보고(2026-10-14, 스크린샷 — 정렬했더니 "구분/내용/타깃/동적
  // 테스트" 같은 엉뚱한 행이 섞여 테이블이 깨져 보임) — 실측으로 확인한
  // 원인: "AI 에이전트 관리"의 "최근 산출물"(상세정보 안)은 이제
  // marked.js로 실제 마크다운 **표**까지 렌더링한다(이전엔 불가능했던
  // 기능) — 그 결과 셀 안에 중첩된 <table>이 생길 수 있다.
  // `tbody.querySelectorAll("tr")`은 자손 전체를 뒤져 그 중첩 표의
  // <tr>까지 집어오고, 정렬 뒤 `tbody.appendChild(tr)`로 그 행들을
  // **바깥 테이블의 직계 자식**으로 떼어내 옮겨버렸다 — 중첩 표가
  // 산산조각나 바깥 테이블 행처럼 보인 것. 바깥 tbody의 **직계 자식만**
  // (`tbody.children`) 대상으로 삼아야 중첩 표는 손대지 않는다.
  const rows = Array.from(tbody.children);
  const nextDir = th.dataset.sortDir === "asc" ? "desc" : "asc";
  tbl.querySelectorAll("th[data-sort-col]").forEach(h => {
    h.removeAttribute("data-sort-dir");
    const indicator = h.querySelector(".sort-indicator");
    if (indicator) indicator.textContent = "";
  });
  th.dataset.sortDir = nextDir;
  const indicator = th.querySelector(".sort-indicator");
  if (indicator) indicator.textContent = nextDir === "asc" ? "▲" : "▼";

  const cellText = (tr) => {
    const cell = tr.children[colIndex];
    return cell ? cell.textContent.trim() : "";
  };
  const isNumericLike = (v) => v === "" || v === "-" || !isNaN(Number(v.replace(/,/g, "")));
  const allNumeric = rows.every(tr => isNumericLike(cellText(tr)));
  rows.sort((a, b) => {
    const va = cellText(a);
    const vb = cellText(b);
    let cmp;
    if (allNumeric) {
      const toNum = (v) => (v === "" || v === "-") ? -Infinity : Number(v.replace(/,/g, ""));
      cmp = toNum(va) - toNum(vb);
    } else {
      cmp = va.localeCompare(vb, "ko");
    }
    return nextDir === "asc" ? cmp : -cmp;
  });
  rows.forEach(tr => tbody.appendChild(tr));
}

// 사용자 요청(2026-10-27, "테이블 컬럼 폭을 마우스로 조정 가능하게
// 개선해줘") — table() 헬퍼가 공유되는 23곳 전부, 위 정렬/필터처럼
// 전역 위임 하나로 처리한다(호출부마다 배선 불필요). 드래그를 시작하는
// 순간 그 테이블의 모든 열 폭을 "지금 렌더된 그대로" px로 얼리고
// table-layout을 fixed로 바꾼다 — 그래야 한 열만 끌 때 다른 열들이
// auto 레이아웃 재계산으로 같이 흔들리지 않는다(평소엔 내용 기준
// 가변폭을 유지하다가, 실제로 조정한 테이블만 그 순간부터 고정폭).
// 핸들은 "열 i / 열 i+1 사이 경계"라, 드래그는 그 둘의 폭만 맞바꾼다
// (한쪽 + delta, 다른쪽 - delta).
//
// 사용자 보고(2026-10-27, "컬럼폭 조정시에도 전체 폭이 넘지 않도록" →
// "핸들을 드래그하는 순간 바로 스크롤" → "비용 메뉴는 기본으로도
// 스크롤, 다른 메뉴는 여전히" → "아직도 발생") — 이 테이블/컬럼 폭을
// .table-wrap보다 좁게 강제로 욱여넣으려는 시도(열 폭 비례 축소,
// table{max-width:100%} 등)를 몇 차례 더 해봤지만 전부 효과가 없어
// 전부 되돌렸다. 원인은 CSS의 근본 규칙이다 — 셀 안에 줄어들 수 없는
// 내용(`<input style="width:120px">`처럼 폭이 고정된 폼 컨트롤, 버튼
// 여러 개)이 있으면 그 "최소 콘텐츠 폭(min-content)"이 `max-width`나
// JS로 지정한 어떤 값보다도 우선한다 — 브라우저는 max-width가 더
// 좁아도 min-content 밑으로는 못 줄인다(table-layout 값과도 무관).
// 즉 "버튼/입력창이 있는 열이 여럿인 테이블은 어느 시점부터는 좌우
// 스크롤이 생기는 게 CSS 스펙상 정상 동작"이고, `.table-wrap`의
// `overflow-x: auto`(아래 CSS)가 바로 그 표준 해법이다 — 페이지
// 밖으로 터지지 않고 테이블 박스 안에서만 스크롤된다. 그래서 이
// 리사이즈 로직은 "드래그로 폭을 조정하는 기능" 하나만 하고, "스크롤
// 자체를 없애겠다"는 시도는 더 안 한다(이미 있는 폭을 얼릴 뿐 축소는
// 안 함) — 정말 스크롤을 줄이려면 그 테이블의 셀 내용 자체(버튼 개수,
// 입력창 폭)를 줄이는 수밖에 없다(예: 이미 compact 처리한 "상사
// 지정"류 열처럼, 테이블별로 따로 손봐야 함).
let _colResizeState = null;
document.addEventListener("mousedown", (ev) => {
  const handle = ev.target.closest(".col-resizer");
  if (!handle) return;
  const th = handle.closest("th");
  const next = th && th.nextElementSibling;
  const tbl = th && th.closest("table");
  if (!th || !next || !tbl) return;
  ev.preventDefault();
  tbl.querySelectorAll(":scope > thead > tr > th").forEach(h => {
    h.style.width = h.offsetWidth + "px";
  });
  tbl.style.tableLayout = "fixed";
  handle.classList.add("resizing");
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
  _colResizeState = {
    th, next, startX: ev.clientX,
    startWidth: th.offsetWidth, startNextWidth: next.offsetWidth,
    handle,
  };
});
document.addEventListener("mousemove", (ev) => {
  if (!_colResizeState) return;
  const { th, next, startX, startWidth, startNextWidth } = _colResizeState;
  const minDelta = 40 - startWidth;
  const maxDelta = startNextWidth - 40;
  const delta = Math.min(maxDelta, Math.max(minDelta, ev.clientX - startX));
  th.style.width = (startWidth + delta) + "px";
  next.style.width = (startNextWidth - delta) + "px";
});
document.addEventListener("mouseup", () => {
  if (!_colResizeState) return;
  _colResizeState.handle.classList.remove("resizing");
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  _colResizeState = null;
});

document.addEventListener("input", (ev) => {
  const filterInput = ev.target.closest("[data-table-filter]");
  if (!filterInput) return;
  const tbl = document.getElementById(filterInput.dataset.tableFilter);
  const tbody = tbl && tbl.querySelector("tbody");
  if (!tbody) return;
  const q = filterInput.value.trim().toLowerCase();
  // _sortTableByColumn()과 같은 이유로 `tbody.querySelectorAll("tbody tr")`
  // 대신 직계 자식(tbody.children)만 본다 — 셀 안에 중첩된 마크다운 표의
  // 행까지 숨김/표시 대상으로 잘못 집어오지 않도록.
  Array.from(tbody.children).forEach(tr => {
    const text = tr.textContent.toLowerCase();
    tr.style.display = (!q || text.includes(q)) ? "" : "none";
  });
});

/* Phase 5(사용자 요청, 2026-09-28, "협업 워크스페이스 UX 개선" — Skill
   자동완성). skill_pool에 등록된 이름을 <datalist>로 내려줘 스킬을 자유
   텍스트로 입력하는 곳마다 브라우저 기본 자동완성이 뜨게 한다 — Skill
   주입이 skill_pool 이름과 "정확히 일치"해야 매칭되므로, 오타로
   조용히 매칭이 안 되는 걸 줄이는 게 목적이다.

   **정정(2026-10-06, 사용자 보고 "하나의 스킬을 선택한 후에는 다른
   스킬을 펼쳐 확인할 수 없어")**: 콤마로 여러 개를 입력하는 단일
   텍스트필드(d-skills/hp-skills)는 브라우저가 입력값 전체를 기준으로
   자동완성을 맞춰서, 이미 뭔가 입력한 뒤 콤마 다음 항목부터는 드롭다운이
   거의 안 열렸다 — 매번 입력을 지우고 다시 써야 했다. `skillPickerHtml()`/
   `initSkillPicker()`로 작은 태그 입력 위젯을 추가해 해결한다: 값을
   하나씩 입력(Enter 또는 "추가" 버튼)할 때마다 칩으로 빠지고 입력창이
   비워지므로, 다음 항목을 고를 때도 매번 전체 자동완성 목록이 뜬다.
   skill_pool에 없는 자유 텍스트(예: 기존 데이터의 "인사 정책")도 그대로
   칩으로 추가할 수 있어 기존 자유 입력 동작은 그대로 유지한다. 단일 값
   필드인 검색의 s-skill은 애초에 문제가 없어 그대로 둔다. */
async function skillDatalistHtml() {
  let names;
  try {
    names = await api("GET", "/api/skill-pool/names");
  } catch (e) {
    return "";
  }
  return '<datalist id="skill-datalist">' +
    names.map(n => '<option value="' + esc(n) + '">').join("") +
    "</datalist>";
}

/* 사용자 요청(2026-10-06, "운영 관리에 사용 가능한 ollama 모델 및
   openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을 불러오는
   방식으로 UI/UX를 개선" → "에이전트 생성, 관리, Crew 실행 등에도
   연동되도록 적용해줘" → "진한 드롭다운으로 강제 선택") — 처음엔
   datalist 자동완성(참고용, 자유 입력 유지)으로 연결했으나, 사용자가
   실제 `<select>` 드롭다운으로 바꿔 등록된 모델 중에서 고르게 하는
   쪽을 선택했다. 레지스트리가 비어 있거나 원하는 모델이 아직 없는
   경우를 위해 "직접 입력..." 옵션을 두고, 고르면 그 옆에 텍스트
   입력이 나타난다 — 등록을 강제하지 않으면서도 평소엔 드롭다운으로
   오타/오입력을 막는다. "AI 에이전트 생성"·"AI 에이전트 관리(모델
   설정)"·"Crew 실행"·협업 워크스페이스 4곳이 전부 이 셋을 공유한다. */
async function fetchModelsByProvider() {
  const byProvider = { anthropic: [], openai: [], ollama: [] };
  let models;
  try {
    // 사용자 보고(2026-10-22, "member 권한으로 대화시 모델 프로바이더를
    // 선택해도 등록된 모델이 불려오지 않음") — `/api/model-registry`는
    // admin+manager 전용이라 member/viewer는 403을 받고 여기서 조용히
    // 빈 목록으로 폴백했다(드롭다운이 "기본값 사용"/"직접 입력..."만
    // 보임). `/api/model-registry/names`(모든 role 허용, provider+모델명
    // 만 노출)로 바꾼다.
    models = await api("GET", "/api/model-registry/names");
  } catch (e) {
    return byProvider;
  }
  models.forEach(m => {
    if (byProvider[m.provider]) byProvider[m.provider].push(m.name);
  });
  return byProvider;
}

function modelSelectHtml(selectId) {
  return '<select id="' + selectId + '"><option value="">기본값 사용</option>' +
    '<option value="__custom__">직접 입력...</option></select> ' +
    '<input id="' + selectId + '-custom" placeholder="모델명 직접 입력" style="display:none;width:160px">';
}

// Tier 2(2026-10-07, "Tier 2까지 진행해줘") — "결과물 작성"/"LLM으로
// 도와줘"/"AI 에이전트 실행"/"팀 Agent 실행" 4곳 모두 이 select 하나를
// 재사용한다 — 작성자가 고르는 형식과 Agent/LLM에게 지시하는 형식이
// 같아야 자연스럽기 때문이다(`deliverables._CONTENT_TYPE_INSTRUCTIONS`).
const CONTENT_TYPE_LABELS = { text: "일반 텍스트", markdown: "Markdown", html: "HTML", code: "코드" };
function contentTypeSelectHtml(selectId) {
  return '<select id="' + selectId + '" title="결과물 형식">' +
    Object.keys(CONTENT_TYPE_LABELS).map(v =>
      '<option value="' + v + '">' + esc(CONTENT_TYPE_LABELS[v]) + '</option>'
    ).join("") +
    '</select>';
}

// providerSelectId가 바뀔 때마다 modelSelectId의 옵션을 그 provider의
// 등록된 모델로 다시 채운다. `initialValue`는 이미 저장된 모델명(예:
// "AI 에이전트 관리"의 기존 llm_model)을 미리 선택해두기 위함 —
// 레지스트리에 없는(과거에 자유 입력으로 저장된) 값이면 "직접 입력..."
// 을 골라 그 값을 미리 채워 보여준다(데이터 손실 없음).
function wireProviderModelSelect(providerSelectId, modelSelectId, modelsByProvider, initialValue) {
  const providerSelect = document.getElementById(providerSelectId);
  const modelSelect = document.getElementById(modelSelectId);
  const customInput = document.getElementById(modelSelectId + "-custom");
  if (!providerSelect || !modelSelect || !customInput) return;

  const rebuildOptions = (preselect) => {
    const names = modelsByProvider[providerSelect.value] || [];
    const useCustom = !!preselect && !names.includes(preselect);
    modelSelect.innerHTML = '<option value="">기본값 사용</option>' +
      names.map(n => '<option value="' + esc(n) + '"' + (n === preselect ? " selected" : "") + '>' + esc(n) + "</option>").join("") +
      '<option value="__custom__"' + (useCustom ? " selected" : "") + '>직접 입력...</option>';
    customInput.style.display = useCustom ? "" : "none";
    customInput.value = useCustom ? preselect : "";
  };

  modelSelect.addEventListener("change", () => {
    const isCustom = modelSelect.value === "__custom__";
    customInput.style.display = isCustom ? "" : "none";
    if (!isCustom) customInput.value = "";
  });
  providerSelect.addEventListener("change", () => rebuildOptions(null));
  rebuildOptions(initialValue || null);
}

function modelSelectValue(modelSelectId) {
  const select = document.getElementById(modelSelectId);
  if (!select) return null;
  if (select.value === "__custom__") {
    const custom = document.getElementById(modelSelectId + "-custom");
    return custom && custom.value.trim() ? custom.value.trim() : null;
  }
  return select.value || null;
}

/* 여러 스킬을 하나씩 추가/삭제하는 작은 태그 입력 위젯. `hiddenId` 입력은
   기존 코드(제출 핸들러의 `.value.split(",")`)와 호환되도록 그대로
   콤마구분 문자열을 담아둔다 — 폼 제출 로직은 손대지 않는다. */
function skillPickerHtml(pickerId, hiddenId) {
  return '<div class="skill-picker" id="' + pickerId + '">' +
    '<div class="skill-picker-row">' +
    '<input type="text" class="skill-picker-input" list="skill-datalist" placeholder="스킬 입력 후 Enter 또는 추가">' +
    '<button type="button" class="small skill-picker-add">추가</button>' +
    '</div>' +
    '<div class="skill-picker-chips"></div>' +
    '</div>' +
    '<input type="hidden" id="' + hiddenId + '">';
}

/* 반환하는 `flush()`는 "추가" 버튼을 안 누르고 입력창에 값을 남겨둔 채
   제출 버튼을 바로 눌러도 놓치지 않도록, 제출 핸들러 맨 앞에서 명시적으로
   호출해 현재 입력을 칩으로 반영한다(같은 엘리먼트에 등록된 capture
   리스너가 bubble 리스너보다 먼저 실행된다고 가정하지 않는다 — AT_TARGET
   단계에선 등록 순서대로 실행되므로 암묵적 순서에 기대지 않는다). */
function initSkillPicker(pickerId, hiddenId, initial) {
  const root = document.getElementById(pickerId);
  const hidden = document.getElementById(hiddenId);
  const input = root.querySelector(".skill-picker-input");
  const chips = root.querySelector(".skill-picker-chips");
  const state = [...(initial || [])];

  const render = () => {
    hidden.value = state.join(",");
    chips.innerHTML = state.map(s =>
      '<span class="skill-chip">' + esc(s) +
      '<button type="button" data-skill="' + esc(s) + '" title="제거">×</button></span>'
    ).join("");
    chips.querySelectorAll("[data-skill]").forEach(btn => {
      btn.onclick = () => {
        const i = state.indexOf(btn.dataset.skill);
        if (i >= 0) state.splice(i, 1);
        render();
      };
    });
  };

  const addCurrent = () => {
    const v = input.value.trim();
    if (v && !state.includes(v)) state.push(v);
    input.value = "";
    render();
  };

  root.querySelector(".skill-picker-add").onclick = addCurrent;
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addCurrent(); }
  });

  render();
  return { flush: addCurrent };
}

let ME = null;

/* 코드 재점검(2026-09-26) 사용자 요청 — 관리자 기능과 일반 업무 기능을
   화면상에서도 명확히 구분한다.

   사용자 요청(2026-09-29, "HAIR 관점에서 외부 사례들을 조사하고... 관리자와
   사용자 고유 업무와 공통 업무를 구분해줘" → "로그인한 계정 유형에 맞춰
   메뉴 노출을 개선해줘")에 따라 group을 둘에서 셋으로 늘렸다
   (관리자/사용자 고유·공통 업무 실측 재분류 그대로 반영):
   - "user": 담당자가 자기 업무를 처리·요청하는 화면(역할마다 안의 동작은
     다르지만 탭 자체는 전 역할에 열려 있다).
   - "common": 역할과 무관한 순수 조회/참조 화면(Viewer 포함 전부 동일하게
     봄) — 원래 "user" 그룹에 섞여 있어 "내 업무"와 성격이 다른데 구분이
     안 됐었다.
   - "admin": 조직/리소스를 운영·관리하는 화면. 예전엔 "관리자 도구"라고만
     불러 매니저도 대부분 쓸 수 있다는 걸 라벨만 보고는 알 수 없었다
     (실측: 11개 중 10개가 admin+manager 공용) — 라벨을 "운영 관리"로
     바꿔 그 실제 구성을 반영한다. adminOnly: true는 그 안에서도 진짜
     ADMIN 전용인 항목(현재 "사용자 관리" 하나)만 배지로 구분 표시한다.

   사용자 요청(2026-09-29, "내 업무, 공용 조회, 운영 관리 각 카테고리...
   프로세스 순에 맞춰서, 또는 자주 사용하는 업무가 먼저 위치하도록
   버튼 배치가 되어 있는지 확인해줘" → "진행해줘")에 따라 "운영 관리"
   그룹 순서를 조정했다 — 실측 비교 결과 "내 업무"/"공용 조회" 그룹은
   이미 합리적(인박스가 맨 앞인 건 프로세스 1단계가 아니라 "가장 자주
   보는 화면 우선"이라는 의도된 배치)이었지만, "운영 관리"는 프로세스
   맵(https://claude.ai/artifact/8dQVis1XEEEZdcdTsytvpv) 13단계에서
   "팀 현황 모니터링"으로 하나로 묶은 대시보드·비용·공정성 지표·활동
   기록 4개가 실제 화면에선 대시보드만 맨 앞에 있고 나머지 3개는
   8~10번째로 흩어져 있었다. 모니터링 4개를 앞으로 모으고, 그 뒤는
   프로세스 순서(배정 → Agent/사람 준비 → Pool 표준화 → 계정 관리)를
   그대로 따른다.

   사용자 요청(2026-10-13, "상단 메뉴 버튼의 순서도 검토해줘" →
   "humanpool/agentpool 순서만 바꿔줘")로 한 번 더 조정 — 프로세스 맵
   16단계가 "AI 에이전트 생성 → AI 에이전트 관리"를 화살표로 이은 한
   묶음으로, 18단계가 "도구/스킬 Pool"과 "사람 인력 관리"를 같은 단계로
   나란히 묶어뒀는데, 기존 순서(jobs → humanpool → agentpool →
   poolregistry)는 humanpool이 그 사이에 끼어 두 묶음을 모두 깨고
   있었다. humanpool과 agentpool 자리를 바꿔 jobs→agentpool(16단계),
   humanpool→poolregistry(18단계) 인접성을 둘 다 맞춘다. */
const TABS = [
  { id: "conversations", label: "대화", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderConversations },
  { id: "inbox", label: "인박스", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderInbox },
  { id: "mywork", label: "내 업무", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderMyWork },
  { id: "board", label: "할 일 보드", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBoard },
  { id: "gallery", label: "갤러리", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderGallery },
  { id: "briefs", label: "업무 요청", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBriefs },
  { id: "approvals", label: "결재", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderApprovals },
  { id: "performance", label: "성과 리뷰", group: "user", roles: ["admin", "manager", "member"], render: renderPerformance },
  { id: "search", label: "인력 검색", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderSearch },
  { id: "orgchart", label: "조직도", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderOrgChart },
  { id: "manual", label: "이용 가이드", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderManual },
  { id: "dashboard", label: "대시보드", group: "admin", roles: ["admin", "manager"], render: renderDashboard },
  { id: "cost", label: "비용", group: "admin", roles: ["admin", "manager"], render: renderCost },
  { id: "fairness", label: "공정성 지표", group: "admin", roles: ["admin", "manager"], render: renderFairness },
  { id: "audit", label: "활동 기록", group: "admin", roles: ["admin", "manager"], render: renderAudit },
  { id: "match", label: "리소스 매칭/배정", group: "admin", roles: ["admin", "manager"], render: renderMatch },
  { id: "jobs", label: "AI 에이전트 생성", group: "admin", roles: ["admin", "manager"], render: renderJobs },
  { id: "agentpool", label: "AI 에이전트 관리", group: "admin", roles: ["admin", "manager"], render: renderAgentPool },
  { id: "agentoptimize", label: "LLM 설정 최적화", group: "admin", roles: ["admin"], adminOnly: true, render: renderAgentOptimization },
  { id: "humanpool", label: "사람 인력 관리", group: "admin", roles: ["admin", "manager"], render: renderHumanPool },
  { id: "poolregistry", label: "도구/스킬 Pool", group: "admin", roles: ["admin", "manager"], render: renderPoolRegistry },
  { id: "teams", label: "팀 관리", group: "admin", roles: ["admin", "manager"], render: renderTeams },
  { id: "pipelines", label: "파이프라인 관리", group: "admin", roles: ["admin", "manager"], render: renderPipelines },
  { id: "modelregistry", label: "모델 레지스트리", group: "admin", roles: ["admin"], adminOnly: true, render: renderModelRegistry },
  { id: "knowledgebase", label: "지식 베이스", group: "admin", roles: ["admin", "manager"], render: renderKnowledgeBase },
  { id: "crew", label: "Crew 빌더", group: "admin", roles: ["admin", "manager"], render: renderCrewBuilder },
  { id: "admin", label: "사용자 관리", group: "admin", roles: ["admin"], adminOnly: true, render: renderAdmin },
];

async function boot() {
  try {
    ME = await api("GET", "/api/me");
    document.getElementById("login-view").style.display = "none";
    document.getElementById("app-view").style.display = "block";
    document.getElementById("whoami").textContent = ME.username + " (" + ME.role + ")";
    buildNav();
    selectTab(TABS.find(t => t.roles.includes(ME.role)).id);
    startMentionNotificationPolling();
  } catch (e) {
    ME = null;
    stopMentionNotificationPolling();
    document.getElementById("app-view").style.display = "none";
    document.getElementById("login-view").style.display = "flex";
  }
}

// (2026-10-08, Phase 5) — **탭 재구성**:
// 기존 3구역("내 업무"/"공용 조회"/"운영 관리")을 "오늘 할 일"/"설정"
// 2구역으로 합친다(설계 문서: "신규 사용자가 매일 보는 화면을 좁힌다").
// `TABS` 배열의 `group` 값(user/common/admin) 자체는 그대로 둔다 —
// "admin" 그룹(Pool 5종·Team·Model Registry 등 자주 안 바뀌는 설정성
// 화면)을 "설정"으로, 나머지(user+common — 업무 요청·성과 리뷰·
// 인력 검색·조직도처럼 역할과 무관하게 자주 참조하는 화면 포함)를
// "오늘 할 일"로 묶는다. 설계 문서가 예시로 든 "인박스·내 업무·할 일
// 보드·결재" 4개만 남기고 나머지(업무 요청 등)를 "설정"으로 보내는
// 더 좁은 해석도 가능했지만, 매일 쓰는 "업무 요청"을 "설정"에 숨기면
// 오히려 더 불편해질 위험이 커서 "admin 전용 여부"를 기준으로
// 묶었다 — 이 해석은 의도적 판단이었다는 걸 정직하게 기록해둔다.
//
// 사용자 요청(2026-11-02, "설정 메뉴 배치를 Group 1 리소스 관리...
// Group 4 운영관리 순으로 조정하고 각 그룹별 분리될 수 있도록") —
// 위에서 하나로 뭉쳐 있던 "설정"(`group==="admin"`인 16개 탭)을 4개
// 하위 그룹으로 다시 나눈다. `TABS`의 `group` 필드는 "오늘 할 일" vs
// "설정" 1차 분류에만 쓰이므로 그대로 두고, 여기 `SETTINGS_GROUPS`가
// "설정" 내부의 2차 배치(순서+묶음)만 따로 정의한다 — 두 분류 기준이
// 섞이면 나중에 셋 중 어느 쪽을 고치는 건지 헷갈리기 때문에 분리해
// 둔다. "공정성 지표"는 사용자 지정 목록에 없었으나(사용자 결정)
// 운영 성격상 운영관리 끝에 둔다.
//
// 사용자 요청(2026-11-02, "그라운딩 관리, 모델관리, 운영관리 3메뉴
// 영역은 숨겨져 있다가 펼쳐 보는 방식으로") — 가장 자주 쓰는 "리소스
// 관리"만 항상 펼쳐두고 나머지 3그룹은 기본 접힌 상태로 시작한다.
// 커스텀 토글 상태/아이콘을 직접 관리하는 대신 네이티브
// `<details>/<summary>`(이 파일의 다른 곳에서도 이미 쓰는 패턴 — 예:
// "팀에 배정 요청", "실행 이력 보기")를 쓴다 — 펼침 여부를 브라우저가
// 알아서 관리해주고, JS 쪽엔 상태 변수가 하나도 늘지 않는다.
// `collapsible: true`인 그룹만 `<details>`+`<summary>`로, 아니면 기존
// 그대로 `<div>`+평범한 `<span>` 라벨로 렌더링한다(`buildNav()` 참고).
//
// 사용자 요청(2026-11-02, "'인력 매칭/배정'을 '리소스 매칭/배정'으로
// 변경, 가장 먼저 위치 및 구분선 추가, agent 관리 문구 삭제") — 가장
// 자주 누르는 동작이라 그룹 맨 앞으로 옮기고 구분선(`"__divider__"`
// 토큰 — `buildNav()`가 라벨 없는 `.nav-divider`만 그린다)으로
// 나머지와 시각적으로 나눈다. "Agent 관리" 하위 소제목은 더 이상
// 안 쓴다 — 나머지(사람 인력 관리/AI 에이전트 생성·관리/Crew 빌더/팀
// 관리)는 전부 같은 레벨의 평범한 버튼으로 나열한다. 라벨 텍스트
// 자체("리소스 매칭/배정")는 `TABS`의 `match` 항목에서 바뀐다.
const SETTINGS_GROUPS = [
  {
    label: "리소스 관리",
    entries: [
      "match", "__divider__",
      "humanpool", "__divider__",
      "jobs", "agentpool", "crew", "__divider__",
      "teams",
    ],
  },
  {
    label: "그라운딩 관리",
    entries: ["pipelines", "poolregistry", "knowledgebase"],
    collapsible: true,
  },
  {
    label: "모델 관리",
    entries: ["agentoptimize", "modelregistry"],
    collapsible: true,
  },
  {
    label: "운영 관리",
    entries: ["dashboard", "cost", "fairness", "audit", "admin"],
    collapsible: true,
  },
];

function buildNav() {
  const nav = document.getElementById("tabs");
  nav.innerHTML = "";

  const todayTabs = TABS.filter(
    t => (t.group === "user" || t.group === "common") && t.roles.includes(ME.role)
  );
  const byId = {};
  TABS.forEach(t => { byId[t.id] = t; });

  const makeButton = (t) => {
    const btn = document.createElement("button");
    btn.textContent = t.label;
    if (t.adminOnly) {
      const badge = document.createElement("span");
      badge.className = "badge-adminonly";
      badge.textContent = "관리자 전용";
      btn.appendChild(badge);
    }
    // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
    // 결과물 확인이 어려워") — "인박스" 탭 밖에 있으면 결재 대기/실행
    // 실패/완료 소식을 전혀 알 길이 없었다. 기존 멘션 알림 폴링(20초)을
    // `_pollInboxBadge()`로 확장해 이 탭 버튼에 미읽은 건수를 직접
    // 띄운다 — 기본은 숨김(hidden), 폴링이 처음 돌면 바로 갱신된다.
    if (t.id === "inbox") {
      const badge = document.createElement("span");
      badge.className = "badge-inbox-count";
      badge.id = "inbox-badge-count";
      badge.hidden = true;
      btn.appendChild(badge);
    }
    btn.dataset.tab = t.id;
    btn.onclick = () => selectTab(t.id);
    return btn;
  };

  const addGroup = (label, tabs, cls) => {
    if (!tabs.length) return;
    const row = document.createElement("div");
    row.className = "nav-group" + (cls ? " " + cls : "");
    const tag = document.createElement("span");
    tag.className = "nav-label";
    tag.textContent = label;
    row.appendChild(tag);
    for (const t of tabs) row.appendChild(makeButton(t));
    nav.appendChild(row);
  };

  addGroup("오늘 할 일", todayTabs, "");

  for (const group of SETTINGS_GROUPS) {
    const row = document.createElement(group.collapsible ? "details" : "div");
    const tag = document.createElement(group.collapsible ? "summary" : "span");
    tag.className = "nav-label";
    tag.textContent = group.label;
    const children = [tag];
    let hasVisible = false;
    for (const entry of group.entries) {
      if (entry === "__divider__") {
        const divider = document.createElement("span");
        divider.className = "nav-divider";
        children.push(divider);
        continue;
      }
      const t = byId[entry];
      if (t && t.roles.includes(ME.role)) {
        children.push(makeButton(t));
        hasVisible = true;
      }
    }
    if (!hasVisible) continue;
    row.className = "nav-group admin-group" + (group.collapsible ? " nav-collapsible" : "");
    children.forEach(c => row.appendChild(c));
    nav.appendChild(row);
  }
}

let currentTab = null;

async function selectTab(id, opts) {
  // "경량 폴링" — 대화 화면을 벗어나면
  // 어느 쪽에서 열었든(독립 "대화" 탭/Task 워크스페이스 임베드) 반드시
  // 멈춘다(단일 전역 타이머라 여기 한 곳에서만 끊어도 충분하다).
  _stopConversationPolling();
  // 사용자 요청(2026-10-14, "모델설정 버튼 클릭시 웹 화면이 가장 위로
  // 자동이동하고 있어, 다른 버튼들도 모두 점검해줘") — 버그 원인: 거의
  // 모든 버튼 핸들러가 성공 후 `refresh()`(=같은 탭을 `selectTab()`으로
  // 다시 그림)를 부르는데, 이 함수가 매번 먼저 `content.innerHTML`을
  // 아주 짧은 "불러오는 중..." 문구로 비웠다 — 긴 목록 아래쪽 버튼을
  // 눌렀다가 스크롤이 남아있는 채로 내용이 한 줄짜리로 줄어들면 브라우저가
  // 스크롤 위치를 0에 가깝게 강제로 잘라낸다(실제 내용이 다시 채워져도
  // 스크롤은 복구되지 않는다). "다른 탭으로 전환"할 때는 맨 위에서
  // 시작하는 게 맞지만 "같은 탭을 제자리에서 새로고침"할 때는 아니다 —
  // `refresh()`만 `preserveScroll: true`로 불러 이 둘을 구분한다.
  const isTabSwitch = currentTab !== id;
  const scrollY = opts && opts.preserveScroll ? window.scrollY : 0;
  currentTab = id;
  for (const btn of document.querySelectorAll("#tabs button")) {
    btn.classList.toggle("active", btn.dataset.tab === id);
  }
  showMsg("global-msg", "", "ok");
  const tab = TABS.find(t => t.id === id);
  const content = document.getElementById("content");
  if (isTabSwitch) {
    content.innerHTML = "<p class=\\"muted\\">불러오는 중...</p>";
  }
  try {
    await tab.render(content);
  } catch (e) {
    content.innerHTML = "";
    showMsg("global-msg", e.message, "error");
  }
  if (opts && opts.preserveScroll) {
    window.scrollTo(0, scrollY);
  }
}

function refresh() { if (currentTab) selectTab(currentTab, { preserveScroll: true }); }

document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  showMsg("login-msg", "", "ok");
  try {
    const data = await api("POST", "/login", {
      username: document.getElementById("login-username").value,
      password: document.getElementById("login-password").value,
    });
    // 탭별 다계정 로그인 — 토큰을 이 탭의 sessionStorage에만 저장한다.
    sessionStorage.setItem("hair_token", data.token);
    await boot();
  } catch (e) {
    showMsg("login-msg", e.message, "error");
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await api("POST", "/logout");
  sessionStorage.removeItem("hair_token");
  await boot();
});

/* ---------- 대화 중심 작업공간(Conversational Workspace,
   2026-10-08, 사용자 요청 "로그인하면 기본적으로 대화창이 주어지고...
   사람이나 에이전트(에이전트 그룹)를 호출하거나 skill을 사용하여
   업무를 수행"). TABS 배열 맨 앞(로그인 직후 첫 화면)에 둔다. ---------- */

let _conversationPollTimer = null;
// 사용자 요청(2026-11-11, "개발업무 Skill의 경우 Default로 대화창에
// 보이지 않도록 설정하고, 필요시 노출하도록") — 대화 화면을 나갔다
// 다시 들어와도 유지되도록 모듈 스코프에 둔다(기본값 false — 개발업무
// 스킬은 default-off, 체크박스로 켜면 그 세션 동안 유지).
let _convShowDevSkills = false;

function _stopConversationPolling() {
  if (_conversationPollTimer) {
    clearInterval(_conversationPollTimer);
    _conversationPollTimer = null;
  }
}

// 사용자 보고(2026-10-14, 스크린샷 — 메시지를 보낸 뒤 진행 중임을 알
// 수 없음) — 기존 "응답을 기다리는 중…"은 `#global-msg`(페이지 맨
// 위)에만 떴다. 대화를 스크롤해 입력창 근처를 보고 있는 사용자에게는
// 화면 밖이라 안 보였다 — 메시지 목록 바로 아래, 입력창 바로 위에
// 똑같은 역할을 하는 인라인 표시(+ 전송 버튼 비활성화)를 추가했다.
//
// 사용자 요청(2026-10-18, "에이전트가 실행되는 경우 어떤 에이전트가
// 어떤 동작을 하고 있는지 알 수 있도록... 펼침/닫힘 UI로... 에이전트
// 그룹 포함") — 메시지 하나로 여러 Job이 한꺼번에 트리거될 수 있다
// (복수 @멘션, 또는 @Team 멘션 하나가 그 팀 전원을 한 Job으로 묶어
// 실행). "N건 진행 중"이라는 뭉뚱그린 문구 대신, 어떤 Agent/Team이
// 각각 실행 중/완료/실패인지 펼쳐 볼 수 있는 목록으로 바꾼다. 다만
// CrewAI Crew(그룹) 실행은 `crew.kickoff()` 자체가 동기 호출이라
// 중간 단계 이벤트를 스트리밍해주지 않는다 — "그룹 안에서 지금 어느
// 멤버가 뭘 하는지"까지는 실측으로 확인 불가능해서, 있는 그대로
// "그룹 전체가 실행 중/완료"로만 정직하게 보여준다(없는 정보를
// 지어내지 않는다).
function _conversationJobLabel(triggered, participants) {
  if (triggered.trigger_type === "agent_mention") {
    const p = participants.find(p => p.participant_type === "agent" && p.participant_id === triggered.agent_id);
    return { icon: "🤖", text: (p && p.display_name) ? p.display_name : "Agent #" + triggered.agent_id };
  }
  if (triggered.trigger_type === "team_mention") {
    const p = participants.find(p => p.participant_type === "team" && p.participant_id === triggered.team_id);
    const name = (p && p.display_name) ? p.display_name : "Team #" + triggered.team_id;
    return { icon: "👥", text: name + " (그룹 전체 실행 — 멤버별 진행 상황은 CrewAI가 제공하지 않음)" };
  }
  return { icon: "🧠", text: "기본 AI" };
}

function _renderConversationPendingPanel(jobMeta) {
  const indicator = document.getElementById("conv-pending-indicator");
  if (!indicator) return;
  indicator.style.display = "";
  const rows = jobMeta.map(m =>
    '<li data-job-progress-id="' + m.jobId + '">' +
    '<span class="job-status-badge running">● 실행 중</span>' +
    m.icon + ' ' + esc(m.text) +
    '</li>'
  ).join("");
  indicator.innerHTML =
    '<details class="pending-indicator-panel" open>' +
    '<summary><span class="spinner"></span>응답 생성 중(' + jobMeta.length +
    '건)… Agent/Team 호출은 몇 분 걸릴 수 있습니다</summary>' +
    '<ul class="job-progress-list">' + rows + '</ul>' +
    '</details>';
  const sendBtn = document.querySelector('#conv-message-form button[type="submit"]');
  if (sendBtn) sendBtn.disabled = true;
}

function _updateConversationJobStatus(jobId, status, error) {
  const li = document.querySelector('[data-job-progress-id="' + jobId + '"]');
  if (!li) return;
  const badge = li.querySelector(".job-status-badge");
  if (!badge) return;
  if (status === "succeeded") {
    badge.className = "job-status-badge succeeded";
    badge.textContent = "✓ 완료";
    return;
  }
  // 사용자 보고(2026-10-18, "실패 메시지 노출시 깨져 보임") — 배지에
  // 에러 문구 전체를 넣지 않는다(배지는 "✗ 실패"로 짧게 유지). 자세한
  // 내용은 별도 줄(.job-error-detail, CSS에서 flex-basis:100%)로
  // 떨어뜨려 폭 안에서 정상적으로 줄바꿈되게 한다.
  badge.className = "job-status-badge failed";
  badge.textContent = "✗ 실패";
  if (error) {
    const detail = document.createElement("div");
    detail.className = "job-error-detail";
    detail.textContent = error;
    li.appendChild(detail);
  }
}

// 전부 끝나면 접어서(펼침/닫힘의 "닫힘") 이번 메시지에 어떤 Agent/Team이
// 관여했는지 기록만 남긴다 — 다음 메시지를 보내면 이 패널 전체가
// 새로 교체된다(무한히 쌓이지 않음).
function _finalizeConversationPendingPanel(allSucceeded) {
  const indicator = document.getElementById("conv-pending-indicator");
  const details = indicator && indicator.querySelector("details");
  if (details) {
    details.open = false;
    const summary = details.querySelector("summary");
    if (summary) {
      summary.textContent = (allSucceeded ? "✓ 응답 완료" : "⚠ 일부 실패") + " — 펼쳐서 진행 내역 보기";
    }
  }
  const sendBtn = document.querySelector('#conv-message-form button[type="submit"]');
  if (sendBtn) sendBtn.disabled = false;
}

// 사용자 보고(2026-10-08, "응답을 기다리는 중…으로 나오고 대화에
// 모델의 응답이 표시되지 않음") — 원래는 메시지를 보낸 뒤 "응답을
// 기다리는 중…" 문구만 한 번 띄우고 끝이라, 실제로 응답이 도착해도
// (Team 멘션처럼 CrewAI가 몇 분씩 걸리는 경우 특히, 실측 — 약
// 6분) 그 문구가 그대로 남아 있어 "멈췄다/안 된다"로 오인하게 했다.
// Task 워크스페이스의 `pollExecutionJob()`과 같은 패턴을 대화 쪽에도
// 적용해 완료/실패를 실제로 확인하고 상태 문구를 갱신한다.
async function pollConversationJob(conversationId, jobId, intervalMs, timeoutMs) {
  // 사용자 보고(2026-10-14, "일부 응답이 실패했습니다: timed out") 재발 —
  // 백엔드 Ollama 타임아웃을 300초에서 600초로 올리면서(`llm.py`의
  // `_call_ollama()` 참고, 실측: 느린 로컬 35B 모델이 337.7초 만에
  // 정상 완료됨을 확인) 이 폴링 포기 시한도 같이 올린다 — 백엔드가
  // 아직 기다리는 중인데 프런트가 먼저 포기하지 않도록 항상 백엔드
  // 타임아웃보다 여유 있게 길게 유지한다.
  // 재발(2026-10-19, "@자율 개발 크루" 실측) — 매니저가 로컬 Ollama
  // 모델로 실제 위임까지 하면(여러 차례 LLM 왕복) 단일 호출보다
  // 훨씬 오래 걸린다. 백엔드 Job 자체는 계속 돌고 있는데
  // (`conversation_jobs.status='running'`, 실측 12분 넘게도 진행 중)
  // 프런트가 700초 만에 먼저 포기해 "실패"로 잘못 보였다 — 백엔드는
  // 멈추지 않았으므로 폴링 포기 시한을 30분으로 넉넉히 늘린다.
  // 사용자 보고(2026-10-28, "프레젠테이션 자료 작성자" 응답 생성 15분
  // 초과 실패) 대응으로 백엔드가 deep_reasoning tier Agent에는 30분
  // (1800초)까지 기다리도록 늘렸다 — 이 폴링 포기 시한이 그와 똑같으면
  // 백엔드가 막 완료되는 순간 프런트가 먼저 포기할 수 있으므로, 항상
  // 백엔드 최댓값보다 여유 있게 길게 유지하는 기존 원칙대로 40분으로
  // 같이 올린다.
  const deadline = Date.now() + (timeoutMs || 2400000); // Team 멘션(CrewAI, 로컬 모델 위임 체인)은 수십 분 걸릴 수 있음
  while (Date.now() < deadline) {
    const jobs = await api("GET", "/api/conversations/" + conversationId + "/jobs");
    const job = jobs.find(j => j.id === jobId);
    if (job && job.status !== "running") return job;
    await new Promise(r => setTimeout(r, intervalMs || 2000));
  }
  throw new Error("응답 완료 확인 시간이 초과됐습니다 — 잠시 후 새로고침해 주세요.");
}

// 사용자 요청(2026-10-19, "대화를 닫고 다시 연 상태에서 아직 결과
// 완료 전인 상황에서 에이전트가 작업 중임을 알려주는 메시지가
// 출력되는지 확인하고 개선해줘") — 기존엔 이 진행 패널이 메시지를
// "보내는" 그 순간에만 떴다(`conv-message-form` submit 핸들러 안에서만
// 호출). 메시지를 보낸 뒤 탭을 닫거나 다른 화면으로 갔다가 Job이 아직
// 끝나기 전에 같은 대화를 다시 열면, 그 시점엔 이미 "보내는 순간"이
// 지났으므로 이 함수가 한 번도 안 불려 진행 중 표시가 전혀 없었다 —
// 화면만 보면 아무 일도 안 하는 것처럼 보였다(실측: `자율 개발 크루`
// 22분짜리 실행 도중 재현). 보내는 흐름과 "다시 열었더니 아직 실행
// 중"인 흐름이 완전히 같은 로직(패널 표시→폴링→완료 반영)을 쓰도록
// 공용 함수로 뺀다 — `renderConversationDetail()`이 대화를 열 때마다
// `GET .../jobs`로 실행 중인 Job이 있는지 직접 확인해 이 함수를 부른다.
function _watchConversationJobs(conversationId, jobEntries, participants, conv) {
  if (!jobEntries || !jobEntries.length) return;
  const jobMeta = jobEntries.map(j => {
    const label = _conversationJobLabel(j, participants);
    return { jobId: j.job_id, icon: label.icon, text: label.text };
  });
  _renderConversationPendingPanel(jobMeta);
  Promise.allSettled(
    jobMeta.map(m =>
      pollConversationJob(conversationId, m.jobId).then(job => {
        _updateConversationJobStatus(m.jobId, job.status, job.error);
        refreshConversationMessages(conversationId, participants, conv);
        return job;
      }).catch(e => {
        _updateConversationJobStatus(m.jobId, "failed", e.message);
        throw e;
      })
    )
  ).then((settled) => {
    const failed = settled.filter(s => s.status === "rejected" || (s.value && s.value.status === "failed"));
    _finalizeConversationPendingPanel(failed.length === 0);
    refreshConversationMessages(conversationId, participants, conv);
    showMsg(
      "global-msg",
      failed.length
        ? "일부 응답이 실패했습니다 — 위 진행 내역을 펼쳐서 확인하세요."
        : "응답이 도착했습니다.",
      failed.length ? "error" : "ok"
    );
  });
}

// ---------- 전역 @멘션 브라우저 알림(사용자 요청, 2026-10-14, "호출
// 받은 사람에게 브라우저 알림이 오고, 알림 클릭시 대화 참가 가능하도록
// 개선해줘") — 특정 대화 화면을 보고 있지 않아도(다른 탭을 보고 있어도)
// 동작해야 하므로 `boot()`에서 로그인 성공 시 한 번만 시작하는 전역
// 폴링이다(개별 대화 화면의 `_conversationPollTimer`와는 별개). ----------

let _mentionNotifyTimer = null;
// 버그 수정(2026-10-14, 사용자 보고 "호출이 안 되는 거 같아" — 실측
// 재현) — 원래는 "최초 폴링은 알림 없이 기준점만 잡는다"는 클라이언트
// 메모리 커서로 중복 알림을 막았는데, 그 탭이 멘션이 이미 온 *뒤에*
// 열리면(실제 재현: admin이 먼저 세 번 호출하고 pm_kim은 그 뒤에 로그인)
// 그 멘션들이 전부 "최초 폴링"으로 분류돼 영영 알림이 안 떴다. 이제
// "안 읽음" 판정 자체를 서버가 들고 있어서(`conversation_participants.
// last_read_message_id` — 그 대화를 실제로 열면 갱신됨) 서버가 돌려주는
// 건 전부 "진짜 안 읽은 멘션"이다. 이 Set은 그래도 같은 폴링 주기
// 내에서(아직 그 대화를 안 열어 계속 "안 읽음"으로 돌아오는 동안) 20초
// 마다 같은 멘션을 또 알림으로 띄우지 않기 위한 세션 한정 중복 방지일
// 뿐이다(페이지 새로고침하면 리셋돼도 무해 — 서버가 "안 읽음"이면 다시
// 한 번 알림이 뜨는 정도).
let _notifiedMentionIds = new Set();

function _requestNotificationPermissionIfNeeded() {
  if (typeof Notification === "undefined") return; // 미지원 브라우저는 조용히 건너뜀
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

async function _openConversationFromNotification(conversationId) {
  window.focus();
  await selectTab("conversations");
  renderConversationDetail(conversationId, "conversation-detail");
}

async function _pollMyMentions() {
  let mentions;
  try {
    mentions = await api("GET", "/api/conversations/my-mentions");
  } catch (e) {
    return; // 네트워크 일시 단절 등 — 다음 주기에 다시 시도
  }
  if (!mentions.length) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  for (const m of mentions) {
    if (_notifiedMentionIds.has(m.message_id)) continue;
    _notifiedMentionIds.add(m.message_id);
    const notif = new Notification("💬 \\"" + m.conversation_title + "\\"에서 호출됨", {
      body: m.content,
      tag: "hair-mention-" + m.message_id,
    });
    notif.onclick = () => {
      _openConversationFromNotification(m.conversation_id);
      notif.close();
    };
  }
}

// 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
// 결과물 확인이 어려워(숨겨져 있는 느낌)") — "인박스" 탭을 직접 열어야만
// 결재 대기/실행 실패/완료 소식을 알 수 있었다(다른 탭에 있으면 전혀
// 모름). 기존 멘션 폴링과 같은 주기(20초)로 `GET /api/inbox`를 같이
// 불러 탭 버튼에 건수 배지를 띄운다 — 새 폴링 체계를 따로 만들지 않고
// 같은 타이머에 얹는다.
async function _pollInboxBadge() {
  const badge = document.getElementById("inbox-badge-count");
  if (!badge) return; // 아직 로그인 전/로그아웃 뒤 — nav가 없음
  let items;
  try {
    items = await api("GET", "/api/inbox");
  } catch (e) {
    return; // 네트워크 일시 단절 등 — 다음 주기에 다시 시도
  }
  if (items.length) {
    badge.textContent = items.length > 99 ? "99+" : String(items.length);
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

function startMentionNotificationPolling() {
  if (_mentionNotifyTimer) return; // 이미 돌고 있으면 중복 시작하지 않음
  _requestNotificationPermissionIfNeeded();
  _pollMyMentions();
  _pollInboxBadge();
  _mentionNotifyTimer = setInterval(() => { _pollMyMentions(); _pollInboxBadge(); }, 20000);
}

function stopMentionNotificationPolling() {
  if (_mentionNotifyTimer) {
    clearInterval(_mentionNotifyTimer);
    _mentionNotifyTimer = null;
  }
  _notifiedMentionIds = new Set(); // 다음 로그인(다른 계정일 수 있음)은 새로 시작
}

async function renderConversations(content) {
  _stopConversationPolling();
  const list = await api("GET", "/api/conversations");
  content.innerHTML =
    '<section class="card"><h2>대화</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>로그인하면 바로 쓸 수 있는 대화 공간입니다 — 사람·Agent·Agent 그룹(Team)을 불러 대화하듯 업무를 처리하세요.</li>' +
      '<li>대화와 업무 요청(Brief)은 서로 독립적인 두 처리 방식입니다 — 대화 내용을 업무 요청으로 쓰고 싶다면 업무 요청 작성란에 직접 옮겨 적으세요.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="new-conversation-form">' +
    '<input id="new-conversation-title" placeholder="새 대화 제목(선택)" style="flex:1">' +
    '<button class="primary" type="submit">새 대화 시작</button>' +
    '</form>' +
    table(["ID", "제목", "시작일", "동작"], list.map(c => [
      c.id,
      esc(c.title || "(제목 없음)") + (c.linked_task_id ? ' <span class="muted">(업무 #' + c.linked_task_id + ')</span>' : ''),
      esc(c.created_at),
      '<button class="small" data-open-conversation="' + c.id + '">열기</button> ' +
      '<button class="small danger" data-leave-conversation="' + c.id + '">나가기</button> ' +
      (c.created_by === ME.id
        ? '<button class="small danger" data-delete-conversation="' + c.id + '">삭제</button>'
        : ''),
    ])) +
    '<div id="conversation-detail"></div>' +
    '</section>';

  document.getElementById("new-conversation-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const title = document.getElementById("new-conversation-title").value.trim();
    try {
      const conv = await api("POST", "/api/conversations", { title });
      showMsg("global-msg", "새 대화를 시작했습니다.", "ok");
      await renderConversations(content);
      renderConversationDetail(conv.id, "conversation-detail");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-open-conversation]").forEach(btn => {
    btn.onclick = () => renderConversationDetail(Number(btn.dataset.openConversation), "conversation-detail");
  });
  content.querySelectorAll("[data-delete-conversation]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 대화를 메시지·참가자까지 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/conversations/" + btn.dataset.deleteConversation);
        showMsg("global-msg", "대화를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-leave-conversation]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 대화에서 나갑니다. 계속할까요?"))) return;
      try {
        const result = await api("POST", "/api/conversations/" + btn.dataset.leaveConversation + "/leave");
        showMsg(
          "global-msg",
          result.conversation_deleted ? "대화에서 나갔습니다(마지막 참가자라 대화도 삭제됐습니다)." : "대화에서 나갔습니다.",
          "ok"
        );
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

async function refreshConversationMessages(conversationId, participants, conv) {
  const box = document.getElementById("conv-messages");
  if (!box) return;
  try {
    const messages = await api("GET", "/api/conversations/" + conversationId + "/messages");
    box.innerHTML = messages.length ? messages.map(m => _renderConversationMessage(m, participants, conv)).join("")
      : '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>';
  } catch (e) {
    // 참가자가 아니게 됐으면(제거됨) 403 — 조용히 무시.
  }
}

// 사용자 보고(2026-10-14, "대화창에 사용중인 모델이 명시되도록 UI를
// 개선해줘") — Agent 멘션 응답은 지금까지 "(AI)"만 보이고 실제로 어떤
// provider/model이 썼는지 전혀 안 보였다(기본 AI만 conv.llm_model을
// 보여줬는데, 그나마도 "그 메시지를 만들 때 실제로 쓰인 값"이 아니라
// "지금 대화 설정값"이라 사용자가 나중에 모델을 바꾸면 과거 메시지
// 라벨까지 거슬러 바뀌는 부정확함이 있었다). 이제 메시지마다 실제로
// 쓰인 provider/model이 `conversation_messages.llm_provider/llm_model`
// 에 저장되므로(`conversations.py`의 `_finish_job_with_message()`/
// `run_team_mention_job()` 참고) 그 값을 우선 쓰고, 마이그레이션 이전
// 과거 메시지(그 컬럼이 비어 있음)만 기존처럼 대화 설정값으로 폴백한다.
function _conversationModelBadge(provider, model) {
  if (!model) return "";
  return provider ? provider + "/" + model : model;
}

function _conversationAuthorLabel(m, participants, convLlmModel) {
  const authorType = m.author_type, authorId = m.author_id;
  // 실측으로 발견한 비대칭(2026-10-13) — Agent 작성자는 participants에서
  // display_name을 찾아 보여주는데, 사람 작성자는 찾아보지도 않고 바로
  // "사용자 #3" 식으로 ID만 보여주고 있었다. 본인이면 로그인 아이디를,
  // 아니면 participants에서 같은 방식으로 찾는다(둘 다 없을 때만 ID로
  // 폴백 — 예: 대화를 나간 뒤에도 과거 메시지는 남는 경우).
  if (authorType === "human") {
    if (ME && authorId === ME.id) return esc(ME.username);
    const hp = participants.find(p => p.participant_type === "human" && p.participant_id === authorId);
    return hp && hp.display_name ? esc(hp.display_name) : "사용자 #" + authorId;
  }
  const badge = _conversationModelBadge(m.llm_provider, m.llm_model) ||
    (authorId === conversationsDefaultAiAuthorId ? (convLlmModel || "") : "");
  if (authorId === conversationsDefaultAiAuthorId) {
    return "AI" + (badge ? " (" + esc(badge) + ")" : "");
  }
  const p = participants.find(p => p.participant_type === "agent" && p.participant_id === authorId);
  if (!p) return "AI 에이전트 #" + authorId;
  return esc(p.display_name) + " (AI" + (badge ? " · " + esc(badge) : "") + ")";
}

// `conversation_messages.author_id`의 예약 sentinel — 실존 agent_pool
// 행이 없는 "대화 자체의 기본 AI"를 가리킨다(`conversations.py`의
// `DEFAULT_AI_AUTHOR_ID`와 반드시 같은 값이어야 한다 — agent_pool.id
// 는 AUTOINCREMENT라 0이 될 수 없어 안전한 sentinel이다).
const conversationsDefaultAiAuthorId = 0;

// 사용자 요청(2026-10-14, "단위 메시지 삭제 기능") — 본인이 보낸 사람
// 메시지는 본인이, 그 외(AI/Agent 응답 포함)는 대화 생성자가 지울 수
// 있다(`conversations.delete_message()`와 같은 권한 — 서버가 최종
// 검증하므로 여기선 버튼 노출 여부만 결정한다).
// 사용자 요청(2026-10-18, "@자율 개발 크루" 결과물 품질 분석 후속) —
// Gate A-G가 fail해도 대화창 경로에서는 조용히 버려지던 문제의 최소
// 안전장치. 측정된 Gate 중 몇 개가 통과했는지만 간단히 보여준다(상세
// 점수는 과하다 — "Crew 실행" 관리자 화면에만 그 수준 상세를 둔다).
function _conversationGateBadge(m) {
  if (!m.gate_scores) return "";
  const measured = Object.values(m.gate_scores).filter(g => !g.not_measured);
  if (!measured.length) return "";
  const passed = measured.filter(g => g.passed).length;
  const allPass = passed === measured.length;
  return ' <span class="gate-badge ' + (allPass ? "gate-ok" : "gate-warn") + '" ' +
    'title="측정된 Gate A-G 중 통과한 비율(참고용 — Task 협업워크스페이스의 정식 Gate 기록과는 별개)">' +
    (allPass ? "Gate " : "⚠ Gate ") + passed + "/" + measured.length + " 통과" +
    "</span>";
}

// 실측으로 확인한 문제(2026-10-18) — "자율 개발 크루"가 매니저를 통해
// 실행됐는데도 매니저가 위임 없이 혼자 처리한 경우가 있었다("자율
// 개발"이라는 이름과 달리 실제로는 1인 작업). 몇 건 위임됐는지를
// 투명하게 보여준다.
function _conversationDelegationBadge(m) {
  if (m.delegation_count === null || m.delegation_count === undefined) return "";
  if (m.delegation_count > 0) {
    return ' <span class="gate-badge gate-ok">위임 ' + m.delegation_count + '건</span>';
  }
  return ' <span class="muted" style="font-size:0.85em">위임 0건</span>';
}

// 사용자 요청(2026-10-19, "HTML 표시시 css, script 등 실행가능하도록
// 개선 가능한지 확인해줘") — 실측 재현: "자율 개발 크루"가 ```html
// 코드펜스 없이 `<!DOCTYPE html>`로 바로 시작하는 완성된 게임을 응답
// 하면, 대화 메시지는 항상 `renderContent(m.content)`(contentType
// 미지정 → 마크다운 경로)를 타서 marked가 이 raw HTML을 그대로
// 통과시키고 DOMPurify가 `<script>`(와 경우에 따라 `<style>`)를
// 안전을 위해 제거해버린다 — 결과물이 캔버스만 텅 빈 채 스타일도
// 동작도 없는 반쪽짜리로 보였다(스크린샷으로 실측). 메시지 본문이
// 통째로 HTML 문서(또는 그걸 감싼 코드펜스)로 "시작"하면
// `renderContent(raw, "html")`(Task 산출물이 이미 쓰는, `sandbox=
// "allow-scripts"` iframe 안에서 CSS/script까지 전부 실행하는 안전한
// 경로)로 보낸다 — 설명과 코드가 섞인 일반 응답(예: 기획서 §5에 코드가
// 일부 포함된 경우)은 `<!DOCTYPE html>`로 시작하지 않으므로 그대로
// 마크다운 경로를 탄다(오탐 거의 없음).
function _looksLikeFullHtmlDocument(raw) {
  const unwrapped = stripOuterFence((raw || "").trim());
  return /^<!DOCTYPE\\s+html/i.test(unwrapped) || /^<html[\\s>]/i.test(unwrapped);
}

// 사용자 보고(2026-10-19, 스크린샷 — "@자율 개발 크루 위 기획을
// 구현해줘"처럼 멘션 대상과 실제 요청 문장이 구분 없이 붙어 보임) —
// 본문 중 실제로 이 대화에 초대된 참가자를 가리키는 "@이름"만(원문
// 추측이 아니라 백엔드 `_detect_mentions()`와 똑같이 참가자 목록
// 기준) 받는 쪽 색과 맞춘 pill로 감싼다. 긴 이름부터 치환해야
// "자율 개발 크루 테스트"가 "자율 개발 크루"에 먼저 부분 매칭돼
// 잘리는 걸 막는다.
function _highlightMentions(content, participants) {
  const mentions = (participants || [])
    .filter(p => p.display_name)
    .map(p => ({
      marker: "@" + p.display_name,
      cls: p.participant_type === "human" ? "mention-human" : "mention-agent",
    }))
    .sort((a, b) => b.marker.length - a.marker.length);
  let result = content;
  mentions.forEach(({ marker, cls }) => {
    if (!result.includes(marker)) return;
    result = result.split(marker).join(
      '<span class="mention-tag ' + cls + '">' + esc(marker) + '</span>'
    );
  });
  return result;
}

// 사용자 요청(2026-10-22, "호출된 사람,에이전트가 대화 내용과
// 구분되도록 ... 입력창에서도 적용해줘") — `_highlightMentions()`와
// 같은 매칭 로직이지만 두 가지가 다르다: (1) 입력창은 보내기 전 raw
// 텍스트를 `marked`/DOMPurify 없이 곧장 mirror div의 innerHTML에
// 꽂으므로, 멘션이 아닌 나머지 텍스트도 먼저 전부 `esc()`해야 한다
// (`_highlightMentions()`는 이후 markdown 렌더링 파이프라인을 믿고
// 멘션이 아닌 부분은 이스케이프하지 않는다 — 입력창엔 그 안전망이
// 없다). (2) pill(.mention-tag, padding 있는 inline-block)을 쓰면
// 그 구간만 글자 폭이 달라져 투명 input 위의 실제 캐럿 위치와 mirror
// 텍스트가 어긋나므로, 폭을 안 바꾸는 font-weight+color만 쓴다.
function _highlightMentionsForInput(content, participants) {
  const escaped = esc(content || "");
  const mentions = (participants || [])
    .filter(p => p.display_name)
    .map(p => ({
      marker: esc("@" + p.display_name),
      cls: p.participant_type === "human" ? "mention-inline-human" : "mention-inline-agent",
    }))
    .sort((a, b) => b.marker.length - a.marker.length);
  let result = escaped;
  mentions.forEach(({ marker, cls }) => {
    if (!result.includes(marker)) return;
    result = result.split(marker).join('<span class="' + cls + '">' + marker + '</span>');
  });
  return result;
}

// 타이핑 중인 input 위에 겹친 투명 오버레이(mirror) div를 실시간으로
// 동기화한다 — IME(한글) 조합 중에도 `input` 이벤트가 그대로 발생하므로
// 별도 처리가 필요 없다. 가로 스크롤(긴 텍스트가 input 폭을 넘었을 때)도
// `scrollLeft`를 그대로 맞춰준다.
function setupMentionInputMirror(inputEl, mirrorEl, participants) {
  function sync() {
    mirrorEl.innerHTML = _highlightMentionsForInput(inputEl.value, participants);
    mirrorEl.scrollLeft = inputEl.scrollLeft;
  }
  inputEl.addEventListener("input", sync);
  inputEl.addEventListener("keyup", sync);
  inputEl.addEventListener("click", sync);
  inputEl.addEventListener("scroll", sync);
  sync();
  // `insertMention()`(드롭다운 클릭으로 멘션 삽입)은 `inputEl.value`를
  // 직접 대입하는데, 이건 `input` 이벤트를 발생시키지 않는다(vanilla JS
  // 속성 — 사용자 타이핑만 이벤트를 낸다) — 호출부가 `onMentionSelected`
  // 콜백에서 이 `sync`를 직접 불러줘야 드롭다운으로 멘션을 고른 직후에도
  // mirror가 갱신된다.
  return { sync };
}

// 사용자 요청(2026-10-21, "Layer 4") — `who`는 이미 `esc()` 처리된
// 표시 이름 문자열("AI (claude-sonnet-5)", "코드리뷰 Agent (AI ·
// gpt-4o-mini)", "사용자 #3" 등) — 괄호 뒤 provider/model 부가정보는
// 이니셜에 불필요해 `" ("` 앞부분만 쓴다. 기본 AI는 한 글자보다
// 식별하기 쉽게 "AI" 그대로 보여준다.
// 알려진 한계: `who`가 이미 escape된 문자열이라 이름이 `<`/`&`/`"`로
// 시작하면 이니셜이 "&"로 보일 수 있다(실제로는 거의 없는 edge case
// — 별도 처리 안 함).
function _conversationAuthorInitial(who) {
  const core = who.split(" (")[0].trim();
  return core === "AI" ? "AI" : core.slice(0, 1).toUpperCase();
}

function _renderConversationMessage(m, participants, conv) {
  const side = m.author_type === "agent" ? "chat-agent" : "chat-human";
  const who = _conversationAuthorLabel(m, participants, conv ? conv.llm_model : null);
  // 사용자 요청(2026-10-19, "대화 초대자... 대화 내용과 구분해서
  // 가독성을 높여줘") — 작성자 이름을 카드 왼쪽 테두리와 같은 색으로
  // 굵게 표시해 본문과 한눈에 구분되게 한다.
  const whoClass = m.author_type === "agent" ? "msg-author-agent" : "msg-author-human";
  const avatarClass = m.author_type === "agent" ? "msg-avatar-agent" : "msg-avatar-human";
  const skillBadge = m.used_skill
    ? ' <span class="gate-badge gate-ok">스킬: ' + esc(m.used_skill) + '</span>'
    : '';
  const canDelete = conv && ME && (
    (m.author_type === "human" && m.author_id === ME.id) || conv.created_by === ME.id
  );
  const deleteBtn = canDelete
    ? ' <button type="button" class="small danger" data-delete-message="' +
      m.conversation_id + ':' + m.id + '">삭제</button>'
    : '';
  // 사용자 요청(2026-11-10, "대화... 등에서 생성한 최종결과물(필요시
  // 지정한 결과물)이 갤러리에 가도록") — 대화의 모든 메시지가 자동으로
  // 결과물이 되지는 않는다(탐색적 잡담까지 쌓이면 갤러리가 지저분해짐)
  // — 참가자가 "이건 남길 만하다" 고른 메시지만 명시적으로 저장한다.
  // 사용자 요청(2026-11-10, "'결과물로 저장' 버튼을 '갤러리로 보내기'로
  // 이름을 바꿔야 함 — 어디로 가는지 이름에서 바로 알 수 있어야 한다"
  // — HAIR-Platform 전체에서 "OOO로 보내기" 명명을 통일한다: 갤러리
  // 행선지는 "갤러리로 보내기", 지식베이스 행선지는 "지식베이스로
  // 보내기"(아래), Agent 관리 화면의 "지식 색인"도 같은 라운드에서
  // "지식베이스로 보내기"로 맞춘다).
  const saveBtn = conv && ME
    ? ' <button type="button" class="small" data-save-deliverable="' +
      m.conversation_id + ':' + m.id + '">갤러리로 보내기</button>'
    : '';
  // 이 메시지를 지식베이스(VectorDB)에 색인 — 실제 임베딩 API를
  // 호출해 비용이 발생하므로(agent_runtime.index_knowledge()/
  // POST .../index-knowledge와 동일 전제) admin/manager 전용으로
  // 좁힌다. 갤러리 저장(비용 없는 단순 텍스트 복사)과는 권한 범위가
  // 다르다.
  const knowledgeBtn = conv && ME && ["admin", "manager"].includes(ME.role)
    ? ' <button type="button" class="small" data-save-knowledge="' +
      m.conversation_id + ':' + m.id + '">지식베이스로 보내기</button>'
    : '';
  const isFullHtmlDoc = _looksLikeFullHtmlDocument(m.content);
  const contentType = isFullHtmlDoc ? "html" : undefined;
  const bodyContent = isFullHtmlDoc ? m.content : _highlightMentions(m.content, participants);
  return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted"><span class="msg-avatar ' + avatarClass + '">' +
    esc(_conversationAuthorInitial(who)) + '</span>' +
    '<strong class="' + whoClass + '">' + who + '</strong>' + skillBadge +
    _conversationGateBadge(m) + _conversationDelegationBadge(m) + ' · ' + esc(m.created_at) +
    saveBtn + knowledgeBtn + deleteBtn + '</div>' +
    '<div>' + renderContent(bodyContent, contentType) + '</div>' +
    '</div>';
}

async function renderConversationDetail(conversationId, containerId) {
  _stopConversationPolling();
  const detail = document.getElementById(containerId);
  if (!detail) return;
  detail.dataset.conversationId = String(conversationId);
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';

  const modelsByProvider = await fetchModelsByProvider();
  let conv, participants, messages, skillNames, runningJobs;
  try {
    conv = await api("GET", "/api/conversations/" + conversationId);
    participants = await api("GET", "/api/conversations/" + conversationId + "/participants");
    messages = await api("GET", "/api/conversations/" + conversationId + "/messages");
    skillNames = await api(
      "GET",
      "/api/skill-pool/names?conversation=true" + (_convShowDevSkills ? "&include_dev=true" : "")
    );
    // 사용자 요청(2026-10-19, "대화를 닫고 다시 연 상태에서 아직 결과
    // 완료 전인 상황에서 작업 중임을 알려주는 메시지가 출력되는지") —
    // 이 대화를 열 때마다 아직 안 끝난 Job이 있는지 직접 확인한다
    // (이전엔 메시지를 "보내는" 그 순간에만 진행 패널을 띄워서, 보낸
    // 뒤 탭을 닫았다가 Job이 끝나기 전에 다시 열면 진행 중이라는
        // 표시가 전혀 없었다).
    runningJobs = (await api("GET", "/api/conversations/" + conversationId + "/jobs"))
      .filter(j => j.status === "running");
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  const participantRows = participants.map(p =>
    '<li>' + esc(label(p.participant_type)) + ' ' + esc(p.display_name || ("#" + p.participant_id)) + '</li>'
  ).join("");

  const messageCards = messages.map(m => _renderConversationMessage(m, participants, conv)).join("");

  detail.innerHTML =
    '<section class="card">' +
    '<h3>' + esc(conv.title || "(제목 없음)") +
    (conv.linked_task_id ? ' <span class="muted">업무 #' + conv.linked_task_id + ' 전용 대화</span>' : '') +
    // 사용자 요청(2026-10-10, "대화 세션 삭제/나가기 기능을 구현해줘")
    // — "삭제"(연쇄 삭제, 되돌릴 수 없음)는 만든 사람(created_by)만
    // 쓸 수 있다(`conversations.delete_conversation()`과 같은 권한
    // 경계). "나가기"는 만든 사람 포함 참가자 누구나 쓸 수 있다 —
    // 처음엔 만든 사람에게 삭제만 보여줬는데, 그러면 다른 참가자가
    // 남아 있어도 "그냥 빠지기"를 못 하고 삭제(전체 파괴)만 고를 수
    // 있는 게 됐다(Playwright로 실제 클릭해보며 발견) — 만든 사람도
    // `leave_conversation()`을 쓸 수 있으므로 버튼도 둘 다 보여준다.
    ' <button class="small danger" id="conv-leave-btn">나가기</button>' +
    (conv.created_by === ME.id
      ? ' <button class="small danger" id="conv-delete-btn">대화 삭제</button>'
      : '') +
    '</h3>' +
    '<div class="inline">' +
    // "AI 에이전트 관리"(provider-{id} select)와 같은 관례 — provider
    // 선택을 JS `.value =` 사후 대입이 아니라 마크업의 `selected`
    // 속성으로 미리 심어둔다(위 사용자 보고 버그의 재발을 막는 더
    // 견고한 방식 — `wireProviderModelSelect()`가 호출되는 시점에
    // 이미 올바른 provider가 선택돼 있어야 모델 목록을 제대로 만든다).
    '<select id="conv-provider">' +
    '<option value=""' + (!conv.llm_provider ? " selected" : "") + '>provider 자동 판별</option>' +
    '<option value="anthropic"' + (conv.llm_provider === "anthropic" ? " selected" : "") + '>Anthropic</option>' +
    '<option value="openai"' + (conv.llm_provider === "openai" ? " selected" : "") + '>OpenAI</option>' +
    '<option value="ollama"' + (conv.llm_provider === "ollama" ? " selected" : "") + '>Ollama(로컬)</option>' +
    '</select>' +
    modelSelectHtml("conv-model") +
    // "AI 에이전트 관리"처럼 매 변경마다 자동저장하지 않고 명시적
    // 버튼으로 한 번에 저장한다 — provider만 바꿔도(모델을 아직 안
    // 고른 상태) 즉시 저장되면 방금까지 고른 모델이 null로 덮어써지는
    // 문제가 있었다(실사용 확인).
    '<button class="small" id="conv-save-model-btn">모델 설정 저장</button>' +
    '<label><input type="checkbox" id="conv-auto-reply"' + (conv.ai_auto_reply ? " checked" : "") + '> AI 자동응답</label>' +
    '</div>' +
    '<p class="muted">AI 자동응답이 켜져 있으면 멘션 없는 메시지에도 매번 LLM 호출이 발생합니다(비용 발생). provider/model을 고른 뒤 "모델 설정 저장"을 눌러야 반영됩니다.</p>' +
    // 사용자 요청(2026-10-14, "대화창에 사용중인 모델이 명시되도록 UI를
    // 개선해줘") — 실측으로 확인한 사실: 이 설정은 **멘션 없는 기본 AI
    // 응답에만** 적용된다. `@Agent`로 직접 부르면 그 Agent 자신에게 저장된
    // 모델이 쓰이고, `@Team`으로 부르면 서버 환경변수/API 키로 자동
    // 판별한 값이 쓰인다(둘 다 이 설정과 무관). 각 메시지가 실제로 어떤
    // 모델로 응답했는지는 메시지 카드 작성자 줄에 표시한다(아래
    // `_conversationAuthorLabel()` 참고).
    '<p class="muted">⚠️ 이 모델 설정은 <b>멘션 없는 기본 AI 응답</b>에만 적용됩니다. ' +
    '<code>@Agent이름</code>으로 부르면 그 Agent 자신에게 저장된 모델이, ' +
    '<code>@Team이름</code>으로 부르면 서버가 자동 판별한 모델이 쓰입니다(이 설정과 무관). ' +
    '각 메시지에 실제로 쓰인 모델은 메시지 작성자 줄에 표시됩니다.</p>' +
    '<h4>참가자</h4>' +
    '<ul id="conv-participant-list">' + participantRows + '</ul>' +
    (participantRows ? '' : '<p class="muted" id="conv-participant-empty-hint">참가자가 없습니다.</p>') +
    // 사용자 요청(2026-10-14, "①(참가자 검색-초대 입력창) 없이
    // ②(메시지 중 @멘션 자동완성)만으로 동작 가능하다면 ①을 삭제") —
    // 아래 메시지창의 @멘션 자동완성이 이미 "아직 초대 안 된 조직
    // 전체 후보"까지 검색해서(`/api/conversations/mentionable`),
    // 드롭다운에서 고르는 순간 `_inviteAndTrackParticipant()`로 먼저
    // 초대한 뒤 멘션 텍스트를 끼워 넣는다 — 메시지를 실제로 보내지
    // 않아도 그 시점에 이미 초대가 끝나 있다(사람/Agent/Team 전부
    // 지원, 기존 이 입력창과 완전히 같은 검색·대상 범위). 그래서
    // 별도의 참가자 검색-초대 입력창은 기능이 완전히 겹쳐 제거한다
    // (아래 "대화"의 메시지창에서 "@"를 치면 같은 역할을 한다).
    '<p class="muted">아래 메시지창에서 "@"를 입력하면 참가자 검색·초대 자동완성이 뜹니다(아직 초대 안 된 사람·Agent·Team도 검색되고, 고르면 바로 초대됩니다).</p>' +
    '<h4>대화</h4>' +
    '<div id="conv-messages">' + (messageCards || '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>') + '</div>' +
    // 사용자 보고(2026-10-14, 스크린샷 — 메시지를 보낸 뒤 진행 중임을
    // 알 수 없음) — "응답을 기다리는 중…"은 이미 있었지만 `#global-msg`
    // (페이지 맨 위)에만 떴다. 대화를 스크롤해 내려와 입력창 근처를
    // 보고 있는 사용자에게는 화면 밖이라 안 보였다 — 메시지 목록
    // 바로 아래, 입력창 바로 위에 똑같은 역할의 인라인 표시를 추가한다.
    '<div id="conv-pending-indicator" style="display:none"></div>' +
    '<form class="inline" id="conv-message-form" style="margin-top:0.5rem;flex-wrap:wrap">' +
    // 사용자 요청(2026-11-11, "개발업무 Skill의 경우 Default로 대화창에
    // 보이지 않도록 설정하고, 필요시 노출하도록") — 완전히 숨기지 않고
    // default-off 체크박스로 켜면 보이게 한다. 켜면 즉시 목록을 다시
    // 불러온다(대화 자체를 다시 열지 않아도 바로 반영 —
    // `renderConversationDetail` 재호출).
    '<label class="muted" style="font-size:0.85rem;white-space:nowrap">' +
    '<input type="checkbox" id="conv-show-dev-skills"' + (_convShowDevSkills ? " checked" : "") + '> 개발업무 스킬도 보기</label>' +
    '<select id="conv-skill-select"><option value="">스킬 선택 안 함</option>' +
    skillNames.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join("") +
    '</select>' +
    '<div class="mention-wrap" style="position:relative;flex:1;min-width:200px">' +
    '<div id="conv-message-highlight" aria-hidden="true"></div>' +
    '<input id="conv-message-input" placeholder="메시지(@참가자이름으로 호출 가능)" style="width:100%;box-sizing:border-box" autocomplete="off">' +
    '<ul class="mention-dropdown" id="conv-mention-dropdown" style="display:none"></ul>' +
    '</div>' +
    // 사용자 보고(2026-11-12, "자료 선택 시 대화입력·전송버튼 클릭
    // 없이 바로 미리보기가 진행된다") — 예전엔 파일을 고르는 즉시
    // 업로드·메시지 생성까지 끝나버려, 그 자료에 대한 설명을 같이
    // 적을 틈이 없었다. 이제는 파일을 골라도 **보류만** 하고(아래
    // pendingAttachmentFile), "전송"을 눌러야 비로소 — 입력창에 적은
    // 글이 있으면 그 메시지를 먼저, 그 뒤에 첨부를 별도 메시지로 —
    // 둘 다 한 번의 "전송" 클릭으로 올라간다. 멘션 감지는 여전히
    // 입력창 텍스트에만 걸린다(첨부 파일 내용은 별도 메시지라 전과
    // 동일하게 멘션 오탐 걱정이 없다).
    '<input type="file" id="conv-file-input" accept=".md,.markdown,.txt,.html,.htm,.pdf" style="display:none">' +
    '<button type="button" class="small" id="conv-file-btn" title="자료 첨부(.md/.txt/.html/.pdf) — 전송 시 함께 올라갑니다">\U0001f4ce 자료</button>' +
    '<span class="muted" id="conv-pending-file-name" style="font-size:0.85rem"></span>' +
    '<button type="button" class="small danger" id="conv-pending-file-remove" style="display:none">자료 제거</button>' +
    '<button class="primary" type="submit">전송</button>' +
    '</form>' +
    '</section>';

  // 사용자 보고(2026-10-08, "대화 모델 선택시 provider 후 모델
  // 레지스트리에 등록된 모델 리스트 제공 안됨") — `wireProviderModelSelect()`
  // 의 초기 `rebuildOptions()`는 호출 시점의 `providerSelect.value`를
  // 그대로 쓴다. provider select의 `selected` 속성을 마크업에서 이미
  // 심어뒀으므로(위) 이 시점엔 `providerSelect.value`가 올바르다 —
  // JS로 뒤늦게 `.value =`를 대입하면 모델 목록이 빈 문자열(기본
  // 옵션) 기준으로 만들어져 항상 비어 보이는 게 원래 버그였다.
  wireProviderModelSelect("conv-provider", "conv-model", modelsByProvider, conv.llm_model);

  const mentionInput = document.getElementById("conv-message-input");
  const mentionDropdown = document.getElementById("conv-mention-dropdown");
  const mentionHighlight = document.getElementById("conv-message-highlight");
  const mentionMirror = setupMentionInputMirror(mentionInput, mentionHighlight, participants);
  // 사용자 요청(2026-10-10, "채팅에서 @로 호출하려면 미리 전체 이름을
  // 알고 있어야 해") — 메시지창 자동완성이 이미 초대된 참가자만 보던
  // 것을, 조직 전체 검색(`/api/conversations/mentionable`, 이미
  // "참가자 초대" 입력창이 쓰던 바로 그 엔드포인트)까지 함께 보도록
  // 확장한다. 미초대 대상을 드롭다운에서 고르면 별도 화면 이동 없이
  // 그 자리에서 바로 초대한 뒤 멘션을 끼워 넣는다(`_inviteAndTrackParticipant`).
  setupMentionAutocomplete(mentionInput, mentionDropdown, participants, {
    search: (q) => api("GET", qs("/api/conversations/mentionable", { q })),
    onInvited: (item) => _inviteAndTrackParticipant(conversationId, participants, item),
    onMentionSelected: () => {
      const skillSelect = document.getElementById("conv-skill-select");
      if (skillSelect) skillSelect.value = "";
      mentionMirror.sync();
    },
  });

  document.getElementById("conv-show-dev-skills").addEventListener("change", (ev) => {
    _convShowDevSkills = ev.target.checked;
    renderConversationDetail(conversationId, containerId);
  });

  // 삭제/나가기 뒤의 새로고침은 `refresh()`(전역, 현재 탭을 다시
  // 렌더)로 한다 — 이 함수는 독립 "대화" 탭과 Task 워크스페이스
  // 임베드(`_loadTaskConversation`) 양쪽에서 호출되므로, 둘 중 어느
  // 쪽에서 열렸는지 모르는 채로도 올바른 화면을 다시 그려준다.
  const deleteBtn = document.getElementById("conv-delete-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      if (!(await confirmModal("이 대화를 메시지·참가자까지 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/conversations/" + conversationId);
        showMsg("global-msg", "대화를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
  const leaveBtn = document.getElementById("conv-leave-btn");
  if (leaveBtn) {
    leaveBtn.addEventListener("click", async () => {
      if (!(await confirmModal("이 대화에서 나갑니다. 계속할까요?"))) return;
      try {
        const result = await api("POST", "/api/conversations/" + conversationId + "/leave");
        showMsg(
          "global-msg",
          result.conversation_deleted ? "대화에서 나갔습니다(마지막 참가자라 대화도 삭제됐습니다)." : "대화에서 나갔습니다.",
          "ok"
        );
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }

  document.getElementById("conv-save-model-btn").addEventListener("click", () => _saveConversationModel(conversationId));

  document.getElementById("conv-auto-reply").addEventListener("change", async (ev) => {
    try {
      await api("PATCH", "/api/conversations/" + conversationId + "/ai-auto-reply", {
        enabled: ev.target.checked,
      });
      showMsg("global-msg", "자동응답 설정을 저장했습니다.", "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 2026-10-14 — 참가자 검색-초대 전용 입력창을 제거했다(아래 메시지창의
  // "@" 자동완성이 완전히 같은 검색·초대를 포함한다, `setupMentionAutocomplete`
  // 호출부와 `_inviteAndTrackParticipant()` 참고).

  // 사용자 보고(2026-11-12) — 전송을 누르기 전까지 선택한 파일을
  // 들고만 있는다(아래 change 핸들러는 업로드하지 않는다 — 실제
  // 업로드는 "전송" 클릭 시에만 일어난다).
  let pendingAttachmentFile = null;
  const pendingFileNameEl = document.getElementById("conv-pending-file-name");
  const pendingFileRemoveBtn = document.getElementById("conv-pending-file-remove");
  function _clearPendingAttachment() {
    pendingAttachmentFile = null;
    pendingFileNameEl.textContent = "";
    pendingFileRemoveBtn.style.display = "none";
    document.getElementById("conv-file-input").value = "";
  }
  pendingFileRemoveBtn.addEventListener("click", _clearPendingAttachment);

  document.getElementById("conv-message-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("conv-message-input");
    const content = input.value.trim();
    if (!content && !pendingAttachmentFile) return;
    const skill = document.getElementById("conv-skill-select").value || undefined;
    try {
      // 첨부가 있으면 먼저 올린다(멘션 감지가 걸리지 않는 별도
      // 메시지로 남음 — 첨부 글 안에 우연히 "@참가자이름"이 있어도
      // 멘션 오탐이 안 난다는 기존 안전장치를 그대로 유지). 입력창
      // 텍스트가 있으면 그 뒤를 이어 두 번째 메시지로 보낸다 — 결과적으로
      // "전송" 한 번으로 둘 다 올라간다.
      if (pendingAttachmentFile) {
        const formData = new FormData();
        formData.append("file", pendingAttachmentFile);
        await apiUpload("/api/conversations/" + conversationId + "/attachments", formData);
        _clearPendingAttachment();
      }
      let result = null;
      if (content) {
        const payload = { content };
        if (skill) payload.skill = skill;
        result = await api("POST", "/api/conversations/" + conversationId + "/messages", payload);
      }
      input.value = "";
      document.getElementById("conv-skill-select").value = "";
      mentionMirror.sync();
      await refreshConversationMessages(conversationId, participants, conv);
      // 사용자 요청(2026-10-18, "어떤 에이전트가 어떤 동작을 하고
      // 있는지... 펼침/닫힘 UI로") — 뭉뚱그린 "N건 진행 중" 대신
      // Agent/Team 이름별로 펼쳐 볼 수 있는 목록을 띄운다. 완료되는
      // 작업마다(전부 끝날 때까지 기다리지 않고) 그 자리에서 상태를
      // 갱신하고 메시지 목록도 바로 반영한다 — 먼저 끝난 결과를
      // 늦게 끝나는 Job 때문에 못 보고 기다리지 않게.
      if (result) _watchConversationJobs(conversationId, result.triggered_jobs, participants, conv);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("conv-file-btn").addEventListener("click", () => {
    document.getElementById("conv-file-input").click();
  });
  document.getElementById("conv-file-input").addEventListener("change", (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    pendingAttachmentFile = file;
    pendingFileNameEl.textContent = "📎 " + file.name + "(전송 시 함께 올라갑니다)";
    pendingFileRemoveBtn.style.display = "";
    ev.target.value = "";
  });

  // 경량 폴링 — 이 대화를 보는 동안 주기적으로
  // 새 메시지를 반영한다(다른 참가자/Agent/Team이 비동기로 남긴
  // 메시지도 수동 새로고침 없이 보이게). 다른 화면으로 이동하면
  // `_stopConversationPolling()`이 멈춘다.
  _conversationPollTimer = setInterval(
    () => refreshConversationMessages(conversationId, participants, conv),
    6000
  );

  // 이 대화를 열었을 때 아직 안 끝난 Job이 있으면(위에서 미리 조회해둔
  // `runningJobs`), 메시지를 "보내는" 흐름과 똑같이 진행 패널을 띄우고
  // 폴링을 이어간다 — 그래야 탭을 닫았다가 Job이 끝나기 전에 다시 열어도
  // "지금 작업 중"이라는 걸 바로 알 수 있다.
  if (runningJobs && runningJobs.length) {
    _watchConversationJobs(
      conversationId,
      runningJobs.map(j => ({
        trigger_type: j.trigger_type,
        job_id: j.id,
        agent_id: j.agent_id,
        team_id: j.team_id,
      })),
      participants,
      conv
    );
  }
}

// 메시지창 자동완성에서 미초대 대상을 고르면 호출된다 — 전체 화면
// 재렌더(`renderConversationDetail`) 없이 (1) 초대 API를 호출하고
// (2) 그 자리에서 쓰는 `participants` 배열과 "참가자" 목록 DOM만
// 갱신한다(전체 재렌더는 작성 중이던 메시지 초안을 지워버린다).
async function _inviteAndTrackParticipant(conversationId, participants, item) {
  try {
    await api("POST", "/api/conversations/" + conversationId + "/participants", {
      participant_type: item.type,
      participant_id: item.id,
    });
  } catch (e) {
    // 드롭다운이 갱신되기 전 같은 대상을 두 번 고르거나, 이번 세션에서
    // 이미 초대한 대상을 다시 멘션하는 등 드문 경합은 "이미 초대됨"으로
    // 조용히 넘기고 멘션만 그대로 진행한다 — 단, 이미 참가자이므로
    // 아래 participants.push/li 추가는 하지 않고 바로 끝낸다(사용자
    // 보고 2026-10-28, "참가자가 두 번 표기됨" — 예전엔 이 catch 분기도
    // 그대로 아래로 흘러 push/appendChild해 참가자 목록에 같은 이름이
    // 중복 표시됐다. 실제로는 서버에 중복 행이 생기지 않는다 — 이
    // 화면의 로컬 state/DOM만 중복이었다).
    if (!String(e.message || "").includes("이미 초대")) {
      showMsg("global-msg", e.message, "error");
      throw e;
    }
    return;
  }
  participants.push({
    participant_type: item.type,
    participant_id: item.id,
    display_name: item.name,
  });
  const emptyHint = document.getElementById("conv-participant-empty-hint");
  if (emptyHint) emptyHint.remove();
  const list = document.getElementById("conv-participant-list");
  if (list) {
    const li = document.createElement("li");
    li.textContent = label(item.type) + " " + item.name;
    list.appendChild(li);
  }
  showMsg("global-msg", item.name + "님을 대화에 초대했습니다.", "ok");
}

async function _saveConversationModel(conversationId) {
  const provider = document.getElementById("conv-provider").value || null;
  const model = modelSelectValue("conv-model");
  try {
    await api("PATCH", "/api/conversations/" + conversationId + "/model", { provider, model });
    showMsg("global-msg", "모델 설정을 저장했습니다.", "ok");
  } catch (e) { showMsg("global-msg", e.message, "error"); }
}

// 이 Task에 종속된
// 대화(`linked_task_id`)가 이미 있으면 그대로 임베드하고, 없으면
// "대화 시작" 버튼만 보여준다(결과물 작성 전에도 항상 쓸 수 있다 —
// 발견한 구조적 한계의 직접적인 해소).
async function _loadTaskConversation(taskId) {
  const box = document.getElementById("ws-conversation-" + taskId);
  if (!box) return;
  let mine;
  try {
    mine = await api("GET", "/api/conversations");
  } catch (e) {
    box.innerHTML = "";
    return;
  }
  const existing = mine.find(c => c.linked_task_id === taskId);
  if (existing) {
    box.innerHTML = '<div id="ws-conversation-detail-' + taskId + '"></div>';
    renderConversationDetail(existing.id, "ws-conversation-detail-" + taskId);
    return;
  }
  box.innerHTML =
    '<section class="card"><h4>대화</h4>' +
    '<p class="muted">이 업무에 대해 바로 대화를 시작할 수 있습니다 — 결과물이 아직 없어도 됩니다.</p>' +
    '<button class="small" id="ws-start-conversation-btn-' + taskId + '">대화 시작</button>' +
    '</section>';
  document.getElementById("ws-start-conversation-btn-" + taskId).onclick = async () => {
    try {
      await api("POST", "/api/conversations", { title: "", linked_task_id: taskId });
      await _loadTaskConversation(taskId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };
}

/* ---------- 통합 인박스(Phase 5, 사용자 요청 2026-09-28) ---------- */

async function renderInbox(content) {
  const items = await api("GET", "/api/inbox");
  const labelOf = {
    approval_pending: "결재 대기",
    execution_failed: "AI 에이전트 실행 실패",
    low_gate_score: "낮은 Gate 점수 경고",
    completed: "✅ 완료됨",
  };
  const cards = items.map(item => {
    if (item.type === "approval_pending") {
      // 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
      // 에이전트명이 표시되지 않는 경우도 있어") — "결재 대기 · 배정
      // #12"만 보여줘 뭘 승인하는지 결재 탭을 또 열어야 알 수 있었다.
      // `/api/inbox`가 이제 task_role_name/resource_name도 주므로
      // (`approvals.describe_subject()`) 그대로 보여준다.
      const detail = item.task_id != null
        ? "#" + item.task_id + " " + esc(item.task_role_name || "") +
          (item.resource_name ? " · " + esc(label(item.resource_type)) + " " + esc(item.resource_name) : "")
        : esc(label(item.subject_type)) + " #" + item.subject_id;
      return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + detail + '</div>' +
        '<button class="small" data-goto-approvals>결재 화면으로 이동</button></div>';
    }
    if (item.type === "execution_failed") {
      return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(item.role_name) + '</div>' +
        '<div>' + esc(item.error || "") + '</div>' +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    if (item.type === "completed") {
      // 사용자 요청(2026-11-12) — 결재가 필요 없는 성공 완료도 요청한
      // 사람이 알 수 있게 한다(지금까지 조용히 묻혔음, 위 inbox.py
      // docstring 참고). 다른 경고성 카드와 톤이 다르다는 걸 보여주려고
      // gate-ok 배지를 쓴다(나머지는 muted 텍스트뿐).
      return '<div class="deliverable-card"><div><span class="gate-badge gate-ok">' + labelOf[item.type] + '</span> · ' + esc(item.role_name) + '</div>' +
        '<div class="muted">' + esc(item.completed_at || "") + '</div>' +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(item.role_name) + '</div>' +
      '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
  }).join("");

  content.innerHTML =
    '<section class="card"><h2>내게 필요한 조치</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>결재 대기·AI 에이전트 실행 실패·낮은 Gate 점수 경고(승인/반려 전인 것만)·✅ 완료됨(결재가 필요 없는 성공, 최근 24시간 이내만)을 한 곳에 모았습니다.</li>' +
      '<li>완전한 시간순 통합은 아니고 종류별로 묶여 있습니다 — 처리는 각 항목의 원래 화면(결재/내 업무)에서 합니다.</li>' +
      '</ul>'
    ) +
    (items.length ? cards : '<p class="muted">지금 조치가 필요한 항목이 없습니다 — 아직 업무를 만들어본 적이 없다면 "이용 가이드" 탭에서 가장 빠른 시작 방법을 확인하세요.</p>') +
    '</section>';

  content.querySelectorAll("[data-goto-approvals]").forEach(btn => {
    btn.onclick = () => selectTab("approvals");
  });
  content.querySelectorAll("[data-goto-task]").forEach(btn => {
    btn.onclick = async () => {
      await selectTab("mywork");
      renderCollabWorkspace(Number(btn.dataset.gotoTask));
    };
  });
}

/* ---------- 내 업무(협업 워크스페이스, 사용자 요청 2026-09-28) ---------- */

async function renderMyWork(content) {
  const rows = await api("GET", "/api/my-work");
  content.innerHTML =
    '<section class="card"><h2>내 업무</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>확정된 배정 중 내가 직접 할 일(사람 배정 당사자)이거나 내가 AI에게 맡긴 일(Agent 배정 요청자)이거나, 내가 속한 팀이 배정된 일(팀 리더/팀원)만 보입니다.</li>' +
      '<li>"열기"를 누르면 그 업무의 협업 워크스페이스(결과물 타임라인)가 아래에 펼쳐집니다.</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
    // 분석하고 부족한 기능을 개선해줘") — 이전엔 "결과물 수"·"최근
    // 결과물" 시각만 있어 지금 상태가 뭔지, 최종 결과물(승인)이 나왔는지
    // 업무를 직접 열어보기 전엔 알 수 없었다. "상태"·"최종 결과물"
    // 열을 추가한다.
    table(["할 일 ID", "역할", "유형", "관계", "상태", "최종 결과물", "결과물 수", "최근 갱신", "동작"], rows.map(r => [
      r.task_id, esc(r.role_name), esc(label(r.resource_type)),
      // 2026-10-07 — "팀 Crew 그룹" 배정만 있고 개별 사람/Agent 배정이
      // 없는 task도 "내 업무"에 뜨게 된 뒤(list_my_work() 수정) 추가.
      ({
        assignee: "내가 할 일",
        requester: "내가 맡긴 일",
        team_leader: "내 팀이 할 일(팀 리더)",
        team_member: "내 팀이 할 일(팀원)",
      })[r.my_relationship] || "내가 맡긴 일",
      // 사용자 요청(2026-11-12, "진행과정과 결과물 확인이 어려워(숨겨져
      // 있는 느낌)") — 기존엔 이 컬럼이 deliverable 상태만 봐서 "아직
      // 실행 안 함"과 "방금 실행시켰는데 아직 안 끝남"이 똑같이 "아직
      // 없음"으로 보였다. `task_completion_summary()`가 내려주는
      // `active_job`(agent_execution_jobs/crew_runs/pipeline_runs 중
      // running·paused_for_approval)을 deliverable 상태보다 먼저
      // 보여준다 — 지금 뭔가 돌고 있다는 신호가 최우선.
      r.active_job === "running" ? '<span class="gate-badge gate-warn">⏳ 실행 중</span>'
        : r.active_job === "paused_for_approval" ? '<span class="gate-badge gate-warn">🕐 승인 대기 중</span>'
        : r.latest_status ? esc(label(r.latest_status)) : '<span class="muted">아직 없음</span>',
      r.has_final_result ? '<span class="gate-badge gate-ok">✓ 승인됨</span>' : '<span class="muted">-</span>',
      r.deliverable_count,
      r.latest_deliverable_at ? esc(r.latest_deliverable_at) : "-",
      '<button class="small" data-open-workspace="' + r.task_id + '">열기</button>',
    ])) +
    '<div id="mywork-detail"></div></section>';

  content.querySelectorAll("[data-open-workspace]").forEach(btn => {
    btn.onclick = () => renderCollabWorkspace(Number(btn.dataset.openWorkspace));
  });
}

// 사용자 요청(2026-11-10, "claude의 artifact 처럼... 최종 결과물을
// 모아보는 갤러리 기능(결과물 삭제시 갤러리에서도 삭제)") — 여러
// Task를 가로지르는 결과물 모아보기. `GET /api/deliverables`는 캐시가
// 아니라 매번 실시간 조회라, 삭제하면 다음 새로고침부터 자동으로
// 빠진다(별도 동기화 코드 없음).
const CONTENT_TYPE_LABEL = { text: "텍스트", markdown: "마크다운", html: "HTML", code: "코드" };
const GALLERY_STATUS_BADGE = {
  draft: "gate-warn", pending_review: "gate-warn", approved: "gate-ok", rejected: "gate-warn",
};
const GALLERY_SORT_LABEL = { created_desc: "최신순", created_asc: "오래된순", status: "상태순" };
let _galleryOffset = 0;
const _GALLERY_LIMIT = 24;

async function renderGallery(content) {
  _galleryOffset = 0;
  await _renderGalleryFiltered(content, {});
}

// 사용자 요청(2026-11-10, "컨텐츠간 경계 구분, 필터링, 소팅 기능 등을
// 좀 더 다양하게 강화") — 유형 필터에 상태 필터·정렬·페이지네이션을
// 더한다("저장된 지식 조각" 화면의 {items,total,limit,offset} 규약을
// 그대로 재사용 — 새 페이지네이션 패턴을 만들지 않음). 카드 경계가
// 흐릿해 보인다는 보고의 실제 원인은 `.gallery-card`가 `<div>`인데
// 카드 배경/테두리는 `section.card`(태그+클래스 조합) 선택자로만
// 걸려 있어 전혀 안 먹고 있었다 — `.gallery-card` 자체에 명시적으로
// 테두리/배경/그림자를 준다(아래 CSS).
async function _renderGalleryFiltered(content, filters) {
  const canSeeAll = ["admin", "manager"].includes(ME.role);
  const sort = filters.sort || "created_desc";
  const params = new URLSearchParams();
  if (filters.content_type) params.set("content_type", filters.content_type);
  if (filters.status) params.set("status", filters.status);
  params.set("sort", sort);
  params.set("limit", _GALLERY_LIMIT);
  params.set("offset", _galleryOffset);
  const page = await api("GET", "/api/deliverables?" + params.toString());
  const rows = page.items;

  const typeOptions = ["", "text", "markdown", "html", "code"].map(ct =>
    '<option value="' + ct + '"' + (filters.content_type === ct ? " selected" : "") + '>' +
    (ct ? CONTENT_TYPE_LABEL[ct] : "전체 유형") + '</option>'
  ).join("");
  const statusOptions = ["", "draft", "pending_review", "approved", "rejected"].map(st =>
    '<option value="' + st + '"' + (filters.status === st ? " selected" : "") + '>' +
    (st ? esc(label(st)) : "전체 상태") + '</option>'
  ).join("");
  const sortOptions = Object.keys(GALLERY_SORT_LABEL).map(s =>
    '<option value="' + s + '"' + (sort === s ? " selected" : "") + '>' + GALLERY_SORT_LABEL[s] + '</option>'
  ).join("");
  const pageStart = page.total ? _galleryOffset + 1 : 0;
  const pageEnd = Math.min(_galleryOffset + _GALLERY_LIMIT, page.total);

  content.innerHTML =
    '<section class="card"><h2>갤러리</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>' + (canSeeAll ? "조직 전체" : "내가 참가한 업무") + '의 결과물을 모아 보여줍니다.</li>' +
      '<li>"새 탭에서 보기"는 그 결과물 하나만 담은 독립 페이지를 엽니다(로그인 세션 필요, 외부 비공개 공유는 아닙니다).</li>' +
      '</ul>'
    ) +
    '<div class="inline" style="margin-bottom:12px;flex-wrap:wrap">' +
    '유형: <select id="gallery-type-filter">' + typeOptions + '</select>' +
    '상태: <select id="gallery-status-filter">' + statusOptions + '</select>' +
    '정렬: <select id="gallery-sort-select">' + sortOptions + '</select>' +
    '</div>' +
    (rows.length === 0 ? '<p class="muted">결과물이 없습니다.</p>' :
    '<div class="gallery-grid">' + rows.map(r => {
      const canDelete = canSeeAll || (r.author_type === "human" && r.author_id === ME.id && r.status === "draft");
      return '<div class="card gallery-card" data-gallery-id="' + r.id + '">' +
        '<div class="gallery-card-head">' +
        '<span class="gate-badge gate-ok">' + esc(CONTENT_TYPE_LABEL[r.content_type] || r.content_type) + '</span>' +
        '<span class="gate-badge ' + (GALLERY_STATUS_BADGE[r.status] || "gate-warn") + '">' + esc(label(r.status)) + '</span>' +
        '<span class="muted">#' + r.id + '</span>' +
        '</div>' +
        '<p><b>' + esc(r.task_role_name) + '</b> (할 일 #' + r.task_id + ')</p>' +
        '<p class="muted">' + esc(r.created_at) + '</p>' +
        '<p>' + esc(r.preview || "(내용 없음)") + '</p>' +
        '<div class="gallery-card-actions">' +
        '<a href="/deliverables/' + r.id + '/view" target="_blank" rel="noopener">새 탭에서 보기</a>' +
        (canDelete ? ' <button class="small danger" data-gallery-delete="' + r.id + '">삭제</button>' : '') +
        '</div>' +
        '</div>';
    }).join("") + '</div>') +
    (page.total > _GALLERY_LIMIT
      ? '<div class="inline" style="margin-top:12px">' +
        '<button type="button" class="small" id="gallery-prev"' + (_galleryOffset <= 0 ? " disabled" : "") + '>이전</button>' +
        '<span class="muted">' + pageStart + '–' + pageEnd + ' / ' + page.total + '</span>' +
        '<button type="button" class="small" id="gallery-next"' + (_galleryOffset + _GALLERY_LIMIT >= page.total ? " disabled" : "") + '>다음</button>' +
        '</div>'
      : '');

  const rerender = (next) => {
    _galleryOffset = 0;
    _renderGalleryFiltered(content, Object.assign({}, filters, next));
  };
  content.querySelector("#gallery-type-filter").onchange = (e) => rerender({ content_type: e.target.value || null });
  content.querySelector("#gallery-status-filter").onchange = (e) => rerender({ status: e.target.value || null });
  content.querySelector("#gallery-sort-select").onchange = (e) => rerender({ sort: e.target.value });
  const prevBtn = content.querySelector("#gallery-prev");
  if (prevBtn) prevBtn.onclick = () => {
    _galleryOffset = Math.max(0, _galleryOffset - _GALLERY_LIMIT);
    _renderGalleryFiltered(content, filters);
  };
  const nextBtn = content.querySelector("#gallery-next");
  if (nextBtn) nextBtn.onclick = () => {
    _galleryOffset += _GALLERY_LIMIT;
    _renderGalleryFiltered(content, filters);
  };
  content.querySelectorAll("[data-gallery-delete]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 결과물을 삭제할까요? 되돌릴 수 없습니다."))) return;
      try {
        await api("DELETE", "/api/deliverables/" + btn.dataset.galleryDelete);
        _renderGalleryFiltered(content, filters);
      } catch (e) {
        showMsg("global-msg", e.message, "error");
      }
    };
  });
}

// 2026-10-08, Phase 4 — "할 일 보드"
// 4칼럼 칸반(backlog/queued/in_progress/done). 드래그 없이 업무를 열어
// select로 상태를 바꾸는 식으로 단순화했다(설계 문서 UI 설계 그대로).
//
// 2026-10-12, "Phase 1 (A)(B)
// (H1)(H2)(I5)부터 구현 시작해줘" — "전체 조직 범위는 범위 밖"이라던
// 원래 주석을 뒤집는다: admin/manager에겐 "전체 조직" 범위 선택지를
// 추가한다(`/api/all-work`). 카드 템플릿(STATUS_COLUMNS)은 그대로
// 재사용하고 데이터 소스만 바꾼다("같은 카드 템플릿, 데이터 소스만
// 교체" 원칙) — 일반 사용자는 기존과 동일하게 "내 업무"만 보인다.
const STATUS_COLUMNS = [
  { key: "backlog", label: "백로그" },
  { key: "queued", label: "대기" },
  { key: "in_progress", label: "진행중" },
  { key: "done", label: "완료" },
];
const BOARD_PRIORITY_BADGE = { urgent: "gate-warn", high: "gate-warn", medium: "gate-ok", low: "gate-ok" };

async function renderBoard(content) {
  const canSeeAll = ["admin", "manager"].includes(ME.role);
  await _renderBoardScope(content, "mine", canSeeAll);
}

async function _renderBoardScope(content, scope, canSeeAll) {
  // 2026-10-14 — 여기서 즉시 content를 "불러오는 중…" 한 줄로 비우면
  // selectTab()의 같은 블랭크와 중복이고(탭 전환 시), `refresh()`로 이
  // 탭을 제자리에서 다시 그릴 때는 스크롤이 맨 위로 튀는 원인이 된다
  // (긴 보드를 스크롤해 본 상태에서 내용이 한 줄로 줄어들면 브라우저가
  // 스크롤 위치를 강제로 잘라낸다) — 아래에서 데이터를 다 가져온 뒤
  // 한 번에 교체한다.
  const endpoint = scope === "all" ? "/api/all-work" : "/api/my-work";
  const rows = await api("GET", endpoint);
  const details = await Promise.all(
    rows.map(r => api("GET", "/api/tasks/" + r.task_id).catch(() => null))
  );
  const tasks = rows
    .map((r, i) => Object.assign({}, r, details[i]))
    .filter(t => t.status !== undefined);

  const columnsHtml = STATUS_COLUMNS.map(col => {
    const colTasks = tasks.filter(t => t.status === col.key);
    // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
    // 결과물 확인이 어려워") — 카드에 역할명·우선순위뿐이라 실행 중인지
    // 결과물이 나왔는지 전혀 안 보여, 칸반을 보고도 열어봐야만 알 수
    // 있었다. "내 업무"와 같은 active_job/has_final_result(`/api/my-
    // work`·`/api/all-work`가 이미 내려줌)를 그대로 재사용한다.
    const progressBadgeHtml = (t) => t.active_job === "running"
      ? '<span class="gate-badge gate-warn">⏳ 실행 중</span>'
      : t.active_job === "paused_for_approval"
      ? '<span class="gate-badge gate-warn">🕐 승인 대기 중</span>'
      : t.has_final_result
      ? '<span class="gate-badge gate-ok">✓ 승인됨</span>'
      : "";
    const cards = colTasks.map(t =>
      '<div class="deliverable-card">' +
      '<div><strong>' + esc(t.role_name) + '</strong></div>' +
      '<div><span class="gate-badge ' + (BOARD_PRIORITY_BADGE[t.priority] || "") + '">' + esc(label(t.priority)) + '</span> ' + progressBadgeHtml(t) + '</div>' +
      '<div class="actions"><button class="small" data-board-open="' + t.task_id + '">열기</button></div>' +
      '</div>'
    ).join("");
    return '<div class="card" style="flex:1;min-width:200px">' +
      '<h4>' + esc(col.label) + ' (' + colTasks.length + ')</h4>' +
      (cards || '<p class="muted">없음</p>') +
      '</div>';
  }).join("");

  const scopeSelectHtml = canSeeAll
    ? '<label>범위: <select id="board-scope">' +
      '<option value="mine"' + (scope === "mine" ? " selected" : "") + '>내 업무</option>' +
      '<option value="all"' + (scope === "all" ? " selected" : "") + '>전체 조직</option>' +
      '</select></label>'
    : "";

  content.innerHTML =
    '<section class="card"><h2>할 일 보드</h2>' +
    (scopeSelectHtml ? '<div class="inline">' + scopeSelectHtml + '</div>' : "") +
    helpBox(
      '<ul class="muted help-list">' +
      (scope === "all"
        ? '<li>조직 전체의 확정된 업무를 보여줍니다(admin/manager 전용 범위).</li>'
        : '<li>"내 업무"에 뜨는, 내가 참가 중인 업무만 보여줍니다' +
          (canSeeAll ? '(위 범위를 "전체 조직"으로 바꾸면 조직 전체를 봅니다).</li>' : '(조직 전체 업무는 "업무 요청" 탭에서 브리프별로 확인하세요).</li>')) +
      '<li>카드의 "열기"로 워크스페이스를 펼쳐 상태/우선순위를 바꾸면 이 보드에도 반영됩니다(새로고침 필요).</li>' +
      '</ul>'
    ) +
    '<div class="inline" style="align-items:flex-start;flex-wrap:wrap">' + columnsHtml + '</div>' +
    '<div id="board-detail"></div>' +
    '</section>';

  content.querySelectorAll("[data-board-open]").forEach(btn => {
    btn.onclick = () => renderCollabWorkspace(Number(btn.dataset.boardOpen), "board-detail");
  });
  const scopeSelect = document.getElementById("board-scope");
  if (scopeSelect) {
    scopeSelect.onchange = () => _renderBoardScope(content, scopeSelect.value, canSeeAll);
  }
}

/* Phase 4 — execute-agent는 202+job_id만 즉시 돌려준다(사용자 요청,
   2026-09-28, "Phase 4 진행해줘"). 실제 완료는 여기서 폴링으로 확인한다
   (실시간 WebSocket은 1차 범위 밖으로 명시적으로 뺐다).
   사용자 결정(2026-11-12, "대화 및 업무에 같은 기준이 적용되도록") —
   백엔드(agent_runtime.py/crew_runtime.py)는 단일 Agent 실행·팀
   Crew 실행·파이프라인 단계 전부 `pollConversationJob()`과 똑같은
   타임아웃 체계(최대 90분, Ollama reasoning/Crew 경로)를 공유하는데,
   이 함수의 포기 시한만 2분으로 훨씬 짧게 남아 있었다 — 백엔드는
   멀쩡히 돌고 있는데 여기만 먼저 포기해 사용자에게 실패처럼 보이는,
   대화 쪽에서 이미 여러 번 겪은 바로 그 문제가 업무 쪽엔 반영이 안
   돼 있었다. `pollConversationJob()`과 같은 40분으로 맞춘다. */
async function pollExecutionJob(taskId, jobId, intervalMs, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 2400000);
  while (Date.now() < deadline) {
    const jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    const job = jobs.find(j => j.id === jobId);
    if (job && job.status !== "running") return job;
    await new Promise(r => setTimeout(r, intervalMs || 1500));
  }
  throw new Error("실행 완료 확인 시간이 초과됐습니다 — 잠시 후 새로고침해 주세요.");
}

// 2026-10-08, Phase 4 — **사용자 결정**:
// 선행 작업 "목록"이 아니라 Riido 수준의 그래프 시각화로 바로 간다.
// 외부 그래프 라이브러리 없이(이 프로젝트 전체가 CDN 의존 없는 순수
// vanilla JS 단일 파일이라는 기존 관례 그대로 유지) 순수 SVG로 그린다
// — depth(의존 사슬 길이)별로 열을 나눠 왼쪽에서 오른쪽으로 흐르게
// 배치하는 가장 단순한 레이어 레이아웃(순환이 없는 DAG라는 서버
// 보장 덕에 depth가 항상 유한하게 정의된다).
function _dependencyGraphDepths(nodes, edges) {
  const dependsOn = {};
  nodes.forEach(n => { dependsOn[n.id] = []; });
  edges.forEach(e => {
    if (dependsOn[e.task_id]) dependsOn[e.task_id].push(e.depends_on_task_id);
  });
  const depth = {};
  function compute(id, seen) {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0; // 서버가 순환을 막지만 방어적으로 무한루프만 피한다
    seen.add(id);
    const deps = dependsOn[id] || [];
    const d = deps.length ? 1 + Math.max(...deps.map(dep => compute(dep, seen))) : 0;
    depth[id] = d;
    return d;
  }
  nodes.forEach(n => compute(n.id, new Set()));
  return depth;
}

function renderDependencyGraphSvg(graph, currentTaskId) {
  const { nodes, edges } = graph;
  if (!nodes.length) return '<p class="muted">연결된 의존관계가 없습니다.</p>';
  const depth = _dependencyGraphDepths(nodes, edges);
  const columns = {};
  nodes.forEach(n => {
    const d = depth[n.id];
    (columns[d] = columns[d] || []).push(n);
  });
  const colWidth = 170, rowHeight = 56, nodeW = 150, nodeH = 42, padding = 16;
  const pos = {};
  Object.keys(columns).forEach(d => {
    columns[d].forEach((n, i) => {
      pos[n.id] = { x: padding + Number(d) * colWidth, y: padding + i * rowHeight };
    });
  });
  const maxDepth = Math.max(...Object.values(depth));
  const maxRows = Math.max(...Object.values(columns).map(c => c.length));
  const width = padding * 2 + (maxDepth + 1) * colWidth - (colWidth - nodeW);
  const height = padding * 2 + maxRows * rowHeight - (rowHeight - nodeH);
  const priorityColor = { low: "#9aa0a6", medium: "#4a90d9", high: "#e0a030", urgent: "#d9534f" };

  const edgesSvg = edges.map(e => {
    const from = pos[e.depends_on_task_id], to = pos[e.task_id];
    if (!from || !to) return "";
    const x1 = from.x + nodeW, y1 = from.y + nodeH / 2;
    const x2 = to.x, y2 = to.y + nodeH / 2;
    return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="#aaa" stroke-width="1.5" marker-end="url(#dep-arrow)"/>';
  }).join("");

  const nodesSvg = nodes.map(n => {
    const p = pos[n.id];
    const isCurrent = n.id === currentTaskId;
    const label20 = n.role_name.length > 16 ? n.role_name.slice(0, 15) + "…" : n.role_name;
    return '<g data-graph-node="' + n.id + '" style="cursor:pointer">' +
      '<rect x="' + p.x + '" y="' + p.y + '" width="' + nodeW + '" height="' + nodeH +
      '" rx="6" fill="' + (isCurrent ? "#eef6ff" : "#fff") + '" stroke="' +
      (priorityColor[n.priority] || "#999") + '" stroke-width="' + (isCurrent ? 3 : 1.5) + '"/>' +
      '<text x="' + (p.x + nodeW / 2) + '" y="' + (p.y + 17) +
      '" text-anchor="middle" font-size="11" font-weight="bold">' + esc(label20) + '</text>' +
      '<text x="' + (p.x + nodeW / 2) + '" y="' + (p.y + 32) +
      '" text-anchor="middle" font-size="10" fill="#666">' +
      esc(label(n.status)) + " · " + esc(label(n.priority)) + '</text>' +
      '</g>';
  }).join("");

  return '<svg width="' + width + '" height="' + height + '" style="max-width:100%">' +
    '<defs><marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" ' +
    'markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#aaa"/></marker></defs>' +
    edgesSvg + nodesSvg + '</svg>';
}

// containerId: 사용자 요청(2026-10-06, "팀 구성과 협업 프로세스를 하나의
// 화면으로 합치는 방안" → 옵션 C 채택)으로 일반화했다 — 원래 "내 업무"
// 탭의 #mywork-detail에만 그릴 수 있었는데, "업무 요청" 탭의 할 일 상세
// 패널에도 같은 워크스페이스를 그대로 재사용하기 위함(중복 구현 금지).
// 기본값을 기존 id로 둬서 "내 업무" 쪽 기존 호출부는 전혀 안 바꿔도 된다.
async function renderCollabWorkspace(taskId, containerId = "mywork-detail") {
  const detail = document.getElementById(containerId);
  detail.dataset.taskId = String(taskId);
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = isSupervisor ? await fetchModelsByProvider() : {};
  let items, jobs, permission, confirmedAgents, confirmedTeams, taskDetail, activity, depGraph, siblingTasks;
  let pipelineTemplates, pipelineRuns, budget;
  try {
    items = await api("GET", "/api/tasks/" + taskId + "/deliverables");
    jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    // 메모/통합 활동 타임라인/
    // 상태·우선순위·의존관계(Phase 4),
    // 2026-10-08 — 전부 워크스페이스를 열 때 같이 불러온다.
    // 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자 기능 삭제해줘") —
    // 참가자 초대(Phase 2)는 Task 워크스페이스 전체에서 제거했다(아래
    // 참가자 카드·ws-invite-input·댓글 @멘션 자동완성도 같이 제거 —
    // 전부 이 `participants` 하나에서 갈라져 나갔던 기능). 백엔드
    // `task_participants` 테이블/API(`deliverables.py`)는 그대로 남아
    // 있지만, 이제 초대할 방법이 없어 `detect_mentioned_agents()`
    // (댓글 @Agent 호출)도 사실상 동작하지 않게 된다 — 의도된 부수
    // 효과(요청 범위 밖이라 백엔드까지 지우진 않았다).
    taskDetail = await api("GET", "/api/tasks/" + taskId);
    activity = await api("GET", "/api/tasks/" + taskId + "/activity");
    depGraph = await api("GET", "/api/tasks/" + taskId + "/dependencies");
    // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") —
    // "선행 작업 ID" 입력도 같은 문제라 같은 브리프의 다른 할 일
    // 목록으로 고를 수 있게 한다(선행 작업은 브리프를 넘어서도 걸 수
    // 있게 백엔드가 허용하지만, 실제로는 거의 항상 같은 브리프 내
    // 작업이라 이 목록만으로 충분 — 드문 브리프 간 선행 작업은 범위
    // 밖으로 남긴다).
    siblingTasks = await api("GET", "/api/briefs/" + taskDetail.brief_id + "/tasks").catch(() => []);
    // 사용자가 스크린샷으로 보고한 갭(2026-09-29) — 참가자기만 하면
    // 무조건 실행 버튼을 보여줘, human assignee가 눌러야만 "권한이
    // 없습니다" 403으로 뒤늦게 알게 됐다. 버튼을 그리기 전에 먼저 확인한다.
    permission = await api("GET", "/api/tasks/" + taskId + "/execution-permission");
    // 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 5 실행 중) — "다중
    // confirmed 허용" 이후 한 할 일에 confirmed agent가 2개 이상이면
    // 이 버튼이 어느 쪽을 실행하는지 전혀 알려주지 않았다(그냥 "먼저
    // 걸리는 하나"가 조용히 실행됨) — 의도와 다른 Agent가 실행돼
    // 실비용만 낭비될 위험이 있었다. 확정된 Agent 배정을 미리 조회해
    // 2개 이상이면 선택 드롭다운을 보여준다(`ExecuteAgentRequest.
    // agent_id`는 이미 추가돼 있었는데 이 UI만 안 쓰고 있었다).
    const assignments = await api("GET", "/api/tasks/" + taskId + "/assignments");
    confirmedAgents = assignments.filter(a => a.resource_type === "agent" && a.status === "confirmed");
    // 사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
    // 개선... 사람-에이전트 연동이 가능한 구조인지 확인") — Phase 3.
    // 이 할 일에 confirmed로 배정된 팀이 있으면 그 팀의 Agent 멤버를 실제 Crew로
    // 실행하는 섹션을 보여준다(팀이 여러 개면 드롭다운으로 하나를
    // 고른다 — 사용자 결정, 전부 동시 실행 아님).
    confirmedTeams = assignments.filter(a => a.resource_type === "team" && a.status === "confirmed");
    // 사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
    // 최소 HITL/HOTL... 단계별 단독 또는 복수의 에이전트가 순차/병행
    // 처리") — 팀 Crew 실행과 같은 자리에, 단계형 파이프라인 실행
    // 섹션을 추가한다. 권한은 위 permission 조회와 동일한 Task이므로
    // 별도 403 분기 없이 그대로 보여준다(서버가 다시 검사한다).
    pipelineTemplates = (await api("GET", "/api/pipeline-templates").catch(() => []))
      .filter(t => t.is_active);
    pipelineRuns = await api("GET", "/api/tasks/" + taskId + "/pipeline-runs").catch(() => []);
    // 사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도 예산
    // 추적") — 실패해도(권한 밖 등) 워크스페이스 전체가 깨지지 않게
    // catch로 감싼다(기존 pipelineRuns 등과 같은 방어).
    budget = await api("GET", "/api/tasks/" + taskId + "/budget").catch(() => null);
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  // 최신 job이 아직 안 끝났거나(생성 중) 실패했으면, 아직 timeline에
  // 안 나오는 그 실행을 별도 카드로 보여준다(성공하면 곧 deliverable로
  // 나타나므로 여기서 다시 보여줄 필요 없음).
  // 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현 가능한
  // 감사로그) — Gate 점수가 낮거나 실행이 실패했을 때 "왜 이렇게
  // 나왔는지"를 사후에 확인할 수 있게, 그 시점 system prompt·증강된
  // 프롬프트·provider/model을 펼쳐 보여준다(관리자/매니저만 — 기술적
  // 세부 정보라 일반 참가자 화면을 어지럽히지 않는다).
  const promptSnapshotDetails = (job) => (isSupervisor && job.prompt_snapshot)
    ? '<details class="muted"><summary>실행에 쓰인 프롬프트 보기(재현용)</summary>' +
      '<p>provider/model: ' + esc(job.prompt_snapshot.llm_provider || "-") + " / " + esc(job.prompt_snapshot.llm_model || "-") + '</p>' +
      '<p>system prompt:</p><pre style="white-space:pre-wrap">' + esc(job.prompt_snapshot.system_prompt || "") + '</pre>' +
      '<p>프롬프트(증강 후):</p><pre style="white-space:pre-wrap">' + esc(job.prompt_snapshot.augmented_prompt || "") + '</pre>' +
      '</details>'
    : "";
  const latestJob = jobs[0];
  const jobCard = (latestJob && latestJob.status === "running")
    ? '<div class="deliverable-card chat-agent"><div class="muted">AI 에이전트 · 생성 중…</div></div>'
    : (latestJob && latestJob.status === "failed"
      ? '<div class="deliverable-card chat-agent"><div class="muted">AI 에이전트 · 실행 실패(' + esc(latestJob.finished_at) + ')</div>' +
        '<div>' + esc(latestJob.error || "알 수 없는 오류") + '</div>' +
        promptSnapshotDetails(latestJob) + '</div>'
      : '');

  // Phase 5(사용자 요청, 2026-09-28, "협업 워크스페이스 UX 개선") — 공유
  // 스레드/채팅형 타임라인: 작성자 타입별로 카드를 좌우로 구분해 "누가
  // 쓴 건지" 한눈에 보이게 한다. Gate 요약 배지도 여기서 처음 노출한다
  // — d.evaluation(Phase 3부터 API엔 있었지만 이전까지 화면 어디에도
  // 안 나오고 있었다).
  const deliverableCards = items.map(d => {
    // 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 3 실행 중) — 결재자가
    // 그 task의 참가자가 아니면(흔한 경우 — 결재 체인은 조직도 기준이지
    // task 참가 여부와 무관) 반려해도 "왜"를 댓글로 남길 방법이 전혀
    // 없었다(댓글은 참가자 전용). 승인/반려에 선택적 사유를 붙여 그
    // 사유를 결재자 자격으로(참가자 여부와 무관하게) 타임라인에 남긴다.
    const decideActions = (isSupervisor && d.approval_id)
      ? '<div class="actions">' +
        '<input placeholder="사유(선택)" data-decide-reason="' + d.approval_id + '" style="flex:1;min-width:120px">' +
        '<button class="small" data-decide-deliverable="' + d.approval_id + '" data-approve="1">승인</button>' +
        '<button class="small danger" data-decide-deliverable="' + d.approval_id + '" data-approve="0">반려</button></div>'
      : (d.approval_id ? '<p class="muted">결재 대기 중 — "결재" 탭 또는 담당 매니저/관리자가 처리합니다.</p>' : '');
    // Phase 2 후속(라이브 시나리오 실행에서 발견한 갭 보완) — 사람
    // draft는 본인이 명시적으로 "검토 요청"을 눌러야만
    // 결재 체인이 붙는다(Agent처럼 자동 제출되지 않음). 본인 draft에만
    // 노출한다(대리 제출 금지).
    const submitAction = (d.author_type === "human" && d.status === "draft" && d.author_id === ME.id)
      ? '<div class="actions"><button class="small" data-submit-deliverable="' + d.id + '">검토 요청</button></div>'
      : '';
    // 실측으로 발견한 갭(사용자 요청, 2026-09-28, "UI/UX 내용 중... 분석
    // 하고 개선해줘") — measured.length가 0일 때 passed===measured.length
    // (0===0)가 참이 돼 "Gate 0/0 통과"를 녹색(gate-ok)으로 보여줬다.
    // "아무것도 측정 안 됨"이 "전부 통과"처럼 보이는 오해의 소지가 있는
    // 표시라, 측정된 Gate가 하나도 없으면 별도 문구로 구분한다.
    const gateBadge = d.evaluation
      ? (() => {
          const gates = Object.values(d.evaluation.gate_scores);
          const measured = gates.filter(g => !g.not_measured);
          if (measured.length === 0) {
            return '<span class="gate-badge gate-warn">Gate 측정 없음</span>';
          }
          const passed = measured.filter(g => g.passed).length;
          const cls = passed === measured.length ? "gate-ok" : "gate-warn";
          return '<span class="gate-badge ' + cls + '">Gate ' + passed + "/" + measured.length +
            " 통과" + (d.evaluation.tcr === null ? "" : " · TCR " + d.evaluation.tcr.toFixed(0) + "%") + '</span>';
        })()
      : "";
    const side = d.author_type === "agent" ? "chat-agent" : "chat-human";
    // 사용자 요청(2026-09-29, "Tool, Skill을 적극 활용하는 시나리오를
    // 기획해 진행해줘") 라이브 실행에서 발견한 갭 — VectorDB/GraphDB
    // 그라운딩이 실제로 뭘 검색해 답변에 반영했는지 결재자가 확인할
    // 방법이 없었다. 이 deliverable을 만든 job의 retrieved_context를
    // 찾아 접을 수 있는 "참고한 근거" 블록으로 보여준다.
    const sourceJob = jobs.find(j => j.deliverable_id === d.id && j.retrieved_context);
    const evidenceBlock = sourceJob
      ? '<details class="muted"><summary>참고한 근거(Tool/Skill 검색 결과)</summary>' +
        '<div>' + renderContent(sourceJob.retrieved_context) + '</div></details>'
      : '';
    // 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현 가능한
    // 감사로그) — 위 sourceJob 패턴과 같은 방식으로, 이 deliverable을
    // 만든 job의 prompt_snapshot을 찾아 보여준다(Gate 점수가 왜 이렇게
    // 나왔는지 사후에 재현해볼 수 있게).
    const promptJob = jobs.find(j => j.deliverable_id === d.id && j.prompt_snapshot);
    const promptBlock = promptJob ? promptSnapshotDetails(promptJob) : "";
    return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted">' + esc(label(d.author_type)) + " · " + esc(label(d.status)) + " · " + esc(d.created_at) + " " + gateBadge + '</div>' +
    '<div>' + renderContent(d.content, d.content_type) + '</div>' +
    '<div class="actions"><button class="small" data-download-deliverable="' + d.id + '">다운로드</button></div>' +
    evidenceBlock +
    promptBlock +
    decideActions +
    submitAction +
    '<div id="comments-' + d.id + '"></div>' +
    '<form class="inline" data-comment-form="' + d.id + '">' +
    // UI 설계(2026-10-08)로 추가했던 "@ 입력 시 참가자 목록 자동완성
    // 드롭다운"은 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자
    // 기능 삭제해줘")으로 참가자 기능 자체와 함께 제거했다 — 평범한
    // 입력창으로 되돌린다.
    '<input placeholder="댓글" id="comment-input-' + d.id +
    '" style="flex:1;box-sizing:border-box" autocomplete="off">' +
    '<button class="small" type="submit">댓글</button></form>' +
    '</div>';
  }).join("") + jobCard;

  const timeline = deliverableCards || '<p class="muted">아직 결과물이 없습니다.</p>';

  // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
  // 분석하고 부족한 기능을 개선해줘") — 전체 타임라인(중간 과정 포함)과
  // "지금 업무에 실제로 쓸 수 있는 최종 결과물"을 구분해 보여준다.
  // 이전엔 승인된 결과물도 다른 초안/반려 건과 뒤섞인 채팅형 목록 안에
  // 묻혀 있어, 여러 차례 주고받은 뒤엔 어느 게 "확정본"인지 스크롤해서
  // 직접 판단해야 했다. 승인된 deliverable 중 가장 최신 것을 상단에
  // 고정 노출한다(같은 task에 여러 건이 승인된 드문 경우, 최신 것을
  // "현재" 최종본으로 간주 — revises_id 기반 버전 체계는 스코프 밖).
  const approvedItems = items.filter(d => d.status === "approved");

  // 사용자 보고(2026-10-19, "'할 일 목록(Todo) 앱 개발'의 최종 결과물에
  // 코드가 포함되어야 하는데 품질 리포트만 있어") — 파이프라인 업무는
  // 품질 판정/검증 같은 "검토" 단계가 항상 맨 나중에 돌고, 그 단계도
  // HITL 없이 바로 'approved'로 올라가므로 위 "가장 최근 승인 건" 규칙이
  // 구현 결과물이 아니라 마지막 리뷰 단계의 판정문을 집어왔다. 파이프라인
  // 템플릿 단계에 `is_primary_deliverable`로 "이 단계 산출물이 진짜
  // 최종 결과물"이라고 명시적으로 표시해두면, 생성 시각과 무관하게 그
  // 단계의 결과물을 우선한다 — 일반(비파이프라인) 업무는 영향 없음.
  let primaryDeliverable = null;
  for (const run of pipelineRuns) {
    const candidates = (run.stage_results || [])
      .filter(sr => sr.is_primary_deliverable && sr.deliverable_id);
    if (!candidates.length) continue;
    const best = candidates.reduce((a, b) => (a.attempt > b.attempt ? a : b));
    primaryDeliverable = items.find(d => d.id === best.deliverable_id) || null;
    if (primaryDeliverable) break;
  }

  const finalResult = primaryDeliverable
    ? primaryDeliverable
    : (approvedItems.length
        ? approvedItems.reduce((a, b) => (a.created_at > b.created_at ? a : b))
        : null);
  const finalResultLabel = finalResult === primaryDeliverable
    ? "✓ 최종 결과물(파이프라인 핵심 산출물)"
    : "✓ 최종 결과물(승인됨)";
  const finalResultCard = finalResult
    ? '<div class="final-result-card">' +
      '<div class="muted">' + finalResultLabel + ' · ' + esc(label(finalResult.author_type)) + ' · ' + esc(finalResult.created_at) + '</div>' +
      '<div>' + renderContent(finalResult.content, finalResult.content_type) + '</div>' +
      '<div class="actions"><button class="small" data-download-deliverable="' + finalResult.id + '">다운로드</button></div>' +
      '</div>'
    : '<p class="muted">아직 승인된 최종 결과물이 없습니다 — 아래는 진행 중인 이력입니다.</p>';

  const modelFields = isSupervisor
    ? '<select id="ws-provider"><option value="">provider 자동 판별</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      modelSelectHtml("ws-model")
    : '<span class="muted">provider/model은 매니저·관리자만 선택할 수 있습니다(기본값 사용).</span>';

  // Phase 4 — 이미 생성 중인 job이 있으면 실행 버튼을 막는다(중복
  // 트리거로 같은 task에 2개의 Agent 실행이 동시에 도는 것을 방지 —
  // "실행 중임을 알려주는 UI" 관례를 여기도 적용). 사용자가 스크린샷으로
  // 보고한 갭(2026-09-29) — permission.eligible이 false면(human
  // assignee지 Agent 요청자가 아니거나, 확정된 Agent 배정 자체가 없음)
  // 버튼을 비활성화하고 이유를 바로 보여준다 — 눌러봐야만 403으로 알게
  // 하지 않는다.
  const EXECUTION_BLOCK_REASONS = {
    not_requester: "이 업무에 AI 에이전트 실행을 요청한 사람 또는 매니저/관리자만 실행할 수 있습니다.",
    no_confirmed_agent: "이 업무에 확정된 AI 에이전트 배정이 없습니다.",
  };
  const hasRunningJob = !!(latestJob && latestJob.status === "running");
  const executeDisabled = hasRunningJob || !permission.eligible;
  const executeBtnLabel = hasRunningJob ? "생성 중…" : "이 업무의 AI 에이전트 실행(비용 발생)";
  // confirmed agent가 2개 이상이면 어느 Agent를 실행할지 명시적으로
  // 고르게 한다 — 1개뿐이면(기존 동작) 선택 없이 바로 실행된다.
  const agentPickerHtml = confirmedAgents.length > 1
    ? '<select id="ws-agent-picker" style="margin-right:0.5rem">' +
      confirmedAgents.map(a => '<option value="' + a.resource_id + '">' +
        esc(a.resource_name || ("Agent #" + a.resource_id)) + '</option>').join("") +
      '</select>'
    : '';
  // 사용자 요청(2026-10-26, "A안 + B안" — 업무 실행에 쓸 참고자료는
  // 저장 없이 그 1회 실행에만 첨부) — "AI 에이전트 관리" 화면에 있던
  // 전역 "자료 업로드" 버튼을 폐지하고, 업무 맥락이 실제로 있는 이
  // 자리로 옮겼다. 선택 사항이라 안 붙여도 기존처럼 그냥 실행된다.
  const executeBtnHtml =
    agentPickerHtml +
    '<input type="file" id="ws-execute-attachment" accept=".md,.markdown,.txt,.html,.htm,.pdf" style="display:none">' +
    '<button type="button" class="small" id="ws-execute-attach-btn" title="이번 실행에만 쓸 참고자료 첨부(선택, 저장 안 됨)">\U0001f4ce 참고자료</button>' +
    '<span class="muted" id="ws-execute-attachment-name"></span> ' +
    '<button class="small" id="ws-execute-agent-btn"' + (executeDisabled ? " disabled" : "") + '>' +
    esc(executeBtnLabel) + '</button>' +
    (!hasRunningJob && !permission.eligible
      ? '<p class="muted">' + esc(EXECUTION_BLOCK_REASONS[permission.reason] || "실행할 수 없습니다.") + '</p>'
      : '') +
    (!hasRunningJob && confirmedAgents.length > 1
      ? '<p class="muted">이 업무에 확정된 Agent가 ' + confirmedAgents.length + '개입니다 — 실행할 Agent를 위에서 선택하세요.</p>'
      : '');

  // 사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
  // 개선... 사람-에이전트 연동이 가능한 구조인지 확인") —
  // Phase 3. 이 할 일에
  // confirmed로 배정된 팀이 있으면 그 팀의 Agent 멤버 전원을 실제
  // CrewAI Crew로 실행하는 섹션을 보여준다. 팀이 여러 개 배정돼
  // 있어도 한 번에 "팀 하나"만 실행한다(사용자 결정) — 전부 동시
  // 실행하면 비용이 팀 수만큼 자동으로 커지고, 의도치 않은 팀이 섞여
  // 실행될 위험도 있다(위 Agent 다중 확정과 같은 이유).
  const teamCrewPickerHtml = confirmedTeams.length > 1
    ? '<select id="ws-team-picker" style="margin-right:0.5rem">' +
      confirmedTeams.map(t => '<option value="' + t.resource_id + '">' +
        esc(t.resource_name || ("팀 #" + t.resource_id)) + '</option>').join("") +
      '</select>'
    : '';
  const teamCrewModelFields = isSupervisor
    ? '<select id="ws-team-crew-provider"><option value="">provider 자동 판별</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      modelSelectHtml("ws-team-crew-model")
    : '<span class="muted">provider/model은 매니저·관리자만 선택할 수 있습니다(기본값 사용).</span>';
  const teamCrewBtnHtml = confirmedTeams.length
    ? teamCrewPickerHtml +
      '<select id="ws-team-crew-process" style="margin-right:0.5rem">' +
      // 사용자 요청(2026-10-20, "고급 에이전트 관리") — "팀 관리"에서
      // 저장한 기본값을 쓰고 싶으면 아무것도 고르지 않아도 되게
      // 기본 선택지로 둔다(서버가 team.default_process로 폴백,
      // `null`을 보내야 그 폴백이 동작한다 — 명시적으로 "sequential"을
      // 고정 전송하면 기본값을 영영 못 쓴다).
      '<option value="">(팀 기본값 사용)</option>' +
      '<option value="sequential">sequential(순서대로 전달)</option>' +
      '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
      teamCrewModelFields +
      '<button class="small" id="ws-execute-team-crew-btn"' + (hasRunningJob ? " disabled" : "") + '>' +
      esc(hasRunningJob ? "생성 중…" : "팀 Agent 실행(Crew, 비용 발생)") + '</button>' +
      crewAdvancedOptionsHtml("ws-team-crew", isSupervisor) +
      (confirmedTeams.length > 1
        ? '<p class="muted">이 업무에 확정된 팀이 ' + confirmedTeams.length + '개입니다 — 실행할 팀을 위에서 선택하세요(그 팀만 실행됩니다).</p>'
        : '<p class="muted">팀의 온보딩된(active) Agent 멤버 전원이 CrewAI로 함께 실행되고, 각자의 산출물이 이 할 일의 결과물로 쌓입니다.</p>')
    : '';

  // 사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
  // 최소 HITL/HOTL... 단계별 단독 또는 복수의 에이전트가 순차/병행
  // 처리") — 파이프라인 실행 섹션. 실행 중인 run이 있으면 새로 시작하는
  // 버튼은 숨기고(서버도 같은 이유로 409를 던진다) 진행상황 타임라인만
  // 보여준다 — 승인 대기 중이면 결재함으로 가라는 안내를 덧붙인다.
  const PIPELINE_RUN_STATUS_LABEL = {
    running: "진행 중", paused_for_approval: "승인 대기", completed: "완료",
    failed: "실패", cancelled: "취소",
  };
  const PIPELINE_STAGE_BADGE_CLASS = {
    running: "running", passed: "succeeded", approved: "succeeded",
    failed_retrying: "running", escalated: "failed", rejected: "failed",
  };
  const PIPELINE_STAGE_LABEL = {
    running: "진행 중", passed: "통과", approved: "승인됨",
    failed_retrying: "재시도 중", escalated: "승인 대기(반복 실패)", rejected: "반려됨",
  };
  const latestPipelineRun = pipelineRuns[0];
  const pipelineRunActive = latestPipelineRun &&
    ["running", "paused_for_approval"].includes(latestPipelineRun.status);
  // 사용자 보고(2026-10-19, "A,C,D,F,G만 표시되고 B,E는 표시 안 됨 —
  // 평가되지 않은 Gate는 미평가로 표시 필요 / 색 표시 의미가 안 보임")
  // — 이전엔 `not_measured`인 Gate(B·E는 이 플랫폼 휴리스틱이 애초에
  // 측정하지 않는다, `_has_failing_gate`와 같은 전제)를 아예 목록에서
  // 빼버려 "B·E가 실패해서 숨겨진 건가" 오해를 샀다. 이제 A-G 7개를
  // 항상 다 보여주되, 미평가는 중립(회색) 배지로 구분하고 범례를 덧붙인다.
  const GATE_LEGEND_HTML =
    '<p class="muted" style="margin:0.2rem 0 0.4rem">' +
    '<span class="gate-badge gate-ok">A</span> 통과 · ' +
    '<span class="gate-badge gate-warn">A</span> 미통과 · ' +
    '<span class="gate-badge gate-na">A</span> 미평가(이 플랫폼이 측정하지 않는 항목) — 글자는 Gate A-G 식별자입니다.' +
    '</p>';
  // 사용자 요청(2026-10-21, "Layer 2 — 실행 가시성 강화") — 기존
  // 상세 목록(Gate 배지·시도 횟수·반려 이유)은 그대로 두고, 그 위에
  // 한눈에 훑을 수 있는 브레드크럼 요약을 추가한다(교체 아님 — 상세
  // 정보를 요약이 대신할 수 없어서).
  const pipelineBreadcrumbHtml = (run) => {
    const currentIdx = run.stage_results.findIndex(sr =>
      ["running", "failed_retrying", "escalated"].includes(sr.status));
    const resolvedCurrentIdx = currentIdx === -1 ? run.stage_results.length - 1 : currentIdx;
    return '<div class="pipeline-breadcrumb">' +
      run.stage_results.map((sr, i) => {
        const cls = PIPELINE_STAGE_BADGE_CLASS[sr.status] || "";
        const isCurrent = i === resolvedCurrentIdx;
        return '<span class="pipeline-breadcrumb-step job-status-badge ' + cls +
          (isCurrent ? ' pipeline-breadcrumb-current' : '') + '">' +
          (i + 1) + '. ' + esc(sr.stage_name) + '</span>';
      }).join('<span class="pipeline-breadcrumb-arrow">→</span>') +
      '</div>';
  };

  // 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사 + 요구사항 추적성
  // 자동 채점") — `gate_scores`는 A-G 7개 외에 "구현"/"코드 검증" 단계에
  // 한해 `code_syntax`/`requirements_traceability` 키가 추가로 들어올 수
  // 있다. 아래 렌더링은 원래 제네릭하게 모든 키를 순회하므로 자동으로
  // 배지가 뜨지만, 긴 영문 키를 그대로 배지 글자로 쓰면 어색해 짧은
  // 한글 라벨로 바꿔 보여준다(A-G는 그대로 글자 자체가 식별자).
  const GATE_EXTRA_LABELS = { code_syntax: "구문", requirements_traceability: "추적성", delegation_check: "위임확인" };
  const pipelineTimelineHtml = (run) => {
    if (!run) return "";
    const rows = run.stage_results.map(sr => {
      const gateSummary = sr.gate_scores
        ? Object.entries(sr.gate_scores)
            .map(([gid, g]) => {
              const label = GATE_EXTRA_LABELS[gid] || gid;
              const cls = g.not_measured ? "gate-na" : (g.passed ? "gate-ok" : "gate-warn");
              const title = g.not_measured
                ? "Gate " + label + " — 미평가"
                : "Gate " + label + " — " + (g.passed ? "통과" : "미통과") +
                  (typeof g.score === "number" ? " (점수 " + g.score + ")" : "");
              return '<span class="gate-badge ' + cls + '" title="' + escAttr(title) + '">' + label + "</span>";
            })
            .join(" ")
        : "";
      return '<li>' +
        '<span class="job-status-badge ' + (PIPELINE_STAGE_BADGE_CLASS[sr.status] || "") + '">' +
        esc(PIPELINE_STAGE_LABEL[sr.status] || sr.status) + "</span> " +
        esc(sr.stage_seq_order + ". " + sr.stage_name) +
        (sr.attempt > 1 ? " (시도 " + sr.attempt + ")" : "") + " " + gateSummary +
        "</li>";
    }).join("");
    return '<details class="card" open><summary>' +
      esc(run.pipeline_template_name || "파이프라인") + " — " +
      esc(PIPELINE_RUN_STATUS_LABEL[run.status] || run.status) + "</summary>" +
      pipelineBreadcrumbHtml(run) +
      GATE_LEGEND_HTML +
      '<ul class="job-progress-list">' + rows + "</ul>" +
      (run.status === "paused_for_approval"
        ? '<p class="muted">사람 승인이 필요한 단계입니다 — "결재/승인" 메뉴에서 처리하세요.</p>'
        : "") +
      "</details>";
  };
  const pipelineExecuteHtml = (pipelineTemplates.length && !pipelineRunActive)
    ? '<select id="ws-pipeline-template-picker" style="margin-right:0.5rem">' +
      pipelineTemplates.map(t => '<option value="' + t.id + '">' + esc(t.name) + " (" + t.stage_count + "단계)</option>").join("") +
      '</select>' +
      '<button class="small" id="ws-execute-pipeline-btn">파이프라인 실행(비용 발생)</button>' +
      '<p class="muted">선택한 파이프라인의 1단계부터 순서대로 진행됩니다 — "사람 승인 필요"로 표시된 단계가 아니면 Gate 기준 통과 시 자동으로 다음 단계로 넘어갑니다.</p>'
    : (pipelineRunActive ? "" : '<p class="muted">등록된(활성) 파이프라인이 없습니다 — "파이프라인 관리" 탭에서 먼저 만드세요.</p>');
  const pipelineSectionHtml =
    '<section class="card"><h4>파이프라인 실행</h4>' +
    pipelineExecuteHtml +
    pipelineTimelineHtml(latestPipelineRun) +
    '</section>';

  // 2026-10-08 — 결과물이 아직 없는
  // 단계에서도 참가자들이 맥락/의사결정을 적어두는 자유 텍스트 공간.
  // 마크다운으로 렌더링(결과물 content_type='markdown'과 같은 렌더러
  // 재사용)하고, 저장 버튼을 누르기 전까진 textarea 그대로 둔다(실시간
  // 공동편집은 스코프 밖 — 미결정 사항).
  const notesCard =
    '<section class="card">' +
    '<h4>메모</h4>' +
    (taskDetail.notes
      ? '<div class="muted" id="ws-notes-view">' + renderContent(taskDetail.notes, "markdown") + '</div>'
      : '<p class="muted" id="ws-notes-view">아직 메모가 없습니다.</p>') +
    '<form id="ws-notes-form">' +
    '<textarea id="ws-notes-input" rows="3" placeholder="이 업무에 대한 메모(마크다운)" ' +
    'style="width:100%;box-sizing:border-box">' + esc(taskDetail.notes || "") + '</textarea>' +
    '<div class="actions"><button class="small" type="submit">메모 저장</button></div>' +
    '</form>' +
    '</section>';

  // 배정·결과물·댓글·Agent 실행·결재·Task 변경을 한 타임라인으로
  // 합친 통합 활동 기록. 기본은 접혀 있다(평소엔 "진행 이력"만으로
  // 충분하고, 이건 "누가 언제 뭘 했는지"를 감사하듯 들여다볼 때만 연다).
  const activityRows = activity.map(a =>
    '<tr><td>' + esc(a.created_at) + '</td>' +
    '<td>사용자 #' + esc(a.actor_id) + '</td>' +
    '<td>' + esc(a.action) + '</td>' +
    '<td class="muted">' + esc(label(a.subject_type)) + (a.subject_id === null ? "" : " #" + esc(a.subject_id)) + '</td></tr>'
  ).join("");
  const activitySection =
    '<details class="card"><summary>활동 타임라인(' + activity.length + '건)</summary>' +
    (activity.length
      ? '<table><thead><tr><th>시각</th><th>행위자</th><th>동작</th><th>대상</th></tr></thead>' +
        '<tbody>' + activityRows + '</tbody></table>'
      : '<p class="muted">아직 기록된 활동이 없습니다.</p>') +
    '</details>';

  // 2026-10-08, Phase 2+3에서 만들었던 "참가자" 카드(결재 없는 가벼운
  // 초대 + ws-invite-input 검색창)는 사용자 요청(2026-10-14, "내 업무
  // 메뉴에서 참가자 기능 삭제해줘")으로 Task 워크스페이스 전체에서
  // 제거했다 — 위 participants fetch 제거 지점 주석 참고.

  // 2026-10-08, Phase 4 — 상태/우선순위는
  // select 변경 즉시 저장(드래그 없이 UI를 단순화하는 설계 의도를
  // select onchange로 구현 — "버튼"이 아니라 "select"지만 드래그앤드롭을
  // 피한다는 본질은 같다).
  const STATUS_OPTIONS = ["backlog", "queued", "in_progress", "done"];
  const PRIORITY_OPTIONS = ["low", "medium", "high", "urgent"];
  const statusPriorityBar =
    '<div class="inline" style="margin:0.25rem 0 0.75rem">' +
    '<label>상태 <select id="ws-status-select">' +
    STATUS_OPTIONS.map(s => '<option value="' + s + '"' +
      (s === taskDetail.status ? " selected" : "") + '>' + esc(label(s)) + '</option>').join("") +
    '</select></label>' +
    '<label>우선순위 <select id="ws-priority-select">' +
    PRIORITY_OPTIONS.map(p => '<option value="' + p + '"' +
      (p === taskDetail.priority ? " selected" : "") + '>' + esc(label(p)) + '</option>').join("") +
    '</select></label>' +
    '</div>';

  // 사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도 예산
  // 추적") — 파이프라인 단계 시도 + Crew 재실행을 합산한 총 재시도
  // 수와, 확정 배정 단가 기반 추정 비용 + 실제 토큰 사용량을 보여준다.
  // admin/manager에게만 "재시도 예산" 입력을 보여준다(거버넌스성
  // 설정이라 `priority`/`status`와 달리 참가자 전체에 안 열어둠).
  const budgetCardHtml = budget
    ? '<div class="card" style="margin:0.25rem 0 0.75rem">' +
      '<div class="stat-row"><span>총 재시도</span><span>' +
      budget.retries.total_retries + '회(파이프라인 ' + budget.retries.pipeline_stage_attempts +
      ' + Crew ' + budget.retries.crew_run_count + ')</span></div>' +
      '<div class="stat-row"><span>추정 비용(단가 기반)</span><span>' +
      budget.cost.estimated_cost.toFixed(2) + '</span></div>' +
      '<div class="stat-row"><span>실제 토큰 사용량</span><span>' +
      budget.cost.token_usage.total_tokens + '</span></div>' +
      (isSupervisor
        ? '<div class="inline" style="margin-top:0.4rem">' +
          '<label>재시도 예산(빈 값=무제한) <input type="number" min="0" id="ws-retry-budget-input" ' +
          'value="' + (taskDetail.max_retry_budget == null ? "" : taskDetail.max_retry_budget) + '" style="width:5rem"></label>' +
          '<button class="small" type="button" id="ws-retry-budget-save">저장</button>' +
          '</div>'
        : '') +
      '</div>'
    : "";

  // 의존관계 그래프(사용자 결정: Riido 수준 시각화). 제거는
  // SVG 안에서 처리하기보다(클릭 판별이 번거롭다) 그래프 아래 평범한
  // 목록에 "제거" 버튼을 둬서 접근성·구현 단순성을 둘 다 잡는다 —
  // 이 task가 의존하는 쪽(`e.task_id === taskId`)만 여기서 지울 수
  // 있다(다른 task가 이 task에 의존하는 edge는 그 task 화면에서 지운다).
  const dependencyListRows = depGraph.edges.map(e => {
    const fromNode = depGraph.nodes.find(n => n.id === e.task_id);
    const toNode = depGraph.nodes.find(n => n.id === e.depends_on_task_id);
    return '<li>' + esc(fromNode ? fromNode.role_name : "#" + e.task_id) +
      ' → ' + esc(toNode ? toNode.role_name : "#" + e.depends_on_task_id) +
      ' <span class="muted">완료를 기다림</span>' +
      (e.task_id === taskId
        ? ' <button class="small danger" data-remove-dependency="' + e.depends_on_task_id + '">제거</button>'
        : '')
      + '</li>';
  }).join("");
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") —
  // 같은 브리프의 다른 할 일(이미 선행으로 걸린 것·자기 자신 제외)만
  // 골라 select로 보여준다. 다른 브리프 작업을 선행으로 걸고 싶으면
  // (백엔드는 허용하지만 드문 경우) API를 직접 써야 한다 — 그 희소
  // 케이스 때문에 모두가 쓰는 폼을 다시 "ID 기억하기"로 되돌리지 않는다.
  const existingDepIds = new Set(
    depGraph.edges.filter(e => e.task_id === taskId).map(e => e.depends_on_task_id)
  );
  const depCandidates = siblingTasks.filter(t => t.id !== taskId && !existingDepIds.has(t.id));
  const dependencyForm = depCandidates.length
    ? '<form class="inline" id="ws-dependency-form">' +
      '<select id="ws-dependency-input" required style="min-width:220px"><option value="">— 선행 작업 선택 —</option>' +
      depCandidates.map(t =>
        '<option value="' + t.id + '">#' + t.id + " " + esc(t.role_name) + " (" + esc(label(t.status)) + ")</option>"
      ).join("") +
      '</select>' +
      '<button class="small" type="submit">선행 작업 추가</button>' +
      '</form>'
    : '<p class="muted">같은 브리프에 선행 작업으로 추가할 수 있는 다른 할 일이 없습니다.</p>';
  const dependencySection =
    '<section class="card">' +
    '<h4>의존관계</h4>' +
    '<p class="muted">이 업무와 연결된 선행/후행 작업을 그래프로 보여줍니다 — 테두리 색은 우선순위(빨강=긴급·주황=높음·파랑=보통·회색=낮음), 네모를 클릭하면 그 업무로 이동합니다.</p>' +
    '<div style="overflow-x:auto">' + renderDependencyGraphSvg(depGraph, taskId) + '</div>' +
    (dependencyListRows ? '<ul>' + dependencyListRows + '</ul>' : '') +
    dependencyForm +
    '</section>';

  // 사용자 요청(2026-09-29, "UI/UX를 개선해줘") — 원래 textarea·provider/
  // model·"LLM으로 도와줘"·"결과물 제출"이 한 줄(.inline flex-wrap)에 전부
  // 뒤섞여 있어 좁은 화면에서 텍스트가 select 옆에 끼어 줄바꿈이 어색했다.
  // textarea를 독립된 줄로 분리하고, "결과물 작성"과 "AI 에이전트 실행"을
  // 서로 다른 카드로 나눠 두 행동의 성격이 다르다는 걸 시각적으로 구분한다.
  detail.innerHTML =
    '<h3>협업 워크스페이스 — 할 일 #' + esc(taskId) + '</h3>' +
    statusPriorityBar +
    budgetCardHtml +
    finalResultCard +
    notesCard +
    // 2026-10-08 — 이
    // Task 전용 대화(`conversations.linked_task_id`)를 여기에
    // 임베드한다. 결과물 유무와 무관하게 항상 노출(그때 발견한
    // "결과물 0건이면 대화 자체가 불가능했던" 구조적 한계를 여기서
    // 해소 — 대화는 더 이상 deliverable에 종속되지 않는다).
    '<div id="ws-conversation-' + taskId + '"><p class="muted">대화를 불러오는 중…</p></div>' +
    dependencySection +
    // 사용자 요청(2026-10-19, "각각이 모두 필요하며 동작하는 기능인지
    // 확인하고... 합리적인 방식으로 순서를 조정... 진행이력, 활동
    // 타임라인이 중간에 보이는 것이 맞는지") — 실측 점검 결과 10개
    // 섹션 전부 실제로 동작하는 기능이었다(미사용/죽은 코드 없음).
    // 다만 순서가 "상태·메모·대화·의존관계(맥락 파악) → 진행
    // 이력/활동 타임라인(과거 기록) → 결과물 작성/AI 실행/파이프라인
    // 실행(할 일)"이라, 지금 업무를 "하러" 들어온 사람이 액션 버튼에
    // 닿기 전에 길어질 수 있는 과거 이력 목록부터 지나가야 했다.
    // "맥락 파악 → 지금 할 일(액션) → (필요하면 펼쳐보는) 과거 기록"
    // 순서로 바꾼다 — 진행 이력·활동 타임라인은 둘 다 "기록 열람"
    // 범주라 맨 끝으로, 액션 3종(결과물 작성/AI 에이전트 실행/파이프라인
    // 실행)은 의존관계 바로 다음으로 옮긴다.
    '<section class="card">' +
    '<h4>결과물 작성</h4>' +
    '<form id="ws-submit-form">' +
    '<textarea id="ws-content" rows="3" placeholder="결과물 내용을 입력하세요" style="width:100%;box-sizing:border-box"></textarea>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    contentTypeSelectHtml("ws-content-type") +
    modelFields +
    '<button class="small" type="button" id="ws-assist-btn">LLM으로 도와줘(비용 발생)</button>' +
    '<button class="primary" type="submit">결과물 제출</button>' +
    '</div>' +
    '</form>' +
    '</section>' +
    '<section class="card">' +
    '<h4>AI 에이전트 실행</h4>' +
    contentTypeSelectHtml("ws-execute-content-type") + ' ' +
    executeBtnHtml +
    '</section>' +
    // 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고...
    // 에이전트 그룹(crew 구성)을 직관적으로") — 지금까지 여러 Agent를
    // 묶어 이 업무에 투입하려면 팀을 미리 등록·배정·승인까지 마쳐야만
    // 가능했다("팀 Agent 실행" 섹션이 confirmedTeams가 있을 때만
    // 보이는 이유). 팀 전제조건 없이 즉석으로 묶어 바로 실행하려면
    // "Crew 빌더"로 — 팀 등록 여부와 무관하게 항상 노출한다.
    '<section class="card"><h4>여러 Agent를 즉석으로 묶어 실행</h4>' +
    '<p class="muted">팀을 미리 등록하지 않아도 Agent를 그 자리에서 골라 바로 실행할 수 있습니다 — 결과는 이 업무의 결과물로 그대로 쌓입니다.</p>' +
    '<button class="small" id="ws-open-crew-builder-btn">Crew 빌더에서 구성하기</button>' +
    '</section>' +
    (confirmedTeams.length
      ? '<section class="card"><h4>팀 Agent 실행(Crew)</h4>' +
        contentTypeSelectHtml("ws-team-crew-content-type") + ' ' + teamCrewBtnHtml + '</section>'
      : '') +
    pipelineSectionHtml +
    '<details class="workspace-timeline-section">' +
    '<summary class="muted" style="cursor:pointer">진행 이력(중간 과정 포함)</summary>' +
    '<div id="workspace-timeline">' + timeline + '</div>' +
    '</details>' +
    activitySection +
    '<div id="ws-result"></div>';

  if (isSupervisor) wireProviderModelSelect("ws-provider", "ws-model", modelsByProvider);
  if (confirmedTeams.length) {
    if (isSupervisor) {
      wireProviderModelSelect("ws-team-crew-provider", "ws-team-crew-model", modelsByProvider);
    }
    wireCrewAdvancedOptions("ws-team-crew", modelsByProvider, isSupervisor);
  }
  const executePipelineBtn = document.getElementById("ws-execute-pipeline-btn");
  if (executePipelineBtn) {
    executePipelineBtn.onclick = async () => {
      const templateId = Number(document.getElementById("ws-pipeline-template-picker").value);
      // 사용자 요청(2026-11-12, "UI/UX 개선") — 단일 Agent 실행/Crew
      // 실행에는 이미 비용 확인이 있었는데, 가장 비용이 커질 수 있는
      // (여러 단계 × 참여자 수만큼 LLM 호출이 누적되는) 파이프라인
      // 실행에는 확인이 전혀 없었다 — 실측으로 발견한 비대칭 수정.
      if (!(await confirmModal(
        "이 파이프라인을 처음부터 끝까지 실행합니다 — 단계마다 실제 LLM API를 호출해 비용이 발생하고, 참여자·재시도가 많을수록 누적 비용이 커질 수 있습니다. 계속할까요?"
      ))) return;
      try {
        await api("POST", "/api/tasks/" + taskId + "/execute-pipeline", {
          pipeline_template_id: templateId,
        });
        showMsg("global-msg", "파이프라인 실행을 시작했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }
  items.forEach(d => loadDeliverableComments(d.id, taskId, containerId));
  _loadTaskConversation(taskId);

  detail.querySelectorAll("[data-decide-deliverable]").forEach(btn => {
    btn.onclick = async () => {
      const reasonInput = detail.querySelector('[data-decide-reason="' + btn.dataset.decideDeliverable + '"]');
      const reason = reasonInput ? reasonInput.value.trim() : "";
      try {
        await api("POST", "/api/approvals/" + btn.dataset.decideDeliverable + "/decide", {
          approve: btn.dataset.approve === "1",
          reason: reason || null,
        });
        showMsg("global-msg", "처리했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  detail.querySelectorAll("[data-submit-deliverable]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/deliverables/" + btn.dataset.submitDeliverable + "/submit", {});
        showMsg("global-msg", "검토를 요청했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  detail.querySelectorAll("[data-download-deliverable]").forEach(btn => {
    btn.onclick = () => {
      const d = items.find(x => String(x.id) === btn.dataset.downloadDeliverable)
        || (finalResult && String(finalResult.id) === btn.dataset.downloadDeliverable ? finalResult : null);
      if (d) downloadDeliverable(d.content, d.content_type, d.id);
    };
  });

  document.getElementById("ws-notes-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const notesVal = document.getElementById("ws-notes-input").value;
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/notes", { notes: notesVal });
      showMsg("global-msg", "메모를 저장했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자 기능 삭제해줘")으로
  // ws-invite-input 검색-초대 배선을 제거했다(위 participants fetch
  // 제거 지점 주석 참고).

  document.getElementById("ws-status-select").addEventListener("change", async (ev) => {
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/status", { status: ev.target.value });
      showMsg("global-msg", "상태를 변경했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("ws-priority-select").addEventListener("change", async (ev) => {
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/priority", { priority: ev.target.value });
      showMsg("global-msg", "우선순위를 변경했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const retryBudgetSaveBtn = document.getElementById("ws-retry-budget-save");
  if (retryBudgetSaveBtn) retryBudgetSaveBtn.addEventListener("click", async () => {
    const raw = document.getElementById("ws-retry-budget-input").value.trim();
    const maxRetryBudget = raw === "" ? null : Number(raw);
    try {
      await api("PATCH", "/api/tasks/" + taskId + "/retry-budget", { max_retry_budget: maxRetryBudget });
      showMsg("global-msg", "재시도 예산을 저장했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const dependencyFormEl = document.getElementById("ws-dependency-form");
  if (dependencyFormEl) dependencyFormEl.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const dependsOnId = Number(document.getElementById("ws-dependency-input").value);
    if (!dependsOnId) { showMsg("global-msg", "선행 작업을 선택하세요.", "error"); return; }
    try {
      await api("POST", "/api/tasks/" + taskId + "/dependencies", {
        depends_on_task_id: dependsOnId,
      });
      showMsg("global-msg", "선행 작업을 추가했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  detail.querySelectorAll("[data-graph-node]").forEach(node => {
    node.addEventListener("click", () => {
      const targetId = Number(node.dataset.graphNode);
      if (targetId !== taskId) renderCollabWorkspace(targetId, containerId);
    });
  });

  detail.querySelectorAll("[data-remove-dependency]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        await api(
          "DELETE",
          "/api/tasks/" + taskId + "/dependencies/" + btn.dataset.removeDependency
        );
        showMsg("global-msg", "선행 작업을 제거했습니다.", "ok");
        renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  document.getElementById("ws-submit-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const contentVal = document.getElementById("ws-content").value.trim();
    if (!contentVal) { showMsg("global-msg", "내용을 입력해야 합니다.", "error"); return; }
    try {
      await api("POST", "/api/tasks/" + taskId + "/deliverables", {
        content: contentVal,
        content_type: document.getElementById("ws-content-type").value,
      });
      showMsg("global-msg", "결과물을 제출했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const assistBtn = document.getElementById("ws-assist-btn");
  assistBtn.onclick = async () => {
    const draftContext = document.getElementById("ws-content").value.trim() || null;
    const payload = { draft_context: draftContext, content_type: document.getElementById("ws-content-type").value };
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = modelSelectValue("ws-model");
      if (provider) payload.provider = provider;
      if (model) payload.model = model;
    }
    try {
      const result = await withBusy(assistBtn, "생성 중…(실제 API 호출)", () => api("POST", "/api/tasks/" + taskId + "/assist", payload));
      document.getElementById("ws-content").value = result.draft;
      showMsg("global-msg", "LLM 초안을 받았습니다 — 검토·수정 후 제출하세요.", "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };

  const attachBtn = document.getElementById("ws-execute-attach-btn");
  const attachInput = document.getElementById("ws-execute-attachment");
  const attachNameEl = document.getElementById("ws-execute-attachment-name");
  if (attachBtn) {
    attachBtn.onclick = () => attachInput.click();
    attachInput.addEventListener("change", () => {
      const f = attachInput.files && attachInput.files[0];
      attachNameEl.textContent = f ? ("첨부됨: " + f.name) : "";
    });
  }

  const executeBtn = document.getElementById("ws-execute-agent-btn");
  executeBtn.onclick = async () => {
    if (!(await confirmModal("이 업무에 배정된 AI 에이전트를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?"))) return;
    const formData = new FormData();
    formData.append("content_type", document.getElementById("ws-execute-content-type").value);
    const agentPicker = document.getElementById("ws-agent-picker");
    if (agentPicker) formData.append("agent_id", agentPicker.value);
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = modelSelectValue("ws-model");
      if (provider) formData.append("provider", provider);
      if (model) formData.append("model", model);
    }
    const attachedFile = attachInput && attachInput.files && attachInput.files[0];
    if (attachedFile) formData.append("attachment", attachedFile);
    try {
      const queued = await apiUpload("/api/tasks/" + taskId + "/execute-agent", formData);
      // 202 응답 직후 즉시 새로고침해 "생성 중…" 카드를 먼저 보여준다
      // (Phase 4 — 응답이 빨리 오는 게 이 비동기화의 핵심이라, 여기서
      // 폴링이 끝날 때까지 버튼을 막아두면 의미가 없다).
      renderCollabWorkspace(taskId, containerId);
      const job = await pollExecutionJob(taskId, queued.job_id);
      if (job.status === "succeeded") {
        showMsg("global-msg", "AI 에이전트 실행이 완료됐습니다.", "ok");
      } else {
        showMsg("global-msg", "AI 에이전트 실행이 실패했습니다: " + job.error, "error");
      }
      if (isViewingWorkspace(taskId, containerId)) renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };

  const openCrewBuilderBtn = document.getElementById("ws-open-crew-builder-btn");
  if (openCrewBuilderBtn) {
    openCrewBuilderBtn.onclick = () => {
      _pendingCrewBuilderTaskId = taskId;
      selectTab("crew");
    };
  }

  const executeTeamCrewBtn = document.getElementById("ws-execute-team-crew-btn");
  if (executeTeamCrewBtn) {
    executeTeamCrewBtn.onclick = async () => {
      const teamPicker = document.getElementById("ws-team-picker");
      const teamId = teamPicker ? Number(teamPicker.value) : confirmedTeams[0].resource_id;
      if (!(await confirmModal("이 팀의 Agent 멤버 전원을 Crew로 실행합니다(실제 LLM API 호출, 비용은 참여 Agent 수만큼 커질 수 있습니다). 계속할까요?"))) return;
      const processVal = document.getElementById("ws-team-crew-process").value;
      const payload = Object.assign({
        team_id: teamId,
        process: processVal || null,
        content_type: document.getElementById("ws-team-crew-content-type").value,
      }, crewAdvancedOptionsPayload("ws-team-crew", isSupervisor));
      if (isSupervisor) {
        const provider = document.getElementById("ws-team-crew-provider").value;
        const model = modelSelectValue("ws-team-crew-model");
        if (provider) payload.provider = provider;
        if (model) payload.model = model;
      }
      try {
        const queued = await api("POST", "/api/tasks/" + taskId + "/execute-team-crew", payload);
        renderCollabWorkspace(taskId, containerId);
        const jobs = await Promise.all(queued.job_ids.map(id => pollExecutionJob(taskId, id)));
        if (jobs.every(j => j.status === "succeeded")) {
          showMsg("global-msg", "팀 Agent 실행이 완료됐습니다(" + jobs.length + "건).", "ok");
        } else {
          const failed = jobs.filter(j => j.status === "failed").length;
          showMsg("global-msg", "팀 Agent 실행 중 " + failed + "건이 실패했습니다 — 진행 이력을 확인하세요.", "error");
        }
        if (isViewingWorkspace(taskId, containerId)) renderCollabWorkspace(taskId, containerId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }

  // 페이지를 새로고침했거나 다른 탭에 갔다 왔을 때도, 이미 돌고 있던
  // job이 있으면 자동으로 이어서 폴링해 끝나는 대로 갱신한다 — 버튼을
  // 눌러야만 완료를 알 수 있는 건 아니다.
  if (hasRunningJob) {
    pollExecutionJob(taskId, latestJob.id).then(job => {
      if (!isViewingWorkspace(taskId, containerId)) return;
      if (job.status === "failed") {
        showMsg("global-msg", "AI 에이전트 실행이 실패했습니다: " + job.error, "error");
      }
      renderCollabWorkspace(taskId, containerId);
    }).catch(() => {});
  }
}

// 백그라운드 폴링이 끝났을 때, 사용자가 그사이 다른 업무/탭으로
// 이동했다면 지금 화면을 덮어쓰지 않기 위한 가드(Phase 4).
function isViewingWorkspace(taskId, containerId = "mywork-detail") {
  const detail = document.getElementById(containerId);
  return !!detail && detail.dataset.taskId === String(taskId);
}

/* 실측으로 발견한 심각한 버그(사용자 요청, 2026-09-28, "UI/UX 내용
   중... 분석하고 개선해줘") — 원래 이 함수 하나가 "댓글 목록 갱신"과
   "폼에 submit 리스너 붙이기"를 같이 했다. 그런데 submit 성공 콜백
   *안에서* 같은 함수를 다시 불러 목록을 갱신했는데, 그 재호출이 같은
   <form> DOM 노드에 리스너를 addEventListener로 "또" 붙였다(노드 자체는
   안 바뀌므로 이전 리스너가 안 사라짐) — 댓글을 한 번 달 때마다
   리스너가 1→2→4→8개로 누적돼, 다음 댓글부터는 같은 POST가 리스너
   개수만큼 중복 발사되는 지수적 중복 제출 버그였다. "목록 갱신"과
   "리스너를 한 번만 붙이기"를 분리해서 고쳤다. */
async function refreshComments(deliverableId) {
  const box = document.getElementById("comments-" + deliverableId);
  if (!box) return;
  try {
    const comments = await api("GET", "/api/deliverables/" + deliverableId + "/comments");
    // 2026-10-08, Phase 3 — Agent가
    // `@멘션` 트리거로 남긴 응답 댓글을 사람 댓글과 시각적으로 구분한다
    // (배경색 + "AI 에이전트" 배지, 설계 문서 UI 설계 그대로). 사용자
    // 결정(a) — 댓글 트리거 결과는 항상 댓글로만 남고, deliverable로
    // 승격하려면 사람이 명시적으로 "이걸 결과물로 제출"을 눌러야 한다.
    // **새 엔드포인트를 만들지 않는다** — 버튼은 그 댓글 내용을 기존
    // "결과물 작성" textarea(#ws-content)에 채워 넣기만 하고, 실제
    // 제출은 기존 `ws-submit-form`(→ 기존 결재 체인)을 그대로 탄다.
    box.innerHTML = comments.map(c =>
      '<div class="' + (c.author_type === "agent" ? "chat-agent" : "muted") + '">↳ ' +
      (c.author_type === "agent" ? '<span class="gate-badge gate-ok">AI 에이전트</span> ' : '') +
      esc(c.content) +
      (c.author_type === "agent"
        ? ' <button class="small" data-promote-comment="' + c.id +
          '" data-promote-content="' + escAttr(c.content) + '">이걸 결과물로 제출</button>'
        : '') +
      '</div>'
    ).join("");
  } catch (e) {
    // 참가자가 아니면 403 — 조용히 무시(타임라인 자체는 이미 조회 권한 확인됨).
  }
}

// UI 설계(2026-10-08) — "@" 입력 시
// 참가자 목록 자동완성 드롭다운. 1차 구현 때는 정적 힌트 텍스트로만
// 대체했는데, 실제 누락으로 확인돼("HAIR-Platform... 구현에 누락
// 부분이 없는지 확인해줘") 보완한다. 외부 라이브러리 없이 순수 JS로
// 커서 위치 기준 마지막 "@" 뒤 텍스트를 쿼리 삼아 필터링한다.
//
// 채팅 호출 UX 개선(2026-10-10, 사용자 요청 "채팅에서 @로 생성된
// AI Agent와 사용자를 호출하려면 미리 전체 이름을 알고 있어야 해") —
// `opts.search`가 주어지면(대화 메시지창만; Task 결과물 댓글은 기존과
// 완전히 같은 "로컬 참가자만" 동작을 유지한다 — `opts` 생략 시 이전
// 동작과 100% 동일) 조직 전체 검색(`/api/conversations/mentionable`,
// 역할/스킬까지 부분 일치 + 이 사용자가 최근 초대한 대상 우선)도 함께
// 조회해 "참가자"/"초대 후 멘션" 두 그룹으로 보여준다. 후자를 고르면
// `opts.onInvited`로 먼저 초대한 뒤 멘션을 끼워 넣는다 — 화면 이동
// 없이 "검색→클릭"만으로 처음 보는 이름도 바로 부를 수 있다.
// 사용자 요청(2026-10-14, "초대화면 포함 선택 창에서 상하 및 엔터
// 키로도 선택 가능하도록 개선해줘") — 멘션 자동완성(`setupMentionAutocomplete`)
// 과 Task 워크스페이스 참가자 초대(`ws-invite-dropdown`)가 똑같은
// "입력하면 뜨는 목록에서 고른다" 패턴을 각자 따로 구현하고 있어서,
// 키보드 내비게이션도 공용 헬퍼 하나로 둘 다 적용한다. `.mention-dropdown
// li.active` CSS는 이미 있었다(하이라이트 스타일만 있고 실제로 켜주는
// 로직이 없었다).
function wireDropdownKeyboardNav(inputEl, dropdownEl, itemSelector, onSelect) {
  let highlightedIndex = -1;

  function items() {
    return Array.from(dropdownEl.querySelectorAll(itemSelector));
  }

  function updateHighlight() {
    const els = items();
    els.forEach((el, i) => el.classList.toggle("active", i === highlightedIndex));
    const active = els[highlightedIndex];
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  inputEl.addEventListener("keydown", (ev) => {
    if (dropdownEl.style.display === "none" || !dropdownEl.innerHTML) return;
    const els = items();
    if (!els.length) return;
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      highlightedIndex = (highlightedIndex + 1) % els.length;
      updateHighlight();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      highlightedIndex = (highlightedIndex - 1 + els.length) % els.length;
      updateHighlight();
    } else if (ev.key === "Enter") {
      // 하이라이트된 항목이 없으면(화살표로 고르기 전) 평범한 Enter로
      // 둔다 — 메시지 전송 폼의 기본 submit을 가로채지 않는다.
      if (highlightedIndex >= 0 && highlightedIndex < els.length) {
        ev.preventDefault();
        onSelect(els[highlightedIndex]);
      }
    } else if (ev.key === "Escape") {
      dropdownEl.style.display = "none";
      dropdownEl.innerHTML = "";
    }
  });

  return {
    // 드롭다운을 새로 그릴 때마다 첫 항목을 바로 하이라이트한다 — 타이핑
    // 후 바로 Enter만 쳐도 가장 위 후보가 선택되는, 흔한 자동완성 UX.
    highlightFirst() {
      highlightedIndex = items().length ? 0 : -1;
      updateHighlight();
    },
  };
}

function setupMentionAutocomplete(inputEl, dropdownEl, participants, opts) {
  opts = opts || {};
  // 사용자 보고(2026-10-14, "여전히 대화 참가자 pm_kim이 pm_kim에게
  // 멘션을 보낼 수 있어, 메시지 목록에서 본인은 제외해줘") — 자기
  // 멘션의 "효과"(기본 AI 억제·알림)는 이미 고쳤지만, 애초에 자동완성
  // 후보 목록에 본인이 그대로 떠서 고를 수 있었다. 이미 초대된 참가자
  // (로컬 후보)와 아직 초대 안 된 조직 전체 검색 결과(원격 후보) 둘
  // 다에서 "나 자신"(사람 타입 + 내 user id)을 제외한다.
  // 사용자 보고(2026-10-28, "대화에서 한번만 호출하고 대화에 참여했는데
  // 참가자에 두번 표기되고 있어") 근본원인 — `participants` 배열은
  // `_inviteAndTrackParticipant()`가 초대 성공 시 바로 push해 최신
  // 상태를 유지하는데, 이 함수는 그 배열을 한 번만 `.filter().map()`
  // 해서 `localCandidates`라는 별도 배열을 만들어 썼다 — 즉 설정 시점
  // 스냅샷이라 이후 초대된 참가자가 여기 반영되지 않았다. 그 결과
  // 같은 대화에서 같은 Agent를 두 번째로 멘션하면(이미 초대된 상태인데도)
  // 로컬 후보로 안 잡혀 "초대 필요"(원격) 쪽으로 다시 분류되고,
  // 서버는 중복 초대를 거절하지만(참가자 테이블엔 중복 안 생김) 이
  // 화면은 그 멘션을 "새 초대"로 착각해 참가자 목록에 같은 이름을
  // 한 번 더 그려 넣었다. `participants` 배열에서 매번 다시 계산해
  // 최신 상태를 반영한다.
  function getLocalCandidates() {
    return participants
      .filter(p => p.display_name)
      .filter(p => !(p.participant_type === "human" && p.participant_id === ME.id))
      .map(p => ({ name: p.display_name, type: p.participant_type, id: p.participant_id }));
  }
  if (!getLocalCandidates().length && !opts.search) return;

  // 사용자 요청(2026-10-28, "대화에서 @로 호출시 너무 많은 멤버가
  // 노출되고있어 사람/AI/팀 카테고리 구분을 추가, ... 호출자를 각각
  // 구분하는 등 개선") — (1) 섹션 구분: 결과를 사람/AI/팀 3개 묶음으로
  // 나눠 묶음별 머리글 + 개수를 보여주고, 한 묶음이 너무 길면
  // MAX_PER_CATEGORY개까지만 보여주고 나머지는 "+N개 더"로 접는다.
  // (2) 타이핑 접두사로 범위 좁히기: "@AI 번역"처럼 "사람"/"AI"/"팀"으로
  // 시작하면 그 종류만 검색한다 — 백엔드는 그대로 두고(이미 사람/AI/팀을
  // 구분해 돌려주고 있다) 프런트에서만 그 접두사를 떼어 필터링한다.
  const CATEGORY_LABEL = { human: "사람", agent: "AI", team: "팀" };
  const CATEGORY_ORDER = ["human", "agent", "team"];
  const CATEGORY_PREFIXES = [
    { prefix: "사람", type: "human" },
    { prefix: "AI", type: "agent" },
    { prefix: "팀", type: "team" },
  ];
  const MAX_PER_CATEGORY = 5;

  function parseCategoryFilter(raw) {
    // "게임 AI팀"처럼 이름 자체에 "AI"가 들어간 후보를 찾으려고 "@AI"만
    // 친 상태(아직 뒤에 공백 없음)는 카테고리 필터가 아니라 여전히
    // 자유 텍스트 검색이어야 한다(기존 동작 유지) — 접두사 뒤에 공백이
    // 와서 "다음 단어를 입력하기 시작했다"가 확실해졌을 때만 켠다.
    const lower = raw.toLowerCase();
    for (const { prefix, type } of CATEGORY_PREFIXES) {
      const p = prefix.toLowerCase();
      if (lower.startsWith(p + " ")) {
        return { type, rest: raw.slice(prefix.length).replace(/^\\s+/, "") };
      }
    }
    return { type: null, rest: raw };
  }

  function renderCategoryGroup(items, isRemote) {
    let html = "";
    for (const type of CATEGORY_ORDER) {
      const group = items.filter(it => it.type === type);
      if (!group.length) continue;
      html += '<li class="muted" style="padding:0.2rem 0.5rem;font-size:0.75rem">' +
        CATEGORY_LABEL[type] + ' (' + group.length + ')</li>';
      html += group.slice(0, MAX_PER_CATEGORY).map(c => {
        const inviteAttrs = isRemote
          ? ' data-mention-invite-type="' + c.type + '" data-mention-invite-id="' + c.id + '"'
          : "";
        return '<li data-mention-name="' + escAttr(c.name) + '"' + inviteAttrs + '>' + esc(c.name) +
          (isRemote ? ' <span class="muted">· 초대 필요</span>' : "") + '</li>';
      }).join("");
      if (group.length > MAX_PER_CATEGORY) {
        html += '<li class="muted" style="padding:0.15rem 0.5rem;font-size:0.72rem">+' +
          (group.length - MAX_PER_CATEGORY) + '개 더 — 계속 입력해 좁혀보세요</li>';
      }
    }
    return html;
  }

  let searchToken = 0;
  let searchDebounce = null;

  function closeDropdown() {
    dropdownEl.style.display = "none";
    dropdownEl.innerHTML = "";
    searchToken++; // 아직 안 돌아온 원격 검색 응답을 무효화한다
  }

  function insertMention(name) {
    const val = inputEl.value;
    const cursor = inputEl.selectionStart;
    const atIndex = val.lastIndexOf("@", cursor - 1);
    if (atIndex === -1) return;
    const before = val.slice(0, atIndex);
    const after = val.slice(cursor);
    const insertedPrefix = before + "@" + name + " ";
    inputEl.value = insertedPrefix + after;
    inputEl.setSelectionRange(insertedPrefix.length, insertedPrefix.length);
    closeDropdown();
    inputEl.focus();
  }

  async function selectCandidate(li) {
    const name = li.dataset.mentionName;
    if (li.dataset.mentionInviteType && opts.onInvited) {
      try {
        await opts.onInvited({
          type: li.dataset.mentionInviteType,
          id: Number(li.dataset.mentionInviteId),
          name,
        });
      } catch (e) { return; } // onInvited가 이미 에러 메시지를 보여줬다
    }
    insertMention(name);
    // 사용자 요청(2026-10-22, "대화자(Human, Agent) 호출시 Skill 선택은
    // 취소되도록 해줘") — 스킬을 고른 채로 멘션까지 보내면 Agent/Team
    // 응답과 별개로 스킬 그라운딩된 "기본 AI" job이 하나 더 떴다(중복
    // 응답 혼란). 백엔드도 같이 고쳤지만(`app.py` — 멘션이 있으면 스킬이
    // 선택돼 있어도 기본 AI를 더 안 띄움), 멘션을 고르는 시점에 드롭다운
    // 선택 자체를 바로 지워서 사용자가 "지금은 스킬이 아니라 멘션 대상이
    // 응답한다"는 걸 눈으로 바로 알 수 있게 한다.
    if (opts.onMentionSelected) opts.onMentionSelected();
  }

  const keyboardNav = wireDropdownKeyboardNav(
    inputEl, dropdownEl, "li[data-mention-name]", selectCandidate
  );

  function renderGroups(localMatches, remoteItems, showHint) {
    if (!localMatches.length && !remoteItems.length) {
      dropdownEl.style.display = "none";
      dropdownEl.innerHTML = "";
      return;
    }
    let html = showHint
      ? '<li class="muted" style="padding:0.2rem 0.5rem;font-size:0.72rem">Tip: "AI "/"사람 "/"팀 "으로 시작하면 그 종류만 볼 수 있어요</li>'
      : "";
    html += renderCategoryGroup(localMatches, false);
    if (remoteItems.length) {
      html += '<li class="muted" style="padding:0.25rem 0.5rem">초대 후 멘션</li>' +
        renderCategoryGroup(remoteItems, true);
    }
    dropdownEl.innerHTML = html;
    dropdownEl.style.display = "block";
    keyboardNav.highlightFirst();
  }

  function handleQuery(query) {
    // 실측으로 발견한 버그(2026-10-08, 라이브 검증) — 원래 "@뒤에
    // 공백이 있으면 멘션이 끝난 것"으로 보고 닫았는데, "게임 AI팀"처럼
    // 이름 자체에 공백이 있는 참가자(흔한 한국어 Team/Agent 이름)를
    // 끝까지 타이핑하면 중간 공백에서 드롭다운이 영구히 닫혀버렸다.
    // 공백 여부로 판단하지 않고, 후보 이름에 그 쿼리가 부분 문자열로
    // 남아있는 동안만 계속 열어둔다.
    const { type: categoryType, rest } = parseCategoryFilter(query);
    const showHint = !categoryType && query.length === 0;
    const localCandidates = getLocalCandidates();
    const localMatches = localCandidates
      .filter(c => !categoryType || c.type === categoryType)
      .filter(c => c.name.includes(rest));
    if (!opts.search) { renderGroups(localMatches, [], showHint); return; }
    // 로컬 매치는 즉시 보여주고(깜빡임 방지), 조직 전체 검색만 디바운스
    // 한다 — 매치가 하나도 없는 상태에서 디바운스 중 잠깐 닫았다 여는
    // 깜빡임을 피하려고, 로컬 매치가 없을 땐 응답이 올 때까지 아무
    // 것도 안 바꾼다(기존 상태 유지).
    if (localMatches.length) renderGroups(localMatches, [], showHint);
    clearTimeout(searchDebounce);
    const myToken = ++searchToken;
    searchDebounce = setTimeout(async () => {
      let remote;
      try {
        remote = await opts.search(rest);
      } catch (e) {
        return;
      }
      if (myToken !== searchToken) return; // 늦게 도착한 응답은 버린다
      const localKeys = new Set(localCandidates.map(c => c.type + ":" + c.id));
      let remoteItems = [
        ...remote.humans.map(h => ({ type: "human", id: h.id, name: h.username })),
        ...remote.agents.map(a => ({ type: "agent", id: a.id, name: a.role_name })),
        ...remote.teams.map(t => ({ type: "team", id: t.id, name: t.name })),
      ]
        .filter(it => !localKeys.has(it.type + ":" + it.id))
        .filter(it => !(it.type === "human" && it.id === ME.id));
      if (categoryType) remoteItems = remoteItems.filter(it => it.type === categoryType);
      renderGroups(localMatches, remoteItems, showHint);
    }, 200);
  }

  inputEl.addEventListener("input", () => {
    const val = inputEl.value;
    const cursor = inputEl.selectionStart;
    const atIndex = val.lastIndexOf("@", cursor - 1);
    if (atIndex === -1) { closeDropdown(); return; }
    handleQuery(val.slice(atIndex + 1, cursor));
  });

  // blur가 드롭다운 클릭보다 먼저 발생해 클릭이 씹히는 것을 막기 위해
  // mousedown(클릭보다 먼저 발생)에서 선택을 처리하고, blur는 약간
  // 지연 후 닫는다.
  inputEl.addEventListener("blur", () => setTimeout(closeDropdown, 150));
  dropdownEl.addEventListener("mousedown", async (ev) => {
    const li = ev.target.closest("[data-mention-name]");
    if (!li) return;
    ev.preventDefault();
    await selectCandidate(li);
  });
}

// 사용자 요청(2026-10-14, "내 업무 메뉴에서 참가자 기능 삭제해줘")으로
// 댓글 입력창의 @멘션 참가자 자동완성(setupMentionAutocomplete 호출)을
// 제거했다 — 입력창 자체는 평범한 댓글 작성용으로 남는다.
async function loadDeliverableComments(deliverableId, taskId, containerId) {
  await refreshComments(deliverableId);
  // Phase 3 — "이걸 결과물로 제출" 버튼은 `refreshComments()`가
  // `box.innerHTML`을 매번 통째로 새로 그리므로, 그 자식 버튼에 직접
  // 리스너를 매번 새로 붙이면 이전에 겪었던 "리스너 누적" 버그가
  // 똑같이 재발한다 — 부모 `box`(innerHTML 교체에도 노드 자체는 안
  // 바뀜)에 한 번만 위임 바인딩한다.
  const box = document.getElementById("comments-" + deliverableId);
  if (box) {
    box.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-promote-comment]");
      if (!btn) return;
      const contentArea = document.getElementById("ws-content");
      if (!contentArea) return;
      contentArea.value = btn.dataset.promoteContent;
      contentArea.scrollIntoView({ behavior: "smooth", block: "center" });
      contentArea.focus();
      showMsg("global-msg", "결과물 작성란에 채웠습니다 — 확인 후 \\"결과물 제출\\"을 눌러주세요.", "ok");
    });
  }
  const form = document.querySelector('[data-comment-form="' + deliverableId + '"]');
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const input = document.getElementById("comment-input-" + deliverableId);
      const val = input.value.trim();
      if (!val) return;
      try {
        const result = await api(
          "POST", "/api/deliverables/" + deliverableId + "/comments", { content: val }
        );
        input.value = "";
        await refreshComments(deliverableId);
        // Phase 3 — @멘션이 트리거됐으면 백그라운드 완료를 폴링해
        // 응답이 오는 대로 댓글 목록을 다시 갱신한다(기존 "AI 에이전트
        // 실행" 버튼의 pollExecutionJob() 패턴 재사용).
        if (result.triggered_jobs && result.triggered_jobs.length) {
          showMsg("global-msg", "Agent에게 알렸습니다 — 응답을 기다리는 중…", "ok");
          Promise.all(result.triggered_jobs.map(j => pollExecutionJob(taskId, j.job_id)))
            .then(() => refreshComments(deliverableId))
            .catch(() => refreshComments(deliverableId));
        }
        if (result.skipped_agent_ids && result.skipped_agent_ids.length) {
          showMsg("global-msg", "다른 실행이 진행 중이라 일부 Agent는 호출하지 못했습니다 — 완료 후 다시 멘션해 주세요.", "error");
        }
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
}

/* ---------- 업무 요청/할 일 ---------- */

async function renderBriefs(content) {
  const canWrite = ["admin", "manager", "member"].includes(ME.role);
  const isAdmin = ME.role === "admin";
  const briefs = await api("GET", "/api/briefs");
  let html = "<section class=\\"card\\"><h2>업무 요청 등록</h2>";
  if (canWrite) {
    html += helpBox(
      '<ul class="muted help-list">' +
      '<li>여기서 하는 일은 "제안된 역할" 칸을 채우는 것뿐입니다 — 실제 AI 분석이 아니라 입력한 텍스트를 줄바꿈 단위로 그대로 나누는 규칙 기반 처리라 비용이 없고 즉시 끝납니다.</li>' +
      '<li>이 제안은 참고용이고, 실제 인원 배정 단위인 "할 일(Task)"은 아래 "할 일로 나누기"에서 직접 만들어야 합니다.</li>' +
      '<li>뷰어(viewer) role은 조회만 가능하고 제출은 못 합니다.</li>' +
      '</ul>'
    ) +
      '<form class="inline" id="brief-form">' +
      '<textarea id="brief-text" placeholder="한 줄에 역할 하나씩 입력하세요(줄바꿈 단위로 나뉩니다). 예:&#10;백엔드 개발자 1명&#10;UI/UX 디자이너 1명" rows="3" style="flex:1;min-width:240px"></textarea>' +
      '<button class="primary" type="submit">제출</button></form>';
  }
  html += "</section><section class=\\"card\\"><h2>업무 요청 목록</h2>" +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"할 일로 나누기"로 만든 할 일(Task)이 "리소스 매칭/배정"·"AI 에이전트 생성" 탭에서 쓸 단위입니다.</li>' +
      '<li><b>삭제</b>(관리자 전용)는 이 업무 요청과 그 아래 할 일·배정·이력까지 전부 되돌릴 수 없게 지웁니다 — 확정된(confirmed) 배정이 하나라도 있으면 막힙니다. 이땐 "할 일 보기 → 상세"에서 그 배정을 먼저 <b>"배정 취소"</b>하세요.</li>' +
      '</ul>'
    );
  html += table(["ID", "요청자", "내용", "상태", "제안된 역할", "동작"], briefs.map(b => {
    let actions = '<button class="small" data-decompose="' + b.id + '">할 일로 나누기</button>' +
      '<button class="small" data-viewtasks="' + b.id + '">할 일 보기</button>';
    if (isAdmin) {
      actions += '<button class="small danger" data-delete-brief="' + b.id + '">삭제</button>';
    }
    return [b.id, esc(b.submitted_by_username || ("#" + b.submitted_by)), esc(b.raw_text), esc(label(b.status)), esc(b.stub_roles.join(", ")),
      canWrite ? '<div class="actions">' + actions + "</div>" : ""];
  }));
  html += '<div id="brief-detail"></div></section>';
  content.innerHTML = html;

  const form = document.getElementById("brief-form");
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const rawText = document.getElementById("brief-text").value;
      if (!rawText.trim()) {
        showMsg("global-msg", "업무 요청 내용을 입력하세요.", "error");
        return;
      }
      try {
        await api("POST", "/api/briefs", { raw_text: rawText });
        showMsg("global-msg", "업무 요청을 제출했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
  content.querySelectorAll("[data-decompose]").forEach(btn => {
    btn.onclick = () => {
      const brief = briefs.find(b => b.id === Number(btn.dataset.decompose));
      renderDecomposeForm(btn.dataset.decompose, brief ? brief.stub_roles : []);
    };
  });
  content.querySelectorAll("[data-viewtasks]").forEach(btn => {
    btn.onclick = () => renderTaskList(btn.dataset.viewtasks);
  });
  content.querySelectorAll("[data-delete-brief]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 업무 요청과 그 아래 할 일·배정·결과물(산출물 평가 이력 포함)을 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/briefs/" + btn.dataset.deleteBrief);
        showMsg("global-msg", "업무 요청을 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

async function renderDecomposeForm(briefId, stubRoles) {
  const detail = document.getElementById("brief-detail");
  const suggestions = (stubRoles || []).length
    ? '<p class="muted">제안된 역할(클릭하면 아래 입력창에 채워집니다 — 브리프를 줄바꿈으로 나눈 것뿐이라 다듬어서 쓰세요): ' +
      stubRoles.map(r => '<button type="button" class="small" data-fill-role="' + esc(r) + '">' + esc(r) + '</button>').join(" ") +
      "</p>"
    : "";
  const skillDatalist = await skillDatalistHtml();
  detail.innerHTML =
    '<h3>업무 요청 #' + esc(briefId) + ' — 할 일 나누기</h3>' +
    suggestions +
    '<form class="inline" id="decompose-form">' +
    '<input id="d-role" placeholder="필요한 역할(예: 백엔드 개발자)" required>' +
    skillPickerHtml("d-skills-picker", "d-skills") +
    '<input id="d-headcount" type="number" min="1" value="1" style="width:70px" title="인원 수">' +
    '<button class="primary" type="submit">할 일 추가</button></form>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>필요 스킬은 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(여러 개 등록 가능, 칩의 ×로 제거).</li>' +
      '<li>자동완성 중 "실시간웹검색"(Tavily)은 대화창·Crew 실행 양쪽에서 동작하고, "일반웹검색"(Parallel Search)은 Crew 실행에서만 동작합니다.</li>' +
      '<li>목록에 없는 이름도 자유롭게 추가할 수 있습니다.</li>' +
      '</ul>'
    ) +
    skillDatalist +
    // 사용자 요청(2026-11-04, "업무 분해 단위 가이드" 설계) — 복잡한
    // 업무를 통째로 할 일 하나에 몰아넣지 않도록, LLM이 독립 검증
    // 가능한 단위 + 선행관계를 제안하면 admin이 검토·수정 후 한
    // 배치로 제출한다. 위 단건 폼은 그대로 둔다(간단한 경우엔 그게
    // 더 빠름) — 이건 복잡한 업무에 한해 쓰는 보조 흐름이다.
    '<div id="decompose-batch">' +
    '<button type="button" class="small" id="decompose-suggest-btn">AI로 분해 제안 받기(복잡한 업무용)</button>' +
    '<div id="decompose-batch-rows"></div>' +
    '</div>';
  const skillPicker = initSkillPicker("d-skills-picker", "d-skills", []);
  detail.querySelectorAll("[data-fill-role]").forEach(btn => {
    btn.onclick = () => { document.getElementById("d-role").value = btn.dataset.fillRole; };
  });
  document.getElementById("decompose-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    skillPicker.flush();
    const skills = document.getElementById("d-skills").value.split(",").map(s => s.trim()).filter(Boolean);
    try {
      await api("POST", "/api/briefs/" + briefId + "/tasks", {
        tasks: [{
          role_name: document.getElementById("d-role").value,
          required_skills: skills,
          headcount: Number(document.getElementById("d-headcount").value) || 1,
        }],
      });
      showMsg("global-msg", "할 일을 추가했습니다.", "ok");
      renderTaskList(briefId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 배치(LLM 제안) 행 상태 — "즉시 모드" UI: 배열을 고치고 매번
  // innerHTML을 통째로 다시 그린다(행 추가/삭제 시 선행업무 드롭다운의
  // 보기 목록 자체가 바뀌어야 하므로 부분 갱신보다 단순하고 안전함).
  let batchItems = [];

  function syncBatchItemsFromInputs() {
    batchItems = batchItems.map((item, i) => ({
      role_name: document.getElementById("db-role-" + i)?.value ?? item.role_name,
      required_skills: (document.getElementById("db-skills-" + i)?.value ?? "")
        .split(",").map(s => s.trim()).filter(Boolean),
      depends_on_index: (() => {
        const v = document.getElementById("db-dep-" + i)?.value ?? "";
        return v === "" ? null : Number(v);
      })(),
    }));
  }

  function renderBatchRows() {
    const container = document.getElementById("decompose-batch-rows");
    if (!batchItems.length) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML =
      table(["역할", "필요 스킬(쉼표로 구분)", "선행 업무", "동작"], batchItems.map((item, i) => [
        '<input id="db-role-' + i + '" value="' + escAttr(item.role_name) + '" style="width:100%">',
        '<input id="db-skills-' + i + '" value="' + escAttr(item.required_skills.join(", ")) + '" style="width:100%">',
        '<select id="db-dep-' + i + '">' +
          '<option value="">(없음)</option>' +
          batchItems.map((other, j) => j === i ? "" :
            '<option value="' + j + '"' + (item.depends_on_index === j ? " selected" : "") + '>' +
            esc(other.role_name || ("#" + (j + 1))) + '</option>').join("") +
          '</select>',
        '<button type="button" class="small danger" data-batch-remove="' + i + '">삭제</button>',
      ])) +
      '<div class="inline">' +
      '<button type="button" class="small" id="decompose-batch-add-row">행 추가</button>' +
      '<button type="button" class="primary small" id="decompose-batch-submit">이 배치로 할 일 추가</button>' +
      '</div>';
    container.querySelectorAll("[data-batch-remove]").forEach(btn => {
      btn.onclick = () => {
        syncBatchItemsFromInputs();
        const removedIndex = Number(btn.dataset.batchRemove);
        batchItems.splice(removedIndex, 1);
        // 삭제된 행을 선행업무로 가리키던 항목은 다시 "(없음)"으로,
        // 뒤쪽 인덱스를 가리키던 항목은 한 칸씩 당겨준다.
        batchItems = batchItems.map(item => {
          if (item.depends_on_index === null) return item;
          if (item.depends_on_index === removedIndex) return { ...item, depends_on_index: null };
          if (item.depends_on_index > removedIndex) {
            return { ...item, depends_on_index: item.depends_on_index - 1 };
          }
          return item;
        });
        renderBatchRows();
      };
    });
    document.getElementById("decompose-batch-add-row").onclick = () => {
      syncBatchItemsFromInputs();
      batchItems.push({ role_name: "", required_skills: [], depends_on_index: null });
      renderBatchRows();
    };
    document.getElementById("decompose-batch-submit").onclick = async () => {
      syncBatchItemsFromInputs();
      if (batchItems.some(item => !item.role_name.trim())) {
        showMsg("global-msg", "역할명이 비어있는 행이 있습니다.", "error");
        return;
      }
      try {
        await api("POST", "/api/briefs/" + briefId + "/tasks", { tasks: batchItems });
        showMsg("global-msg", "배치로 할 일 " + batchItems.length + "건을 추가했습니다.", "ok");
        renderTaskList(briefId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }

  document.getElementById("decompose-suggest-btn").addEventListener("click", async () => {
    try {
      const result = await api("POST", "/api/briefs/" + briefId + "/decompose-suggest", {});
      batchItems = result.tasks || [];
      if (!batchItems.length) {
        showMsg("global-msg", "AI 제안을 받지 못했습니다 — 수동으로 입력해주세요.", "error");
      }
      renderBatchRows();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

async function renderTaskList(briefId) {
  const detail = document.getElementById("brief-detail");
  const isAdmin = ME.role === "admin";
  const tasks = await api("GET", "/api/briefs/" + briefId + "/tasks");
  detail.innerHTML = "<h3>업무 요청 #" + esc(briefId) + " — 할 일 목록</h3>" +
    // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
    // 분석하고 부족한 기능을 개선해줘") — 이전엔 이 화면에서 할 일의
    // 결과물 진행 상태를 전혀 알 수 없어(id/역할/스킬/인원뿐), 확인하려면
    // "내 업무"에서 찾거나 배정·워크스페이스를 따로 추적해야 했다.
    table(["할 일 ID", "역할", "필요 스킬", "인원", "상태", "최종 결과물", "동작"], tasks.map(t => [
      t.id, esc(t.role_name), esc(t.required_skills.join(", ")), t.headcount,
      // 사용자 요청(2026-11-12) — 요청을 막 제출한 바로 이 화면에서도
      // "실행 중"을 바로 볼 수 있어야 "내 업무" 탭까지 따로 찾아가지
      // 않아도 된다(renderMyWork()의 같은 로직, 백엔드 집계도 공유).
      t.active_job === "running" ? '<span class="gate-badge gate-warn">⏳ 실행 중</span>'
        : t.active_job === "paused_for_approval" ? '<span class="gate-badge gate-warn">🕐 승인 대기 중</span>'
        : t.latest_status ? esc(label(t.latest_status)) : '<span class="muted">아직 없음</span>',
      t.has_final_result ? '<span class="gate-badge gate-ok">✓ 승인됨</span>' : '<span class="muted">-</span>',
      '<div class="actions"><button class="small" data-task-detail="' + t.id + '">상세</button>' +
        (isAdmin ? '<button class="small danger" data-delete-task="' + t.id + '">삭제</button>' : "") +
        "</div>",
    ])) +
    // 사용자 요청(2026-10-06, "팀 구성과 협업 프로세스를 하나의 화면으로
    // 합치는 방안" → 옵션 C 채택) — "상세"를 누르면 이 할 일의 배정
    // 현황(+admin/manager는 매칭·배정 요청, 확정됐으면 협업 워크스페이스
    // 요약까지)을 아래에 바로 펼친다. "인력 매칭/배정"·"결재"·"내 업무"는
    // 여러 업무를 가로지르는 일괄 처리용으로 그대로 남겨둔다 — 완전
    // 통합은 매니저의 일괄 처리 효율을 떨어뜨릴 위험이 있어 기각했다.
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"상세"에서 배정 현황을 바로 확인할 수 있습니다.</li>' +
      '<li>"리소스 매칭/배정"·"결재"는 여러 할 일을 한꺼번에 처리할 때, "AI 에이전트 생성"은 새 Agent가 필요할 때 씁니다.</li>' +
      '</ul>'
    ) +
    '<div id="task-detail"></div>';
  detail.querySelectorAll("[data-delete-task]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 할 일과 그 배정·결과물(산출물 평가 이력 포함)을 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/briefs/" + briefId + "/tasks/" + btn.dataset.deleteTask);
        showMsg("global-msg", "할 일을 삭제했습니다.", "ok");
        renderTaskList(briefId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  detail.querySelectorAll("[data-task-detail]").forEach(btn => {
    btn.onclick = () => {
      const task = tasks.find(t => t.id === Number(btn.dataset.taskDetail));
      renderTaskDetailPanel(task);
    };
  });
}

/* 할 일 하나의 생애주기(배정 현황 → 매칭/배정 요청 → 확정 후 협업
   워크스페이스)를 한 화면에서 보여준다(옵션 C).
   매칭 폼·워크스페이스는 기존 "인력 매칭/배정"·"내 업무" 탭의 렌더
   함수를 containerId 파라미터로 그대로 재사용한다(중복 구현 금지) —
   admin/manager만 노출한다: 매칭 결과가 다른 후보의 비용을 보여주고,
   deliverables 조회는 admin/manager가 아니면 참가자 본인만 허용돼
   (403) 있어, 그 둘은 감독 권한이 있는 role에만 안전하게 임베드할 수
   있다. member/viewer는 배정 현황만 읽기 전용으로 본다(자기 업무
   처리는 기존처럼 "내 업무" 탭에서). */
async function renderTaskDetailPanel(task, forceWorkspace = false) {
  const container = document.getElementById("task-detail");
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  container.innerHTML = '<p class="muted">불러오는 중...</p>';
  const assignments = await api("GET", "/api/tasks/" + task.id + "/assignments");
  // 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) — "다중
  // confirmed 허용"으로 바뀌면서 이 패널도 더 이상 .find()로 하나만
  // 골라 보여줄 수 없다. confirmed/requested 전부를 보여주고, 각 건에
  // 배정 ID를 노출해 팀 리더 변경·협업 워크스페이스 진입에 쓴다.
  const confirmedList = assignments.filter(a => a.status === "confirmed");
  const pendingList = assignments.filter(a => a.status === "requested");
  const historyList = assignments.filter(a => a.status !== "confirmed" && a.status !== "requested");

  const resourceLabel = (a) => esc(label(a.resource_type)) + " " +
    (a.resource_name ? esc(a.resource_name) : "#" + a.resource_id) +
    (a.resource_type === "team" ? ' <span class="muted">(팀 리더: #' + a.team_leader_id + ')</span>' : "");

  // 사용자 보고(2026-10-19, "업무 요청 삭제가 '확정된 배정이 있어
  // 삭제할 수 없습니다 — 먼저 정리하세요'로 막히는데 정리할 방법이
  // 없다") — 지금까지 이 에러 메시지만 "먼저 정리하세요"라 안내하고
  // 실제로 배정을 되돌릴 버튼이 화면 어디에도 없었다. 배정 현황을
  // 보여주는 바로 이 자리(admin/manager 전용)에 "배정 취소" 버튼을
  // 추가한다 — task/brief 삭제를 막는 confirmed 배정도 여기서 바로
  // cancelled로 되돌릴 수 있다.
  const cancelBtnHtml = (a) => isSupervisor
    ? ' <button class="small danger" data-cancel-assignment="' + a.id + '">배정 취소</button>'
    : "";
  const confirmedHtml = confirmedList.length
    ? confirmedList.map(a => '<p><span class="gate-badge gate-ok">확정됨</span> ' + resourceLabel(a) + "에게 배정됨" + cancelBtnHtml(a) + "</p>").join("")
    : "";
  const pendingHtml = pendingList.map(a =>
    '<p><span class="gate-badge gate-warn">결재 대기</span> ' + resourceLabel(a) +
    ' — <button class="small" data-goto-approvals="1">결재 탭에서 처리</button>' +
    cancelBtnHtml(a) +
    (isSupervisor && a.resource_type === "team"
      ? ' <span class="inline" style="display:inline-flex;gap:0.25rem">' +
        '<input type="number" min="1" step="1" placeholder="새 팀 리더 user ID" data-team-leader-input="' + a.id + '" style="width:140px">' +
        '<button class="small" data-change-team-leader="' + a.id + '">팀 리더 변경</button></span>'
      : "") +
    "</p>"
  ).join("");
  const statusHtml = (confirmedHtml || pendingHtml)
    ? confirmedHtml + pendingHtml
    : '<p class="muted">아직 배정 요청이 없습니다.</p>';
  const historyHtml = historyList.length
    ? '<details class="muted"><summary>이전 배정 이력(' + historyList.length + '건)</summary>' +
      table(["타입", "대상", "상태"], historyList.map(a => [esc(label(a.resource_type)), esc(a.resource_name || ("#" + a.resource_id)), esc(label(a.status))])) +
      "</details>"
    : "";

  // 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) — 사람/Agent
  // 후보 매칭("인력 매칭/배정"과 공유)에 더해, 등록된 팀 전체를 한 할
  // 일에 배정 요청하는 선택지를 추가한다("팀 관리" 탭에서 미리 구성).
  let teamOptionsHtml = "";
  let pipelineOptionsHtml = "";
  if (isSupervisor) {
    const teams = await api("GET", "/api/teams").catch(() => []);
    const activeTeams = teams.filter(t => t.is_active);
    teamOptionsHtml = activeTeams.length
      ? '<form class="inline" id="task-detail-team-assign-form">' +
        '<select id="task-detail-team-select" required>' +
        activeTeams.map(t => '<option value="' + t.id + '">' + esc(t.name) + '</option>').join("") +
        '</select><button class="small" type="submit">이 팀에 배정 요청</button></form>'
      : '<p class="muted">등록된 활성 팀이 없습니다 — "팀 관리" 탭에서 먼저 만드세요.</p>';
    // 사용자 요청(2026-10-29, "파이프라인을 assignments.resource_type에
    // 추가") — 팀 배정과 같은 패턴으로, 파이프라인도 사람/Agent/Team처럼
    // 결재를 거쳐 배정할 수 있다. 승인되면 그 즉시 실행이 시작된다
    // (`app.py`의 `decide_approval()` 참고).
    const pipelineTemplates = await api("GET", "/api/pipeline-templates").catch(() => []);
    const activePipelines = pipelineTemplates.filter(p => p.is_active);
    pipelineOptionsHtml = activePipelines.length
      ? '<form class="inline" id="task-detail-pipeline-assign-form">' +
        '<select id="task-detail-pipeline-select" required>' +
        activePipelines.map(p => '<option value="' + p.id + '">' + esc(p.name) + '</option>').join("") +
        '</select><button class="small" type="submit">이 파이프라인에 배정 요청</button></form>'
      : '<p class="muted">등록된 활성 파이프라인이 없습니다 — "파이프라인" 탭에서 먼저 만드세요.</p>';
  }

  // 실사용 리뷰로 발견한 갭(2026-10-07) — "다중 confirmed 허용" 이후
  // 이미 배정이 확정된 할 일에도 "후보 매칭하기"가 변함없이 1순위로
  // 노출돼, 이미 담당자가 있는 일도 아직 비어있는 것처럼 보였다(배정을
  // *추가*하는 것과 *처음* 배정하는 건 의도가 다르다). confirmed가
  // 하나라도 있으면 "<details>" 뒤로 접어 넣고 문구를 "추가 배정"으로
  // 바꿔 이게 보충 동작임을 분명히 한다.
  const matchAndTeamHtml = isSupervisor
    ? '<button class="small" id="task-detail-match-btn">후보 매칭하기</button><div id="task-detail-match"></div>' +
      '<details><summary>팀에 배정 요청</summary>' + teamOptionsHtml + '</details>' +
      '<details><summary>파이프라인에 배정 요청</summary>' + pipelineOptionsHtml + '</details>'
    : "";
  container.innerHTML =
    '<section class="card"><h4>할 일 #' + task.id + " — " + esc(task.role_name) + " 상세</h4>" +
    statusHtml + historyHtml +
    (isSupervisor && confirmedList.length
      ? '<details><summary>추가 배정(이미 확정된 배정이 있습니다 — 사람/Agent/팀을 더 투입하려면)</summary>' + matchAndTeamHtml + '</details>'
      : matchAndTeamHtml) +
    (isSupervisor && confirmedList.length ? '<div id="task-detail-workspace"></div>' : "") +
    "</section>";

  container.querySelectorAll("[data-goto-approvals]").forEach(btn => {
    btn.onclick = () => selectTab("approvals");
  });
  container.querySelectorAll("[data-cancel-assignment]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 배정을 취소합니다 — 되돌릴 수 없고, 다시 배정하려면 새로 요청해야 합니다. 계속할까요?"))) return;
      try {
        await api("POST", "/api/assignments/" + btn.dataset.cancelAssignment + "/cancel", {});
        showMsg("global-msg", "배정을 취소했습니다.", "ok");
        renderTaskDetailPanel(task);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  container.querySelectorAll("[data-change-team-leader]").forEach(btn => {
    btn.onclick = async () => {
      const assignmentId = btn.dataset.changeTeamLeader;
      const input = container.querySelector('[data-team-leader-input="' + assignmentId + '"]');
      const newLeaderId = Number(input.value);
      if (!Number.isInteger(newLeaderId) || newLeaderId < 1) {
        showMsg("global-msg", "새 팀 리더의 user ID를 입력하세요.", "error"); return;
      }
      try {
        await api("PATCH", "/api/assignments/" + assignmentId + "/team-leader", { team_leader_id: newLeaderId });
        showMsg("global-msg", "팀 리더를 변경했습니다.", "ok");
        renderTaskDetailPanel(task);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  if (isSupervisor) {
    document.getElementById("task-detail-match-btn").onclick = async () => {
      try {
        const result = await api("POST", "/api/tasks/" + task.id + "/match");
        renderMatchResult(task.id, result, "task-detail-match",
          (assignResult) => renderTaskDetailPanel(task, !!(assignResult && assignResult.status === "confirmed")));
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
    const teamAssignForm = document.getElementById("task-detail-team-assign-form");
    if (teamAssignForm) {
      teamAssignForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const teamId = Number(document.getElementById("task-detail-team-select").value);
        try {
          await api("POST", "/api/tasks/" + task.id + "/assignments", {
            resource_type: "team", resource_id: teamId,
          });
          showMsg("global-msg", "팀 배정 요청을 보냈습니다 — \\"결재\\" 탭에서 처리하세요.", "ok");
          renderTaskDetailPanel(task);
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    }
    const pipelineAssignForm = document.getElementById("task-detail-pipeline-assign-form");
    if (pipelineAssignForm) {
      pipelineAssignForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const pipelineId = Number(document.getElementById("task-detail-pipeline-select").value);
        try {
          await api("POST", "/api/tasks/" + task.id + "/assignments", {
            resource_type: "pipeline", resource_id: pipelineId,
          });
          showMsg("global-msg", "파이프라인 배정 요청을 보냈습니다 — \\"결재\\" 탭에서 승인하면 바로 실행이 시작됩니다.", "ok");
          renderTaskDetailPanel(task);
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    }
  }
  // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
  // 결과물 확인이 어려워(숨겨져 있는 느낌)") — admin/manager는 확정된
  // 배정이 하나라도 있으면 항상 워크스페이스를 바로 보여줬지만, member가
  // 셀프배정으로 그 자리에서 즉시 confirmed가 될 때는(`forceWorkspace`)
  // 보여주지 않았다 — "결재" 탭조차 안 거치고 확정됐는데 "바로
  // 협업 워크스페이스에서 작업을 시작할 수 있습니다"라는 안내만 뜨고
  // 실제로는 "내 업무" 탭을 따로 찾아가야 했다. member가 참가자가 아닌
  // 남의 할 일까지 무조건 열면(권한 없음 에러만 뜸) 오히려 혼란이라,
  // "방금 내가 확정시킨 배정"일 때만 좁혀서 연다.
  if ((isSupervisor || forceWorkspace) && confirmedList.length) {
    renderCollabWorkspace(task.id, "task-detail-workspace");
  }
}

/* ---------- 인력 매칭·배정 ---------- */

// 사용자 보고(2026-10-13, "할일 ID의 경우 의미없는 숫자로 할일을 찾아야
// 해(할일 아이디를 사전에 모르면 사용 불가)") — 원래는 숫자 입력칸에
// Task ID를 직접 타이핑해야 했다(어디서도 그 ID를 보여주지 않음).
// `GET /api/briefs/{id}/tasks`의 docstring조차 "Web UI가 매칭/배분
// 화면에서 브리프별 Task 목록을 골라야 한다"고 적어뒀는데 이 화면만
// 그 엔드포인트를 안 쓰고 있었다 — 브리프별로 묶은 select로 바꾼다.
async function _fetchAllTasksGroupedByBrief() {
  const briefs = await api("GET", "/api/briefs");
  const taskLists = await Promise.all(
    briefs.map(b => api("GET", "/api/briefs/" + b.id + "/tasks").catch(() => []))
  );
  return briefs.map((b, i) => ({ brief: b, tasks: taskLists[i] }));
}

function _taskPickerOptionsHtml(groups) {
  return groups.map(g => {
    if (!g.tasks.length) return "";
    const briefLabel = "#" + g.brief.id + " " + (g.brief.raw_text.length > 24 ? g.brief.raw_text.slice(0, 24) + "…" : g.brief.raw_text);
    const options = g.tasks.map(t =>
      '<option value="' + t.id + '">#' + t.id + " " + esc(t.role_name) + " (" + esc(label(t.status)) + ")</option>"
    ).join("");
    return '<optgroup label="' + escAttr(briefLabel) + '">' + options + '</optgroup>';
  }).join("");
}

async function renderMatch(content) {
  // 2026-10-14 — _renderBoardScope()와 같은 이유로 조기 블랭크를
  // 제거한다(아래에서 데이터를 다 가져온 뒤 한 번에 교체).
  const groups = await _fetchAllTasksGroupedByBrief();
  const hasTasks = groups.some(g => g.tasks.length);
  content.innerHTML =
    '<section class="card"><h2>할 일에 맞는 리소스 찾기</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>아래 목록은 "업무 요청" 탭에서 "할 일로 나누기"로 만든 할 일 전체입니다(브리프별로 묶음).</li>' +
      '<li>역할명이 서로 포함 관계(부분일치 — 예: "프론트엔드 개발" ↔ "프론트엔드 개발자")인 후보 중 필요 스킬까지 다 가진 후보가 있으면 그것부터 보여주고, 없으면 가용성40%·적합도35%·비용25% 가중치로 점수를 매겨 상위 3명을 제시합니다(사람 후보가 아직 성과점수를 안 받았으면 적합도를 빼고 가용성60%·비용40%로 재계산).</li>' +
      '<li>역할명이 부분일치로도 전혀 안 걸리는 후보가 없으면(예: "콘텐츠 카피라이팅" ↔ "카피라이팅 에이전트"처럼 문자열이 서로 포함되지 않는 경우), "AI로 추천 받기"로 LLM이 전체 Pool에서 의미상 맞는 후보를 찾아줍니다(비용 발생 가능, 명시적으로 눌러야만 호출됨).</li>' +
      '<li>"배정 요청"을 누르면 즉시 확정되는 게 아니라 "결재" 탭에서 승인 절차를 거쳐야 확정(confirmed)됩니다(단, 본인 셀프배정은 결재 없이 즉시 확정됩니다).</li>' +
      '</ul>'
    ) +
    (hasTasks
      ? '<form class="inline" id="match-form">' +
        '<select id="match-task-id" required style="min-width:280px"><option value="">— 할 일 선택 —</option>' +
        _taskPickerOptionsHtml(groups) + '</select>' +
        '<button class="primary" type="submit">찾기</button></form>' +
        '<div id="match-result"></div>'
      : '<p class="msg error">아직 할 일이 없습니다 — "업무 요청" 탭에서 먼저 "할 일로 나누기"를 해주세요.</p>') +
    "</section>";
  const form = document.getElementById("match-form");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const taskId = Number(document.getElementById("match-task-id").value);
    if (!Number.isInteger(taskId) || taskId < 1) {
      showMsg("global-msg", "할 일을 선택하세요.", "error"); return;
    }
    try {
      const result = await api("POST", "/api/tasks/" + taskId + "/match");
      renderMatchResult(taskId, result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// containerId/onAssigned: 사용자 요청(2026-10-06, "팀 구성과 협업
// 프로세스를 하나의 화면으로 합치는 방안" → 옵션 C) — "업무 요청" 탭의
// 할 일 상세 패널에서도 같은 매칭 결과 표를 재사용하기 위해 일반화했다.
// 기본값을 기존 id로 둬서 "인력 매칭/배정" 탭의 기존 호출부는 그대로
// 둔다. `onAssigned`는 배정 요청 성공 직후 호출돼, 임베드한 쪽(할 일
// 상세 패널)이 자기 상태를 새로고침할 수 있게 한다(이 함수 자체는 그
// 화면 구조를 몰라도 된다).
// 사용자 요청(2026-11-02, "LLM judge로 추천 인력을 찾도록 개선,
// 추천인력이 없는 경우 기존의 사람/에이전트를 지정해 찾을 수 있도록
// 개선") — 배정 요청 버튼 클릭 처리를 공용 헬퍼로 뺐다. 원래
// `renderMatchResult()`의 매칭 결과 테이블에서만 쓰던 로직인데, 아래
// 수동 검색 결과 테이블에도 똑같이 필요해져 중복을 피하려고 분리했다
// (동작은 그대로 — 계정 미연결 경고 confirm, 배정 요청 POST, 성공 시
// onAssigned 콜백).
function _wireMatchAssignButtons(container, taskId, onAssigned) {
  container.querySelectorAll("[data-assign-id]").forEach(btn => {
    btn.onclick = async () => {
      if (btn.dataset.unlinked === "1" && !(await confirmModal(
        "이 사람은 로그인 계정이 연결돼 있지 않습니다 — 배정이 승인돼도 본인이 \\"내 업무\\"에서 확인할 방법이 없습니다. \\"사람 인력 관리\\"에서 계정을 먼저 연결하는 걸 권장합니다. 그래도 배정 요청하시겠습니까?"
      ))) return;
      try {
        const result = await api("POST", "/api/tasks/" + taskId + "/assignments", {
          resource_type: btn.dataset.assignType, resource_id: Number(btn.dataset.assignId),
        });
        // 사용자 요청(2026-11-05, 로드맵 7번 "셀프배정 결재 단순화") —
        // 본인 셀프배정은 결재 없이 즉시 confirmed로 돌아온다.
        showMsg("global-msg",
          result.status === "confirmed"
            ? "셀프배정이 결재 없이 바로 확정됐습니다 — 바로 협업 워크스페이스에서 작업을 시작할 수 있습니다."
            : "배정 요청을 보냈습니다 — \\"결재\\" 탭에서 처리하세요.",
          "ok");
        // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서
        // 진행과정과 결과물 확인이 어려워") — 결과(특히 셀프배정
        // 즉시확정 여부)를 콜백에 넘겨, 호출부가 "방금 확정됐으면
        // 바로 워크스페이스를 보여준다" 같은 판단을 할 수 있게 한다.
        if (onAssigned) onAssigned(result);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

function renderMatchResult(taskId, result, containerId = "match-result", onAssigned) {
  const box = document.getElementById(containerId);
  // 사용자 보고(2026-11-02, 스크린샷 — "Cannot set properties of null
  // (setting 'innerHTML')") — 실측 원인: "AI로 추천 받기"에 진행 중
  // 표시가 없어 사용자가 응답을 기다리다 여러 번 눌렀고(서버 로그에
  // use_llm_judge=true 요청이 연달아 5건 찍힘), 그중 하나가 응답을
  // 기다리는 동안 다른 탭으로 이동해 이 컨테이너가 DOM에서 사라진
  // 뒤에 뒤늦게 응답이 와서 `box.innerHTML = ...`가 null에 쓰려다
  // 터졌다. 화면을 이미 벗어났으면 반영할 대상이 없으니 조용히
  // 무시한다(에러 아님).
  if (!box) return;
  if (result.generate_new) {
    const triedLlm = result.match_mode === "llm_judge_failed";
    box.innerHTML =
      '<p class="msg error">' + (triedLlm ? "AI 추천도 적합한 후보를 찾지 못했습니다." : "매칭되는 후보가 없습니다.") + '</p>' +
      (triedLlm
        ? ""
        : '<button class="small" id="' + containerId + '-llm-judge-btn">AI로 추천 받기(LLM 호출, 비용 발생 가능)</button>') +
      '<p class="muted">또는 "AI 에이전트 생성" 탭에서 새 Agent를 만들거나, "사람 인력 관리" 탭에서 이 역할의 사람을 등록해보세요.</p>';
    const llmBtn = document.getElementById(containerId + "-llm-judge-btn");
    if (llmBtn) {
      llmBtn.onclick = async () => {
        // 위 버그의 근본 원인 수정 — 요청 중엔 버튼을 비활성화하고
        // 진행 중임을 표시해, 응답을 못 받았다고 오해해 여러 번
        // 누르는 일을 막는다.
        llmBtn.disabled = true;
        llmBtn.textContent = "AI 추천 받는 중…";
        try {
          const r2 = await api("POST", "/api/tasks/" + taskId + "/match?use_llm_judge=true");
          renderMatchResult(taskId, r2, containerId, onAssigned);
        } catch (e) {
          showMsg("global-msg", e.message, "error");
          llmBtn.disabled = false;
          llmBtn.textContent = "AI로 추천 받기(LLM 호출, 비용 발생 가능)";
        }
      };
    }
    return;
  }
  // 사용자 보고(2026-10-06, "매칭/배정 화면에도 계정 미연결 경고
  // 추가해줘") — 사람 후보가 로그인 계정과 연결 안 돼 있으면 배정·승인이
  // 돼도 당사자가 "내 업무"에서 그 일을 볼 방법이 없다. Agent는
  // 로그인 계정 개념이 없어 "해당없음"으로 둔다.
  const accountCell = (c) => {
    if (c.type !== "human") return '<span class="muted">해당없음</span>';
    return c.user_id != null
      ? "연결됨"
      : '<span class="gate-badge gate-warn">⚠ 미연결</span>';
  };
  // 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
  // 에이전트명이 표시되지 않는 경우도 있어") — `role`은 직무 타이틀이라
  // 같은 역할의 사람이 여럿이면 ID 숫자로만 구분해야 했다. "인력 검색"
  // 탭은 이미 사람 이름을 보여주고 있었는데(백엔드가 따로 조회해 끼워
  // 넣음) 이 매칭 결과 테이블만 빠져 있었다 — `ResourceCandidate.name`을
  // 추가해(`models.py`/`pool.py`) 똑같이 보여준다. Agent는 역할명이
  // 곧 식별자라 "-"로 둔다.
  // 사용자 요청(2026-10-27, "다룬 테이블도 ID 표기되는 것이 남아
  // 있는지 확인") — ResourceCandidate.code(human=employee_id,
  // agent=code)로 교체.
  box.innerHTML = table(
    ["순위", "타입", "코드", "이름", "역할", "가용", "비용", "점수", "근거", "계정", "동작"],
    result.top.map((sc, i) => [
      i + 1, esc(label(sc.candidate.type)), esc(sc.candidate.code || ("#" + sc.candidate.id)), esc(sc.candidate.name || sc.candidate.role), esc(sc.candidate.role),
      sc.candidate.is_available ? "가능" : "불가",
      sc.candidate.cost,
      sc.score.toFixed(3) + (sc.exact_match ? " (역할 일치)" : (sc.rationale ? " (AI 추천)" : "")),
      // 사용자 요청(2026-11-02, "LLM judge로 추천 인력을 찾도록 개선") —
      // LLM이 근거(rationale)를 줬으면 그걸 사람이 읽는 문장으로
      // 보여주고, 기존 결정론적 매칭(rationale 없음)은 그대로 점수
      // breakdown JSON을 보여준다.
      esc(sc.rationale || JSON.stringify(sc.breakdown)),
      accountCell(sc.candidate),
      '<div class="actions">' +
      '<button class="small" data-assign-type="' + esc(sc.candidate.type) + '" data-assign-id="' + sc.candidate.id + '" data-unlinked="' + (sc.candidate.type === "human" && sc.candidate.user_id == null ? "1" : "0") + '">배정 요청</button>' +
      // 사용자 결정(2026-10-12, "위임은 Gate A-G 그대로 타고, audit_log
      // 에도 남겨줘") — Agent 후보에만 "위임"(결재 없이 즉시 확정)을
      // 추가로 보여준다. 사람/팀은 Linear식 "위임" 개념의 대상이 아니다.
      (sc.candidate.type === "agent"
        ? '<button class="small" data-delegate-agent-id="' + sc.candidate.id + '" title="결재 없이 즉시 연결 — 바로 실행 가능">위임</button>'
        : "") +
      '</div>',
    ])
  );
  _wireMatchAssignButtons(box, taskId, onAssigned);
  box.querySelectorAll("[data-delegate-agent-id]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal(
        "결재 없이 이 Agent를 바로 이 업무에 연결합니다(즉시 실행 가능) — 계속하시겠습니까?"
      ))) return;
      try {
        await api("POST", "/api/tasks/" + taskId + "/delegate", {
          agent_id: Number(btn.dataset.delegateAgentId),
        });
        showMsg("global-msg", "위임했습니다 — 결재 없이 바로 실행할 수 있습니다.", "ok");
        if (onAssigned) onAssigned();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 결재 ---------- */

async function renderApprovals(content) {
  const isDecider = ["admin", "manager"].includes(ME.role);
  let html = "";
  let inbox = [];
  if (isDecider) {
    html += helpBox(
      '<ul class="muted help-list">' +
      '<li>배정 요청은 요청자의 상사 체인(조직도 "상사" 지정)을 따라 자동으로 단계가 만들어집니다 — 1단계가 처리돼야 2단계가 열립니다(다단계 결재).</li>' +
      '<li>상사가 지정 안 돼 있으면(예: 관리자 본인 요청) 특정 승인자 없이 1단계만 만들어져 admin/manager 누구나 처리할 수 있습니다.</li>' +
      '<li>본인이 요청한 건은 본인이 승인할 수 없습니다(자기 승인 방지) — "내 승인 대기함"에는 애초에 안 뜹니다.</li>' +
      '</ul>'
    );
    inbox = await api("GET", qs("/api/approvals", { scope: "inbox" }));
    html += '<section class="card"><h2>내 승인 대기함</h2>' + approvalTable(inbox, true, "requester") +
      '<div id="approval-content-detail" style="margin-top:0.75rem"></div></section>';
  }
  const mine = await api("GET", qs("/api/approvals", { scope: "mine" }));
  html += '<section class="card"><h2>내가 요청한 건</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>승인</b>하면 배정 상태가 confirmed(확정)로 바뀌어 조직도·비용·성과 리뷰에 실제로 반영됩니다.</li>' +
      '<li><b>반려</b>하면 그 배정 요청은 거기서 끝나고(재사용 불가), 다시 배정하려면 "리소스 매칭/배정"에서 새로 요청해야 합니다.</li>' +
      '</ul>'
    ) +
    approvalTable(mine, false, "approver") +
    (isDecider ? "" : '<div id="approval-content-detail" style="margin-top:0.75rem"></div>') +
    "</section>";
  content.innerHTML = html;
  content.querySelectorAll("[data-decide]").forEach(btn => {
    btn.onclick = async () => {
      const reasonInput = content.querySelector('[data-decide-reason-for="' + btn.dataset.decide + '"]');
      const reason = reasonInput ? reasonInput.value.trim() : "";
      try {
        await api("POST", "/api/approvals/" + btn.dataset.decide + "/decide", {
          approve: btn.dataset.approve === "1",
          reason: reason || null,
        });
        showMsg("global-msg", "처리했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  // "내용 보기" — 결재 대기함과 "내가 요청한 건" 둘 다 같은 id
  // 네임스페이스(approvals.id)를 쓰므로 합친 조회맵 하나로 충분하다.
  const contentById = {};
  [...inbox, ...mine].forEach(a => { contentById[a.id] = a; });
  const detailPanel = content.querySelector("#approval-content-detail");
  content.querySelectorAll("[data-approval-content-toggle]").forEach(btn => {
    btn.onclick = () => {
      const a = contentById[Number(btn.dataset.approvalContentToggle)];
      if (!a || !detailPanel) return;
      detailPanel.innerHTML =
        '<div class="card" style="background:var(--bg-alt,#f7f7f7)">' +
        '<div class="actions" style="justify-content:space-between"><b>#' + a.id + " " +
        esc(a.resource_name || label(a.subject_type)) + '</b>' +
        '<button type="button" class="small" data-approval-content-close>닫기</button></div>' +
        renderContent(a.content, a.content_type || "text") + "</div>";
      detailPanel.querySelector("[data-approval-content-close]").onclick = () => { detailPanel.innerHTML = ""; };
      detailPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
  });
}

// 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나 에이전트명이
// 표시되지 않는 경우도 있어") — 원래는 "대상 종류"/"대상 ID"가
// `assignments.id`/`deliverables.id`를 그대로 보여줘, 승인권자가 "무엇을
// 승인하는지" 다른 탭을 또 열어야 알 수 있었다. `GET /api/approvals`가
// 이제 `task_id`/`task_role_name`/`resource_type`/`resource_name`을
// 같이 내려주므로(`approvals.describe_subject()`) 그걸 보여준다 —
// 대상이 지워졌으면(task_id가 null) 원래 종류+ID로 안전하게 되돌아간다.
// 사용자 보고(2026-10-28, "결재에서 승인을 요청하고 있으나, 해당
// 컨텐츠를 검토할 수 있는 UI가 없어") — 지금까지 이 표는 "무엇을
// 승인하는지"(업무/대상/단계)만 보여주고, 실제로 검토해야 할 내용
// (deliverable/pipeline_run_stage의 산출물 텍스트)은 전혀 안
// 보여줬다 — 결재자가 다른 탭을 또 열어 역추적해야 했다(그마저도
// pipeline_run_stage는 볼 수 있는 다른 화면 자체가 없었다). "내용"
// 열을 추가해 `a.content`가 있으면 보기 버튼을, 없으면(assignment
// 등 애초에 리뷰할 본문이 없는 유형) "-"를 보여준다.
// 사용자 요청(2026-11-03, "결재 내 승인 대기의 경우 요청자가, 내가
// 요청한 건의 경우 결재권자가 표시되도록 개선") — 화면마다 궁금한
// "사람"이 다르다: "내 승인 대기함"은 누가 이걸 요청했는지(요청자),
// "내가 요청한 건"은 누가 처리하는지/했는지(결재권자). `personColumn`
// 으로 어느 쪽을 보여줄지 고른다.
function approvalTable(rows, withActions, personColumn) {
  const headers = ["ID", "업무", "대상"];
  if (personColumn === "requester") headers.push("요청자");
  else if (personColumn === "approver") headers.push("결재권자");
  headers.push("단계", "상태", "내용");
  if (withActions) headers.push("동작");
  return table(headers, rows.map(a => {
    const taskCell = a.task_id != null
      ? "#" + a.task_id + " " + esc(a.task_role_name || "")
      : '<span class="muted">(지워진 ' + esc(label(a.subject_type)) + " #" + a.subject_id + ")</span>";
    const resourceCell = a.resource_name
      ? esc(label(a.subject_type)) + ": " + esc(label(a.resource_type)) + " " + esc(a.resource_name)
      : esc(label(a.subject_type));
    const contentCell = a.content
      ? '<button type="button" class="small" data-approval-content-toggle="' + a.id + '">내용 보기</button>'
      : '<span class="muted">-</span>';
    const row = [a.id, taskCell, resourceCell];
    if (personColumn === "requester") {
      row.push(esc(a.requested_by_username || ("#" + a.requested_by)));
    } else if (personColumn === "approver") {
      // 처리 완료됐으면 실제로 결정한 사람(approved_by)을, 아직
      // pending이면 배정된 결재권자(approver_id)를 보여준다 —
      // approver_id가 비어 있으면(상사 미지정) "Admin/매니저 누구나"가
      // 처리할 수 있다는 뜻이라 그대로 알려준다.
      if (a.status !== "pending") {
        row.push(a.approved_by_username ? esc(a.approved_by_username) : '<span class="muted">-</span>');
      } else if (a.approver_id != null) {
        row.push(esc(a.approver_username || ("#" + a.approver_id)));
      } else {
        row.push('<span class="muted">미지정(Admin/매니저 누구나)</span>');
      }
    }
    row.push(a.step, esc(label(a.status)), contentCell);
    if (withActions) {
      row.push(a.status === "pending"
        ? '<div class="actions">' +
          '<input placeholder="사유(선택)" data-decide-reason-for="' + a.id + '" style="width:120px">' +
          '<button class="small" data-decide="' + a.id + '" data-approve="1">승인</button>' +
          '<button class="small danger" data-decide="' + a.id + '" data-approve="0">반려</button></div>'
        : "");
    }
    return row;
  }));
}

/* ---------- AI 에이전트 생성 ---------- */

async function renderJobs(content) {
  // 사용자 보고(2026-10-06, "업무 요청 ID 등 불러올 수 있는 정보들을
  // 찾아 오입력되지 않도록 개선") — 이전엔 "업무 요청 ID"·"할 일 ID"를
  // 숫자로 직접 타이핑해야 해서, 존재하지 않거나 엉뚱한 ID를 입력해도
  // 제출 전엔 알 방법이 없었다(404는 제출 후에야 뜸). 이미 서버가 갖고
  // 있는 업무 요청/할 일 목록을 그대로 불러와 고르는 방식으로 바꾼다.
  const briefs = await api("GET", "/api/briefs");
  const briefLabel = (b) => {
    const text = (b.raw_text || "").trim();
    const snippet = text.length > 24 ? text.slice(0, 24) + "…" : text;
    return "#" + b.id + " " + snippet;
  };
  const briefOptionsHtml = briefs.length
    ? briefs.map(b => '<option value="' + b.id + '">' + esc(briefLabel(b)) + '</option>').join("")
    : '<option value="">(등록된 업무 요청이 없습니다)</option>';
  const modelsByProvider = await fetchModelsByProvider();
  // 사용자 보고(2026-10-27, "Agent 직접 생성 스킬이 도구/스킬 Pool의
  // 스킬인지 확인" → "skill pool에 등록된 스킬명을 보여주고 선택할 수
  // 있도록 개선") — `skillPickerHtml()`의 입력창이 `list="skill-
  // datalist"`를 참조하는데, 이 화면(renderJobs)은 지금까지 그
  // `<datalist id="skill-datalist">` 자체를 한 번도 안 만들어 넣었다
  // (renderBriefs/renderHumanPool은 만든다) — 그래서 "Agent 직접
  // 생성"의 스킬 입력창은 타이핑해도 자동완성이 전혀 안 뜨는, 그냥
  // 빈 자유 입력창이었다. 다른 두 화면과 같은 패턴으로 끼워 넣는다.
  const skillDatalist = await skillDatalistHtml();

  content.innerHTML =
    '<section class="card"><h2>AI 에이전트 생성</h2>' +
    '<form class="inline" id="job-form">' +
    '<select id="job-brief-id" required style="width:220px">' + briefOptionsHtml + '</select>' +
    '<select id="job-framework"><option value="stub">stub(무료 스텁)</option>' +
    '<option value="simple_llm">simple_llm(1회 LLM 호출)</option>' +
    '<option value="crewai">crewai(실제 CrewAI Agent)</option></select>' +
    '<select id="job-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    modelSelectHtml("job-model") +
    '<button class="primary" type="submit">생성 실행</button></form>' +
    '<div id="job-tasks-picker" class="muted" style="margin-bottom:0.75rem">업무 요청을 선택하면 그 아래 할 일 목록이 뜹니다.</div>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>stub</b>(무료): LLM을 전혀 호출하지 않는 규칙 기반 스텁입니다 — 역할명·스킬로 자리표시자 산출물만 만들고 즉시 끝납니다. 조직도/배정 흐름만 비용 없이 채워보고 싶을 때 씁니다.</li>' +
      '<li><b>simple_llm</b>(비용 발생): 실제 LLM API를 딱 한 번만 호출해 "이 역할이 처음 맡을 산출물 하나"를 받아옵니다. CrewAI 같은 프레임워크 오버헤드 없이 가장 가벼운 실제 호출입니다.</li>' +
      '<li><b>crewai</b>(비용 발생, 보통 더 큼): 실제 CrewAI Agent+Task+Crew를 구성해 실행합니다 — 필요 스킬에 지식검색/관계조회 같은 게 있으면 Skill Pool이 매칭해주는 실제 도구를 생성 시점부터 붙여서 태어나므로, 이후 "Crew 실행" 탭에서 바로 협업시킬 Agent를 만들 때 씁니다.</li>' +
      '<li>만든 Agent는 이 탭이 아니라 "AI 에이전트 관리" 탭에서 온보딩 승인(pending_onboarding → active) 후에야 실제 업무 배정·실행에 쓸 수 있습니다.</li>' +
      '<li><b>중복 방지</b>: 같은 할 일(역할)에 이미 생성된 Agent가 있으면 새 Agent를 만들지 않고 기존 것을 재사용합니다 — 이 경우 "AI 에이전트 관리"에 새 행이 안 생기는데, 결과에 "재사용" 배지와 안내가 표시됩니다. 새 Agent를 만들려면 다른 할 일을 고르세요.</li>' +
      '</ul>'
    ) +
    '<div id="job-result"></div></section>' +
    // 사용자 요청(2026-10-21, "Agent 직접 생성" — CrewAI Studio UI
    // 참조) — 위 Task 기반 생성과 달리 Brief/Task 선택 없이 역할명·
    // goal·backstory·expected_output·스킬을 바로 입력해 즉시 등록한다.
    // skillPickerHtml()/initSkillPicker()는 이미 Task 생성·사람 인력
    // 등록에서 쓰는 컴포넌트를 그대로 재사용(새로 안 만듦).
    '<section class="card"><h2>Agent 직접 생성(Brief/Task 없이 즉시 등록)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>역할명만 필수입니다 — goal/backstory/expected_output을 비워두면 실행 시점에 역할명·스킬 기반으로 자동 생성됩니다("AI 에이전트 관리"에서 나중에 덮어쓸 수 있는 것과 동일한 규칙).</li>' +
      '<li>이렇게 만든 Agent도 똑같이 <b>pending_onboarding</b> 상태로 시작합니다 — "AI 에이전트 관리" 탭에서 온보딩 승인해야 실제 업무에 쓸 수 있습니다. 도구 연결도 그 탭에서(이 Agent가 실제로 생긴 뒤에만 가능).</li>' +
      '</ul>'
    ) +
    '<form id="direct-agent-form">' +
    '<label>역할명(필수)<br><input id="direct-agent-role-name" required style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<label style="display:block;margin-top:0.5rem">스킬<br>' + skillPickerHtml("direct-agent-skills-picker", "direct-agent-skills") + skillDatalist + '</label>' +
    '<label style="display:block;margin-top:0.5rem">Goal(목표)<br><textarea id="direct-agent-goal" rows="2" style="width:100%;box-sizing:border-box;margin-top:0.2rem" placeholder="비워두면 역할명·스킬 기반으로 자동 생성"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">Backstory(배경)<br><textarea id="direct-agent-backstory" rows="2" style="width:100%;box-sizing:border-box;margin-top:0.2rem" placeholder="비워두면 역할명·스킬 기반으로 자동 생성"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">Expected Output(기대 산출물)<br><textarea id="direct-agent-expected" rows="2" style="width:100%;box-sizing:border-box;margin-top:0.2rem" placeholder="비워두면 플랫폼 공통 기본 문구 사용"></textarea></label>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">Agent 생성</button>' +
    '</form>' +
    '<div id="direct-agent-result"></div></section>';

  const tasksPicker = document.getElementById("job-tasks-picker");
  const loadTasksForBrief = async (briefId) => {
    if (!briefId) {
      tasksPicker.innerHTML = '<span class="muted">업무 요청을 선택하면 그 아래 할 일 목록이 뜹니다.</span>';
      return;
    }
    tasksPicker.innerHTML = '<span class="muted">불러오는 중...</span>';
    const tasks = await api("GET", "/api/briefs/" + briefId + "/tasks");
    tasksPicker.innerHTML = tasks.length
      ? '<b>할 일 선택(여러 개 가능):</b> ' +
        tasks.map(t =>
          '<label style="margin-right:0.75rem"><input type="checkbox" class="job-task-checkbox" value="' + t.id + '"> ' +
          "#" + t.id + " " + esc(t.role_name) +
          (t.required_skills.length ? " (" + esc(t.required_skills.join(", ")) + ")" : "") +
          "</label>"
        ).join("")
      : '<span class="muted">이 업무 요청엔 할 일이 없습니다 — "업무 요청" 탭의 "할 일로 나누기"에서 먼저 만드세요.</span>';
  };

  const briefSelect = document.getElementById("job-brief-id");
  briefSelect.addEventListener("change", () => loadTasksForBrief(briefSelect.value));
  if (briefSelect.value) await loadTasksForBrief(briefSelect.value);
  wireProviderModelSelect("job-provider", "job-model", modelsByProvider);

  document.getElementById("job-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const briefId = Number(briefSelect.value);
    if (!Number.isInteger(briefId) || briefId < 1) {
      showMsg("global-msg", "업무 요청을 선택하세요.", "error"); return;
    }
    const taskIds = Array.from(tasksPicker.querySelectorAll(".job-task-checkbox:checked")).map(cb => Number(cb.value));
    if (!taskIds.length) {
      showMsg("global-msg", "할 일을 하나 이상 선택하세요.", "error"); return;
    }
    const framework = document.getElementById("job-framework").value;
    const provider = document.getElementById("job-provider").value || null;
    const model = modelSelectValue("job-model");
    const submitBtn = ev.submitter || document.querySelector("#job-form button[type=submit]");
    const busyLabel = framework === "stub" ? "생성 중…" : "생성 중…(실제 API 호출, 시간이 걸릴 수 있습니다)";
    try {
      const result = await withBusy(submitBtn, busyLabel, () => api("POST", "/api/briefs/" + briefId + "/generate", {
        task_ids: taskIds, use_llm: framework !== "stub", framework, provider, model,
      }));
      renderJobResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-10-21, "Agent 직접 생성") — 위 Task 기반 생성과
  // 별개인 두 번째 폼. skillPickerHtml()가 이미 넣어둔 hidden input의
  // 값(쉼표로 join)을 flush() 호출 뒤에 읽는다(입력창에 남은 값을
  // "추가" 안 누르고 바로 제출해도 놓치지 않도록 — 기존 호출부 관례).
  const directSkillPicker = initSkillPicker("direct-agent-skills-picker", "direct-agent-skills", []);
  document.getElementById("direct-agent-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const roleName = document.getElementById("direct-agent-role-name").value.trim();
    if (!roleName) { showMsg("global-msg", "역할명을 입력하세요.", "error"); return; }
    directSkillPicker.flush();
    const skillsRaw = document.getElementById("direct-agent-skills").value;
    const skillTags = skillsRaw ? skillsRaw.split(",").filter(Boolean) : [];
    const submitBtn = ev.submitter || document.querySelector("#direct-agent-form button[type=submit]");
    try {
      const result = await withBusy(submitBtn, "생성 중…", () => api("POST", "/api/agent-pool", {
        role_name: roleName,
        skill_tags: skillTags,
        goal: document.getElementById("direct-agent-goal").value,
        backstory: document.getElementById("direct-agent-backstory").value,
        expected_output: document.getElementById("direct-agent-expected").value,
      }));
      document.getElementById("direct-agent-result").innerHTML =
        '<p class="msg ok">Agent #' + result.id + '을(를) 등록했습니다 — "AI 에이전트 관리" 탭에서 온보딩 승인 후, 필요하면 도구도 거기서 연결하세요.</p>';
      document.getElementById("direct-agent-form").reset();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function renderJobResult(result) {
  const box = document.getElementById("job-result");
  const reusedItems = (result.items || []).filter(it => it.reused);
  const reusedCount = result.reused_count != null ? result.reused_count : reusedItems.length;

  // dedup: 같은 역할(task)에 이미 생성된 에이전트가 있으면 새 에이전트를
  // 만들지 않고 기존 job_item을 재사용한다. 이 경우 "AI 에이전트 관리"에 새
  // 행이 안 생기는데, 화면이 "성공"으로만 뜨면 "생성했는데 안 보인다"는
  // 오해를 피할 수 없다 — 재사용을 명시적으로 알리고 관리 탭으로 이동을 돕는다.
  //
  // **정정(2026-10-06, 사용자 보고 "생성은 '성공'으로 떴는데 관리 탭에
  // 안 보임")**: 배너가 "재사용했다"고만 말하고 "그래서 몇 번 Agent를
  // 보라는 건지"는 안 알려줘서, 관리 탭에 가도 여러 행 중 어떤 게
  // 그건지 못 찾는 게 진짜 혼란의 원인이었다 — 이제 재사용된 기존
  // Agent의 ID·역할명을 배너와 표에 직접 명시한다.
  let banner = "";
  if (reusedCount > 0) {
    const refs = reusedItems
      .filter(it => it.agent_pool_id != null)
      .map(it => esc(it.agent_pool_code || ("#" + it.agent_pool_id)) + "(" + esc(it.agent_pool_role_name) + ")")
      .join(", ");
    banner =
      '<div class="msg warn"><b>새 에이전트가 생성되지 않았습니다.</b> ' +
      "같은 역할에 이미 생성된 에이전트가 있어 " + reusedCount + "건을 재사용했습니다(중복 방지)" +
      (refs ? " — 기존 Agent " + refs + "를 그대로 씁니다." : ".") +
      ' "AI 에이전트 관리" 탭에서 위 ID로 찾으세요. 새 에이전트를 만들려면 다른 할 일 ID로 생성하세요. ' +
      '<button class="small" id="goto-agentpool">AI 에이전트 관리로 이동</button></div>';
  }

  // 사용자 요청(2026-10-07, "현재 UI가 복수의 에이전트 그룹 생성 및
  // HAIR 관점의 연동에 적합한 경우는 아님... AI 에이전트 생성과 통합
  // 검토") — 한 브리프의 여러 할 일을 한 번에 골라 역할별 Agent를
  // 동시에 만드는 기능이 이미 있었으므로(CrewAI의 "역할 기반 그룹"
  // 장점이 여기서 나온다), 생성 직후 그 Agent들을 바로 "팀"으로
  // 묶을 수 있게 한다 — 지금까지는 "Crew 실행"이라는 업무와 무관한
  // 즉석 화면에서만 여러 Agent를 다룰 수 있었고, 그룹을 저장할 방법도
  // 없었다. "팀 관리"에서 하려면 Agent를 다른 할 일로 따로 만들어야
  // 했던 갭도 이걸로 없어진다 — 할 일을 처음부터 같이 고르면
  // 되므로.
  const agentItems = result.items.filter(it => it.agent_pool_id != null);
  const bundleCheckbox = (it) => it.agent_pool_id != null
    ? '<input type="checkbox" class="bundle-agent-checkbox" value="' + it.agent_pool_id + '" checked>'
    : "";
  const bundleFormHtml = agentItems.length > 1
    ? '<form id="bundle-team-form" class="inline" style="margin-top:0.75rem">' +
      '<input id="bundle-team-name" placeholder="팀 이름" required>' +
      '<button class="small" type="submit">체크한 Agent를 팀으로 묶기</button></form>' +
      '<p class="muted">역할별로 같이 만든 Agent를 팀으로 등록해두면, 나중에 "업무 요청"의 할 일 상세에서 팀 전체를 배정하고 "팀 Agent 실행"으로 CrewAI 협업을 시킬 수 있습니다 — "팀 관리" 탭에서 멤버를 더 추가/제외할 수도 있습니다.</p>'
    : "";

  box.innerHTML = banner +
    "<p>생성 결과 상태: <b>" + esc(label(result.status)) + "</b></p>" +
    table(["묶기", "ID", "할 일 ID", "상태", "연결된 Agent", "에러", "재시도", "동작"], result.items.map(it => [
      bundleCheckbox(it),
      it.id, it.task_id,
      esc(label(it.status)) + (it.reused ? ' <span class="badge-reused">재사용</span>' : ""),
      it.agent_pool_id != null ? esc(it.agent_pool_code || ("#" + it.agent_pool_id)) + " " + esc(it.agent_pool_role_name) : '<span class="muted">-</span>',
      esc(it.error || ""), it.retry_count,
      it.status === "failed" ? '<button class="small" data-retry-job="' + result.job_id + '" data-retry-item="' + it.id + '">재시도</button>' : "",
    ])) +
    bundleFormHtml;
  box.querySelectorAll("[data-retry-item]").forEach(btn => {
    btn.onclick = async () => {
      try {
        const r = await api("POST", "/api/jobs/" + btn.dataset.retryJob + "/items/" + btn.dataset.retryItem + "/retry");
        renderJobResult(r);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  const goto = document.getElementById("goto-agentpool");
  if (goto) goto.onclick = () => selectTab("agentpool");

  const bundleForm = document.getElementById("bundle-team-form");
  if (bundleForm) {
    bundleForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const agentIds = Array.from(box.querySelectorAll(".bundle-agent-checkbox:checked")).map(cb => Number(cb.value));
      if (agentIds.length < 1) { showMsg("global-msg", "팀으로 묶을 Agent를 하나 이상 체크하세요.", "error"); return; }
      const name = document.getElementById("bundle-team-name").value.trim();
      if (!name) { showMsg("global-msg", "팀 이름을 입력하세요.", "error"); return; }
      try {
        const team = await api("POST", "/api/teams", { name });
        for (const agentId of agentIds) {
          await api("POST", "/api/teams/" + team.id + "/members", { member_type: "agent", member_id: agentId });
        }
        showMsg("global-msg", "팀 \\"" + name + "\\"을(를) 만들고 Agent " + agentIds.length + "명을 추가했습니다 — \\"팀 관리\\" 탭에서 확인하세요.", "ok");
        bundleForm.reset();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
}

/* ---------- 사람 인력 ---------- */

async function renderHumanPool(content) {
  const [people, skillDatalist, userNames] = await Promise.all([
    api("GET", "/api/human-pool"),
    skillDatalistHtml(),
    api("GET", "/api/users/names").catch(() => []),
  ]);
  const isAdmin = ME.role === "admin";
  // 사용자 보고(2026-10-06, "사람 인력으로 등록되어 있지만 사용자 관리에
  // 등록되지 않은 경우 로그인도 안 돼 일을 할 수 없다") — 등록을 로그인
  // 계정과 무관하게 둬서 매칭·배정·승인이 경고 없이 통과되던 갭을
  // 메운다. 계정은 한 Human Pool 레코드에만 연결 가능하므로(백엔드가
  // 거부), 이미 다른 레코드에 연결된 계정은 "등록 가능한 계정" 목록에서
  // 제외한다.
  const linkedUserIds = new Set(people.filter(p => p.user_id !== null).map(p => p.user_id));
  const usersById = Object.fromEntries(userNames.map(u => [u.id, u]));
  const linkableOptionsHtml = (currentUserId) =>
    '<option value="">— 계정 선택 —</option>' +
    userNames
      .filter(u => u.id === currentUserId || !linkedUserIds.has(u.id))
      .map(u => '<option value="' + u.id + '"' + (u.id === currentUserId ? " selected" : "") +
        '>' + esc(u.username) + " (" + esc(u.role) + ")</option>")
      .join("");

  content.innerHTML =
    '<section class="card"><h2>사람 인력 등록</h2>' +
    '<form class="inline" id="hp-form">' +
    '<select id="hp-user-id" required>' + linkableOptionsHtml(null) + '</select>' +
    '<input id="hp-name" placeholder="이름(표시용, 계정 선택 시 자동 채움)" required>' +
    '<input id="hp-role" placeholder="역할" required>' +
    skillPickerHtml("hp-skills-picker", "hp-skills") +
    skillDatalist +
    '<input id="hp-cost" type="number" step="0.01" placeholder="비용" value="0">' +
    // 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번 적용 예정으로
    // 8자리~12자리 입력 및 표시 가능해야 함") — 아직 전사 사번 체계가
    // 없는 사람이 많아 선택 입력(비워도 등록 가능).
    '<input id="hp-employee-id" placeholder="사번(선택, 숫자 8~12자리)" pattern="\\d{8,12}" title="숫자 8~12자리" style="width:160px">' +
    '<button class="primary" type="submit">등록</button></form>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>계정 선택</b>은 "사용자 관리"에서 먼저 만든 로그인 계정 중 아직 다른 사람 인력에 연결 안 된 것만 보여줍니다 — 계정 없이 등록하면 그 사람은 로그인할 수 없어 매칭·배정이 돼도 실제로 업무를 볼 수 없으니, 계정을 먼저 "사용자 관리"에서 만드세요.</li>' +
      '<li><b>사번</b>은 선택 입력입니다 — 비워두면 목록에 "#id"로 표시되고, 나중에 목록의 "사번 저장"으로 언제든 채울 수 있습니다(숫자 8~12자리만 허용, 중복 불가).</li>' +
      '<li>스킬은 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(여러 개 등록 가능, 칩의 ×로 제거) — 목록에 없는 이름도 자유롭게 추가할 수 있습니다.</li>' +
      '<li><b>비용</b>은 인력 매칭 점수의 25%(또는 성과점수가 없으면 40%)를 차지합니다 — 낮을수록 유리합니다.</li>' +
      '<li>아직 성과 데이터가 없으니 초기엔 "가능" 상태여야 매칭 후보로 잡히고(가용성이 매칭 점수의 40~60%), 스킬은 할 일의 필요 스킬과 겹칠수록 유리합니다.</li>' +
      '</ul>'
    ) +
    '</section><section class="card"><h2>사람 인력 목록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>동의</b>는 성과 리뷰 사전 동의입니다 — 동의 없이는 성과점수를 절대 입력·노출하지 않고, 동의를 철회하면 기존 점수도 즉시 지워집니다.</li>' +
      '<li><b>오프보딩</b>은 가용성을 offboarded로 바꿔 매칭 후보에서 빠지게 하는데, 이 화면에서 다시 되돌리는 버튼은 없습니다(사실상 편도) — 실수로 눌렀다면 관리자가 DB를 직접 고쳐야 합니다.</li>' +
      '<li><b>삭제</b>(관리자 전용)는 오프보딩과 별개로 레코드 자체를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 삭제가 막힙니다.</li>' +
      '<li><b>계정 연결</b>은 이 사람을 로그인 계정과 이어줍니다 — 연결해야만 그 사람이 "내 업무" 탭에서 자기 업무를 볼 수 있습니다(연결 안 하면 구조적으로 절대 안 보입니다). 드롭다운엔 아직 다른 사람 인력에 연결 안 된 계정만 뜹니다.</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번 적용 예정으로
    // 8자리~12자리 입력 및 표시 가능해야 함") — "ID" 열을 사번으로
    // 바꾸고(미입력이면 Agent/Tool/Skill과 같은 "#id" 폴백), 기존
    // 레코드에도 "사번 저장" 인라인 입력을 둔다(점수입력과 같은 패턴).
    table(["사번", "이름", "역할", "스킬", "가용성", "비용", "동의", "성과점수", "연결된 계정", "동작"], people.map(p => {
      const middleActions = p.availability === "offboarded" ? "" :
        '<button class="small" data-toggle-consent="' + p.id + '" data-consent="' + (p.performance_consent ? "0" : "1") + '">' +
          (p.performance_consent ? "동의 철회" : "동의") + "</button>" +
        '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="score-' + p.id + '">' +
        '<button class="small" data-set-score="' + p.id + '">점수입력</button></span>' +
        '<span class="pair"><input placeholder="사번(숫자 8~12자리)" pattern="\\d{8,12}" title="숫자 8~12자리" id="empid-' + p.id + '" value="' + escAttr(p.employee_id || "") + '" style="width:150px">' +
        '<button class="small" data-set-employee-id="' + p.id + '">사번 저장</button></span>' +
        '<span class="pair"><select id="linkuser-' + p.id + '">' + linkableOptionsHtml(p.user_id) + '</select>' +
        '<button class="small" data-link-user="' + p.id + '">계정 연결</button></span>' +
        '<button class="small danger" data-offboard="' + p.id + '">오프보딩</button>';
      const deleteAction = isAdmin ? '<button class="small danger" data-delete-human="' + p.id + '">삭제</button>' : "";
      const linkedAccount = p.user_id === null ? "-" :
        (usersById[p.user_id] ? esc(usersById[p.user_id].username) : "#" + p.user_id);
      return [esc(p.employee_id || ("#" + p.id)), esc(p.name), esc(p.role), esc(p.skill_tags.join(", ")), esc(label(p.availability)), p.cost_estimate,
        p.performance_consent ? "동의함" : "미동의",
        p.performance_score === null ? "-" : p.performance_score,
        linkedAccount,
        '<div class="actions">' + middleActions + deleteAction + "</div>"];
    })) + "</section>";

  const skillPicker = initSkillPicker("hp-skills-picker", "hp-skills", []);
  document.getElementById("hp-user-id").addEventListener("change", (ev) => {
    const selected = userNames.find(u => String(u.id) === ev.target.value);
    const nameInput = document.getElementById("hp-name");
    if (selected && !nameInput.value.trim()) nameInput.value = selected.username;
  });
  document.getElementById("hp-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    skillPicker.flush();
    const userIdValue = document.getElementById("hp-user-id").value;
    if (!userIdValue) {
      showMsg("global-msg", '계정을 선택하세요 — 없으면 "사용자 관리"에서 먼저 만드세요.', "error");
      return;
    }
    const employeeIdValue = document.getElementById("hp-employee-id").value.trim();
    try {
      await api("POST", "/api/human-pool", Object.assign({
        name: document.getElementById("hp-name").value,
        role: document.getElementById("hp-role").value,
        skill_tags: document.getElementById("hp-skills").value.split(",").map(s => s.trim()).filter(Boolean),
        cost_estimate: Number(document.getElementById("hp-cost").value) || 0,
        user_id: Number(userIdValue),
      }, employeeIdValue ? { employee_id: employeeIdValue } : {}));
      showMsg("global-msg", "등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  content.querySelectorAll("[data-toggle-consent]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/human-pool/" + btn.dataset.toggleConsent + "/consent", { consent: btn.dataset.consent === "1" });
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-score]").forEach(btn => {
    btn.onclick = async () => {
      const input = document.getElementById("score-" + btn.dataset.setScore);
      try {
        await api("POST", "/api/human-pool/" + btn.dataset.setScore + "/performance", { score: Number(input.value) });
        showMsg("global-msg", "점수를 입력했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-employee-id]").forEach(btn => {
    btn.onclick = async () => {
      const input = document.getElementById("empid-" + btn.dataset.setEmployeeId);
      const value = input.value.trim();
      if (!value) { showMsg("global-msg", "사번을 입력하세요.", "error"); return; }
      try {
        await api("PATCH", "/api/human-pool/" + btn.dataset.setEmployeeId, { employee_id: value });
        showMsg("global-msg", "사번을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-link-user]").forEach(btn => {
    btn.onclick = async () => {
      const select = document.getElementById("linkuser-" + btn.dataset.linkUser);
      const userId = Number(select.value);
      if (!Number.isInteger(userId) || userId < 1) {
        showMsg("global-msg", "연결할 계정을 선택하세요.", "error"); return;
      }
      try {
        await api("PATCH", "/api/human-pool/" + btn.dataset.linkUser, { user_id: userId });
        showMsg("global-msg", "계정을 연결했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-offboard]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("정말 오프보딩하시겠습니까?"))) return;
      try {
        await api("POST", "/api/human-pool/" + btn.dataset.offboard + "/offboard");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-human]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 사람 인력 레코드를 완전히 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/human-pool/" + btn.dataset.deleteHuman);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- AI 에이전트 ---------- */

// 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
// 하는 기능을 구현해줘" → 명확화: 실제 LLM 가중치 재학습이 아니라 CrewAI
// Agent/LLM 동작 파라미터 조정) — 비워두면 CrewAI 기본값을 그대로 쓴다.
// 숫자 입력은 빈 칸이 "기본값 사용"이고, bool류는 체크박스 대신
// "기본값/켜기/끄기" 3단 select를 쓴다 — 체크박스로 하면 "체크 안 함"이
// "명시적으로 false로 저장"과 구분이 안 돼, CrewAI 기본값이 true인
// respect_context_window 같은 값을 의도치 않게 false로 덮어쓸 위험이
// 있다(실제로 설계 중 발견한 함정이라 기록해둔다).
function _tuneTriStateHtml(id, current) {
  const value = current === true ? "true" : current === false ? "false" : "";
  return '<select id="' + id + '">' +
    '<option value=""' + (value === "" ? " selected" : "") + '>기본값 사용</option>' +
    '<option value="true"' + (value === "true" ? " selected" : "") + '>켜기</option>' +
    '<option value="false"' + (value === "false" ? " selected" : "") + '>끄기</option>' +
    '</select>';
}
// 사용자 요청(2026-10-18, "Agent의 목표, 기능, 입출력 등을 고려해 가용한
// LLM 모델에서 종류 및 설정 등을 최적화 setting 하는 admin 기능") — 규칙
// 기반 추천(비용 없음, `GET .../optimize-recommendation`)과 선택적 LLM
// 보정(비용 발생, `POST .../optimize-recommendation/refine`)의 결과를
// 같은 모양으로 렌더링한다. 전역 위임 클릭 리스너(아래, "최적화 추천"/
// "AI 의견 보기"/"추천값 입력란에 채우기" 버튼)와 `renderAgentOptimization()`
// 둘 다 재사용한다.
const _OPTIMIZE_TIER_LABELS = {
  deep_reasoning: "심층추론(기획/분석/설계)",
  precision_fast: "정밀·신속(구현/리뷰/QA)",
  standard: "표준(작성/조정)",
};
function _renderOptimizeRecommendation(agentId, rec, source) {
  const target = (source === "llm" && rec.llm_refined) ? rec.llm_refined : rec.recommended;
  const tuningText = Object.keys(target.tuning_config || {}).length
    ? Object.entries(target.tuning_config).map(([k, v]) => k + "=" + v).join(", ")
    : "(변경 없음)";
  let html = '<div class="msg ' + (rec.changed ? "warn" : "ok") + '" style="margin-top:0.5rem">' +
    '<div><span class="gate-badge">' + esc(_OPTIMIZE_TIER_LABELS[rec.tier] || rec.tier) + "</span> " +
    '<small class="muted">' + esc(rec.classification_source) + "</small></div>" +
    "<p>" + esc(rec.rationale) + "</p>" +
    "<p><b>추천:</b> " + esc(target.provider || "(현재 유지)") + " / " + esc(target.model || "(현재 유지)") +
    "<br><small>" + esc(tuningText) + "</small></p>";
  if (source === "llm" && rec.llm_rationale) {
    html += '<p class="muted"><b>AI 보정 근거:</b> ' + esc(rec.llm_rationale) + "</p>";
  }
  if (source === "llm" && rec.source === "rule_based_fallback") {
    html += '<p class="muted">⚠ AI 보정 실패 — 규칙 기반 추천을 그대로 유지합니다(' +
      esc(rec.llm_error || "") + ")</p>";
  }
  // 사용자 요청(2026-10-26, "모델 추천의 경우 모델 레지스트리에 등록된
  // 모델을 선택할 수 있도록 해줘") — 1위 추천만 고정해 채우지 않고,
  // `agent_optimizer.recommend_model()`이 함께 돌려준 "등록된 활성
  // 모델 전체(이 tier 기준 순위)" 후보 목록을 셀렉트로 보여줘 admin이
  // 2위/3위 등 다른 레지스트리 모델을 직접 골라 채울 수 있게 한다.
  const candidates = (rec.recommended && rec.recommended.candidates) || [];
  const pickSelId = "optimize-model-pick-" + agentId;
  if (candidates.length) {
    html += '<div style="margin-top:0.3rem"><label>등록된 모델 중 선택: ' +
      '<select id="' + pickSelId + '" data-candidates="' + escAttr(JSON.stringify(candidates)) + '">' +
      candidates.map((c, i) => {
        const selected = (c.provider === target.provider && c.model === target.model) ? " selected" : "";
        const label = (c.tier_mapped ? "★ " : "") + c.provider + " / " + c.model;
        return '<option value="' + i + '"' + selected + '>' + esc(label) + '</option>';
      }).join("") +
      '</select> <span class="muted">(★ = 이 역할 tier에 관리자가 직접 매핑한 모델)</span></label></div>';
  }
  html += '<button class="small" data-fill-optimize="' + agentId + '" data-optimize-payload="' +
    escAttr(JSON.stringify(target)) + '" data-optimize-pick-sel="' +
    (candidates.length ? pickSelId : "") + '">추천값 입력란에 채우기</button> ';
  if (source !== "llm") {
    html += '<button class="small" data-refine-optimize="' + agentId + '">AI 의견 보기(비용 발생)</button>';
  }
  html += "</div>";
  return html;
}
function tuningFormHtml(a) {
  const t = a.tuning_config || {};
  const num = (id, value, placeholder) =>
    '<input type="number" id="' + id + '" value="' + (value === undefined || value === null ? "" : value) +
    '" placeholder="' + placeholder + '" style="width:5rem">';
  return '<details><summary>파인튜닝</summary>' +
    '<div class="inline" style="flex-wrap:wrap">' +
    '<label>max_iter ' + num("tune-maxiter-" + a.id, t.max_iter, "기본값") + '</label>' +
    '<label>max_rpm ' + num("tune-maxrpm-" + a.id, t.max_rpm, "무제한") + '</label>' +
    '<label>max_execution_time(초) ' + num("tune-maxtime-" + a.id, t.max_execution_time, "무제한") + '</label>' +
    '<label>max_retry_limit ' + num("tune-maxretry-" + a.id, t.max_retry_limit, "기본값") + '</label>' +
    '<label>temperature <input type="number" step="0.1" min="0" max="2" id="tune-temp-' + a.id +
    '" value="' + (t.temperature ?? "") + '" placeholder="기본값" style="width:5rem">' +
    '<small style="display:block;color:#888">Anthropic은 0~1만 허용 — 1 초과는 OpenAI/Ollama 전용으로 두세요</small></label>' +
    '<label>reasoning ' + _tuneTriStateHtml("tune-reasoning-" + a.id, t.reasoning ?? null) + '</label>' +
    '<label>max_reasoning_attempts ' + num("tune-maxreasonattempts-" + a.id, t.max_reasoning_attempts, "무제한(reasoning 켰을 때만 의미 있음)") + '</label>' +
    '<label>코드 실행 허용 ' + _tuneTriStateHtml("tune-codeexec-" + a.id, t.allow_code_execution ?? null) + '</label>' +
    '<label>컨텍스트 자동 축소 ' + _tuneTriStateHtml("tune-respectctx-" + a.id, t.respect_context_window ?? null) + '</label>' +
    '<button class="small" data-set-tuning="' + a.id + '">파인튜닝 저장</button>' +
    '</div></details>';
}

// 사용자 요청(2026-10-20, "CrewAI 에이전트... role, goal, backstory,
// tool... 상세조정하고 수정하기 위한 고급 에이전트 관리 기능") —
// 지금까지 `Agent.goal`/`backstory`는 역할명+스킬로 매번 자동 생성됐고
// `Task.expected_output`은 플랫폼 전역 하드코딩 문구 하나뿐이었다.
// 비워두면 그대로 자동 생성(하위 호환) — `effective_*`(서버가 미리
// 계산해 보내주는 "지금 실제로 적용 중인 값")를 placeholder로 보여줘
// 뭐가 쓰이고 있는지 먼저 보이게 한다.
function personaFormHtml(a) {
  const field = (id, label, stored, effective) =>
    '<label style="display:block;margin-top:0.4rem">' + label + '<br>' +
    '<textarea id="' + id + '" rows="3" style="width:100%;box-sizing:border-box;margin-top:0.2rem" ' +
    'placeholder="비워두면 자동 생성: ' + escAttr(effective || "") + '">' + esc(stored || "") + '</textarea></label>';
  return '<details><summary>역할/인격(goal·backstory·expected_output)</summary>' +
    '<p class="muted">CrewAI Crew 실행(팀 Agent 실행·파이프라인·Crew 실행)에만 적용됩니다 — "실행(일 시키기)" 단독 호출에는 적용되지 않습니다(파인튜닝과 같은 범위). 빈 칸으로 저장하면 그 항목만 자동 생성으로 되돌아갑니다.</p>' +
    field("persona-goal-" + a.id, "Goal(목표)", a.goal, a.effective_goal) +
    field("persona-backstory-" + a.id, "Backstory(배경)", a.backstory, a.effective_backstory) +
    field("persona-expected-" + a.id, "Expected Output(기대 산출물)", a.expected_output, a.effective_expected_output) +
    '<button class="small" data-set-persona="' + a.id + '" style="margin-top:0.4rem">역할/인격 저장</button>' +
    '</details>';
}

async function renderAgentPool(content) {
  const agents = await api("GET", "/api/agent-pool");
  const isAdmin = ME.role === "admin";
  const canExecute = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = canExecute ? await fetchModelsByProvider() : {};
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") — 같은
  // 패턴: "Tool Pool ID" 숫자 입력도 "도구/스킬 Pool" 탭을 따로 열어
  // ID를 외워와야 했다. 도구 목록을 같이 받아 select로 바꾼다.
  const tools = canExecute ? await api("GET", "/api/tool-pool") : [];
  const toolNameById = {};
  tools.forEach(t => { toolNameById[t.id] = t.name; });
  content.innerHTML = '<section class="card"><h2>AI 에이전트 목록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"생성방식" 열은 "AI 에이전트 생성" 탭에서 어떤 옵션(stub/simple_llm/crewai)으로 태어났는지를 보여줍니다 — crewai로 만든 Agent만 그 시점에 스킬 매칭 도구를 갖고 태어납니다.</li>' +
    '<li>신규 Agent는 전부 pending_onboarding 상태로 생기고, <b>온보딩 승인</b>을 눌러야 active가 돼 "인력 검색"·매칭 후보로 노출됩니다.</li>' +
    '<li><b>오프보딩</b>(active→deprecated)은 이 화면에서 되돌릴 버튼이 없습니다 — 다시 쓰려면 관리자가 API/DB를 직접 손대야 합니다.</li>' +
    '<li><b>모델 설정</b>은 이 에이전트가 기본으로 쓸 LLM provider/model을 저장만 합니다(비용 없음).</li>' +
    '<li><b>실행</b>은 온보딩된(활성) 에이전트에게 실제로 업무를 맡깁니다 — 설정해둔 모델(없으면 전역 기본값)로 실제 API를 호출하므로 비용이 발생합니다. 스킬에 지식검색이 있으면 지식검색(RAG), 관계조회가 있으면 관계분석 방식으로 자동 동작합니다.</li>' +
    '<li><b>지식베이스로 보내기</b>는 이 에이전트의 산출물을 임베딩해 지식검색 에이전트가 찾을 수 있게 저장합니다(비용 발생).</li>' +
    '<li><b>MCP 서버(JSON)</b>에 예: [{"url":"http://localhost:8000/mcp","transport":"streamable-http"}] 형태로 넣으면 "Crew 실행" 시 이 Agent에 실제 MCP 도구가 붙습니다 — 빈 칸으로 두고 저장하면 기존 설정을 그대로 둡니다.</li>' +
    '<li><b>도구 연결</b>은 아래에서 "도구/스킬 Pool"에 등록된 도구를 골라 연결하면, 스킬 매칭과 무관하게 그 Agent에 도구가 하나 더 붙습니다.</li>' +
    // 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... AI
    // 에이전트 목록 포함 AI 에이전트 지정 항목이나 호출시(채팅 포함)에도
    // 활용될 수 있도록 개선") — "역할"(생성 당시 역할, 예: "백엔드
    // 개발자 Agent")이 식별자 역할까지 겸해 와서 같은 역할의 Agent가
    // 여럿이면 ID로만 구분해야 했다. 선택 입력인 "이름"을 추가해 지정한
    // 이름이 매칭·Crew 선택·팀 멤버·결재·채팅 @멘션 전부에서 역할명보다
    // 우선 쓰이게 한다(지정 안 하면 기존처럼 역할명 그대로 — 100% 하위
    // 호환).
    '<li><b>이름</b>은 선택 입력입니다 — 지정하면 매칭 결과·Crew 실행·팀 멤버·결재·인박스·채팅 @멘션 등 이 Agent가 보이는 모든 화면에서 역할명 대신 그 이름이 먼저 보입니다. 비워서 저장하면 다시 역할명으로 돌아갑니다.</li>' +
    '<li><b>파인튜닝</b>은 실제 LLM 가중치 재학습이 아니라 CrewAI Agent/LLM의 동작 파라미터(반복 횟수·분당 호출 제한·실행 시간 제한·재시도 횟수·temperature·reasoning·코드 실행 허용·컨텍스트 윈도우 자동 축소)를 저장합니다 — <b>framework가 crewai인 Agent가 "Crew 실행"에 투입될 때만</b> 반영되고, stub/simple_llm Agent나 생성 직후 단일 실행에는 적용되지 않습니다. 비워두면 CrewAI 기본값을 그대로 씁니다.</li>' +
    '<li><b>역할/인격(goal·backstory·expected_output)</b>은 지금까지 역할명·스킬로 매번 자동 생성되던 CrewAI Agent의 목표·배경과, 플랫폼 공통 문구였던 기대 산출물을 Agent별로 직접 덮어씁니다 — 파인튜닝과 같은 범위(Crew 실행 전용)이고, 비워두면 역시 자동 생성으로 돌아갑니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 레코드를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 막힙니다.</li>' +
    '</ul>'
    ) +
    // 사용자 보고(2026-10-14, 스크린샷 — 13개 열이 전부 끼어서 "성과
    // 점수(수동)" 같은 헤더가 한 글자씩 세로로 줄바꿈될 만큼 눌림) —
    // 식별·관리에 바로 필요한 "핵심 정보"(ID/이름/역할/스킬/상태/
    // 생성방식/동작)만 열로 남기고, "모델 실행 후에만 쌓이는 정보"
    // (소속팀/비용/성과점수/자동평가/최근 산출물)는 "상세정보"
    // 한 열의 접힌 패널로 묶는다.
    // 사용자 요청(2026-10-14, "Agent 목록에 LLM 정보는 기본으로 표시되도록
    // 해줘") — LLM(provider/model)만은 접지 않고 별도 열로 항상 보이게
    // 뺀다(실행에 어떤 모델이 쓰이는지는 "실행 후에만 쌓이는 정보"가
    // 아니라 실행 전에도 미리 확인해야 하는 설정이라 상세 패널에 묻혀
    // 있으면 안 됨 — "대화창 사용 모델 표시" 개선과 같은 취지).
    // 사용자 요청(2026-10-27, "노출되지 않도록 개선") — 정수 PK는
    // 화면에서 숨기고 코드만 보여준다(수정/삭제 등 동작은 a.id를 그대로
    // 쓰므로 기능은 안 바뀜).
    table(["코드", "이름", "역할", "스킬", "상태", "생성방식", "LLM", "상세정보", "동작"], agents.map(a => {
      // 사용자 보고(2026-10-14, 스크린샷 — "동작" 칸 하나에 10개 넘는
      // 버튼/입력이 전부 세로로 풀폭 스택돼 행 하나가 화면을 가득
      // 채움) — 자주 쓰는 상태변경/실행 버튼만 바로 보이게 남기고,
      // 나머지(이름·비용·점수·모델·도구연결·파인튜닝)는 "⚙ 설정"
      // 하나로 접는다.
      let actions = "";
      if (isAdmin) {
        if (a.status === "pending_onboarding") actions += '<button class="small" data-onboard="' + a.id + '">온보딩 승인</button>';
        if (a.status === "active") actions += '<button class="small danger" data-deprecate="' + a.id + '">오프보딩</button>';
        actions += '<button class="small danger" data-delete-agent="' + a.id + '">삭제</button>';
      }
      if (canExecute && a.status === "active") {
        actions += '<button class="small" data-execute="' + a.id + '">실행(일 시키기)</button>';
        // "지식 색인"만 남긴다 — 이 Agent 자신의 산출물을 "지식
        // 베이스"(전역·영구)로 승격하는 기능이라 Agent 화면이 맞는
        // 자리다. 반면 "참고자료 업로드"(2026-10-26, 1차)는 업무 맥락이
        // 없는 이 화면에 있던 게 원래 문제였다 — 사용자 분석(2026-10-26,
        // 2차, "A안 + B안")으로 "업무 실행 1회성 참고자료는 저장 없이
        // 그 실행에만 첨부돼야 한다"가 정리돼, 실제 업무 맥락이 있는
        // 협업 워크스페이스의 "AI 에이전트 실행" 카드로 옮기고 여기서는
        // 없앴다(`renderCollabWorkspace()`의 `ws-execute-attach-btn`
        // 참고).
        actions += '<button class="small" data-index-knowledge="' + a.id + '">지식베이스로 보내기</button>';
      }
      let settings = "";
      if (canExecute) {
        settings +=
          '<span class="pair"><input placeholder="이름(비우면 역할명으로)" id="name-' + a.id + '" value="' + escAttr(a.name || "") + '" style="width:140px">' +
          '<button class="small" data-set-name="' + a.id + '">이름 저장</button></span>';
      }
      if (isAdmin) {
        settings +=
          '<span class="pair"><input type="number" step="0.01" placeholder="비용" id="cost-' + a.id + '">' +
          '<button class="small" data-set-cost="' + a.id + '">비용입력</button></span>' +
          '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="ascore-' + a.id + '">' +
          '<button class="small" data-set-ascore="' + a.id + '">점수입력</button></span>';
      }
      if (canExecute) {
        const providers = ["anthropic", "openai", "ollama"];
        settings += '<select id="provider-' + a.id + '">' +
          providers.map(p => '<option value="' + p + '"' + (p === a.llm_provider ? " selected" : "") + ">" + p + "</option>").join("") +
          "</select>" +
          modelSelectHtml("model-" + a.id) +
          '<input placeholder="MCP 서버(JSON, 비우면 유지)" id="mcp-' + a.id + '" value="' + esc(a.mcp_servers && a.mcp_servers.length ? JSON.stringify(a.mcp_servers) : "").replace(/"/g, "&quot;") + '">' +
          '<button class="small" data-set-model="' + a.id + '">모델 설정</button>';
      }
      if (canExecute) {
        const toolOptions = tools.length
          ? tools.map(t => '<option value="' + t.id + '">' + esc(t.name) + " (#" + t.id + ")" +
              (a.linked_tool_ids.includes(t.id) ? " — 연결됨" : "") + '</option>').join("")
          : '<option value="">(등록된 도구 없음)</option>';
        settings +=
          '<select id="linktool-' + a.id + '">' + toolOptions + '</select>' +
          '<span class="pair"><button class="small" data-link-tool="' + a.id + '">연결</button>' +
          '<button class="small" data-unlink-tool="' + a.id + '">해제</button></span>' +
          '<span class="muted">연결된 도구: ' + (a.linked_tool_ids.length
            ? a.linked_tool_ids.map(id => esc(toolNameById[id] || ("#" + id))).join(", ")
            : "없음(스킬 기반 자동 연결은 별도)") + '</span>';
      }
      if (canExecute) {
        settings += tuningFormHtml(a);
        settings += personaFormHtml(a);
        settings += '<div><button class="small" data-recommend-optimize="' + a.id +
          '">⚡ 최적화 추천</button><div id="optimize-result-' + a.id + '"></div></div>';
      }
      if (settings) {
        actions += '<details><summary>⚙ 설정</summary><div class="inline" style="flex-wrap:wrap;align-items:center">' + settings + '</div></details>';
      }
      // 사용자 보고(2026-10-14, "최근 산출물도 전체화면/복사 가능한
      // 형태로 개선") — `last_output`은 보통 수백~수천 자짜리 실제
      // LLM 마크다운 산출물(제목·목록·코드블록 포함)인데, 80자 평문
      // 자르기만 하면 줄바꿈이 사라지고 마크다운 기호가 그대로 노출돼
      // 미리보기로서 의미가 없었다. 다른 화면(산출물 상세보기 등)과
      // 똑같이 `renderContent()`(Claude Artifact 스타일 — 복사·전체화면
      // 버튼은 전역 위임으로 이미 연결돼 있어 추가 배선 불필요)로
      // 렌더링한다.
      const output = a.last_output ? renderContent(a.last_output) : '<span class="muted">-</span>';
      const llmInfo = a.llm_provider ? esc(a.llm_provider) + "/" + esc(a.llm_model || "") : "-";
      // Phase 3 aggregate_agent_score() — 관리자 수동 점수와 별도로,
      // 이 Agent가 만든 산출물들의 실측 Gate A-G/TCR 평균을 보여준다
      // (사용자 요청, 2026-09-28, "Phase1~4까지... 분석하고 개선해줘"
      // 리뷰 중 발견 — 이전까지 API에 있어도 화면 어디에도 안 보였다).
      const autoEval = a.auto_evaluation
        ? (a.auto_evaluation.avg_tcr === null ? "TCR 미측정" : "TCR " + a.auto_evaluation.avg_tcr.toFixed(0) + "%") +
          " (n=" + a.auto_evaluation.sample_count + ")"
        : "-";
      // 사용자 요청(2026-10-07, "현재 UI가 복수의 에이전트 그룹 생성 및
      // HAIR 관점의 연동에 적합한 경우는 아님... AI 에이전트 관리 노출
      // 검토") — 이 Agent가 어느 팀 소속인지 여기서 바로 보여준다.
      const teamNames = a.team_names && a.team_names.length
        ? '<button class="small" data-goto-teams="1" title="팀 관리로 이동">' + a.team_names.map(esc).join(", ") + '</button>'
        : '<span class="muted">-</span>';
      // 핵심 정보(식별·상태·동작)와 분리한 "모델 실행 후 쌓이는 정보"
      // 묶음 — 소속팀/비용/성과점수/자동평가/최근 산출물은 매번
      // 볼 필요는 없지만 완전히 없애면 안 되는 정보라 접어서 둔다.
      // LLM은 별도 열로 뺐으니 여기서는 중복 표시하지 않는다.
      const detail = '<details><summary>▶ 상세</summary>' +
        '<div style="text-align:left;white-space:normal">' +
        '<div>소속팀: ' + teamNames + '</div>' +
        '<div>비용: ' + a.cost_estimate + '</div>' +
        '<div>성과점수(수동): ' + (a.performance_score === null ? "-" : a.performance_score) + '</div>' +
        '<div>자동평가(Gate/TCR): ' + esc(autoEval) + '</div>' +
        '<div>최근 산출물:' + (a.last_output ? "" : " " + output) + '</div>' +
        (a.last_output ? output : "") +
        '</div></details>';
      return [esc(a.code || "-"), a.name ? esc(a.name) : '<span class="muted">(미지정)</span>', esc(a.role_name), esc(a.skill_tags.join(", ")),
        esc(label(a.status)), esc(label(a.framework)), llmInfo, detail, '<div class="actions">' + actions + "</div>"];
    })) + '<div id="execute-result"></div></section>';

  if (canExecute) {
    agents.forEach(a => wireProviderModelSelect("provider-" + a.id, "model-" + a.id, modelsByProvider, a.llm_model));
  }

  content.querySelectorAll("[data-goto-teams]").forEach(btn => {
    btn.onclick = () => selectTab("teams");
  });
  content.querySelectorAll("[data-onboard]").forEach(btn => {
    btn.onclick = async () => { try { await api("POST", "/api/agent-pool/" + btn.dataset.onboard + "/onboard"); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); } };
  });
  content.querySelectorAll("[data-deprecate]").forEach(btn => {
    // 실측으로 발견한 갭(사용자 요청, 2026-09-28, "UI/UX 내용 중... 분석
    // 하고 개선해줘") — 이 버튼만 확인 창이 없었다. 사람 쪽 "오프보딩"
    // (data-offboard)과 앱 안의 모든 삭제 버튼은 confirm을 거치는데,
    // 이 화면 안내문 자체가 "되돌릴 버튼이 없다"고 명시하는 되돌리기
    // 어려운 동작인데도 한 번의 클릭만으로 실행됐다.
    btn.onclick = async () => {
      if (!(await confirmModal("이 Agent를 오프보딩합니다(active→deprecated, 이 화면엔 되돌리는 버튼이 없습니다). 계속할까요?"))) return;
      try {
        await api("POST", "/api/agent-pool/" + btn.dataset.deprecate + "/deprecate");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-agent]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 AI 에이전트 레코드를 완전히 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/agent-pool/" + btn.dataset.deleteAgent);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-cost]").forEach(btn => {
    btn.onclick = async () => {
      const v = document.getElementById("cost-" + btn.dataset.setCost).value;
      try { await api("POST", "/api/agent-pool/" + btn.dataset.setCost + "/cost", { cost_estimate: Number(v) }); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-ascore]").forEach(btn => {
    btn.onclick = async () => {
      const v = document.getElementById("ascore-" + btn.dataset.setAscore).value;
      try { await api("POST", "/api/agent-pool/" + btn.dataset.setAscore + "/performance", { score: Number(v) }); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-name]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setName;
      const v = document.getElementById("name-" + id).value;
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/name", { name: v || null });
        showMsg("global-msg", v ? "이름을 저장했습니다." : "이름을 지웠습니다(역할명으로 표시됩니다).", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-tuning]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setTuning;
      const numOrNull = (elId) => {
        const v = document.getElementById(elId).value;
        return v === "" ? null : Number(v);
      };
      const triOrNull = (elId) => {
        const v = document.getElementById(elId).value;
        return v === "" ? null : v === "true";
      };
      const payload = {
        max_iter: numOrNull("tune-maxiter-" + id),
        max_rpm: numOrNull("tune-maxrpm-" + id),
        max_execution_time: numOrNull("tune-maxtime-" + id),
        max_retry_limit: numOrNull("tune-maxretry-" + id),
        temperature: numOrNull("tune-temp-" + id),
        reasoning: triOrNull("tune-reasoning-" + id),
        max_reasoning_attempts: numOrNull("tune-maxreasonattempts-" + id),
        allow_code_execution: triOrNull("tune-codeexec-" + id),
        respect_context_window: triOrNull("tune-respectctx-" + id),
      };
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/tuning", payload);
        showMsg("global-msg", "파인튜닝 설정을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-persona]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setPersona;
      const payload = {
        goal: document.getElementById("persona-goal-" + id).value,
        backstory: document.getElementById("persona-backstory-" + id).value,
        expected_output: document.getElementById("persona-expected-" + id).value,
      };
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/persona", payload);
        showMsg("global-msg", "역할/인격 설정을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-model]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.setModel;
      const provider = document.getElementById("provider-" + id).value;
      const model = modelSelectValue("model-" + id);
      const mcpRaw = document.getElementById("mcp-" + id).value.trim();
      const payload = { provider, model };
      if (mcpRaw) {
        try {
          payload.mcp_servers = JSON.parse(mcpRaw);
        } catch (e) {
          showMsg("global-msg", "MCP 서버 설정이 올바른 JSON이 아닙니다: " + e.message, "error");
          return;
        }
      }
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/model", payload);
        showMsg("global-msg", "모델 설정을 저장했습니다 — 다음 실행부터 적용됩니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-execute]").forEach(btn => {
    btn.onclick = async () => {
      const taskPrompt = window.prompt("이 에이전트에게 시킬 업무를 입력하세요(실제 LLM API 호출 — 비용 발생):");
      if (!taskPrompt) return;
      try {
        const r = await withBusy(btn, "실행 중…", () => api("POST", "/api/agent-pool/" + btn.dataset.execute + "/execute", { prompt: taskPrompt }));
        document.getElementById("execute-result").innerHTML =
          '<div class="msg ok"><b>실행 결과</b><br>' + renderContent(r.output) + "</div>";
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-index-knowledge]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 에이전트의 최근 산출물을 임베딩해 색인합니다(실제 임베딩 API 호출 — 비용 발생). 계속할까요?"))) return;
      try {
        await withBusy(btn, "보내는 중…", () => api("POST", "/api/agent-pool/" + btn.dataset.indexKnowledge + "/index-knowledge", {}));
        showMsg("global-msg", "지식베이스로 보냈습니다 — RAG 검색에서 바로 찾을 수 있습니다.", "ok");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-link-tool]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.linkTool;
      const toolId = document.getElementById("linktool-" + id).value;
      if (!toolId) { showMsg("global-msg", "연결할 도구를 선택하세요.", "error"); return; }
      try {
        await api("POST", "/api/agent-pool/" + id + "/tools/" + toolId);
        showMsg("global-msg", "도구를 연결했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-unlink-tool]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.unlinkTool;
      const toolId = document.getElementById("linktool-" + id).value;
      if (!toolId) { showMsg("global-msg", "해제할 도구를 선택하세요.", "error"); return; }
      try {
        await api("DELETE", "/api/agent-pool/" + id + "/tools/" + toolId);
        showMsg("global-msg", "도구 연결을 해제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- LLM 설정 최적화(일괄) ---------- */

async function renderAgentOptimization(content) {
  content.innerHTML = '<section class="card"><h2>LLM 설정 최적화 추천</h2><p class="muted">불러오는 중…</p></section>';
  let recs, platformLlm;
  let registry;
  try {
    [recs, platformLlm, registry] = await Promise.all([
      api("GET", "/api/agent-pool-optimization/recommendations"),
      api("GET", "/api/platform-llm-config"),
      api("GET", "/api/model-registry"),
    ]);
  } catch (e) {
    content.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }
  // 사용자 요청(2026-11-02, "LLM 설정에 HAIR-Platform 공통/기본 동작을
  // 위한 LLM을 지정할 수 있게") — `embedding-config` 폼(지식 베이스
  // 탭)과 같은 모양: 명시적으로 설정해둔 값이 있으면 그걸, 없으면 지금
  // 실제로 쓰이는 자동판별 결과를 보여준다. 설정하면 agent_optimizer.
  // refine_with_llm()·대화 "기본 AI"/요약처럼 Agent별 설정이 따로
  // 없는 공통 동작에서 환경변수보다 먼저 적용된다(Agent 실행 자체의
  // provider/model 해석은 그대로 — 범위 밖).
  //
  // 사용자 요청(2026-11-02, "모델 레지스트리에 등록된 모델에 한해
  // 선택할 수 있도록 개선") — 자유 텍스트 provider/모델명 입력을
  // "모델 레지스트리" 탭에 등록+활성화된 모델 중에서 고르는 셀렉트로
  // 바꾼다(백엔드 `llm.set_platform_default_llm()`도 같은 조건으로
  // 다시 검증한다 — 여기 목록에 없는 값은 선택 자체가 안 되지만,
  // API를 직접 호출해도 막힌다).
  const activeModels = registry.filter(m => m.is_active);
  const platformLlmFormHtml = activeModels.length
    ? '<form class="inline" id="platform-llm-config-form">' +
      '<select id="plc-model" required>' +
      activeModels.map(m => {
        const value = m.provider + "|" + m.name;
        const selected = (m.provider === platformLlm.provider && m.name === platformLlm.model) ? " selected" : "";
        return '<option value="' + escAttr(value) + '"' + selected + '>' + esc(m.provider) + " / " + esc(m.name) + '</option>';
      }).join("") +
      '</select>' +
      '<button type="submit" class="small primary">저장</button>' +
      '</form>'
    : '<p class="msg error">등록·활성화된 모델이 없습니다 — "모델 레지스트리" 탭에서 먼저 모델을 등록하세요.</p>';
  const platformLlmHtml =
    '<section class="card"><h2>플랫폼 공통 기본 LLM</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>Agent별 설정이 없는 공통 동작(LLM 설정 최적화 추천 보정, 대화창 "기본 AI"/요약 등)이 쓰는 기본값입니다 — 설정하면 환경변수보다 먼저 적용됩니다. 각 Agent 자체의 모델 설정과는 별개입니다.</li>' +
      '<li>"모델 레지스트리" 탭에 등록되고 활성화된 모델만 고를 수 있습니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (platformLlm.is_explicit ? '<b>설정됨</b>' : '<span class="muted">자동판별 중(아직 명시적으로 설정 안 함)</span>') +
    ' — provider: <b>' + esc(platformLlm.provider) + '</b>, 모델: <b>' + esc(platformLlm.model) + '</b></p>' +
    platformLlmFormHtml +
    "</section>";

  const changedCount = recs.filter(r => r.changed).length;
  content.innerHTML = platformLlmHtml + '<section class="card"><h2>LLM 설정 최적화 추천</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>모델 랭킹은 기본적으로 모델명 패턴 추정(실제 벤치마크 데이터 아님)이지만, "모델 레지스트리" 화면에서 모델별로 심층추론/정밀·신속/표준 tier를 직접 매핑해두면(★ 표시) 그 매핑이 패턴 추정보다 항상 우선합니다 — 새 모델 출시·provider 교체 시 여기 코드가 아니라 그 화면에서 매핑만 바꾸면 됩니다.</li>' +
    '<li>이 화면은 <b>일괄 적용</b> 화면입니다 — 체크한 Agent는 "선택 적용"을 누르면 미리보기 없이 바로 모델/파인튜닝 설정이 저장됩니다. 하나씩 검토하려면 "AI 에이전트 관리" 화면의 "⚡ 최적화 추천" 버튼을 쓰세요(그쪽은 입력란에 채우기만 하고 저장은 admin이 직접 합니다).</li>' +
    // 사용자 요청(2026-10-26, "llm 설정 최적화에는 에이전트를 추천
    // 설정된 에이전트도 재선택하고 다시 추천 모델을 적용할 수 있도록
    // UI/기능 개선") — 이전엔 `r.changed`(1위 추천이 이미 현재 설정과
    // 같음)인 Agent는 체크박스/모델 선택 자체가 없어서, "이미 적용함"
    // 또는 "이미 최적"인 Agent를 다시 선택해 다른 등록 모델로 바꾸거나
    // 재적용할 방법이 없었다. 체크박스·모델 선택은 이제 전체 Agent에
    // 항상 뜬다 — "변경 필요"는 그냥 참고 표시일 뿐, 체크 자체를
    // 막지 않는다.
    '<li>"변경 필요"가 아니어도(이미 추천과 같거나 한 번 적용한 Agent) 체크하고 "추천 Provider/Model"의 셀렉트에서 다른 등록 모델을 골라 다시 적용할 수 있습니다.</li>' +
    "</ul>"
    ) +
    '<button id="select-changed-optimize" class="small">변경 필요만 선택</button> ' +
    '<button id="apply-selected-optimize" class="small">선택 적용</button> ' +
    '<span class="muted">변경 필요 ' + changedCount + "/" + recs.length + "건</span>" +
    table(["적용", "역할", "분류", "현재 Provider/Model", "추천 Provider/Model", "tuning 변경 필드", "근거"], recs.map(r => {
      // 사용자 요청(2026-10-26, "모델 추천의 경우 모델 레지스트리에
      // 등록된 모델을 선택할 수 있도록 해줘") — 일괄 적용 화면에서도
      // 1위 추천 고정이 아니라, 레지스트리 활성 모델 후보 중 다른 걸
      // 골라 적용할 수 있게 셀렉트로 보여준다(`r.changed` 여부와
      // 무관하게 항상 — 바로 위 help-list 항목 참고).
      const candidates = r.recommended.candidates || [];
      const modelCell = candidates.length
        ? '<select class="optimize-model-pick" data-agent-id="' + r.agent_id +
          '" data-candidates="' + escAttr(JSON.stringify(candidates)) + '">' +
          candidates.map((c, i) => {
            const selected = (c.provider === r.recommended.provider && c.model === r.recommended.model) ? " selected" : "";
            const label = (c.tier_mapped ? "★ " : "") + c.provider + " / " + c.model;
            return '<option value="' + i + '"' + selected + '>' + esc(label) + '</option>';
          }).join("") +
          '</select>'
        : esc(r.recommended.provider || "(기본값)") + " / " + esc(r.recommended.model || "(기본값)");
      return [
        '<input type="checkbox" class="optimize-apply-check" data-agent-id="' + r.agent_id +
          '" data-changed="' + (r.changed ? "1" : "0") + '" data-payload="' + escAttr(JSON.stringify({
            provider: r.recommended.provider,
            model: r.recommended.model,
            tuning_config: r.recommended.tuning_config,
          })) + '">',
        esc(r.role_name),
        esc(_OPTIMIZE_TIER_LABELS[r.tier] || r.tier),
        esc(r.current.provider || "(기본값)") + " / " + esc(r.current.model || "(기본값)"),
        modelCell,
        Object.keys(r.tuning_diff).length ? esc(Object.keys(r.tuning_diff).join(", ")) : "(없음)",
        '<span class="muted">' + esc(r.rationale) + "</span>",
      ];
    })) +
    "</section>";

  const platformLlmForm = document.getElementById("platform-llm-config-form");
  if (platformLlmForm) platformLlmForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const [provider, model] = document.getElementById("plc-model").value.split("|");
    try {
      await api("PUT", "/api/platform-llm-config", { provider, model });
      showMsg("global-msg", "플랫폼 공통 기본 LLM을 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("select-changed-optimize").onclick = () => {
    Array.from(content.querySelectorAll(".optimize-apply-check")).forEach(chk => {
      chk.checked = chk.dataset.changed === "1";
    });
  };

  document.getElementById("apply-selected-optimize").onclick = async () => {
    const checks = Array.from(content.querySelectorAll(".optimize-apply-check:checked"));
    if (!checks.length) { showMsg("global-msg", "적용할 Agent를 선택하세요.", "error"); return; }
    if (!(await confirmModal(checks.length + "개 Agent에 추천 설정을 적용합니다. 계속할까요?"))) return;
    let ok = 0, fail = 0;
    for (const chk of checks) {
      const id = chk.dataset.agentId;
      const payload = JSON.parse(chk.dataset.payload);
      const pickSel = content.querySelector('.optimize-model-pick[data-agent-id="' + id + '"]');
      if (pickSel) {
        const cands = JSON.parse(pickSel.dataset.candidates || "[]");
        const chosen = cands[Number(pickSel.value)];
        if (chosen) {
          payload.provider = chosen.provider;
          payload.model = chosen.model;
        }
      }
      try {
        if (payload.provider) {
          await api("PATCH", "/api/agent-pool/" + id + "/model", { provider: payload.provider, model: payload.model });
        }
        await api("PATCH", "/api/agent-pool/" + id + "/tuning", Object.assign({
          max_iter: null, max_rpm: null, max_execution_time: null, max_retry_limit: null,
          temperature: null, reasoning: null, max_reasoning_attempts: null,
          allow_code_execution: null, respect_context_window: null,
        }, payload.tuning_config || {}));
        ok++;
      } catch (e) { fail++; }
    }
    showMsg("global-msg", ok + "개 적용 완료" + (fail ? ", " + fail + "개 실패" : "") + ".", fail ? "error" : "ok");
    renderAgentOptimization(content);
  };
}

/* ---------- 도구/스킬 Pool ---------- */

let _poolCategoryFilter = "all";

// 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로 구분하고
// 필터링 되도록 개선") — 분류 값을 한글 배지로 통일해 표시한다(대화창
// 쪽 라벨과 같은 문구를 써서 두 화면의 용어가 어긋나지 않게 한다).
const POOL_CATEGORY_LABEL = { sw_dev: "개발업무", general: "일반업무" };

async function renderPoolRegistry(content) {
  const [allTools, allSkills] = await Promise.all([
    api("GET", "/api/tool-pool"),
    api("GET", "/api/skill-pool"),
  ]);
  const tools = _poolCategoryFilter === "all"
    ? allTools
    : allTools.filter(t => t.category === _poolCategoryFilter);
  const skills = _poolCategoryFilter === "all"
    ? allSkills
    : allSkills.filter(s => s.category === _poolCategoryFilter);
  const isAdmin = ME.role === "admin";
  // 사용자 요청(2026-10-27, "기본 도구 ID의 경우도 코드로 선택 가능하도록
  // 수정 필요(ID 노출 안됨)") — 테이블 표시·폼 선택 모두 정수 id 대신
  // tool_pool.code를 쓴다. id는 체크박스 value(실제 API 페이로드)로만
  // 남고 화면에는 안 보인다.
  const toolCodeById = {};
  // 분류 필터가 걸려 있어도 코드 조회·도구 선택 체크박스는 전체
  // 목록(allTools) 기준이어야 한다 — 필터는 "목록에 보일 행"만 좁히는
  // 용도이고, 다른 분류의 도구를 스킬에 연결하는 것 자체는 막지 않는다.
  allTools.forEach(t => { toolCodeById[t.id] = t.code || ("#" + t.id); });
  // 사용자 요청(2026-10-27, "신규 도구, 스킬 생성의 경우 AI 에이전트
  // 생성 화면처럼 박스를 분리해줘") — 지금까지는 목록 카드 맨 아래에
  // 생성 폼을 그대로 이어붙였다. "AI 에이전트 생성" 화면(renderJobs)이
  // "AI 에이전트 생성"/"Agent 직접 생성"을 각각 독립된 카드로 분리한
  // 것과 같은 원칙으로, 목록 카드와 생성 카드를 분리하고(Tool Pool
  // 목록 / 신규 도구 생성 / Skill Pool 목록 / 신규 스킬 생성 — 총 4개
  // 카드) 생성 폼도 그 화면의 "Agent 직접 생성"과 같은 "세로로 쌓인
  // 넓은 입력칸" 구조로 맞춘다(기존 한 줄 inline 입력은 필드 수가
  // 늘면서 이미 비좁았다).
  content.innerHTML =
    // 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로
    // 구분하고 필터링 되도록 개선") — Tool/Skill 두 표 공용 필터
    // 하나(목록만 좁힌다 — 아래 도구 선택 체크박스들은 분류와 무관하게
    // 항상 전체를 보여준다).
    '<section class="card" style="padding-bottom:0.6rem">' +
    '<label>분류 필터 <select id="pool-category-filter" style="margin-left:0.4rem">' +
    '<option value="all"' + (_poolCategoryFilter === "all" ? " selected" : "") + '>전체</option>' +
    '<option value="general"' + (_poolCategoryFilter === "general" ? " selected" : "") + '>일반업무</option>' +
    '<option value="sw_dev"' + (_poolCategoryFilter === "sw_dev" ? " selected" : "") + '>개발업무</option>' +
    '</select></label>' +
    '</section>' +
    '<section class="card"><h2>Tool Pool — 재사용 가능한 도구</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>MCP 서버나 native 도구(VectorDB/GraphDB/실시간 웹 검색)를 이름으로 등록해 여러 Agent가 재사용합니다.</li>' +
    '<li>native는 config에 {"native_id": "vectordb_search"} 또는 {"native_id": "graphdb_query"} 또는 {"native_id": "tavily_search"}(Tavily 실시간 웹 검색, API 키 필요(TAVILY_API_KEY) — skill_tags에 "실시간웹검색"을 넣으면 매칭됩니다)를 지원합니다.</li>' +
    '<li>mcp_stdio/mcp_remote는 {"command":...,"args":[...]} 또는 {"url":...,"transport":...} 형태입니다 — 이 두 종류는 Agent가 실행될 때마다 실제로 서버(로컬 프로세스 또는 원격)에 접속을 시도하므로, 잘못된 command/url을 등록해두면 그 스킬을 가진 Agent 실행이 접속 단계에서 실패합니다.</li>' +
    // 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능을
    // 제공해줘") — mcp_stdio와 같은 신뢰 등급(admin 전용)이라는 걸 화면에도
    // 명시한다. 실행 중 Docker가 있으면 격리 실행, 없으면 일반
    // subprocess로 폴백한다는 한계도 숨기지 않는다.
    '<li><b>custom_python</b>(admin 전용)은 Python 함수를 직접 등록해 새 CrewAI Tool로 쓴다 — <code>def tool_fn(query: str) -> str:</code> 하나만 정의하면 됩니다. 등록된 코드는 그 도구가 호출될 때마다 서버에서 실제로 실행됩니다(Docker가 설치돼 있으면 네트워크 차단된 컨테이너에서, 없으면 격리 없는 일반 프로세스로 폴백) — mcp_stdio와 같은 신뢰 수준이 필요해 admin 전용입니다.</li>' +
    '<li>"활성" 표시를 꺼두면(비활성) 스킬이 매칭돼도 더 이상 자동으로 붙지 않습니다(등록은 유지).</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 도구를 참조하던 Agent 개별 연결도 함께 지웁니다.</li>' +
    '</ul>'
    ) +
    table(["코드", "이름", "종류", "분류", "설명", "활성", "동작"], tools.map(t => [
      esc(t.code || "-"), esc(t.name), esc(t.kind), esc(POOL_CATEGORY_LABEL[t.category] || t.category), esc(t.description), t.is_active ? "활성" : "비활성",
      (isAdmin || !["mcp_stdio", "mcp_remote", "custom_python"].includes(t.kind))
        ? '<div class="actions"><button class="small" data-edit-tool="' + t.id + '">수정</button>' +
          (isAdmin ? '<button class="small danger" data-delete-tool="' + t.id + '">삭제</button>' : "") +
          "</div>"
        : "",
    ])) +
    "</section>" +
    '<section class="card"><h2>신규 도구 생성</h2>' +
    // 사용자 보고(2026-10-27, 스크린샷 — 입력창·선택 옵션이 한쪽에
    // 치우치고 박스 전체 폭을 못 씀) — 원인은 `display:flex;flex-
    // direction:column`에 `max-width:560px`를 준 것: 플렉스 column의
    // 기본 align-items:stretch는 <input>/<select>처럼 교체 요소(고유
    // 크기가 있는 폼 컨트롤)에는 안 먹혀 좁은 기본 폭 그대로 남는다.
    // "Agent 직접 생성" 카드(`direct-agent-form`)가 쓰는 pattern —
    // 그냥 block 폼 + 각 필드에 `width:100%`를 직접 주는 방식 — 으로
    // 맞춘다.
    '<form id="tool-form">' +
    '<label style="display:block">이름<br><input id="tp-name" required style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<label style="display:block;margin-top:0.5rem">종류<br><select id="tp-kind" style="width:100%;box-sizing:border-box;margin-top:0.2rem">' +
    '<option value="native">native</option><option value="mcp_remote">mcp_remote</option>' +
    '<option value="mcp_stdio">mcp_stdio</option><option value="custom_python">custom_python</option>' +
    '</select></label>' +
    '<label style="display:block;margin-top:0.5rem">설명<br><textarea id="tp-description" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">분류<br>' +
    '<select id="tp-category" style="width:100%;box-sizing:border-box;margin-top:0.2rem"><option value="general">일반업무</option><option value="sw_dev">개발업무</option></select>' +
    '</label>' +
    '<label id="tp-config-wrap" style="display:block;margin-top:0.5rem">config JSON<br><input id="tp-config" placeholder="예: {&#39;native_id&#39;:&#39;vectordb_search&#39;}" style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<div id="tp-custom-python-fields" style="display:none">' +
    '<label style="display:block;margin-top:0.5rem">Python 함수<br><textarea id="tp-custom-source" placeholder="def tool_fn(query: str) -> str:&#10;    return &#34;...&#34;" rows="18" style="width:100%;box-sizing:border-box;font-family:monospace;resize:vertical;min-height:320px;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">타임아웃(초) <input type="number" id="tp-custom-timeout" value="10" min="1" max="60" style="width:4rem"></label>' +
    '</div>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">도구 등록</button></form>' +
    "</section>" +
    '<section class="card"><h2>Skill Pool — skill_tags 정식 등록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>모드 "tool"인 스킬은 기본 도구 ID가 가리키는 도구를 이 스킬(이름이 정확히 일치)을 가진 모든 Agent에게 실행할 때마다 자동으로 붙입니다(run_crew/crewai_agent_generator 실행마다 다시 계산 — 나중에 스킬 설정을 바꾸면 이미 만들어진 Agent에도 다음 실행부터 바로 반영됩니다).</li>' +
    '<li>모드 "none"은 도구 연결 없이 이름표만 붙이는 용도입니다 — 하니스 방법론/agent-evaluator 절차 스킬 17개가 여기 해당합니다(사람 판단이 필요한 절차라 자동 도구화가 안 됨).</li>' +
    '<li>"recommend-fix-workflow" 하나만 예외로 실제 MCP 도구(정적 Gate 조치 지식 조회)가 붙어 있습니다.</li>' +
    '<li><b>대화창 표시</b>를 꺼두면 대화창의 스킬 선택 드롭다운에는 안 보이지만(선택해서 고를 수 없음), Agent의 skill_tags로는 그대로 쓸 수 있습니다 — mcp_remote/mcp_stdio/custom_python 도구가 붙은 스킬은 대화창 기본 AI(단발 호출, 실행 루프 없음)에서는 실제로 호출이 안 돼 기본값이 꺼짐으로 돼 있습니다(실제 CrewAI Agent 실행에서는 똑같이 동작합니다).</li>' +
    '<li><b>분류</b>(사용자 요청, 2026-11-11, "SW개발업무와 일반업무로 구분, 개발업무는 Default로 대화창에 안 보이고 필요시 노출") — "개발업무"로 분류된 스킬/도구는 대화창 드롭다운에 default로 안 보이지만 완전히 숨겨진 건 아닙니다 — 대화창의 "개발업무 스킬도 보기" 체크박스를 켠 사용자에게는 보입니다("대화창 표시"가 꺼진 스킬과는 다른 축으로, 둘 다 적용됩니다 — show_in_conversation이 꺼져 있으면 체크박스를 켜도 안 보입니다). Agent/Task의 skill_tags 자동완성에는 분류와 무관하게 항상 둘 다 뜹니다(그 경로는 CrewAI 실행이라 개발업무 스킬도 실제로 동작하기 때문). 위쪽 "분류 필터"로 목록을 좁혀볼 수 있고, 수정 화면에서 바로 바꿀 수 있습니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 스킬 레코드만 지웁니다 — 같은 이름의 skill_tags를 가진 Agent/사람은 그대로 남고, 다음 실행부터 자동 도구 연결만 안 됩니다.</li>' +
    '</ul>'
    ) +
    table(["코드", "이름", "설명", "기본 도구", "모드", "분류", "대화창 표시", "동작"], skills.map(s => [
      esc(s.code || "-"), esc(s.name), esc(s.description),
      esc(s.default_tool_pool_ids.map(id => toolCodeById[id] || ("#" + id)).join(", ") || "-"),
      esc(s.augmentation_mode),
      esc(POOL_CATEGORY_LABEL[s.category] || s.category),
      // 사용자 보고(2026-11-11, 스크린샷 — "대화창 표시"가 "표시"인데도
      // 실제 대화창에는 안 보임) — `show_in_conversation`과 `category`는
      // 독립된 두 조건이고, 대화창 API는 show_in_conversation=1이 아니면
      // 무조건 빠지고, category='sw_dev'면 "개발업무 스킬도 보기"
      // 체크박스를 켠 사용자에게만 보인다(완전히 숨기는 게 아니라
      // default-off). 이 칸은 지금까지 show_in_conversation 하나만
      // 보여줘 "표시"인데 실제로는 (기본값으로는) 안 뜨는 모순된
      // 화면이었다 — 두 조건을 합친 실제 결과를 보여주도록 고친다.
      !s.show_in_conversation
        ? "숨김"
        : (s.category === "sw_dev" ? "기본 숨김(개발업무)" : "표시"),
      '<div class="actions"><button class="small" data-edit-skill="' + s.id + '">수정</button>' +
        (isAdmin ? '<button class="small danger" data-delete-skill="' + s.id + '">삭제</button>' : "") +
        "</div>",
    ])) +
    "</section>" +
    '<section class="card"><h2>신규 스킬 생성</h2>' +
    '<form id="skill-form">' +
    '<label style="display:block">스킬명(skill_tags와 일치)<br><input id="sp-name" required style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
    '<label style="display:block;margin-top:0.5rem">설명<br><textarea id="sp-description" rows="3" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem"></textarea></label>' +
    '<label style="display:block;margin-top:0.5rem">모드<br>' +
    '<select id="sp-mode" style="margin-top:0.2rem"><option value="tool">tool(자동 호출)</option><option value="always">always</option><option value="none">none</option></select>' +
    '</label>' +
    '<label style="display:block;margin-top:0.5rem">분류<br>' +
    '<select id="sp-category" style="margin-top:0.2rem"><option value="general">일반업무(대화창에 기본 표시)</option><option value="sw_dev">개발업무(대화창에 기본 숨김)</option></select>' +
    '</label>' +
    '<div id="sp-tool-picker" style="margin-top:0.5rem">' +
    '<div class="muted" style="margin-bottom:0.2rem">기본 도구(코드로 선택, 복수 가능 — 모드가 tool/always일 때만)</div>' +
    allTools.map(t =>
      '<label style="margin-right:0.8rem;display:inline-block"><input type="checkbox" class="sp-tool-chk" value="' + t.id + '"> ' + esc(t.code || ("#" + t.id)) + '</label>'
    ).join("") +
    '</div>' +
    '<label style="display:block;margin-top:0.5rem"><input type="checkbox" id="sp-show-in-conv" checked> 대화창에 표시</label>' +
    '<button class="primary" type="submit" style="margin-top:0.6rem">스킬 등록</button></form>' +
    "</section>";

  document.getElementById("pool-category-filter").addEventListener("change", (ev) => {
    _poolCategoryFilter = ev.target.value;
    renderPoolRegistry(content);
  });
  const tpKindSelect = document.getElementById("tp-kind");
  const tpConfigInput = document.getElementById("tp-config");
  const tpConfigWrap = document.getElementById("tp-config-wrap");
  const tpCustomFields = document.getElementById("tp-custom-python-fields");
  const syncToolKindFields = () => {
    const isCustom = tpKindSelect.value === "custom_python";
    tpCustomFields.style.display = isCustom ? "block" : "none";
    tpConfigWrap.style.display = isCustom ? "none" : "";
  };
  tpKindSelect.addEventListener("change", syncToolKindFields);
  syncToolKindFields();

  document.getElementById("tool-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const kind = tpKindSelect.value;
    let config = {};
    if (kind === "custom_python") {
      // 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는
      // 기능") — 여긴 JSON이 아니라 Python 소스를 그대로 담는다.
      config = {
        function_source: document.getElementById("tp-custom-source").value,
        timeout_seconds: Number(document.getElementById("tp-custom-timeout").value) || 10,
      };
    } else {
      const raw = tpConfigInput.value.trim();
      if (raw) {
        try { config = JSON.parse(raw); } catch (e) {
          showMsg("global-msg", "config가 올바른 JSON이 아닙니다: " + e.message, "error"); return;
        }
      }
    }
    try {
      await api("POST", "/api/tool-pool", {
        name: document.getElementById("tp-name").value,
        kind,
        description: document.getElementById("tp-description").value,
        config,
        category: document.getElementById("tp-category").value,
      });
      showMsg("global-msg", "도구를 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const spModeSelect = document.getElementById("sp-mode");
  const spToolPicker = document.getElementById("sp-tool-picker");
  const syncSkillToolPicker = () => {
    const enabled = spModeSelect.value === "tool" || spModeSelect.value === "always";
    spToolPicker.style.opacity = enabled ? "1" : "0.45";
    spToolPicker.querySelectorAll(".sp-tool-chk").forEach(chk => {
      chk.disabled = !enabled;
      if (!enabled) chk.checked = false;
    });
  };
  spModeSelect.addEventListener("change", syncSkillToolPicker);
  syncSkillToolPicker();

  document.getElementById("skill-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const default_tool_pool_ids = Array.from(spToolPicker.querySelectorAll(".sp-tool-chk:checked"))
      .map(chk => Number(chk.value));
    try {
      await api("POST", "/api/skill-pool", {
        name: document.getElementById("sp-name").value,
        description: document.getElementById("sp-description").value,
        default_tool_pool_ids,
        augmentation_mode: spModeSelect.value,
        show_in_conversation: document.getElementById("sp-show-in-conv").checked,
        category: document.getElementById("sp-category").value,
      });
      showMsg("global-msg", "스킬을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을 추가해줘",
  // 이어서 "(1) 수정 버튼 클릭시 수정 대상의 하단이 아니라 목록의
  // 하단에 편집 창이 생성되어 확인하기 어려움 (2) 코드 입력창의 높이가
  // 너무 낮음 (3) 스킬도 도구 수정 UI처럼... 너무 협소 (4) 에이전트
  // 연결에 영향을 주는 이름은 수정 대상에서 제외") — 네 가지 다 반영:
  // ① 패널을 테이블 하단 고정 자리가 아니라 그 행(<tr data-edit-tool="
  // 클릭한 버튼의 closest tr>") 바로 아래에 전체 폭(colspan)으로 끼워
  // 넣는다. ② Python 소스 textarea를 세로로 훨씬 키우고 resize도 허용.
  // ③ 스킬 수정 폼도 도구 수정과 같은 "세로로 쌓인 넓은 입력칸" 구조로
  // 바꾼다(기존엔 등록 폼과 똑같이 한 줄에 욱여넣은 inline 입력이었다).
  // ④ name은 두 쪽 다 입력칸이 아니라 읽기전용 텍스트로만 보여준다
  // (서버도 PATCH에서 `name`을 거부한다 — `pool_registry.update_tool()`/
  // `update_skill()` 참고, Skill 매칭·Agent 연결이 이름 문자열을 그대로
  // 참조해 바꾸면 그 연결이 조용히 끊어지기 때문).
  let openToolEditRow = null;
  content.querySelectorAll("[data-edit-tool]").forEach(btn => {
    btn.onclick = () => {
      const t = tools.find(x => x.id === Number(btn.dataset.editTool));
      if (openToolEditRow) { openToolEditRow.remove(); openToolEditRow = null; }
      const isCustom = t.kind === "custom_python";
      const formHtml =
        '<h4 style="margin-top:0">도구 #' + t.id + ' 수정</h4>' +
        '<p><b>' + esc(t.name) + '</b> <span class="muted">(종류: ' + esc(t.kind) + ')</span> ' +
        '<span class="muted">— 이름·종류는 변경 불가(이름은 Skill 매칭·Agent 연결이 참조해 바꾸면 그 연결이 끊어집니다. 종류를 바꾸려면 삭제 후 새로 등록하세요).</span></p>' +
        '<label style="display:block;margin-top:0.4rem">설명<br>' +
        '<input id="tpe-description" value="' + escAttr(t.description) + '" style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>' +
        '<label style="display:block;margin-top:0.4rem">분류<br>' +
        '<select id="tpe-category" style="margin-top:0.2rem">' +
        '<option value="general"' + (t.category === "general" ? " selected" : "") + '>일반업무</option>' +
        '<option value="sw_dev"' + (t.category === "sw_dev" ? " selected" : "") + '>개발업무</option>' +
        '</select></label>' +
        (isCustom
          ? '<label style="display:block;margin-top:0.6rem">Python 함수<br>' +
            '<textarea id="tpe-custom-source" rows="24" style="width:100%;box-sizing:border-box;font-family:monospace;resize:vertical;min-height:420px;margin-top:0.2rem">' + esc(t.config.function_source || "") + '</textarea></label>' +
            '<label>타임아웃(초) <input type="number" id="tpe-custom-timeout" value="' + (t.config.timeout_seconds || 10) + '" min="1" max="60" style="width:4rem"></label>'
          : '<label style="display:block;margin-top:0.6rem">config JSON<br>' +
            '<input id="tpe-config" value="' + escAttr(JSON.stringify(t.config)) + '" style="width:100%;box-sizing:border-box;margin-top:0.2rem"></label>') +
        '<div class="actions" style="margin-top:0.6rem"><button class="primary" type="submit">저장</button>' +
        '<button class="small" type="button" id="tool-edit-cancel">취소</button></div>';
      btn.closest("tr").insertAdjacentHTML(
        "afterend",
        '<tr><td colspan="7"><form id="tool-edit-form" style="padding:0.6rem 0">' + formHtml + "</form></td></tr>"
      );
      const tr = btn.closest("tr").nextElementSibling;
      openToolEditRow = tr;
      tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
      tr.querySelector("#tool-edit-cancel").onclick = () => { tr.remove(); openToolEditRow = null; };
      tr.querySelector("#tool-edit-form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        let config;
        if (isCustom) {
          config = {
            function_source: tr.querySelector("#tpe-custom-source").value,
            timeout_seconds: Number(tr.querySelector("#tpe-custom-timeout").value) || 10,
          };
        } else {
          const raw = tr.querySelector("#tpe-config").value.trim();
          try { config = raw ? JSON.parse(raw) : {}; } catch (e) {
            showMsg("global-msg", "config가 올바른 JSON이 아닙니다: " + e.message, "error"); return;
          }
        }
        try {
          await api("PATCH", "/api/tool-pool/" + t.id, {
            description: tr.querySelector("#tpe-description").value,
            config,
            category: tr.querySelector("#tpe-category").value,
          });
          showMsg("global-msg", "도구를 수정했습니다.", "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    };
  });
  let openSkillEditRow = null;
  content.querySelectorAll("[data-edit-skill]").forEach(btn => {
    btn.onclick = () => {
      const s = skills.find(x => x.id === Number(btn.dataset.editSkill));
      if (openSkillEditRow) { openSkillEditRow.remove(); openSkillEditRow = null; }
      // 사용자 요청(2026-10-27, "skill 생성시 ID에 tool/always/none이
      // 포함됨, 수정에서 모드 변경이 ID가 바뀌는 게 아니라면 막아야 함
      // + 기본 도구 ID도 코드로 선택(ID 노출 안됨), 기존 모드가 tool/
      // always인 경우에만 활성화") — 모드는 name/kind와 같이 읽기전용
      // 텍스트로만 보여주고(서버도 더는 받지 않는다, pool_registry.
      // update_skill() 참고) 아예 PATCH에 안 보낸다. 도구는 체크박스로
      // 코드만 보여주고(값은 id), 기존 모드가 tool/always일 때만 활성화.
      const modeAllowsTools = s.augmentation_mode === "tool" || s.augmentation_mode === "always";
      const checkedToolIds = new Set(s.default_tool_pool_ids);
      const formHtml =
        '<h4 style="margin-top:0">스킬 #' + s.id + ' 수정</h4>' +
        '<p><b>' + esc(s.name) + '</b> <span class="muted">— 이름은 변경 불가(Agent의 skill_tags와 정확히 일치해야 매칭되므로, 바꾸면 그 연결이 끊어집니다).</span></p>' +
        '<p class="muted">모드: <b>' + esc(s.augmentation_mode) + '</b>(수정 불가 — 코드(' + esc(s.code || "-") + ')에 모드가 세그먼트로 박혀 있어, 모드를 바꾸려면 삭제 후 새로 등록해야 합니다).</p>' +
        '<label style="display:block;margin-top:0.4rem">설명<br>' +
        '<textarea id="spe-description" rows="3" style="width:100%;box-sizing:border-box;resize:vertical;margin-top:0.2rem">' + esc(s.description) + '</textarea></label>' +
        '<label style="display:block;margin-top:0.4rem">분류<br>' +
        '<select id="spe-category" style="margin-top:0.2rem">' +
        '<option value="general"' + (s.category === "general" ? " selected" : "") + '>일반업무(대화창에 기본 표시)</option>' +
        '<option value="sw_dev"' + (s.category === "sw_dev" ? " selected" : "") + '>개발업무(대화창에 기본 숨김)</option>' +
        '</select></label>' +
        '<div style="margin-top:0.6rem' + (modeAllowsTools ? "" : ";opacity:0.45") + '">기본 도구(코드로 선택, 복수 가능' + (modeAllowsTools ? "" : " — 이 스킬은 모드가 none이라 비활성") + ')<br>' +
        allTools.map(t =>
          '<label style="margin-right:0.8rem;display:inline-block"><input type="checkbox" class="spe-tool-chk" value="' + t.id + '"' +
          (checkedToolIds.has(t.id) ? " checked" : "") + (modeAllowsTools ? "" : " disabled") + '> ' + esc(t.code || ("#" + t.id)) + '</label>'
        ).join("") +
        '</div>' +
        '<label style="display:block;margin-top:0.6rem"><input type="checkbox" id="spe-show-in-conv"' + (s.show_in_conversation ? " checked" : "") + '> 대화창에 표시</label>' +
        (s.category === "sw_dev"
          ? '<p class="muted" style="margin-top:0.2rem">⚠ 분류가 "개발업무"라 이 체크박스를 켜도 대화창 드롭다운에는 default로 안 뜹니다 — 대화창의 "개발업무 스킬도 보기" 체크박스를 켠 사용자에게만 보입니다.</p>'
          : "") +
        '<div class="actions" style="margin-top:0.6rem"><button class="primary" type="submit">저장</button>' +
        '<button class="small" type="button" id="skill-edit-cancel">취소</button></div>';
      btn.closest("tr").insertAdjacentHTML(
        "afterend",
        '<tr><td colspan="8"><form id="skill-edit-form" style="padding:0.6rem 0">' + formHtml + "</form></td></tr>"
      );
      const tr = btn.closest("tr").nextElementSibling;
      openSkillEditRow = tr;
      tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
      tr.querySelector("#skill-edit-cancel").onclick = () => { tr.remove(); openSkillEditRow = null; };
      tr.querySelector("#skill-edit-form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const default_tool_pool_ids = Array.from(tr.querySelectorAll(".spe-tool-chk:checked"))
          .map(chk => Number(chk.value));
        try {
          await api("PATCH", "/api/skill-pool/" + s.id, {
            description: tr.querySelector("#spe-description").value,
            default_tool_pool_ids,
            show_in_conversation: tr.querySelector("#spe-show-in-conv").checked,
            category: tr.querySelector("#spe-category").value,
          });
          showMsg("global-msg", "스킬을 수정했습니다.", "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    };
  });
  content.querySelectorAll("[data-delete-tool]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 도구를 완전히 지웁니다(연결된 Agent 연결도 함께 지워집니다, 되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/tool-pool/" + btn.dataset.deleteTool);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-skill]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 스킬을 완전히 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/skill-pool/" + btn.dataset.deleteSkill);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 팀(사용자 요청, 2026-10-06, "복수의 사람이 복수의 Agent와
   또는 CrewAI 장점인 역할에 따른 Agent를 묶어 생성된 그룹과 협업하도록...
   다시 기획"). Phase 1+2 —
   human_pool/agent_pool 멤버를 역할명과 함께 묶어 등록하는 "등록→재사용"
   화면(tool_pool/skill_pool/model_registry와 같은 패턴). 팀 자체를 할 일에
   배정하는 동작("배정 요청")은 이 탭이 아니라 "업무 요청" 탭의 할 일 상세
   패널에서 한다(팀 정의와 배정은 관심사가 다르다 — teams.py 참고). */

async function renderTeams(content) {
  const [teams, people, agents, pipelineTemplates] = await Promise.all([
    api("GET", "/api/teams"),
    api("GET", "/api/human-pool"),
    api("GET", "/api/agent-pool"),
    api("GET", "/api/pipeline-templates").catch(() => []),
  ]);
  const isAdmin = ME.role === "admin";
  const memberOptionsHtml =
    '<option value="">멤버 선택</option>' +
    '<optgroup label="사람">' +
    people.map(p => '<option value="human:' + p.id + '">' + esc(p.name) + '(' + esc(p.role) + ')</option>').join("") +
    '</optgroup>' +
    '<optgroup label="Agent">' +
    agents.map(a => '<option value="agent:' + a.id + '">' + agentDisplayLabel(a) + '(#' + a.id + ')</option>').join("") +
    '</optgroup>';

  // 사용자 요청(2026-10-20, "고급 에이전트 관리") — 지금까지 "팀 Agent
  // 실행"을 누를 때마다 process·매니저를 매번 새로 골라야 했다. 매니저
  // 후보는 이 팀의 온보딩(active)된 Agent 멤버로만 좁힌다(서버도
  // `teams.update_team_defaults()`에서 같은 제약을 검증한다).
  // 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 재정렬
  // API(`PATCH .../members/order`)는 `team_members.id`(행 id)를 받지
  // `agent_pool.id`를 받지 않는다 — 캔버스가 둘 다 들고 있어야 해서
  // `agent_pool` 필드에 `teamMemberRowId`를 덧붙여 돌려준다.
  const activeAgentMembersOf = (t) => t.members
    .filter(m => m.member_type === "agent")
    .map(m => {
      const a = agents.find(x => x.id === m.member_id);
      return a && a.status === "active"
        ? Object.assign({}, a, { teamMemberRowId: m.id })
        : null;
    })
    .filter(Boolean);

  const teamDefaultsFormHtml = (t) => {
    const activeAgentMembers = activeAgentMembersOf(t);
    return '<form class="inline" data-team-defaults-form="' + t.id + '" style="margin-top:0.5rem;flex-wrap:wrap">' +
      '<label>기본 process ' +
      '<select data-default-process>' +
      '<option value="">(미지정)</option>' +
      '<option value="sequential"' + (t.default_process === "sequential" ? " selected" : "") + '>sequential</option>' +
      '<option value="hierarchical"' + (t.default_process === "hierarchical" ? " selected" : "") + '>hierarchical</option>' +
      '</select></label>' +
      '<label>기본 매니저 ' +
      '<select data-default-manager' + (activeAgentMembers.length ? "" : " disabled") + '>' +
      '<option value="">(미지정 — 자동 추정)</option>' +
      activeAgentMembers.map(a => '<option value="' + a.id + '"' +
        (t.default_manager_agent_id === a.id ? " selected" : "") + '>' + agentDisplayLabel(a) + " (#" + a.id + ")</option>").join("") +
      '</select></label>' +
      '<label>연결 파이프라인 ' +
      '<select data-default-pipeline>' +
      '<option value="">(미지정 — @멘션 시 단발 실행)</option>' +
      pipelineTemplates.map(p => '<option value="' + p.id + '"' +
        (t.pipeline_template_id === p.id ? " selected" : "") + '>' + esc(p.name) + '</option>').join("") +
      '</select></label>' +
      '<button class="small" type="submit">기본값 저장</button></form>';
  };

  // 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 기존
  // 멤버 표(CRUD)는 전혀 안 건드리고, 그 위에 "구조 보기/순서·매니저
  // 편집" 캔버스를 추가로 얹는다. Human 멤버는 CrewAI 실행 그래프에
  // 포함되지 않으므로(`active_agent_member_ids()`) 캔버스 노드가
  // 아니라 별도 띠로만 보여준다(거짓 없는 시각화).
  const teamCardHtml = (t) => {
    const activeAgentMembers = activeAgentMembersOf(t);
    const humanMembers = t.members.filter(m => m.member_type === "human");
    const humanStripHtml = humanMembers.length
      ? '<p class="muted">결재/참여 인력: ' +
        humanMembers.map(m => esc(m.member_name || ("#" + m.member_id)) +
          (m.role_label ? "(" + esc(m.role_label) + ")" : "")).join(", ") +
        '</p>'
      : "";
    // 사용자 보고(2026-10-21, "노드 크기 변경 기능... 지금 노드 크기
    // 조절은 안되고 너무 커 모든 노드가 표시되지 않음") — 축소/확대/
    // 전체 보기 버튼을 캔버스 바깥(Drawflow가 관리하는 DOM 트리
    // 밖)에 둬서 Drawflow의 pan/zoom 변환에 영향받지 않게 한다.
    const canvasHtml = activeAgentMembers.length
      ? '<div class="team-canvas-wrap" style="margin-bottom:0.6rem">' +
        '<div class="team-canvas-toolbar" style="margin-bottom:0.3rem">' +
        '<button type="button" class="small" data-canvas-zoom-out="' + t.id + '">축소 −</button> ' +
        '<button type="button" class="small" data-canvas-zoom-in="' + t.id + '">확대 +</button> ' +
        '<button type="button" class="small" data-canvas-zoom-fit="' + t.id + '">전체 보기</button>' +
        '</div>' +
        '<div class="team-canvas" id="team-canvas-' + t.id + '" style="height:260px;border:1px solid var(--color-border);border-radius:6px;background:#fafbfc;overflow:hidden"></div>' +
        '<p class="muted" style="margin:0.3rem 0 0">노드를 좌우로 드래그하면 순차 릴레이 순서가 바뀝니다(hierarchical에서는 순서가 의미 없어 드래그가 반영되지 않습니다). 노드의 "매니저로 지정" 버튼으로 매니저를 바꿀 수 있습니다. Ctrl+스크롤(모바일은 두 손가락)로도 확대/축소할 수 있습니다.</p>' +
        '</div>'
      : '<p class="muted">온보딩(active)된 Agent 멤버가 있어야 워크플로우를 미리 볼 수 있습니다.</p>';
    // 사용자 요청(2026-10-29, "Team 온보딩 라이프사이클") — 상태 배지는
    // agent_pool에서 쓰던 라벨을 그대로 재사용한다(LABELS에 이미 있음).
    const statusBadgeClass = t.status === "active" ? "" : " gate-badge gate-warn";
    const linkedTaskHtml = (t.task_id || t.brief_id)
      ? '<p class="muted">연결된 업무: ' +
        (t.task_id ? "Task #" + t.task_id : "") +
        (t.task_id && t.brief_id ? ", " : "") +
        (t.brief_id ? "Brief #" + t.brief_id : "") +
        (t.completed_at ? " (미션 완료: " + esc(t.completed_at) + ")" : "") +
        '</p>'
      : "";
    const lifecycleButtonsHtml =
      (t.status === "pending_onboarding"
        ? '<button class="small" data-onboard-team="' + t.id + '">온보딩 시작</button>'
        : "") +
      (t.status === "active" && (t.task_id || t.brief_id)
        ? '<button class="small" data-complete-mission="' + t.id + '">미션 완료</button>'
        : "");
    // 사용자 요청(2026-10-29, 개선 계획 Phase 3 "조직도 용어 재정리") —
    // 기존 "조직도" 메뉴(상사 체인 뷰)는 그대로 두고 이름도 안 바꾼다
    // (혼란 위험이 더 크다는 판단) — 대신 "TF팀"이라는 용어가 실제로
    // 쓰이는 자리인 여기서 배지로 명확히 구분한다. task_id/brief_id가
    // 있으면 범용 재사용 팀이 아니라 "TF팀"임을 바로 보여준다.
    const tfTeamBadgeHtml = (t.task_id || t.brief_id)
      ? ' <span class="gate-badge" title="업무에 종속된 조직 — 기존 조직도 메뉴(상사 체인 뷰)와는 다른 개념입니다">TF팀</span>'
      : "";
    return '<section class="card" data-team-card="' + t.id + '">' +
      '<h3>#' + t.id + " " + esc(t.name) + tfTeamBadgeHtml + ' <span class="gate-badge' + statusBadgeClass + '">' + esc(label(t.status)) + '</span></h3>' +
      (t.description ? '<p class="muted">' + esc(t.description) + '</p>' : "") +
      linkedTaskHtml +
      humanStripHtml +
      canvasHtml +
      table(["타입", "멤버", "역할 라벨", "동작"], t.members.map(m => [
        esc(label(m.member_type)), esc(m.member_name || ("#" + m.member_id)), esc(m.role_label),
        '<button class="small danger" data-remove-member="' + t.id + ':' + m.id + '">제외</button>',
      ])) +
      '<form class="inline" data-add-member-form="' + t.id + '">' +
      '<select data-member-select required style="min-width:180px">' + memberOptionsHtml + '</select>' +
      '<input data-role-label placeholder="역할 라벨(선택)" style="width:140px">' +
      '<button class="small" type="submit">멤버 추가</button></form>' +
      teamDefaultsFormHtml(t) +
      '<div class="actions" style="margin-top:0.5rem">' +
      lifecycleButtonsHtml +
      // 버그 수정(2026-11-12, 이용 가이드 검증 중 발견) — 이 범용 토글이
      // `is_active`만 바꾸고 `status`는 그대로 둔다(teams.set_team_active()
      // 참고). pending_onboarding 팀에서 이 버튼을 누르면 "활성화" 한
      // 클릭으로 온보딩 절차(data-onboard-team) 없이 is_active=1이 돼
      // @멘션·배정 후보 쿼리(`WHERE is_active = 1`만 봄)에 바로 노출된다 —
      // "온보딩 전엔 호출 후보에 안 뜬다"는 가이드의 전제를 깨는 실제
      // 우회 경로였다. pending_onboarding 동안은 숨기고 "온보딩 시작"
      // 버튼(위 lifecycleButtonsHtml)만 쓰게 한다 — 그 버튼은 status와
      // is_active를 항상 함께 맞춘다.
      (t.status === "pending_onboarding" ? "" :
      '<button class="small" data-toggle-active="' + t.id + '" data-next-active="' + (t.is_active ? "0" : "1") + '">' +
      (t.is_active ? "비활성화" : "활성화") + '</button>') +
      '<button class="small" data-view-team-history="' + t.id + '">실행 이력 보기</button>' +
      (isAdmin ? '<button class="small danger" data-delete-team="' + t.id + '">팀 삭제</button>' : "") +
      '</div>' +
      '<div id="team-history-' + t.id + '"></div>' +
      '</section>';
  };

  content.innerHTML =
    '<section class="card"><h2>팀 — 복수 사람·Agent 그룹 등록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>사람(human_pool)·Agent(agent_pool) 멤버를 역할 라벨과 함께 묶어두면, "업무 요청" 탭의 할 일 상세에서 이 팀 전체를 한 할 일에 배정(resource_type="team")할 수 있습니다.</li>' +
    '<li>팀 배정의 결재 주체는 팀 리더(기본값은 배정 요청자, 확정 전까지 변경 가능)입니다 — 배정이 승인되면 팀의 사람 멤버(계정 연결된 경우)·팀 리더 모두 그 할 일의 협업 워크스페이스에 결과물을 쓸 수 있습니다.</li>' +
    '<li>그 워크스페이스의 "팀 Agent 실행" 버튼으로 이 팀의 온보딩된 Agent 멤버 전원을 실제 CrewAI Crew로 함께 실행할 수 있습니다(결과는 Agent별 결과물+결재 체인으로 그대로 쌓입니다).</li>' +
    '<li><b>기본 process/매니저</b>를 저장해두면 "팀 Agent 실행"에서 매번 새로 고르지 않아도 이 값이 기본으로 쓰입니다(그 자리에서 여전히 바꿀 수 있습니다) — 매니저는 hierarchical일 때만 쓰이고, 후보는 이 팀의 온보딩된 Agent 멤버로 제한됩니다.</li>' +
    '<li>Agent를 이 탭에서 바로 만들려면 "AI 에이전트 생성"에서 여러 할 일을 한 번에 골라 생성한 뒤 그 결과 화면의 "팀으로 묶기"를 쓰는 게 더 빠릅니다.</li>' +
    '<li>Task ID/Brief ID를 지정해 만들면 "TF팀"(그 업무에 종속된 팀)이 됩니다 — 생성 즉시 "온보딩 대기" 상태로 시작해, "온보딩 시작"을 눌러야 @멘션/배정 후보에 노출됩니다. 비워두면 기존처럼 바로 쓸 수 있는 범용 팀이 됩니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="team-create-form">' +
    '<input id="team-name" placeholder="팀 이름" required>' +
    '<input id="team-description" placeholder="설명(선택)" style="flex:1;min-width:160px">' +
    '<input id="team-task-id" type="number" min="1" step="1" placeholder="Task ID(선택 — TF팀)" style="width:170px">' +
    '<input id="team-brief-id" type="number" min="1" step="1" placeholder="Brief ID(선택 — TF팀)" style="width:170px">' +
    '<button class="primary" type="submit">팀 등록</button></form>' +
    "</section>" +
    (teams.length ? teams.map(teamCardHtml).join("") : '<p class="muted">등록된 팀이 없습니다.</p>');

  document.getElementById("team-create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const taskIdVal = document.getElementById("team-task-id").value;
    const briefIdVal = document.getElementById("team-brief-id").value;
    try {
      await api("POST", "/api/teams", {
        name: document.getElementById("team-name").value,
        description: document.getElementById("team-description").value,
        task_id: taskIdVal ? Number(taskIdVal) : null,
        brief_id: briefIdVal ? Number(briefIdVal) : null,
      });
      showMsg("global-msg", "팀을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-add-member-form]").forEach(form => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const teamId = form.dataset.addMemberForm;
      const sel = form.querySelector("[data-member-select]").value;
      if (!sel) { showMsg("global-msg", "멤버를 선택하세요.", "error"); return; }
      const [memberType, memberId] = sel.split(":");
      const roleLabel = form.querySelector("[data-role-label]").value;
      try {
        await api("POST", "/api/teams/" + teamId + "/members", {
          member_type: memberType, member_id: Number(memberId), role_label: roleLabel,
        });
        showMsg("global-msg", "멤버를 추가했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  content.querySelectorAll("[data-team-defaults-form]").forEach(form => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const teamId = form.dataset.teamDefaultsForm;
      const processVal = form.querySelector("[data-default-process]").value;
      const managerVal = form.querySelector("[data-default-manager]").value;
      const pipelineVal = form.querySelector("[data-default-pipeline]").value;
      try {
        await api("PATCH", "/api/teams/" + teamId, {
          default_process: processVal || null,
          default_manager_agent_id: managerVal ? Number(managerVal) : null,
          pipeline_template_id: pipelineVal ? Number(pipelineVal) : null,
        });
        showMsg("global-msg", "팀 기본값을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  content.querySelectorAll("[data-remove-member]").forEach(btn => {
    btn.onclick = async () => {
      const [teamId, memberRowId] = btn.dataset.removeMember.split(":");
      try {
        await api("DELETE", "/api/teams/" + teamId + "/members/" + memberRowId);
        showMsg("global-msg", "멤버를 제외했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-toggle-active]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/teams/" + btn.dataset.toggleActive + "/active", {
          is_active: btn.dataset.nextActive === "1",
        });
        showMsg("global-msg", "변경했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-onboard-team]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/teams/" + btn.dataset.onboardTeam + "/onboard");
        showMsg("global-msg", "온보딩 완료 — 이제 @멘션/배정 후보에 노출됩니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-complete-mission]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 팀의 미션을 완료로 표시합니다 — 이후 @멘션/배정 후보에서 제외됩니다. 계속할까요?"))) return;
      try {
        await api("POST", "/api/teams/" + btn.dataset.completeMission + "/complete-mission");
        showMsg("global-msg", "미션 완료로 표시했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-view-team-history]").forEach(btn => {
    btn.onclick = async () => {
      const teamId = btn.dataset.viewTeamHistory;
      const panel = document.getElementById("team-history-" + teamId);
      try {
        await _renderCrewHistory(panel, Number(teamId));
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-delete-team]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 팀을 완전히 지웁니다(되돌릴 수 없음). 과거 배정 이력은 남습니다. 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/teams/" + btn.dataset.deleteTeam);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  // 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — innerHTML을
  // 다 채운 뒤(컨테이너가 실제 DOM에 붙은 뒤)에만 Drawflow를 그 자리에
  // 꽂을 수 있다. refresh()가 renderTeams 전체를 다시 부르는 기존
  // 관례 그대로 — 멤버 추가/제외/기본값 저장 등 어떤 변경도 항상
  // 깨끗한 최신 상태로 캔버스를 다시 그린다(특별한 재초기화 훅 불필요).
  teams.forEach(t => {
    const activeAgentMembers = activeAgentMembersOf(t);
    if (!activeAgentMembers.length) return;
    const canvasControls = _initTeamWorkflowCanvas(
      document.getElementById("team-canvas-" + t.id), t, activeAgentMembers
    );
    if (!canvasControls) return;
    const zoomOutBtn = content.querySelector('[data-canvas-zoom-out="' + t.id + '"]');
    const zoomInBtn = content.querySelector('[data-canvas-zoom-in="' + t.id + '"]');
    const zoomFitBtn = content.querySelector('[data-canvas-zoom-fit="' + t.id + '"]');
    if (zoomOutBtn) zoomOutBtn.onclick = canvasControls.zoomOut;
    if (zoomInBtn) zoomInBtn.onclick = canvasControls.zoomIn;
    if (zoomFitBtn) zoomFitBtn.onclick = canvasControls.fit;
  });
}

/* ---------- 파이프라인 빌더(사용자 요청, 2026-10-18, "BMAD-METHOD와
   하네스 방법론을 참조해... 개발 Agent 팀을 구성(기존의 개발팀 갱신)...
   각 단계별 단독 또는 복수의 에이전트가 순차/병행 처리... 다른
   업무(시장조사-전략/기획 수립 등)에도 적용될 수 있도록 일반화") ----------
   엔진은 "단계 + 역할"만 안다(도메인 무관) — 그래서 이 화면 하나로
   SW개발 파이프라인도, 다른 업무 파이프라인도 똑같이 만들 수 있다. */

const PIPELINE_MODE_LABEL = {
  single: "단독(1명)",
  sequential_relay: "순차 릴레이(N명, 앞사람 결과를 이어받음)",
  parallel_delegated: "병행 위임(리드 1명이 코워커에게 실제로 위임)",
};

function pipelineStageAgentRowHtml(activeAgents) {
  return '<div class="inline pipeline-agent-row" style="margin-bottom:0.3rem">' +
    '<select class="pipeline-agent-role" style="min-width:200px"><option value="">— 역할 선택 —</option>' +
    activeAgents.map(a => '<option value="' + esc(a.role_name) + '">' + agentDisplayLabel(a) + '</option>').join("") +
    '</select>' +
    '<input class="pipeline-agent-instructions" placeholder="이 역할만의 구체 지시(선택)" style="flex:1;min-width:200px">' +
    '<button type="button" class="small danger pipeline-agent-row-remove">×</button>' +
    '</div>';
}

function wirePipelineAgentRowRemovers(scope) {
  scope.querySelectorAll(".pipeline-agent-row-remove").forEach(btn => {
    btn.onclick = () => {
      const rows = scope.querySelectorAll(".pipeline-agent-row");
      if (rows.length <= 1) { showMsg("global-msg", "최소 역할 1개는 있어야 합니다.", "error"); return; }
      btn.closest(".pipeline-agent-row").remove();
    };
  });
}

function pipelineStageCardHtml(templateId, stage, index, total) {
  const agentsText = stage.agents.map(a =>
    esc(a.role_name) + (a.role_name === stage.lead_role_name ? " (리드)" : "")
  ).join(", ");
  return '<div class="card" style="margin-bottom:0.5rem" data-pipeline-stage-card="' + stage.id + '">' +
    '<div class="inline" style="justify-content:space-between">' +
    '<b>' + stage.seq_order + ". " + esc(stage.name) + "</b>" +
    '<span class="muted">' + (PIPELINE_MODE_LABEL[stage.mode] || stage.mode) +
    (stage.is_primary_deliverable ? ' <span class="gate-badge gate-ok">최종 결과물 단계</span>' : "") +
    "</span>" +
    "</div>" +
    (stage.instructions ? '<p class="muted">' + esc(stage.instructions) + "</p>" : "") +
    (stage.expected_output ? '<p class="muted"><b>기대 산출물:</b> ' + esc(stage.expected_output) + "</p>" : "") +
    "<p>참여 역할: " + agentsText + "</p>" +
    '<p class="muted">Gate 통과 기준 ' + Math.round(stage.min_gate_pass_ratio * 100) + "% · 최대 재시도 " +
    stage.max_retry + "회 · 사람 승인 " + (stage.requires_human_approval ? "필요" : "불필요") + "</p>" +
    '<div class="actions">' +
    (index > 0 ? '<button class="small" data-reorder-stage="' + stage.id + ':up">↑</button>' : "") +
    (index < total - 1 ? '<button class="small" data-reorder-stage="' + stage.id + ':down">↓</button>' : "") +
    '<button class="small danger" data-delete-stage="' + stage.id + '">단계 삭제</button>' +
    "</div></div>";
}

function pipelineAddStageFormHtml(templateId, activeAgents) {
  return '<form class="card" data-add-stage-form="' + templateId + '" style="margin-top:0.5rem">' +
    "<h4>+ 단계 추가</h4>" +
    '<div class="inline">' +
    '<input class="stage-name" placeholder="단계 이름" required style="min-width:140px">' +
    '<select class="stage-mode">' +
    Object.keys(PIPELINE_MODE_LABEL).map(m => '<option value="' + m + '">' + PIPELINE_MODE_LABEL[m] + "</option>").join("") +
    "</select>" +
    "</div>" +
    '<textarea class="stage-instructions" placeholder="이 단계 공통 지시문(선택 — 예: &quot;설계 산출물을 검증하세요&quot;)" style="width:100%;margin-top:0.3rem;min-height:2.2rem"></textarea>' +
    // 사용자 요청(2026-10-20, "고급 에이전트 관리") — 지금까지 모든
    // 단계가 똑같은 플랫폼 공통 Task.expected_output 문구를 썼다(예:
    // "QA 기능테스트" 단계도 "구현" 단계와 같은 기대 산출물 문구). 빈
    // 칸이면 그 공통 문구를 그대로 쓴다.
    '<textarea class="stage-expected-output" placeholder="이 단계의 기대 산출물(선택 — 예: &quot;실제로 실행 가능한 테스트 코드와 통과/실패 리포트&quot;, 비우면 공통 기본 문구 사용)" style="width:100%;margin-top:0.3rem;min-height:2.2rem"></textarea>' +
    '<p class="muted" style="margin:0.3rem 0 0">역할을 추가한 순서가 "순차 릴레이"의 처리 순서입니다. "병행 위임"에서는 <b>첫 번째로 추가한 역할이 리드(위임하는 쪽)</b>가 됩니다.</p>' +
    '<div class="stage-agent-rows">' + pipelineStageAgentRowHtml(activeAgents) + "</div>" +
    '<button type="button" class="small stage-add-agent-row">+ 역할 추가</button>' +
    '<div class="inline" style="margin-top:0.4rem">' +
    '<label class="inline" style="gap:0.3rem"><input type="checkbox" class="stage-requires-approval"> 사람 승인 필요(계획 확정 체크포인트)</label>' +
    // 사용자 보고(2026-10-19, "최종 결과물에 코드가 아니라 품질
    // 리포트만 있다") — Task 워크스페이스의 "최종 결과물"은 이 단계
    // 산출물을 우선 집는다(체크 안 하면 기존처럼 "가장 최근 승인된
    // deliverable"로 폴백). 보통 "구현/작성" 성격의 단계 하나에만
    // 체크한다 — 검증/보고 단계는 보통 아니다.
    '<label class="inline" style="gap:0.3rem"><input type="checkbox" class="stage-is-primary"> 이 단계 산출물이 최종 결과물</label>' +
    '</div>' +
    '<div class="inline" style="margin-top:0.3rem">' +
    '<label>최대 재시도 <input type="number" class="stage-max-retry" value="2" min="1" max="10" style="width:60px"></label>' +
    '<label>Gate 통과 기준 <input type="number" class="stage-min-gate-ratio" value="0" min="0" max="100" style="width:60px">%</label>' +
    "</div>" +
    '<button class="primary small" type="submit" style="margin-top:0.4rem">이 단계 추가</button>' +
    "</form>";
}

function pipelineTemplateCardHtml(t, detail, activeAgents) {
  const stages = detail ? detail.stages : [];
  return '<section class="card" data-pipeline-template-card="' + t.id + '">' +
    '<h3>#' + t.id + " " + esc(t.name) +
    (t.is_active ? "" : ' <span class="gate-badge gate-warn">비활성</span>') +
    ' <span class="muted">(' + esc(t.domain) + ")</span></h3>" +
    (t.description ? '<p class="muted">' + esc(t.description) + "</p>" : "") +
    (stages.length
      ? stages.map((s, i) => pipelineStageCardHtml(t.id, s, i, stages.length)).join("")
      : '<p class="muted">아직 단계가 없습니다 — 아래에서 추가하세요.</p>') +
    pipelineAddStageFormHtml(t.id, activeAgents) +
    '<div class="actions" style="margin-top:0.5rem">' +
    '<button class="small" data-toggle-pipeline-active="' + t.id + '" data-next-active="' + (t.is_active ? "0" : "1") + '">' +
    (t.is_active ? "비활성화" : "활성화") + "</button>" +
    (ME.role === "admin" ? '<button class="small danger" data-delete-pipeline-template="' + t.id + '">템플릿 삭제</button>' : "") +
    "</div></section>";
}

async function renderPipelines(content) {
  const [templates, agents] = await Promise.all([
    api("GET", "/api/pipeline-templates"),
    api("GET", "/api/agent-pool"),
  ]);
  const activeAgents = agents.filter(a => a.status === "active");
  const details = await Promise.all(
    templates.map(t => api("GET", "/api/pipeline-templates/" + t.id))
  );
  const detailById = {};
  templates.forEach((t, i) => { detailById[t.id] = details[i]; });

  content.innerHTML =
    '<section class="card"><h2>파이프라인 관리 — 단계형 개발/업무 Agent 팀</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"단계(stage)"를 순서대로 쌓아 파이프라인을 만듭니다 — 각 단계는 <b>단독(1명)</b>, <b>순차 릴레이(N명이 순서대로 이어받음)</b>, <b>병행 위임(리드 1명이 코워커들에게 실제로 하위 과업을 쪼개 위임)</b> 중 하나로 동작합니다.</li>' +
      '<li>단계별로 Gate A-G 통과 기준·최대 재시도·"사람 승인 필요" 여부를 정할 수 있습니다 — 승인이 필요 없는 단계는 Gate만 통과하면 자동으로 다음 단계로 넘어갑니다(최소 HITL). 반복해도 기준을 못 넘기면 그때만 결재함으로 올라갑니다(최소 HOTL).</li>' +
      '<li>실행은 할 일(Task)의 협업 워크스페이스에서 "파이프라인 실행"으로 시작합니다 — 이 화면은 템플릿(설계도)만 관리합니다.</li>' +
      '<li>SW 개발뿐 아니라 시장조사-분석(3C/SWOT)-전략/기획 같은 다른 업무도 같은 방식으로 만들 수 있습니다(역할만 다를 뿐 엔진은 동일).</li>' +
      "</ul>"
    ) +
    '<form class="inline" id="pipeline-template-create-form">' +
    '<input id="pipeline-template-name" placeholder="파이프라인 이름" required>' +
    '<input id="pipeline-template-description" placeholder="설명(선택)" style="flex:1;min-width:160px">' +
    '<input id="pipeline-template-domain" placeholder="도메인(선택, 예: software_dev)" style="width:180px">' +
    '<button class="primary" type="submit">파이프라인 등록</button></form>' +
    "</section>" +
    (templates.length
      ? templates.map(t => pipelineTemplateCardHtml(t, detailById[t.id], activeAgents)).join("")
      : '<p class="muted">등록된 파이프라인이 없습니다.</p>');

  document.getElementById("pipeline-template-create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/pipeline-templates", {
        name: document.getElementById("pipeline-template-name").value,
        description: document.getElementById("pipeline-template-description").value,
        domain: document.getElementById("pipeline-template-domain").value || "general",
      });
      showMsg("global-msg", "파이프라인을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-add-stage-form]").forEach(form => {
    wirePipelineAgentRowRemovers(form);
    form.querySelector(".stage-add-agent-row").onclick = () => {
      form.querySelector(".stage-agent-rows").insertAdjacentHTML("beforeend", pipelineStageAgentRowHtml(activeAgents));
      wirePipelineAgentRowRemovers(form);
    };
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const templateId = form.dataset.addStageForm;
      const mode = form.querySelector(".stage-mode").value;
      const rows = Array.from(form.querySelectorAll(".pipeline-agent-row"));
      const roleAgents = rows.map(r => ({
        role_name: r.querySelector(".pipeline-agent-role").value,
        instructions: r.querySelector(".pipeline-agent-instructions").value,
      }));
      if (roleAgents.some(a => !a.role_name)) {
        showMsg("global-msg", "모든 역할 줄에서 역할을 선택하세요.", "error"); return;
      }
      if (mode === "single" && roleAgents.length !== 1) {
        showMsg("global-msg", "단독 모드는 역할이 정확히 1개여야 합니다.", "error"); return;
      }
      if (mode === "parallel_delegated" && roleAgents.length < 2) {
        showMsg("global-msg", "병행 위임 모드는 리드 포함 최소 2개 역할이 필요합니다.", "error"); return;
      }
      const payload = {
        name: form.querySelector(".stage-name").value,
        mode,
        agents: roleAgents,
        instructions: form.querySelector(".stage-instructions").value,
        max_retry: Number(form.querySelector(".stage-max-retry").value) || 2,
        min_gate_pass_ratio: (Number(form.querySelector(".stage-min-gate-ratio").value) || 0) / 100,
        requires_human_approval: form.querySelector(".stage-requires-approval").checked,
        is_primary_deliverable: form.querySelector(".stage-is-primary").checked,
        expected_output: form.querySelector(".stage-expected-output").value,
      };
      if (mode === "parallel_delegated") payload.lead_role_name = roleAgents[0].role_name;
      try {
        await api("POST", "/api/pipeline-templates/" + templateId + "/stages", payload);
        showMsg("global-msg", "단계를 추가했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  content.querySelectorAll("[data-reorder-stage]").forEach(btn => {
    btn.onclick = async () => {
      const [stageId, direction] = btn.dataset.reorderStage.split(":");
      const templateId = btn.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      try {
        await api("POST", "/api/pipeline-templates/" + templateId + "/stages/" + stageId + "/reorder", { direction });
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-delete-stage]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 단계를 지웁니다. 계속할까요?"))) return;
      const stageId = btn.dataset.deleteStage;
      const templateId = btn.closest("[data-pipeline-template-card]").dataset.pipelineTemplateCard;
      try {
        await api("DELETE", "/api/pipeline-templates/" + templateId + "/stages/" + stageId);
        showMsg("global-msg", "단계를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-toggle-pipeline-active]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/pipeline-templates/" + btn.dataset.togglePipelineActive + "/active", {
          is_active: btn.dataset.nextActive === "1",
        });
        showMsg("global-msg", "변경했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-delete-pipeline-template]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 파이프라인 템플릿을 완전히 지웁니다(실행 이력이 있으면 지울 수 없습니다). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/pipeline-templates/" + btn.dataset.deletePipelineTemplate);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 모델 레지스트리(사용자 요청, 2026-10-06, "운영 관리에 사용
   가능한 ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고
   등록된 모델을 불러오는 방식으로 UI/UX를 개선") ---------- */

async function renderModelRegistry(content) {
  const isAdmin = ME.role === "admin";
  const models = await api("GET", "/api/model-registry");
  const byProvider = (p) => models.filter(m => m.provider === p);
  const providerLabel = { anthropic: "Anthropic", openai: "OpenAI", ollama: "Ollama(로컬)" };
  const providerOptionsHtml = Object.keys(providerLabel)
    .map(p => '<option value="' + p + '">' + providerLabel[p] + "</option>").join("");
  // 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화 각
  // tier에 적용되는 모델을 매핑하는 기능을 제공... 향후 새로운 모델
  // 출시되거나 모델 프로바이더 변경시에도 각 Tier별 모델 맵핑 변경
  // 하여 관리, 일단 설정된 후 변경할 수 있는 기능이 제공되지 않아") —
  // "tier 직접 매핑으로 변경"(premium/balanced/economy 등급 간접
  // 매핑 대신, agent_optimizer의 tier 이름 자체에 모델을 직접 매핑).
  // 체크박스 — 모델 하나가 여러 tier에 동시 매핑될 수 있다. 전부
  // 체크 해제한 뒤 저장하면 그 모델은 다시 이름 패턴 추정으로
  // 돌아간다(초기화) — 몇 번이든 다시 바꿀 수 있다.
  const _OPTIMIZATION_TIER_LABELS = {
    deep_reasoning: "심층추론", precision_fast: "정밀·신속", standard: "표준",
  };
  const tierMappingHtml = (m) =>
    Object.keys(_OPTIMIZATION_TIER_LABELS).map(t =>
      '<label style="white-space:nowrap;margin-right:0.5rem"><input type="checkbox" class="mr-tier-pick" data-model-id="' + m.id +
      '" value="' + t + '"' + ((m.optimization_tiers || []).includes(t) ? " checked" : "") + '> ' +
      esc(_OPTIMIZATION_TIER_LABELS[t]) + "</label>"
    ).join("") +
    '<button class="small" data-save-optimization-tiers="' + m.id + '">저장</button>';
  // 사용자 요청(2026-11-12, "ollama, openai 등 프로바이더의 모델 성능
  // Test하고 결과 표시, 티어 추천 기능") — 고정 테스트 프롬프트 1건을
  // 실제로 호출해 속도(글자/초)를 재고, 그 속도만으로 tier를
  // 추천한다(품질 평가 아님 — `model_benchmark.py` 모듈 docstring
  // 참고). "저장"은 직접 눌러야 기존 "최적화 Tier 매핑"에 반영된다
  // (추천이 자동 적용되지 않음 — ⚡ 최적화 추천과 같은 관례).
  const benchmarkHtml = (m) => {
    const b = m.latest_benchmark;
    let resultHtml;
    if (!b) {
      resultHtml = '<span class="muted">아직 테스트 안 함</span>';
    } else if (b.status === "failed") {
      resultHtml = '<span class="gate-badge gate-warn" title="' + esc(b.error || "") + '">실패</span> ' +
        '<span class="muted">' + esc(b.tested_at) + '</span>';
    } else {
      resultHtml = b.chars_per_second.toFixed(1) + '자/초 · ' + b.latency_seconds.toFixed(1) + '초 소요 · ' +
        '<span class="gate-badge" title="속도만 반영한 참고용 추천입니다 — 실제 응답 품질은 직접 확인하세요">추천: ' +
        esc(_OPTIMIZATION_TIER_LABELS[b.recommended_tier] || b.recommended_tier) + '</span> ' +
        '<button class="small" data-apply-benchmark-tier="' + m.id + '" data-tier="' + b.recommended_tier + '">이 tier로 저장</button> ' +
        '<span class="muted">(' + esc(b.tested_at) + ')</span>';
    }
    return resultHtml + '<br><button class="small" data-run-benchmark="' + m.id + '">성능 테스트</button>';
  };
  const providerTableHtml = (provider) => {
    const rows = byProvider(provider);
    return "<h3>" + providerLabel[provider] + "</h3>" +
      table(["모델명", "상태", "최적화 Tier 매핑", "성능 테스트", "동작"], rows.map(m => [
        esc(m.name),
        m.is_active ? "활성" : "비활성",
        tierMappingHtml(m),
        benchmarkHtml(m),
        '<div class="actions">' +
          '<button class="small" data-toggle-model-active="' + m.id + '" data-active="' + (m.is_active ? "0" : "1") + '">' +
          (m.is_active ? "비활성화" : "활성화") + "</button>" +
          (isAdmin ? '<button class="small danger" data-delete-model="' + m.id + '">삭제</button>' : "") +
          "</div>",
      ]));
  };

  content.innerHTML =
    '<section class="card"><h2>모델 레지스트리</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>여기 등록한 모델이 "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"·협업 워크스페이스의 "모델명" 입력창 자동완성에 뜹니다 — 목록에 없는 이름도 그대로 입력할 수 있어 자유 입력 자체는 막지 않습니다(참고용 자동완성).</li>' +
      '<li><b>불러오기</b>는 Ollama면 로컬 서버(OLLAMA_BASE_URL)에서, OpenAI/Anthropic이면 그 계정의 API 키로 실제 보유 중인 모델 목록을 조회합니다(토큰을 생성하지 않는 카탈로그 조회라 과금되지 않습니다) — 조회만으로는 아무것도 저장되지 않고, 선택 후 "선택한 모델 등록"을 눌러야 저장됩니다.</li>' +
      '<li><b>비활성화</b>는 자동완성에서만 빼고 등록 자체는 유지합니다.</li>' +
      '<li><b>삭제</b>는 레지스트리 항목만 지웁니다 — 이미 Agent에 저장된 모델 설정 자체는 안 바뀝니다.</li>' +
      '<li><b>최적화 Tier 매핑</b>은 "LLM 설정 최적화"(Agent 관리 화면의 "⚡ 최적화 추천")가 심층추론/정밀·신속/표준 역할에 이 모델을 최우선으로 추천할지 직접 지정합니다(모델 하나가 여러 tier에 동시 매핑될 수 있습니다) — 아무것도 체크하지 않으면 모델명 패턴 추정으로 폴백합니다. 새 모델 출시·provider 교체 시 여기 매핑만 바꾸면 되고, 몇 번이든 다시 바꿀 수 있습니다.</li>' +
      '<li><b>성능 테스트</b>는 고정된 짧은 프롬프트 1건을 이 모델에 실제로 보내 응답 속도(글자/초)를 재고, 그 속도만으로 tier를 추천합니다 — <b>속도만 보는 참고용이지 품질 평가가 아닙니다</b>(얼마나 똑똑한지는 측정하지 않습니다). "이 tier로 저장"을 눌러야 위 "최적화 Tier 매핑"에 실제로 반영됩니다. 클라우드 provider는 실제 API 비용이 발생합니다(Ollama는 로컬 연산이라 무료).</li>' +
      '<li>이 탭 전체가 admin 전용입니다 — manager를 포함한 다른 역할에는 메뉴 자체가 보이지 않습니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="mr-fetch-form">' +
    '<select id="mr-fetch-provider">' + providerOptionsHtml + "</select>" +
    '<button type="submit" class="small">불러오기</button>' +
    "</form>" +
    '<div id="mr-fetch-result"></div>' +
    '<form class="inline" id="mr-manual-form">' +
    '<select id="mr-manual-provider">' + providerOptionsHtml + "</select>" +
    '<input id="mr-manual-name" placeholder="모델명 직접 입력(불러오기 없이 바로 등록)" style="flex:1;min-width:200px" required>' +
    '<button type="submit" class="small">수동 등록</button>' +
    "</form>" +
    providerTableHtml("anthropic") +
    providerTableHtml("openai") +
    providerTableHtml("ollama") +
    "</section>";

  document.getElementById("mr-fetch-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const provider = document.getElementById("mr-fetch-provider").value;
    const resultBox = document.getElementById("mr-fetch-result");
    resultBox.innerHTML = '<p class="muted">불러오는 중...</p>';
    let resp;
    try {
      resp = await api("GET", qs("/api/model-registry/fetch", { provider }));
    } catch (e) {
      resultBox.innerHTML = "";
      showMsg("global-msg", e.message, "error");
      return;
    }
    const registeredNames = new Set(byProvider(provider).map(m => m.name));
    resultBox.innerHTML = resp.models.length
      ? '<div style="max-height:220px;overflow-y:auto;border:1px solid #ddd;padding:0.5rem;margin:0.5rem 0">' +
        resp.models.map(name =>
          '<label style="display:block"><input type="checkbox" class="mr-candidate" value="' + esc(name) + '"' +
          (registeredNames.has(name) ? " disabled checked" : "") + "> " + esc(name) +
          (registeredNames.has(name) ? ' <span class="muted">(이미 등록됨)</span>' : "") + "</label>"
        ).join("") +
        "</div>" +
        '<button type="button" class="small primary" id="mr-register-selected">선택한 모델 등록</button>'
      : '<p class="muted">불러올 수 있는 모델이 없습니다.</p>';
    const registerBtn = document.getElementById("mr-register-selected");
    if (registerBtn) {
      registerBtn.onclick = async () => {
        const names = Array.from(resultBox.querySelectorAll(".mr-candidate:checked:not(:disabled)")).map(cb => cb.value);
        if (!names.length) { showMsg("global-msg", "등록할 모델을 선택하세요.", "error"); return; }
        try {
          for (const name of names) {
            await api("POST", "/api/model-registry", { provider, name });
          }
          showMsg("global-msg", names.length + "개 모델을 등록했습니다.", "ok");
          refresh();
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      };
    }
  });

  document.getElementById("mr-manual-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/model-registry", {
        provider: document.getElementById("mr-manual-provider").value,
        name: document.getElementById("mr-manual-name").value,
      });
      showMsg("global-msg", "모델을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-toggle-model-active]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/model-registry/" + btn.dataset.toggleModelActive + "/active", {
          is_active: btn.dataset.active === "1",
        });
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-model]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 모델을 레지스트리에서 지웁니다(되돌릴 수 없음, 이미 저장된 Agent의 모델 설정 자체는 안 바뀝니다). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/model-registry/" + btn.dataset.deleteModel);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-save-optimization-tiers]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.saveOptimizationTiers;
      const checks = content.querySelectorAll('.mr-tier-pick[data-model-id="' + modelId + '"]:checked');
      try {
        await api("PATCH", "/api/model-registry/" + modelId + "/optimization-tiers", {
          optimization_tiers: Array.from(checks).map(c => c.value),
        });
        showMsg("global-msg", "최적화 Tier 매핑을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-run-benchmark]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 모델에 실제로 테스트 프롬프트 1건을 보내 응답 속도를 측정합니다 — 클라우드 provider는 실제 API 비용이 발생합니다(Ollama는 로컬 연산이라 무료). 계속할까요?"))) return;
      try {
        await withBusy(btn, "테스트 중…", () =>
          api("POST", "/api/model-registry/" + btn.dataset.runBenchmark + "/benchmark", {})
        );
        showMsg("global-msg", "성능 테스트가 끝났습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-apply-benchmark-tier]").forEach(btn => {
    btn.onclick = async () => {
      const modelId = btn.dataset.applyBenchmarkTier;
      const tier = btn.dataset.tier;
      const existing = models.find(m => m.id === Number(modelId));
      const nextTiers = Array.from(new Set([...(existing.optimization_tiers || []), tier]));
      try {
        await api("PATCH", "/api/model-registry/" + modelId + "/optimization-tiers", {
          optimization_tiers: nextTiers,
        });
        showMsg("global-msg", "추천 tier를 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 지식 베이스(VectorDB/GraphDB 관리) ---------- */

// 사용자 요청(2026-10-26, "공통의 지식을 관리하기 위해 Admin에 VectorDB,
// GraphDB 관리 기능을 추가해줘") — 페이지네이션 offset을 탭 밖(모듈
// 스코프)에 둔다. `refresh()`는 `selectTab()`으로 이 함수를 처음부터
// 다시 부르므로(Crew 빌더의 `_pendingCrewBuilderTaskId`와 같은 이유),
// "다음" 버튼을 눌렀을 때 이 값이 0으로 되돌아가면 페이지 이동이 전혀
// 안 되는 것처럼 보인다.
let _kbChunkOffset = 0;
const _KB_CHUNK_LIMIT = 20;

async function renderKnowledgeBase(content) {
  const [embeddingConfig, ragSearchConfig, knowledgeDocs, chunkStats, chunkPage, graphStats, relationships] = await Promise.all([
    api("GET", "/api/embedding-config"),
    api("GET", "/api/rag-search-config"),
    api("GET", "/api/documents"),
    api("GET", "/api/knowledge-chunks/stats"),
    api("GET", qs("/api/knowledge-chunks", { limit: _KB_CHUNK_LIMIT, offset: _kbChunkOffset })),
    api("GET", "/api/graph/stats"),
    api("GET", "/api/graph/relationships"),
  ]);

  const modelBreakdown = chunkStats.by_model.length
    ? chunkStats.by_model.map(m => esc(m.embedding_provider) + "/" + esc(m.embedding_model) + ": " + m.count + "건").join(", ")
    : "없음";
  const chunkRows = chunkPage.items.map(c => [
    c.source_document_filename ? ("문서: " + esc(c.source_document_filename))
      : (c.source_agent_role_name ? ("Agent: " + esc(c.source_agent_role_name)) : "기타"),
    esc(c.text.length > 80 ? c.text.slice(0, 80) + "…" : c.text),
    String(c.text.length),
    esc(c.embedding_provider) + "/" + esc(c.embedding_model),
    esc(c.created_at),
    '<button class="small danger" data-delete-chunk="' + c.id + '">삭제</button>',
  ]);
  const pageStart = chunkPage.total ? _kbChunkOffset + 1 : 0;
  const pageEnd = Math.min(_kbChunkOffset + _KB_CHUNK_LIMIT, chunkPage.total);

  content.innerHTML =
    // 사용자 요청(2026-10-26, "embedding 모델 설정 기능을 구현해줘
    // (하드코딩 하지않도록)") — 원래 "모델 레지스트리" 탭에 있었으나,
    // VectorDB/GraphDB 관리 기능이 이 탭으로 모이면서 함께 옮겼다
    // (코드는 그대로, 위치만 이동).
    '<section class="card"><h2>임베딩 모델 설정(VectorDB 검색용)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"지식검색" 스킬을 가진 Agent가 과거 산출물을 검색할 때 쓰는 임베딩 provider/모델입니다 — 채팅용 모델과는 별개입니다.</li>' +
      '<li>서로 다른 모델로 만든 벡터는 비교하지 않으므로(의미 공간이 다름) 시스템 전체에 하나만 설정합니다. 바꾸면 그 이전에 다른 모델로 색인된 과거 산출물은 더 이상 검색되지 않습니다(다시 색인해야 함).</li>' +
      '<li>설정한 적이 없으면 자동판별(OPENAI_API_KEY가 있으면 openai, 없으면 로컬 ollama) 결과를 보여줍니다 — 아래 폼으로 저장하면 그걸 명시적으로 고정합니다.</li>' +
      '<li>provider를 고르면 그 provider가 실제로 보유한 임베딩 모델 목록을 자동으로 불러와 "모델 선택" 드롭다운에 채워줍니다(Ollama는 로컬 서버, OpenAI는 API 키로 조회 — 과금 없는 카탈로그 조회입니다). 드롭다운에서 고르면 아래 모델명 칸에 그대로 채워집니다 — 목록에 없는 이름이 필요하면 그 칸에 직접 입력해도 됩니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (embeddingConfig.is_explicit ? '<b>설정됨</b>' : '<span class="muted">자동판별 중(아직 명시적으로 설정 안 함)</span>') +
    ' — provider: <b>' + esc(embeddingConfig.provider) + '</b>, 모델: <b>' + esc(embeddingConfig.model) + '</b></p>' +
    '<form class="inline" id="embedding-config-form">' +
    '<select id="ec-provider">' +
    '<option value="openai"' + (embeddingConfig.provider === "openai" ? " selected" : "") + '>OpenAI</option>' +
    '<option value="ollama"' + (embeddingConfig.provider === "ollama" ? " selected" : "") + '>Ollama(로컬)</option>' +
    '</select>' +
    '<select id="ec-model-pick" style="min-width:220px"><option value="">모델 목록 불러오는 중…</option></select>' +
    '<input id="ec-model" placeholder="모델명(예: mxbai-embed-large:latest)" value="' + escAttr(embeddingConfig.model) + '" style="flex:1;min-width:220px" required>' +
    '<button type="submit" class="small primary">저장</button>' +
    '<span id="ec-fetch-status" class="muted"></span>' +
    '</form>' +
    "</section>" +
    // 사용자 요청(2026-11-03, "RAG 검색 품질 향상... 우선순위를 정해
    // 진행해줘") — top_k(검색 결과 개수)와 min_score(관련성 하한,
    // 코사인 유사도 기준)를 admin/manager가 조정할 수 있게 한다.
    '<section class="card"><h2>RAG 검색 설정</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>top_k: 검색 1회당 가져올 결과 개수(기본 3개).</li>' +
      '<li>min_score: 관련성 하한(코사인 유사도, 0.0~1.0) — 이보다 낮은 결과는 무관한 것으로 보고 제외합니다(기본 0.2). 너무 높이면 관련 있는 결과도 걸러질 수 있습니다.</li>' +
      '</ul>'
    ) +
    '<p>' + (ragSearchConfig.is_explicit ? '<b>설정됨</b>' : '<span class="muted">기본값 사용 중(아직 명시적으로 설정 안 함)</span>') +
    ' — top_k: <b>' + esc(ragSearchConfig.top_k) + '</b>, min_score: <b>' + esc(ragSearchConfig.min_score) + '</b></p>' +
    '<form class="inline" id="rag-search-config-form">' +
    '<input id="rsc-top-k" type="number" min="1" step="1" placeholder="top_k" value="' + escAttr(ragSearchConfig.top_k) + '" style="width:100px" required>' +
    '<input id="rsc-min-score" type="number" min="0" max="1" step="0.05" placeholder="min_score" value="' + escAttr(ragSearchConfig.min_score) + '" style="width:120px" required>' +
    '<button type="submit" class="small primary">저장</button>' +
    '</form>' +
    "</section>" +
    // 사용자 요청(2026-10-26, "대화창 및 에이전트로 구성된 업무에서
    // 외부 자료(마크다운, pdf, html)를 입력받을 수 있도록") — 역시
    // 코드는 그대로, 위치만 이동.
    '<section class="card"><h2>지식 자료실(RAG 참고 자료 — 파일 업로드)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>여기 올린 자료는 대화와 무관하게 전체 시스템에서 공유됩니다 — "지식검색" 스킬을 가진 모든 Agent가 검색할 수 있습니다(대화창·AI 에이전트 관리에서 올린 파일도 똑같은 곳에 쌓입니다).</li>' +
      '<li>지원 형식: .md / .markdown / .txt / .html / .htm / .pdf (최대 10MB).</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="kd-upload-form">' +
    '<input type="file" id="kd-file-input" accept=".md,.markdown,.txt,.html,.htm,.pdf" required>' +
    '<button type="submit" class="small primary">업로드·색인</button>' +
    '</form>' +
    table(["파일명", "업로더", "글자수", "조각수", "업로드일", "동작"], knowledgeDocs.map(d => [
      esc(d.filename),
      esc(d.uploaded_by_username),
      String(d.char_count),
      String(d.chunk_count),
      esc(d.created_at),
      '<button class="small danger" data-delete-document="' + d.id + '">삭제</button>',
    ])) +
    (knowledgeDocs.length ? "" : '<p class="muted">아직 올린 자료가 없습니다.</p>') +
    "</section>" +
    // 신규 — 파일 없이 텍스트를 바로 붙여넣어 색인.
    '<section class="card"><h2>텍스트 직접 입력</h2>' +
    '<p class="muted">파일을 만들기 번거로운 짧은 메모·정책 요약 등을 그대로 붙여넣어 색인합니다. 파일 업로드와 같은 전역 VectorDB에 들어갑니다.</p>' +
    '<form id="kb-text-form">' +
    '<input id="kb-text-label" placeholder="라벨(예: 2026 휴가 정책 요약 — 비우면 &quot;직접 입력&quot;)" style="width:100%;box-sizing:border-box;margin-bottom:0.4rem">' +
    '<textarea id="kb-text-body" placeholder="색인할 텍스트를 붙여넣으세요" rows="4" style="width:100%;box-sizing:border-box" required></textarea>' +
    '<button type="submit" class="small primary" style="margin-top:0.4rem">저장·색인</button>' +
    '</form>' +
    "</section>" +
    // 신규 — 낱개 조각 조회/삭제 + 통계.
    '<section class="card"><h2>저장된 지식 조각(Chunk)</h2>' +
    '<p class="muted">전체 ' + chunkStats.total_chunks + '개 조각 · ' + chunkStats.total_characters + '자 · 모델별 ' + modelBreakdown + '</p>' +
    '<p class="muted">출처 — Agent 산출물: ' + chunkStats.by_source.agent_output + '건, 업로드/직접입력: ' + chunkStats.by_source.uploaded_document + '건, 기타: ' + chunkStats.by_source.other + '건</p>' +
    table(["출처", "내용(미리보기)", "글자수", "모델", "생성일", "동작"], chunkRows) +
    (chunkRows.length ? "" : '<p class="muted">저장된 조각이 없습니다.</p>') +
    '<div class="inline">' +
    '<button type="button" class="small" id="kb-chunks-prev"' + (_kbChunkOffset <= 0 ? " disabled" : "") + '>이전</button>' +
    '<span class="muted">' + pageStart + '–' + pageEnd + ' / ' + chunkPage.total + '</span>' +
    '<button type="button" class="small" id="kb-chunks-next"' + (_kbChunkOffset + _KB_CHUNK_LIMIT >= chunkPage.total ? " disabled" : "") + '>다음</button>' +
    '</div>' +
    "</section>" +
    // 신규 — GraphDB는 별도 저장소가 없어 조회+통계만 제공.
    '<section class="card"><h2>GraphDB 관계 조회</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>GraphDB는 별도 저장소가 없습니다 — "사람 인력 관리"(상사 지정)·"결재"(배정 승인) 데이터를 관계로 읽어 보여주는 것뿐이라, 여기서 입력/삭제할 대상이 없습니다.</li>' +
      '<li>관계 자체를 바꾸려면 "사람 인력 관리"에서 상사를 지정하거나, 배정을 승인/반려하세요.</li>' +
      '</ul>'
    ) +
    '<p class="muted">상사가 지정된 사용자 ' + graphStats.users_with_manager_count + '명 · 직속 관리자 ' + graphStats.distinct_managers_count + '명 · 확정된 배정 ' + graphStats.confirmed_assignments_count + '건</p>' +
    // 사용자 요청(2026-11-03, "요청자ID, 리소스, Task ID만으로는 관계를
    // 파악하기 어려워... 가독성 높고 의미있는 정보전달이 되기 위한
    // 노출 항목을 제안해줘" → "그렇게 수정해줘") — 원시 ID 대신
    // 사람이 읽을 이름/상태로 바꾼다. Task ID는 업무명 옆 괄호로
    // 축소 표기(별도 컬럼으로 안 둠 — 매니저가 "몇 번"보다 "뭘 하는
    // 업무인지"를 먼저 읽는 게 자연스러움).
    table(["요청자", "업무명", "배정된 리소스", "업무 상태"], relationships.map(r => [
      esc(r.requested_by_username) + " (user#" + r.requested_by + ")",
      esc(r.role_name) + " (#" + r.task_id + ")",
      esc(LABELS[r.resource_type] || r.resource_type) + ": " + esc(r.resource_name || (r.resource_type + " #" + r.resource_id)),
      esc(LABELS[r.task_status] || r.task_status),
    ])) +
    (relationships.length ? "" : '<p class="muted">확정된 배정 관계가 없습니다.</p>') +
    "</section>";

  document.getElementById("embedding-config-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("PUT", "/api/embedding-config", {
        provider: document.getElementById("ec-provider").value,
        model: document.getElementById("ec-model").value,
      });
      showMsg("global-msg", "임베딩 모델 설정을 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 사용자 요청(2026-11-10, "ollama, openai 선택시 가용한 모델이 자동
  // 표시되도록") — provider를 고르는 순간(초기 렌더 포함) 그 provider가
  // 실제로 보유한 모델을 조회해 <select id="ec-model-pick">에 채운다.
  // <input list>+<datalist> 콤보박스로 먼저 만들었으나(사용자 보고,
  // "openai, ollama 선택해도 임베딩 모델이 선택이 안됨") 브라우저마다
  // 드롭다운 화살표가 아예 안 보이거나 클릭으로 못 고르는 경우가
  // 있어 — 명확하게 클릭해서 고르는 <select>로 바꿨다. 고르면 그
  // 값을 실제 저장 대상인 `#ec-model` 입력란에 그대로 채워 넣고,
  // 그 입력란은 계속 자유 텍스트로 직접 고칠 수 있게 살려둔다(조회
  // 실패해도 수동 입력으로 저장 가능 — model_registry "불러오기"와
  // 같은 원칙, 핵심 기능은 절대 안 깨짐).
  async function _refreshEmbeddingModelCandidates() {
    const provider = document.getElementById("ec-provider").value;
    const statusEl = document.getElementById("ec-fetch-status");
    const pickEl = document.getElementById("ec-model-pick");
    const currentModel = document.getElementById("ec-model").value;
    pickEl.innerHTML = '<option value="">불러오는 중…</option>';
    statusEl.textContent = "";
    try {
      const resp = await api("GET", qs("/api/embedding-config/fetch", { provider }));
      if (!resp.models.length) {
        pickEl.innerHTML = '<option value="">(불러올 모델 없음 — 아래에 직접 입력)</option>';
        statusEl.textContent = "";
        return;
      }
      pickEl.innerHTML = '<option value="">모델 선택…(' + resp.models.length + '개)</option>' +
        resp.models.map(name =>
          '<option value="' + escAttr(name) + '"' + (name === currentModel ? " selected" : "") + '>' + esc(name) + '</option>'
        ).join("");
      statusEl.textContent = "";
    } catch (e) {
      pickEl.innerHTML = '<option value="">(목록을 못 불러옴 — 아래에 직접 입력)</option>';
      statusEl.textContent = e.message;
    }
  }
  document.getElementById("ec-provider").addEventListener("change", _refreshEmbeddingModelCandidates);
  document.getElementById("ec-model-pick").addEventListener("change", (ev) => {
    if (ev.target.value) document.getElementById("ec-model").value = ev.target.value;
  });
  _refreshEmbeddingModelCandidates();

  document.getElementById("rag-search-config-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("PUT", "/api/rag-search-config", {
        top_k: Number(document.getElementById("rsc-top-k").value),
        min_score: Number(document.getElementById("rsc-min-score").value),
      });
      showMsg("global-msg", "RAG 검색 설정을 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("kd-upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fileInput = document.getElementById("kd-file-input");
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const result = await apiUpload("/api/documents", formData);
      showMsg("global-msg", result.filename + " 업로드 완료(" + result.char_count + "자, " + result.chunk_count + "개 조각으로 색인됨).", "ok");
      _kbChunkOffset = 0;
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-delete-document]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await confirmModal("이 자료를 삭제합니다(색인된 조각도 함께 지워집니다). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/documents/" + btn.getAttribute("data-delete-document"));
        showMsg("global-msg", "삭제했습니다.", "ok");
        _kbChunkOffset = 0;
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  document.getElementById("kb-text-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = document.getElementById("kb-text-body").value;
    const label = document.getElementById("kb-text-label").value;
    try {
      const result = await api("POST", "/api/documents/text", label ? { text, label } : { text });
      showMsg("global-msg", "저장 완료(" + result.char_count + "자, " + result.chunk_count + "개 조각으로 색인됨).", "ok");
      _kbChunkOffset = 0;
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-delete-chunk]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await confirmModal("이 조각을 삭제합니다. 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/knowledge-chunks/" + btn.getAttribute("data-delete-chunk"));
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  const prevBtn = document.getElementById("kb-chunks-prev");
  if (prevBtn) prevBtn.addEventListener("click", () => {
    _kbChunkOffset = Math.max(0, _kbChunkOffset - _KB_CHUNK_LIMIT);
    refresh();
  });
  const nextBtn = document.getElementById("kb-chunks-next");
  if (nextBtn) nextBtn.addEventListener("click", () => {
    _kbChunkOffset += _KB_CHUNK_LIMIT;
    refresh();
  });
}

/* ---------- Crew 빌더(Agent 협업) ---------- */

// 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고, 에이전트
// 그룹(crew 구성)을 직관적으로 하기 위한... 개편") — 지금까지 실제
// 업무에 여러 Agent를 묶어 투입하려면 "① 팀부터 만들고(사전 등록) →
// ② 배정+승인 → ③ 협업 워크스페이스에서 실행"의 3단계를 거쳐야 했고,
// 그 전에 미리 조합을 테스트해보고 싶으면 결과가 저장되지 않는 별도
// 샌드박스(아래 `renderCrew`)로 가야 했다. 이 화면이 그 둘을 하나로
// 묶는다 — "업무에 바로 실행"(신규, 팀 없이 즉석 실행+실제 결과물
// 저장)과 "즉석 테스트"(기존 `renderCrew`, 변경 없이 그대로 위임) 두
// 모드를 탭으로 전환한다.
let _pendingCrewBuilderTaskId = null;

async function renderCrewBuilder(content) {
  const presetTaskId = _pendingCrewBuilderTaskId;
  _pendingCrewBuilderTaskId = null;
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  content.innerHTML =
    '<section class="card"><h2>Crew 빌더 — 여러 Agent를 묶어 실제로 협업시키기</h2>' +
    '<div class="actions">' +
    '<button class="small" id="crewbuilder-tab-task" type="button">업무에 바로 실행</button>' +
    '<button class="small" id="crewbuilder-tab-sandbox" type="button">즉석 테스트(저장 안 됨)</button>' +
    // 사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행 가시성")
    // — 로드맵 3단계. `GET /api/crew-runs`가 admin/manager 전용이라
    // 이 탭도 같은 권한으로만 보여준다.
    (isSupervisor ? '<button class="small" id="crewbuilder-tab-history" type="button">실행 이력</button>' : "") +
    '</div></section>' +
    '<div id="crewbuilder-body"></div>';
  const tabTask = document.getElementById("crewbuilder-tab-task");
  const tabSandbox = document.getElementById("crewbuilder-tab-sandbox");
  const tabHistory = document.getElementById("crewbuilder-tab-history");
  const body = document.getElementById("crewbuilder-body");
  const setActive = (mode) => {
    tabTask.classList.toggle("primary", mode === "task");
    tabSandbox.classList.toggle("primary", mode === "sandbox");
    if (tabHistory) tabHistory.classList.toggle("primary", mode === "history");
  };
  tabTask.onclick = () => { setActive("task"); _renderAdhocCrewComposer(body, null); };
  tabSandbox.onclick = () => { setActive("sandbox"); renderCrew(body); };
  if (tabHistory) tabHistory.onclick = () => { setActive("history"); _renderCrewHistory(body); };
  setActive("task");
  await _renderAdhocCrewComposer(body, presetTaskId);
}

// 사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행 가시성") —
// 지금까지 팀 Crew·즉석 Crew 실행은 끝나는 순간 협업 다이어그램·토큰
// 사용량·Gate 점수가 전부 사라졌다(admin 즉석 테스트 샌드박스에서만
// 실행 직후 한 번 보여주고 끝). `crew_history.py`가 영구 기록한
// 데이터를 그 샌드박스가 이미 쓰는 `renderCrewResult()`/
// `crewDiagramSvg()`에 그대로 먹여 똑같이 보여준다(새 렌더 로직 불필요
// — `crew_history.get_run()`이 그 두 함수가 기대하는 모양 그대로
// `agents`/`delegations`/`gate_scores`/`tcr`/`token_usage`/
// `final_output`을 돌려준다).
// 사용자 요청(2026-10-21, "Layer 2 — 실행 가시성 강화") — "실행
// 이력" 테이블과 실패 상세 화면 양쪽이 각자 `expected`(= manager가
// 있으면 agent_ids에서 매니저 제외)를 중복 계산하던 걸 하나로
// 합친다. 숫자 계산 자체는 기존과 동일 — 표시만 텍스트에서 진행
// 바로 바뀐다.
function crewRunExpectedCount(run) {
  const expected = run.manager_agent_id !== null
    ? run.agent_ids.length - 1 : run.agent_ids.length;
  return Math.max(expected, 1);
}

function crewRunProgressHtml(run) {
  const total = crewRunExpectedCount(run);
  const completed = run.status === "failed"
    ? run.completed_agent_ids.length : run.progress.length;
  const pct = Math.round((completed / total) * 100);
  return '<div class="progress-bar" style="width:80px;display:inline-block;vertical-align:middle;margin-right:0.4rem">' +
    '<div class="progress-bar-fill' + (run.status === "failed" ? " failed" : "") + '" style="width:' + pct + '%"></div>' +
    '</div>' + completed + '/' + total;
}

async function _renderCrewHistory(container, teamId) {
  // 사용자 요청(2026-10-29, 개선 계획 Phase 1 "Team 실행 이력 필터") —
  // teamId가 주어지면 그 팀의 실행만 좁혀 보여준다("팀 관리" 탭의
  // "실행 이력 보기"가 씀). 안 주면 기존처럼 전체(Crew 빌더의
  // "실행 이력" 탭).
  const runs = await api("GET", "/api/crew-runs" + (teamId ? "?team_id=" + teamId : ""));
  container.innerHTML =
    '<section class="card">' +
    '<p class="muted">팀 Agent 실행·즉석 실행 둘 다 여기 기록됩니다(즉석 테스트 샌드박스는 지금도 저장되지 않습니다). 진행 중인 실행은 "진행" 열에서 지금까지 끝난 Agent 수를 볼 수 있습니다 — 새로고침하면 갱신됩니다.</p>' +
    (runs.length
      ? table(["ID", "업무", "종류", "process", "상태", "진행", "시작", "동작"], runs.map(r => {
          const progressText = (r.status === "running" || r.status === "failed")
            ? crewRunProgressHtml(r)
            : "-";
          return [
            r.id,
            '<a href="#" data-goto-task="' + r.task_id + '">할 일 #' + r.task_id + '</a>',
            r.kind === "team" ? "팀(#" + r.team_id + ")" : "즉석",
            r.process,
            esc(label(r.status)),
            progressText,
            esc(r.created_at),
            '<button class="small" data-view-crew-run="' + r.id + '">보기</button>',
          ];
        }))
      : '<p class="muted">아직 기록된 실행이 없습니다.</p>') +
    '</section>' +
    '<div id="crew-result"></div>';
  container.querySelectorAll("[data-goto-task]").forEach(a => {
    a.onclick = (ev) => {
      ev.preventDefault();
      _pendingCrewBuilderTaskId = null; // 다른 화면으로 이동 — 빌더 상태 아님
      selectTab("briefs");
      showMsg("global-msg", '"업무 요청" 탭에서 해당 브리프를 찾아 할 일 #' + a.dataset.gotoTask + "의 상세를 확인하세요.", "ok");
    };
  });
  container.querySelectorAll("[data-view-crew-run]").forEach(btn => {
    btn.onclick = async () => {
      try {
        const run = await api("GET", "/api/crew-runs/" + btn.dataset.viewCrewRun);
        if (run.status === "running") {
          showMsg("global-msg", "아직 실행 중입니다(" + run.progress.length + "건 완료) — 끝난 뒤 다시 보기를 눌러주세요.", "ok");
          return;
        }
        if (run.status === "failed") {
          _renderFailedCrewRun(document.getElementById("crew-result"), run, container);
          return;
        }
        renderCrewResult(run);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

// 사용자 요청(2026-10-20, "실패 시 일부 재시도" — Crew 빌더 로드맵
// 5단계) — 실패한 실행도 "아무 것도 안 남았다"가 아니라, 이미 완료된
// Agent 수와 남은 Agent 수를 보여주고 재시도 버튼을 제공한다.
function _renderFailedCrewRun(resultBox, run, historyContainer) {
  const completedCount = run.completed_agent_ids.length;
  const remaining = crewRunExpectedCount(run) - completedCount;
  resultBox.innerHTML =
    '<section class="card">' +
    '<p class="msg error">이 실행은 실패했습니다: ' + esc(run.error || "원인 미상") + '</p>' +
    '<p class="muted">' + crewRunProgressHtml(run) + '명 완료 — ' +
    (completedCount > 0 ? '완료된 Agent의 결과물은 이미 이 업무의 결과물로 저장됐습니다.' : '아무도 완료하지 못했습니다.') +
    '</p>' +
    (remaining > 0
      ? '<button class="small" id="crew-run-retry-remaining">실패한 Agent만 재시도(' + remaining + '명)</button>'
      : "") +
    '</section>';
  const retryBtn = document.getElementById("crew-run-retry-remaining");
  if (!retryBtn) return;
  retryBtn.onclick = async () => {
    try {
      const queued = await withBusy(retryBtn, "재시도 중…(시간이 걸릴 수 있습니다)", () =>
        api("POST", "/api/crew-runs/" + run.id + "/retry-remaining", {}));
      const jobs = await Promise.all(queued.job_ids.map(id => pollExecutionJob(run.task_id, id)));
      if (jobs.every(j => j.status === "succeeded")) {
        showMsg("global-msg", "재시도가 완료됐습니다(" + jobs.length + "건) — 이 업무의 결과물로 쌓였습니다.", "ok");
      } else {
        const failed = jobs.filter(j => j.status === "failed").length;
        showMsg("global-msg", "재시도 중 " + failed + "건이 실패했습니다 — 실행 이력에서 다시 확인하세요.", "error");
      }
      await _renderCrewHistory(historyContainer);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };
}

// 팀 사전 등록 없이, Task 하나를 고르고 → Agent를 즉석으로 골라 →
// 바로 실행한다. 결과는 "팀 Agent 실행"과 똑같이 그 Task의
// deliverable+결재 체인으로 쌓인다(`POST .../execute-adhoc-crew`,
// `deliverables.start_adhoc_crew_execution()` 참고) — 다른 점은 팀이라는
// 전제조건이 없고, Agent별로 서로 다른 지시를 줄 수 있다는 것뿐이다.
async function _renderAdhocCrewComposer(container, presetTaskId) {
  const groups = await _fetchAllTasksGroupedByBrief();
  const hasTasks = groups.some(g => g.tasks.length);
  container.innerHTML =
    '<section class="card">' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>팀을 미리 등록·배정·승인하지 않아도, 이 업무의 참가자라면 Agent를 즉석으로 골라 바로 실행할 수 있습니다 — 결과는 이 업무의 결과물+결재 체인으로 그대로 쌓입니다.</li>' +
      '<li>Agent별로 서로 다른 지시를 줄 수 있습니다("팀 Agent 실행"은 참여 전원에게 같은 지시를 줍니다).</li>' +
      '<li>온보딩(active) 안 된 Agent는 선택할 수 없습니다.</li>' +
      '<li>실제 LLM API를 호출하므로 비용이 발생합니다(참여 Agent 수만큼 커질 수 있음).</li>' +
      '</ul>'
    ) +
    (hasTasks
      ? '<select id="crewbuilder-task-id" style="min-width:280px"><option value="">— 할 일 선택 —</option>' +
        _taskPickerOptionsHtml(groups) + '</select>'
      : '<p class="msg error">아직 할 일이 없습니다 — "업무 요청" 탭에서 먼저 만드세요.</p>') +
    '</section>' +
    '<div id="crewbuilder-task-form"></div>';
  const picker = document.getElementById("crewbuilder-task-id");
  if (!picker) return;
  const formBox = document.getElementById("crewbuilder-task-form");
  const renderForm = async () => {
    const taskId = Number(picker.value);
    if (!taskId) { formBox.innerHTML = ""; return; }
    await _renderAdhocCrewForm(formBox, taskId);
  };
  picker.addEventListener("change", renderForm);
  if (presetTaskId) {
    picker.value = String(presetTaskId);
    await renderForm();
  }
}

// 사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움") — 추천 Agent가
// 있으면 빈 행 1개 대신 추천된 조합으로 미리 채운 행 N개로 시작한다.
// 역할명을 그대로 쓴 기본 지시문을 붙여주되 자유롭게 수정할 수 있다.
async function _suggestedCrewRowsHtml(taskId, active, task) {
  const suggestions = await api("GET", "/api/tasks/" + taskId + "/suggested-agents").catch(() => []);
  if (!suggestions.length) return { html: crewRowHtml(active), suggested: false };
  const html = suggestions.map(s =>
    crewRowHtml(active, s.id, "'" + task.role_name + "' 업무를 수행하세요.")
  ).join("");
  return { html, suggested: true };
}

async function _renderAdhocCrewForm(formBox, taskId) {
  const agents = await api("GET", "/api/agent-pool");
  const active = agents.filter(a => a.status === "active");
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = isSupervisor ? await fetchModelsByProvider() : {};
  const task = await api("GET", "/api/tasks/" + taskId);
  const { html: rowsHtml, suggested } = await _suggestedCrewRowsHtml(taskId, active, task);
  formBox.innerHTML = active.length
    ? '<section class="card">' +
      (suggested
        ? '<p class="muted">이 업무에 적합해 보이는 Agent 조합을 미리 채웠습니다 — 자유롭게 수정/삭제/추가하세요. ' +
          '<button type="button" class="small" id="crewbuilder-adhoc-clear-suggestion">추천 비우고 새로 시작</button></p>'
        : "") +
      '<form id="crewbuilder-adhoc-form">' +
      '<div id="crewbuilder-adhoc-rows">' + rowsHtml + '</div>' +
      '<button type="button" class="small" id="crewbuilder-adhoc-add-row">+ Agent 추가</button>' +
      '<div class="inline" style="margin-top:0.5rem">' +
      '<select id="crewbuilder-adhoc-process"><option value="sequential">sequential(순서대로 전달)</option>' +
      '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
      '<select id="crewbuilder-adhoc-manager"><option value="">매니저 자동 배정(hierarchical 전용)</option>' +
      crewAgentOptionsHtml(active) + '</select>' +
      (isSupervisor
        ? '<select id="crewbuilder-adhoc-provider"><option value="">provider 자동 판별</option>' +
          '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
          '<option value="ollama">Ollama(로컬)</option></select>' + modelSelectHtml("crewbuilder-adhoc-model")
        : '<span class="muted">provider/model은 매니저·관리자만 선택할 수 있습니다(기본값 사용).</span>') +
      '<button class="primary" type="submit">이 업무에 바로 실행(비용 발생)</button>' +
      '</div>' + crewAdvancedOptionsHtml("crewbuilder-adhoc", isSupervisor, true) +
      '</form><div id="crewbuilder-adhoc-promotion"></div></section>'
    : '<p class="muted">온보딩된(활성) 에이전트가 없습니다 — "AI 에이전트 생성"·"AI 에이전트 관리" 탭에서 먼저 준비하세요.</p>';
  if (!active.length) return;
  if (isSupervisor) wireProviderModelSelect("crewbuilder-adhoc-provider", "crewbuilder-adhoc-model", modelsByProvider);
  wireCrewAdvancedOptions("crewbuilder-adhoc", modelsByProvider, isSupervisor);
  wireCrewRowRemovers();
  wireCrewProcessToggle("crewbuilder-adhoc-process", "crewbuilder-adhoc-rows", "crewbuilder-adhoc-self-review");
  document.getElementById("crewbuilder-adhoc-add-row").onclick = () => {
    document.getElementById("crewbuilder-adhoc-rows").insertAdjacentHTML("beforeend", crewRowHtml(active));
    wireCrewRowRemovers();
    wireCrewProcessToggle("crewbuilder-adhoc-process", "crewbuilder-adhoc-rows", "crewbuilder-adhoc-self-review");
  };
  const clearBtn = document.getElementById("crewbuilder-adhoc-clear-suggestion");
  if (clearBtn) {
    clearBtn.onclick = () => {
      document.getElementById("crewbuilder-adhoc-rows").innerHTML = crewRowHtml(active);
      wireCrewRowRemovers();
      wireCrewProcessToggle("crewbuilder-adhoc-process", "crewbuilder-adhoc-rows", "crewbuilder-adhoc-self-review");
      clearBtn.closest("p").remove();
    };
  }
  document.getElementById("crewbuilder-adhoc-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showMsg("global-msg", "", "");
    const rows = Array.from(formBox.querySelectorAll(".crew-row"));
    const agentIds = [];
    const taskDescriptions = [];
    const parallelAgentIds = [];
    for (const row of rows) {
      const agentId = Number(row.querySelector(".crew-row-agent").value);
      const taskDesc = row.querySelector(".crew-row-task").value.trim();
      if (!agentId) { showMsg("global-msg", "모든 줄에서 Agent를 선택하세요.", "error"); return; }
      if (!taskDesc) { showMsg("global-msg", "모든 줄에서 할 일을 입력하세요.", "error"); return; }
      agentIds.push(agentId);
      taskDescriptions.push(taskDesc);
      if (row.querySelector(".crew-row-parallel").checked) parallelAgentIds.push(agentId);
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 Agent는 선택해야 합니다.", "error"); return; }
    if (!(await confirmModal(agentIds.length + "개 Agent로 이 업무에 바로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?"))) return;
    const managerVal = document.getElementById("crewbuilder-adhoc-manager").value;
    const payload = Object.assign({
      agent_ids: agentIds,
      task_descriptions: taskDescriptions,
      process: document.getElementById("crewbuilder-adhoc-process").value,
      manager_agent_id: managerVal ? Number(managerVal) : null,
      parallel_agent_ids: parallelAgentIds,
    }, crewAdvancedOptionsPayload("crewbuilder-adhoc", isSupervisor, true));
    if (isSupervisor) {
      const provider = document.getElementById("crewbuilder-adhoc-provider").value;
      const model = modelSelectValue("crewbuilder-adhoc-model");
      if (provider) payload.provider = provider;
      if (model) payload.model = model;
    }
    const submitBtn = ev.submitter || formBox.querySelector("#crewbuilder-adhoc-form button[type=submit]");
    try {
      const queued = await withBusy(submitBtn, "실행 중…(시간이 걸릴 수 있습니다)", () =>
        api("POST", "/api/tasks/" + taskId + "/execute-adhoc-crew", payload));
      const jobs = await Promise.all(queued.job_ids.map(id => pollExecutionJob(taskId, id)));
      if (jobs.every(j => j.status === "succeeded")) {
        showMsg("global-msg", "Crew 실행이 완료됐습니다(" + jobs.length + "건) — 이 업무의 결과물로 쌓였습니다.", "ok");
        // 사용자 요청(2026-10-20, "Crew 빌더... 이 조합을 저장") — 잘
        // 돌아간 조합을 매번 처음부터 다시 고르지 않도록, 방금 실행한
        // Agent 구성 그대로 팀/파이프라인 단계로 저장할 수 있게 한다.
        await _renderCrewPromotionPanel(
          document.getElementById("crewbuilder-adhoc-promotion"),
          { agentIds, taskDescriptions, process: payload.process, managerAgentId: payload.manager_agent_id, active }
        );
      } else {
        const failed = jobs.filter(j => j.status === "failed").length;
        showMsg("global-msg", "Crew 실행 중 " + failed + "건이 실패했습니다 — 그 업무의 워크스페이스에서 진행 이력을 확인하세요.", "error");
      }
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// 사용자 요청(2026-10-20, "Crew 빌더... 이 조합을 저장") — 로드맵
// 2단계. 방금 실행한 Agent 구성(+process/매니저)을 팀 또는 파이프라인
// 단계로 승격한다 — 새 백엔드 없이 기존 API만 조합한다(팀: `teams.
// create_team`+`add_team_member`+`update_team_defaults`, 파이프라인:
// `pipeline_runtime.create_pipeline_template`+`add_stage`).
async function _renderCrewPromotionPanel(container, { agentIds, taskDescriptions, process, managerAgentId, active }) {
  // 팀/파이프라인 등록(POST /api/teams, /api/pipeline-templates 등)은
  // 전부 admin/manager 전용 엔드포인트다 — member는 애초에 저장할
  // 수단이 없으므로 패널 자체를 안 보여준다(눌러보고서야 403을 받는
  // 것보다 처음부터 안 보이는 쪽이 정직하다).
  if (!["admin", "manager"].includes(ME.role)) { container.innerHTML = ""; return; }
  const roleNameById = {};
  active.forEach(a => { roleNameById[a.id] = a.role_name; });
  const templates = await api("GET", "/api/pipeline-templates").catch(() => []);
  container.innerHTML =
    '<section class="card" style="margin-top:0.5rem"><h4>이 조합을 저장해 다음에 재사용</h4>' +
    '<form class="inline" id="crewbuilder-save-team-form">' +
    '<input id="crewbuilder-save-team-name" placeholder="팀 이름" required style="min-width:160px">' +
    '<button class="small" type="submit">이 조합을 팀으로 저장</button>' +
    '</form>' +
    '<form class="inline" id="crewbuilder-save-pipeline-form" style="margin-top:0.4rem;flex-wrap:wrap">' +
    '<select id="crewbuilder-save-pipeline-target">' +
    '<option value="">— 새 파이프라인 만들기 —</option>' +
    templates.map(t => '<option value="' + t.id + '">' + esc(t.name) + " (" + t.stage_count + "단계)</option>").join("") +
    '</select>' +
    '<input id="crewbuilder-save-pipeline-new-name" placeholder="새 파이프라인 이름" style="min-width:140px">' +
    '<input id="crewbuilder-save-pipeline-stage-name" placeholder="단계 이름" required style="min-width:140px">' +
    '<button class="small" type="submit">이 구성을 파이프라인 단계로 저장</button>' +
    '</form>' +
    (process === "hierarchical" && !managerAgentId
      ? '<p class="muted">매니저를 자동 배정으로 두고 실행했습니다 — 파이프라인 단계(병행 위임)는 매니저를 반드시 지정해야 해서, 이 조합은 파이프라인으로 저장할 수 없습니다. 매니저를 직접 골라 다시 실행하면 저장할 수 있습니다.</p>'
      : "") +
    "</section>";

  document.getElementById("crewbuilder-save-team-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = document.getElementById("crewbuilder-save-team-name").value.trim();
    if (!name) return;
    try {
      const team = await api("POST", "/api/teams", { name });
      for (const agentId of agentIds) {
        await api("POST", "/api/teams/" + team.id + "/members", { member_type: "agent", member_id: agentId });
      }
      await api("PATCH", "/api/teams/" + team.id, {
        default_process: process,
        default_manager_agent_id: managerAgentId || null,
      });
      showMsg("global-msg", '팀 "' + esc(name) + '"으로 저장했습니다 — "팀 관리" 탭에서 확인·배정할 수 있습니다.', "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const pipelineForm = document.getElementById("crewbuilder-save-pipeline-form");
  if (!pipelineForm) return;
  const targetSelect = document.getElementById("crewbuilder-save-pipeline-target");
  const newNameInput = document.getElementById("crewbuilder-save-pipeline-new-name");
  const syncNewNameVisibility = () => { newNameInput.style.display = targetSelect.value ? "none" : ""; };
  targetSelect.addEventListener("change", syncNewNameVisibility);
  syncNewNameVisibility();

  pipelineForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (process === "hierarchical" && !managerAgentId) {
      showMsg("global-msg", "매니저를 지정하지 않고 실행한 조합은 파이프라인 단계로 저장할 수 없습니다(병행 위임은 매니저가 필수).", "error");
      return;
    }
    const stageName = document.getElementById("crewbuilder-save-pipeline-stage-name").value.trim();
    if (!stageName) return;
    try {
      let templateId = Number(targetSelect.value) || null;
      if (!templateId) {
        const newName = newNameInput.value.trim();
        if (!newName) { showMsg("global-msg", "새 파이프라인 이름을 입력하세요.", "error"); return; }
        const tpl = await api("POST", "/api/pipeline-templates", { name: newName });
        templateId = tpl.id;
      }
      const mode = agentIds.length === 1 ? "single" : (process === "hierarchical" ? "parallel_delegated" : "sequential_relay");
      const stageAgents = agentIds.map((id, i) => ({
        role_name: roleNameById[id],
        instructions: taskDescriptions[i] || "",
      }));
      const body = { name: stageName, mode, agents: stageAgents };
      if (mode === "parallel_delegated") body.lead_role_name = roleNameById[managerAgentId];
      await api("POST", "/api/pipeline-templates/" + templateId + "/stages", body);
      showMsg("global-msg", '파이프라인 단계 "' + esc(stageName) + '"로 저장했습니다 — "파이프라인 관리" 탭에서 확인·조정할 수 있습니다.', "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// 사용자 보고(2026-10-06, "crew 실행 UI/UX를 개선해줘 — 에이전트 ID를
// 알 수 없음 등") — 이전엔 위 표에서 ID를 직접 읽어 textarea에
// "1: 할 일" 식으로 손으로 옮겨 적어야 했다(오타·형식 실수 위험, 이
// 세션에서 반복 고친 "직접 입력 대신 실제 데이터에서 선택" 패턴과 같은
// 문제). Agent별로 드롭다운+할 일 입력 행을 두고 "+ Agent 추가"로 늘리는
// 방식으로 바꾼다 — 드롭다운 자체가 "#id 역할명(스킬)"을 보여줘 ID를
// 몰라도 된다.
// 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... AI 에이전트
// 지정 항목이나 호출시에도 활용될 수 있도록") — Agent를 고르는 모든
// 드롭다운/라벨이 지정한 이름을 역할명보다 우선 보여주도록 한곳에
// 모은다(지정 안 했으면 역할명 그대로 — 호출부 변경 없이 자동 적용).
function agentDisplayLabel(a) {
  return a.name ? esc(a.name) + " (" + esc(a.role_name) + ")" : esc(a.role_name);
}

// 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고..." — Crew
// 빌더 로드맵 4단계, "Crew 레벨 고급 옵션") — 세 Crew 실행 폼(즉석
// 테스트/업무에 바로 실행/팀 Agent 실행)이 전부 같은 고급 설정(메모리·
// planning·매니저 전용 LLM)을 쓰므로 한곳에 모아 중복 없이 재사용한다.
// `memory`는 비용을 늘리는 방향이 아니라(OPENAI_API_KEY 없이 쓰기 위한
// 방어적 toggle) 모두에게 열어두고, `planning`/매니저 전용 LLM은
// provider/model과 같은 이유로 admin/manager 전용이다(백엔드도 똑같이
// 막는다 — `app.py`의 "팀원은... 직접 선택할 수 없습니다" 검사 참고).
// `enableSelfReview`(사용자 요청, 2026-11-04, "Agent 자가비평" 설계) —
// `self_review`는 `AdhocCrewExecuteRequest`에만 있는 필드라(team-crew·
// 샌드박스는 지원 안 함), 이 공유 헬퍼 3개에 opt-in 파라미터로만
// 추가한다 — 호출부가 안 넘기면(undefined, falsy) 기존 두 호출부는
// 렌더링·payload 모두 한 글자도 안 바뀐다.
function crewAdvancedOptionsHtml(prefix, isSupervisor, enableSelfReview) {
  const supervisorFields = isSupervisor
    ? '<div class="inline" style="margin-top:0.3rem">' +
      '<label><input type="checkbox" id="' + prefix + '-planning"> 사전 계획(planning, 추가 LLM 호출 비용 발생)</label>' +
      '</div>' +
      '<div class="inline" style="margin-top:0.3rem">' +
      '<span class="muted">매니저 전용 LLM(매니저 자동 배정 시에만 적용) — </span>' +
      '<select id="' + prefix + '-manager-provider"><option value="">워커와 동일</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      modelSelectHtml(prefix + "-manager-model") +
      "</div>"
    : "";
  // self_review는 provider/model 같은 비용 선택권이 없는 단순 토글이라
  // supervisorFields(관리자 전용)가 아니라 항상 보이는 블록에 둔다 —
  // member도 설정할 수 있어야 한다(백엔드 RBAC과 일치).
  const selfReviewField = enableSelfReview
    ? '<div class="inline" style="margin-top:0.3rem">' +
      '<label><input type="checkbox" id="' + prefix + '-self-review"> 자기 검토(완료 후 같은 Agent가 자신의 초안을 요구사항과 대조해 한 번 더 수정 — sequential·비병렬 전용)</label>' +
      '</div>'
    : "";
  return '<details class="muted" style="margin-top:0.4rem">' +
    "<summary>고급 설정(메모리" + (isSupervisor ? "·사전 계획·매니저 전용 LLM" : "") + ")</summary>" +
    '<div class="inline" style="margin-top:0.3rem">' +
    '<label><input type="checkbox" id="' + prefix + '-memory" checked> 메모리 사용(끄면 provider가 openai가 아니어도 OPENAI_API_KEY 없이 실행할 수 있습니다)</label>' +
    "</div>" +
    selfReviewField +
    supervisorFields +
    "</details>";
}

function wireCrewAdvancedOptions(prefix, modelsByProvider, isSupervisor) {
  if (isSupervisor) {
    wireProviderModelSelect(prefix + "-manager-provider", prefix + "-manager-model", modelsByProvider);
  }
}

function crewAdvancedOptionsPayload(prefix, isSupervisor, enableSelfReview) {
  const payload = { memory: document.getElementById(prefix + "-memory").checked };
  if (isSupervisor) {
    payload.planning = document.getElementById(prefix + "-planning").checked;
    const managerProvider = document.getElementById(prefix + "-manager-provider").value;
    const managerModel = modelSelectValue(prefix + "-manager-model");
    if (managerProvider) payload.manager_provider = managerProvider;
    if (managerModel) payload.manager_model = managerModel;
  }
  if (enableSelfReview) {
    payload.self_review = document.getElementById(prefix + "-self-review").checked;
  }
  return payload;
}

function crewAgentOptionsHtml(active) {
  // 사용자 요청(2026-10-27, "Crew 빌더 - 즉석 테스트... ID 대신 agent
  // code가, 추가로 에이전트명 표시되도록") — 드롭다운 라벨의 "#id"도
  // 같은 화면에서 노출되는 정수 PK라 코드로 바꾼다(value는 그대로 id —
  // 제출 payload는 안 바뀜).
  return active.map(a =>
    '<option value="' + a.id + '">' + esc(a.code || ("#" + a.id)) + " " + agentDisplayLabel(a) +
    (a.skill_tags.length ? " (" + esc(a.skill_tags.join(", ")) + ")" : "") + "</option>"
  ).join("");
}

// 사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움" — Crew 빌더 로드맵
// 5단계) — `presetAgentId`/`presetTaskDesc`가 주어지면(추천 결과로
// 채울 때) 행이 그 값으로 미리 선택/입력된 채로 시작한다. 생략하면
// 기존처럼 빈 행(수동 흐름 그대로 보존).
//
// 사용자 요청(2026-10-20, "독립 작업 병렬 실행") — 체크박스 1개 추가.
// `process="sequential"`에서만 의미가 있어 hierarchical로 바뀌면
// `wireCrewProcessToggle()`이 비활성화한다(아래 참고).
function crewRowHtml(active, presetAgentId, presetTaskDesc) {
  const agentOptions = active.map(a =>
    '<option value="' + a.id + '"' + (presetAgentId === a.id ? " selected" : "") + '>' + esc(a.code || ("#" + a.id)) + " " +
    agentDisplayLabel(a) + (a.skill_tags.length ? " (" + esc(a.skill_tags.join(", ")) + ")" : "") + "</option>"
  ).join("");
  return '<div class="inline crew-row" style="margin-bottom:0.3rem">' +
    '<select class="crew-row-agent" style="min-width:220px"><option value="">— Agent 선택 —</option>' +
    agentOptions + "</select>" +
    '<input class="crew-row-task" placeholder="이 Agent가 할 일" style="flex:1;min-width:200px"' +
    (presetTaskDesc ? ' value="' + escAttr(presetTaskDesc) + '"' : "") + ">" +
    '<label class="muted" style="white-space:nowrap"><input type="checkbox" class="crew-row-parallel"> 병렬(독립 작업)</label>' +
    '<button type="button" class="small danger crew-row-remove">×</button>' +
    "</div>";
}

// hierarchical(매니저 조율)에서는 "병렬" 지정이 적용될 지점이 없다
// (`crew_runtime.run_crew()` 참고) — process select가 바뀔 때마다
// 체크박스를 비활성화/활성화해 혼동을 막는다.
// `selfReviewCheckboxId`(사용자 요청, 2026-11-04, "Agent 자가비평" 설계) —
// 백엔드가 `self_review`를 `process='sequential'`이고 어느 행도 "병렬"
// 체크 안 돼 있을 때만 허용하므로(`app.py`의 execute-adhoc-crew 400
// 체크와 동일), 그 조건이 깨지면 체크박스를 미리 끄고 비활성화해
// 사용자가 400을 직접 맞닥뜨리기 전에 UI에서 막는다. 생략하면
// (team-crew/샌드박스 호출부) 기존 동작과 동일.
function wireCrewProcessToggle(processSelectId, rowsContainerId, selfReviewCheckboxId) {
  const select = document.getElementById(processSelectId);
  const container = document.getElementById(rowsContainerId);
  if (!select || !container) return;
  const selfReviewCb = selfReviewCheckboxId ? document.getElementById(selfReviewCheckboxId) : null;
  const sync = () => {
    const isSequential = select.value === "sequential";
    container.querySelectorAll(".crew-row-parallel").forEach(cb => {
      cb.disabled = !isSequential;
      if (!isSequential) cb.checked = false;
    });
    if (selfReviewCb) {
      const anyParallel = !!container.querySelector(".crew-row-parallel:checked");
      selfReviewCb.disabled = !isSequential || anyParallel;
      if (selfReviewCb.disabled) selfReviewCb.checked = false;
    }
  };
  select.addEventListener("change", sync);
  if (selfReviewCb) container.addEventListener("change", sync);
  sync();
}

function wireCrewRowRemovers() {
  document.querySelectorAll(".crew-row-remove").forEach(btn => {
    btn.onclick = () => {
      const rows = document.querySelectorAll(".crew-row");
      if (rows.length <= 1) { showMsg("global-msg", "최소 한 Agent는 있어야 합니다.", "error"); return; }
      btn.closest(".crew-row").remove();
    };
  });
}

async function renderCrew(content) {
  const agents = await api("GET", "/api/agent-pool");
  const active = agents.filter(a => a.status === "active");
  const modelsByProvider = await fetchModelsByProvider();
  content.innerHTML =
    '<section class="card"><h2>Crew 실행(즉석 테스트) — 여러 Agent를 묶어 실제로 협업시키기</h2>' +
    '<p class="muted"><b>이 화면은 업무(할 일)와 연결되지 않는 즉석 실험용입니다</b> — 결과가 어떤 할 일의 결과물로도 저장되지 않고, 끝나면 그걸로 끝입니다.</p>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>실제 업무에 여러 Agent를 역할별로 묶어 투입하려면: ① "AI 에이전트 생성"에서 한 브리프의 여러 할 일을 같이 선택해 역할별 Agent를 만들고 그 결과 화면에서 "팀으로 묶기"를 누르거나, 이미 있는 Agent들을 "팀 관리"에서 팀으로 등록한 뒤 ② "업무 요청"의 할 일 상세에서 그 팀을 배정 → 결재까지 마치면 ③ 협업 워크스페이스의 "팀 Agent 실행(Crew)" 버튼으로 실행하세요 — 그 경로는 결과가 그 할 일의 결과물+결재 체인으로 그대로 쌓입니다.</li>' +
      '<li>아래는 그 전에 조합·프롬프트를 미리 테스트해보고 싶을 때만 쓰세요.</li>' +
      '<li>Agent를 드롭다운으로 고르고 그 옆에 할 일을 입력하세요 — "+ Agent 추가"로 참여 인원을 늘릴 수 있습니다.</li>' +
      '<li><b>sequential</b>이면 앞줄 에이전트의 산출물이 다음 에이전트에게 자동으로 전달됩니다(순서대로 릴레이).</li>' +
      '<li><b>hierarchical</b>은 "매니저 Agent"를 지정하면 그 매니저가 작업을 조율·위임만 하고 직접 산출물을 내지는 않습니다(비워두면 CrewAI가 자동으로 매니저 LLM을 하나 배정) — 아래 결과의 "Agent별 산출물"에서 매니저 행은 항상 "(없음)"으로 나옵니다.</li>' +
      '<li>모든 Agent는 위임(allow_delegation)이 켜져 있어 필요하면 서로에게 실제로 질문·재위임할 수 있고, 그 내역이 "실제 위임/협업 발생 내역"에 기록됩니다.</li>' +
      '<li>온보딩(active) 안 된 Agent는 드롭다운에 안 뜨고 지정할 수도 없습니다.</li>' +
      '<li>실제 LLM API를 호출하므로 비용이 발생합니다(참여 Agent 수만큼 커질 수 있음).</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "Crew 빌더 - 즉석 테스트에서 테이블에 ID
    // 표시됨, ID 대신 agent code가, 추가로 에이전트명 표시되도록") —
    // "AI 에이전트 관리" 테이블과 같은 표기(코드+이름)로 맞춘다. 정수
    // id는 드롭다운(`crewAgentOptionsHtml`)에서 여전히 내부적으로
    // 쓰이므로 기능은 안 바뀐다.
    (active.length
      ? table(["코드", "이름", "역할", "스킬"], active.map(a => [
          esc(a.code || "-"),
          a.name ? esc(a.name) : '<span class="muted">(미지정)</span>',
          esc(a.role_name), esc(a.skill_tags.join(", ")),
        ]))
      : '<p class="muted">온보딩된(활성) 에이전트가 없습니다 — 먼저 "AI 에이전트 생성"·"AI 에이전트 관리" 탭에서 준비하세요.</p>') +
    '<form id="crew-form">' +
    '<div id="crew-rows">' + (active.length ? crewRowHtml(active) : "") + "</div>" +
    (active.length ? '<button type="button" class="small" id="crew-add-row">+ Agent 추가</button>' : "") +
    '<div class="inline" style="margin-top:0.5rem">' +
    '<select id="crew-process"><option value="sequential">sequential(순서대로 전달)</option>' +
    '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
    '<select id="crew-manager"><option value="">매니저 자동 배정(hierarchical 전용)</option>' +
    crewAgentOptionsHtml(active) + "</select>" +
    '<select id="crew-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    modelSelectHtml("crew-model") +
    '<button class="primary" type="submit"' + (active.length ? "" : " disabled") + '>Crew 실행(비용 발생)</button>' +
    "</div></form>" +
    crewAdvancedOptionsHtml("crew", true) +
    '<div id="crew-result"></div></section>';

  wireProviderModelSelect("crew-provider", "crew-model", modelsByProvider);
  wireCrewAdvancedOptions("crew", modelsByProvider, true);
  wireCrewRowRemovers();
  wireCrewProcessToggle("crew-process", "crew-rows");
  const addRowBtn = document.getElementById("crew-add-row");
  if (addRowBtn) {
    addRowBtn.onclick = () => {
      document.getElementById("crew-rows").insertAdjacentHTML("beforeend", crewRowHtml(active));
      wireCrewRowRemovers();
      wireCrewProcessToggle("crew-process", "crew-rows");
    };
  }

  document.getElementById("crew-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showMsg("global-msg", "", "");  // 이전 시도의 에러 배너가 남아 새 결과와 헷갈리지 않도록 지운다
    const rows = Array.from(document.querySelectorAll(".crew-row"));
    const agentIds = [];
    const taskDescriptions = [];
    const parallelAgentIds = [];
    for (const row of rows) {
      const agentId = Number(row.querySelector(".crew-row-agent").value);
      const taskDesc = row.querySelector(".crew-row-task").value.trim();
      if (!agentId) { showMsg("global-msg", "모든 줄에서 Agent를 선택하세요.", "error"); return; }
      if (!taskDesc) { showMsg("global-msg", "모든 줄에서 할 일을 입력하세요.", "error"); return; }
      agentIds.push(agentId);
      taskDescriptions.push(taskDesc);
      if (row.querySelector(".crew-row-parallel").checked) parallelAgentIds.push(agentId);
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 Agent는 선택해야 합니다.", "error"); return; }
    const managerVal = document.getElementById("crew-manager").value;
    if (!(await confirmModal(agentIds.length + "개 Agent로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?"))) return;

    const submitBtn = ev.submitter || document.querySelector("#crew-form button[type=submit]");
    try {
      const result = await withBusy(submitBtn, "실행 중…(시간이 걸릴 수 있습니다)", () => api("POST", "/api/crew/run", Object.assign({
        agent_ids: agentIds,
        task_descriptions: taskDescriptions,
        process: document.getElementById("crew-process").value,
        manager_agent_id: managerVal ? Number(managerVal) : null,
        provider: document.getElementById("crew-provider").value || null,
        model: modelSelectValue("crew-model"),
        parallel_agent_ids: parallelAgentIds,
      }, crewAdvancedOptionsPayload("crew", true))));
      renderCrewResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

/* 사용자 요청(2026-10-06, "crew 실행 UI/UX를 개선해줘 — 묶인 Agent간
   협업 관계를 다이어그램으로 표시") — 지금까지는 "Agent별 산출물"·"위임
   내역" 표만 있어, 몇 명이 어떤 구조(순차 릴레이 vs 매니저 조율)로
   묶였는지, 실제로 누가 누구에게 위임했는지를 표를 읽어 머릿속으로
   재구성해야 했다. 실선(회색)은 요청 시점에 이미 정해진 구조(sequential
   순서 전달 / hierarchical 조율 관계) — 항상 그린다. 점선(주황)은
   `result.delegations`에 실제로 기록된 위임만 그린다(추측 금지,
   "실제 발동 증거를 남긴다" 원칙과 같은 정신) — 대상이
   확인되지 않으면(알 수 없는 동료를 호출해 CrewAI가 에러를 반환한
   경우 등) 화살표 대신 아래 "확인 안 된 위임" 목록에 원문을 남긴다.
   외부 차트 라이브러리 없이 순수 inline SVG로 그린다(이 앱의 기존
   무의존성 원칙과 동일). */
// 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — crewDiagramSvg()
// 의 노드 좌표 계산(구조선 — 실행 결과와 무관하게 process+매니저+멤버
// 목록만으로 계산 가능한 부분)을 떼어내 "팀 관리" 화면의 실행 전
// 미리보기 캔버스와 공유한다. `agentList`는 `role_name`(필수, 위치를
// 찾는 키 — CrewAI가 실제로 보고하는 coworker 문자열과 일치해야 해서
// 건드리면 안 됨)을 가진 객체 배열. `managerMember`는 `agentList`의
// 원소 중 하나(명시적 매니저) 또는 null/undefined(hierarchical인데
// 매니저 미지정 — CrewAI 자동 배정, 합성 노드로 표시). 이 함수는
// 순수 좌표 계산만 하고 SVG 문자열은 만들지 않는다 — 호출자(아래
// crewDiagramSvg, 새 팀 캔버스)가 각자 자신에게 맞는 방식(raw SVG
// `<line>` vs Drawflow `addConnection()`)으로 그린다.
function _crewStructuralLayout(agentList, process, managerMember) {
  const isHierarchical = process === "hierarchical";
  const nodeW = 150, nodeH = 44, gapX = 190;
  const positions = {};
  let managerNode = null;
  let workers = agentList;
  let svgWidth, svgHeight;

  if (isHierarchical) {
    workers = agentList.filter(a => a !== managerMember);
    svgWidth = Math.max(workers.length, 1) * gapX + 20;
    svgHeight = 170;
    managerNode = managerMember || { role_name: "매니저(LLM 자동 배정)", synthetic: true };
    positions[managerNode.role_name] = { x: svgWidth / 2 - nodeW / 2, y: 16 };
    workers.forEach((a, i) => { positions[a.role_name] = { x: i * gapX + 20, y: 112 }; });
  } else {
    svgWidth = Math.max(agentList.length, 1) * gapX + 20;
    svgHeight = 90;
    agentList.forEach((a, i) => { positions[a.role_name] = { x: i * gapX + 20, y: 23 }; });
  }
  return { positions, svgWidth, svgHeight, nodeW, nodeH, gapX, isHierarchical, managerNode, workers };
}

// 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — "팀 관리"
// 화면에서 `_crewStructuralLayout()`이 계산한 좌표를 Drawflow 캔버스에
// 그린다. 캔버스는 서버 상태(process/매니저/순서)의 투영이다 — 연결선은
// 항상 자동으로 그려지고, 사용자가 직접 선을 긋거나 지우지 않는다.
// 사용자가 하는 일은 노드를 드래그해 순서를 바꾸거나(sequential에서만
// 의미 있음) "매니저로 지정"을 누르는 것뿐 — 그 결과가 서버에 반영되면
// `refresh()`가 전체를 다시 그려 항상 정돈된 모습으로 돌아온다.
// Drawflow CDN 로드에 실패해도(오프라인 등) 조용히 아무 것도 안 그리고
// 끝난다 — 기존 멤버 표 CRUD는 전혀 영향받지 않는다(이 설계의 핵심
// 안전장치).
function _initTeamWorkflowCanvas(container, team, activeAgentMembers) {
  if (!container || typeof Drawflow === "undefined") return null;
  container.innerHTML = "";
  const editor = new Drawflow(container);
  // 사용자 보고(2026-10-21, "노드 크기 변경 기능... 지금 노드 크기
  // 조절은 안되고 너무 커 모든 노드가 표시되지 않음") — _crewStructuralLayout
  // 의 nodeW=150/gapX=190은 원래 SVG(viewBox로 컨테이너 폭에 자동
  // 맞춰짐)용 상수라, Drawflow의 실제 DOM 좌표에 그대로 쓰면 멤버가
  // 몇 명만 넘어도 컨테이너 밖으로 넘친다. zoom_min을 넉넉히 낮게 둬
  // 아래 fitToContainer()가 멤버 수가 많아도 전부 보이게 축소할 수
  // 있게 한다.
  editor.zoom_min = 0.2;
  editor.zoom_max = 1.6;
  editor.zoom_value = 0.1;
  editor.start();

  const process = team.default_process || "sequential";
  const managerMember = activeAgentMembers.find(a => a.id === team.default_manager_agent_id) || null;
  const layout = _crewStructuralLayout(activeAgentMembers, process, managerMember);
  const nodeIdByRoleName = {};

  const nodeHtml = (a, isManager) =>
    '<div class="team-canvas-node' + (isManager ? " team-canvas-node-manager" : "") + '">' +
    '<div class="team-canvas-node-label">' + esc(agentDisplayLabel(a)) + '</div>' +
    (process === "hierarchical" && !isManager
      ? '<button type="button" class="small" data-set-manager="' + a.id + '">매니저로 지정</button>'
      : "") +
    "</div>";

  const allNodes = layout.isHierarchical ? [layout.managerNode].concat(layout.workers) : activeAgentMembers;
  allNodes.forEach(a => {
    if (a.synthetic) return;  // CrewAI 자동 배정 매니저 — 실제 Agent가 아니라 노드로 안 그림
    const pos = layout.positions[a.role_name];
    const isManager = a === layout.managerNode;
    const nodeId = editor.addNode(
      "agent", 1, 1, pos.x, pos.y,
      isManager ? "team-canvas-manager" : "team-canvas-worker",
      { agentId: a.id }, nodeHtml(a, isManager)
    );
    nodeIdByRoleName[a.role_name] = nodeId;
  });

  if (layout.isHierarchical && !layout.managerNode.synthetic) {
    layout.workers.forEach(a => {
      editor.addConnection(
        nodeIdByRoleName[layout.managerNode.role_name], nodeIdByRoleName[a.role_name],
        "output_1", "input_1"
      );
    });
  } else if (!layout.isHierarchical) {
    for (let i = 0; i < activeAgentMembers.length - 1; i++) {
      editor.addConnection(
        nodeIdByRoleName[activeAgentMembers[i].role_name],
        nodeIdByRoleName[activeAgentMembers[i + 1].role_name],
        "output_1", "input_1"
      );
    }
  }

  container.querySelectorAll("[data-set-manager]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/teams/" + team.id, {
          default_manager_agent_id: Number(btn.dataset.setManager),
        });
        showMsg("global-msg", "매니저를 지정했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  if (!layout.isHierarchical && activeAgentMembers.length > 1) {
    editor.on("nodeMoved", async () => {
      let exported;
      try {
        exported = editor.export().drawflow.Home.data;
      } catch (e) { return; }  // Drawflow 내부 구조가 예상과 다르면 조용히 무시(기존 순서 유지)
      const nodes = Object.values(exported);
      nodes.sort((a, b) => a.pos_x - b.pos_x);
      const orderedRowIds = nodes
        .map(n => {
          const agent = activeAgentMembers.find(x => x.id === n.data.agentId);
          return agent ? agent.teamMemberRowId : null;
        })
        .filter(Boolean);
      if (orderedRowIds.length !== activeAgentMembers.length) return;  // 안전장치 — 집합이 안 맞으면 저장 안 함
      try {
        await api("PATCH", "/api/teams/" + team.id + "/members/order", {
          member_row_ids: orderedRowIds,
        });
        showMsg("global-msg", "순서를 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }

  // Drawflow는 zoom_in()/zoom_out()(0.1 단위)만 공개 API로 제공하고
  // 현재 배율을 직접 읽는 API는 없다 — 시작값은 항상 1.0(Drawflow
  // 기본값)이므로 이후 변화는 이 클로저에서 직접 추적한다.
  let currentZoom = 1.0;
  const applyZoomSteps = (targetZoom) => {
    const clamped = Math.max(editor.zoom_min, Math.min(editor.zoom_max, targetZoom));
    const steps = Math.round((clamped - currentZoom) / editor.zoom_value);
    for (let i = 0; i < Math.abs(steps); i++) {
      if (steps < 0) editor.zoom_out(); else editor.zoom_in();
    }
    currentZoom = clamped;
  };
  const fitToContainer = () => {
    const w = container.clientWidth || 600;
    const h = container.clientHeight || 260;
    applyZoomSteps(Math.min(1, w / layout.svgWidth, h / layout.svgHeight));
  };
  fitToContainer();  // 최초 렌더부터 전체 노드가 보이게

  return {
    zoomIn: () => applyZoomSteps(currentZoom + editor.zoom_value),
    zoomOut: () => applyZoomSteps(currentZoom - editor.zoom_value),
    fit: fitToContainer,
  };
}

function crewDiagramSvg(result) {
  const agents = result.agents;
  const manager = agents.find(a => a.output === null) || null;
  const layout = _crewStructuralLayout(agents, result.process, manager);
  const { positions, svgWidth, nodeW, nodeH, isHierarchical, managerNode, workers } = layout;
  let maxY = layout.svgHeight;  // 위임 곡선이 마지막 행 아래로 처지면 viewBox가 그만큼 커져야 잘리지 않는다.

  let body = "";

  if (isHierarchical) {
    const mPos = positions[managerNode.role_name];
    workers.forEach(a => {
      const wPos = positions[a.role_name];
      body += '<line x1="' + (mPos.x + nodeW / 2) + '" y1="' + (mPos.y + nodeH) + '" x2="' + (wPos.x + nodeW / 2) + '" y2="' + wPos.y +
        '" stroke="#999" stroke-width="2" marker-end="url(#crew-arrow-structural)" />';
    });
  } else {
    for (let i = 0; i < agents.length - 1; i++) {
      const p1 = positions[agents[i].role_name], p2 = positions[agents[i + 1].role_name];
      body += '<line x1="' + (p1.x + nodeW) + '" y1="' + (p1.y + nodeH / 2) + '" x2="' + p2.x + '" y2="' + (p2.y + nodeH / 2) +
        '" stroke="#999" stroke-width="2" marker-end="url(#crew-arrow-structural)" />';
    }
  }

  const unresolved = [];
  const pairs = {};
  (result.delegations || []).forEach(d => {
    if (!d.to_agent_role || !positions[d.to_agent_role] || !positions[d.from_agent_role]) {
      unresolved.push(d);
      return;
    }
    const key = d.from_agent_role + "\\u2192" + d.to_agent_role;
    if (!pairs[key]) pairs[key] = { from: d.from_agent_role, to: d.to_agent_role, tools: [] };
    if (!pairs[key].tools.includes(d.tool)) pairs[key].tools.push(d.tool);
  });
  Object.values(pairs).forEach(pair => {
    const p1 = positions[pair.from], p2 = positions[pair.to];
    const x1 = p1.x + nodeW / 2, y1 = p1.y + nodeH, x2 = p2.x + nodeW / 2, y2 = p2.y + nodeH;
    const midX = (x1 + x2) / 2, midY = Math.max(y1, y2) + 26;
    maxY = Math.max(maxY, midY + 14);  // 라벨 텍스트 한 줄 높이만큼 여유를 더 둔다.
    body += '<path d="M' + x1 + "," + y1 + " Q" + midX + "," + midY + " " + x2 + "," + y2 +
      '" stroke="#b45309" stroke-width="2" stroke-dasharray="4,3" fill="none" marker-end="url(#crew-arrow-delegation)" />' +
      '<text x="' + midX + '" y="' + (midY - 4) + '" font-size="10" fill="#b45309" text-anchor="middle">' + esc(pair.tools.join(", ")) + "</text>";
  });

  const allNodes = isHierarchical ? [managerNode].concat(workers) : agents;
  allNodes.forEach(a => {
    const pos = positions[a.role_name];
    const isManagerNode = a === managerNode;
    // 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정... 호출시(채팅
    // 포함)에도 활용될 수 있도록") — 다이어그램 라벨만 이름을 우선
    // 보여준다. `positions`/위임 매칭 키는 여전히 role_name 그대로다
    // (CrewAI가 실제로 보고하는 coworker 문자열과 일치해야 해서 건드리면
    // 위임 선 자체가 안 그려진다).
    const rawLabel = a.name || a.role_name;
    const label = rawLabel.length > 18 ? rawLabel.slice(0, 17) + "…" : rawLabel;
    body += '<rect x="' + pos.x + '" y="' + pos.y + '" width="' + nodeW + '" height="' + nodeH + '" rx="8" ' +
      'fill="' + (isManagerNode ? "#fef3c7" : "#eef1f6") + '" stroke="' + (isManagerNode ? "#b45309" : "#888") + '" stroke-width="1.5" />' +
      '<text x="' + (pos.x + nodeW / 2) + '" y="' + (pos.y + nodeH / 2 + 4) + '" font-size="12" text-anchor="middle" fill="#222">' +
      esc(label) + "</text>";
  });

  const svg = '<svg viewBox="0 0 ' + svgWidth + " " + maxY + '" style="width:100%;max-width:' + svgWidth + 'px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg">' +
    "<defs>" +
    '<marker id="crew-arrow-structural" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#999"/></marker>' +
    '<marker id="crew-arrow-delegation" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#b45309"/></marker>' +
    "</defs>" + body + "</svg>";
  return { svg, unresolved };
}

async function renderCrewResult(result) {
  const box = document.getElementById("crew-result");
  const diagram = crewDiagramSvg(result);
  // 사용자 요청(2026-11-05, 로드맵 8번 "Crew 빌더 diff 모드") — 이
  // Task의 지난 Crew 실행들을 가져와 "바로 이전 실행"을 찾는다.
  // 샌드박스(`/api/crew/run`)로 돌린 결과는 Task에 안 묶여 `task_id`
  // 자체가 없으므로(기존에도 그랬다) 그 경우는 비교 기능 없이 기존
  // 그대로 렌더한다 — 새로 뭔가 깨지는 게 없다.
  let prevRun = null;
  if (result.task_id) {
    try {
      const pastRuns = await api("GET", "/api/tasks/" + result.task_id + "/crew-runs");
      const earlier = pastRuns.filter(r => result.id != null && r.id < result.id);
      if (earlier.length) {
        prevRun = earlier.reduce((a, b) => (a.id > b.id ? a : b));
      }
    } catch (e) { /* 이력 조회 실패해도 diff 없이 기존처럼 보여준다 */ }
  }

  let html = "<h3>협업 관계 다이어그램</h3>" +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>실선(회색)은 요청 시점에 정해진 구조(순차 전달 또는 매니저 조율)입니다.</li>' +
      '<li>점선(주황)은 실제로 발생한 위임/질의입니다.</li>' +
      '</ul>'
    ) +
    diagram.svg +
    (diagram.unresolved.length
      ? '<details class="muted"><summary>확인 안 된 위임(' + diagram.unresolved.length + '건) — 대상 Agent를 다이어그램에서 특정하지 못함</summary>' +
        table(["위임한 Agent", "LLM이 지목한 대상(원문)", "도구"], diagram.unresolved.map(d => [esc(d.from_agent_role || "-"), esc(d.to_agent_role || "(응답에 coworker 없음)"), esc(d.tool)])) +
        "</details>"
      : "");
  html += '<h3>최종 산출물' +
    (prevRun
      ? ' <button class="small" type="button" data-diff-final="1">이전 실행과 비교</button>'
      : ' <button class="small" type="button" disabled title="비교할 이전 실행이 없습니다">이전 실행과 비교</button>') +
    '</h3><div class="msg ok">' + renderContent(result.final_output) + "</div>" +
    '<div id="crew-diff-final" style="display:none"></div>';
  // 사용자 요청(2026-10-12, "UI 개선 잔여 작업 진행해줘") — CrewAI가
  // 이미 집계해주는 토큰 사용량을 `run_crew()`가 이제 반환한다(크루
  // 완료 전엔 비어있을 수
  // 없다 — crew.kickoff() 성공 이후에만 이 함수가 반환되므로 항상 값이
  // 있다). 기존 대시보드의 `.stat-row` 패턴(새 CSS 불필요)을 그대로
  // 재사용한다. 비용/실행시간은 CrewAI가 집계해주지 않아(UsageMetrics에
  // 없음) 보여주지 않는다 — 없는 값을 지어내지 않는다.
  const tu = result.token_usage;
  html += "<h3>토큰 사용량(이 Crew 실행 전체)</h3>" +
    '<div class="card">' +
    '<div class="stat-row"><span>총 토큰</span><span>' + tu.total_tokens + '</span></div>' +
    '<div class="stat-row"><span>입력(prompt)</span><span>' + tu.prompt_tokens + '</span></div>' +
    '<div class="stat-row"><span>출력(completion)</span><span>' + tu.completion_tokens + '</span></div>' +
    '<div class="stat-row"><span>LLM 호출 수</span><span>' + tu.successful_requests + '</span></div>' +
    '</div>';
  // 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
  // 연결해줘") — run_crew()가 이제 gate_scores/tcr을 반환한다. 단건
  // deliverable 카드의 gateBadge와 같은 "측정 없음" 구분 원칙을 그대로
  // 따른다.
  const gates = Object.values(result.gate_scores);
  const measured = gates.filter(g => !g.not_measured);
  html += "<h3>Gate A-G 채점(이 Crew 실행 전체)</h3>";
  html += measured.length === 0
    ? '<p class="muted">Gate 측정 없음</p>'
    : '<p>' + measured.filter(g => g.passed).length + "/" + measured.length + " 통과" +
      (result.tcr === null ? "" : " · TCR " + result.tcr.toFixed(0) + "%") + '</p>';
  // 사용자 요청(2026-10-27, "다른 UI에도 ID가 남아 있는지 확인해줘") —
  // agent_pool은 이미 code가 있으니 raw agent_id 대신 쓴다.
  // 사용자 요청(2026-11-05, 로드맵 8번) — "비교" 열 추가, 이전 실행에
  // 같은 agent_id의 산출물이 있을 때만 버튼을 활성화한다.
  html += "<h3>Agent별 산출물</h3>" + table(["Agent 코드", "역할", "산출물", "비교"], result.agents.map(a => {
    const prevAgent = prevRun ? prevRun.agents.find(pa => pa.agent_id === a.agent_id && pa.output !== null) : null;
    const compareCell = a.output === null
      ? ""
      : prevAgent
        ? '<button class="small" type="button" data-diff-agent="' + a.agent_id + '">비교</button>'
        : '<span class="muted">(이전 실행에 없음)</span>';
    return [
      esc(a.code || ("#" + a.agent_id)), agentDisplayLabel(a),
      a.output === null ? "(없음 — 매니저는 조율만 하고 직접 산출물을 내지 않습니다)"
        : esc(a.output).slice(0, 200) + (a.output.length > 200 ? "…" : ""),
      compareCell,
    ];
  }));
  html += '<div id="crew-diff-agent" style="display:none"></div>';
  html += "<h3>실제 위임/협업 발생 내역(" + result.delegations.length + "건)</h3>";
  html += result.delegations.length
    ? table(["위임한 Agent 역할", "대상 Agent 역할", "사용한 도구"], result.delegations.map(d => [esc(d.from_agent_role || "-"), esc(d.to_agent_role || "(확인 안 됨)"), esc(d.tool)]))
    : '<p class="muted">이번 실행에서는 Agent 간 실제 위임이 발생하지 않았습니다(각자 독립적으로 완수).</p>';
  box.innerHTML = html;

  if (prevRun) {
    const finalDiffBox = document.getElementById("crew-diff-final");
    const finalBtn = box.querySelector("[data-diff-final]");
    if (finalBtn) {
      finalBtn.onclick = () => {
        if (finalDiffBox.style.display === "none") {
          finalDiffBox.innerHTML = renderTextDiff(prevRun.final_output, result.final_output);
          finalDiffBox.style.display = "block";
        } else {
          finalDiffBox.style.display = "none";
        }
      };
    }
    const agentDiffBox = document.getElementById("crew-diff-agent");
    box.querySelectorAll("[data-diff-agent]").forEach(btn => {
      btn.onclick = () => {
        const agentId = Number(btn.dataset.diffAgent);
        const a = result.agents.find(x => x.agent_id === agentId);
        const prevAgent = prevRun.agents.find(pa => pa.agent_id === agentId && pa.output !== null);
        agentDiffBox.innerHTML = "<h4>" + esc(agentDisplayLabel(a)) + " — 이전 실행과 비교</h4>" +
          renderTextDiff(prevAgent.output, a.output);
        agentDiffBox.style.display = "block";
      };
    });
  }
}

/* ---------- 인력 검색 ---------- */

async function renderSearch(content) {
  const PAGE_SIZE = 20;
  let offset = 0;

  const skillDatalist = await skillDatalistHtml();
  content.innerHTML =
    '<section class="card"><h2>사람·AI 에이전트 통합 검색</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>이 화면은 매칭 알고리즘 없이 필터로 후보를 훑어보는 용도입니다(실제 배정 점수·순위를 보려면 "리소스 매칭/배정" 탭을 쓰세요).</li>' +
      '<li>AI 에이전트는 온보딩 승인(active) 안 된 것(pending_onboarding/deprecated)은 여기 안 뜹니다 — "AI 에이전트 관리" 탭에서 상태 무관 전체 목록을 볼 수 있습니다. 오프보딩된 사람도 마찬가지로 빠집니다.</li>' +
      '<li>"부서" 필터는 없습니다 — 이 시스템엔 부서 데이터가 없고, "역할"이 사실상 같은 구실을 합니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="search-form">' +
    '<input id="s-role" placeholder="역할"><input id="s-skill" placeholder="스킬" list="skill-datalist">' +
    '<select id="s-avail"><option value="">가용 전체</option><option value="available">가능만</option><option value="unavailable">불가만</option></select>' +
    '<button class="primary" type="submit">검색</button></form>' +
    skillDatalist +
    '<div id="search-result"></div>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    '<button class="small" id="search-prev">이전</button>' +
    '<span id="search-page-info" class="muted"></span>' +
    '<button class="small" id="search-next">다음</button>' +
    '</div></section>';

  const load = async () => {
    const body = await api("GET", qs("/api/pool/search", {
      role: document.getElementById("s-role").value,
      skill: document.getElementById("s-skill").value,
      availability: document.getElementById("s-avail").value,
      limit: PAGE_SIZE,
      offset,
    }));
    document.getElementById("search-result").innerHTML = table(
      ["타입", "코드", "이름", "역할", "가용", "비용", "스킬", "성과점수"],
      body.items.map(c => [esc(label(c.type)), esc(c.code || ("#" + c.id)), esc(c.name || c.role), esc(c.role), c.is_available ? "가능" : "불가", c.cost, esc(c.skill_tags.join(", ")), c.performance_score === null ? "-" : c.performance_score])
    );
    const shown = body.items.length ? offset + 1 : 0;
    document.getElementById("search-page-info").textContent =
      "전체 " + body.total + "건 중 " + shown + "-" + (offset + body.items.length) + "건";
    document.getElementById("search-prev").disabled = offset === 0;
    document.getElementById("search-next").disabled = offset + PAGE_SIZE >= body.total;
  };

  document.getElementById("search-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    offset = 0;
    try { await load(); } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("search-prev").addEventListener("click", async () => {
    offset = Math.max(0, offset - PAGE_SIZE);
    try { await load(); } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("search-next").addEventListener("click", async () => {
    offset += PAGE_SIZE;
    try { await load(); } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  await load();
}

/* ---------- 조직도 ---------- */

const ORG_CHART_COLOR = { human: "#4a90d9", agent: "#8e5fd9", team: "#3fa34d", pipeline: "#d9824a" };

// 사용자 요청(2026-10-13) — 조직도를 "매니저별 테이블 나열"에서 실제
// 트리 다이어그램으로 바꾼다. org-chart API는 매니저 1명 + 그 매니저에게
// 귀속된 리포트 목록(2단계)만 주므로, 버킷 하나당 루트(매니저) 1개 +
// 자식(리포트) 열 하나짜리 단순 트리를 그린다(의존관계 그래프의 다단 컬럼
// 레이아웃과 달리 깊이가 항상 2라 더 단순하게 구현했다).
function renderOrgChartTreeSvg(bucket) {
  const nodeW = 170, nodeH = 40, rowHeight = 52, padding = 16, gapX = 70;
  const reports = bucket.reports;
  const rootX = padding, childX = padding + nodeW + gapX;
  const height = padding * 2 + Math.max(1, reports.length) * rowHeight - (rowHeight - nodeH);
  const rootY = padding + (height - padding * 2) / 2 - nodeH / 2;
  const width = childX + nodeW + padding;

  const managerLabel = bucket.manager_username ? bucket.manager_username : "(지정된 관리자 없음)";
  const rootSvg = '<rect x="' + rootX + '" y="' + rootY + '" width="' + nodeW + '" height="' + nodeH +
    '" rx="6" fill="#fff" stroke="#333" stroke-width="2"/>' +
    '<text x="' + (rootX + nodeW / 2) + '" y="' + (rootY + nodeH / 2 + 4) +
    '" text-anchor="middle" font-size="12" font-weight="bold">' + esc(managerLabel) + '</text>';

  const childrenSvg = reports.map((r, i) => {
    const y = padding + i * rowHeight;
    const color = ORG_CHART_COLOR[r.resource_type] || "#999";
    const roleLabel20 = r.role_name.length > 18 ? r.role_name.slice(0, 17) + "…" : r.role_name;
    const nameLabel20 = (r.resource_name || "-").length > 20 ? r.resource_name.slice(0, 19) + "…" : (r.resource_name || "-");
    const x1 = rootX + nodeW, y1 = rootY + nodeH / 2;
    const x2 = childX, y2 = y + nodeH / 2;
    const midX = (x1 + x2) / 2;
    return '<path d="M' + x1 + ',' + y1 + ' C' + midX + ',' + y1 + ' ' + midX + ',' + y2 + ' ' + x2 + ',' + y2 +
      '" fill="none" stroke="#bbb" stroke-width="1.5" marker-end="url(#org-arrow)"/>' +
      '<g><title>' + esc(r.role_name) + " · " + esc(label(r.resource_type)) + " · " + esc(r.resource_name || "-") +
      " (배정 #" + r.assignment_id + ", 할 일 #" + r.task_id + ')</title>' +
      '<rect x="' + childX + '" y="' + y + '" width="' + nodeW + '" height="' + nodeH +
      '" rx="6" fill="#fff" stroke="' + color + '" stroke-width="2"/>' +
      '<text x="' + (childX + nodeW / 2) + '" y="' + (y + 16) +
      '" text-anchor="middle" font-size="11" font-weight="bold">' + esc(nameLabel20) + '</text>' +
      '<text x="' + (childX + nodeW / 2) + '" y="' + (y + 31) +
      '" text-anchor="middle" font-size="10" fill="#666">' + esc(roleLabel20) + '</text></g>';
  }).join("");

  return '<svg width="' + width + '" height="' + height + '" style="max-width:100%">' +
    '<defs><marker id="org-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" ' +
    'markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#bbb"/></marker></defs>' +
    rootSvg + childrenSvg + '</svg>';
}

async function renderOrgChart(content) {
  const chart = await api("GET", "/api/org-chart");
  let html = '<section class="card"><h2>조직도(확정된 배정)</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>여기 나오는 건 결재까지 끝나 상태가 confirmed(확정)된 배정뿐입니다 — "리소스 매칭/배정"에서 방금 요청했거나 "결재" 탭에서 아직 대기 중인 건은 승인되기 전까지 이 화면에 나타나지 않습니다.</li>' +
      '<li>묶는 기준(관리자)이 사람과 AI 에이전트가 다릅니다: 사람 배정은 그 사람의 "상사"(사용자 관리에서 지정)로, AI 에이전트 배정은 Agent에게 상사가 없으므로 그 배정을 요청한 사람으로 귀속시킵니다.</li>' +
      '<li>상사가 없으면 "(지정된 관리자 없음)" 묶음에 들어갑니다.</li>' +
      '<li>이름이 비슷해 헷갈리기 쉬운 점: 이 화면은 "조직도"라는 이름이지만 confirmed 배정을 모아 보여주는 스냅샷일 뿐 생성·삭제가 되는 조직이 아닙니다 — 업무에 종속되고 생성·온보딩·해체되는 실제 조직은 "팀 관리" 탭에서 "TF팀" 배지가 붙은 팀들입니다(Task/Brief ID를 지정해 만든 팀).</li>' +
      '</ul>'
    );
  if (chart.length) {
    html += '<p class="muted">' +
      '<span style="color:' + ORG_CHART_COLOR.human + '">■</span> 사람 &nbsp;' +
      '<span style="color:' + ORG_CHART_COLOR.agent + '">■</span> AI 에이전트 &nbsp;' +
      '<span style="color:' + ORG_CHART_COLOR.team + '">■</span> 팀 &nbsp;' +
      '<span style="color:' + ORG_CHART_COLOR.pipeline + '">■</span> 파이프라인' +
      ' — 카드에 마우스를 올리면 배정 ID·할 일 ID가 보입니다.</p>';
  }
  for (const bucket of chart) {
    html += '<div class="card" style="overflow-x:auto">' +
      renderOrgChartTreeSvg(bucket) +
      '<details class="muted"><summary>표로 보기</summary>' +
      table(["배정 ID", "할 일 ID", "역할", "타입", "리소스"], bucket.reports.map(r => [
        r.assignment_id, r.task_id, esc(r.role_name), esc(label(r.resource_type)), esc(r.resource_name || "-"),
      ])) +
      '</details></div>';
  }
  if (!chart.length) html += '<p class="muted">확정된 배정이 없습니다.</p>';
  html += "</section>";
  content.innerHTML = html;
}

/* ---------- 성과 리뷰 ---------- */

async function renderPerformance(content) {
  const rows = await api("GET", "/api/performance-review");
  content.innerHTML = '<section class="card"><h2>성과 리뷰</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>사람의 "성과점수"는 사전 동의가 있어야만 값이 채워집니다 — "사람 인력 관리" 탭에서 동의를 받은 사람만 점수를 입력할 수 있고, 동의를 철회하면 그 순간 점수가 지워집니다.</li>' +
      '<li>AI 에이전트는 동의 개념이 없어 항상 입력·조회 가능합니다.</li>' +
      '<li>이 화면의 점수는 둘 다 관리자가 직접 입력한 수동값입니다 — Agent의 실제 Gate A-G 자동 채점 결과는 이 화면이 아니라 "AI 에이전트 관리" 탭의 "자동평가(Gate/TCR)" 열에서 따로 봅니다(둘을 아직 한 화면에 병기하진 않습니다).</li>' +
      '<li>"확정 배정 수"만 실제 데이터(confirmed 배정 건수)입니다.</li>' +
      '<li>팀(Team) 행은 동의·성과점수 개념이 없어("-") 확정 배정 수만 보여줍니다 — 팀의 성과는 그 멤버 Agent/사람 행에서 개별로 확인하세요.</li>' +
      '</ul>'
    ) +
    table(["타입", "코드", "이름", "역할", "동의", "성과점수", "확정 배정 수"], rows.map(r => [
      esc(label(r.type)), esc(r.code || ("#" + r.id)), esc(r.name || r.role), esc(r.role),
      r.performance_consent === null ? "-" : (r.performance_consent ? "동의함" : "미동의"),
      r.performance_score === null ? "-" : r.performance_score,
      r.confirmed_assignments,
    ])) + "</section>";
}

/* ---------- 매니저 대시보드(Phase 5, 사용자 요청 2026-09-28) ---------- */

// 사용자 요청(2026-10-21, "Layer 3 — 데이터 시각화 정리") — 손수
// 좌표를 계산하던 SVG를 이미 로드된 Chart.js(CDN)로 교체한다.
// `renderGateRadarChart()`는 canvas 자리만 반환하고(HTML 문자열
// 조립 시점엔 아직 DOM에 안 붙어 있어 Chart.js를 바로 못 그린다),
// 실제 차트 생성은 `_initGateRadarChart()`가 `content.innerHTML = ...`
// 로 그 canvas가 실제 DOM에 붙은 **뒤에** 호출돼야 한다(`renderDashboard`
// 참고). not_measured(`null`)는 그래프 값은 기존처럼 0으로 클램프해
// "측정 안 됨이 낮게 보이도록" 하되, 그 점이 0이 아니라 "미측정"이라는
// 사실은 툴팁 콜백으로 전달한다(JSON만 주고받는 범용 `data-chart-config`
// 메커니즘, webui.py의 `_activateRichBlocks` 참고 — 은 함수 콜백을
// 못 담아서 여기선 못 쓴다, 그래서 직접 `new Chart()`를 호출한다).
function renderGateRadarChart() {
  return '<canvas id="gate-radar-canvas" width="220" height="230" style="max-width:100%"></canvas>';
}

function _initGateRadarChart(gateAverages) {
  const canvas = document.getElementById("gate-radar-canvas");
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    canvas.insertAdjacentHTML("afterend", '<p class="muted">차트 라이브러리를 불러오지 못했습니다.</p>');
    return;
  }
  const gates = ["A", "B", "C", "D", "E", "F", "G"];
  const rawValues = gates.map(g => gateAverages[g]);
  const plotValues = rawValues.map(v => Math.max(0, Math.min(1, v || 0)));
  new Chart(canvas, {
    type: "radar",
    data: {
      labels: gates,
      datasets: [{
        data: plotValues,
        backgroundColor: "rgba(74,144,217,0.25)",
        borderColor: "#4a90d9",
        pointBackgroundColor: "#4a90d9",
      }],
    },
    options: {
      responsive: false,
      scales: { r: { min: 0, max: 1, ticks: { display: false } } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = rawValues[ctx.dataIndex];
              return "Gate " + gates[ctx.dataIndex] + ": " + (v === null ? "미측정" : v.toFixed(2));
            },
          },
        },
      },
    },
  });
}

// 사용자 요청(2026-10-21, "Layer 3") — "비용 변경 이력"을 Chart.js
// 멀티 데이터셋 라인 차트로. cost_trend는 최신순(DESC)·여러 리소스
// (subject_type+subject_id)가 뒤섞인 단가 변경 이벤트 목록이라,
// 리소스별로 데이터셋을 나누고 x축(이벤트의 시간순 인덱스)을
// 공유해 "누가 언제"를 같은 좌표계에서 비교할 수 있게 한다(기존 SVG
// 설계 그대로). **`responsive: false`가 핵심** — 이벤트가 많아지면
// canvas 자체를 넓게 그려 `overflow-x:auto`로 가로 스크롤하게 하려는
// 의도적 설계(기존 SVG도 `max-width:none`으로 똑같이 했다) — Chart.js
// 기본 반응형(컨테이너에 맞춰 쪼그라듦)을 그대로 두면 이 설계가 깨진다.
function renderCostTrendChart(trend) {
  if (!trend.length) return '<p class="muted">비용 변경 이력이 없습니다.</p>';
  const padding = 34;
  const width = Math.max(260, padding * 2 + (trend.length - 1) * 40);
  return '<div style="overflow-x:auto"><canvas id="cost-trend-canvas" width="' + width + '" height="180"></canvas></div>';
}

function _initCostTrendChart(trend) {
  if (!trend.length) return;
  const canvas = document.getElementById("cost-trend-canvas");
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    canvas.insertAdjacentHTML("afterend", '<p class="muted">차트 라이브러리를 불러오지 못했습니다.</p>');
    return;
  }
  const asc = trend.slice().reverse(); // DESC로 오므로 오래된 것 → 최신으로 뒤집는다
  const colors = ["#4a90d9", "#8e5fd9", "#3fa34d", "#e0a030", "#d9534f", "#2aa8a8", "#c2538f"];
  const bySeries = {};
  asc.forEach((t, i) => {
    const key = t.subject_type + ":" + t.subject_id;
    (bySeries[key] = bySeries[key] || []).push({ x: i, y: t.cost_estimate, t });
  });
  const datasets = Object.entries(bySeries).map(([key, pts], idx) => {
    const [stype, sid] = key.split(":");
    return {
      label: label(stype) + " #" + sid,
      data: pts,
      borderColor: colors[idx % colors.length],
      backgroundColor: colors[idx % colors.length],
      showLine: pts.length > 1,  // 포인트가 1개뿐인 시리즈는 점만(기존 SVG와 동일)
    };
  });
  new Chart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: false,
      maintainAspectRatio: false,
      scales: { x: { type: "linear", ticks: { stepSize: 1, display: false } } },
      plugins: {
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const t = ctx.raw.t;
              return label(t.subject_type) + " #" + t.subject_id + " · " + t.created_at + " · " + t.cost_estimate;
            },
          },
        },
      },
    },
  });
}

async function renderDashboard(content) {
  const d = await api("GET", "/api/dashboard");
  const gs = d.team_gate_scores;
  const gateRow = ["A", "B", "C", "D", "E", "F", "G"].map(g => {
    const v = gs.gate_averages[g];
    return '<div class="stat-row"><span>' + g + '</span><span>' + (v === null ? "-" : v.toFixed(2)) + '</span></div>';
  }).join("");

  // 사용자 요청(2026-10-27, "다룬 테이블도 ID 표기되는 것이 남아
  // 있는지 확인") — agent_pool/human_pool 변경 이력이라 code로 교체.
  const trendRows = d.cost_trend.map(t => [
    esc(t.created_at), esc(label(t.subject_type)), esc(t.subject_code || ("#" + t.subject_id)), t.cost_estimate,
  ]);

  // 2026-10-08, Phase 4 — "팀 부하" 바
  // 차트(진행 중 Task 수를 막대 길이로 표현, 외부 차트 라이브러리 없이
  // div width%로 충분)와 "주의 필요" 목록.
  const maxWorkload = Math.max(1, ...d.workload.map(w => w.in_progress_count));
  const workloadRows = d.workload.map(w =>
    '<div class="stat-row"><span>' + esc(label(w.resource_type)) + " " + esc(w.name || "#" + w.resource_id) + '</span>' +
    '<span style="flex:1;margin:0 0.5rem;background:#eee;border-radius:3px">' +
    '<span style="display:block;height:0.8em;background:#4a90d9;border-radius:3px;width:' +
    Math.round((w.in_progress_count / maxWorkload) * 100) + '%"></span></span>' +
    '<span>' + w.in_progress_count + '</span></div>'
  ).join("") || '<p class="muted">진행 중인 업무가 없습니다.</p>';

  const bottleneckRows = d.bottlenecks.map(b =>
    '<li>#' + b.id + " " + esc(b.role_name) + " — " +
    esc(b.reasons.map(r => ({ blocked: "선행 작업 미완료로 대기 중", rejected_twice: "결과물 2회 이상 반려" })[r] || r).join(", ")) +
    '</li>'
  ).join("") || '<p class="muted">주의가 필요한 업무가 없습니다.</p>';

  // 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 1번 "Gate
  // 점수 기반 자동 경고") — 최근 Gate 평가가 연속으로 실패한 Agent를
  // "AI 에이전트 관리" 탭을 일일이 열어보지 않아도 여기서 바로 본다.
  const agentBottleneckRows = (d.agent_bottlenecks || []).map(a =>
    '<li>' + esc(a.agent_code || ("#" + a.agent_id)) + " " + esc(a.role_name) + " — 최근 " + a.recent_evaluation_count +
    '건 연속 Gate 실패</li>'
  ).join("") || '<p class="muted">최근 연속 Gate 실패가 있는 Agent가 없습니다.</p>';

  content.innerHTML =
    '<section class="card"><h2>매니저 대시보드</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>팀 전체 Agent Gate 점수 평균·대기 승인 건수·비용 변경 이력·팀 부하·주의 필요 업무를 한 화면에 모았습니다.</li>' +
      '<li>Gate 점수는 실제로 측정된 산출물이 있을 때만 값이 나옵니다(not_measured는 평균에서 제외).</li>' +
      '<li>비용 추이는 "AI 에이전트 관리"·"사람 인력 관리" 탭에서 단가를 바꿀 때마다 남는 감사로그 이력이라, 지금까지 아무도 단가를 바꾼 적이 없으면 비어 있습니다.</li>' +
      '<li>"주의 필요"는 마감일 개념이 이 플랫폼에 없어 "선행 작업 미완료로 막힘"·"결과물 2회 이상 반려"만 신호로 씁니다.</li>' +
      '<li>"Agent 주의 필요"는 한 Agent의 가장 최근 Gate 평가 3건이 전부 하나 이상의 Gate에서 실패한 경우입니다(평가가 3건 미만이면 표본 부족으로 판단하지 않습니다) — 자동으로 오프보딩하지는 않고 신호만 띄웁니다.</li>' +
      '</ul>'
    ) +
    '<div class="dashboard-grid">' +
    '<div class="card"><h3>팀 Agent 평균 Gate 점수(표본 ' + gs.sample_count + '건)</h3>' +
    '<div style="text-align:center">' + renderGateRadarChart() + '</div>' +
    '<details class="muted"><summary>숫자로 보기</summary>' + gateRow + '</details>' +
    '<div class="stat-row"><span>평균 TCR</span><span>' + (gs.avg_tcr === null ? "-" : gs.avg_tcr.toFixed(1) + "%") + '</span></div></div>' +
    '<div class="card"><h3>대기 승인 건수</h3><p style="font-size:2em;margin:0">' + d.pending_approval_count + '</p></div>' +
    '<div class="card"><h3>현재 비용 스냅샷</h3><p style="font-size:2em;margin:0">' + d.cost_snapshot.total_cost + '</p></div>' +
    '</div>' +
    '<h3>팀 부하(진행 중인 업무 수)</h3>' + workloadRows +
    '<h3>주의 필요(' + d.bottlenecks.length + '건)</h3><ul>' + bottleneckRows + '</ul>' +
    '<h3>Agent 주의 필요(' + (d.agent_bottlenecks || []).length + '건)</h3><ul>' + agentBottleneckRows + '</ul>' +
    '<h3>비용 변경 이력(최근 ' + d.cost_trend.length + '건)</h3>' +
    renderCostTrendChart(d.cost_trend) +
    '<details class="muted"><summary>표로 보기</summary>' +
    table(["시각", "대상 종류", "대상 ID", "변경된 단가"], trendRows) +
    '</details>' +
    '</section>';
  // 사용자 요청(2026-10-21, "Layer 3") — Chart.js는 실제 DOM에 붙은
  // <canvas>가 있어야 그릴 수 있다 — content.innerHTML 대입이 끝난
  // 뒤(canvas가 실제로 DOM에 붙은 뒤)에만 호출해야 한다.
  _initGateRadarChart(gs.gate_averages);
  _initCostTrendChart(d.cost_trend);
}

/* ---------- 비용 ---------- */

async function renderCost(content) {
  const report = await api("GET", "/api/cost-report");
  content.innerHTML = '<section class="card"><h2>비용 리포트(총액: ' + report.total_cost + ')</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>이 "합계"는 "단가(cost_estimate, 사람/에이전트 관리 탭에서 수동 입력)" × "확정 배정 수"입니다.</li>' +
      '<li>실제로 LLM API에 청구된 금액이 아닙니다(simple_llm/crewai 생성이나 Crew 실행·에이전트 실행 때 실제로 나간 API 비용은 이 리포트에 잡히지 않습니다).</li>' +
      '<li>즉 "일 시켰을 때 실제 API 비용"이 아니라 "이 인력을 계속 쓰면 예상되는 인건비/도입비" 관점의 숫자입니다.</li>' +
      '<li>팀(Team) 행은 단가·합계 개념이 없어("-") 확정 배정 수만 보여줍니다 — 팀의 비용은 그 멤버 Agent/사람 행에 이미 개별로 집계돼 있습니다(중복 합산 방지).</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "비용의 ID도 agent code로 변경") — agent는
    // code, human은 employee_id(둘 다 없으면 "#id" 폴백).
    table(["타입", "코드", "이름", "역할", "단가", "확정 배정 수", "합계"], report.resources.map(r => [
      esc(label(r.type)), esc(r.code || ("#" + r.id)), esc(r.name || r.role), esc(r.role),
      r.cost_estimate === null ? "-" : r.cost_estimate, r.confirmed_assignments,
      r.total_cost === null ? "-" : r.total_cost,
    ])) + "</section>";
}

/* ---------- 공정성 ---------- */

// 사용자 요청(2026-10-13) — 사람 vs AI 에이전트 반려율을 숫자 두 줄
// 대신 막대 비교로. 격차가 10%p를 넘으면 빨간색으로 강조해 "격차가
// 있는지"가 표를 안 읽어도 바로 보이게 한다(10%p는 디자인 임의값 —
// fairness.py의 low_sample_warning과는 별개 기준).
function renderFairnessBarSvg(r) {
  const padding = 76, chartW = 220, barH = 28, rowGap = 14;
  const width = padding + chartW + 50;
  const rows = [
    { label: "사람", rate: r.human.rejection_rate, color: ORG_CHART_COLOR.human },
    { label: "AI 에이전트", rate: r.agent.rejection_rate, color: ORG_CHART_COLOR.agent },
  ];
  const height = rowGap + rows.length * (barH + rowGap) + (r.rejection_rate_gap === null ? 0 : 20);

  const bars = rows.map((row, i) => {
    const y = rowGap + i * (barH + rowGap);
    const nameLabel = '<text x="' + (padding - 8) + '" y="' + (y + barH / 2 + 4) +
      '" text-anchor="end" font-size="11" font-weight="bold">' + esc(row.label) + '</text>';
    if (row.rate === null) {
      return nameLabel + '<text x="' + (padding + 4) + '" y="' + (y + barH / 2 + 4) +
        '" font-size="11" fill="#999">표본 없음</text>';
    }
    const w = Math.round(row.rate * chartW);
    return nameLabel +
      '<rect x="' + padding + '" y="' + y + '" width="' + chartW + '" height="' + barH +
      '" rx="4" fill="#eee"/>' +
      '<rect x="' + padding + '" y="' + y + '" width="' + w + '" height="' + barH +
      '" rx="4" fill="' + row.color + '"/>' +
      '<text x="' + (padding + Math.max(w, 2) + 6) + '" y="' + (y + barH / 2 + 4) +
      '" font-size="11" fill="#333">' + (row.rate * 100).toFixed(1) + '%</text>';
  }).join("");

  const gapNote = r.rejection_rate_gap === null ? "" :
    '<text x="' + padding + '" y="' + (height - 4) + '" font-size="10" fill="' +
    (r.rejection_rate_gap > 0.1 ? "#d9534f" : "#888") + '">격차: ' +
    (r.rejection_rate_gap * 100).toFixed(1) + '%p</text>';

  return '<svg width="' + width + '" height="' + height + '" style="max-width:100%">' + bars + gapNote + '</svg>';
}

async function renderFairness(content) {
  const r = await api("GET", "/api/fairness-report");
  content.innerHTML = '<section class="card"><h2>배정 공정성 지표</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>사람과 AI 에이전트가 같은 매칭 기준(가용성/적합도/비용 가중치)으로 배정되는데도 반려율에 체계적 차이가 나는지 보는 지표입니다.</li>' +
      '<li>"확정"·"반려"는 결재까지 끝난(confirmed/rejected) 배정만 세고, 아직 대기 중인 배정은 "결정됨"에 안 들어갑니다.</li>' +
      '<li>"반려율 격차"가 크면 어느 한쪽이 불리하게 반려되는 패턴이 있을 수 있다는 신호입니다(단, 표본이 작으면 우연일 수 있음).</li>' +
      '</ul>'
    ) +
    (r.low_sample_warning ? '<p class="msg error">표본이 작아(각 10건 미만) 통계적 해석에 주의가 필요합니다.</p>' : "") +
    renderFairnessBarSvg(r) +
    '<details class="muted"><summary>표로 보기</summary>' +
    table(["구분", "확정", "반려", "결정됨", "반려율"], [
      ["사람", r.human.confirmed, r.human.rejected, r.human.decided, r.human.rejection_rate === null ? "-" : r.human.rejection_rate.toFixed(3)],
      ["AI 에이전트", r.agent.confirmed, r.agent.rejected, r.agent.decided, r.agent.rejection_rate === null ? "-" : r.agent.rejection_rate.toFixed(3)],
    ]) +
    "<p>반려율 격차: " + (r.rejection_rate_gap === null ? "-" : r.rejection_rate_gap.toFixed(3)) + "</p>" +
    '</details></section>';
}

/* ---------- 활동 기록 ---------- */

async function renderAudit(content) {
  // 사용자 보고(2026-10-13, "할일 ID를 사전에 모르면 사용 불가") — "처리자
  // ID" 필터도 같은 문제라 활성 계정 목록(select)으로 바꾼다. 비활성
  // 계정이 과거에 남긴 기록은 이 select로 못 고르지만(`/api/users/names`가
  // 활성 계정만 주므로), "전체"를 고르면 필터 없이 다 보이니 완전히
  // 막히진 않는다.
  const users = await api("GET", "/api/users/names").catch(() => []);
  content.innerHTML =
    '<section class="card"><h2>활동 기록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>이 로그는 DB 트리거로 수정·삭제 자체가 막혀 있는 append-only 기록입니다 — 다른 탭의 "삭제" 버튼으로 브리프/할 일/사람/에이전트/도구/스킬을 실제로 지워도, "무엇을 언제 누가 지웠는지"는 여기 그대로 남습니다.</li>' +
      '<li>Admin·매니저만 조회할 수 있습니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="audit-form">' +
    '<select id="a-actor"><option value="">처리자: 전체</option>' +
    users.map(u => '<option value="' + u.id + '">' + esc(u.username) + " (" + esc(label(u.role)) + ")</option>").join("") +
    '</select>' +
    '<input id="a-subject" placeholder="대상 종류(예: brief, assignment)">' +
    '<button class="primary" type="submit">조회</button></form>' +
    '<div id="audit-result"></div></section>';
  const load = async () => {
    const body = await api("GET", qs("/api/audit-log", {
      actor_id: document.getElementById("a-actor").value,
      subject_type: document.getElementById("a-subject").value,
      limit: 50,
    }));
    document.getElementById("audit-result").innerHTML =
      "<p class=\\"muted\\">전체 " + body.total + "건 중 " + body.items.length + "건 표시</p>" +
      // 사용자 요청(2026-10-27, "처리자 ID는 아이디로 대상 ID는 agent
      // code로 변경") — 처리자는 username, 대상은 agent_pool인 경우만
      // code(그 외 종류는 code 개념이 없어 id 그대로).
      table(["시각", "처리자", "동작", "대상 종류", "대상 ID"], body.items.map(a => [
        esc(a.created_at), esc(a.actor_username || ("#" + a.actor_id)), esc(a.action), esc(label(a.subject_type)),
        a.subject_id === null ? "-" : esc(a.subject_code || a.subject_id),
      ]));
  };
  document.getElementById("audit-form").addEventListener("submit", (ev) => { ev.preventDefault(); load(); });
  await load();
}

/* ---------- 관리자 ---------- */

async function renderAdmin(content) {
  const users = await api("GET", "/api/users");
  content.innerHTML =
    '<section class="card"><h2>사용자 생성</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>4가지 권한(role)은 화면 접근 범위를 결정합니다.</li>' +
      '<li><b>관리자</b>: 전 기능.</li>' +
      '<li><b>매니저</b>: "운영 관리" 대부분에 접근 가능하지만, 사용자 생성/상사 지정·각종 삭제·Agent 온보딩 승인/오프보딩처럼 조직 구조에 직접 영향을 주는 기능과 "모델 관리"(LLM 설정 최적화·모델 레지스트리)는 관리자 전용.</li>' +
      '<li><b>팀원</b>: 업무 요청 제출·결재 요청 등 "내 업무" 화면.</li>' +
      '<li><b>뷰어</b>: 조회만.</li>' +
      '<li>생성 직후엔 상사가 지정 안 돼 있어(아래에서 지정), 이 사용자가 배정을 요청하면 결재가 단일 단계(승인자 미지정)로 처리됩니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="user-form">' +
    '<input id="u-username" placeholder="아이디" required>' +
    '<input id="u-password" type="password" placeholder="비밀번호" required>' +
    '<select id="u-role"><option value="admin">관리자</option><option value="manager">매니저</option>' +
    '<option value="member" selected>팀원</option><option value="viewer">뷰어</option></select>' +
    // 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및 표시되도록
    // UI개선") — human_pool과 같은 선택 입력(숫자 8~12자리).
    '<input id="u-employee-id" placeholder="사번(선택, 숫자 8~12자리)" pattern="\\d{8,12}" title="숫자 8~12자리" style="width:160px">' +
    '<button class="primary" type="submit">생성</button></form>' +
    "</section>" +
    '<section class="card"><h2>사용자 목록</h2>' +
    // 사용자 요청(2026-09-29, "생성과 상사 지정만 가능... (1) 비밀번호
    // 조회/초기화 (2) 사용자 삭제 (3) '동작'의 의미 불명") — 비밀번호는
    // bcrypt 해시라 "조회"는 원리상 불가능하고(평문을 저장하지 않음)
    // "초기화"만 제공한다. "삭제"는 users.id가 audit_log/assignments/
    // approvals/human_pool.user_id에서 NOT NULL로 참조돼 하드 삭제가
    // FK 위반으로 불가능하다(append-only 감사 이력 보존 원칙과 충돌) — Human/Agent
    // Pool과 같은 "비활성화"(계정은 남기고 로그인만 막음) 방식을 쓴다.
    // "동작" 열은 실제로 상사 지정 하나뿐이었으므로 그대로 이름을 바꿨다.
    helpBox(
      '<ul class="muted help-list">' +
      '<li>비밀번호는 bcrypt 해시로 저장돼 "조회"는 할 수 없습니다 — <b>초기화</b>로 새 비밀번호를 지정한 뒤 별도 채널로 전달하세요.</li>' +
      '<li><b>계정 상태</b>는 하드 삭제 대신 비활성화 방식입니다(감사 이력·배정 이력이 이 계정을 참조하고 있어 완전히 지우면 그 이력이 깨집니다) — 비활성화된 계정은 즉시 로그인이 막히고 기존 세션도 끊깁니다.</li>' +
      '<li>상사를 지정하면 그 사람이 배정을 요청할 때 결재가 상사 체인을 따라 다단계로 자동 라우팅됩니다("결재" 탭 참고).</li>' +
      '<li>본인을 직접 상사로 지정하는 것만 막혀 있고, A→B→A 같은 두 단계 이상의 순환은 막지 않습니다.</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및 표시되도록
    // UI개선") — "ID" 열을 사번으로 바꾸고(미입력이면 "#id" 폴백,
    // human_pool과 같은 패턴), 기존 계정에도 "사번 저장" 인라인 입력을
    // 둔다(col-actions-compact라 폭 고정 문제 없음).
    table(["사번", "아이디", "권한", "계정 상태", "상사", "상사 지정", "비밀번호 재설정", "사번 변경", "계정 상태 변경"], users.map(u => {
      const isSelf = u.id === ME.id;
      // 사용자 보고(2026-10-27, 스크린샷 — "상사" 열이 username이 아니라
      // 정수 manager_id를 그대로 보여줌) — 같은 화면에 이미 전체
      // users 배열이 있으니 그 자리에서 이름을 찾는다.
      const managerUser = u.manager_id === null ? null : users.find(m => m.id === u.manager_id);
      return [
        esc(u.employee_id || ("#" + u.id)), esc(u.username), esc(u.role),
        u.is_active ? '<span class="gate-badge gate-ok">활성</span>' : '<span class="gate-badge gate-warn">비활성</span>',
        u.manager_id === null ? "-" : esc(managerUser ? managerUser.username : "#" + u.manager_id),
        '<div class="actions"><span class="pair"><select id="mgr-' + u.id + '" style="width:110px"><option value="">(없음)</option>' +
          users.filter(m => m.id !== u.id).map(m => '<option value="' + m.id + '"' + (m.id === u.manager_id ? " selected" : "") + ">" + esc(m.username) + "</option>").join("") +
        '</select><button class="small" data-set-manager="' + u.id + '">저장</button></span></div>',
        '<div class="actions"><span class="pair"><input type="password" id="pw-' + u.id + '" placeholder="새 비밀번호" style="width:120px">' +
          '<button class="small" data-reset-password="' + u.id + '">초기화</button></span></div>',
        '<div class="actions"><span class="pair"><input id="uempid-' + u.id + '" placeholder="사번(숫자 8~12자리)" pattern="\\d{8,12}" title="숫자 8~12자리" value="' + escAttr(u.employee_id || "") + '" style="width:120px">' +
          '<button class="small" data-set-user-employee-id="' + u.id + '">저장</button></span></div>',
        isSelf
          ? '<span class="muted">본인 계정</span>'
          : (u.is_active
            ? '<button class="small danger" data-set-status="' + u.id + '" data-active="0">비활성화</button>'
            : '<button class="small" data-set-status="' + u.id + '" data-active="1">재활성화</button>'),
      ];
    })) + "</section>";

  document.getElementById("user-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const employeeIdValue = document.getElementById("u-employee-id").value.trim();
    try {
      await api("POST", "/api/users", Object.assign({
        username: document.getElementById("u-username").value,
        password: document.getElementById("u-password").value,
        role: document.getElementById("u-role").value,
      }, employeeIdValue ? { employee_id: employeeIdValue } : {}));
      showMsg("global-msg", "생성했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  content.querySelectorAll("[data-set-manager]").forEach(btn => {
    btn.onclick = async () => {
      const sel = document.getElementById("mgr-" + btn.dataset.setManager);
      try {
        await api("PATCH", "/api/users/" + btn.dataset.setManager + "/manager", {
          manager_id: sel.value ? Number(sel.value) : null,
        });
        showMsg("global-msg", "상사를 지정했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-reset-password]").forEach(btn => {
    btn.onclick = async () => {
      const input = document.getElementById("pw-" + btn.dataset.resetPassword);
      const newPassword = input.value;
      if (!newPassword) { showMsg("global-msg", "새 비밀번호를 입력해야 합니다.", "error"); return; }
      if (!(await confirmModal("이 계정의 비밀번호를 초기화합니다. 새 비밀번호를 본인에게 안전하게 전달해야 합니다. 계속할까요?"))) return;
      try {
        await api("PATCH", "/api/users/" + btn.dataset.resetPassword + "/password", {
          password: newPassword,
        });
        showMsg("global-msg", "비밀번호를 초기화했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-user-employee-id]").forEach(btn => {
    btn.onclick = async () => {
      const input = document.getElementById("uempid-" + btn.dataset.setUserEmployeeId);
      const value = input.value.trim();
      if (!value) { showMsg("global-msg", "사번을 입력하세요.", "error"); return; }
      try {
        await api("PATCH", "/api/users/" + btn.dataset.setUserEmployeeId + "/employee-id", {
          employee_id: value,
        });
        showMsg("global-msg", "사번을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-status]").forEach(btn => {
    btn.onclick = async () => {
      const activate = btn.dataset.active === "1";
      const msg = activate
        ? "이 계정을 다시 활성화합니다. 계속할까요?"
        : "이 계정을 비활성화합니다 — 즉시 로그인이 막히고 기존 세션도 끊깁니다(계정과 이력은 삭제되지 않음). 계속할까요?";
      if (!(await confirmModal(msg))) return;
      try {
        await api("PATCH", "/api/users/" + btn.dataset.setStatus + "/status", {
          is_active: activate,
        });
        showMsg("global-msg", activate ? "재활성화했습니다." : "비활성화했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 이용 가이드(사용자 요청, 2026-10-18, "대화를 진행하면서
   사람이나 에이전트를 호출해 업무를 수행하는 방식, 사람-에이전트(에이전트
   그룹) N:N 조합으로 복잡한 업무 및 개발을 수행하는 절차, 파이프라인
   구성 및 활용법, Agent 및 Agent 그룹 생성 절차 등... 실용적인 메뉴얼을
   작성해 메뉴로 접근할 수 있도록") ----------
   전부 "common" 탭(4개 역할 모두 접근 가능)으로 둔다 — admin 전용 기능
   (Agent 생성·파이프라인 빌더 등)도 설명 대상이지만, 그 기능이 실제로
   왜/어떻게 쓰이는지는 member/viewer도 알아야 결과를 이해할 수 있다
   (admin 전용 버튼은 본문에서 그때그때 "admin/manager 전용"이라고
   표시한다). 내용은 전부 이 코드베이스에 실제로 있는 메뉴 이름·버튼
   문구·동작 순서를 그대로 옮긴 것이다(가상의 기능 설명 금지) — marked.js
   가 raw HTML(`<h2 id="...">`)을 그대로 통과시키고 DOMPurify가 id
   속성을 허용하므로, 섹션 제목만 raw HTML로 써서 목차 앵커 링크가
   동작하게 한다(marked v13은 기본적으로 헤더에 id를 안 붙인다). */
const MANUAL_MARKDOWN = [
'<h2 id="toc">목차</h2>',
'',
'1. [개요](#ov) — 이 플랫폼이 뭘 하는 도구인지',
'2. [가장 빠른 시작 — 대화로 바로 호출하기](#chat)',
'3. [정식 업무(Task) 경로 — 요청부터 결재까지](#task)',
'4. [N:N 조합 — 사람+Agent 여러 명이 함께 일하기](#nn)',
'5. [Agent / Agent 그룹 만들기](#agent)',
'6. [파이프라인 — 단계형 자동 진행 엔진](#pipeline)',
'7. [실전 시나리오 4가지](#scenario)',
'8. [메뉴 전체 지도](#menu)',
'9. [자주 겪는 문제](#trouble)',
'',
'> 🔰 **처음이라 바쁘다면** — 1절만 가볍게 읽고 바로 [2절](#chat)로 가서 따라 해보세요. 나머지 절은 필요할 때 찾아보면 됩니다. 이미 **26개의 활성 Agent**가 등록돼 있어, 아무것도 새로 만들지 않고도 지금 바로 체험할 수 있습니다.',
'',
'<h2 id="ov">1. 개요</h2>',
'',
'HAIR(Human-AI Resource Management)는 **사람과 AI Agent를 하나의 조직도 위에서 같은 방식으로 배치·배분·승인·실행**시키는 협업 플랫폼입니다.',
'핵심 원칙 3가지만 기억하면 나머지는 전부 그 응용입니다.',
'',
'- **사람과 Agent는 같은 테이블, 다른 역할입니다** — 일단 조직에 들어온 뒤로는 배치·배분·승인·팀 구성이 사람이든 Agent든 같은 절차·같은 화면("리소스 매칭/배정"·"결재"·"조직도")을 씁니다. Agent를 만들고 온보딩하는 화면, 사람을 등록하는 화면은 각자 성격이 달라 따로 있습니다 — 통일된 건 그 이후의 절차입니다.',
'- **승인 없이 조직에 반영되는 변화는 없습니다** — Agent가 뭘 만들어도 그게 "확정된 결과"가 되려면 반드시 사람의 결재를 거칩니다(이런 "사람이 중간에서 확인하는 구조"를 줄여서 **HITL**(Human-In-The-Loop)이라고 부릅니다 — 이 가이드 여러 곳에 나오는 말입니다).',
'  단, 파이프라인의 자동 진행 단계는 Gate A-G 기준을 통과하면 "통과 자체가 곧 승인"이라 사람 개입이 최소화됩니다.',
'- **측정 없이 신뢰하지 않습니다** — Agent가 만든 모든 결과물은 Gate A-G(목표달성·행동무결성·신뢰성·성능계약·보안경계·다중에이전트협업·관측성)로 자동 채점됩니다.',
'',
'업무를 시키는 방법은 크게 두 갈래입니다 — **가벼운 대화**(3절)와 **정식 업무 절차**(4절).',
'둘 다 같은 Agent/팀 등록을 공유하므로 아무 때나 오갈 수 있습니다.',
'',
'<h2 id="chat">2. 가장 빠른 시작 — 대화로 바로 호출하기</h2>',
'',
'📍 **메뉴**: 대화',
'',
'가장 가벼운 방법입니다.',
'결재 없이 즉시 답을 받고 싶을 때 씁니다.',
'아래 순서를 그대로 따라 하면 바로 체험할 수 있습니다.',
'',
'### 1단계 — 대화 열기',
'',
'왼쪽 메뉴에서 **"대화"** 탭을 엽니다.',
'로그인 직후에도 독립 대화가 바로 시작됩니다(업무에 종속되지 않은 자유 대화).',
'처음이면 **"새 대화 제목(선택)"** 입력란(비워도 됩니다)에 적고 "**새 대화 시작**"을 누르세요.',
'기존 대화가 있으면 목록에서 "**열기**"를 누릅니다.',
'',
'> 💡 **뭘 물어볼지 막막하다면** — 이미 등록돼 바로 쓸 수 있는 활성 Agent가 26개 있습니다(새로 만들 필요 없음). 예를 들어 `@코드 검증가 이 함수 리뷰해줘`, `@데이터 분석가 이 숫자들의 증감률을 계산해줘`, `@외부 자료 조사가 ○○ 시장 동향을 조사해줘`처럼 바로 불러보세요. 전체 목록은 "**인력 검색**" 탭에서 확인할 수 있습니다.',
'',
'### 2단계 — 메시지 입력창의 컨트롤 이해하기',
'',
'화면 맨 아래 입력 영역에 왼쪽부터 순서대로:',
'',
'- **"개발업무 스킬도 보기" 체크박스**(기본값 꺼짐) — 바로 옆 스킬 선택 드롭다운에는 기본적으로 **일반업무**(기획·전략·자료조사·보고서·데이터분석·제안서·실적서 등) 스킬만 보입니다. 소프트웨어 개발/Gate 채점처럼 **개발업무**용 스킬은 완전히 없어진 게 아니라 "기본적으로 안 보이게" 숨겨뒀을 뿐이라 — 이 체크박스를 켜면 바로 보입니다. 일반 업무를 하는 사용자는 이 체크박스를 켤 일이 거의 없습니다(어느 스킬이 어느 분류인지는 "도구/스킬 Pool" 관리 화면에서 확인·변경할 수 있습니다 — admin/manager 전용, 8절 메뉴 지도에서 위치를 찾을 수 있습니다).',
'- **스킬 선택 드롭다운**(기본값 "스킬 선택 안 함") — 비워두고 질문만 보내도 되지만, 멘션 없이 보내는 응답을 특정 스킬로 "그라운딩"하고 싶을 때 씁니다.',
'  예를 들어 실시간 정보가 필요한 질문이면 "**실시간웹검색**"을 골라두세요 — Tavily로 실제 웹 검색을 한 뒤 그 결과를 참고해 답합니다(서버에 `TAVILY_API_KEY` 설정이 먼저 돼 있어야 합니다 — 관리자에게 문의).',
'  **멘션을 하나라도 넣으면 이 선택은 자동으로 비워집니다** — 멘션이 있으면 그 호출이 우선이고, 스킬 선택은 "아무도 안 불렀을 때(기본 AI가 답할 때) 어떤 지식/도구로 그라운딩할지"를 고르는 용도이기 때문입니다.',
'- **메시지 입력창** — **`@`를 입력**하면 사람·Agent·Team 검색 자동완성이 뜹니다. 아직 이 대화에 초대 안 된 대상도 검색되고, 목록에서 고르면 **그 자리에서 자동으로 초대된 뒤** 멘션이 끼워 넣어집니다(따로 "참가자 초대" 화면을 거칠 필요가 없습니다).',
'- **📎 자료 버튼** — 로컬 파일(`.md`/`.markdown`/`.txt`/`.html`/`.htm`/`.pdf`)을 고르면 버튼 옆에 파일명이 뜨며 **보류 상태로 대기**합니다(바로 올라가지 않음, "자료 제거"로 취소 가능). 이 상태에서 메시지를 적고 **"전송"**을 눌러야 — 입력한 메시지가 있으면 그 메시지가 먼저, 첨부 내용은 바로 이어서 별도 메시지로 — 한 번에 같이 올라갑니다(메시지 없이 첨부만 보내도 됩니다). **이 대화에만 1회성으로 쓰이고 영구 저장되지 않습니다**(어디에도 파일 자체나 텍스트가 남지 않는 휘발성 설계 — 영구 보관하려는 자료는 8절 메뉴 지도의 "도구/스킬 Pool"이 아니라 admin/manager의 "지식 베이스" 관리 기능을 따로 써야 합니다).',
'',
'### 3단계 — 멘션으로 누구를 부를지 정하고 메시지 전송',
'',
'- **특정 Agent를 `@역할명` 또는 `@지정한이름`으로 멘션**하면 그 Agent만 실행됩니다(예: `@DA 이 기능의 데이터 모델을 설계해줘`).',
'- **Team을 `@팀이름`으로 멘션**하면 그 팀의 Agent 멤버 전원이 실행됩니다(4절 "N:N 조합" 참고 — 매니저가 있으면 자동으로 위임 구조로 돕니다).',
'- **사람을 `@아이디`로 멘션**하면 AI가 끼어들지 않고 그 사람이 직접 답해야 하는 대화로 남습니다.',
'- **아무도 멘션하지 않으면** 대화의 **"AI 자동응답"** 체크박스 상태에 따라 기본 모델이 대신 답합니다(켜져 있으면 멘션 없는 모든 메시지마다 LLM이 호출돼 비용이 발생합니다 — 체크박스는 대화 상단에 있습니다).',
'',
'### 4단계 — (선택) 기본 AI가 쓸 모델 직접 지정',
'',
'대화 상단의 provider/모델 select에서 고르고 "**모델 설정 저장**"을 눌러야 반영됩니다.',
'',
'> ⚠️ 흔히 헷갈리는 부분: 이 설정은 **멘션 없는 기본 AI 응답에만** 적용됩니다. `@Agent이름`으로 부르면 그 Agent 자신에게 저장된 모델이, `@Team이름`으로 부르면 서버가 자동 판별한 모델이 쓰입니다(이 설정과 무관). 각 메시지에 실제로 쓰인 모델은 메시지 작성자 줄에 표시됩니다.',
'',
'### 5단계 — 결과 확인',
'',
'실행에는 실제 LLM API 호출이 들어가므로 **비용이 발생**합니다 — 결과는 잠시 뒤 메시지로 도착하고(비동기 처리, 화면에 "응답을 기다리는 중…" 표시), 실패하면 원인이 메시지로 안내됩니다.',
'',
'각 메시지 줄에는 두 가지 "보내기" 버튼이 있습니다(대화의 모든 메시지가 자동으로 쌓이지 않고, 남길 만하다고 직접 고른 메시지만 보냅니다):',
'',
'- **갤러리로 보내기**(모든 참가자) — 이 메시지 하나를 결과물로 승격해 "갤러리" 탭에서 모아볼 수 있게 합니다(비용 없는 단순 저장).',
'- **지식베이스로 보내기**(admin/manager 전용) — 이 메시지를 임베딩해 VectorDB에 색인합니다(실제 임베딩 API 호출 — 비용 발생). 색인된 내용은 이후 대화에서 스킬 선택 드롭다운의 "**지식검색**"를 고르거나, "지식검색" 스킬을 가진 Agent를 실행하면 실시간으로 검색돼 답변에 반영됩니다.',
'',
'### 6단계 — (선택) 정식 절차로 넘기기',
'',
'대화와 업무 요청(Brief)은 서로 독립적인 두 처리 방식입니다 — 결재·Gate 채점이 필요해지면 대화 내용을 복사해 업무 요청 작성란(3절)에 직접 옮겨 적으세요.',
'',
'> 실수하기 쉬운 점: Agent/Team을 멘션했는데 반응이 없다면, 그 대상이 아직 **이 대화의 참가자가 아닐 가능성**이 가장 큽니다 — 자동완성 목록에서 골라 초대부터 된 상태인지 확인하세요(9절 "자주 겪는 문제" 참고).',
'',
'<h2 id="task">3. 정식 업무(Task) 경로 — 요청부터 결재까지</h2>',
'',
'승인 이력·Gate 채점·결과물 타임라인이 전부 필요한 "진짜 업무"는 이 경로를 씁니다.',
'단계가 2절보다 많지만, 한 번 해두면 같은 할 일에 사람·Agent를 몇 명이든 더 투입할 수 있고 이력이 전부 남습니다.',
'',
'### 1단계 — 업무 요청 제출(제안 단계, 비용 없음)',
'',
'📍 **메뉴**: 업무 요청',
'',
'**"업무 요청"** 탭을 엽니다.',
'**"한 줄에 역할 하나씩"** 입력하세요(예: 입력창 placeholder 그대로 "백엔드 개발자 1명" 다음 줄에 "UI/UX 디자이너 1명") → **"제출"**.',
'이건 실제 AI 분석이 아니라 입력한 텍스트를 줄바꿈 단위로 그대로 나누는 **규칙 기반 처리**라 비용이 없고 즉시 끝납니다 — 아래 "제안된 역할" 칸에 채워질 뿐인 **참고용 제안**입니다(뷰어 role은 조회만 가능, 제출 불가).',
'',
'### 2단계 — 할 일(Task)로 나누기(실제 배분 단위 만들기)',
'',
'📍 **메뉴**: 업무 요청(같은 화면)',
'',
'방금 제출한 업무 요청 행에서 "**할 일로 나누기**"를 누릅니다.',
'제안된 역할 칩을 클릭하면 아래 입력란에 그대로 채워집니다(다듬어서 쓰세요). 직접 채울 항목:',
'',
'- **필요한 역할**(예: "백엔드 개발자") — 필수. 💡 이미 등록된 레퍼런스 Agent(26개)와 이름이 겹치면("백엔드 개발자"/"코드 검증가"/"데이터 분석가" 등), 사람/Agent를 새로 만들지 않아도 다음 3단계의 후보 매칭에서 바로 그 Agent가 뜹니다.',
'- **필요 스킬** — 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(칩의 ×로 제거, 여러 개 등록 가능, 목록에 없는 이름도 자유 입력 가능). 자동완성 중 **"실시간웹검색"**(Tavily)은 대화창·Crew 실행 양쪽에서 동작하고, **"일반웹검색"**(Parallel Search)은 Crew 실행에서만 동작합니다 — 이 둘의 차이는 꼭 기억해두세요(9절 "자주 겪는 문제"에도 관련 내용이 있습니다).',
'- **인원 수**(기본 1).',
'',
'"**할 일 추가**"를 누르면 즉시 할 일 목록에 반영됩니다. 이후 모든 배정·Agent 생성·실행은 이 할 일(Task) 단위로 이뤄집니다. 목록에서 "**상세**"를 누르면 배정 현황을 같은 화면에서 바로 볼 수 있습니다(아래 4단계).',
'',
'**업무가 복잡해서 여러 할 일로 한 번에 쪼개고 싶다면**(admin/manager 전용) — "**AI로 분해 제안 받기(복잡한 업무용)**" 버튼을 누르면 LLM이 업무 요청 원문을 읽고 여러 할 일(역할·필요 스킬)과 **선행 업무 관계**까지 한 번에 제안합니다(비용 발생). 제안된 표에서 역할·스킬·"선행 업무" 드롭다운을 직접 고쳐가며 다듬은 뒤 "**이 배치로 할 일 추가**"를 누르면, 할 일들이 한꺼번에 만들어지면서 지정한 선행 관계도 자동으로 연결됩니다(의존관계 그래프에 바로 반영 — 8절 "조직도"가 아니라 할 일 상세의 의존관계 시각화에서 확인). 순환 의존(A가 B의 선행, B가 다시 A의 선행)은 자동으로 거부됩니다.',
'',
'### 3단계 — 사람/Agent/Team/파이프라인 연결하기',
'',
'📍 **메뉴**: 업무 요청(할 일 상세) · 리소스 매칭/배정 · 팀 관리 · 파이프라인 관리',
'',
'네 가지 중 필요한 만큼 씁니다 — **같은 할 일에 여러 건의 확정 배정이 공존**할 수 있습니다(사람 여러 명 + Agent 여러 개 + 팀까지 동시에 투입하는 게 기본값). 네 가지 전부 같은 결재 체인(4단계)을 거칩니다 — 리소스 종류가 달라도 "요청 → 승인/반려" 절차는 동일합니다.',
'',
'*(A) 후보를 찾아서 배정 요청(사람/Agent)*',
'',
'"할 일 보기" 목록에서 "**상세**"를 누르고 **"후보 매칭하기"**(또는 "리소스 매칭/배정" 탭에서 할 일을 고르고 **"찾기"**) — 역할명이 서로 포함 관계(부분일치)인 후보 중 필요 스킬까지 다 가진 후보가 있으면 그것부터, 없으면 **가용성 40%·적합도 35%·비용 25%** 가중치로(사람 후보가 아직 성과점수를 못 받았으면 적합도를 빼고 가용성 60%·비용 40%로) 점수 매긴 상위 3명을 보여줍니다. 부분일치로도 후보가 전혀 없으면 "**AI로 추천 받기**" 버튼으로 LLM이 전체 Pool에서 의미상 맞는 후보를 찾아줍니다(비용 발생 가능, 눌러야만 호출됨).',
'원하는 후보 줄에서 "**배정 요청**"을 누르면 조직도 매니저 체인을 따라 결재 단계가 자동 생성됩니다 — 아직 승인 전 상태입니다. **단, 본인 자신을 배정 요청하는 셀프배정은 예외입니다** — 본인 시간만 쓰는 배정이라 결재 체인 자체를 만들지 않고 그 자리에서 바로 확정됩니다(4단계를 거치지 않고 바로 5단계로 진행 가능).',
'',
'*(B) 아직 쓸 Agent가 없다면*',
'',
'"**AI 에이전트 생성**"에서 이 할 일을 골라 바로 만듭니다(5절에서 자세히 다룹니다).',
'',
'*(C) 팀(Team)에 배정 요청*',
'',
'할 일 상세의 "**팀에 배정 요청**" 펼침 영역에서 등록된 활성 팀 중 하나를 골라 "**이 팀에 배정 요청**"을 누릅니다(4절 "N:N 조합"에서 미리 팀을 만들어둬야 목록에 뜹니다 — 없으면 "팀 관리" 탭에서 먼저 등록하라는 안내가 대신 나옵니다). 팀 배정의 결재 주체는 **팀 리더**(기본값은 배정 요청자 본인, 승인 전까지는 할 일 상세에서 다른 사용자 ID로 바꿀 수 있습니다)입니다.',
'',
'*(D) 파이프라인에 직접 배정 요청*',
'',
'할 일 상세의 "**파이프라인에 배정 요청**" 펼침 영역에서 등록된 활성 파이프라인 템플릿을 골라 "**이 파이프라인에 배정 요청**"을 누릅니다. 사람/Agent/Team과 똑같이 결재를 거치고, **승인되는 순간 그 파이프라인 실행이 바로 시작됩니다**(이미 같은 할 일에서 실행 중인 파이프라인이 있으면 배정은 확정되지만 중복 실행은 안 됩니다). 5단계의 "파이프라인 실행" 버튼으로 직접 시작하는 것과 결과적으로 같은 엔진을 타지만, 이 경로는 결재 이력이 남는다는 점이 다릅니다.',
'',
'### 4단계 — 결재 처리',
'',
'📍 **메뉴**: 결재',
'',
'**"결재"** 탭 → "**내 승인 대기함**"에서 본인 차례의 요청을 처리합니다(자기 자신이 요청한 건 "내 승인 대기함"에 아예 뜨지 않습니다 — 자기 승인 방지. 단, 셀프배정은 애초에 이 단계 자체가 생략됩니다 — 위 3단계(A) 참고).',
'사유를 적고(선택) "**승인**"하면 배정이 **확정**으로 바뀌어 조직도·비용·성과 리뷰에 실제로 반영됩니다. "**반려**"하면 그 요청은 거기서 끝나고, 다시 배정하려면 3단계부터 새로 요청해야 합니다.',
'상사가 지정 안 돼 있으면(예: 관리자 본인 요청) 특정 승인자 없이 1단계만 생성돼 admin/manager 누구나 처리할 수 있습니다.',
'',
'### 5단계 — 협업 워크스페이스에서 실행',
'',
'📍 **메뉴**: 내 업무(또는 "업무 요청"의 할 일 상세에 바로 임베드)',
'',
'배정이 확정되면 그 할 일의 **협업 워크스페이스**가 열립니다(할 일 상세 화면에 바로 임베드되거나 "내 업무"에서 접근). 여기서 고를 수 있는 것:',
'',
'- **"이 업무의 AI 에이전트 실행(비용 발생)"**: Agent 1명에게 온디맨드로 맡깁니다. 실행 버튼 옆 "**📎 참고자료**"로 이번 1회 실행에만 쓸 파일을 첨부할 수 있습니다(2절의 대화 첨부와 같은 원리 — 추출한 텍스트만 프롬프트에 쓰이고 파일 자체나 텍스트는 저장되지 않습니다).',
'- **"팀 Agent 실행(Crew, 비용 발생)"**: 배정된 팀의 Agent 멤버 전원을 실제 CrewAI Crew로 함께 실행합니다(4절).',
'- **"파이프라인 실행(비용 발생)"**: 등록된 파이프라인 템플릿을 골라 처음부터 끝까지 단계별로 자동 진행시킵니다(6절).',
'- 사람이 직접 쓴 결과물도 "**검토 요청**"을 누르면 Agent 결과물과 같은 결재 체인을 거칩니다.',
'- (admin/manager 전용) **"재시도 예산"** 입력 — 이 할 일이 파이프라인·Crew 재시도를 합쳐서 총 몇 번까지 시도할 수 있는지 상한을 걸어둘 수 있습니다(빈 값=무제한). 상한에 걸리면 그 단계의 자체 재시도 횟수가 남아 있어도 즉시 사람 승인으로 에스컬레이션됩니다 — 같은 할 일을 반복 재시도하며 비용만 계속 쓰는 걸 막는 안전장치입니다.',
'',
'### 6단계 — 결과물 결재로 마무리',
'',
'📍 **메뉴**: 결재',
'',
'결과물은 Gate A-G 자동 채점이 붙고, 4단계와 같은 **"결재"** 탭에서 승인/반려를 거쳐야 "확정된 결과"가 됩니다.',
'',
'### 7단계 — 결과물 모아보기 / 단독으로 열기 / 삭제',
'',
'📍 **메뉴**: 갤러리',
'',
'- **"갤러리"** 탭(사람 메뉴)에서 여러 할 일을 가로질러 결과물을 한 화면에 모아 봅니다 — admin/manager는 조직 전체, 그 외엔 본인이 참가한 할 일의 결과물만 보입니다. 유형(텍스트/마크다운/HTML/코드)별로 걸러볼 수 있습니다.',
'- 카드의 **"새 탭에서 보기"**를 누르면 그 결과물 하나만 담긴 독립 페이지가 새 탭에 열립니다(로그인 세션은 그대로 필요합니다 — 외부에 공개로 공유되는 링크가 아닙니다). 코드는 문법 강조, HTML은 그대로 렌더링, 나머지는 마크다운으로 보여줍니다.',
'- **"삭제"** 버튼은 admin/manager는 항상, 그 외엔 본인이 작성하고 아직 제출 전(초안, draft)인 결과물에만 보입니다 — 제출 후에는 결재 이력이라 본인도 못 지웁니다. 삭제하면 갤러리·협업 워크스페이스 양쪽에서 즉시 사라집니다(별도 새로고침 없이 바로 반영).',
'',
'> 배정을 잘못 걸었거나 더 이상 필요 없어졌다면, 할 일 상세 화면(admin/manager 전용)의 **"배정 취소"** 버튼으로 confirmed/requested 상태를 되돌릴 수 있습니다 — 업무 요청·할 일 삭제가 "확정된 배정이 있어 삭제할 수 없다"로 막힐 때도 이 버튼으로 먼저 정리하세요.',
'',
'<h2 id="nn">4. N:N 조합 — 사람+Agent 여러 명이 함께 일하기</h2>',
'',
'한 업무에 사람 여러 명과 Agent 여러 개를 동시에 묶는 두 가지 방법입니다.',
'',
'### 방법 A — 팀(Team) 등록(재사용 가능, 정식 경로에 적합)',
'',
'📍 **메뉴**: 팀 관리',
'',
'1. **"팀 관리"** 탭(admin/manager 전용) → "팀 등록"에 이름·설명을 넣습니다. **Task ID/Brief ID를 입력하면 "TF팀"**(그 업무에 종속된 조직)이 되고, 비워두면 여러 업무에 걸쳐 재사용하는 **범용 팀**이 됩니다(둘의 차이는 아래 "팀의 상태(생명주기)" 참고).',
'2. 만든 팀 카드에서 **"멤버 추가"** — 사람(human_pool)과 Agent를 역할 라벨과 함께 몇 명이든 추가합니다(예: 사람 2명 + Agent 10명을 한 팀에).',
'3. **위임형(hierarchical) 협업을 원하면**, 매니저를 맡을 Agent를 추가할 때 역할 라벨에 **"매니저"라는 단어를 포함**시키세요(예: "매니저(hierarchical 실행 시 지정)").',
'   그러면 이 팀을 실행할 때 그 Agent가 자동으로 매니저로 지정돼, 나머지 팀원에게 하위 과업을 실제로 쪼개 위임합니다.',
'   역할 라벨에 "매니저"가 없으면 전원이 순차(sequential)로 각자 수행합니다. 팀 카드의 "**기본값 저장**" 폼에서 기본 process/매니저를 미리 지정해두면 실행할 때마다 다시 고르지 않아도 됩니다.',
'4. **팀의 상태(생명주기)** — Agent 1명과 똑같은 3단계를 팀도 따릅니다(팀 카드 제목 옆 배지로 표시):',
'   - **온보딩 대기**: Task ID/Brief ID를 지정해 만든 TF팀은 이 상태로 시작합니다 — 검증 전까지는 **대화 @멘션이나 할 일 배정 후보 목록에 뜨지 않습니다**. Task ID/Brief ID 없이 만든 범용 팀은 바로 "활성"으로 시작합니다.',
'   - **활성**: "**온보딩 시작**" 버튼을 누르면 이 상태가 되고, 그 순간부터 @멘션·배정이 가능해집니다.',
'   - **비활성화됨**: "비활성화" 버튼(기존 활성화/비활성화 토글) 또는 TF팀의 경우 "**미션 완료**" 버튼(연결된 업무가 끝났을 때, 완료 시각이 함께 기록됩니다)으로 전환합니다. 이후로는 다시 @멘션·배정 후보에 뜨지 않습니다.',
'   - **팀 삭제**는 그 팀에 확정된 배정이 남아 있으면 막힙니다(오류 메시지로 안내) — 할 일 상세의 "배정 취소"로 먼저 정리하세요(사람/Agent/할 일 삭제와 같은 보호 규칙입니다).',
'5. 이 팀은 두 군데서 실행할 수 있습니다:',
'   - 대화에서 **`@팀이름`** 멘션(2절) — 매니저가 있으면 위임형, 없으면 전원 실행 후 각자 결과를 메시지로 올립니다.',
'   - 업무(Task)에 팀을 배정한 뒤 협업 워크스페이스의 **"팀 Agent 실행"** 버튼, 또는 3절의 (C) 방법대로 직접 "팀에 배정 요청" — 결과가 Agent별 결과물+결재 체인으로 그 업무에 그대로 쌓입니다.',
'6. **파이프라인 연결**(누구나 설정 가능, 1개 팀 한정 아님) — 팀 카드의 "기본값 저장" 폼에서 "**연결 파이프라인**"을 고르면, 그 팀을 `@멘션`할 때 단발 실행 대신 그 파이프라인(6절)을 그대로 탑니다. 예를 들어 팀에 "소프트웨어 개발 파이프라인"을 연결해두면, 그 팀을 `@멘션`하는 순간 요구분석부터 품질 관리까지 7단계(코드 검증·QA·품질 관리 단계는 Gate 기준 자동 재시도/승인 대기)가 그대로 진행됩니다. 연결 안 된 팀은 기존처럼 한 번에 실행됩니다. "자율 개발 크루"는 기본으로 만들어져 있는 팀이 아닙니다 — "팀 관리"에서 정확히 그 이름으로 직접 만들어야 합니다(만들면 서버가 자동으로 멤버 구성을 7단계 전체 참조 역할로 채우고 "소프트웨어 개발 파이프라인"에 연결해줍니다). 이 팀도 `hair reset-data`로 초기화 대상입니다(9절 참고) — Agent 자체(요구사항 분석가·AA·DA·개발 계획 수립가·개발자 4명·UI 기획자·코드 검증가·테스트 계획 수립가·QA 기능테스터·품질 관리자 등)는 초기화돼도 그대로 남아 있으니, 초기화 후에도 같은 이름으로 팀만 다시 만들면 됩니다.',
'7. **팀 관리 리포팅** — "성과 리뷰"·"비용" 탭에도 팀이 사람·Agent와 나란히 한 행으로 나옵니다(팀 자체의 성과점수·단가는 없어 "확정 배정 수"만 집계됩니다 — 비용은 멤버 Agent 쪽에 이미 개별로 잡힙니다). 팀 카드의 "**실행 이력 보기**" 버튼을 누르면 "Crew 빌더"의 실행 이력 목록이 그 팀의 실행 건으로 좁혀져 나옵니다.',
'',
'### 방법 B — Crew 빌더(즉석 1회성, 재사용 등록 없이)',
'',
'📍 **메뉴**: Crew 빌더',
'',
'**"Crew 빌더"** 탭(admin/manager 전용)에서 매번 Agent를 직접 골라 그 자리에서 협업시킵니다 — 팀으로 등록해둘 필요가 없는 일회성 조합에 적합합니다. 세 가지 하위 모드가 있습니다:',
'',
'- **"업무에 바로 실행"**: 실제 할 일(Task)에 결과를 남기며 실행합니다(기본 모드) — 여러 Agent를 고르고, 처리 방식(sequential/hierarchical)과 hierarchical일 때의 매니저 Agent를 직접 선택합니다. sequential이고 병행 실행이 아니면 **"자기 검토"** 체크박스도 켤 수 있습니다 — 각 Agent가 자기 초안을 원 요구사항과 대조해 스스로 한 번 더 검토·수정한 뒤에 제출합니다(member도 켤 수 있는 단순 품질 옵션, 결과물은 검토 후 최종본만 남고 초안은 실행 이력에서만 보임).',
'- **"즉석 테스트(저장 안 됨)"**: 업무에 연결하지 않고 협업 구조만 가볍게 실험해봅니다 — 결과가 할 일에 남지 않습니다.',
'- **"실행 이력"**(admin/manager 전용): 과거 Crew 실행의 협업 다이어그램·토큰 사용량·Gate 점수를 그대로 다시 볼 수 있습니다. "팀 관리" 탭의 각 팀 카드에서 "실행 이력 보기"를 누르면 이 목록이 그 팀의 실행만으로 좁혀져 나옵니다. 같은 할 일에 재실행한 이력이 있으면 결과 화면에 **"이전 실행과 비교"** 버튼이 떠 — 최종 산출물·Agent별 산출물 모두 추가분(녹색)/삭제분(빨간색) 줄 단위 diff로 바로 비교할 수 있습니다(재시도할 때마다 전에 되던 기능이 조용히 사라지는지 한눈에 확인하는 용도).',
'',
'**이 조합을 팀/파이프라인으로 저장**: "업무에 바로 실행"으로 한 번 실행하면 결과 아래에 "이 조합을 저장해 다음에 재사용" 패널이 나타납니다(admin/manager 전용) — 방금 쓴 Agent 구성(+처리 방식/매니저)을 그대로 **"팀으로 저장"**(이후 "팀 관리"에서 재사용·배정 가능)하거나, 기존 파이프라인에 **"파이프라인 단계로 저장"**할 수 있습니다(hierarchical이면 매니저를 직접 지정해 실행했을 때만 — 자동 배정으로 실행했으면 저장 버튼이 비활성화됩니다). 매번 즉석으로 Agent를 고르는 게 번거로워졌다면 이 버튼으로 바로 정식 팀/파이프라인으로 승격시키세요.',
'',
'<h2 id="agent">5. Agent / Agent 그룹 만들기</h2>',
'',
'### 단일 Agent 만들기(admin/manager 전용, "AI 에이전트 생성" 탭)',
'',
'📍 **메뉴**: AI 에이전트 생성 · AI 에이전트 관리 · LLM 설정 최적화 · 모델 레지스트리',
'',
'1. 먼저 **업무 요청 → 할 일**이 있어야 합니다(3절 1-2단계) — Agent는 "어떤 할 일을 맡을지" 역할명을 가지고 태어납니다.',
'2. 할 일을 하나 이상 고르고, 생성 방식을 고릅니다:',
'   - **stub**(무료): LLM 호출 없는 규칙 기반 자리표시자 — 흐름만 비용 없이 채워볼 때.',
'   - **simple_llm**(비용 발생): 실제 LLM을 한 번만 호출하는 가장 가벼운 실제 Agent.',
'   - **crewai**(비용 발생): 실제 CrewAI Agent로 만듭니다 — 이후 "Crew 빌더"/"팀 Agent 실행"/파이프라인에서 다른 Agent와 실제로 협업·위임할 수 있는 건 이 방식뿐입니다.',
'     필요 스킬에 지식검색/관계조회 등이 있으면 관련 Tool이 생성 시점부터 자동으로 붙습니다.',
'3. 만든 Agent는 "**AI 에이전트 관리**"에서 **온보딩 승인**(온보딩 대기 → 활성)을 눌러야 실제 배정·실행·대화 호출에 쓸 수 있습니다.',
'4. 같은 탭에서 Agent별로:',
'   - **모델 설정**: 기본 LLM provider/model(비용 없음, 설정만 저장).',
'   - **파인튜닝**: temperature·reasoning on/off·재시도 횟수 등 CrewAI 동작 파라미터(비용 없음, 설정만 저장 — "Crew 빌더"로 실제 CrewAI Crew에 투입될 때만 반영).',
'   - **⚡ 최적화 추천**: 이 Agent의 역할 특성(심층추론/정밀·신속/표준 3단계 중 하나로 분류)에 맞는 provider/model/파인튜닝을 추천받습니다(규칙 기반, 비용 없음). "등록된 모델 중 선택" 드롭다운에 **레지스트리의 활성 모델 전체가 순위대로** 뜨므로 1위 추천 대신 다른 모델을 직접 골라도 됩니다(★ 표시는 관리자가 "모델 레지스트리"에서 그 tier에 직접 매핑해둔 모델 — 아래 참고). 추천값은 입력란에 채워지기만 하고 저장은 직접 눌러야 합니다. "AI 의견 보기"를 누르면 LLM에게 추가로 검토받을 수 있습니다(이건 비용 발생).',
'5. 여러 Agent를 한꺼번에 최적화하려면 **"LLM 설정 최적화"** 탭(admin 전용)에서 전체 목록을 보고 체크한 Agent에 바로 적용합니다 — 이미 추천과 같거나 한 번 적용한 Agent도 다시 체크해서 다른 모델로 재적용할 수 있습니다.',
'6. **"모델 레지스트리"** 탭(admin 전용)에서 모델별로 **"최적화 Tier 매핑"** 체크박스(심층추론/정밀·신속/표준, 모델 하나가 여러 tier에 동시 매핑 가능)를 지정해두면 ⚡ 최적화 추천이 이름 패턴 추정 대신 그 매핑을 항상 먼저 후보로 올립니다 — 새 모델이 나오거나 provider를 바꿀 때 코드 수정 없이 여기서 매핑만 바꾸면 되고, 몇 번이든 다시 바꿀 수 있습니다(아무것도 체크 안 하면 기존처럼 모델명 패턴 추정으로 동작). 어느 tier에 매핑할지 감이 안 오면 같은 화면의 **"성능 테스트"** 버튼으로 실제 응답 속도를 재볼 수 있습니다 — 고정 프롬프트 1건을 실제로 호출해 글자/초를 측정하고 그 속도만으로 tier를 추천합니다(품질 평가가 아니라 속도만 봅니다). "이 tier로 저장"을 누르면 위 체크박스에 바로 반영됩니다.',
'',
'### Agent 그룹(팀) 만들기',
'',
'4절 "N:N 조합 — 방법 A"를 참고하세요.',
'"AI 에이전트 생성"에서 여러 할 일을 한 번에 골라 만든 직후에는 그 결과 화면에 **"팀으로 묶기"** 단축 버튼도 있어, "팀 관리" 탭을 따로 열지 않고도 바로 팀을 만들 수 있습니다.',
'',
'<h2 id="pipeline">6. 파이프라인 — 단계형 자동 진행 엔진</h2>',
'',
'사람이 **계획 확정 시점에 한 번만 승인**하면, 그 뒤로는 구현 → 검증 → 품질 관리가 Gate A-G 기준으로 자동 반복되는 엔진입니다 — 쉽게 말해 "처음에 방향만 한 번 확인해주면, 그다음은 기준을 통과할 때까지 알아서 반복하고, 정말 안 될 때만 다시 사람을 부르는" 방식입니다.',
'개발 외 업무(시장조사·전략 등)에도 역할만 바꿔 그대로 재사용할 수 있는 범용 엔진입니다.',
'',
'### 개념',
'',
'파이프라인은 **단계(stage)의 순서 있는 목록**입니다.',
'각 단계는 셋 중 하나로 동작합니다:',
'',
'| 모드 | 동작 | 예 |',
'|---|---|---|',
'| 단독(single) | Agent 1명이 혼자 처리 | 요구사항 분석가 1명 |',
'| 순차 릴레이(sequential_relay) | N명이 순서대로 앞 사람 결과를 이어받음 | AA → DA |',
'| 병행 위임(parallel_delegated) | 리드 1명이 코워커들에게 실제로 과업을 쪼개 위임 | (admin이 직접 구성) |',
'',
'단계마다 **Gate A-G 통과 기준·최대 재시도 횟수·"사람 승인 필요" 여부**를 정합니다.',
'승인이 필요 없는 단계는 기준만 통과하면 자동으로 다음 단계로 넘어가고(최소 HITL), 기준 미달이면 "어떤 Gate가 왜 부족한지" 피드백을 반영해 자동 재시도합니다.',
'재시도를 다 써도 못 넘기면 그때만 결재함으로 올라옵니다 — 자동 반복이 완전히 실패했을 때만 사람에게 옵니다.',
'',
'### 바로 써보기 — 기본 제공 "소프트웨어 개발 파이프라인"',
'',
'📍 **메뉴**: 내 업무(할 일의 협업 워크스페이스) 또는 대화',
'',
'아래 7단계가 이미 등록돼 있어 템플릿을 새로 만들 필요 없이 바로 실행할 수 있습니다.',
'',
'| # | 단계 | 모드 | 참여 | 사람 승인 |',
'|---|---|---|---|---|',
'| 1 | 요구분석/계획 확정 | 단독 | 요구사항 분석가 | **필요**(여기서만 사람이 1회 개입) |',
'| 2 | 설계 | 순차 릴레이 | AA → DA | 불필요 |',
'| 3 | 개발 계획 수립 | 단독 | 개발 계획 수립가 | 불필요 |',
'| 4 | 구현 | 순차 릴레이 | UI 기획자 → 데이터/인프라 개발자 → 백엔드 개발자 → 프론트엔드 개발자 → 풀스택 개발자(최종 통합) | 불필요 |',
'| 5 | 코드 검증 | 단독 | 코드 검증가 | 불필요 |',
'| 6 | QA 기능테스트 | 순차 릴레이 | 테스트 계획 수립가 → QA 기능테스터 | 불필요 |',
'| 7 | 품질 관리 최종 판정 | 단독 | 품질 관리자 | 불필요(반복 실패 시에만 자동 에스컬레이션) |',
'',
'5·6·7단계(코드 검증/QA 기능테스트/품질 관리 최종 판정)는 Gate 통과 기준이 "측정된 Gate 절반 이상 통과"로 설정돼 있어 — 미달이면 사유를 반영해 자동 재시도합니다. 나머지 단계는 Gate를 측정·기록만 하고 자동으로 통과합니다. **마지막 자동 재시도에서는 자가검토가 자동으로 켜져** — Agent가 스스로 초안을 원 요구사항과 대조해 한 번 더 고친 뒤 제출합니다 — 그래도 기준을 못 넘기면 그때 사람 승인으로 올라갑니다(자동 반복이 완전히 실패했을 때만 사람에게 옵니다).',
'실행은 **업무(Task)의 협업 워크스페이스**에서 이 템플릿을 고르고 "파이프라인 실행(비용 발생)"을 누르면 시작되거나, 이 파이프라인에 연결된 팀(4절 참고)을 대화에서 `@팀이름`으로 멘션해도 똑같이 시작됩니다(3절 4단계) — 기본 제공 팀은 없으므로 연결하려면 직접 팀을 만들어야 합니다.',
'진행 상황은 같은 화면에서 단계별 배지(대기/진행중/통과/재시도N/승인대기/반려)로 실시간 확인합니다 — 대화에서 시작한 경우 같은 정보가 진행 메시지로 대화에도 올라옵니다.',
'',
'### 바로 써보기 — 기본 제공 "사업/기획 업무 처리 파이프라인"',
'',
'소프트웨어 개발 외 업무에도 같은 엔진을 그대로 쓸 수 있다는 걸 보여주는 두 번째 기본 제공 템플릿입니다.',
'',
'| # | 단계 | 모드 | 참여 | 사람 승인 |',
'|---|---|---|---|---|',
'| 1 | 요구사항 분석/계획 확정 | 단독 | 요구사항 분석가 | **필요** |',
'| 2 | 자료 조사·분석 | 순차 릴레이 | 외부 자료 조사가 → 데이터 분석가 → 3C4P SWOT 분석가 | 불필요 |',
'| 3 | 기획 계획 수립 | 단독 | PM | 불필요 |',
'| 4 | 작성 | 순차 릴레이 | 기획서 작성자 → 사업전략 수립가 → C-level 보고서 작성자 → 프레젠테이션 자료 작성자(최종 통합) | 불필요 |',
'| 5 | 내용 검증 | 단독 | 품질 관리자 | 불필요(Gate 0.5) |',
'| 6 | 표현 검토 | 순차 릴레이 | 콘텐츠 카피라이팅 | 불필요(Gate 0.5) |',
'| 7 | 최종 승인 판정 | 단독 | PM | 불필요(Gate 0.5, 반복 실패 시에만 에스컬레이션) |',
'',
'단계 구성·Gate 규칙·최소 HITL 원칙 전부 "소프트웨어 개발 파이프라인"과 완전히 동일한 엔진입니다 — 역할만 사업/기획 쪽으로 바뀐 것뿐입니다.',
'**"데이터 분석가"**는 `데이터분석가이드` 스킬(실제 CrewAI Tool "수치 데이터 분석기" 연결)로 합계·평균·증감률·표준편차를 코드로 직접 계산합니다 — LLM이 숫자를 서술로 지어내지 않도록, 값이 부족하면(표준편차는 2개 미만, 증감률은 기준값 0) 추측 대신 계산 불가를 그대로 알립니다.',
'',
'### 기본 제공 "제안서 작성 파이프라인"·"실적 보고 파이프라인"',
'',
'"사업/기획 업무 처리 파이프라인"의 "작성" 단계는 매번 기획서·전략·경영진 요약·발표자료 4종을 한꺼번에 만듭니다 — 제안서나 실적서 하나만 필요할 때 이 4종이 같이 나오는 낭비를 막기 위해 두 업무는 독립 템플릿으로 분리했습니다.',
'',
'| # | 제안서 작성 파이프라인 | 실적 보고 파이프라인 |',
'|---|---|---|',
'| 1 | 요구사항 분석/계획 확정(사람 승인 필요) | 요구사항 분석/계획 확정(사람 승인 필요) |',
'| 2 | 자료 조사·분석(외부 자료 조사가 → 데이터 분석가) | 데이터 분석(데이터 분석가) |',
'| 3 | 작성(제안서 작성자) | 작성(실적 보고서 작성자) |',
'| 4 | 내용 검증(품질 관리자, Gate 0.5) | 최종 승인 판정(PM, 검증+요구사항 추적성 겸임, Gate 0.5) |',
'| 5 | 표현 검토(콘텐츠 카피라이팅, Gate 0.5) | — |',
'| 6 | 최종 승인 판정(PM, Gate 0.5) | — |',
'',
'제안서는 발주처 평가 기준(배점표) 대응과 경쟁사 대비 차별화가 핵심이라 "표현 검토" 단계까지 그대로 두었고, 실적서는 정기·정형 문서라 그 단계를 생략해 더 가볍게 구성했습니다.',
'',
'### 새 파이프라인 만들기(admin/manager 전용, "파이프라인 관리" 탭)',
'',
'📍 **메뉴**: 파이프라인 관리',
'',
'1. "파이프라인 등록"으로 이름·설명·도메인(예: software_dev, biz_strategy)을 정합니다.',
'2. "단계 추가"로 단계를 하나씩 쌓습니다 — 이름, 모드(단독/순차 릴레이/병행 위임)를 고르면 그 모드에 맞는 역할 선택 UI가 바뀝니다(단독=1명, 순차 릴레이=N명+순서, 병행 위임=리드 1명+코워커 여러 명).',
'   공통 지시문, Gate 통과 비율(%), 최대 재시도, "사람 승인 필요" 체크박스, **산출물 형식**(텍스트/마크다운/HTML/코드 — 코드로 지정하면 결과 화면에 문법 강조가 자동 적용됩니다)도 이때 정합니다.',
'   - ⚠️ **병행 위임(parallel_delegated) 모드는 신중하게 쓰세요** — 매니저가 "Delegate work to coworker" 도구로 그때그때 동적으로 위임하는 방식인데, 실측 결과 매니저가 실제 위임 대신 위임 계획만 요약하고 끝내는 경우가 반복 확인됐습니다. 여러 명이 순서대로 작업을 이어받으면 되는 경우라면 **순차 릴레이**가 더 안정적입니다(기본 제공 파이프라인 4개 전부, 다인원 단계가 전부 순차 릴레이인 이유).',
'3. 단계 순서는 카드의 위/아래 이동 버튼으로 나중에도 바꿀 수 있습니다.',
'',
'<h2 id="scenario">7. 실전 시나리오 4가지</h2>',
'',
'### 시나리오 A — 가벼운 질문 하나',
'📍 **메뉴**: 대화',
'독립 대화에서 `@코드 검증가 이 함수 리뷰해줘`처럼 바로 멘션.',
'결재·Gate 없이 가장 빠르게 답을 받습니다.',
'',
'### 시나리오 B — 작은 기능 하나 개발',
'📍 **메뉴**: 업무 요청 → AI 에이전트 생성 → AI 에이전트 관리 → 내 업무 → 결재',
'업무 요청 제출 → 할 일로 나누기 → "AI 에이전트 생성"(crewai 방식) → 온보딩 승인 → 협업 워크스페이스에서 "이 업무의 AI 에이전트 실행" → 결과물 결재.',
'',
'### 시나리오 C — 복잡한 신규 기능/게임 개발(여러 역할 협업 필요)',
'📍 **메뉴**: Crew 빌더 (또는 업무 요청 → 내 업무)',
'둘 중 하나를 고릅니다 — (1) Crew 빌더로 개발자 여러 명을 즉석에 모아 바로 실행하거나, (2) 정식 업무로 만들어 "소프트웨어 개발 파이프라인"을 실행해 요구분석부터 품질 판정까지 Gate 기준 자동 진행시킵니다.',
'품질·추적성이 중요하면 (2)를, 속도가 중요하면 (1)을 권장합니다.',
'',
'### 시나리오 D — 시장조사·전략 보고서',
'📍 **메뉴**: 팀 관리 (또는 파이프라인 관리)',
'경영/사업전략 역할군(외부 자료 조사가·3C4P SWOT 분석가·사업전략 수립가 등, 이미 기본 등록됨)을 팀으로 묶거나, 6절의 시장조사-전략 파이프라인 예시대로 빌더에서 새 템플릿을 만들어 실행합니다.',
'',
'<h2 id="menu">8. 메뉴 전체 지도</h2>',
'',
'**오늘 할 일(user)**: 대화 · 인박스(결재 대기+실행 실패+낮은 Gate 경고 모음) · 내 업무 · 할 일 보드 · 갤러리(여러 할 일의 결과물 모아보기) · 업무 요청 · 결재 · 성과 리뷰',
'',
'**조회(common, 전원 접근)**: 인력 검색 · 조직도 · 이용 가이드(이 화면)',
'',
'**설정/운영(admin·manager 전용, 일부 admin 전용)**: 대시보드 · 비용 · 공정성 지표 · 활동 기록 · 리소스 매칭/배정 · AI 에이전트 생성 · AI 에이전트 관리 · 사람 인력 관리 · 도구/스킬 Pool · 팀 관리 · 파이프라인 관리 · 지식 베이스 · Crew 빌더 · LLM 설정 최적화(admin 전용) · 모델 레지스트리(admin 전용) · 사용자 관리(admin 전용)',
'',
'<h2 id="trouble">9. 자주 겪는 문제</h2>',
'',
'- **`@멘션`했는데 아무 반응이 없다** → 그 Agent/Team/사람이 이 대화의 참가자가 아닐 가능성이 가장 큽니다.',
'  메시지창에서 `@`를 다시 입력해 자동완성 목록에서 직접 골라 초대부터 되게 하세요.',
'- **방금 만든 Agent가 배정/실행 목록에 안 보인다** → "AI 에이전트 관리"에서 온보딩 승인(온보딩 대기 → 활성)을 먼저 눌러야 합니다.',
'- **Task ID/Brief ID를 넣어 만든 팀(TF팀)이 `@멘션`·배정 후보에 안 보인다** → TF팀은 "온보딩 대기" 상태로 시작합니다. "팀 관리"에서 그 팀 카드의 "**온보딩 시작**" 버튼을 눌러야 활성화됩니다(4절 참고).',
'- **팀을 지우려는데 안 지워진다** → 그 팀에 확정된 배정이 남아 있으면 삭제가 막힙니다 — 할 일 상세의 "배정 취소"로 먼저 정리하세요.',
'- **"조직도"와 "팀 관리"의 "TF팀"이 같은 개념 아닌가?** → 다릅니다. "조직도" 메뉴는 확정된 배정만 모아 보여주는 **읽기 전용 스냅샷**(생성/삭제 불가)이고, "TF팀"은 "팀 관리"에서 **직접 만들고 온보딩·해체시키는 실제 조직**입니다. 헷갈리면 조직도 화면 상단 안내문을 참고하세요.',
'- **"AI 에이전트 생성"에서 새 Agent가 안 만들어지고 재사용 배지만 뜬다** → 같은 할 일(역할)에 이미 Agent가 있으면 새로 안 만들고 기존 것을 재사용합니다 — 의도한 동작입니다.',
'  새로 만들려면 다른 할 일을 고르세요.',
'- **팀/파이프라인 실행이 너무 오래 걸리거나 실패한다** → 참여 인원이 많을수록(특히 순차 릴레이·병행 위임 단계) LLM 왕복 호출이 많아져 시간이 걸립니다.',
'  "AI 에이전트 관리"의 파인튜닝에서 `reasoning`을 꺼 호출 수를 줄일 수 있습니다(5절의 "⚡ 최적화 추천"을 먼저 써보세요).',
'  로컬(Ollama) 모델을 쓰는 단계는 클라우드보다 타임아웃을 길게(5400초) 잡아두지만, 그래도 모델이 크면 수십 분 걸릴 수 있습니다 — "인박스"에서 실행 실패/장시간 대기를 확인하세요.',
'- **실행 버튼을 눌렀는데 비용이 걱정된다** → "실행"이라는 이름이 붙은 버튼(AI 에이전트 실행 / 팀 Agent 실행 / 파이프라인 실행 / Crew 실행 / AI 의견 보기)은 전부 실제 LLM API를 호출해 비용이 발생합니다.',
'  설정 저장(모델 설정·파인튜닝·팀/파이프라인 등록 자체)은 비용이 없습니다.',
'- **자기 자신이 요청한 걸 자기가 승인하고 싶다** → **본인을 배정하는 셀프배정이라면 애초에 결재가 뜨지 않습니다** — 본인 시간만 쓰는 배정은 요청 즉시 자동 확정됩니다(approval_id 없음). 다른 사람·Agent·팀을 배정 요청한 경우에만 결재가 생기고, 그건 요청자 본인이 승인할 수 없게 의도적으로 막혀 있습니다(self-approval 방지) — 다른 관리자가 대신 승인해야 합니다.',
'- **방금 만든 팀이 `hair reset-data` 이후 사라졌다** → 팀(team)도 초기화 대상입니다(Agent/Tool/Skill/파이프라인 템플릿과 달리 팀은 재기동 시 자동으로 다시 채워지지 않는 데이터라서) — 다시 "팀 관리"에서 등록해야 합니다.',
].join("\\n");

async function renderManual(content) {
  content.innerHTML = '<section class="card manual-card">' +
    '<div class="md-content manual-content">' + _renderMarkdown(MANUAL_MARKDOWN) + '</div>' +
    '</section>';
}

boot();
</script>
</body>
</html>
"""


def render_deliverable_view_page(deliverable: dict, task_role_name: str | None) -> str:
    """단독 결과물 뷰 페이지(`GET /deliverables/{id}/view`) — SPA 전체(탭·
    네비게이션)를 띄우지 않고 그 결과물 하나만 담은 가벼운 독립 페이지를
    새 탭에서 연다. 로그인 세션은 그대로 필요하다(`app.py`가
    `Depends(get_current_user)`를 유지 — 비인증 공개 URL이 아니다).

    SPA 안의 `renderContent()`를 그대로 끌어오지 않고 최소한의 자체
    렌더링만 둔다(의도적 — 수백 줄짜리 SPA 전용 함수를 여기로 끌어오면
    결합도가 더 커진다): html은 sandbox iframe, code는 highlight.js,
    markdown/text는 marked.js+DOMPurify, 전부 CDN 로드 실패 시
    textContent로 안전하게 폴백한다(이 플랫폼의 기존 CDN 라이브러리
    전부와 같은 원칙)."""
    title = f"결과물 #{deliverable['id']}"
    if task_role_name:
        title += f" — {task_role_name}"
    payload = json.dumps(
        {"content": deliverable["content"], "content_type": deliverable["content_type"]},
        ensure_ascii=False,
    )
    escaped_title = html.escape(title)
    escaped_content_type = html.escape(deliverable["content_type"])
    escaped_created_at = html.escape(deliverable["created_at"])
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@13.0.3/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px;
    margin: 0 auto; padding: 24px 16px; background: #f7f7f9; color: #1a1a1a; }}
  h1 {{ font-size: 18px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  pre {{ overflow-x: auto; padding: 16px; border-radius: 6px; }}
  .md-content table {{ border-collapse: collapse; }}
  .md-content th, .md-content td {{ border: 1px solid #ccc; padding: 4px 8px; }}
  iframe.html-view {{ width: 100%; height: 80vh; border: 1px solid #ccc; border-radius: 6px; }}
</style>
</head>
<body>
<h1>{escaped_title}</h1>
<div class="meta">content_type: {escaped_content_type} · 작성: {escaped_created_at}</div>
<div id="content"></div>
<script>
const DATA = {payload};
const el = document.getElementById("content");
if (DATA.content_type === "html") {{
  const iframe = document.createElement("iframe");
  iframe.className = "html-view";
  iframe.sandbox = "allow-scripts";
  iframe.srcdoc = DATA.content;
  el.appendChild(iframe);
}} else if (DATA.content_type === "code") {{
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = DATA.content;
  pre.appendChild(code);
  el.appendChild(pre);
  if (window.hljs) {{ hljs.highlightElement(code); }}
}} else if (window.marked && window.DOMPurify) {{
  const div = document.createElement("div");
  div.className = "md-content";
  div.innerHTML = DOMPurify.sanitize(marked.parse(DATA.content));
  el.appendChild(div);
}} else {{
  const pre = document.createElement("pre");
  pre.textContent = DATA.content;
  el.appendChild(pre);
}}
</script>
</body>
</html>
"""
