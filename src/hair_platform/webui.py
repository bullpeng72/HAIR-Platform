"""F11 — Web UI 셸. AgentForge의 웹 UI 패턴(f-string+html.escape, Jinja2 미사용,
빌드 스텝 없는 순수 HTML/CSS/JS)을 그대로 계승한다.

코드 재점검(2026-09-26, R4)에서 발견: SPEC.md §1·F1-F14가 전부 "화면"·"버튼"·
"Web UI 폼"을 명시적으로 요구했는데도, v0-v4 개발과 3라운드의 코드 재점검을
거치는 동안 HTML/CSS/JS가 단 한 줄도 없었다 — 지금까지 만든 건 잘 테스트된
JSON REST API뿐, 브라우저에서 조작 가능한 화면은 전무했다(SPEC.md §1: "전 기능을
인증된 Web UI에서 조작 가능하게 하는 것이 AgentForge와의 핵심 차이다").

이 모듈은 그 화면을 채운다 — 단일 HTML 페이지 + 바닐라 JS(프레임워크·빌드
스텝 없음)로, 이미 구현·검증된 JSON API(`app.py`)를 그대로 호출한다. 새 비즈니스
로직은 없다 — 순수 프레젠테이션 레이어다.
"""

from __future__ import annotations

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
  :root { color-scheme: light; }
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; background: #f5f6f8; color: #1b1f24; }
  header { background: #14213d; color: #fff; padding: 0.75rem 1rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  header h1 { font-size: 1.1rem; margin: 0; }
  header .spacer { flex: 1; }
  header button { background: #fca311; border: none; padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer; }
  nav { background: #fff; border-bottom: 1px solid #ddd; }
  .nav-group { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem; padding: 0.4rem 0.5rem; }
  .nav-group + .nav-group { border-top: 1px dashed #e2e4e8; }
  .nav-group.admin-group { background: #fff7ec; }
  .nav-label { font-size: 0.72rem; font-weight: 600; color: #888; margin-right: 0.4rem; min-width: 72px; }
  .nav-group.admin-group .nav-label { color: #a15c00; }
  nav button { background: none; border: 1px solid #ccc; padding: 0.35rem 0.7rem; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }
  nav button.active { background: #14213d; color: #fff; border-color: #14213d; }
  .nav-group.admin-group button { border-color: #f0c98a; }
  .nav-group.admin-group button.active { background: #a15c00; border-color: #a15c00; }
  .badge-adminonly { font-size: 0.65rem; background: #a15c00; color: #fff; border-radius: 3px; padding: 0 0.3rem; margin-left: 0.3rem; }
  main { padding: 1rem; max-width: 1100px; margin: 0 auto; }
  section.card { background: #fff; border: 1px solid #e2e4e8; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
  .deliverable-card { background: #f9fafb; border: 1px solid #e2e4e8; border-radius: 6px; padding: 0.6rem 0.75rem; margin-bottom: 0.6rem; }
  .deliverable-card + .deliverable-card { margin-top: 0; }
  .deliverable-card.chat-human { margin-right: 12%; border-left: 3px solid #2563eb; }
  .deliverable-card.chat-agent { margin-left: 12%; border-left: 3px solid #7c3aed; background: #f5f3ff; }
  .mention-dropdown { position: absolute; z-index: 20; top: 100%; left: 0; width: 100%; max-height: 10rem; overflow-y: auto; margin: 2px 0 0; padding: 0.2rem 0; list-style: none; background: #fff; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.12); }
  .mention-dropdown li { padding: 0.3rem 0.6rem; cursor: pointer; font-size: 0.85rem; }
  .mention-dropdown li:hover, .mention-dropdown li.active { background: #eef6ff; }
  .gate-badge { display: inline-block; padding: 0.05rem 0.4rem; border-radius: 3px; font-size: 0.85em; }
  .gate-badge.gate-ok { background: #dcfce7; color: #166534; }
  .gate-badge.gate-warn { background: #fef3c7; color: #92400e; }
  .final-result-card { background: #f0fdf4; border: 1px solid #86efac; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; }
  .dashboard-grid { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .dashboard-grid .card { flex: 1; min-width: 200px; margin-bottom: 0; }
  .dashboard-grid .stat-row { display: flex; justify-content: space-between; padding: 0.15rem 0; border-bottom: 1px solid #eee; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }
  th { background: #f0f1f4; }
  input, select, textarea { padding: 0.35rem; border: 1px solid #ccc; border-radius: 4px; font-size: 0.85rem; }
  form.inline { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 0.75rem; }
  button.primary { background: #14213d; color: #fff; border: none; padding: 0.4rem 0.9rem; border-radius: 4px; cursor: pointer; }
  button.danger { background: #d64545; color: #fff; border: none; padding: 0.3rem 0.6rem; border-radius: 4px; cursor: pointer; }
  button.small { padding: 0.25rem 0.5rem; font-size: 0.8rem; cursor: pointer; }
  .msg { padding: 0.5rem 0.75rem; border-radius: 4px; margin-bottom: 0.75rem; font-size: 0.85rem; }
   .msg.error { background: #fde2e2; color: #7a1212; }
   .msg.ok { background: #e3f6e5; color: #185c2c; }
   .msg.warn { background: #fff4d6; color: #7a5c00; }
   .badge-reused { font-size: 0.65rem; background: #a15c00; color: #fff; border-radius: 3px; padding: 0 0.3rem; margin-left: 0.3rem; }
  .muted { color: #777; font-size: 0.8rem; }
  .help-list { margin: 0.3rem 0 0.75rem; padding-left: 1.1rem; }
  .help-list li { margin-bottom: 0.35rem; line-height: 1.4; }
  .help-list li:last-child { margin-bottom: 0; }
  .skill-picker { display: inline-flex; flex-direction: column; gap: 0.3rem; }
  .skill-picker-row { display: flex; gap: 0.3rem; }
  .skill-picker-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .skill-chip { display: inline-flex; align-items: center; gap: 0.25rem; background: #eef1f6; border-radius: 12px; padding: 0.1rem 0.3rem 0.1rem 0.6rem; font-size: 0.8rem; }
  .skill-chip button { border: none; background: none; cursor: pointer; font-size: 0.9rem; line-height: 1; padding: 0 0.2rem; color: #666; }
  table { table-layout: fixed; }
  td, th { overflow-wrap: break-word; }
  td.col-actions, th.col-actions { width: 220px; }
  td.col-output, th.col-output { width: 280px; }
  .actions { display: flex; flex-direction: column; align-items: stretch; gap: 0.3rem; }
  .actions > button, .actions > select, .actions > input { width: 100%; box-sizing: border-box; }
  .actions .pair { display: flex; gap: 0.2rem; }
  .actions .pair input, .actions .pair select { width: 0; min-width: 0; flex: 1; }
  .actions .pair button { width: auto; flex-shrink: 0; }
  #login-view { display: flex; align-items: center; justify-content: center; height: 100vh; }
  #login-view .card { width: 320px; }
  #login-view input { width: 100%; margin-bottom: 0.5rem; box-sizing: border-box; }
  code { background: #f0f1f4; padding: 0 0.25rem; border-radius: 3px; }

  /* 사용자 요청(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT,
     HTML 등 다양할 수 있을텐데... Tier 2까지 진행해줘") — content_type별
     표시 스타일. .code-block 안의 토큰 클래스는 renderContent()의 경량
     정규식 하이라이터가 붙인다(완전한 파서가 아니라 식별자/문자열/주석/
     숫자/일부 키워드만 색을 입히는 수준 — 외부 라이브러리 없이 안전하게
     처리하려는 기존 관례를 그대로 따른다). */
  .code-block { background: #1e1e1e; color: #d4d4d4; border-radius: 6px; padding: 0.6rem 0.75rem; overflow-x: auto; }
  .code-block code { background: transparent; padding: 0; color: inherit; }
  .code-block .tok-kw { color: #569cd6; }
  .code-block .tok-str { color: #ce9178; }
  .code-block .tok-com { color: #6a9955; }
  .code-block .tok-num { color: #b5cea8; }
  .md-content h1, .md-content h2, .md-content h3,
  .md-content h4, .md-content h5, .md-content h6 { margin: 0.4rem 0 0.2rem; }
  .md-content li { margin-left: 1rem; }
  .html-preview { width: 100%; min-height: 240px; border: 1px solid #e2e4e8; border-radius: 6px; background: #fff; }

  /* Phase 5(사용자 요청, 2026-09-28, "반응형만 포함(내 업무/알림 확인용)")
     — 이 앱은 고정 1100px 레이아웃(과거 다크모드 회귀 회피와 같은 이유로
     의도적 단순화)이었다. 전체 반응형 재설계는 스코프 밖으로 명시적으로
     뺐고, 사용자가 고른 범위대로 "인박스"·"내 업무"(승인 알림 확인의
     실질적 진입점)만 좁게 고친다 — 표가 많은 관리자 화면은 건드리지
     않는다(그 화면들까지 좁히면 표가 깨질 위험이 더 크다). */
  @media (max-width: 640px) {
    main { max-width: 100%; padding: 0.6rem; }
    header { padding: 0.6rem; gap: 0.5rem; }
    header h1 { font-size: 1rem; }
    .deliverable-card.chat-human { margin-right: 4%; }
    .deliverable-card.chat-agent { margin-left: 4%; }
    .dashboard-grid { flex-direction: column; }
    .dashboard-grid .card { min-width: 0; }
  }
</style>
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

// 경량 구문 강조 — 완전한 토크나이저가 아니라 문자열/주석/숫자/흔한
// 키워드만 정규식으로 색을 입힌다(2026-10-07, "Tier 2까지 진행해줘").
// 입력은 호출자가 이미 esc()로 이스케이프한 문자열이라는 전제라, 여기서
// 추가하는 <span> 태그 외에는 원본 텍스트에 있던 "<"/">"가 전부 엔티티로
// 바뀌어 있어 새 HTML을 주입할 수 없다 — 그래서 외부 라이브러리(마크다운
// 렌더러 등을 안 쓰기로 한 기존 결정, 위 주석 참고) 없이도 안전하다.
const _CODE_KEYWORDS = /\\b(function|const|let|var|return|if|else|for|while|def|class|import|from|export|async|await|try|except|catch|finally|new|this|self)\\b/g;
function highlightCode(escaped) {
  // 순서 중요 — 키워드 치환을 가장 먼저 해야 한다. 나중에 하면 뒤 패스들이
  // 이미 넣은 `class="tok-..."` 속성의 "class"라는 글자 자체를 키워드로
  // 다시 매칭해 중첩·깨진 span을 만든다(실측으로 발견, 2026-10-07).
  return escaped
    .replace(_CODE_KEYWORDS, '<span class="tok-kw">$1</span>')
    .replace(/(&quot;.*?&quot;|&#39;.*?&#39;)/g, '<span class="tok-str">$1</span>')
    .replace(/(\\/\\/.*$|#.*$)/gm, '<span class="tok-com">$1</span>')
    .replace(/\\b(\\d+)\\b/g, '<span class="tok-num">$1</span>');
}

// 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 1/2 실행 중) — Agent/사람
// 산출물에 마크다운 코드펜스(```)나 **굵게**가 섞여 있어도 전부 이스케이프
// 후 줄바꿈만 <br>로 바꿔 보여줘, 백틱·별표가 그대로 노출돼 가독성이
// 떨어졌다(코드 패치 설명 같은 산출물일수록 심함).
//
// Tier 2(사용자 요청, 2026-10-07, "HAIR 협업 결과물이 코드, 마크다운,
// PPT, HTML 등 다양할 수 있을텐데... Tier 2까지 진행해줘") — content_type
// 별로 다르게 그린다. markdown/code/text 세 경로 모두 esc()로 이스케이프한
// "뒤에" 정규식으로 안전한 태그만 덧씌우므로(원본의 "<"/">"는 이미
// 엔티티라 다시 해석되지 않음) XSS 위험이 없다 — 완전한 마크다운 렌더러를
// 새로 들이지 않기로 한 기존 결정을 그대로 유지한다. html만 예외: 그
// 자체가 실행 가능한 HTML이라 브라우저가 파싱해야 하므로, 부모 페이지와
// 완전히 분리된 sandboxed <iframe>(allow-same-origin 없음 — 그 안의
// 스크립트가 부모 쿠키/DOM/세션에 접근할 수 없음) 안에서만 렌더링한다.
// 실측으로 발견한 버그(2026-10-07, 실제 서버에 실제 gpt-4o-mini로 시나리오를
// 돌려보다 발견 — 리뷰 서버의 손수 만든 가짜 응답으로는 한 번도 안 드러났다):
// content_type을 "html"/"markdown"으로 지정해도 실제 LLM은 응답 전체를
// ```html ... ``` / ```markdown ... ``` 한 겹으로 또 감싸는 경우가 흔하다.
// html은 그 결과 iframe 위에 "```html" 글자가 그대로 떠 보기 흉했고,
// markdown은 더 심각했다 — 전체가 "```markdown 코드 펜스" 하나로 인식돼
// highlightCode()의 주석 강조 규칙(`#.*$`)이 마크다운 헤딩(`#`/`##`)을
// 전부 "주석"으로 착각해 <span>으로 감싸버리는 바람에, 그 아래 헤딩/목록
// 정규식이 더 이상 줄 맨 앞에서 "#"/"-"를 못 찾아 헤딩이 전혀 안 먹혔다
// (Node로 실제 응답을 직접 렌더링해 재현·확인). 응답 "전체"가 코드
// 펜스 하나로만 감싸여 있을 때만 그 바깥 펜스를 벗겨내고 안쪽 내용으로
// 렌더링한다 — 본문 중간에 섞인 펜스(예: 시나리오 1의 설명+코드 혼합
// 응답)는 그대로 둔다(그건 의도된 "그 형식을 먼저 보여주는" 문서지,
// 벗겨낼 "감싸기"가 아니다).
function stripOuterFence(raw) {
  const m = raw.match(/^```[a-zA-Z0-9]*\\n([\\s\\S]*?)\\n?```\\s*$/);
  return m ? m[1] : raw;
}

function renderContent(raw, contentType) {
  if (contentType === "html" || contentType === "code" || contentType === "markdown") {
    raw = stripOuterFence(raw);
  }
  if (contentType === "html") {
    return '<iframe class="html-preview" sandbox="allow-scripts" srcdoc="' + escAttr(raw) + '"></iframe>';
  }
  if (contentType === "code") {
    return '<pre class="code-block"><code>' + highlightCode(esc(raw)) + '</code></pre>';
  }
  const escaped = esc(raw);
  const withCodeBlocks = escaped.replace(
    /```[a-zA-Z0-9]*\\n([\\s\\S]*?)```/g,
    (_match, code) => '<pre class="code-block"><code>' + highlightCode(code.replace(/\\n$/, "")) + "</code></pre>"
  );
  if (contentType !== "markdown") {
    return withCodeBlocks
      .replace(/\\*\\*(.+?)\\*\\*/g, "<strong>$1</strong>")
      .replace(/\\n/g, "<br>");
  }
  // markdown — 헤딩/목록/인라인 코드까지 조금 더 넓게 처리한다(여전히
  // esc() 이후의 안전한 치환일 뿐, 별도 파서 라이브러리는 안 씀).
  return '<div class="md-content">' + withCodeBlocks
    .replace(/^###### (.+)$/gm, "<h6>$1</h6>")
    .replace(/^##### (.+)$/gm, "<h5>$1</h5>")
    .replace(/^#### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\\*\\*(.+?)\\*\\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/\\n/g, "<br>") + '</div>';
}

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

function qs(path, params) {
  const usp = new URLSearchParams();
  for (const k in params) {
    if (params[k] !== undefined && params[k] !== null && params[k] !== "") usp.set(k, params[k]);
  }
  const s = usp.toString();
  return s ? (path + "?" + s) : path;
}

function showMsg(elId, text, kind) {
  const el = document.getElementById(elId);
  if (!text) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="msg ' + (kind || "ok") + '">' + esc(text) + "</div>";
}

/* 실제 LLM API를 호출해 오래 걸릴 수 있는 버튼(Crew 실행, AI 에이전트
   생성, 개별 실행, 지식 색인)에 "처리 중" 표시를 준다 — 요청 중 버튼을
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

const LABELS = {
  human: "사람", agent: "AI 에이전트", team: "팀",
  brief: "업무 요청", assignment: "배정", deliverable: "결과물", human_pool: "사람 인력", agent_pool: "AI 에이전트",
  agent_execution_jobs: "AI 실행 작업",
  job: "생성 작업", job_item: "생성 항목", approval: "결재", user: "사용자",
  pending: "대기중", waiting: "대기(이전 단계)", approved: "승인됨", rejected: "반려됨", skipped: "건너뜀",
  requested: "요청됨", confirmed: "확정됨", superseded: "대체됨",
  running: "진행중", succeeded: "성공", failed: "실패", partial: "일부 실패",
  submitted: "제출됨", reviewed: "검토됨",
  available: "가능", unavailable: "불가", offboarded: "오프보딩됨",
  active: "활성", pending_onboarding: "온보딩 대기", deprecated: "비활성화됨",
  draft: "초안", pending_review: "리뷰 대기", revised: "수정됨",
  // ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4) — 할 일 보드 상태/우선순위.
  backlog: "백로그", queued: "대기", in_progress: "진행중", done: "완료",
  low: "낮음", medium: "보통", high: "높음", urgent: "긴급",
  task_dependencies: "의존관계", tasks: "업무",
};
function label(v) { return LABELS[v] || v; }

function headerColClass(h) {
  if (h === "동작") return "col-actions";
  if (h === "최근 산출물") return "col-output";
  return "";
}

function table(headers, rows) {
  let html = "<table><thead><tr>";
  for (const h of headers) {
    const cls = headerColClass(h);
    html += "<th" + (cls ? ' class="' + cls + '"' : "") + ">" + esc(h) + "</th>";
  }
  html += "</tr></thead><tbody>";
  if (!rows.length) {
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
  html += "</tbody></table>";
  return html;
}

/* Phase 5(사용자 요청, 2026-09-28, "협업 워크스페이스 UX 개선" — Skill
   자동완성). skill_pool에 등록된 이름을 <datalist>로 내려줘 스킬을 자유
   텍스트로 입력하는 곳마다 브라우저 기본 자동완성이 뜨게 한다 — Skill
   주입(§4)이 skill_pool 이름과 "정확히 일치"해야 매칭되므로, 오타로
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
    models = await api("GET", "/api/model-registry");
  } catch (e) {
    return byProvider;
  }
  models.filter(m => m.is_active).forEach(m => {
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
   화면상에서도 명확히 구분한다(ADR.md 결정 9).

   사용자 요청(2026-09-29, "HAIR 관점에서 외부 사례들을 조사하고... 관리자와
   사용자 고유 업무와 공통 업무를 구분해줘" → "로그인한 계정 유형에 맞춰
   메뉴 노출을 개선해줘")에 따라 group을 둘에서 셋으로 늘렸다
   (`docs/ROLE_TASK_CLASSIFICATION.md` 실측 재분류 그대로 반영):
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
   그대로 따른다. */
const TABS = [
  { id: "conversations", label: "대화", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderConversations },
  { id: "inbox", label: "인박스", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderInbox },
  { id: "mywork", label: "내 업무", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderMyWork },
  { id: "board", label: "할 일 보드", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBoard },
  { id: "briefs", label: "업무 요청", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBriefs },
  { id: "approvals", label: "결재", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderApprovals },
  { id: "performance", label: "성과 리뷰", group: "user", roles: ["admin", "manager", "member"], render: renderPerformance },
  { id: "search", label: "인력 검색", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderSearch },
  { id: "orgchart", label: "조직도", group: "common", roles: ["admin", "manager", "member", "viewer"], render: renderOrgChart },
  { id: "dashboard", label: "대시보드", group: "admin", roles: ["admin", "manager"], render: renderDashboard },
  { id: "cost", label: "비용", group: "admin", roles: ["admin", "manager"], render: renderCost },
  { id: "fairness", label: "공정성 지표", group: "admin", roles: ["admin", "manager"], render: renderFairness },
  { id: "audit", label: "활동 기록", group: "admin", roles: ["admin", "manager"], render: renderAudit },
  { id: "match", label: "인력 매칭/배정", group: "admin", roles: ["admin", "manager"], render: renderMatch },
  { id: "jobs", label: "AI 에이전트 생성", group: "admin", roles: ["admin", "manager"], render: renderJobs },
  { id: "humanpool", label: "사람 인력 관리", group: "admin", roles: ["admin", "manager"], render: renderHumanPool },
  { id: "agentpool", label: "AI 에이전트 관리", group: "admin", roles: ["admin", "manager"], render: renderAgentPool },
  { id: "poolregistry", label: "도구/스킬 Pool", group: "admin", roles: ["admin", "manager"], render: renderPoolRegistry },
  { id: "teams", label: "팀 관리", group: "admin", roles: ["admin", "manager"], render: renderTeams },
  { id: "modelregistry", label: "모델 레지스트리", group: "admin", roles: ["admin", "manager"], render: renderModelRegistry },
  { id: "crew", label: "Crew 실행", group: "admin", roles: ["admin", "manager"], render: renderCrew },
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
  } catch (e) {
    ME = null;
    document.getElementById("app-view").style.display = "none";
    document.getElementById("login-view").style.display = "flex";
  }
}

// ASANA_PARITY_DESIGN_PLAN.md F20(2026-10-08, Phase 5) — **탭 재구성**:
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
// 묶었다 — `docs/LIMITS.md`에 이 해석을 정직하게 기록해둔다.
function buildNav() {
  const nav = document.getElementById("tabs");
  nav.innerHTML = "";

  const todayTabs = TABS.filter(
    t => (t.group === "user" || t.group === "common") && t.roles.includes(ME.role)
  );
  const settingsTabs = TABS.filter(t => t.group === "admin" && t.roles.includes(ME.role));

  const addGroup = (label, tabs, cls) => {
    if (!tabs.length) return;
    const row = document.createElement("div");
    row.className = "nav-group" + (cls ? " " + cls : "");
    const tag = document.createElement("span");
    tag.className = "nav-label";
    tag.textContent = label;
    row.appendChild(tag);
    for (const t of tabs) {
      const btn = document.createElement("button");
      btn.textContent = t.label;
      if (t.adminOnly) {
        const badge = document.createElement("span");
        badge.className = "badge-adminonly";
        badge.textContent = "관리자 전용";
        btn.appendChild(badge);
      }
      btn.dataset.tab = t.id;
      btn.onclick = () => selectTab(t.id);
      row.appendChild(btn);
    }
    nav.appendChild(row);
  };

  addGroup("오늘 할 일", todayTabs, "");
  addGroup("설정", settingsTabs, "admin-group");
}

let currentTab = null;

async function selectTab(id) {
  // ASANA_PARITY_DESIGN_PLAN.md §9-6 "경량 폴링" — 대화 화면을 벗어나면
  // 어느 쪽에서 열었든(독립 "대화" 탭/Task 워크스페이스 임베드) 반드시
  // 멈춘다(단일 전역 타이머라 여기 한 곳에서만 끊어도 충분하다).
  _stopConversationPolling();
  currentTab = id;
  for (const btn of document.querySelectorAll("#tabs button")) {
    btn.classList.toggle("active", btn.dataset.tab === id);
  }
  showMsg("global-msg", "", "ok");
  const tab = TABS.find(t => t.id === id);
  const content = document.getElementById("content");
  content.innerHTML = "<p class=\\"muted\\">불러오는 중...</p>";
  try {
    await tab.render(content);
  } catch (e) {
    content.innerHTML = "";
    showMsg("global-msg", e.message, "error");
  }
}

function refresh() { if (currentTab) selectTab(currentTab); }

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

/* ---------- 대화 중심 작업공간(Conversational Workspace, §9,
   2026-10-08, 사용자 요청 "로그인하면 기본적으로 대화창이 주어지고...
   사람이나 에이전트(에이전트 그룹)를 호출하거나 skill을 사용하여
   업무를 수행"). TABS 배열 맨 앞(로그인 직후 첫 화면)에 둔다. ---------- */

let _conversationPollTimer = null;

function _stopConversationPolling() {
  if (_conversationPollTimer) {
    clearInterval(_conversationPollTimer);
    _conversationPollTimer = null;
  }
}

// 사용자 보고(2026-10-08, "응답을 기다리는 중…으로 나오고 대화에
// 모델의 응답이 표시되지 않음") — 원래는 메시지를 보낸 뒤 "응답을
// 기다리는 중…" 문구만 한 번 띄우고 끝이라, 실제로 응답이 도착해도
// (Team 멘션처럼 CrewAI가 몇 분씩 걸리는 경우 특히, §80 실측 — 약
// 6분) 그 문구가 그대로 남아 있어 "멈췄다/안 된다"로 오인하게 했다.
// Task 워크스페이스의 `pollExecutionJob()`과 같은 패턴을 대화 쪽에도
// 적용해 완료/실패를 실제로 확인하고 상태 문구를 갱신한다.
async function pollConversationJob(conversationId, jobId, intervalMs, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 400000); // Team 멘션(CrewAI)은 수 분 걸릴 수 있음
  while (Date.now() < deadline) {
    const jobs = await api("GET", "/api/conversations/" + conversationId + "/jobs");
    const job = jobs.find(j => j.id === jobId);
    if (job && job.status !== "running") return job;
    await new Promise(r => setTimeout(r, intervalMs || 2000));
  }
  throw new Error("응답 완료 확인 시간이 초과됐습니다 — 잠시 후 새로고침해 주세요.");
}

async function renderConversations(content) {
  _stopConversationPolling();
  const list = await api("GET", "/api/conversations");
  content.innerHTML =
    '<section class="card"><h2>대화</h2>' +
    '<ul class="muted help-list">' +
    '<li>로그인하면 바로 쓸 수 있는 대화 공간입니다 — 사람·Agent·Agent 그룹(Team)을 불러 대화하듯 업무를 처리하세요.</li>' +
    '<li>대화가 무거워지면 "정식 업무 요청으로 전환" 버튼으로 기존 업무 요청(Brief) 흐름에 올릴 수 있습니다 — 가벼운 대화와 정식 절차가 공존합니다.</li>' +
    '</ul>' +
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
      if (!window.confirm("이 대화를 메시지·참가자까지 전부 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
      try {
        await api("DELETE", "/api/conversations/" + btn.dataset.deleteConversation);
        showMsg("global-msg", "대화를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-leave-conversation]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 대화에서 나갑니다. 계속할까요?")) return;
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

async function refreshConversationMessages(conversationId, participants, llmModel) {
  const box = document.getElementById("conv-messages");
  if (!box) return;
  try {
    const messages = await api("GET", "/api/conversations/" + conversationId + "/messages");
    box.innerHTML = messages.length ? messages.map(m => _renderConversationMessage(m, participants, llmModel)).join("")
      : '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>';
  } catch (e) {
    // 참가자가 아니게 됐으면(제거됨) 403 — 조용히 무시.
  }
}

function _conversationAuthorLabel(authorType, authorId, participants, llmModel) {
  if (authorType === "human") return "사용자 #" + authorId;
  if (authorId === conversationsDefaultAiAuthorId) {
    return "AI" + (llmModel ? "(" + esc(llmModel) + ")" : "");
  }
  const p = participants.find(p => p.participant_type === "agent" && p.participant_id === authorId);
  return p ? esc(p.display_name) + " (AI)" : "AI 에이전트 #" + authorId;
}

// `conversation_messages.author_id`의 예약 sentinel — 실존 agent_pool
// 행이 없는 "대화 자체의 기본 AI"를 가리킨다(`conversations.py`의
// `DEFAULT_AI_AUTHOR_ID`와 반드시 같은 값이어야 한다 — agent_pool.id
// 는 AUTOINCREMENT라 0이 될 수 없어 안전한 sentinel이다).
const conversationsDefaultAiAuthorId = 0;

function _renderConversationMessage(m, participants, llmModel) {
  const side = m.author_type === "agent" ? "chat-agent" : "chat-human";
  const who = _conversationAuthorLabel(m.author_type, m.author_id, participants, llmModel);
  const skillBadge = m.used_skill
    ? ' <span class="gate-badge gate-ok">스킬: ' + esc(m.used_skill) + '</span>'
    : '';
  return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted">' + who + skillBadge + ' · ' + esc(m.created_at) + '</div>' +
    '<div>' + renderContent(m.content) + '</div>' +
    '</div>';
}

async function renderConversationDetail(conversationId, containerId) {
  _stopConversationPolling();
  const detail = document.getElementById(containerId);
  if (!detail) return;
  detail.dataset.conversationId = String(conversationId);
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';

  const modelsByProvider = await fetchModelsByProvider();
  let conv, participants, messages, skillNames;
  try {
    conv = await api("GET", "/api/conversations/" + conversationId);
    participants = await api("GET", "/api/conversations/" + conversationId + "/participants");
    messages = await api("GET", "/api/conversations/" + conversationId + "/messages");
    skillNames = await api("GET", "/api/skill-pool/names");
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  const participantRows = participants.map(p =>
    '<li>' + esc(label(p.participant_type)) + ' ' + esc(p.display_name || ("#" + p.participant_id)) + '</li>'
  ).join("");

  const messageCards = messages.map(m => _renderConversationMessage(m, participants, conv.llm_model)).join("");

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
    '<button class="small" id="conv-promote-btn">정식 업무 요청으로 전환</button>' +
    '</div>' +
    '<p class="muted">AI 자동응답이 켜져 있으면 멘션 없는 메시지에도 매번 LLM 호출이 발생합니다(비용 발생). provider/model을 고른 뒤 "모델 설정 저장"을 눌러야 반영됩니다.</p>' +
    '<h4>참가자</h4>' +
    '<ul id="conv-participant-list">' + participantRows + '</ul>' +
    (participantRows ? '' : '<p class="muted" id="conv-participant-empty-hint">참가자가 없습니다.</p>') +
    '<div style="position:relative;margin-bottom:0.75rem">' +
    '<input id="conv-invite-input" placeholder="이름으로 검색해 초대(사람·Agent·Team, 조직 전체 검색)" style="width:100%;box-sizing:border-box" autocomplete="off">' +
    '<ul class="mention-dropdown" id="conv-invite-dropdown" style="display:none"></ul>' +
    '</div>' +
    '<h4>대화</h4>' +
    '<div id="conv-messages">' + (messageCards || '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>') + '</div>' +
    '<form class="inline" id="conv-message-form" style="margin-top:0.5rem;flex-wrap:wrap">' +
    '<select id="conv-skill-select"><option value="">스킬 선택 안 함</option>' +
    skillNames.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join("") +
    '</select>' +
    '<div class="mention-wrap" style="position:relative;flex:1;min-width:200px">' +
    '<input id="conv-message-input" placeholder="메시지(@참가자이름으로 호출 가능)" style="width:100%;box-sizing:border-box" autocomplete="off">' +
    '<ul class="mention-dropdown" id="conv-mention-dropdown" style="display:none"></ul>' +
    '</div>' +
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
  // 사용자 요청(2026-10-10, "채팅에서 @로 호출하려면 미리 전체 이름을
  // 알고 있어야 해") — 메시지창 자동완성이 이미 초대된 참가자만 보던
  // 것을, 조직 전체 검색(`/api/conversations/mentionable`, 이미
  // "참가자 초대" 입력창이 쓰던 바로 그 엔드포인트)까지 함께 보도록
  // 확장한다. 미초대 대상을 드롭다운에서 고르면 별도 화면 이동 없이
  // 그 자리에서 바로 초대한 뒤 멘션을 끼워 넣는다(`_inviteAndTrackParticipant`).
  setupMentionAutocomplete(mentionInput, mentionDropdown, participants, {
    search: (q) => api("GET", qs("/api/conversations/mentionable", { q })),
    onInvited: (item) => _inviteAndTrackParticipant(conversationId, participants, item),
  });

  // 삭제/나가기 뒤의 새로고침은 `refresh()`(전역, 현재 탭을 다시
  // 렌더)로 한다 — 이 함수는 독립 "대화" 탭과 Task 워크스페이스
  // 임베드(`_loadTaskConversation`) 양쪽에서 호출되므로, 둘 중 어느
  // 쪽에서 열렸는지 모르는 채로도 올바른 화면을 다시 그려준다.
  const deleteBtn = document.getElementById("conv-delete-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      if (!window.confirm("이 대화를 메시지·참가자까지 전부 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
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
      if (!window.confirm("이 대화에서 나갑니다. 계속할까요?")) return;
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

  document.getElementById("conv-promote-btn").addEventListener("click", async () => {
    // 사용자 보고(2026-10-08, "'정식 업무 요청으로 전환'시 모든 대화
    // 내용이 업무 요청 목록 '내용'으로 전달되고 있어, 이 방식을
    // 개선해줘") — 원래는 대화 전체를 raw_text로 그대로 넘겨 Brief를
    // **자동 생성**했다. 사람이 걸러낼 기회 없이 잡담·중복·주제 이탈
    // 메시지까지 그대로 정식 업무 요청의 "내용"이 돼버리는 게 문제였다.
    // "이걸 결과물로 제출"(F15 Phase 3)과 같은 원칙으로 바꾼다 — 자동
    // 생성 대신 기존 "업무 요청" 폼의 textarea에 미리 채워만 주고,
    // 사람이 다듬은 뒤 그 폼의 기존 "제출" 버튼을 직접 눌러야 실제
    // Brief가 된다(새 제출 경로를 만들지 않는다).
    if (!messages.length) {
      showMsg("global-msg", "전환할 메시지가 없습니다.", "error");
      return;
    }
    const transcript = messages.map(m =>
      (m.author_type === "agent" ? "AI" : "사용자") + ": " + m.content
    ).join("\\n");
    await selectTab("briefs");
    const textarea = document.getElementById("brief-text");
    if (textarea) {
      textarea.value = transcript;
      textarea.scrollIntoView({ behavior: "smooth", block: "center" });
      textarea.focus();
    }
    showMsg("global-msg", "대화 내용을 업무 요청 작성란에 채웠습니다 — 내용을 다듬은 뒤 \\"제출\\"을 눌러주세요.", "ok");
  });

  // 참가자 검색-초대: 입력마다 조직 전체에서 검색(디바운스)하고,
  // 후보를 클릭하면 바로 초대한다(사용자 결정 4 — 검색 범위는 조직
  // 전체, 같은 Brief/Task로 좁히지 않는다).
  const inviteInput = document.getElementById("conv-invite-input");
  const inviteDropdown = document.getElementById("conv-invite-dropdown");
  let inviteDebounce = null;
  inviteInput.addEventListener("input", () => {
    clearTimeout(inviteDebounce);
    const q = inviteInput.value.trim();
    if (!q) { inviteDropdown.style.display = "none"; inviteDropdown.innerHTML = ""; return; }
    inviteDebounce = setTimeout(async () => {
      let results;
      try {
        results = await api("GET", qs("/api/conversations/mentionable", { q }));
      } catch (e) { return; }
      const items = [
        ...results.humans.map(h => ({ type: "human", id: h.id, name: h.username })),
        ...results.agents.map(a => ({ type: "agent", id: a.id, name: a.role_name })),
        ...results.teams.map(t => ({ type: "team", id: t.id, name: t.name })),
      ];
      if (!items.length) { inviteDropdown.style.display = "none"; inviteDropdown.innerHTML = ""; return; }
      const TYPE_LABEL = { human: "사람", agent: "AI 에이전트", team: "팀" };
      inviteDropdown.innerHTML = items.map(it =>
        '<li data-invite-type="' + it.type + '" data-invite-id="' + it.id + '">' +
        esc(it.name) + ' <span class="muted">(' + TYPE_LABEL[it.type] + ')</span></li>'
      ).join("");
      inviteDropdown.style.display = "block";
    }, 250);
  });
  inviteInput.addEventListener("blur", () => setTimeout(() => { inviteDropdown.style.display = "none"; }, 150));
  inviteDropdown.addEventListener("mousedown", async (ev) => {
    const li = ev.target.closest("[data-invite-type]");
    if (!li) return;
    ev.preventDefault();
    try {
      await api("POST", "/api/conversations/" + conversationId + "/participants", {
        participant_type: li.dataset.inviteType,
        participant_id: Number(li.dataset.inviteId),
      });
      showMsg("global-msg", "초대했습니다.", "ok");
      renderConversationDetail(conversationId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("conv-message-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("conv-message-input");
    const content = input.value.trim();
    if (!content) return;
    const skill = document.getElementById("conv-skill-select").value || undefined;
    try {
      const payload = { content };
      if (skill) payload.skill = skill;
      const result = await api("POST", "/api/conversations/" + conversationId + "/messages", payload);
      input.value = "";
      document.getElementById("conv-skill-select").value = "";
      await refreshConversationMessages(conversationId, participants, conv.llm_model);
      if (result.triggered_jobs && result.triggered_jobs.length) {
        showMsg("global-msg", "응답을 기다리는 중…(Agent/Team 호출은 몇 분 걸릴 수 있습니다)", "ok");
        Promise.all(
          result.triggered_jobs.map(j => pollConversationJob(conversationId, j.job_id))
        ).then((jobs) => {
          refreshConversationMessages(conversationId, participants, conv.llm_model);
          const failed = jobs.filter(j => j.status === "failed");
          showMsg(
            "global-msg",
            failed.length
              ? "일부 응답이 실패했습니다: " + failed.map(j => j.error).join(", ")
              : "응답이 도착했습니다.",
            failed.length ? "error" : "ok"
          );
        }).catch((e) => {
          refreshConversationMessages(conversationId, participants, conv.llm_model);
          showMsg("global-msg", e.message, "error");
        });
      }
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 경량 폴링(9-6 로드맵 "구 F21-4") — 이 대화를 보는 동안 주기적으로
  // 새 메시지를 반영한다(다른 참가자/Agent/Team이 비동기로 남긴
  // 메시지도 수동 새로고침 없이 보이게). 다른 화면으로 이동하면
  // `_stopConversationPolling()`이 멈춘다.
  _conversationPollTimer = setInterval(
    () => refreshConversationMessages(conversationId, participants, conv.llm_model),
    6000
  );
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
    // 드롭다운이 갱신되기 전 같은 대상을 두 번 고르는 등 드문 경합은
    // "이미 초대됨"으로 조용히 넘기고 멘션은 그대로 진행한다.
    if (!String(e.message || "").includes("이미 초대")) {
      showMsg("global-msg", e.message, "error");
      throw e;
    }
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

// ASANA_PARITY_DESIGN_PLAN.md §9-5(F21-1 흡수) — 이 Task에 종속된
// 대화(`linked_task_id`)가 이미 있으면 그대로 임베드하고, 없으면
// "대화 시작" 버튼만 보여준다(결과물 작성 전에도 항상 쓸 수 있다 —
// F15 때 발견한 구조적 한계의 직접적인 해소).
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
  };
  const cards = items.map(item => {
    if (item.type === "approval_pending") {
      return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(label(item.subject_type)) + " #" + item.subject_id + '</div>' +
        '<button class="small" data-goto-approvals>결재 화면으로 이동</button></div>';
    }
    if (item.type === "execution_failed") {
      return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(item.role_name) + '</div>' +
        '<div>' + esc(item.error || "") + '</div>' +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(item.role_name) + '</div>' +
      '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
  }).join("");

  // ASANA_PARITY_DESIGN_PLAN.md F20(2026-10-08, Phase 5) — "샘플로
  // 체험하기"는 admin 전용(seed API 자체가 admin 전용)이고, 로그인
  // 직후 가장 먼저 보이는 화면(TABS 배열 맨 앞 = inbox)에 둬야 "로그인
  // 첫 화면"이라는 설계 의도와 맞는다. 처리할 조치가 하나도 없는
  // 빈 인박스일 때만 보여준다 — 이미 데이터가 쌓여 있는 운영 환경에서
  // 매번 보이면 오히려 잡음이다.
  const seedPrompt = (ME.role === "admin" && !items.length)
    ? '<div class="card"><p class="muted">아직 업무 데이터가 없나요?</p>' +
      '<button class="small" id="seed-demo-btn">샘플로 체험하기</button></div>'
    : '';

  content.innerHTML =
    '<section class="card"><h2>내게 필요한 조치</h2>' +
    '<ul class="muted help-list">' +
    '<li>결재 대기·AI 에이전트 실행 실패·낮은 Gate 점수 경고(승인/반려 전인 것만)를 한 곳에 모았습니다.</li>' +
    '<li>완전한 시간순 통합은 아니고 종류별로 묶여 있습니다 — 처리는 각 항목의 원래 화면(결재/내 업무)에서 합니다.</li>' +
    '</ul>' +
    (items.length ? cards : '<p class="muted">지금 조치가 필요한 항목이 없습니다.</p>') +
    seedPrompt +
    '</section>';

  const seedBtn = document.getElementById("seed-demo-btn");
  if (seedBtn) {
    seedBtn.onclick = async () => {
      try {
        const result = await api("POST", "/api/demo/seed", {});
        showMsg("global-msg", "샘플 데이터를 만들었습니다 — '업무 요청' 탭에서 확인하세요.", "ok");
        await selectTab("briefs");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }

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
    '<ul class="muted help-list">' +
    '<li>확정된 배정 중 내가 직접 할 일(사람 배정 당사자)이거나 내가 AI에게 맡긴 일(Agent 배정 요청자)이거나, 내가 속한 팀이 배정된 일(팀 리더/팀원)만 보입니다.</li>' +
    '<li>"열기"를 누르면 그 업무의 협업 워크스페이스(결과물 타임라인)가 아래에 펼쳐집니다.</li>' +
    '</ul>' +
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
      r.latest_status ? esc(label(r.latest_status)) : '<span class="muted">아직 없음</span>',
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

// ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4) — "할 일 보드"
// 4칼럼 칸반(backlog/queued/in_progress/done). 드래그 없이 업무를 열어
// select로 상태를 바꾸는 식으로 단순화했다(설계 문서 UI 설계 그대로).
// "내 업무"와 같은 데이터(참가 중인 task)를 쓴다 — 전체 조직의 모든
// task를 한 보드에 보여주는 것은 범위 밖(그건 기존 "업무 요청" 탭이
// 브리프별로 이미 하고 있다).
async function renderBoard(content) {
  content.innerHTML = '<section class="card"><h2>할 일 보드</h2><p class="muted">불러오는 중…</p></section>';
  const rows = await api("GET", "/api/my-work");
  const details = await Promise.all(
    rows.map(r => api("GET", "/api/tasks/" + r.task_id).catch(() => null))
  );
  const tasks = rows
    .map((r, i) => Object.assign({}, r, details[i]))
    .filter(t => t.status !== undefined);

  const STATUS_COLUMNS = [
    { key: "backlog", label: "백로그" },
    { key: "queued", label: "대기" },
    { key: "in_progress", label: "진행중" },
    { key: "done", label: "완료" },
  ];
  const PRIORITY_BADGE = { urgent: "gate-warn", high: "gate-warn", medium: "gate-ok", low: "gate-ok" };
  const columnsHtml = STATUS_COLUMNS.map(col => {
    const colTasks = tasks.filter(t => t.status === col.key);
    const cards = colTasks.map(t =>
      '<div class="deliverable-card">' +
      '<div><strong>' + esc(t.role_name) + '</strong></div>' +
      '<div><span class="gate-badge ' + (PRIORITY_BADGE[t.priority] || "") + '">' + esc(label(t.priority)) + '</span></div>' +
      '<div class="actions"><button class="small" data-board-open="' + t.task_id + '">열기</button></div>' +
      '</div>'
    ).join("");
    return '<div class="card" style="flex:1;min-width:200px">' +
      '<h4>' + esc(col.label) + ' (' + colTasks.length + ')</h4>' +
      (cards || '<p class="muted">없음</p>') +
      '</div>';
  }).join("");

  content.innerHTML =
    '<section class="card"><h2>할 일 보드</h2>' +
    '<ul class="muted help-list">' +
    '<li>"내 업무"에 뜨는, 내가 참가 중인 업무만 보여줍니다(조직 전체 업무는 "업무 요청" 탭에서 브리프별로 확인하세요).</li>' +
    '<li>카드의 "열기"로 워크스페이스를 펼쳐 상태/우선순위를 바꾸면 이 보드에도 반영됩니다(새로고침 필요).</li>' +
    '</ul>' +
    '<div class="inline" style="align-items:flex-start;flex-wrap:wrap">' + columnsHtml + '</div>' +
    '<div id="board-detail"></div>' +
    '</section>';

  content.querySelectorAll("[data-board-open]").forEach(btn => {
    btn.onclick = () => renderCollabWorkspace(Number(btn.dataset.boardOpen), "board-detail");
  });
}

/* Phase 4 — execute-agent는 202+job_id만 즉시 돌려준다(사용자 요청,
   2026-09-28, "Phase 4 진행해줘"). 실제 완료는 여기서 폴링으로 확인한다
   (실시간 WebSocket은 COLLABORATION_PLATFORM_PLAN.md §6에서 1차 범위 밖으로
   명시). */
async function pollExecutionJob(taskId, jobId, intervalMs, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 120000);
  while (Date.now() < deadline) {
    const jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    const job = jobs.find(j => j.id === jobId);
    if (job && job.status !== "running") return job;
    await new Promise(r => setTimeout(r, intervalMs || 1500));
  }
  throw new Error("실행 완료 확인 시간이 초과됐습니다 — 잠시 후 새로고침해 주세요.");
}

// ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4) — **사용자 결정**:
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
  let items, jobs, permission, confirmedAgents, confirmedTeams, taskDetail, activity, participants, depGraph;
  try {
    items = await api("GET", "/api/tasks/" + taskId + "/deliverables");
    jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    // ASANA_PARITY_DESIGN_PLAN.md F17(메모)/F19(통합 활동 타임라인)/
    // F15(참가자 초대, Phase 2)/F16(상태·우선순위·의존관계, Phase 4),
    // 2026-10-08 — 전부 워크스페이스를 열 때 같이 불러온다.
    taskDetail = await api("GET", "/api/tasks/" + taskId);
    activity = await api("GET", "/api/tasks/" + taskId + "/activity");
    participants = await api("GET", "/api/tasks/" + taskId + "/participants");
    depGraph = await api("GET", "/api/tasks/" + taskId + "/dependencies");
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
    // agent_id`는 §61에서 이미 추가돼 있었는데 이 UI만 안 쓰고 있었다).
    const assignments = await api("GET", "/api/tasks/" + taskId + "/assignments");
    confirmedAgents = assignments.filter(a => a.resource_type === "agent" && a.status === "confirmed");
    // 사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
    // 개선... 사람-에이전트 연동이 가능한 구조인지 확인") —
    // `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 3. 이 할 일에
    // confirmed로 배정된 팀이 있으면 그 팀의 Agent 멤버를 실제 Crew로
    // 실행하는 섹션을 보여준다(팀이 여러 개면 드롭다운으로 하나를
    // 고른다 — 사용자 결정, 전부 동시 실행 아님).
    confirmedTeams = assignments.filter(a => a.resource_type === "team" && a.status === "confirmed");
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  // 최신 job이 아직 안 끝났거나(생성 중) 실패했으면, 아직 timeline에
  // 안 나오는 그 실행을 별도 카드로 보여준다(성공하면 곧 deliverable로
  // 나타나므로 여기서 다시 보여줄 필요 없음).
  const latestJob = jobs[0];
  const jobCard = (latestJob && latestJob.status === "running")
    ? '<div class="deliverable-card chat-agent"><div class="muted">AI 에이전트 · 생성 중…</div></div>'
    : (latestJob && latestJob.status === "failed"
      ? '<div class="deliverable-card chat-agent"><div class="muted">AI 에이전트 · 실행 실패(' + esc(latestJob.finished_at) + ')</div>' +
        '<div>' + esc(latestJob.error || "알 수 없는 오류") + '</div></div>'
      : '');

  // Phase 5(사용자 요청, 2026-09-28, "협업 워크스페이스 UX 개선") — 공유
  // 스레드/채팅형 타임라인: 작성자 타입별로 카드를 좌우로 구분해 "누가
  // 쓴 건지" 한눈에 보이게 한다. Gate 요약 배지도 여기서 처음 노출한다
  // — d.evaluation(Phase 3부터 API엔 있었지만 이전까지 화면 어디에도
  // 안 나오고 있었다).
  const deliverableCards = items.map(d => {
    // 실사용 리뷰로 발견한 갭(2026-10-07, 시나리오 3 실행 중) — 결재자가
    // 그 task의 참가자가 아니면(흔한 경우 — F7 체인은 조직도 기준이지
    // task 참가 여부와 무관) 반려해도 "왜"를 댓글로 남길 방법이 전혀
    // 없었다(댓글은 참가자 전용). 승인/반려에 선택적 사유를 붙여 그
    // 사유를 결재자 자격으로(참가자 여부와 무관하게) 타임라인에 남긴다.
    const decideActions = (isSupervisor && d.approval_id)
      ? '<div class="actions">' +
        '<input placeholder="사유(선택)" data-decide-reason="' + d.approval_id + '" style="flex:1;min-width:120px">' +
        '<button class="small" data-decide-deliverable="' + d.approval_id + '" data-approve="1">승인</button>' +
        '<button class="small danger" data-decide-deliverable="' + d.approval_id + '" data-approve="0">반려</button></div>'
      : (d.approval_id ? '<p class="muted">결재 대기 중 — "결재" 탭 또는 담당 매니저/관리자가 처리합니다.</p>' : '');
    // Phase 2 후속(라이브 시나리오 실행에서 발견한 갭 보완, docs/LIMITS.md
    // §33-34) — 사람 draft는 본인이 명시적으로 "검토 요청"을 눌러야만
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
    return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted">' + esc(label(d.author_type)) + " · " + esc(label(d.status)) + " · " + esc(d.created_at) + " " + gateBadge + '</div>' +
    '<div>' + renderContent(d.content, d.content_type) + '</div>' +
    '<div class="actions"><button class="small" data-download-deliverable="' + d.id + '">다운로드</button></div>' +
    evidenceBlock +
    decideActions +
    submitAction +
    '<div id="comments-' + d.id + '"></div>' +
    '<form class="inline" data-comment-form="' + d.id + '">' +
    // ASANA_PARITY_DESIGN_PLAN.md F15 UI 설계(2026-10-08) — "댓글
    // 입력창에 @ 입력 시 참가자 목록 자동완성 드롭다운"을 그대로
    // 구현한다(1차 구현 땐 정적 힌트 텍스트로만 대체했었다 — 라이브
    // 감사에서 실제 누락으로 확인돼 보완). input을 relative 컨테이너로
    // 감싸고 그 아래 절대 위치 드롭다운 <ul>을 둔다.
    '<div class="mention-wrap" style="position:relative;flex:1">' +
    '<input placeholder="댓글(@참가자이름으로 호출/멘션 가능)" id="comment-input-' + d.id +
    '" style="width:100%;box-sizing:border-box" autocomplete="off">' +
    '<ul class="mention-dropdown" id="mention-dropdown-' + d.id + '" style="display:none"></ul>' +
    '</div>' +
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
  const finalResult = approvedItems.length
    ? approvedItems.reduce((a, b) => (a.created_at > b.created_at ? a : b))
    : null;
  const finalResultCard = finalResult
    ? '<div class="final-result-card">' +
      '<div class="muted">✓ 최종 결과물(승인됨) · ' + esc(label(finalResult.author_type)) + ' · ' + esc(finalResult.created_at) + '</div>' +
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
  const executeBtnHtml =
    agentPickerHtml +
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
  // `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 3. 이 할 일에
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
      '<option value="sequential">sequential(순서대로 전달)</option>' +
      '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
      teamCrewModelFields +
      '<button class="small" id="ws-execute-team-crew-btn"' + (hasRunningJob ? " disabled" : "") + '>' +
      esc(hasRunningJob ? "생성 중…" : "팀 Agent 실행(Crew, 비용 발생)") + '</button>' +
      (confirmedTeams.length > 1
        ? '<p class="muted">이 업무에 확정된 팀이 ' + confirmedTeams.length + '개입니다 — 실행할 팀을 위에서 선택하세요(그 팀만 실행됩니다).</p>'
        : '<p class="muted">팀의 온보딩된(active) Agent 멤버 전원이 CrewAI로 함께 실행되고, 각자의 산출물이 이 할 일의 결과물로 쌓입니다.</p>')
    : '';

  // ASANA_PARITY_DESIGN_PLAN.md F17(2026-10-08) — 결과물이 아직 없는
  // 단계에서도 참가자들이 맥락/의사결정을 적어두는 자유 텍스트 공간.
  // 마크다운으로 렌더링(결과물 content_type='markdown'과 같은 렌더러
  // 재사용)하고, 저장 버튼을 누르기 전까진 textarea 그대로 둔다(실시간
  // 공동편집은 스코프 밖 — ADR 미결정 사항).
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

  // F19 — 배정·결과물·댓글·Agent 실행·결재·Task 변경을 한 타임라인으로
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

  // ASANA_PARITY_DESIGN_PLAN.md F15(2026-10-08, Phase 2+3) — 결재 없는
  // 가벼운 초대. `display_name`(Phase 3 추가, `list_participants()`가
  // agent_pool/users를 조인해 내려줌)을 보여줘야 참가자가 댓글에
  // "@정확한이름"을 쓸 수 있다(멘션은 role_name 완전일치 — `detect_
  // mentioned_agents()` 참고).
  const invitedAgentNames = participants
    .filter(p => p.participant_type === "agent" && p.display_name)
    .map(p => p.display_name);
  const participantRows = participants.map(p =>
    '<li>' + esc(label(p.participant_type)) + ' ' +
    (p.display_name ? esc(p.display_name) : '#' + esc(p.participant_id)) +
    ' <span class="muted">(초대: 사용자 #' + esc(p.invited_by) + ', ' + esc(p.invited_at) + ')</span></li>'
  ).join("");
  const participantsCard =
    '<section class="card">' +
    '<h4>참가자</h4>' +
    '<p class="muted">초대된 사람/Agent는 결재 없이도 이 업무의 댓글을 읽고 달 수 있습니다(결과물 작성·AI 실행 권한은 별개 — 정식 배정이 있어야 합니다). 댓글에 "@Agent이름"을 쓰면 그 Agent를 가볍게 호출합니다(결과는 댓글로만 남고, 결과물로 쓰려면 별도로 제출해야 합니다).</p>' +
    (participantRows ? '<ul>' + participantRows + '</ul>' : '<p class="muted">아직 초대된 참가자가 없습니다.</p>') +
    (invitedAgentNames.length
      ? '<p class="muted">멘션 가능: ' + invitedAgentNames.map(n => '@' + esc(n)).join(", ") + '</p>'
      : '') +
    '<form class="inline" id="ws-invite-form">' +
    '<select id="ws-invite-type"><option value="human">사람(사용자 ID)</option><option value="agent">Agent(Agent ID)</option></select>' +
    '<input type="number" id="ws-invite-id" placeholder="ID" style="width:6rem" required>' +
    '<button class="small" type="submit">초대</button>' +
    '</form>' +
    '</section>';

  // ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4) — 상태/우선순위는
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

  // F16 — 의존관계 그래프(사용자 결정: Riido 수준 시각화). 제거는
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
  const dependencySection =
    '<section class="card">' +
    '<h4>의존관계</h4>' +
    '<p class="muted">이 업무와 연결된 선행/후행 작업을 그래프로 보여줍니다 — 테두리 색은 우선순위(빨강=긴급·주황=높음·파랑=보통·회색=낮음), 네모를 클릭하면 그 업무로 이동합니다.</p>' +
    '<div style="overflow-x:auto">' + renderDependencyGraphSvg(depGraph, taskId) + '</div>' +
    (dependencyListRows ? '<ul>' + dependencyListRows + '</ul>' : '') +
    '<form class="inline" id="ws-dependency-form">' +
    '<input type="number" id="ws-dependency-input" placeholder="선행 작업 ID" style="width:9rem" required>' +
    '<button class="small" type="submit">선행 작업 추가</button>' +
    '</form>' +
    '</section>';

  // 사용자 요청(2026-09-29, "UI/UX를 개선해줘") — 원래 textarea·provider/
  // model·"LLM으로 도와줘"·"결과물 제출"이 한 줄(.inline flex-wrap)에 전부
  // 뒤섞여 있어 좁은 화면에서 텍스트가 select 옆에 끼어 줄바꿈이 어색했다.
  // textarea를 독립된 줄로 분리하고, "결과물 작성"과 "AI 에이전트 실행"을
  // 서로 다른 카드로 나눠 두 행동의 성격이 다르다는 걸 시각적으로 구분한다.
  detail.innerHTML =
    '<h3>협업 워크스페이스 — 할 일 #' + esc(taskId) + '</h3>' +
    statusPriorityBar +
    finalResultCard +
    notesCard +
    // ASANA_PARITY_DESIGN_PLAN.md §9-5(2026-10-08, F21-1 흡수) — 이
    // Task 전용 대화(`conversations.linked_task_id`)를 여기에
    // 임베드한다. 결과물 유무와 무관하게 항상 노출(F15 때 발견한
    // "결과물 0건이면 대화 자체가 불가능했던" 구조적 한계를 여기서
    // 해소 — 대화는 더 이상 deliverable에 종속되지 않는다).
    '<div id="ws-conversation-' + taskId + '"><p class="muted">대화를 불러오는 중…</p></div>' +
    participantsCard +
    dependencySection +
    '<h4 class="muted">진행 이력(중간 과정 포함)</h4>' +
    '<div id="workspace-timeline">' + timeline + '</div>' +
    activitySection +
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
    (confirmedTeams.length
      ? '<section class="card"><h4>팀 Agent 실행(Crew)</h4>' +
        contentTypeSelectHtml("ws-team-crew-content-type") + ' ' + teamCrewBtnHtml + '</section>'
      : '') +
    '<div id="ws-result"></div>';

  if (isSupervisor) wireProviderModelSelect("ws-provider", "ws-model", modelsByProvider);
  if (isSupervisor && confirmedTeams.length) {
    wireProviderModelSelect("ws-team-crew-provider", "ws-team-crew-model", modelsByProvider);
  }
  items.forEach(d => loadDeliverableComments(d.id, taskId, containerId, participants));
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

  document.getElementById("ws-invite-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const participantType = document.getElementById("ws-invite-type").value;
    const participantId = Number(document.getElementById("ws-invite-id").value);
    try {
      await api("POST", "/api/tasks/" + taskId + "/participants", {
        participant_type: participantType,
        participant_id: participantId,
      });
      showMsg("global-msg", "참가자를 초대했습니다.", "ok");
      renderCollabWorkspace(taskId, containerId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

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

  document.getElementById("ws-dependency-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const dependsOnId = Number(document.getElementById("ws-dependency-input").value);
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

  const executeBtn = document.getElementById("ws-execute-agent-btn");
  executeBtn.onclick = async () => {
    if (!window.confirm("이 업무에 배정된 AI 에이전트를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?")) return;
    const payload = { content_type: document.getElementById("ws-execute-content-type").value };
    const agentPicker = document.getElementById("ws-agent-picker");
    if (agentPicker) payload.agent_id = Number(agentPicker.value);
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = modelSelectValue("ws-model");
      if (provider) payload.provider = provider;
      if (model) payload.model = model;
    }
    try {
      const queued = await api("POST", "/api/tasks/" + taskId + "/execute-agent", payload);
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

  const executeTeamCrewBtn = document.getElementById("ws-execute-team-crew-btn");
  if (executeTeamCrewBtn) {
    executeTeamCrewBtn.onclick = async () => {
      const teamPicker = document.getElementById("ws-team-picker");
      const teamId = teamPicker ? Number(teamPicker.value) : confirmedTeams[0].resource_id;
      if (!window.confirm("이 팀의 Agent 멤버 전원을 Crew로 실행합니다(실제 LLM API 호출, 비용은 참여 Agent 수만큼 커질 수 있습니다). 계속할까요?")) return;
      const payload = {
        team_id: teamId,
        process: document.getElementById("ws-team-crew-process").value,
        content_type: document.getElementById("ws-team-crew-content-type").value,
      };
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
    // ASANA_PARITY_DESIGN_PLAN.md F15(2026-10-08, Phase 3) — Agent가
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

// ASANA_PARITY_DESIGN_PLAN.md F15 UI 설계(2026-10-08) — "@" 입력 시
// 참가자 목록 자동완성 드롭다운. 1차 구현 때는 정적 힌트 텍스트로만
// 대체했는데, 실제 누락으로 확인돼("HAIR-Platform... 구현에 누락
// 부분이 없는지 확인해줘") 보완한다. 외부 라이브러리 없이 순수 JS로
// 커서 위치 기준 마지막 "@" 뒤 텍스트를 쿼리 삼아 필터링한다.
//
// §10 채팅 호출 UX 개선(2026-10-10, 사용자 요청 "채팅에서 @로 생성된
// AI Agent와 사용자를 호출하려면 미리 전체 이름을 알고 있어야 해") —
// `opts.search`가 주어지면(대화 메시지창만; Task 결과물 댓글은 기존과
// 완전히 같은 "로컬 참가자만" 동작을 유지한다 — `opts` 생략 시 이전
// 동작과 100% 동일) 조직 전체 검색(`/api/conversations/mentionable`,
// 역할/스킬까지 부분 일치 + 이 사용자가 최근 초대한 대상 우선)도 함께
// 조회해 "참가자"/"초대 후 멘션" 두 그룹으로 보여준다. 후자를 고르면
// `opts.onInvited`로 먼저 초대한 뒤 멘션을 끼워 넣는다 — 화면 이동
// 없이 "검색→클릭"만으로 처음 보는 이름도 바로 부를 수 있다.
function setupMentionAutocomplete(inputEl, dropdownEl, participants, opts) {
  opts = opts || {};
  const localCandidates = participants
    .filter(p => p.display_name)
    .map(p => ({ name: p.display_name, type: p.participant_type, id: p.participant_id }));
  if (!localCandidates.length && !opts.search) return;

  const MENTION_TYPE_BADGE = { agent: "(AI)", team: "(팀)" };
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

  function renderGroups(localMatches, remoteItems) {
    if (!localMatches.length && !remoteItems.length) {
      dropdownEl.style.display = "none";
      dropdownEl.innerHTML = "";
      return;
    }
    let html = localMatches.map(c =>
      '<li data-mention-name="' + escAttr(c.name) + '">' + esc(c.name) +
      (MENTION_TYPE_BADGE[c.type] ? ' <span class="muted">' + MENTION_TYPE_BADGE[c.type] + '</span>' : '') +
      '</li>'
    ).join("");
    if (remoteItems.length) {
      html += '<li class="muted" style="padding:0.25rem 0.5rem">초대 후 멘션</li>' +
        remoteItems.map(c =>
          '<li data-mention-name="' + escAttr(c.name) + '" data-mention-invite-type="' + c.type +
          '" data-mention-invite-id="' + c.id + '">' + esc(c.name) +
          ' <span class="muted">' + (MENTION_TYPE_BADGE[c.type] || '(사람)') + ' · 초대 필요</span></li>'
        ).join("");
    }
    dropdownEl.innerHTML = html;
    dropdownEl.style.display = "block";
  }

  function handleQuery(query) {
    // 실측으로 발견한 버그(2026-10-08, §9 라이브 검증) — 원래 "@뒤에
    // 공백이 있으면 멘션이 끝난 것"으로 보고 닫았는데, "게임 AI팀"처럼
    // 이름 자체에 공백이 있는 참가자(흔한 한국어 Team/Agent 이름)를
    // 끝까지 타이핑하면 중간 공백에서 드롭다운이 영구히 닫혀버렸다.
    // 공백 여부로 판단하지 않고, 후보 이름에 그 쿼리가 부분 문자열로
    // 남아있는 동안만 계속 열어둔다.
    const localMatches = localCandidates.filter(c => c.name.includes(query));
    if (!opts.search) { renderGroups(localMatches, []); return; }
    // 로컬 매치는 즉시 보여주고(깜빡임 방지), 조직 전체 검색만 디바운스
    // 한다 — 매치가 하나도 없는 상태에서 디바운스 중 잠깐 닫았다 여는
    // 깜빡임을 피하려고, 로컬 매치가 없을 땐 응답이 올 때까지 아무
    // 것도 안 바꾼다(기존 상태 유지).
    if (localMatches.length) renderGroups(localMatches, []);
    clearTimeout(searchDebounce);
    const myToken = ++searchToken;
    searchDebounce = setTimeout(async () => {
      let remote;
      try {
        remote = await opts.search(query);
      } catch (e) {
        return;
      }
      if (myToken !== searchToken) return; // 늦게 도착한 응답은 버린다
      const localKeys = new Set(localCandidates.map(c => c.type + ":" + c.id));
      const remoteItems = [
        ...remote.humans.map(h => ({ type: "human", id: h.id, name: h.username })),
        ...remote.agents.map(a => ({ type: "agent", id: a.id, name: a.role_name })),
        ...remote.teams.map(t => ({ type: "team", id: t.id, name: t.name })),
      ].filter(it => !localKeys.has(it.type + ":" + it.id));
      renderGroups(localMatches, remoteItems);
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
  });
}

async function loadDeliverableComments(deliverableId, taskId, containerId, participants) {
  await refreshComments(deliverableId);
  const mentionInput = document.getElementById("comment-input-" + deliverableId);
  const mentionDropdown = document.getElementById("mention-dropdown-" + deliverableId);
  if (mentionInput && mentionDropdown && participants) {
    setupMentionAutocomplete(mentionInput, mentionDropdown, participants);
  }
  // F15 Phase 3 — "이걸 결과물로 제출" 버튼은 `refreshComments()`가
  // `box.innerHTML`을 매번 통째로 새로 그리므로, 그 자식 버튼에 직접
  // 리스너를 매번 새로 붙이면 §73 이전에 겪었던 "리스너 누적" 버그가
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
        // F15 Phase 3 — @멘션이 트리거됐으면 백그라운드 완료를 폴링해
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
    html += '<ul class="muted help-list">' +
      '<li>여기서 하는 일은 "제안된 역할" 칸을 채우는 것뿐입니다 — 실제 AI 분석이 아니라 입력한 텍스트를 줄바꿈 단위로 그대로 나누는 규칙 기반 처리라 비용이 없고 즉시 끝납니다.</li>' +
      '<li>이 제안은 참고용이고, 실제 인원 배정 단위인 "할 일(Task)"은 아래 "할 일로 나누기"에서 직접 만들어야 합니다.</li>' +
      '<li>뷰어(viewer) role은 조회만 가능하고 제출은 못 합니다.</li>' +
      '</ul>' +
      '<form class="inline" id="brief-form">' +
      '<textarea id="brief-text" placeholder="한 줄에 역할 하나씩 입력하세요(줄바꿈 단위로 나뉩니다). 예:&#10;백엔드 개발자 1명&#10;UI/UX 디자이너 1명" rows="3" style="flex:1;min-width:240px"></textarea>' +
      '<button class="primary" type="submit">제출</button></form>';
  }
  html += "</section><section class=\\"card\\"><h2>업무 요청 목록</h2>" +
    '<ul class="muted help-list">' +
    '<li>"할 일로 나누기"로 만든 할 일(Task)이 "인력 매칭/배정"·"AI 에이전트 생성" 탭에서 쓸 단위입니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 업무 요청과 그 아래 할 일·배정·이력까지 전부 되돌릴 수 없게 지웁니다 — 확정된(confirmed) 배정이 하나라도 있으면 막힙니다.</li>' +
    '</ul>';
  html += table(["ID", "내용", "상태", "제안된 역할", "동작"], briefs.map(b => {
    let actions = '<button class="small" data-decompose="' + b.id + '">할 일로 나누기</button>' +
      '<button class="small" data-viewtasks="' + b.id + '">할 일 보기</button>';
    if (isAdmin) {
      actions += '<button class="small danger" data-delete-brief="' + b.id + '">삭제</button>';
    }
    return [b.id, esc(b.raw_text), esc(label(b.status)), esc(b.stub_roles.join(", ")),
      canWrite ? '<div class="actions">' + actions + "</div>" : ""];
  }));
  html += '<div id="brief-detail"></div></section>';
  content.innerHTML = html;

  const form = document.getElementById("brief-form");
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api("POST", "/api/briefs", { raw_text: document.getElementById("brief-text").value });
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
      if (!window.confirm("이 업무 요청과 그 아래 할 일·배정·결과물(산출물 평가 이력 포함)을 전부 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
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
    '<ul class="muted help-list">' +
    '<li>필요 스킬은 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(여러 개 등록 가능, 칩의 ×로 제거).</li>' +
    '<li>자동완성 중 "외부검색"은 위키백과 검색만 지원하고, "일반웹검색"은 위키백과가 아니라 전체 인터넷(Parallel Search)을 검색하는 별도 스킬입니다.</li>' +
    '<li>목록에 없는 이름도 자유롭게 추가할 수 있습니다.</li>' +
    '</ul>' +
    skillDatalist;
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
      t.latest_status ? esc(label(t.latest_status)) : '<span class="muted">아직 없음</span>',
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
    // 통합은 매니저의 일괄 처리 효율을 떨어뜨릴 위험이 있어 기각했다
    // (검토 근거: docs/LIMITS.md §55).
    '<ul class="muted help-list">' +
    '<li>"상세"에서 배정 현황을 바로 확인할 수 있습니다.</li>' +
    '<li>"인력 매칭/배정"·"결재"는 여러 할 일을 한꺼번에 처리할 때, "AI 에이전트 생성"은 새 Agent가 필요할 때 씁니다.</li>' +
    '</ul>' +
    '<div id="task-detail"></div>';
  detail.querySelectorAll("[data-delete-task]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 할 일과 그 배정·결과물(산출물 평가 이력 포함)을 전부 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
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
   워크스페이스)를 한 화면에서 보여준다(옵션 C, docs/LIMITS.md §55).
   매칭 폼·워크스페이스는 기존 "인력 매칭/배정"·"내 업무" 탭의 렌더
   함수를 containerId 파라미터로 그대로 재사용한다(중복 구현 금지) —
   admin/manager만 노출한다: 매칭 결과가 다른 후보의 비용을 보여주고,
   deliverables 조회는 admin/manager가 아니면 참가자 본인만 허용돼
   (403) 있어, 그 둘은 감독 권한이 있는 role에만 안전하게 임베드할 수
   있다. member/viewer는 배정 현황만 읽기 전용으로 본다(자기 업무
   처리는 기존처럼 "내 업무" 탭에서). */
async function renderTaskDetailPanel(task) {
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

  const confirmedHtml = confirmedList.length
    ? confirmedList.map(a => '<p><span class="gate-badge gate-ok">확정됨</span> ' + resourceLabel(a) + "에게 배정됨</p>").join("")
    : "";
  const pendingHtml = pendingList.map(a =>
    '<p><span class="gate-badge gate-warn">결재 대기</span> ' + resourceLabel(a) +
    ' — <button class="small" data-goto-approvals="1">결재 탭에서 처리</button>' +
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
  if (isSupervisor) {
    const teams = await api("GET", "/api/teams").catch(() => []);
    const activeTeams = teams.filter(t => t.is_active);
    teamOptionsHtml = activeTeams.length
      ? '<form class="inline" id="task-detail-team-assign-form">' +
        '<select id="task-detail-team-select" required>' +
        activeTeams.map(t => '<option value="' + t.id + '">' + esc(t.name) + '</option>').join("") +
        '</select><button class="small" type="submit">이 팀에 배정 요청</button></form>'
      : '<p class="muted">등록된 활성 팀이 없습니다 — "팀 관리" 탭에서 먼저 만드세요.</p>';
  }

  // 실사용 리뷰로 발견한 갭(2026-10-07) — "다중 confirmed 허용" 이후
  // 이미 배정이 확정된 할 일에도 "후보 매칭하기"가 변함없이 1순위로
  // 노출돼, 이미 담당자가 있는 일도 아직 비어있는 것처럼 보였다(배정을
  // *추가*하는 것과 *처음* 배정하는 건 의도가 다르다). confirmed가
  // 하나라도 있으면 "<details>" 뒤로 접어 넣고 문구를 "추가 배정"으로
  // 바꿔 이게 보충 동작임을 분명히 한다.
  const matchAndTeamHtml = isSupervisor
    ? '<button class="small" id="task-detail-match-btn">후보 매칭하기</button><div id="task-detail-match"></div>' +
      '<details><summary>팀에 배정 요청</summary>' + teamOptionsHtml + '</details>'
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
        renderMatchResult(task.id, result, "task-detail-match", () => renderTaskDetailPanel(task));
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
  }
  if (isSupervisor && confirmedList.length) {
    renderCollabWorkspace(task.id, "task-detail-workspace");
  }
}

/* ---------- 인력 매칭·배정 ---------- */

async function renderMatch(content) {
  content.innerHTML =
    '<section class="card"><h2>할 일에 맞는 인력 찾기</h2>' +
    '<ul class="muted help-list">' +
    '<li>"할 일 ID"는 "업무 요청" 탭에서 "할 일로 나누기"로 만든 것을 씁니다.</li>' +
    '<li>완전히 일치하는 역할·스킬을 가진 후보가 있으면 그것부터 보여주고, 없으면 가용성40%·적합도35%·비용25% 가중치로 점수를 매겨 상위 3명을 제시합니다(사람 후보가 아직 성과점수를 안 받았으면 적합도를 빼고 가용성60%·비용40%로 재계산).</li>' +
    '<li>후보가 아예 없으면 "AI 에이전트 생성" 탭으로 유도합니다.</li>' +
    '<li>"배정 요청"을 누르면 즉시 확정되는 게 아니라 "결재" 탭에서 승인 절차를 거쳐야 확정(confirmed)됩니다.</li>' +
    '</ul>' +
    '<form class="inline" id="match-form">' +
    '<input id="match-task-id" type="number" min="1" step="1" placeholder="할 일 ID" required style="width:100px">' +
    '<button class="primary" type="submit">찾기</button></form>' +
    '<div id="match-result"></div></section>';
  document.getElementById("match-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const taskId = Number(document.getElementById("match-task-id").value);
    if (!Number.isInteger(taskId) || taskId < 1) {
      showMsg("global-msg", "할 일 ID는 1 이상의 정수여야 합니다.", "error"); return;
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
function renderMatchResult(taskId, result, containerId = "match-result", onAssigned) {
  const box = document.getElementById(containerId);
  if (result.generate_new) {
    box.innerHTML = '<p class="msg error">매칭되는 후보가 없습니다 — "AI 에이전트 생성" 탭에서 새 Agent를 만들거나, "사람 인력 관리" 탭에서 이 역할의 사람을 등록해보세요.</p>';
    return;
  }
  // 사용자 보고(2026-10-06, "매칭/배정 화면에도 계정 미연결 경고
  // 추가해줘") — 사람 후보가 로그인 계정과 연결 안 돼 있으면 배정·승인이
  // 돼도 당사자가 "내 업무"에서 그 일을 볼 방법이 없다(docs/LIMITS.md
  // §51). Agent는 로그인 계정 개념이 없어 "해당없음"으로 둔다.
  const accountCell = (c) => {
    if (c.type !== "human") return '<span class="muted">해당없음</span>';
    return c.user_id != null
      ? "연결됨"
      : '<span class="gate-badge gate-warn">⚠ 미연결</span>';
  };
  box.innerHTML = table(
    ["순위", "타입", "ID", "역할", "가용", "비용", "점수", "점수 상세", "계정", "동작"],
    result.top.map((sc, i) => [
      i + 1, esc(label(sc.candidate.type)), sc.candidate.id, esc(sc.candidate.role),
      sc.candidate.is_available ? "가능" : "불가",
      sc.candidate.cost,
      sc.score.toFixed(3) + (sc.exact_match ? " (완전일치)" : ""),
      esc(JSON.stringify(sc.breakdown)),
      accountCell(sc.candidate),
      '<button class="small" data-assign-type="' + esc(sc.candidate.type) + '" data-assign-id="' + sc.candidate.id + '" data-unlinked="' + (sc.candidate.type === "human" && sc.candidate.user_id == null ? "1" : "0") + '">배정 요청</button>',
    ])
  );
  box.querySelectorAll("[data-assign-id]").forEach(btn => {
    btn.onclick = async () => {
      if (btn.dataset.unlinked === "1" && !window.confirm(
        "이 사람은 로그인 계정이 연결돼 있지 않습니다 — 배정이 승인돼도 본인이 \\"내 업무\\"에서 확인할 방법이 없습니다. \\"사람 인력 관리\\"에서 계정을 먼저 연결하는 걸 권장합니다. 그래도 배정 요청하시겠습니까?"
      )) return;
      try {
        await api("POST", "/api/tasks/" + taskId + "/assignments", {
          resource_type: btn.dataset.assignType, resource_id: Number(btn.dataset.assignId),
        });
        showMsg("global-msg", "배정 요청을 보냈습니다 — \\"결재\\" 탭에서 처리하세요.", "ok");
        if (onAssigned) onAssigned();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 결재 ---------- */

async function renderApprovals(content) {
  const isDecider = ["admin", "manager"].includes(ME.role);
  let html = "";
  if (isDecider) {
    html += '<ul class="muted help-list">' +
      '<li>배정 요청은 요청자의 상사 체인(조직도 "상사" 지정)을 따라 자동으로 단계가 만들어집니다 — 1단계가 처리돼야 2단계가 열립니다(다단계 결재).</li>' +
      '<li>상사가 지정 안 돼 있으면(예: 관리자 본인 요청) 특정 승인자 없이 1단계만 만들어져 admin/manager 누구나 처리할 수 있습니다.</li>' +
      '<li>본인이 요청한 건은 본인이 승인할 수 없습니다(자기 승인 방지) — "내 승인 대기함"에는 애초에 안 뜹니다.</li>' +
      '</ul>';
    const inbox = await api("GET", qs("/api/approvals", { scope: "inbox" }));
    html += '<section class="card"><h2>내 승인 대기함</h2>' + approvalTable(inbox, true) + "</section>";
  }
  const mine = await api("GET", qs("/api/approvals", { scope: "mine" }));
  html += '<section class="card"><h2>내가 요청한 건</h2>' +
    '<ul class="muted help-list">' +
    '<li><b>승인</b>하면 배정 상태가 confirmed(확정)로 바뀌어 조직도·비용·성과 리뷰에 실제로 반영됩니다.</li>' +
    '<li><b>반려</b>하면 그 배정 요청은 거기서 끝나고(재사용 불가), 다시 배정하려면 "인력 매칭/배정"에서 새로 요청해야 합니다.</li>' +
    '</ul>' +
    approvalTable(mine, false) + "</section>";
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
}

function approvalTable(rows, withActions) {
  const headers = ["ID", "대상 종류", "대상 ID", "단계", "상태"];
  if (withActions) headers.push("동작");
  return table(headers, rows.map(a => {
    const row = [a.id, esc(label(a.subject_type)), a.subject_id, a.step, esc(label(a.status))];
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
    '<ul class="muted help-list">' +
    '<li><b>stub</b>(무료): LLM을 전혀 호출하지 않는 규칙 기반 스텁입니다 — 역할명·스킬로 자리표시자 산출물만 만들고 즉시 끝납니다. 조직도/배정 흐름만 비용 없이 채워보고 싶을 때 씁니다.</li>' +
    '<li><b>simple_llm</b>(비용 발생): 실제 LLM API를 딱 한 번만 호출해 "이 역할이 처음 맡을 산출물 하나"를 받아옵니다. CrewAI 같은 프레임워크 오버헤드 없이 가장 가벼운 실제 호출입니다.</li>' +
    '<li><b>crewai</b>(비용 발생, 보통 더 큼): 실제 CrewAI Agent+Task+Crew를 구성해 실행합니다 — 필요 스킬에 vectordb/graphdb 같은 게 있으면 Skill Pool이 매칭해주는 실제 도구를 생성 시점부터 붙여서 태어나므로, 이후 "Crew 실행" 탭에서 바로 협업시킬 Agent를 만들 때 씁니다.</li>' +
      '<li>만든 Agent는 이 탭이 아니라 "AI 에이전트 관리" 탭에서 온보딩 승인(pending_onboarding → active) 후에야 실제 업무 배정·실행에 쓸 수 있습니다.</li>' +
      '<li><b>중복 방지(NF7)</b>: 같은 할 일(역할)에 이미 생성된 Agent가 있으면 새 Agent를 만들지 않고 기존 것을 재사용합니다 — 이 경우 "AI 에이전트 관리"에 새 행이 안 생기는데, 결과에 "재사용" 배지와 안내가 표시됩니다. 새 Agent를 만들려면 다른 할 일을 고르세요.</li>' +
      '</ul>' +
    '<div id="job-result"></div></section>';

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
}

function renderJobResult(result) {
  const box = document.getElementById("job-result");
  const reusedItems = (result.items || []).filter(it => it.reused);
  const reusedCount = result.reused_count != null ? result.reused_count : reusedItems.length;

  // NF7 dedup: 같은 역할(task)에 이미 생성된 에이전트가 있으면 새 에이전트를
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
      .map(it => "#" + it.agent_pool_id + "(" + esc(it.agent_pool_role_name) + ")")
      .join(", ");
    banner =
      '<div class="msg warn"><b>새 에이전트가 생성되지 않았습니다.</b> ' +
      "같은 역할에 이미 생성된 에이전트가 있어 " + reusedCount + "건을 재사용했습니다(NF7 중복 방지)" +
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
  // 했던 갭(§64)도 이걸로 없어진다 — 할 일을 처음부터 같이 고르면
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
      it.agent_pool_id != null ? "#" + it.agent_pool_id + " " + esc(it.agent_pool_role_name) : '<span class="muted">-</span>',
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
    '<button class="primary" type="submit">등록</button></form>' +
    '<ul class="muted help-list">' +
    '<li><b>계정 선택</b>은 "사용자 관리"에서 먼저 만든 로그인 계정 중 아직 다른 사람 인력에 연결 안 된 것만 보여줍니다 — 계정 없이 등록하면 그 사람은 로그인할 수 없어 매칭·배정이 돼도 실제로 업무를 볼 수 없으니(docs/LIMITS.md §50), 계정을 먼저 "사용자 관리"에서 만드세요.</li>' +
    '<li>스킬은 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(여러 개 등록 가능, 칩의 ×로 제거) — 목록에 없는 이름도 자유롭게 추가할 수 있습니다.</li>' +
    '<li><b>비용</b>은 인력 매칭 점수의 25%(또는 성과점수가 없으면 40%)를 차지합니다 — 낮을수록 유리합니다.</li>' +
    '<li>아직 성과 데이터가 없으니 초기엔 "가능" 상태여야 매칭 후보로 잡히고(가용성이 매칭 점수의 40~60%), 스킬은 할 일의 필요 스킬과 겹칠수록 유리합니다.</li>' +
    '</ul>' +
    '</section><section class="card"><h2>사람 인력 목록</h2>' +
    '<ul class="muted help-list">' +
    '<li><b>동의</b>는 성과 리뷰 사전 동의(NF9)입니다 — 동의 없이는 성과점수를 절대 입력·노출하지 않고, 동의를 철회하면 기존 점수도 즉시 지워집니다.</li>' +
    '<li><b>오프보딩</b>은 가용성을 offboarded로 바꿔 매칭 후보에서 빠지게 하는데, 이 화면에서 다시 되돌리는 버튼은 없습니다(사실상 편도) — 실수로 눌렀다면 관리자가 DB를 직접 고쳐야 합니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 오프보딩과 별개로 레코드 자체를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 삭제가 막힙니다.</li>' +
    '<li><b>계정 연결</b>은 이 사람을 로그인 계정과 이어줍니다 — 연결해야만 그 사람이 "내 업무" 탭에서 자기 업무를 볼 수 있습니다(연결 안 하면 구조적으로 절대 안 보입니다). 드롭다운엔 아직 다른 사람 인력에 연결 안 된 계정만 뜹니다.</li>' +
    '</ul>' +
    table(["ID", "이름", "역할", "스킬", "가용성", "비용", "동의", "성과점수", "연결된 계정", "동작"], people.map(p => {
      const middleActions = p.availability === "offboarded" ? "" :
        '<button class="small" data-toggle-consent="' + p.id + '" data-consent="' + (p.performance_consent ? "0" : "1") + '">' +
          (p.performance_consent ? "동의 철회" : "동의") + "</button>" +
        '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="score-' + p.id + '">' +
        '<button class="small" data-set-score="' + p.id + '">점수입력</button></span>' +
        '<span class="pair"><select id="linkuser-' + p.id + '">' + linkableOptionsHtml(p.user_id) + '</select>' +
        '<button class="small" data-link-user="' + p.id + '">계정 연결</button></span>' +
        '<button class="small danger" data-offboard="' + p.id + '">오프보딩</button>';
      const deleteAction = isAdmin ? '<button class="small danger" data-delete-human="' + p.id + '">삭제</button>' : "";
      const linkedAccount = p.user_id === null ? "-" :
        (usersById[p.user_id] ? esc(usersById[p.user_id].username) : "#" + p.user_id);
      return [p.id, esc(p.name), esc(p.role), esc(p.skill_tags.join(", ")), esc(label(p.availability)), p.cost_estimate,
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
    try {
      await api("POST", "/api/human-pool", {
        name: document.getElementById("hp-name").value,
        role: document.getElementById("hp-role").value,
        skill_tags: document.getElementById("hp-skills").value.split(",").map(s => s.trim()).filter(Boolean),
        cost_estimate: Number(document.getElementById("hp-cost").value) || 0,
        user_id: Number(userIdValue),
      });
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
      if (!confirm("정말 오프보딩하시겠습니까?")) return;
      try {
        await api("POST", "/api/human-pool/" + btn.dataset.offboard + "/offboard");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-human]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 사람 인력 레코드를 완전히 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
      try {
        await api("DELETE", "/api/human-pool/" + btn.dataset.deleteHuman);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- AI 에이전트 ---------- */

async function renderAgentPool(content) {
  const agents = await api("GET", "/api/agent-pool");
  const isAdmin = ME.role === "admin";
  const canExecute = ["admin", "manager"].includes(ME.role);
  const modelsByProvider = canExecute ? await fetchModelsByProvider() : {};
  content.innerHTML = '<section class="card"><h2>AI 에이전트 목록</h2>' +
    '<ul class="muted help-list">' +
    '<li>"생성방식" 열은 "AI 에이전트 생성" 탭에서 어떤 옵션(stub/simple_llm/crewai)으로 태어났는지를 보여줍니다 — crewai로 만든 Agent만 그 시점에 스킬 매칭 도구를 갖고 태어납니다.</li>' +
    '<li>신규 Agent는 전부 pending_onboarding 상태로 생기고, <b>온보딩 승인</b>을 눌러야 active가 돼 "인력 검색"·매칭 후보로 노출됩니다.</li>' +
    '<li><b>오프보딩</b>(active→deprecated)은 이 화면에서 되돌릴 버튼이 없습니다 — 다시 쓰려면 관리자가 API/DB를 직접 손대야 합니다.</li>' +
    '<li><b>모델 설정</b>은 이 에이전트가 기본으로 쓸 LLM provider/model을 저장만 합니다(비용 없음).</li>' +
    '<li><b>실행</b>은 온보딩된(활성) 에이전트에게 실제로 업무를 맡깁니다 — 설정해둔 모델(없으면 전역 기본값)로 실제 API를 호출하므로 비용이 발생합니다. 스킬에 vectordb/rag가 있으면 지식검색(RAG), graphdb가 있으면 관계분석 방식으로 자동 동작합니다(ADR 결정 11).</li>' +
    '<li><b>지식 색인</b>은 이 에이전트의 산출물을 임베딩해 지식검색 에이전트가 찾을 수 있게 저장합니다(비용 발생).</li>' +
    '<li><b>MCP 서버(JSON)</b>에 예: [{"url":"http://localhost:8000/mcp","transport":"streamable-http"}] 형태로 넣으면 "Crew 실행" 시 이 Agent에 실제 MCP 도구가 붙습니다(ADR 결정 10) — 빈 칸으로 두고 저장하면 기존 설정을 그대로 둡니다.</li>' +
    '<li><b>Tool Pool ID</b>에 "도구/스킬 Pool" 탭에서 등록한 도구 ID를 넣고 연결하면, 스킬 매칭과 무관하게 그 Agent에 도구가 하나 더 붙습니다(ADR 결정 12).</li>' +
    '<li><b>삭제</b>(관리자 전용)는 레코드를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 막힙니다.</li>' +
    '</ul>' +
    table(["ID", "역할", "스킬", "소속 팀", "비용", "성과점수(수동)", "자동평가(Gate/TCR)", "상태", "생성방식", "LLM", "최근 산출물", "동작"], agents.map(a => {
      let actions = "";
      if (isAdmin) {
        if (a.status === "pending_onboarding") actions += '<button class="small" data-onboard="' + a.id + '">온보딩 승인</button>';
        if (a.status === "active") actions += '<button class="small danger" data-deprecate="' + a.id + '">오프보딩</button>';
        actions += '<button class="small danger" data-delete-agent="' + a.id + '">삭제</button>';
        actions +=
          '<span class="pair"><input type="number" step="0.01" placeholder="비용" id="cost-' + a.id + '">' +
          '<button class="small" data-set-cost="' + a.id + '">비용입력</button></span>' +
          '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="ascore-' + a.id + '">' +
          '<button class="small" data-set-ascore="' + a.id + '">점수입력</button></span>';
      }
      if (canExecute) {
        const providers = ["anthropic", "openai", "ollama"];
        actions += '<select id="provider-' + a.id + '">' +
          providers.map(p => '<option value="' + p + '"' + (p === a.llm_provider ? " selected" : "") + ">" + p + "</option>").join("") +
          "</select>" +
          modelSelectHtml("model-" + a.id) +
          '<input placeholder="MCP 서버(JSON, 비우면 유지)" id="mcp-' + a.id + '" value="' + esc(a.mcp_servers && a.mcp_servers.length ? JSON.stringify(a.mcp_servers) : "").replace(/"/g, "&quot;") + '">' +
          '<button class="small" data-set-model="' + a.id + '">모델 설정</button>';
      }
      if (canExecute && a.status === "active") {
        actions += '<button class="small" data-execute="' + a.id + '">실행(일 시키기)</button>';
        actions += '<button class="small" data-index-knowledge="' + a.id + '">지식 색인</button>';
      }
      if (canExecute) {
        actions +=
          '<input placeholder="Tool Pool ID" id="linktool-' + a.id + '" type="number" min="1">' +
          '<span class="pair"><button class="small" data-link-tool="' + a.id + '">연결</button>' +
          '<button class="small" data-unlink-tool="' + a.id + '">해제</button></span>' +
          '<span class="muted">연결된 도구 ID: ' + (a.linked_tool_ids.length ? esc(a.linked_tool_ids.join(", ")) : "없음(스킬 기반 자동 연결은 별도)") + '</span>';
      }
      const output = a.last_output ? esc(a.last_output).slice(0, 80) + (a.last_output.length > 80 ? "…" : "") : "-";
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
      return [a.id, esc(a.role_name), esc(a.skill_tags.join(", ")), teamNames, a.cost_estimate,
        a.performance_score === null ? "-" : a.performance_score, autoEval, esc(label(a.status)), esc(a.framework), llmInfo, output,
        '<div class="actions">' + actions + "</div>"];
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
      if (!window.confirm("이 Agent를 오프보딩합니다(active→deprecated, 이 화면엔 되돌리는 버튼이 없습니다). 계속할까요?")) return;
      try {
        await api("POST", "/api/agent-pool/" + btn.dataset.deprecate + "/deprecate");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-agent]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 AI 에이전트 레코드를 완전히 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
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
      if (!window.confirm("이 에이전트의 최근 산출물을 임베딩해 색인합니다(실제 임베딩 API 호출 — 비용 발생). 계속할까요?")) return;
      try {
        await withBusy(btn, "색인 중…", () => api("POST", "/api/agent-pool/" + btn.dataset.indexKnowledge + "/index-knowledge", {}));
        showMsg("global-msg", "지식 색인을 완료했습니다.", "ok");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-link-tool]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.linkTool;
      const toolId = document.getElementById("linktool-" + id).value;
      if (!toolId) { showMsg("global-msg", "Tool Pool ID를 입력하세요.", "error"); return; }
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
      if (!toolId) { showMsg("global-msg", "Tool Pool ID를 입력하세요.", "error"); return; }
      try {
        await api("DELETE", "/api/agent-pool/" + id + "/tools/" + toolId);
        showMsg("global-msg", "도구 연결을 해제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 도구/스킬 Pool(ADR 결정 12) ---------- */

async function renderPoolRegistry(content) {
  const [tools, skills] = await Promise.all([
    api("GET", "/api/tool-pool"),
    api("GET", "/api/skill-pool"),
  ]);
  const isAdmin = ME.role === "admin";
  content.innerHTML =
    '<section class="card"><h2>Tool Pool — 재사용 가능한 도구</h2>' +
    '<ul class="muted help-list">' +
    '<li>MCP 서버나 native 도구(VectorDB/GraphDB/외부 자료 검색)를 이름으로 등록해 여러 Agent가 재사용합니다.</li>' +
    '<li>native는 config에 {"native_id": "vectordb_search"} 또는 {"native_id": "graphdb_query"} 또는 {"native_id": "web_search"}(Wikipedia 검색, API 키 불필요·무료 — skill_tags에 "외부검색"을 넣으면 매칭됩니다)를 지원합니다.</li>' +
    '<li>mcp_stdio/mcp_remote는 {"command":...,"args":[...]} 또는 {"url":...,"transport":...} 형태입니다 — 이 두 종류는 Agent가 실행될 때마다 실제로 서버(로컬 프로세스 또는 원격)에 접속을 시도하므로, 잘못된 command/url을 등록해두면 그 스킬을 가진 Agent 실행이 접속 단계에서 실패합니다.</li>' +
    '<li>"활성" 표시를 꺼두면(비활성) 스킬이 매칭돼도 더 이상 자동으로 붙지 않습니다(등록은 유지).</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 도구를 참조하던 Agent 개별 연결도 함께 지웁니다.</li>' +
    '</ul>' +
    table(["ID", "이름", "종류", "설명", "활성", "동작"], tools.map(t => [
      t.id, esc(t.name), esc(t.kind), esc(t.description), t.is_active ? "활성" : "비활성",
      isAdmin ? '<div class="actions"><button class="small danger" data-delete-tool="' + t.id + '">삭제</button></div>' : "",
    ])) +
    '<form class="inline" id="tool-form">' +
    '<input id="tp-name" placeholder="이름" required>' +
    '<select id="tp-kind"><option value="native">native</option><option value="mcp_remote">mcp_remote</option><option value="mcp_stdio">mcp_stdio</option></select>' +
    '<input id="tp-description" placeholder="설명" style="flex:1;min-width:160px">' +
    '<input id="tp-config" placeholder="config JSON, 예: {&#39;native_id&#39;:&#39;vectordb_search&#39;}" style="flex:1;min-width:220px">' +
    '<button class="primary" type="submit">도구 등록</button></form>' +
    "</section>" +
    '<section class="card"><h2>Skill Pool — skill_tags 정식 등록</h2>' +
    '<ul class="muted help-list">' +
    '<li>모드 "tool"인 스킬은 기본 도구 ID가 가리키는 도구를 이 스킬(이름이 정확히 일치)을 가진 모든 Agent에게 실행할 때마다 자동으로 붙입니다(run_crew/crewai_agent_generator 실행마다 다시 계산 — 나중에 스킬 설정을 바꾸면 이미 만들어진 Agent에도 다음 실행부터 바로 반영됩니다).</li>' +
    '<li>모드 "none"은 도구 연결 없이 이름표만 붙이는 용도입니다 — 하니스 방법론/agent-evaluator 절차 스킬 17개가 여기 해당합니다(사람 판단이 필요한 절차라 자동 도구화가 안 됨).</li>' +
    '<li>"recommend-fix-workflow" 하나만 예외로 실제 MCP 도구(정적 Gate 조치 지식 조회)가 붙어 있습니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 이 스킬 레코드만 지웁니다 — 같은 이름의 skill_tags를 가진 Agent/사람은 그대로 남고, 다음 실행부터 자동 도구 연결만 안 됩니다.</li>' +
    '</ul>' +
    table(["ID", "이름", "설명", "기본 도구 ID", "모드", "동작"], skills.map(s => [
      s.id, esc(s.name), esc(s.description), esc(s.default_tool_pool_ids.join(", ") || "-"), esc(s.augmentation_mode),
      isAdmin ? '<div class="actions"><button class="small danger" data-delete-skill="' + s.id + '">삭제</button></div>' : "",
    ])) +
    '<form class="inline" id="skill-form">' +
    '<input id="sp-name" placeholder="스킬명(skill_tags와 일치)" required>' +
    '<input id="sp-description" placeholder="설명" style="flex:1;min-width:160px">' +
    '<input id="sp-tool-ids" placeholder="기본 도구 ID(콤마)" style="width:140px">' +
    '<select id="sp-mode"><option value="tool">tool(자동 호출)</option><option value="always">always</option><option value="none">none</option></select>' +
    '<button class="primary" type="submit">스킬 등록</button></form>' +
    "</section>";

  document.getElementById("tool-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    let config = {};
    const raw = document.getElementById("tp-config").value.trim();
    if (raw) {
      try { config = JSON.parse(raw); } catch (e) {
        showMsg("global-msg", "config가 올바른 JSON이 아닙니다: " + e.message, "error"); return;
      }
    }
    try {
      await api("POST", "/api/tool-pool", {
        name: document.getElementById("tp-name").value,
        kind: document.getElementById("tp-kind").value,
        description: document.getElementById("tp-description").value,
        config,
      });
      showMsg("global-msg", "도구를 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  document.getElementById("skill-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const idsRaw = document.getElementById("sp-tool-ids").value.trim();
    const default_tool_pool_ids = idsRaw
      ? idsRaw.split(",").map(s => Number(s.trim())).filter(n => Number.isInteger(n) && n > 0)
      : [];
    try {
      await api("POST", "/api/skill-pool", {
        name: document.getElementById("sp-name").value,
        description: document.getElementById("sp-description").value,
        default_tool_pool_ids,
        augmentation_mode: document.getElementById("sp-mode").value,
      });
      showMsg("global-msg", "스킬을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-delete-tool]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 도구를 완전히 지웁니다(연결된 Agent 연결도 함께 지워집니다, 되돌릴 수 없음). 계속할까요?")) return;
      try {
        await api("DELETE", "/api/tool-pool/" + btn.dataset.deleteTool);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-skill]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 스킬을 완전히 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
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
   다시 기획"). `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 1+2 —
   human_pool/agent_pool 멤버를 역할명과 함께 묶어 등록하는 "등록→재사용"
   화면(tool_pool/skill_pool/model_registry와 같은 패턴). 팀 자체를 할 일에
   배정하는 동작("배정 요청")은 이 탭이 아니라 "업무 요청" 탭의 할 일 상세
   패널에서 한다(팀 정의와 배정은 관심사가 다르다 — teams.py 참고). */

async function renderTeams(content) {
  const [teams, people, agents] = await Promise.all([
    api("GET", "/api/teams"),
    api("GET", "/api/human-pool"),
    api("GET", "/api/agent-pool"),
  ]);
  const isAdmin = ME.role === "admin";
  const memberOptionsHtml =
    '<option value="">멤버 선택</option>' +
    '<optgroup label="사람">' +
    people.map(p => '<option value="human:' + p.id + '">' + esc(p.name) + '(' + esc(p.role) + ')</option>').join("") +
    '</optgroup>' +
    '<optgroup label="Agent">' +
    agents.map(a => '<option value="agent:' + a.id + '">' + esc(a.role_name) + '(#' + a.id + ')</option>').join("") +
    '</optgroup>';

  const teamCardHtml = (t) =>
    '<section class="card" data-team-card="' + t.id + '">' +
    '<h3>#' + t.id + " " + esc(t.name) + (t.is_active ? "" : ' <span class="gate-badge gate-warn">비활성</span>') + '</h3>' +
    (t.description ? '<p class="muted">' + esc(t.description) + '</p>' : "") +
    table(["타입", "멤버", "역할 라벨", "동작"], t.members.map(m => [
      esc(label(m.member_type)), esc(m.member_name || ("#" + m.member_id)), esc(m.role_label),
      '<button class="small danger" data-remove-member="' + t.id + ':' + m.id + '">제외</button>',
    ])) +
    '<form class="inline" data-add-member-form="' + t.id + '">' +
    '<select data-member-select required style="min-width:180px">' + memberOptionsHtml + '</select>' +
    '<input data-role-label placeholder="역할 라벨(선택)" style="width:140px">' +
    '<button class="small" type="submit">멤버 추가</button></form>' +
    '<div class="actions" style="margin-top:0.5rem">' +
    '<button class="small" data-toggle-active="' + t.id + '" data-next-active="' + (t.is_active ? "0" : "1") + '">' +
    (t.is_active ? "비활성화" : "활성화") + '</button>' +
    (isAdmin ? '<button class="small danger" data-delete-team="' + t.id + '">팀 삭제</button>' : "") +
    '</div>' +
    '</section>';

  content.innerHTML =
    '<section class="card"><h2>팀 — 복수 사람·Agent 그룹 등록</h2>' +
    '<ul class="muted help-list">' +
    '<li>사람(human_pool)·Agent(agent_pool) 멤버를 역할 라벨과 함께 묶어두면, "업무 요청" 탭의 할 일 상세에서 이 팀 전체를 한 할 일에 배정(resource_type="team")할 수 있습니다.</li>' +
    '<li>팀 배정의 결재 주체는 팀 리더(기본값은 배정 요청자, 확정 전까지 변경 가능)입니다 — 배정이 승인되면 팀의 사람 멤버(계정 연결된 경우)·팀 리더 모두 그 할 일의 협업 워크스페이스에 결과물을 쓸 수 있습니다.</li>' +
    '<li>그 워크스페이스의 "팀 Agent 실행" 버튼으로 이 팀의 온보딩된 Agent 멤버 전원을 실제 CrewAI Crew로 함께 실행할 수 있습니다(결과는 Agent별 결과물+결재 체인으로 그대로 쌓입니다).</li>' +
    '<li>Agent를 이 탭에서 바로 만들려면 "AI 에이전트 생성"에서 여러 할 일을 한 번에 골라 생성한 뒤 그 결과 화면의 "팀으로 묶기"를 쓰는 게 더 빠릅니다.</li>' +
    '</ul>' +
    '<form class="inline" id="team-create-form">' +
    '<input id="team-name" placeholder="팀 이름" required>' +
    '<input id="team-description" placeholder="설명(선택)" style="flex:1;min-width:160px">' +
    '<button class="primary" type="submit">팀 등록</button></form>' +
    "</section>" +
    (teams.length ? teams.map(teamCardHtml).join("") : '<p class="muted">등록된 팀이 없습니다.</p>');

  document.getElementById("team-create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/teams", {
        name: document.getElementById("team-name").value,
        description: document.getElementById("team-description").value,
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

  content.querySelectorAll("[data-delete-team]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 팀을 완전히 지웁니다(되돌릴 수 없음). 과거 배정 이력은 남습니다. 계속할까요?")) return;
      try {
        await api("DELETE", "/api/teams/" + btn.dataset.deleteTeam);
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
  const providerTableHtml = (provider) => {
    const rows = byProvider(provider);
    return "<h3>" + providerLabel[provider] + "</h3>" +
      table(["모델명", "상태", "동작"], rows.map(m => [
        esc(m.name),
        m.is_active ? "활성" : "비활성",
        '<div class="actions">' +
          '<button class="small" data-toggle-model-active="' + m.id + '" data-active="' + (m.is_active ? "0" : "1") + '">' +
          (m.is_active ? "비활성화" : "활성화") + "</button>" +
          (isAdmin ? '<button class="small danger" data-delete-model="' + m.id + '">삭제</button>' : "") +
          "</div>",
      ]));
  };

  content.innerHTML =
    '<section class="card"><h2>모델 레지스트리</h2>' +
    '<ul class="muted help-list">' +
    '<li>여기 등록한 모델이 "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"·협업 워크스페이스의 "모델명" 입력창 자동완성에 뜹니다 — 목록에 없는 이름도 그대로 입력할 수 있어 자유 입력 자체는 막지 않습니다(참고용 자동완성).</li>' +
    '<li><b>불러오기</b>는 Ollama면 로컬 서버(OLLAMA_BASE_URL)에서, OpenAI/Anthropic이면 그 계정의 API 키로 실제 보유 중인 모델 목록을 조회합니다(토큰을 생성하지 않는 카탈로그 조회라 과금되지 않습니다) — 조회만으로는 아무것도 저장되지 않고, 선택 후 "선택한 모델 등록"을 눌러야 저장됩니다.</li>' +
    '<li><b>비활성화</b>는 자동완성에서만 빼고 등록 자체는 유지합니다.</li>' +
    '<li><b>삭제</b>(관리자 전용)는 레지스트리 항목만 지웁니다 — 이미 Agent에 저장된 모델 설정 자체는 안 바뀝니다.</li>' +
    '</ul>' +
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
      if (!window.confirm("이 모델을 레지스트리에서 지웁니다(되돌릴 수 없음, 이미 저장된 Agent의 모델 설정 자체는 안 바뀝니다). 계속할까요?")) return;
      try {
        await api("DELETE", "/api/model-registry/" + btn.dataset.deleteModel);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- Crew 실행(ADR 결정 11 — Agent 협업) ---------- */

// 사용자 보고(2026-10-06, "crew 실행 UI/UX를 개선해줘 — 에이전트 ID를
// 알 수 없음 등") — 이전엔 위 표에서 ID를 직접 읽어 textarea에
// "1: 할 일" 식으로 손으로 옮겨 적어야 했다(오타·형식 실수 위험, 이
// 세션에서 반복 고친 "직접 입력 대신 실제 데이터에서 선택" 패턴과 같은
// 문제). Agent별로 드롭다운+할 일 입력 행을 두고 "+ Agent 추가"로 늘리는
// 방식으로 바꾼다 — 드롭다운 자체가 "#id 역할명(스킬)"을 보여줘 ID를
// 몰라도 된다.
function crewAgentOptionsHtml(active) {
  return active.map(a =>
    '<option value="' + a.id + '">#' + a.id + " " + esc(a.role_name) +
    (a.skill_tags.length ? " (" + esc(a.skill_tags.join(", ")) + ")" : "") + "</option>"
  ).join("");
}

function crewRowHtml(active) {
  return '<div class="inline crew-row" style="margin-bottom:0.3rem">' +
    '<select class="crew-row-agent" style="min-width:220px"><option value="">— Agent 선택 —</option>' +
    crewAgentOptionsHtml(active) + "</select>" +
    '<input class="crew-row-task" placeholder="이 Agent가 할 일" style="flex:1;min-width:200px">' +
    '<button type="button" class="small danger crew-row-remove">×</button>' +
    "</div>";
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
    '<ul class="muted help-list">' +
    '<li>실제 업무에 여러 Agent를 역할별로 묶어 투입하려면: ① "AI 에이전트 생성"에서 한 브리프의 여러 할 일을 같이 선택해 역할별 Agent를 만들고 그 결과 화면에서 "팀으로 묶기"를 누르거나, 이미 있는 Agent들을 "팀 관리"에서 팀으로 등록한 뒤 ② "업무 요청"의 할 일 상세에서 그 팀을 배정 → 결재까지 마치면 ③ 협업 워크스페이스의 "팀 Agent 실행(Crew)" 버튼으로 실행하세요 — 그 경로는 결과가 그 할 일의 결과물+결재 체인으로 그대로 쌓입니다.</li>' +
    '<li>아래는 그 전에 조합·프롬프트를 미리 테스트해보고 싶을 때만 쓰세요.</li>' +
    '<li>Agent를 드롭다운으로 고르고 그 옆에 할 일을 입력하세요 — "+ Agent 추가"로 참여 인원을 늘릴 수 있습니다.</li>' +
    '<li><b>sequential</b>이면 앞줄 에이전트의 산출물이 다음 에이전트에게 자동으로 전달됩니다(순서대로 릴레이).</li>' +
    '<li><b>hierarchical</b>은 "매니저 Agent"를 지정하면 그 매니저가 작업을 조율·위임만 하고 직접 산출물을 내지는 않습니다(비워두면 CrewAI가 자동으로 매니저 LLM을 하나 배정) — 아래 결과의 "Agent별 산출물"에서 매니저 행은 항상 "(없음)"으로 나옵니다.</li>' +
    '<li>모든 Agent는 위임(allow_delegation)이 켜져 있어 필요하면 서로에게 실제로 질문·재위임할 수 있고, 그 내역이 "실제 위임/협업 발생 내역"에 기록됩니다.</li>' +
    '<li>온보딩(active) 안 된 Agent는 드롭다운에 안 뜨고 지정할 수도 없습니다.</li>' +
    '<li>실제 LLM API를 호출하므로 비용이 발생합니다(참여 Agent 수만큼 커질 수 있음).</li>' +
    '</ul>' +
    (active.length
      ? table(["ID", "역할", "스킬"], active.map(a => [a.id, esc(a.role_name), esc(a.skill_tags.join(", "))]))
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
    '<div id="crew-result"></div></section>';

  wireProviderModelSelect("crew-provider", "crew-model", modelsByProvider);
  wireCrewRowRemovers();
  const addRowBtn = document.getElementById("crew-add-row");
  if (addRowBtn) {
    addRowBtn.onclick = () => {
      document.getElementById("crew-rows").insertAdjacentHTML("beforeend", crewRowHtml(active));
      wireCrewRowRemovers();
    };
  }

  document.getElementById("crew-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showMsg("global-msg", "", "");  // 이전 시도의 에러 배너가 남아 새 결과와 헷갈리지 않도록 지운다
    const rows = Array.from(document.querySelectorAll(".crew-row"));
    const agentIds = [];
    const taskDescriptions = [];
    for (const row of rows) {
      const agentId = Number(row.querySelector(".crew-row-agent").value);
      const taskDesc = row.querySelector(".crew-row-task").value.trim();
      if (!agentId) { showMsg("global-msg", "모든 줄에서 Agent를 선택하세요.", "error"); return; }
      if (!taskDesc) { showMsg("global-msg", "모든 줄에서 할 일을 입력하세요.", "error"); return; }
      agentIds.push(agentId);
      taskDescriptions.push(taskDesc);
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 Agent는 선택해야 합니다.", "error"); return; }
    const managerVal = document.getElementById("crew-manager").value;
    if (!window.confirm(agentIds.length + "개 Agent로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?")) return;

    const submitBtn = ev.submitter || document.querySelector("#crew-form button[type=submit]");
    try {
      const result = await withBusy(submitBtn, "실행 중…(시간이 걸릴 수 있습니다)", () => api("POST", "/api/crew/run", {
        agent_ids: agentIds,
        task_descriptions: taskDescriptions,
        process: document.getElementById("crew-process").value,
        manager_agent_id: managerVal ? Number(managerVal) : null,
        provider: document.getElementById("crew-provider").value || null,
        model: modelSelectValue("crew-model"),
      }));
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
   `result.delegations`에 실제로 기록된 위임만 그린다(추측 금지, ADR
   결정 11 "실제 발동 증거를 남긴다" 원칙과 같은 정신) — 대상이
   확인되지 않으면(알 수 없는 동료를 호출해 CrewAI가 에러를 반환한
   경우 등) 화살표 대신 아래 "확인 안 된 위임" 목록에 원문을 남긴다.
   외부 차트 라이브러리 없이 순수 inline SVG로 그린다(이 앱의 기존
   무의존성 원칙과 동일). */
function crewDiagramSvg(result) {
  const agents = result.agents;
  const manager = agents.find(a => a.output === null) || null;
  const workers = agents.filter(a => a !== manager);
  const isHierarchical = result.process === "hierarchical";
  const nodeW = 150, nodeH = 44, gapX = 190;
  const positions = {};
  let managerNode = null;
  let svgWidth, svgHeight;

  if (isHierarchical) {
    svgWidth = Math.max(workers.length, 1) * gapX + 20;
    svgHeight = 170;
    managerNode = manager || { role_name: "매니저(LLM 자동 배정)", synthetic: true };
    positions[managerNode.role_name] = { x: svgWidth / 2 - nodeW / 2, y: 16 };
    workers.forEach((a, i) => { positions[a.role_name] = { x: i * gapX + 20, y: 112 }; });
  } else {
    svgWidth = Math.max(agents.length, 1) * gapX + 20;
    svgHeight = 90;
    agents.forEach((a, i) => { positions[a.role_name] = { x: i * gapX + 20, y: 23 }; });
  }
  let maxY = svgHeight;  // 위임 곡선이 마지막 행 아래로 처지면 viewBox가 그만큼 커져야 잘리지 않는다.

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
    const label = a.role_name.length > 18 ? a.role_name.slice(0, 17) + "…" : a.role_name;
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

function renderCrewResult(result) {
  const box = document.getElementById("crew-result");
  const diagram = crewDiagramSvg(result);
  let html = "<h3>협업 관계 다이어그램</h3>" +
    '<ul class="muted help-list">' +
    '<li>실선(회색)은 요청 시점에 정해진 구조(순차 전달 또는 매니저 조율)입니다.</li>' +
    '<li>점선(주황)은 실제로 발생한 위임/질의입니다.</li>' +
    '</ul>' +
    diagram.svg +
    (diagram.unresolved.length
      ? '<details class="muted"><summary>확인 안 된 위임(' + diagram.unresolved.length + '건) — 대상 Agent를 다이어그램에서 특정하지 못함</summary>' +
        table(["위임한 Agent", "LLM이 지목한 대상(원문)", "도구"], diagram.unresolved.map(d => [esc(d.from_agent_role || "-"), esc(d.to_agent_role || "(응답에 coworker 없음)"), esc(d.tool)])) +
        "</details>"
      : "");
  html += '<h3>최종 산출물</h3><div class="msg ok">' + renderContent(result.final_output) + "</div>";
  // 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
  // 연결해줘") — run_crew()가 이제 gate_scores/tcr을 반환한다. 단건
  // deliverable 카드의 gateBadge와 같은 "측정 없음" 구분 원칙(위 §530
  // 참고)을 그대로 따른다.
  const gates = Object.values(result.gate_scores);
  const measured = gates.filter(g => !g.not_measured);
  html += "<h3>Gate A-G 채점(이 Crew 실행 전체)</h3>";
  html += measured.length === 0
    ? '<p class="muted">Gate 측정 없음</p>'
    : '<p>' + measured.filter(g => g.passed).length + "/" + measured.length + " 통과" +
      (result.tcr === null ? "" : " · TCR " + result.tcr.toFixed(0) + "%") + '</p>';
  html += "<h3>Agent별 산출물</h3>" + table(["Agent ID", "역할", "산출물"], result.agents.map(a => [
    a.agent_id, esc(a.role_name),
    a.output === null ? "(없음 — 매니저는 조율만 하고 직접 산출물을 내지 않습니다)"
      : esc(a.output).slice(0, 200) + (a.output.length > 200 ? "…" : ""),
  ]));
  html += "<h3>실제 위임/협업 발생 내역(" + result.delegations.length + "건)</h3>";
  html += result.delegations.length
    ? table(["위임한 Agent 역할", "대상 Agent 역할", "사용한 도구"], result.delegations.map(d => [esc(d.from_agent_role || "-"), esc(d.to_agent_role || "(확인 안 됨)"), esc(d.tool)]))
    : '<p class="muted">이번 실행에서는 Agent 간 실제 위임이 발생하지 않았습니다(각자 독립적으로 완수).</p>';
  box.innerHTML = html;
}

/* ---------- 인력 검색 ---------- */

async function renderSearch(content) {
  const PAGE_SIZE = 20;
  let offset = 0;

  const skillDatalist = await skillDatalistHtml();
  content.innerHTML =
    '<section class="card"><h2>사람·AI 에이전트 통합 검색</h2>' +
    '<ul class="muted help-list">' +
    '<li>이 화면은 매칭 알고리즘 없이 필터로 후보를 훑어보는 용도입니다(실제 배정 점수·순위를 보려면 "인력 매칭/배정" 탭을 쓰세요).</li>' +
    '<li>AI 에이전트는 온보딩 승인(active) 안 된 것(pending_onboarding/deprecated)은 여기 안 뜹니다 — "AI 에이전트 관리" 탭에서 상태 무관 전체 목록을 볼 수 있습니다. 오프보딩된 사람도 마찬가지로 빠집니다.</li>' +
    '<li>"부서" 필터는 없습니다 — 이 시스템엔 부서 데이터가 없고, "역할"이 사실상 같은 구실을 합니다.</li>' +
    '</ul>' +
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
      ["타입", "ID", "이름", "역할", "가용", "비용", "스킬", "성과점수"],
      body.items.map(c => [esc(label(c.type)), c.id, esc(c.name || "-"), esc(c.role), c.is_available ? "가능" : "불가", c.cost, esc(c.skill_tags.join(", ")), c.performance_score === null ? "-" : c.performance_score])
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

async function renderOrgChart(content) {
  const chart = await api("GET", "/api/org-chart");
  let html = '<section class="card"><h2>조직도(확정된 배정)</h2>' +
    '<ul class="muted help-list">' +
    '<li>여기 나오는 건 결재까지 끝나 상태가 confirmed(확정)된 배정뿐입니다 — "인력 매칭/배정"에서 방금 요청했거나 "결재" 탭에서 아직 대기 중인 건은 승인되기 전까지 이 화면에 나타나지 않습니다.</li>' +
    '<li>묶는 기준(관리자)이 사람과 AI 에이전트가 다릅니다: 사람 배정은 그 사람의 "상사"(사용자 관리에서 지정)로, AI 에이전트 배정은 Agent에게 상사가 없으므로 그 배정을 요청한 사람으로 귀속시킵니다.</li>' +
    '<li>상사가 없으면 "(지정된 관리자 없음)" 묶음에 들어갑니다.</li>' +
    '</ul>';
  for (const bucket of chart) {
    html += "<h3>" + (bucket.manager_username ? esc(bucket.manager_username) : "(지정된 관리자 없음)") + "</h3>";
    html += table(["배정 ID", "할 일 ID", "역할", "타입", "리소스"], bucket.reports.map(r => [
      r.assignment_id, r.task_id, esc(r.role_name), esc(label(r.resource_type)), esc(r.resource_name || "-"),
    ]));
  }
  if (!chart.length) html += '<p class="muted">확정된 배정이 없습니다.</p>';
  html += "</section>";
  content.innerHTML = html;
}

/* ---------- 성과 리뷰 ---------- */

async function renderPerformance(content) {
  const rows = await api("GET", "/api/performance-review");
  content.innerHTML = '<section class="card"><h2>성과 리뷰</h2>' +
    '<ul class="muted help-list">' +
    '<li>사람의 "성과점수"는 사전 동의(NF9)가 있어야만 값이 채워집니다 — "사람 인력 관리" 탭에서 동의를 받은 사람만 점수를 입력할 수 있고, 동의를 철회하면 그 순간 점수가 지워집니다.</li>' +
    '<li>AI 에이전트는 동의 개념이 없어 항상 입력·조회 가능합니다.</li>' +
    '<li>이 화면의 점수는 둘 다 관리자가 직접 입력한 수동값입니다 — Agent의 실제 Gate A-G 자동 채점 결과는 이 화면이 아니라 "AI 에이전트 관리" 탭의 "자동평가(Gate/TCR)" 열에서 따로 봅니다(둘을 아직 한 화면에 병기하진 않습니다).</li>' +
    '<li>"확정 배정 수"만 실제 데이터(confirmed 배정 건수)입니다.</li>' +
    '</ul>' +
    table(["타입", "ID", "이름", "역할", "동의", "성과점수", "확정 배정 수"], rows.map(r => [
      esc(label(r.type)), r.id, esc(r.name || "-"), esc(r.role),
      r.performance_consent === null ? "-" : (r.performance_consent ? "동의함" : "미동의"),
      r.performance_score === null ? "-" : r.performance_score,
      r.confirmed_assignments,
    ])) + "</section>";
}

/* ---------- 매니저 대시보드(Phase 5, 사용자 요청 2026-09-28) ---------- */

async function renderDashboard(content) {
  const d = await api("GET", "/api/dashboard");
  const gs = d.team_gate_scores;
  const gateRow = ["A", "B", "C", "D", "E", "F", "G"].map(g => {
    const v = gs.gate_averages[g];
    return '<div class="stat-row"><span>' + g + '</span><span>' + (v === null ? "-" : v.toFixed(2)) + '</span></div>';
  }).join("");

  const trendRows = d.cost_trend.map(t => [
    esc(t.created_at), esc(label(t.subject_type)), t.subject_id, t.cost_estimate,
  ]);

  // ASANA_PARITY_DESIGN_PLAN.md F18(2026-10-08, Phase 4) — "팀 부하" 바
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

  content.innerHTML =
    '<section class="card"><h2>매니저 대시보드</h2>' +
    '<ul class="muted help-list">' +
    '<li>팀 전체 Agent Gate 점수 평균·대기 승인 건수·비용 변경 이력·팀 부하·주의 필요 업무를 한 화면에 모았습니다.</li>' +
    '<li>Gate 점수는 실제로 측정된 산출물이 있을 때만 값이 나옵니다(not_measured는 평균에서 제외).</li>' +
    '<li>비용 추이는 "AI 에이전트 관리"·"사람 인력 관리" 탭에서 단가를 바꿀 때마다 남는 감사로그 이력이라, 지금까지 아무도 단가를 바꾼 적이 없으면 비어 있습니다.</li>' +
    '<li>"주의 필요"는 마감일 개념이 이 플랫폼에 없어 "선행 작업 미완료로 막힘"·"결과물 2회 이상 반려"만 신호로 씁니다.</li>' +
    '</ul>' +
    '<div class="dashboard-grid">' +
    '<div class="card"><h3>팀 Agent 평균 Gate 점수(표본 ' + gs.sample_count + '건)</h3>' + gateRow +
    '<div class="stat-row"><span>평균 TCR</span><span>' + (gs.avg_tcr === null ? "-" : gs.avg_tcr.toFixed(1) + "%") + '</span></div></div>' +
    '<div class="card"><h3>대기 승인 건수</h3><p style="font-size:2em;margin:0">' + d.pending_approval_count + '</p></div>' +
    '<div class="card"><h3>현재 비용 스냅샷</h3><p style="font-size:2em;margin:0">' + d.cost_snapshot.total_cost + '</p></div>' +
    '</div>' +
    '<h3>팀 부하(진행 중인 업무 수)</h3>' + workloadRows +
    '<h3>주의 필요(' + d.bottlenecks.length + '건)</h3><ul>' + bottleneckRows + '</ul>' +
    '<h3>비용 변경 이력(최근 ' + d.cost_trend.length + '건)</h3>' +
    table(["시각", "대상 종류", "대상 ID", "변경된 단가"], trendRows) +
    '</section>';
}

/* ---------- 비용 ---------- */

async function renderCost(content) {
  const report = await api("GET", "/api/cost-report");
  content.innerHTML = '<section class="card"><h2>비용 리포트(총액: ' + report.total_cost + ')</h2>' +
    '<ul class="muted help-list">' +
    '<li>이 "합계"는 "단가(cost_estimate, 사람/에이전트 관리 탭에서 수동 입력)" × "확정 배정 수"입니다.</li>' +
    '<li>실제로 LLM API에 청구된 금액이 아닙니다(simple_llm/crewai 생성이나 Crew 실행·에이전트 실행 때 실제로 나간 API 비용은 이 리포트에 잡히지 않습니다).</li>' +
    '<li>즉 "일 시켰을 때 실제 API 비용"이 아니라 "이 인력을 계속 쓰면 예상되는 인건비/도입비" 관점의 숫자입니다.</li>' +
    '</ul>' +
    table(["타입", "ID", "이름", "역할", "단가", "확정 배정 수", "합계"], report.resources.map(r => [
      esc(label(r.type)), r.id, esc(r.name || "-"), esc(r.role), r.cost_estimate, r.confirmed_assignments, r.total_cost,
    ])) + "</section>";
}

/* ---------- 공정성 ---------- */

async function renderFairness(content) {
  const r = await api("GET", "/api/fairness-report");
  content.innerHTML = '<section class="card"><h2>배정 공정성 지표</h2>' +
    '<ul class="muted help-list">' +
    '<li>사람과 AI 에이전트가 같은 매칭 기준(가용성/적합도/비용 가중치)으로 배정되는데도 반려율에 체계적 차이가 나는지 보는 지표입니다.</li>' +
    '<li>"확정"·"반려"는 결재까지 끝난(confirmed/rejected) 배정만 세고, 아직 대기 중인 배정은 "결정됨"에 안 들어갑니다.</li>' +
    '<li>"반려율 격차"가 크면 어느 한쪽이 불리하게 반려되는 패턴이 있을 수 있다는 신호입니다(단, 표본이 작으면 우연일 수 있음).</li>' +
    '</ul>' +
    (r.low_sample_warning ? '<p class="msg error">표본이 작아(각 10건 미만) 통계적 해석에 주의가 필요합니다.</p>' : "") +
    table(["구분", "확정", "반려", "결정됨", "반려율"], [
      ["사람", r.human.confirmed, r.human.rejected, r.human.decided, r.human.rejection_rate === null ? "-" : r.human.rejection_rate.toFixed(3)],
      ["AI 에이전트", r.agent.confirmed, r.agent.rejected, r.agent.decided, r.agent.rejection_rate === null ? "-" : r.agent.rejection_rate.toFixed(3)],
    ]) +
    "<p>반려율 격차: " + (r.rejection_rate_gap === null ? "-" : r.rejection_rate_gap.toFixed(3)) + "</p></section>";
}

/* ---------- 활동 기록 ---------- */

async function renderAudit(content) {
  content.innerHTML =
    '<section class="card"><h2>활동 기록</h2>' +
    '<ul class="muted help-list">' +
    '<li>이 로그는 DB 트리거로 수정·삭제 자체가 막혀 있는 append-only 기록입니다(NF10) — 다른 탭의 "삭제" 버튼으로 브리프/할 일/사람/에이전트/도구/스킬을 실제로 지워도, "무엇을 언제 누가 지웠는지"는 여기 그대로 남습니다.</li>' +
    '<li>Admin·매니저만 조회할 수 있습니다.</li>' +
    '</ul>' +
    '<form class="inline" id="audit-form">' +
    '<input id="a-actor" type="number" min="1" step="1" placeholder="처리자 ID">' +
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
      table(["시각", "처리자 ID", "동작", "대상 종류", "대상 ID"], body.items.map(a => [
        esc(a.created_at), a.actor_id, esc(a.action), esc(label(a.subject_type)), a.subject_id === null ? "-" : a.subject_id,
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
    '<ul class="muted help-list">' +
    '<li>4가지 권한(role)은 화면 접근 범위를 결정합니다.</li>' +
    '<li><b>관리자</b>: 전 기능.</li>' +
    '<li><b>매니저</b>: "운영 관리" 대부분에 접근 가능하지만, 사용자 생성/상사 지정·각종 삭제·Agent 온보딩 승인/오프보딩처럼 조직 구조에 직접 영향을 주는 기능은 관리자 전용.</li>' +
    '<li><b>팀원</b>: 업무 요청 제출·결재 요청 등 "내 업무" 화면.</li>' +
    '<li><b>뷰어</b>: 조회만.</li>' +
    '<li>생성 직후엔 상사가 지정 안 돼 있어(아래에서 지정), 이 사용자가 배정을 요청하면 결재가 단일 단계(승인자 미지정)로 처리됩니다.</li>' +
    '</ul>' +
    '<form class="inline" id="user-form">' +
    '<input id="u-username" placeholder="아이디" required>' +
    '<input id="u-password" type="password" placeholder="비밀번호" required>' +
    '<select id="u-role"><option value="admin">관리자</option><option value="manager">매니저</option>' +
    '<option value="member" selected>팀원</option><option value="viewer">뷰어</option></select>' +
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
    '<ul class="muted help-list">' +
    '<li>비밀번호는 bcrypt 해시로 저장돼 "조회"는 할 수 없습니다 — <b>초기화</b>로 새 비밀번호를 지정한 뒤 별도 채널로 전달하세요.</li>' +
    '<li><b>계정 상태</b>는 하드 삭제 대신 비활성화 방식입니다(감사 이력·배정 이력이 이 계정을 참조하고 있어 완전히 지우면 그 이력이 깨집니다) — 비활성화된 계정은 즉시 로그인이 막히고 기존 세션도 끊깁니다.</li>' +
    '<li>상사를 지정하면 그 사람이 배정을 요청할 때 결재가 상사 체인을 따라 다단계로 자동 라우팅됩니다("결재" 탭 참고).</li>' +
    '<li>본인을 직접 상사로 지정하는 것만 막혀 있고, A→B→A 같은 두 단계 이상의 순환은 막지 않습니다.</li>' +
    '</ul>' +
    table(["ID", "아이디", "권한", "계정 상태", "상사", "상사 지정", "비밀번호 재설정", "계정 상태 변경"], users.map(u => {
      const isSelf = u.id === ME.id;
      return [
        u.id, esc(u.username), esc(u.role),
        u.is_active ? '<span class="gate-badge gate-ok">활성</span>' : '<span class="gate-badge gate-warn">비활성</span>',
        u.manager_id === null ? "-" : u.manager_id,
        '<div class="actions"><span class="pair"><select id="mgr-' + u.id + '"><option value="">(없음)</option>' +
          users.filter(m => m.id !== u.id).map(m => '<option value="' + m.id + '"' + (m.id === u.manager_id ? " selected" : "") + ">" + esc(m.username) + "</option>").join("") +
        '</select><button class="small" data-set-manager="' + u.id + '">저장</button></span></div>',
        '<div class="actions"><span class="pair"><input type="password" id="pw-' + u.id + '" placeholder="새 비밀번호" style="width:120px">' +
          '<button class="small" data-reset-password="' + u.id + '">초기화</button></span></div>',
        isSelf
          ? '<span class="muted">본인 계정</span>'
          : (u.is_active
            ? '<button class="small danger" data-set-status="' + u.id + '" data-active="0">비활성화</button>'
            : '<button class="small" data-set-status="' + u.id + '" data-active="1">재활성화</button>'),
      ];
    })) + "</section>";

  document.getElementById("user-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/users", {
        username: document.getElementById("u-username").value,
        password: document.getElementById("u-password").value,
        role: document.getElementById("u-role").value,
      });
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
      if (!window.confirm("이 계정의 비밀번호를 초기화합니다. 새 비밀번호를 본인에게 안전하게 전달해야 합니다. 계속할까요?")) return;
      try {
        await api("PATCH", "/api/users/" + btn.dataset.resetPassword + "/password", {
          password: newPassword,
        });
        showMsg("global-msg", "비밀번호를 초기화했습니다.", "ok");
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
      if (!window.confirm(msg)) return;
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

boot();
</script>
</body>
</html>
"""
