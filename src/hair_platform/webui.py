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
  :root { color-scheme: light dark; }
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
  td.col-actions, th.col-actions { width: 160px; }
  td.col-output, th.col-output { width: 280px; }
  .actions { display: flex; flex-direction: column; align-items: stretch; gap: 0.3rem; }
  .actions > button, .actions > select { width: 100%; }
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
  { id: "briefs", label: "업무 요청", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderBriefs },
  { id: "approvals", label: "결재", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderApprovals },
  { id: "search", label: "인력 검색", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderSearch },
  { id: "orgchart", label: "조직도", group: "user", roles: ["admin", "manager", "member", "viewer"], render: renderOrgChart },
  { id: "performance", label: "성과 리뷰", group: "user", roles: ["admin", "manager", "member"], render: renderPerformance },
  { id: "match", label: "인력 매칭/배정", group: "admin", roles: ["admin", "manager"], render: renderMatch },
  { id: "jobs", label: "AI 에이전트 생성", group: "admin", roles: ["admin", "manager"], render: renderJobs },
  { id: "humanpool", label: "사람 인력 관리", group: "admin", roles: ["admin", "manager"], render: renderHumanPool },
  { id: "agentpool", label: "AI 에이전트 관리", group: "admin", roles: ["admin", "manager"], render: renderAgentPool },
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

/* ---------- 업무 요청/할 일 ---------- */

async function renderBriefs(content) {
  const canWrite = ["admin", "manager", "member"].includes(ME.role);
  const briefs = await api("GET", "/api/briefs");
  let html = "<section class=\\"card\\"><h2>업무 요청 등록</h2>";
  if (canWrite) {
    html += '<form class="inline" id="brief-form">' +
      '<textarea id="brief-text" placeholder="예: 백엔드 개발자 1명" rows="2" style="flex:1;min-width:240px"></textarea>' +
      '<button class="primary" type="submit">제출</button></form>';
  }
  html += "</section><section class=\\"card\\"><h2>업무 요청 목록</h2>";
  html += table(["ID", "내용", "상태", "제안된 역할", "동작"], briefs.map(b => [
    b.id,
    esc(b.raw_text),
    esc(label(b.status)),
    esc(b.stub_roles.join(", ")),
    canWrite ? '<div class="actions"><button class="small" data-decompose="' + b.id + '">할 일로 나누기</button>' +
      '<button class="small" data-viewtasks="' + b.id + '">할 일 보기</button></div>' : "",
  ]));
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
    btn.onclick = () => renderDecomposeForm(btn.dataset.decompose);
  });
  content.querySelectorAll("[data-viewtasks]").forEach(btn => {
    btn.onclick = () => renderTaskList(btn.dataset.viewtasks);
  });
}

function renderDecomposeForm(briefId) {
  const detail = document.getElementById("brief-detail");
  detail.innerHTML =
    '<h3>업무 요청 #' + esc(briefId) + ' — 할 일 나누기</h3>' +
    '<form class="inline" id="decompose-form">' +
    '<input id="d-role" placeholder="필요한 역할(예: 백엔드 개발자)" required>' +
    '<input id="d-skills" placeholder="필요 스킬(콤마로 구분)">' +
    '<input id="d-headcount" type="number" min="1" value="1" style="width:70px" title="인원 수">' +
    '<button class="primary" type="submit">할 일 추가</button></form>';
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
  const tasks = await api("GET", "/api/briefs/" + briefId + "/tasks");
  detail.innerHTML = "<h3>업무 요청 #" + esc(briefId) + " — 할 일 목록</h3>" +
    table(["할 일 ID", "역할", "필요 스킬", "인원"], tasks.map(t => [
      t.id, esc(t.role_name), esc(t.required_skills.join(", ")), t.headcount,
    ])) +
    '<p class="muted">할 일 ID는 "인력 매칭/배정", "AI 에이전트 생성" 탭에서 사용합니다.</p>';
}

/* ---------- 인력 매칭·배정 ---------- */

async function renderMatch(content) {
  content.innerHTML =
    '<section class="card"><h2>할 일에 맞는 인력 찾기</h2>' +
    '<form class="inline" id="match-form">' +
    '<input id="match-task-id" type="number" placeholder="할 일 ID" required style="width:100px">' +
    '<button class="primary" type="submit">찾기</button></form>' +
    '<div id="match-result"></div></section>';
  document.getElementById("match-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const taskId = document.getElementById("match-task-id").value;
    try {
      const result = await api("POST", "/api/tasks/" + taskId + "/match");
      renderMatchResult(taskId, result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function renderMatchResult(taskId, result) {
  const box = document.getElementById("match-result");
  if (result.generate_new) {
    box.innerHTML = '<p class="msg error">매칭되는 후보가 없습니다 — "AI 에이전트 생성" 탭에서 새로 만들어 보세요.</p>';
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
    const inbox = await api("GET", qs("/api/approvals", { scope: "inbox" }));
    html += '<section class="card"><h2>내 승인 대기함</h2>' + approvalTable(inbox, true) + "</section>";
  }
  const mine = await api("GET", qs("/api/approvals", { scope: "mine" }));
  html += '<section class="card"><h2>내가 요청한 건</h2>' + approvalTable(mine, false) + "</section>";
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
    '<input id="job-brief-id" type="number" placeholder="업무 요청 ID" required style="width:110px">' +
    '<input id="job-task-ids" placeholder="할 일 ID(콤마로 구분)" required style="width:180px">' +
    '<select id="job-framework"><option value="stub">stub(무료 스텁)</option>' +
    '<option value="simple_llm">simple_llm(1회 LLM 호출)</option>' +
    '<option value="crewai">crewai(실제 CrewAI Agent)</option></select>' +
    '<select id="job-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    '<input id="job-model" placeholder="모델명(비우면 기본값)" style="width:200px">' +
    '<button class="primary" type="submit">생성 실행</button></form>' +
    '<p class="muted">stub은 비용이 없습니다. simple_llm/crewai는 실제 API를 호출해 비용이 발생합니다.</p>' +
    '<div id="job-result"></div></section>';
  document.getElementById("job-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const briefId = document.getElementById("job-brief-id").value;
    const taskIds = document.getElementById("job-task-ids").value.split(",").map(s => Number(s.trim())).filter(n => !isNaN(n));
    const framework = document.getElementById("job-framework").value;
    const provider = document.getElementById("job-provider").value || null;
    const model = document.getElementById("job-model").value || null;
    try {
      const result = await api("POST", "/api/briefs/" + briefId + "/generate", {
        task_ids: taskIds, use_llm: framework !== "stub", framework, provider, model,
      });
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
  content.innerHTML =
    '<section class="card"><h2>사람 인력 등록</h2>' +
    '<form class="inline" id="hp-form">' +
    '<input id="hp-name" placeholder="이름" required>' +
    '<input id="hp-role" placeholder="역할" required>' +
    '<input id="hp-skills" placeholder="스킬(콤마로 구분)">' +
    '<input id="hp-cost" type="number" step="0.01" placeholder="비용" value="0">' +
    '<button class="primary" type="submit">등록</button></form>' +
    '</section><section class="card"><h2>사람 인력 목록</h2>' +
    table(["ID", "이름", "역할", "스킬", "가용성", "비용", "동의", "성과점수", "동작"], people.map(p => [
      p.id, esc(p.name), esc(p.role), esc(p.skill_tags.join(", ")), esc(label(p.availability)), p.cost_estimate,
      p.performance_consent ? "동의함" : "미동의",
      p.performance_score === null ? "-" : p.performance_score,
      p.availability === "offboarded" ? "<span class=\\"muted\\">오프보딩됨</span>" :
        '<div class="actions">' +
        '<button class="small" data-toggle-consent="' + p.id + '" data-consent="' + (p.performance_consent ? "0" : "1") + '">' +
          (p.performance_consent ? "동의 철회" : "동의") + "</button>" +
        '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="score-' + p.id + '">' +
        '<button class="small" data-set-score="' + p.id + '">점수입력</button></span>' +
        '<button class="small danger" data-offboard="' + p.id + '">오프보딩</button>' +
        "</div>",
    ])) + "</section>";

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
  content.querySelectorAll("[data-offboard]").forEach(btn => {
    btn.onclick = async () => {
      if (!confirm("정말 오프보딩하시겠습니까?")) return;
      try {
        await api("POST", "/api/human-pool/" + btn.dataset.offboard + "/offboard");
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
    '<p class="muted">"모델 설정"은 이 에이전트가 기본으로 쓸 LLM provider/model을 저장만 합니다(비용 없음). "실행"은 온보딩된(활성) 에이전트에게 실제로 업무를 맡깁니다 — 설정해둔 모델(없으면 전역 기본값)로 실제 API를 호출하므로 비용이 발생합니다. 스킬에 vectordb/rag가 있으면 지식검색(RAG), graphdb가 있으면 관계분석 방식으로 자동 동작합니다(ADR 결정 11). "지식 색인"은 이 에이전트의 산출물을 임베딩해 지식검색 에이전트가 찾을 수 있게 저장합니다(비용 발생).</p>' +
    table(["ID", "역할", "스킬", "비용", "성과점수", "상태", "생성방식", "LLM", "최근 산출물", "동작"], agents.map(a => {
      let actions = "";
      if (isAdmin) {
        if (a.status === "pending_onboarding") actions += '<button class="small" data-onboard="' + a.id + '">온보딩 승인</button>';
        if (a.status === "active") actions += '<button class="small danger" data-deprecate="' + a.id + '">오프보딩</button>';
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
          '<span class="pair"><input placeholder="모델명(비우면 기본값)" id="model-' + a.id + '" value="' + esc(a.llm_model || "") + '">' +
          '<button class="small" data-set-model="' + a.id + '">모델 설정</button></span>';
      }
      if (canExecute && a.status === "active") {
        actions += '<button class="small" data-execute="' + a.id + '">실행(일 시키기)</button>';
        actions += '<button class="small" data-index-knowledge="' + a.id + '">지식 색인</button>';
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
      try {
        await api("PATCH", "/api/agent-pool/" + id + "/model", { provider, model });
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
        const r = await api("POST", "/api/agent-pool/" + btn.dataset.execute + "/execute", { prompt: taskPrompt });
        document.getElementById("execute-result").innerHTML =
          '<div class="msg ok"><b>실행 결과</b><br>' + esc(r.output).replace(/\\n/g, "<br>") + "</div>";
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-index-knowledge]").forEach(btn => {
    btn.onclick = async () => {
      if (!window.confirm("이 에이전트의 최근 산출물을 임베딩해 색인합니다(실제 임베딩 API 호출 — 비용 발생). 계속할까요?")) return;
      try {
        await api("POST", "/api/agent-pool/" + btn.dataset.indexKnowledge + "/index-knowledge", {});
        showMsg("global-msg", "지식 색인을 완료했습니다.", "ok");
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
    '<p class="muted">한 줄에 "에이전트ID: 이 에이전트가 할 일"을 입력하세요. sequential이면 앞줄 에이전트의 산출물이 다음 에이전트에게 자동으로 전달됩니다(Task.context). 모든 에이전트는 allow_delegation이 켜져 있어 필요하면 서로에게 실제로 위임/질문할 수 있습니다. 실제 LLM API를 호출하므로 비용이 발생합니다.</p>' +
    (active.length
      ? table(["ID", "역할", "스킬"], active.map(a => [a.id, esc(a.role_name), esc(a.skill_tags.join(", "))]))
      : '<p class="muted">온보딩된(활성) 에이전트가 없습니다 — 먼저 "AI 에이전트 생성"·"AI 에이전트 관리" 탭에서 준비하세요.</p>') +
    '<form id="crew-form">' +
    '<textarea id="crew-lines" rows="5" style="width:100%;box-sizing:border-box" placeholder="1: 요구사항 정의서 초안을 작성해줘&#10;2: 그 요구사항을 바탕으로 화면 설계를 해줘"></textarea>' +
    '<div class="inline" style="margin-top:0.5rem">' +
    '<select id="crew-process"><option value="sequential">sequential(순서대로 전달)</option>' +
    '<option value="hierarchical">hierarchical(매니저가 조율)</option></select>' +
    '<input id="crew-manager" type="number" placeholder="매니저 Agent ID(hierarchical 전용, 선택)">' +
    '<select id="crew-provider"><option value="">provider 자동 판별</option>' +
    '<option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>' +
    '<option value="ollama">Ollama(로컬)</option></select>' +
    '<input id="crew-model" placeholder="모델명(비우면 기본값)">' +
    '<button class="primary" type="submit">Crew 실행(비용 발생)</button>' +
    "</div></form>" +
    '<div id="crew-result"></div></section>';

  document.getElementById("crew-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const lines = document.getElementById("crew-lines").value.split("\\n").map(l => l.trim()).filter(Boolean);
    const agentIds = [];
    const taskDescriptions = [];
    for (const line of lines) {
      const idx = line.indexOf(":");
      if (idx < 0) { showMsg("global-msg", '형식이 잘못됐습니다: "' + line + '" (예: "1: 할 일")', "error"); return; }
      const id = Number(line.slice(0, idx).trim());
      if (isNaN(id)) { showMsg("global-msg", "에이전트 ID가 숫자가 아닙니다: " + line, "error"); return; }
      agentIds.push(id);
      taskDescriptions.push(line.slice(idx + 1).trim());
    }
    if (!agentIds.length) { showMsg("global-msg", "최소 한 줄은 입력해야 합니다.", "error"); return; }
    if (!window.confirm(agentIds.length + "개 Agent로 Crew를 실행합니다(실제 LLM API 호출, 비용 발생). 계속할까요?")) return;

    const managerVal = document.getElementById("crew-manager").value;
    try {
      const result = await api("POST", "/api/crew/run", {
        agent_ids: agentIds,
        task_descriptions: taskDescriptions,
        process: document.getElementById("crew-process").value,
        manager_agent_id: managerVal ? Number(managerVal) : null,
        provider: document.getElementById("crew-provider").value || null,
        model: document.getElementById("crew-model").value || null,
      });
      renderCrewResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function renderCrewResult(result) {
  const box = document.getElementById("crew-result");
  let html = '<h3>최종 산출물</h3><div class="msg ok">' + esc(result.final_output).replace(/\\n/g, "<br>") + "</div>";
  html += "<h3>Agent별 산출물</h3>" + table(["Agent ID", "역할", "산출물"], result.agents.map(a => [
    a.agent_id, esc(a.role_name), esc(a.output).slice(0, 200) + (a.output.length > 200 ? "…" : ""),
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
  let html = '<section class="card"><h2>조직도(확정된 배정)</h2>';
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
    table(["타입", "ID", "이름", "역할", "단가", "확정 배정 수", "합계"], report.resources.map(r => [
      esc(label(r.type)), r.id, esc(r.name || "-"), esc(r.role), r.cost_estimate, r.confirmed_assignments, r.total_cost,
    ])) + "</section>";
}

/* ---------- 공정성 ---------- */

async function renderFairness(content) {
  const r = await api("GET", "/api/fairness-report");
  content.innerHTML = '<section class="card"><h2>배정 공정성 지표</h2>' +
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
    '<form class="inline" id="audit-form">' +
    '<input id="a-actor" type="number" placeholder="처리자 ID">' +
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
    '<form class="inline" id="user-form">' +
    '<input id="u-username" placeholder="아이디" required>' +
    '<input id="u-password" type="password" placeholder="비밀번호" required>' +
    '<select id="u-role"><option value="admin">관리자</option><option value="manager">매니저</option>' +
    '<option value="member" selected>팀원</option><option value="viewer">뷰어</option></select>' +
    '<button class="primary" type="submit">생성</button></form>' +
    "</section>" +
    '<section class="card"><h2>사용자 목록 / 상사 지정</h2>' +
    '<p class="muted">상사를 지정하면 결재 요청이 자동으로 그 상사에게 라우팅됩니다.</p>' +
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
