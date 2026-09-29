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
  .nav-group.common-group { background: #eafaf7; }
  .nav-label { font-size: 0.72rem; font-weight: 600; color: #888; margin-right: 0.4rem; min-width: 72px; }
  .nav-group.admin-group .nav-label { color: #a15c00; }
  .nav-group.common-group .nav-label { color: #0f766e; }
  nav button { background: none; border: 1px solid #ccc; padding: 0.35rem 0.7rem; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }
  nav button.active { background: #14213d; color: #fff; border-color: #14213d; }
  .nav-group.admin-group button { border-color: #f0c98a; }
  .nav-group.admin-group button.active { background: #a15c00; border-color: #a15c00; }
  .nav-group.common-group button { border-color: #99e2d6; }
  .nav-group.common-group button.active { background: #0f766e; border-color: #0f766e; }
  .badge-adminonly { font-size: 0.65rem; background: #a15c00; color: #fff; border-radius: 3px; padding: 0 0.3rem; margin-left: 0.3rem; }
  main { padding: 1rem; max-width: 1100px; margin: 0 auto; }
  section.card { background: #fff; border: 1px solid #e2e4e8; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
  .deliverable-card { background: #f9fafb; border: 1px solid #e2e4e8; border-radius: 6px; padding: 0.6rem 0.75rem; margin-bottom: 0.6rem; }
  .deliverable-card + .deliverable-card { margin-top: 0; }
  .deliverable-card.chat-human { margin-right: 12%; border-left: 3px solid #2563eb; }
  .deliverable-card.chat-agent { margin-left: 12%; border-left: 3px solid #7c3aed; background: #f5f3ff; }
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
  .muted { color: #777; font-size: 0.8rem; }
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

async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
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
  human: "사람", agent: "AI 에이전트",
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
   조용히 매칭이 안 되는 걸 줄이는 게 목적이다. 한계: 콤마로 여러 개를
   입력하는 필드(d-skills/hp-skills)는 브라우저가 입력값 전체를 기준으로
   맞추기 때문에, 이미 뭔가 입력한 뒤 콤마 다음 항목에는 자동완성이 잘
   안 먹는다(단일 값 필드인 검색의 s-skill은 문제없이 동작) — 완전한
   태그 입력 위젯을 새로 만들진 않고 이 정도로 그쳤다. */
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
  { id: "inbox", label: "인박스", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderInbox },
  { id: "mywork", label: "내 업무", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderMyWork },
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

function buildNav() {
  const nav = document.getElementById("tabs");
  nav.innerHTML = "";

  const userTabs = TABS.filter(t => t.group === "user" && t.roles.includes(ME.role));
  const commonTabs = TABS.filter(t => t.group === "common" && t.roles.includes(ME.role));
  const adminTabs = TABS.filter(t => t.group === "admin" && t.roles.includes(ME.role));

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

  addGroup("내 업무", userTabs, "");
  addGroup("공용 조회", commonTabs, "common-group");
  addGroup("운영 관리", adminTabs, "admin-group");
}

let currentTab = null;

async function selectTab(id) {
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
    await api("POST", "/login", {
      username: document.getElementById("login-username").value,
      password: document.getElementById("login-password").value,
    });
    await boot();
  } catch (e) {
    showMsg("login-msg", e.message, "error");
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await api("POST", "/logout");
  await boot();
});

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

  content.innerHTML =
    '<section class="card"><h2>내게 필요한 조치</h2>' +
    '<p class="muted">결재 대기·AI 에이전트 실행 실패·낮은 Gate 점수 경고(승인/반려 전인 것만)를 한 곳에 모았습니다. 완전한 시간순 통합은 아니고 종류별로 묶여 있습니다 — 처리는 각 항목의 원래 화면(결재/내 업무)에서 합니다.</p>' +
    (items.length ? cards : '<p class="muted">지금 조치가 필요한 항목이 없습니다.</p>') +
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
    '<p class="muted">확정된 배정 중 내가 직접 할 일(사람 배정 당사자)이거나 내가 AI에게 맡긴 일(Agent 배정 요청자)만 보입니다. "열기"를 누르면 그 업무의 협업 워크스페이스(결과물 타임라인)가 아래에 펼쳐집니다.</p>' +
    // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
    // 분석하고 부족한 기능을 개선해줘") — 이전엔 "결과물 수"·"최근
    // 결과물" 시각만 있어 지금 상태가 뭔지, 최종 결과물(승인)이 나왔는지
    // 업무를 직접 열어보기 전엔 알 수 없었다. "상태"·"최종 결과물"
    // 열을 추가한다.
    table(["할 일 ID", "역할", "유형", "관계", "상태", "최종 결과물", "결과물 수", "최근 갱신", "동작"], rows.map(r => [
      r.task_id, esc(r.role_name), esc(label(r.resource_type)),
      r.my_relationship === "assignee" ? "내가 할 일" : "내가 맡긴 일",
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

async function renderCollabWorkspace(taskId) {
  const detail = document.getElementById("mywork-detail");
  detail.dataset.taskId = String(taskId);
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  let items, jobs, permission;
  try {
    items = await api("GET", "/api/tasks/" + taskId + "/deliverables");
    jobs = await api("GET", "/api/tasks/" + taskId + "/agent-execution-jobs");
    // 사용자가 스크린샷으로 보고한 갭(2026-09-29) — 참가자기만 하면
    // 무조건 실행 버튼을 보여줘, human assignee가 눌러야만 "권한이
    // 없습니다" 403으로 뒤늦게 알게 됐다. 버튼을 그리기 전에 먼저 확인한다.
    permission = await api("GET", "/api/tasks/" + taskId + "/execution-permission");
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
    const decideActions = (isSupervisor && d.approval_id)
      ? '<div class="actions"><button class="small" data-decide-deliverable="' + d.approval_id + '" data-approve="1">승인</button>' +
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
        '<div>' + esc(sourceJob.retrieved_context).replace(/\\n/g, "<br>") + '</div></details>'
      : '';
    return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted">' + esc(label(d.author_type)) + " · " + esc(label(d.status)) + " · " + esc(d.created_at) + " " + gateBadge + '</div>' +
    '<div>' + esc(d.content).replace(/\\n/g, "<br>") + '</div>' +
    evidenceBlock +
    decideActions +
    submitAction +
    '<div id="comments-' + d.id + '"></div>' +
    '<form class="inline" data-comment-form="' + d.id + '">' +
    '<input placeholder="댓글" id="comment-input-' + d.id + '" style="flex:1">' +
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
      '<div>' + esc(finalResult.content).replace(/\\n/g, "<br>") + '</div>' +
      '</div>'
    : '<p class="muted">아직 승인된 최종 결과물이 없습니다 — 아래는 진행 중인 이력입니다.</p>';

  const modelFields = isSupervisor
    ? '<select id="ws-provider"><option value="">provider 자동 판별</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      '<input id="ws-model" placeholder="모델명(비우면 기본값)">'
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
  const executeBtnHtml =
    '<button class="small" id="ws-execute-agent-btn"' + (executeDisabled ? " disabled" : "") + '>' +
    esc(executeBtnLabel) + '</button>' +
    (!hasRunningJob && !permission.eligible
      ? '<p class="muted">' + esc(EXECUTION_BLOCK_REASONS[permission.reason] || "실행할 수 없습니다.") + '</p>'
      : '');

  // 사용자 요청(2026-09-29, "UI/UX를 개선해줘") — 원래 textarea·provider/
  // model·"LLM으로 도와줘"·"결과물 제출"이 한 줄(.inline flex-wrap)에 전부
  // 뒤섞여 있어 좁은 화면에서 텍스트가 select 옆에 끼어 줄바꿈이 어색했다.
  // textarea를 독립된 줄로 분리하고, "결과물 작성"과 "AI 에이전트 실행"을
  // 서로 다른 카드로 나눠 두 행동의 성격이 다르다는 걸 시각적으로 구분한다.
  detail.innerHTML =
    '<h3>협업 워크스페이스 — 할 일 #' + esc(taskId) + '</h3>' +
    finalResultCard +
    '<h4 class="muted">진행 이력(중간 과정 포함)</h4>' +
    '<div id="workspace-timeline">' + timeline + '</div>' +
    '<section class="card">' +
    '<h4>결과물 작성</h4>' +
    '<form id="ws-submit-form">' +
    '<textarea id="ws-content" rows="3" placeholder="결과물 내용을 입력하세요" style="width:100%;box-sizing:border-box"></textarea>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    modelFields +
    '<button class="small" type="button" id="ws-assist-btn">LLM으로 도와줘(비용 발생)</button>' +
    '<button class="primary" type="submit">결과물 제출</button>' +
    '</div>' +
    '</form>' +
    '</section>' +
    '<section class="card">' +
    '<h4>AI 에이전트 실행</h4>' +
    executeBtnHtml +
    '</section>' +
    '<div id="ws-result"></div>';

  items.forEach(d => loadDeliverableComments(d.id));

  detail.querySelectorAll("[data-decide-deliverable]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/approvals/" + btn.dataset.decideDeliverable + "/decide", {
          approve: btn.dataset.approve === "1",
        });
        showMsg("global-msg", "처리했습니다.", "ok");
        renderCollabWorkspace(taskId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  detail.querySelectorAll("[data-submit-deliverable]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/deliverables/" + btn.dataset.submitDeliverable + "/submit", {});
        showMsg("global-msg", "검토를 요청했습니다.", "ok");
        renderCollabWorkspace(taskId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  document.getElementById("ws-submit-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const contentVal = document.getElementById("ws-content").value.trim();
    if (!contentVal) { showMsg("global-msg", "내용을 입력해야 합니다.", "error"); return; }
    try {
      await api("POST", "/api/tasks/" + taskId + "/deliverables", { content: contentVal });
      showMsg("global-msg", "결과물을 제출했습니다.", "ok");
      renderCollabWorkspace(taskId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  const assistBtn = document.getElementById("ws-assist-btn");
  assistBtn.onclick = async () => {
    const draftContext = document.getElementById("ws-content").value.trim() || null;
    const payload = { draft_context: draftContext };
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = document.getElementById("ws-model").value;
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
    const payload = {};
    if (isSupervisor) {
      const provider = document.getElementById("ws-provider").value;
      const model = document.getElementById("ws-model").value;
      if (provider) payload.provider = provider;
      if (model) payload.model = model;
    }
    try {
      const queued = await api("POST", "/api/tasks/" + taskId + "/execute-agent", payload);
      // 202 응답 직후 즉시 새로고침해 "생성 중…" 카드를 먼저 보여준다
      // (Phase 4 — 응답이 빨리 오는 게 이 비동기화의 핵심이라, 여기서
      // 폴링이 끝날 때까지 버튼을 막아두면 의미가 없다).
      renderCollabWorkspace(taskId);
      const job = await pollExecutionJob(taskId, queued.job_id);
      if (job.status === "succeeded") {
        showMsg("global-msg", "AI 에이전트 실행이 완료됐습니다.", "ok");
      } else {
        showMsg("global-msg", "AI 에이전트 실행이 실패했습니다: " + job.error, "error");
      }
      if (isViewingWorkspace(taskId)) renderCollabWorkspace(taskId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };

  // 페이지를 새로고침했거나 다른 탭에 갔다 왔을 때도, 이미 돌고 있던
  // job이 있으면 자동으로 이어서 폴링해 끝나는 대로 갱신한다 — 버튼을
  // 눌러야만 완료를 알 수 있는 건 아니다.
  if (hasRunningJob) {
    pollExecutionJob(taskId, latestJob.id).then(job => {
      if (!isViewingWorkspace(taskId)) return;
      if (job.status === "failed") {
        showMsg("global-msg", "AI 에이전트 실행이 실패했습니다: " + job.error, "error");
      }
      renderCollabWorkspace(taskId);
    }).catch(() => {});
  }
}

// 백그라운드 폴링이 끝났을 때, 사용자가 그사이 다른 업무/탭으로
// 이동했다면 지금 화면을 덮어쓰지 않기 위한 가드(Phase 4).
function isViewingWorkspace(taskId) {
  const detail = document.getElementById("mywork-detail");
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
    box.innerHTML = comments.map(c => '<div class="muted">↳ ' + esc(c.content) + '</div>').join("");
  } catch (e) {
    // 참가자가 아니면 403 — 조용히 무시(타임라인 자체는 이미 조회 권한 확인됨).
  }
}

async function loadDeliverableComments(deliverableId) {
  await refreshComments(deliverableId);
  const form = document.querySelector('[data-comment-form="' + deliverableId + '"]');
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const input = document.getElementById("comment-input-" + deliverableId);
      const val = input.value.trim();
      if (!val) return;
      try {
        await api("POST", "/api/deliverables/" + deliverableId + "/comments", { content: val });
        input.value = "";
        await refreshComments(deliverableId);
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
    html += '<p class="muted">여기서 하는 일은 "제안된 역할" 칸을 채우는 것뿐입니다 — 실제 AI 분석이 아니라 입력한 텍스트를 줄바꿈 단위로 그대로 나누는 규칙 기반 처리라 비용이 없고 즉시 끝납니다. 이 제안은 참고용이고, 실제 인원 배정 단위인 "할 일(Task)"은 아래 "할 일로 나누기"에서 직접 만들어야 합니다. 뷰어(viewer) role은 조회만 가능하고 제출은 못 합니다.</p>' +
      '<form class="inline" id="brief-form">' +
      '<textarea id="brief-text" placeholder="한 줄에 역할 하나씩 입력하세요(줄바꿈 단위로 나뉩니다). 예:&#10;백엔드 개발자 1명&#10;UI/UX 디자이너 1명" rows="3" style="flex:1;min-width:240px"></textarea>' +
      '<button class="primary" type="submit">제출</button></form>';
  }
  html += "</section><section class=\\"card\\"><h2>업무 요청 목록</h2>" +
    '<p class="muted">"할 일로 나누기"로 만든 할 일(Task)이 "인력 매칭/배정"·"AI 에이전트 생성" 탭에서 쓸 단위입니다. "삭제"(관리자 전용)는 이 업무 요청과 그 아래 할 일·배정·이력까지 전부 되돌릴 수 없게 지웁니다 — 확정된(confirmed) 배정이 하나라도 있으면 막힙니다.</p>';
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
    '<input id="d-skills" placeholder="필요 스킬(콤마로 구분)" list="skill-datalist">' +
    '<input id="d-headcount" type="number" min="1" value="1" style="width:70px" title="인원 수">' +
    '<button class="primary" type="submit">할 일 추가</button></form>' +
    skillDatalist;
  detail.querySelectorAll("[data-fill-role]").forEach(btn => {
    btn.onclick = () => { document.getElementById("d-role").value = btn.dataset.fillRole; };
  });
  document.getElementById("decompose-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
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
      isAdmin ? '<button class="small danger" data-delete-task="' + t.id + '">삭제</button>' : "",
    ])) +
    '<p class="muted">할 일 ID는 "인력 매칭/배정", "AI 에이전트 생성" 탭에서 사용합니다. 결과물 상세는 "내 업무" 탭에서 워크스페이스를 열어 확인하세요.</p>';
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
}

/* ---------- 인력 매칭·배정 ---------- */

async function renderMatch(content) {
  content.innerHTML =
    '<section class="card"><h2>할 일에 맞는 인력 찾기</h2>' +
    '<p class="muted">"할 일 ID"는 "업무 요청" 탭에서 "할 일로 나누기"로 만든 것을 씁니다. 완전히 일치하는 역할·스킬을 가진 후보가 있으면 그것부터 보여주고, 없으면 가용성40%·적합도35%·비용25% 가중치로 점수를 매겨 상위 3명을 제시합니다(사람 후보가 아직 성과점수를 안 받았으면 적합도를 빼고 가용성60%·비용40%로 재계산). 후보가 아예 없으면 "AI 에이전트 생성" 탭으로 유도합니다. "배정 요청"을 누르면 즉시 확정되는 게 아니라 "결재" 탭에서 승인 절차를 거쳐야 확정(confirmed)됩니다.</p>' +
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

function renderMatchResult(taskId, result) {
  const box = document.getElementById("match-result");
  if (result.generate_new) {
    box.innerHTML = '<p class="msg error">매칭되는 후보가 없습니다 — "AI 에이전트 생성" 탭에서 새 Agent를 만들거나, "사람 인력 관리" 탭에서 이 역할의 사람을 등록해보세요.</p>';
    return;
  }
  box.innerHTML = table(
    ["순위", "타입", "ID", "역할", "가용", "비용", "점수", "점수 상세", "동작"],
    result.top.map((sc, i) => [
      i + 1, esc(label(sc.candidate.type)), sc.candidate.id, esc(sc.candidate.role),
      sc.candidate.is_available ? "가능" : "불가",
      sc.candidate.cost,
      sc.score.toFixed(3) + (sc.exact_match ? " (완전일치)" : ""),
      esc(JSON.stringify(sc.breakdown)),
      '<button class="small" data-assign-type="' + esc(sc.candidate.type) + '" data-assign-id="' + sc.candidate.id + '">배정 요청</button>',
    ])
  );
  box.querySelectorAll("[data-assign-id]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/tasks/" + taskId + "/assignments", {
          resource_type: btn.dataset.assignType, resource_id: Number(btn.dataset.assignId),
        });
        showMsg("global-msg", "배정 요청을 보냈습니다 — \\"결재\\" 탭에서 처리하세요.", "ok");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 결재 ---------- */

async function renderApprovals(content) {
  const isDecider = ["admin", "manager"].includes(ME.role);
  let html = "";
  if (isDecider) {
    html += '<p class="muted">배정 요청은 요청자의 상사 체인(조직도 "상사" 지정)을 따라 자동으로 단계가 만들어집니다 — 1단계가 처리돼야 2단계가 열립니다(다단계 결재). 상사가 지정 안 돼 있으면(예: 관리자 본인 요청) 특정 승인자 없이 1단계만 만들어져 admin/manager 누구나 처리할 수 있습니다. 본인이 요청한 건은 본인이 승인할 수 없습니다(자기 승인 방지) — "내 승인 대기함"에는 애초에 안 뜹니다.</p>';
    const inbox = await api("GET", qs("/api/approvals", { scope: "inbox" }));
    html += '<section class="card"><h2>내 승인 대기함</h2>' + approvalTable(inbox, true) + "</section>";
  }
  const mine = await api("GET", qs("/api/approvals", { scope: "mine" }));
  html += '<section class="card"><h2>내가 요청한 건</h2>' +
    '<p class="muted">"승인"하면 배정 상태가 confirmed(확정)로 바뀌어 조직도·비용·성과 리뷰에 실제로 반영됩니다. "반려"하면 그 배정 요청은 거기서 끝나고(재사용 불가), 다시 배정하려면 "인력 매칭/배정"에서 새로 요청해야 합니다.</p>' +
    approvalTable(mine, false) + "</section>";
  content.innerHTML = html;
  content.querySelectorAll("[data-decide]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/approvals/" + btn.dataset.decide + "/decide", { approve: btn.dataset.approve === "1" });
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
        ? '<div class="actions"><button class="small" data-decide="' + a.id + '" data-approve="1">승인</button>' +
          '<button class="small danger" data-decide="' + a.id + '" data-approve="0">반려</button></div>'
        : "");
    }
    return row;
  }));
}

/* ---------- AI 에이전트 생성 ---------- */

async function renderJobs(content) {
  content.innerHTML =
    '<section class="card"><h2>AI 에이전트 생성</h2>' +
    '<form class="inline" id="job-form">' +
    '<input id="job-brief-id" type="number" min="1" step="1" placeholder="업무 요청 ID" required style="width:110px">' +
    '<input id="job-task-ids" placeholder="할 일 ID(콤마로 구분)" required style="width:180px">' +
    '<select id="job-framework"><option value="stub">stub(무료 스텁)</option>' +
    '<option value="simple_llm">simple_llm(1회 LLM 호출)</option>' +
    '<option value="crewai">crewai(실제 CrewAI Agent)</option></select>' +
    '<select id="job-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    '<input id="job-model" placeholder="모델명(비우면 기본값)" style="width:200px">' +
    '<button class="primary" type="submit">생성 실행</button></form>' +
    '<p class="muted"><b>stub</b>(무료): LLM을 전혀 호출하지 않는 규칙 기반 스텁입니다 — 역할명·스킬로 자리표시자 산출물만 만들고 즉시 끝납니다. 조직도/배정 흐름만 비용 없이 채워보고 싶을 때 씁니다.<br>' +
    '<b>simple_llm</b>(비용 발생): 실제 LLM API를 딱 한 번만 호출해 "이 역할이 처음 맡을 산출물 하나"를 받아옵니다. CrewAI 같은 프레임워크 오버헤드 없이 가장 가벼운 실제 호출입니다.<br>' +
    '<b>crewai</b>(비용 발생, 보통 더 큼): 실제 CrewAI Agent+Task+Crew를 구성해 실행합니다 — 필요 스킬에 vectordb/graphdb 같은 게 있으면 Skill Pool이 매칭해주는 실제 도구를 생성 시점부터 붙여서 태어나므로, 이후 "Crew 실행" 탭에서 바로 협업시킬 Agent를 만들 때 씁니다.<br>' +
    '만든 Agent는 이 탭이 아니라 "AI 에이전트 관리" 탭에서 온보딩 승인(pending_onboarding → active) 후에야 실제 업무 배정·실행에 쓸 수 있습니다.</p>' +
    '<div id="job-result"></div></section>';
  document.getElementById("job-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const briefId = Number(document.getElementById("job-brief-id").value);
    if (!Number.isInteger(briefId) || briefId < 1) {
      showMsg("global-msg", "업무 요청 ID는 1 이상의 정수여야 합니다.", "error"); return;
    }
    const taskIds = document.getElementById("job-task-ids").value.split(",").map(s => Number(s.trim())).filter(n => Number.isInteger(n) && n > 0);
    if (!taskIds.length) {
      showMsg("global-msg", "할 일 ID는 1 이상의 정수로 최소 하나 입력해야 합니다.", "error"); return;
    }
    const framework = document.getElementById("job-framework").value;
    const provider = document.getElementById("job-provider").value || null;
    const model = document.getElementById("job-model").value || null;
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
  box.innerHTML = "<p>생성 결과 상태: <b>" + esc(label(result.status)) + "</b></p>" +
    table(["ID", "할 일 ID", "상태", "에러", "재시도", "동작"], result.items.map(it => [
      it.id, it.task_id, esc(label(it.status)), esc(it.error || ""), it.retry_count,
      it.status === "failed" ? '<button class="small" data-retry-job="' + result.job_id + '" data-retry-item="' + it.id + '">재시도</button>' : "",
    ]));
  box.querySelectorAll("[data-retry-item]").forEach(btn => {
    btn.onclick = async () => {
      try {
        const r = await api("POST", "/api/jobs/" + btn.dataset.retryJob + "/items/" + btn.dataset.retryItem + "/retry");
        renderJobResult(r);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 사람 인력 ---------- */

async function renderHumanPool(content) {
  const [people, skillDatalist] = await Promise.all([
    api("GET", "/api/human-pool"),
    skillDatalistHtml(),
  ]);
  const isAdmin = ME.role === "admin";
  content.innerHTML =
    '<section class="card"><h2>사람 인력 등록</h2>' +
    '<form class="inline" id="hp-form">' +
    '<input id="hp-name" placeholder="이름" required>' +
    '<input id="hp-role" placeholder="역할" required>' +
    '<input id="hp-skills" placeholder="스킬(콤마로 구분)" list="skill-datalist">' +
    skillDatalist +
    '<input id="hp-cost" type="number" step="0.01" placeholder="비용" value="0">' +
    '<button class="primary" type="submit">등록</button></form>' +
    '<p class="muted">"비용"은 인력 매칭 점수의 25%(또는 성과점수가 없으면 40%)를 차지합니다 — 낮을수록 유리합니다. 아직 성과 데이터가 없으니 초기엔 "가능" 상태여야 매칭 후보로 잡히고(가용성이 매칭 점수의 40~60%), 스킬은 할 일의 필요 스킬과 겹칠수록 유리합니다.</p>' +
    '</section><section class="card"><h2>사람 인력 목록</h2>' +
    '<p class="muted">"동의"는 성과 리뷰 사전 동의(NF9)입니다 — 동의 없이는 성과점수를 절대 입력·노출하지 않고, 동의를 철회하면 기존 점수도 즉시 지워집니다. "오프보딩"은 가용성을 offboarded로 바꿔 매칭 후보에서 빠지게 하는데, 이 화면에서 다시 되돌리는 버튼은 없습니다(사실상 편도) — 실수로 눌렀다면 관리자가 DB를 직접 고쳐야 합니다. "삭제"(관리자 전용)는 오프보딩과 별개로 레코드 자체를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 삭제가 막힙니다. "계정 연결"은 이 사람을 로그인 계정과 이어줍니다(사용자 관리 탭에서 만든 사용자의 ID) — 연결해야만 그 사람이 "내 업무" 탭에서 자기 업무를 볼 수 있습니다(연결 안 하면 구조적으로 절대 안 보입니다).</p>' +
    table(["ID", "이름", "역할", "스킬", "가용성", "비용", "동의", "성과점수", "연결된 계정 ID", "동작"], people.map(p => {
      const middleActions = p.availability === "offboarded" ? "" :
        '<button class="small" data-toggle-consent="' + p.id + '" data-consent="' + (p.performance_consent ? "0" : "1") + '">' +
          (p.performance_consent ? "동의 철회" : "동의") + "</button>" +
        '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="score-' + p.id + '">' +
        '<button class="small" data-set-score="' + p.id + '">점수입력</button></span>' +
        '<span class="pair"><input type="number" min="1" step="1" placeholder="사용자 ID" id="linkuser-' + p.id + '">' +
        '<button class="small" data-link-user="' + p.id + '">계정 연결</button></span>' +
        '<button class="small danger" data-offboard="' + p.id + '">오프보딩</button>';
      const deleteAction = isAdmin ? '<button class="small danger" data-delete-human="' + p.id + '">삭제</button>' : "";
      return [p.id, esc(p.name), esc(p.role), esc(p.skill_tags.join(", ")), esc(label(p.availability)), p.cost_estimate,
        p.performance_consent ? "동의함" : "미동의",
        p.performance_score === null ? "-" : p.performance_score,
        p.user_id === null ? "-" : p.user_id,
        '<div class="actions">' + middleActions + deleteAction + "</div>"];
    })) + "</section>";

  document.getElementById("hp-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/api/human-pool", {
        name: document.getElementById("hp-name").value,
        role: document.getElementById("hp-role").value,
        skill_tags: document.getElementById("hp-skills").value.split(",").map(s => s.trim()).filter(Boolean),
        cost_estimate: Number(document.getElementById("hp-cost").value) || 0,
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
      const input = document.getElementById("linkuser-" + btn.dataset.linkUser);
      const userId = Number(input.value);
      if (!Number.isInteger(userId) || userId < 1) {
        showMsg("global-msg", "사용자 ID는 1 이상의 정수여야 합니다.", "error"); return;
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
  content.innerHTML = '<section class="card"><h2>AI 에이전트 목록</h2>' +
    '<p class="muted">"생성방식" 열은 "AI 에이전트 생성" 탭에서 어떤 옵션(stub/simple_llm/crewai)으로 태어났는지를 보여줍니다 — crewai로 만든 Agent만 그 시점에 스킬 매칭 도구를 갖고 태어납니다. 신규 Agent는 전부 pending_onboarding 상태로 생기고, "온보딩 승인"을 눌러야 active가 돼 "인력 검색"·매칭 후보로 노출됩니다. "오프보딩"(active→deprecated)은 이 화면에서 되돌릴 버튼이 없습니다 — 다시 쓰려면 관리자가 API/DB를 직접 손대야 합니다. "모델 설정"은 이 에이전트가 기본으로 쓸 LLM provider/model을 저장만 합니다(비용 없음). "실행"은 온보딩된(활성) 에이전트에게 실제로 업무를 맡깁니다 — 설정해둔 모델(없으면 전역 기본값)로 실제 API를 호출하므로 비용이 발생합니다. 스킬에 vectordb/rag가 있으면 지식검색(RAG), graphdb가 있으면 관계분석 방식으로 자동 동작합니다(ADR 결정 11). "지식 색인"은 이 에이전트의 산출물을 임베딩해 지식검색 에이전트가 찾을 수 있게 저장합니다(비용 발생). "MCP 서버(JSON)"에 예: [{"url":"http://localhost:8000/mcp","transport":"streamable-http"}] 형태로 넣으면 "Crew 실행" 시 이 Agent에 실제 MCP 도구가 붙습니다(ADR 결정 10) — 빈 칸으로 두고 저장하면 기존 설정을 그대로 둡니다. "Tool Pool ID"에 "도구/스킬 Pool" 탭에서 등록한 도구 ID를 넣고 연결하면, 스킬 매칭과 무관하게 그 Agent에 도구가 하나 더 붙습니다(ADR 결정 12). "삭제"(관리자 전용)는 레코드를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 막힙니다.</p>' +
    table(["ID", "역할", "스킬", "비용", "성과점수(수동)", "자동평가(Gate/TCR)", "상태", "생성방식", "LLM", "최근 산출물", "동작"], agents.map(a => {
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
          '<input placeholder="모델명(비우면 기본값)" id="model-' + a.id + '" value="' + esc(a.llm_model || "") + '">' +
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
      return [a.id, esc(a.role_name), esc(a.skill_tags.join(", ")), a.cost_estimate,
        a.performance_score === null ? "-" : a.performance_score, autoEval, esc(label(a.status)), esc(a.framework), llmInfo, output,
        '<div class="actions">' + actions + "</div>"];
    })) + '<div id="execute-result"></div></section>';

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
      const model = document.getElementById("model-" + id).value || null;
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
          '<div class="msg ok"><b>실행 결과</b><br>' + esc(r.output).replace(/\\n/g, "<br>") + "</div>";
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
    '<p class="muted">MCP 서버나 native 도구(VectorDB/GraphDB/외부 자료 검색)를 이름으로 등록해 여러 Agent가 재사용합니다. native는 config에 {"native_id": "vectordb_search"} 또는 {"native_id": "graphdb_query"} 또는 {"native_id": "web_search"}(Wikipedia 검색, API 키 불필요·무료 — skill_tags에 "websearch"/"외부검색"을 넣으면 매칭됩니다)를 지원합니다. mcp_stdio/mcp_remote는 {"command":...,"args":[...]} 또는 {"url":...,"transport":...} 형태입니다 — 이 두 종류는 Agent가 실행될 때마다 실제로 서버(로컬 프로세스 또는 원격)에 접속을 시도하므로, 잘못된 command/url을 등록해두면 그 스킬을 가진 Agent 실행이 접속 단계에서 실패합니다. "활성" 표시를 꺼두면(비활성) 스킬이 매칭돼도 더 이상 자동으로 붙지 않습니다(등록은 유지). "삭제"(관리자 전용)는 이 도구를 참조하던 Agent 개별 연결도 함께 지웁니다.</p>' +
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
    '<p class="muted">모드 "tool"인 스킬은 기본 도구 ID가 가리키는 도구를 이 스킬(이름이 정확히 일치)을 가진 모든 Agent에게 실행할 때마다 자동으로 붙입니다(run_crew/crewai_agent_generator 실행마다 다시 계산 — 나중에 스킬 설정을 바꾸면 이미 만들어진 Agent에도 다음 실행부터 바로 반영됩니다). 모드 "none"은 도구 연결 없이 이름표만 붙이는 용도입니다 — 하니스 방법론/agent-evaluator 절차 스킬 17개가 여기 해당합니다(사람 판단이 필요한 절차라 자동 도구화가 안 됨). "recommend-fix-workflow" 하나만 예외로 실제 MCP 도구(정적 Gate 조치 지식 조회)가 붙어 있습니다. "삭제"(관리자 전용)는 이 스킬 레코드만 지웁니다 — 같은 이름의 skill_tags를 가진 Agent/사람은 그대로 남고, 다음 실행부터 자동 도구 연결만 안 됩니다.</p>' +
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

/* ---------- Crew 실행(ADR 결정 11 — Agent 협업) ---------- */

async function renderCrew(content) {
  const agents = await api("GET", "/api/agent-pool");
  const active = agents.filter(a => a.status === "active");
  content.innerHTML =
    '<section class="card"><h2>Crew 실행 — 여러 Agent를 묶어 실제로 협업시키기</h2>' +
    '<p class="muted">한 줄에 "에이전트ID: 이 에이전트가 할 일"을 입력하세요. <b>sequential</b>이면 앞줄 에이전트의 산출물이 다음 에이전트에게 자동으로 전달됩니다(순서대로 릴레이). <b>hierarchical</b>은 "매니저 Agent ID"를 지정하면 그 매니저가 작업을 조율·위임만 하고 직접 산출물을 내지는 않습니다(비워두면 CrewAI가 자동으로 매니저 LLM을 하나 배정) — 아래 결과의 "Agent별 산출물"에서 매니저 행은 항상 "(없음)"으로 나옵니다. 모든 Agent는 위임(allow_delegation)이 켜져 있어 필요하면 서로에게 실제로 질문·재위임할 수 있고, 그 내역이 "실제 위임/협업 발생 내역"에 기록됩니다. 온보딩(active) 안 된 Agent는 목록에 안 뜨고 지정할 수도 없습니다. 실제 LLM API를 호출하므로 비용이 발생합니다(참여 Agent 수만큼 커질 수 있음).</p>' +
    (active.length
      ? table(["ID", "역할", "스킬"], active.map(a => [a.id, esc(a.role_name), esc(a.skill_tags.join(", "))]))
      : '<p class="muted">온보딩된(활성) 에이전트가 없습니다 — 먼저 "AI 에이전트 생성"·"AI 에이전트 관리" 탭에서 준비하세요.</p>') +
    '<form id="crew-form">' +
    '<textarea id="crew-lines" rows="5" style="width:100%;box-sizing:border-box" placeholder="1: 요구사항 정의서 초안을 작성해줘&#10;2: 그 요구사항을 바탕으로 화면 설계를 해줘"></textarea>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    '<select id="crew-process"><option value="sequential">sequential(순서대로 전달)</option>' +
    '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
    '<input id="crew-manager" type="number" min="1" step="1" placeholder="매니저 Agent ID(hierarchical 전용, 선택)">' +
    '<select id="crew-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    '<input id="crew-model" placeholder="모델명(비우면 기본값)">' +
    '<button class="primary" type="submit">Crew 실행(비용 발생)</button>' +
    "</div></form>" +
    '<div id="crew-result"></div></section>';

  document.getElementById("crew-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showMsg("global-msg", "", "");  // 이전 시도의 에러 배너가 남아 새 결과와 헷갈리지 않도록 지운다
    const lines = document.getElementById("crew-lines").value.split("\\n").map(l => l.trim()).filter(Boolean);
    const agentIds = [];
    const taskDescriptions = [];
    for (const line of lines) {
      const idx = line.indexOf(":");
      if (idx < 0) { showMsg("global-msg", '형식이 잘못됐습니다: "' + line + '" (예: "1: 할 일")', "error"); return; }
      const id = Number(line.slice(0, idx).trim());
      if (!Number.isInteger(id) || id < 1) { showMsg("global-msg", "에이전트 ID는 1 이상의 정수여야 합니다: " + line, "error"); return; }
      agentIds.push(id);
      taskDescriptions.push(line.slice(idx + 1).trim());
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 줄은 입력해야 합니다.", "error"); return; }
    const managerVal = document.getElementById("crew-manager").value;
    if (managerVal && (!Number.isInteger(Number(managerVal)) || Number(managerVal) < 1)) {
      showMsg("global-msg", "매니저 Agent ID는 1 이상의 정수여야 합니다.", "error"); return;
    }
    if (!window.confirm(agentIds.length + "개 Agent로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?")) return;

    const submitBtn = ev.submitter || document.querySelector("#crew-form button[type=submit]");
    try {
      const result = await withBusy(submitBtn, "실행 중…(시간이 걸릴 수 있습니다)", () => api("POST", "/api/crew/run", {
        agent_ids: agentIds,
        task_descriptions: taskDescriptions,
        process: document.getElementById("crew-process").value,
        manager_agent_id: managerVal ? Number(managerVal) : null,
        provider: document.getElementById("crew-provider").value || null,
        model: document.getElementById("crew-model").value || null,
      }));
      renderCrewResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function renderCrewResult(result) {
  const box = document.getElementById("crew-result");
  let html = '<h3>최종 산출물</h3><div class="msg ok">' + esc(result.final_output).replace(/\\n/g, "<br>") + "</div>";
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
    ? table(["위임한 Agent 역할", "사용한 도구"], result.delegations.map(d => [esc(d.from_agent_role || "-"), esc(d.tool)]))
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
    '<p class="muted">이 화면은 매칭 알고리즘 없이 필터로 후보를 훑어보는 용도입니다(실제 배정 점수·순위를 보려면 "인력 매칭/배정" 탭을 쓰세요). AI 에이전트는 온보딩 승인(active) 안 된 것(pending_onboarding/deprecated)은 여기 안 뜹니다 — "AI 에이전트 관리" 탭에서 상태 무관 전체 목록을 볼 수 있습니다. 오프보딩된 사람도 마찬가지로 빠집니다. "부서" 필터는 없습니다 — 이 시스템엔 부서 데이터가 없고, "역할"이 사실상 같은 구실을 합니다.</p>' +
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
    '<p class="muted">여기 나오는 건 결재까지 끝나 상태가 confirmed(확정)된 배정뿐입니다 — "인력 매칭/배정"에서 방금 요청했거나 "결재" 탭에서 아직 대기 중인 건은 승인되기 전까지 이 화면에 나타나지 않습니다. 묶는 기준(관리자)이 사람과 AI 에이전트가 다릅니다: 사람 배정은 그 사람의 "상사"(사용자 관리에서 지정)로, AI 에이전트 배정은 Agent에게 상사가 없으므로 그 배정을 요청한 사람으로 귀속시킵니다. 상사가 없으면 "(지정된 관리자 없음)" 묶음에 들어갑니다.</p>';
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
    '<p class="muted">사람의 "성과점수"는 사전 동의(NF9)가 있어야만 값이 채워집니다 — "사람 인력 관리" 탭에서 동의를 받은 사람만 점수를 입력할 수 있고, 동의를 철회하면 그 순간 점수가 지워집니다. AI 에이전트는 동의 개념이 없어 항상 입력·조회 가능합니다. 이 화면의 점수는 둘 다 관리자가 직접 입력한 수동값입니다 — Agent의 실제 Gate A-G 자동 채점 결과는 이 화면이 아니라 "AI 에이전트 관리" 탭의 "자동평가(Gate/TCR)" 열에서 따로 봅니다(둘을 아직 한 화면에 병기하진 않습니다). "확정 배정 수"만 실제 데이터(confirmed 배정 건수)입니다.</p>' +
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

  content.innerHTML =
    '<section class="card"><h2>매니저 대시보드</h2>' +
    '<p class="muted">팀 전체 Agent Gate 점수 평균·대기 승인 건수·비용 변경 이력을 한 화면에 모았습니다. Gate 점수는 실제로 측정된 산출물이 있을 때만 값이 나옵니다(not_measured는 평균에서 제외). 비용 추이는 "AI 에이전트 관리"·"사람 인력 관리" 탭에서 단가를 바꿀 때마다 남는 감사로그 이력이라, 지금까지 아무도 단가를 바꾼 적이 없으면 비어 있습니다.</p>' +
    '<div class="dashboard-grid">' +
    '<div class="card"><h3>팀 Agent 평균 Gate 점수(표본 ' + gs.sample_count + '건)</h3>' + gateRow +
    '<div class="stat-row"><span>평균 TCR</span><span>' + (gs.avg_tcr === null ? "-" : gs.avg_tcr.toFixed(1) + "%") + '</span></div></div>' +
    '<div class="card"><h3>대기 승인 건수</h3><p style="font-size:2em;margin:0">' + d.pending_approval_count + '</p></div>' +
    '<div class="card"><h3>현재 비용 스냅샷</h3><p style="font-size:2em;margin:0">' + d.cost_snapshot.total_cost + '</p></div>' +
    '</div>' +
    '<h3>비용 변경 이력(최근 ' + d.cost_trend.length + '건)</h3>' +
    table(["시각", "대상 종류", "대상 ID", "변경된 단가"], trendRows) +
    '</section>';
}

/* ---------- 비용 ---------- */

async function renderCost(content) {
  const report = await api("GET", "/api/cost-report");
  content.innerHTML = '<section class="card"><h2>비용 리포트(총액: ' + report.total_cost + ')</h2>' +
    '<p class="muted">이 "합계"는 "단가(cost_estimate, 사람/에이전트 관리 탭에서 수동 입력)" × "확정 배정 수"입니다 — 실제로 LLM API에 청구된 금액이 아닙니다(simple_llm/crewai 생성이나 Crew 실행·에이전트 실행 때 실제로 나간 API 비용은 이 리포트에 잡히지 않습니다). 즉 "일 시켰을 때 실제 API 비용"이 아니라 "이 인력을 계속 쓰면 예상되는 인건비/도입비" 관점의 숫자입니다.</p>' +
    table(["타입", "ID", "이름", "역할", "단가", "확정 배정 수", "합계"], report.resources.map(r => [
      esc(label(r.type)), r.id, esc(r.name || "-"), esc(r.role), r.cost_estimate, r.confirmed_assignments, r.total_cost,
    ])) + "</section>";
}

/* ---------- 공정성 ---------- */

async function renderFairness(content) {
  const r = await api("GET", "/api/fairness-report");
  content.innerHTML = '<section class="card"><h2>배정 공정성 지표</h2>' +
    '<p class="muted">사람과 AI 에이전트가 같은 매칭 기준(가용성/적합도/비용 가중치)으로 배정되는데도 반려율에 체계적 차이가 나는지 보는 지표입니다 — "확정"·"반려"는 결재까지 끝난(confirmed/rejected) 배정만 세고, 아직 대기 중인 배정은 "결정됨"에 안 들어갑니다. "반려율 격차"가 크면 어느 한쪽이 불리하게 반려되는 패턴이 있을 수 있다는 신호입니다(단, 표본이 작으면 우연일 수 있음).</p>' +
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
    '<p class="muted">이 로그는 DB 트리거로 수정·삭제 자체가 막혀 있는 append-only 기록입니다(NF10) — 다른 탭의 "삭제" 버튼으로 브리프/할 일/사람/에이전트/도구/스킬을 실제로 지워도, "무엇을 언제 누가 지웠는지"는 여기 그대로 남습니다. Admin·매니저만 조회할 수 있습니다.</p>' +
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
    '<p class="muted">4가지 권한(role)은 화면 접근 범위를 결정합니다 — 관리자: 전 기능, 매니저: "운영 관리" 대부분에 접근 가능하지만 사용자 생성/상사 지정·각종 삭제·Agent 온보딩 승인/오프보딩처럼 조직 구조에 직접 영향을 주는 기능은 관리자 전용, 팀원: 업무 요청 제출·결재 요청 등 "내 업무" 화면, 뷰어: 조회만. 생성 직후엔 상사가 지정 안 돼 있어(아래에서 지정), 이 사용자가 배정을 요청하면 결재가 단일 단계(승인자 미지정)로 처리됩니다.</p>' +
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
    '<p class="muted">비밀번호는 bcrypt 해시로 저장돼 "조회"는 할 수 없습니다 — "초기화"로 새 비밀번호를 지정한 뒤 별도 채널로 전달하세요. "계정 상태"는 하드 삭제 대신 비활성화 방식입니다(감사 이력·배정 이력이 이 계정을 참조하고 있어 완전히 지우면 그 이력이 깨집니다) — 비활성화된 계정은 즉시 로그인이 막히고 기존 세션도 끊깁니다. 상사를 지정하면 그 사람이 배정을 요청할 때 결재가 상사 체인을 따라 다단계로 자동 라우팅됩니다("결재" 탭 참고). 본인을 직접 상사로 지정하는 것만 막혀 있고, A→B→A 같은 두 단계 이상의 순환은 막지 않습니다.</p>' +
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
