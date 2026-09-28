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
  brief: "업무 요청", assignment: "배정", human_pool: "사람 인력", agent_pool: "AI 에이전트",
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

let ME = null;

/* 코드 재점검(2026-09-26) 사용자 요청 — 관리자 기능과 일반 업무 기능을
   화면상에서도 명확히 구분한다(ADR.md 결정 9). group: "user"는 담당자가
   자기 업무를 처리하는 화면, group: "admin"은 조직/리소스를 운영·관리하는
   화면이다 — roles 배열(백엔드 RBAC과 동일)로 실제 접근 제어는 그대로 하되,
   내비게이션을 둘로 나눠 보여준다. adminOnly: true는 그중에서도 ADMIN
   role만 되는 항목(매니저도 접근 가능한 관리자 도구와 구분 표시). */
const TABS = [
  { id: "mywork", label: "내 업무", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderMyWork },
  { id: "briefs", label: "업무 요청", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBriefs },
  { id: "approvals", label: "결재", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderApprovals },
  { id: "search", label: "인력 검색", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderSearch },
  { id: "orgchart", label: "조직도", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderOrgChart },
  { id: "performance", label: "성과 리뷰", group: "user", roles: ["admin", "manager", "member"], render: renderPerformance },
  { id: "match", label: "인력 매칭/배정", group: "admin", roles: ["admin", "manager"], render: renderMatch },
  { id: "jobs", label: "AI 에이전트 생성", group: "admin", roles: ["admin", "manager"], render: renderJobs },
  { id: "humanpool", label: "사람 인력 관리", group: "admin", roles: ["admin", "manager"], render: renderHumanPool },
  { id: "agentpool", label: "AI 에이전트 관리", group: "admin", roles: ["admin", "manager"], render: renderAgentPool },
  { id: "poolregistry", label: "도구/스킬 Pool", group: "admin", roles: ["admin", "manager"], render: renderPoolRegistry },
  { id: "crew", label: "Crew 실행", group: "admin", roles: ["admin", "manager"], render: renderCrew },
  { id: "cost", label: "비용", group: "admin", roles: ["admin", "manager"], render: renderCost },
  { id: "fairness", label: "공정성 지표", group: "admin", roles: ["admin", "manager"], render: renderFairness },
  { id: "audit", label: "활동 기록", group: "admin", roles: ["admin", "manager"], render: renderAudit },
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
  addGroup("관리자 도구", adminTabs, "admin-group");
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

/* ---------- 내 업무(협업 워크스페이스, 사용자 요청 2026-09-28) ---------- */

async function renderMyWork(content) {
  const rows = await api("GET", "/api/my-work");
  content.innerHTML =
    '<section class="card"><h2>내 업무</h2>' +
    '<p class="muted">확정된 배정 중 내가 직접 할 일(사람 배정 당사자)이거나 내가 AI에게 맡긴 일(Agent 배정 요청자)만 보입니다. "열기"를 누르면 그 업무의 협업 워크스페이스(결과물 타임라인)가 아래에 펼쳐집니다.</p>' +
    table(["할 일 ID", "역할", "유형", "관계", "결과물 수", "최근 결과물", "동작"], rows.map(r => [
      r.task_id, esc(r.role_name), esc(label(r.resource_type)),
      r.my_relationship === "assignee" ? "내가 할 일" : "내가 맡긴 일",
      r.deliverable_count,
      r.latest_deliverable_at ? esc(r.latest_deliverable_at) : "-",
      '<button class="small" data-open-workspace="' + r.task_id + '">열기</button>',
    ])) +
    '<div id="mywork-detail"></div></section>';

  content.querySelectorAll("[data-open-workspace]").forEach(btn => {
    btn.onclick = () => renderCollabWorkspace(Number(btn.dataset.openWorkspace));
  });
}

async function renderCollabWorkspace(taskId) {
  const detail = document.getElementById("mywork-detail");
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  let items;
  try {
    items = await api("GET", "/api/tasks/" + taskId + "/deliverables");
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  const timeline = items.length
    ? items.map(d =>
        '<div class="deliverable-card">' +
        '<div class="muted">' + esc(label(d.author_type)) + " · " + esc(label(d.status)) + " · " + esc(d.created_at) + '</div>' +
        '<div>' + esc(d.content).replace(/\\n/g, "<br>") + '</div>' +
        '<div id="comments-' + d.id + '"></div>' +
        '<form class="inline" data-comment-form="' + d.id + '">' +
        '<input placeholder="댓글" id="comment-input-' + d.id + '" style="flex:1">' +
        '<button class="small" type="submit">댓글</button></form>' +
        '</div>'
      ).join("")
    : '<p class="muted">아직 결과물이 없습니다.</p>';

  const modelFields = isSupervisor
    ? '<select id="ws-provider"><option value="">provider 자동 판별</option>' +
      '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
      '<option value="ollama">Ollama(로컬)</option></select>' +
      '<input id="ws-model" placeholder="모델명(비우면 기본값)">'
    : '<span class="muted">provider/model은 매니저·관리자만 선택할 수 있습니다(기본값 사용).</span>';

  detail.innerHTML =
    '<h3>협업 워크스페이스 — 할 일 #' + esc(taskId) + '</h3>' +
    '<div id="workspace-timeline">' + timeline + '</div>' +
    '<form class="inline" id="ws-submit-form">' +
    '<textarea id="ws-content" rows="3" placeholder="결과물 내용을 입력하세요" style="flex:1;min-width:240px"></textarea>' +
    modelFields +
    '<button class="small" type="button" id="ws-assist-btn">LLM으로 도와줘(비용 발생)</button>' +
    '<button class="primary" type="submit">결과물 제출</button>' +
    '</form>' +
    '<button class="small" id="ws-execute-agent-btn">이 업무의 AI 에이전트 실행(비용 발생)</button>' +
    '<div id="ws-result"></div>';

  items.forEach(d => loadDeliverableComments(d.id));

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
      await withBusy(executeBtn, "실행 중…", () => api("POST", "/api/tasks/" + taskId + "/execute-agent", payload));
      showMsg("global-msg", "AI 에이전트 실행이 완료됐습니다.", "ok");
      renderCollabWorkspace(taskId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };
}

async function loadDeliverableComments(deliverableId) {
  const box = document.getElementById("comments-" + deliverableId);
  if (!box) return;
  try {
    const comments = await api("GET", "/api/deliverables/" + deliverableId + "/comments");
    box.innerHTML = comments.map(c => '<div class="muted">↳ ' + esc(c.content) + '</div>').join("");
  } catch (e) {
    // 참가자가 아니면 403 — 조용히 무시(타임라인 자체는 이미 조회 권한 확인됨).
  }
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
        loadDeliverableComments(deliverableId);
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
      if (!window.confirm("이 업무 요청과 그 아래 할 일·배정을 전부 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
      try {
        await api("DELETE", "/api/briefs/" + btn.dataset.deleteBrief);
        showMsg("global-msg", "업무 요청을 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

function renderDecomposeForm(briefId, stubRoles) {
  const detail = document.getElementById("brief-detail");
  const suggestions = (stubRoles || []).length
    ? '<p class="muted">제안된 역할(클릭하면 아래 입력창에 채워집니다 — 브리프를 줄바꿈으로 나눈 것뿐이라 다듬어서 쓰세요): ' +
      stubRoles.map(r => '<button type="button" class="small" data-fill-role="' + esc(r) + '">' + esc(r) + '</button>').join(" ") +
      "</p>"
    : "";
  detail.innerHTML =
    '<h3>업무 요청 #' + esc(briefId) + ' — 할 일 나누기</h3>' +
    suggestions +
    '<form class="inline" id="decompose-form">' +
    '<input id="d-role" placeholder="필요한 역할(예: 백엔드 개발자)" required>' +
    '<input id="d-skills" placeholder="필요 스킬(콤마로 구분)">' +
    '<input id="d-headcount" type="number" min="1" value="1" style="width:70px" title="인원 수">' +
    '<button class="primary" type="submit">할 일 추가</button></form>';
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
    table(["할 일 ID", "역할", "필요 스킬", "인원", "동작"], tasks.map(t => [
      t.id, esc(t.role_name), esc(t.required_skills.join(", ")), t.headcount,
      isAdmin ? '<button class="small danger" data-delete-task="' + t.id + '">삭제</button>' : "",
    ])) +
    '<p class="muted">할 일 ID는 "인력 매칭/배정", "AI 에이전트 생성" 탭에서 사용합니다.</p>';
  detail.querySelectorAll("[data-delete-task]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 할 일과 그 배정을 전부 지웁니다(되돌릴 수 없음). 계속할까요?")) return;
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
  const people = await api("GET", "/api/human-pool");
  const isAdmin = ME.role === "admin";
  content.innerHTML =
    '<section class="card"><h2>사람 인력 등록</h2>' +
    '<form class="inline" id="hp-form">' +
    '<input id="hp-name" placeholder="이름" required>' +
    '<input id="hp-role" placeholder="역할" required>' +
    '<input id="hp-skills" placeholder="스킬(콤마로 구분)">' +
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
    table(["ID", "역할", "스킬", "비용", "성과점수", "상태", "생성방식", "LLM", "최근 산출물", "동작"], agents.map(a => {
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
      return [a.id, esc(a.role_name), esc(a.skill_tags.join(", ")), a.cost_estimate,
        a.performance_score === null ? "-" : a.performance_score, esc(label(a.status)), esc(a.framework), llmInfo, output,
        '<div class="actions">' + actions + "</div>"];
    })) + '<div id="execute-result"></div></section>';

  content.querySelectorAll("[data-onboard]").forEach(btn => {
    btn.onclick = async () => { try { await api("POST", "/api/agent-pool/" + btn.dataset.onboard + "/onboard"); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); } };
  });
  content.querySelectorAll("[data-deprecate]").forEach(btn => {
    btn.onclick = async () => { try { await api("POST", "/api/agent-pool/" + btn.dataset.deprecate + "/deprecate"); refresh(); } catch (e) { showMsg("global-msg", e.message, "error"); } };
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
    '<p class="muted">MCP 서버나 native 도구(VectorDB/GraphDB)를 이름으로 등록해 여러 Agent가 재사용합니다. native는 config에 {"native_id": "vectordb_search"} 또는 {"native_id": "graphdb_query"}만 지원합니다. mcp_stdio/mcp_remote는 {"command":...,"args":[...]} 또는 {"url":...,"transport":...} 형태입니다 — 이 두 종류는 Agent가 실행될 때마다 실제로 서버(로컬 프로세스 또는 원격)에 접속을 시도하므로, 잘못된 command/url을 등록해두면 그 스킬을 가진 Agent 실행이 접속 단계에서 실패합니다. "활성" 표시를 꺼두면(비활성) 스킬이 매칭돼도 더 이상 자동으로 붙지 않습니다(등록은 유지). "삭제"(관리자 전용)는 이 도구를 참조하던 Agent 개별 연결도 함께 지웁니다.</p>' +
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
  content.innerHTML =
    '<section class="card"><h2>사람·AI 에이전트 통합 검색</h2>' +
    '<p class="muted">이 화면은 매칭 알고리즘 없이 필터로 후보를 훑어보는 용도입니다(실제 배정 점수·순위를 보려면 "인력 매칭/배정" 탭을 쓰세요). AI 에이전트는 온보딩 승인(active) 안 된 것(pending_onboarding/deprecated)은 여기 안 뜹니다 — "AI 에이전트 관리" 탭에서 상태 무관 전체 목록을 볼 수 있습니다. 오프보딩된 사람도 마찬가지로 빠집니다.</p>' +
    '<form class="inline" id="search-form">' +
    '<input id="s-role" placeholder="역할"><input id="s-skill" placeholder="스킬">' +
    '<label><input id="s-avail" type="checkbox"> 가용만</label>' +
    '<button class="primary" type="submit">검색</button></form>' +
    '<div id="search-result"></div></section>';
  document.getElementById("search-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const results = await api("GET", qs("/api/pool/search", {
        role: document.getElementById("s-role").value,
        skill: document.getElementById("s-skill").value,
        available_only: document.getElementById("s-avail").checked ? "true" : "",
      }));
      document.getElementById("search-result").innerHTML = table(
        ["타입", "ID", "이름", "역할", "가용", "비용", "스킬", "성과점수"],
        results.map(c => [esc(label(c.type)), c.id, esc(c.name || "-"), esc(c.role), c.is_available ? "가능" : "불가", c.cost, esc(c.skill_tags.join(", ")), c.performance_score === null ? "-" : c.performance_score])
      );
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
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
    '<p class="muted">사람의 "성과점수"는 사전 동의(NF9)가 있어야만 값이 채워집니다 — "사람 인력 관리" 탭에서 동의를 받은 사람만 점수를 입력할 수 있고, 동의를 철회하면 그 순간 점수가 지워집니다. AI 에이전트는 동의 개념이 없어 항상 입력·조회 가능합니다. 두 타입 모두 점수는 아직 자동 채점(Gate A–G) 연동 전이라 관리자가 직접 입력한 값이고, "확정 배정 수"만 실제 데이터(confirmed 배정 건수)입니다.</p>' +
    table(["타입", "ID", "이름", "역할", "동의", "성과점수", "확정 배정 수"], rows.map(r => [
      esc(label(r.type)), r.id, esc(r.name || "-"), esc(r.role),
      r.performance_consent === null ? "-" : (r.performance_consent ? "동의함" : "미동의"),
      r.performance_score === null ? "-" : r.performance_score,
      r.confirmed_assignments,
    ])) + "</section>";
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
    '<p class="muted">4가지 권한(role)은 화면 접근 범위를 결정합니다 — 관리자: 전 기능, 매니저: "관리자 도구" 대부분에 접근 가능하지만 사용자 생성/상사 지정·각종 삭제·Agent 온보딩 승인/오프보딩처럼 조직 구조에 직접 영향을 주는 기능은 관리자 전용, 팀원: 업무 요청 제출·결재 요청 등 "내 업무" 화면, 뷰어: 조회만. 생성 직후엔 상사가 지정 안 돼 있어(아래에서 지정), 이 사용자가 배정을 요청하면 결재가 단일 단계(승인자 미지정)로 처리됩니다.</p>' +
    '<form class="inline" id="user-form">' +
    '<input id="u-username" placeholder="아이디" required>' +
    '<input id="u-password" type="password" placeholder="비밀번호" required>' +
    '<select id="u-role"><option value="admin">관리자</option><option value="manager">매니저</option>' +
    '<option value="member" selected>팀원</option><option value="viewer">뷰어</option></select>' +
    '<button class="primary" type="submit">생성</button></form>' +
    "</section>" +
    '<section class="card"><h2>사용자 목록 / 상사 지정</h2>' +
    '<p class="muted">상사를 지정하면 그 사람이 배정을 요청할 때 결재가 상사 체인을 따라 다단계로 자동 라우팅됩니다("결재" 탭 참고). 본인을 직접 상사로 지정하는 것만 막혀 있고, A→B→A 같은 두 단계 이상의 순환은 막지 않습니다(다단계 체인 계산 시 조용히 그 지점에서 끊고 진행하므로 무한루프는 안 나지만, 의도한 결재선과 다르게 짧게 끝날 수 있습니다).</p>' +
    table(["ID", "아이디", "권한", "상사", "동작"], users.map(u => [
      u.id, esc(u.username), esc(u.role), u.manager_id === null ? "-" : u.manager_id,
      '<div class="actions"><span class="pair"><select id="mgr-' + u.id + '"><option value="">(없음)</option>' +
        users.filter(m => m.id !== u.id).map(m => '<option value="' + m.id + '"' + (m.id === u.manager_id ? " selected" : "") + ">" + esc(m.username) + "</option>").join("") +
      '</select><button class="small" data-set-manager="' + u.id + '">저장</button></span></div>',
    ])) + "</section>";

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
}

boot();
</script>
</body>
</html>
"""
