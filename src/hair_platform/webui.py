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
  nav { display: flex; flex-wrap: wrap; gap: 0.25rem; background: #fff; padding: 0.5rem; border-bottom: 1px solid #ddd; }
  nav button { background: none; border: 1px solid #ccc; padding: 0.35rem 0.7rem; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }
  nav button.active { background: #14213d; color: #fff; border-color: #14213d; }
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
      <input id="login-username" placeholder="username" autocomplete="username" required>
      <input id="login-password" type="password" placeholder="password" autocomplete="current-password" required>
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

function table(headers, rows) {
  let html = "<table><thead><tr>";
  for (const h of headers) html += "<th>" + esc(h) + "</th>";
  html += "</tr></thead><tbody>";
  if (!rows.length) {
    html += '<tr><td colspan="' + headers.length + '" class="muted">데이터가 없습니다.</td></tr>';
  }
  for (const r of rows) {
    html += "<tr>";
    for (const c of r) html += "<td>" + c + "</td>";
    html += "</tr>";
  }
  html += "</tbody></table>";
  return html;
}

let ME = null;

const TABS = [
  { id: "briefs", label: "F1·F2 브리프/Task", roles: ["admin", "manager", "member", "viewer"], render: renderBriefs },
  { id: "match", label: "F6·F14 매칭/배분", roles: ["admin", "manager"], render: renderMatch },
  { id: "approvals", label: "F7 결재", roles: ["admin", "manager", "member", "viewer"], render: renderApprovals },
  { id: "jobs", label: "F4 Agent 생성", roles: ["admin", "manager"], render: renderJobs },
  { id: "humanpool", label: "F12 Human Pool", roles: ["admin", "manager"], render: renderHumanPool },
  { id: "agentpool", label: "F9 Agent Pool", roles: ["admin", "manager"], render: renderAgentPool },
  { id: "search", label: "F3 Pool 검색", roles: ["admin", "manager", "member", "viewer"], render: renderSearch },
  { id: "orgchart", label: "F5 조직도", roles: ["admin", "manager", "member", "viewer"], render: renderOrgChart },
  { id: "performance", label: "F8 성과리뷰", roles: ["admin", "manager", "member"], render: renderPerformance },
  { id: "cost", label: "F10 비용", roles: ["admin", "manager"], render: renderCost },
  { id: "fairness", label: "공정성 지표", roles: ["admin", "manager"], render: renderFairness },
  { id: "audit", label: "F13 감사로그", roles: ["admin", "manager"], render: renderAudit },
  { id: "admin", label: "관리자(사용자)", roles: ["admin"], render: renderAdmin },
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
  for (const t of TABS) {
    if (!t.roles.includes(ME.role)) continue;
    const btn = document.createElement("button");
    btn.textContent = t.label;
    btn.dataset.tab = t.id;
    btn.onclick = () => selectTab(t.id);
    nav.appendChild(btn);
  }
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

/* ---------- F1/F2 브리프 ---------- */

async function renderBriefs(content) {
  const canWrite = ["admin", "manager", "member"].includes(ME.role);
  const briefs = await api("GET", "/api/briefs");
  let html = "<section class=\\"card\\"><h2>F1 — 브리프 제출</h2>";
  if (canWrite) {
    html += '<form class="inline" id="brief-form">' +
      '<textarea id="brief-text" placeholder="예: 백엔드 개발자 1명" rows="2" style="flex:1;min-width:240px"></textarea>' +
      '<button class="primary" type="submit">제출</button></form>';
  }
  html += "</section><section class=\\"card\\"><h2>브리프 목록 / F2 Task 분해</h2>";
  html += table(["ID", "내용", "상태", "제안된 역할", "동작"], briefs.map(b => [
    b.id,
    esc(b.raw_text),
    esc(b.status),
    esc(b.stub_roles.join(", ")),
    canWrite ? '<button class="small" data-decompose="' + b.id + '">Task 분해</button> ' +
      '<button class="small" data-viewtasks="' + b.id + '">Task 보기</button>' : "",
  ]));
  html += '<div id="brief-detail"></div></section>';
  content.innerHTML = html;

  const form = document.getElementById("brief-form");
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api("POST", "/api/briefs", { raw_text: document.getElementById("brief-text").value });
        showMsg("global-msg", "브리프를 제출했습니다.", "ok");
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
    '<h3>브리프 #' + esc(briefId) + ' Task 분해</h3>' +
    '<form class="inline" id="decompose-form">' +
    '<input id="d-role" placeholder="role_name" required>' +
    '<input id="d-skills" placeholder="required_skills(콤마구분)">' +
    '<input id="d-headcount" type="number" min="1" value="1" style="width:70px">' +
    '<button class="primary" type="submit">Task 추가</button></form>';
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
      showMsg("global-msg", "Task를 추가했습니다.", "ok");
      renderTaskList(briefId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

async function renderTaskList(briefId) {
  const detail = document.getElementById("brief-detail");
  const tasks = await api("GET", "/api/briefs/" + briefId + "/tasks");
  detail.innerHTML = "<h3>브리프 #" + esc(briefId) + " Task 목록</h3>" +
    table(["Task ID", "역할", "필요 스킬", "인원"], tasks.map(t => [
      t.id, esc(t.role_name), esc(t.required_skills.join(", ")), t.headcount,
    ])) +
    '<p class="muted">Task ID는 "F6 매칭/배분", "F4 Agent 생성" 탭에서 사용합니다.</p>';
}

/* ---------- F6/F14 매칭·배분 ---------- */

async function renderMatch(content) {
  content.innerHTML =
    '<section class="card"><h2>F6/F14 — Task 매칭</h2>' +
    '<form class="inline" id="match-form">' +
    '<input id="match-task-id" type="number" placeholder="task_id" required style="width:100px">' +
    '<button class="primary" type="submit">매칭 조회</button></form>' +
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
    box.innerHTML = '<p class="msg error">매칭되는 후보가 없습니다 — F4(Agent 생성)를 제안합니다.</p>';
    return;
  }
  box.innerHTML = table(
    ["순위", "타입", "ID", "역할", "가용", "비용", "점수", "breakdown", "동작"],
    result.top.map((sc, i) => [
      i + 1, esc(sc.candidate.type), sc.candidate.id, esc(sc.candidate.role),
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
        const r = await api("POST", "/api/tasks/" + taskId + "/assignments", {
          resource_type: btn.dataset.assignType, resource_id: Number(btn.dataset.assignId),
        });
        showMsg("global-msg", "배정 요청 완료 (approval_id=" + r.approval_id + ") — F7 결재 탭에서 처리하세요.", "ok");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- F7 결재 ---------- */

async function renderApprovals(content) {
  const isDecider = ["admin", "manager"].includes(ME.role);
  let html = "";
  if (isDecider) {
    const inbox = await api("GET", qs("/api/approvals", { scope: "inbox" }));
    html += '<section class="card"><h2>F7 — 내 승인 대기함</h2>' + approvalTable(inbox, true) + "</section>";
  }
  const mine = await api("GET", qs("/api/approvals", { scope: "mine" }));
  html += '<section class="card"><h2>F7 — 내가 요청한 건</h2>' + approvalTable(mine, false) + "</section>";
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
  const headers = ["ID", "대상", "subject_id", "step", "상태"];
  if (withActions) headers.push("동작");
  return table(headers, rows.map(a => {
    const row = [a.id, esc(a.subject_type), a.subject_id, a.step, esc(a.status)];
    if (withActions) {
      row.push(a.status === "pending"
        ? '<button class="small" data-decide="' + a.id + '" data-approve="1">승인</button> ' +
          '<button class="small danger" data-decide="' + a.id + '" data-approve="0">반려</button>'
        : "");
    }
    return row;
  }));
}

/* ---------- F4 Agent 생성(Job Queue) ---------- */

async function renderJobs(content) {
  content.innerHTML =
    '<section class="card"><h2>F4 — Agent 생성(Job Queue)</h2>' +
    '<form class="inline" id="job-form">' +
    '<input id="job-brief-id" type="number" placeholder="brief_id" required style="width:100px">' +
    '<input id="job-task-ids" placeholder="task_id(콤마구분)" required style="width:180px">' +
    '<button class="primary" type="submit">생성 실행</button></form>' +
    '<div id="job-result"></div></section>';
  document.getElementById("job-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const briefId = document.getElementById("job-brief-id").value;
    const taskIds = document.getElementById("job-task-ids").value.split(",").map(s => Number(s.trim())).filter(n => !isNaN(n));
    try {
      const result = await api("POST", "/api/briefs/" + briefId + "/generate", { task_ids: taskIds });
      renderJobResult(result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function renderJobResult(result) {
  const box = document.getElementById("job-result");
  box.innerHTML = "<p>job_id=" + result.job_id + ", status=<b>" + esc(result.status) + "</b></p>" +
    table(["item_id", "task_id", "상태", "에러", "재시도", "동작"], result.items.map(it => [
      it.id, it.task_id, esc(it.status), esc(it.error || ""), it.retry_count,
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

/* ---------- F12 Human Pool ---------- */

async function renderHumanPool(content) {
  const people = await api("GET", "/api/human-pool");
  content.innerHTML =
    '<section class="card"><h2>F12 — Human Pool 온보딩</h2>' +
    '<form class="inline" id="hp-form">' +
    '<input id="hp-name" placeholder="이름" required>' +
    '<input id="hp-role" placeholder="역할" required>' +
    '<input id="hp-skills" placeholder="스킬(콤마구분)">' +
    '<input id="hp-cost" type="number" step="0.01" placeholder="비용" value="0">' +
    '<button class="primary" type="submit">등록</button></form>' +
    '</section><section class="card"><h2>Human Pool 목록</h2>' +
    table(["ID", "이름", "역할", "스킬", "가용성", "비용", "동의", "성과점수", "동작"], people.map(p => [
      p.id, esc(p.name), esc(p.role), esc(p.skill_tags.join(", ")), esc(p.availability), p.cost_estimate,
      p.performance_consent ? "동의함" : "미동의",
      p.performance_score === null ? "-" : p.performance_score,
      p.availability === "offboarded" ? "<span class=\\"muted\\">오프보딩됨</span>" :
        '<button class="small" data-toggle-consent="' + p.id + '" data-consent="' + (p.performance_consent ? "0" : "1") + '">' +
          (p.performance_consent ? "동의 철회" : "동의") + "</button> " +
        '<input class="small" style="width:60px" type="number" step="0.01" min="0" max="1" id="score-' + p.id + '"> ' +
        '<button class="small" data-set-score="' + p.id + '">점수입력</button> ' +
        '<button class="small danger" data-offboard="' + p.id + '">오프보딩</button>',
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

/* ---------- F9 Agent Pool ---------- */

async function renderAgentPool(content) {
  const agents = await api("GET", "/api/agent-pool");
  const isAdmin = ME.role === "admin";
  content.innerHTML = '<section class="card"><h2>F9 — Agent Pool</h2>' +
    table(["ID", "역할", "스킬", "비용", "성과점수", "상태", "동작"], agents.map(a => {
      let actions = "";
      if (isAdmin) {
        if (a.status === "pending_onboarding") actions += '<button class="small" data-onboard="' + a.id + '">온보딩 승인</button> ';
        if (a.status === "active") actions += '<button class="small danger" data-deprecate="' + a.id + '">오프보딩</button> ';
        actions += '<input class="small" style="width:70px" type="number" step="0.01" id="cost-' + a.id + '"> ' +
          '<button class="small" data-set-cost="' + a.id + '">비용입력</button> ' +
          '<input class="small" style="width:60px" type="number" step="0.01" min="0" max="1" id="ascore-' + a.id + '"> ' +
          '<button class="small" data-set-ascore="' + a.id + '">점수입력</button>';
      }
      return [a.id, esc(a.role_name), esc(a.skill_tags.join(", ")), a.cost_estimate,
        a.performance_score === null ? "-" : a.performance_score, esc(a.status), actions];
    })) + "</section>";

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
}

/* ---------- F3 Pool 검색 ---------- */

async function renderSearch(content) {
  content.innerHTML =
    '<section class="card"><h2>F3 — Pool 통합 검색</h2>' +
    '<form class="inline" id="search-form">' +
    '<input id="s-role" placeholder="role"><input id="s-skill" placeholder="skill">' +
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
        results.map(c => [esc(c.type), c.id, esc(c.name || "-"), esc(c.role), c.is_available ? "가능" : "불가", c.cost, esc(c.skill_tags.join(", ")), c.performance_score === null ? "-" : c.performance_score])
      );
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

/* ---------- F5 조직도 ---------- */

async function renderOrgChart(content) {
  const chart = await api("GET", "/api/org-chart");
  let html = '<section class="card"><h2>F5 — 조직도(확정된 배정)</h2>';
  for (const bucket of chart) {
    html += "<h3>" + (bucket.manager_username ? esc(bucket.manager_username) : "(지정된 관리자 없음)") + "</h3>";
    html += table(["배정 ID", "Task ID", "역할", "타입", "리소스"], bucket.reports.map(r => [
      r.assignment_id, r.task_id, esc(r.role_name), esc(r.resource_type), esc(r.resource_name || "-"),
    ]));
  }
  if (!chart.length) html += '<p class="muted">확정된 배정이 없습니다.</p>';
  html += "</section>";
  content.innerHTML = html;
}

/* ---------- F8 성과리뷰 ---------- */

async function renderPerformance(content) {
  const rows = await api("GET", "/api/performance-review");
  content.innerHTML = '<section class="card"><h2>F8 — 성과리뷰</h2>' +
    table(["타입", "ID", "이름", "역할", "동의", "성과점수", "확정 배정 수"], rows.map(r => [
      esc(r.type), r.id, esc(r.name || "-"), esc(r.role),
      r.performance_consent === null ? "-" : (r.performance_consent ? "동의함" : "미동의"),
      r.performance_score === null ? "-" : r.performance_score,
      r.confirmed_assignments,
    ])) + "</section>";
}

/* ---------- F10 비용 ---------- */

async function renderCost(content) {
  const report = await api("GET", "/api/cost-report");
  content.innerHTML = '<section class="card"><h2>F10 — 비용 리포트(총액: ' + report.total_cost + ')</h2>' +
    table(["타입", "ID", "이름", "역할", "단가", "확정 배정 수", "합계"], report.resources.map(r => [
      esc(r.type), r.id, esc(r.name || "-"), esc(r.role), r.cost_estimate, r.confirmed_assignments, r.total_cost,
    ])) + "</section>";
}

/* ---------- 공정성 ---------- */

async function renderFairness(content) {
  const r = await api("GET", "/api/fairness-report");
  content.innerHTML = '<section class="card"><h2>배정 공정성 지표(NF4)</h2>' +
    (r.low_sample_warning ? '<p class="msg error">표본이 작아(각 10건 미만) 통계적 해석에 주의가 필요합니다.</p>' : "") +
    table(["구분", "확정", "반려", "결정됨", "반려율"], [
      ["사람", r.human.confirmed, r.human.rejected, r.human.decided, r.human.rejection_rate === null ? "-" : r.human.rejection_rate.toFixed(3)],
      ["Agent", r.agent.confirmed, r.agent.rejected, r.agent.decided, r.agent.rejection_rate === null ? "-" : r.agent.rejection_rate.toFixed(3)],
    ]) +
    "<p>반려율 격차: " + (r.rejection_rate_gap === null ? "-" : r.rejection_rate_gap.toFixed(3)) + "</p></section>";
}

/* ---------- F13 감사로그 ---------- */

async function renderAudit(content) {
  content.innerHTML =
    '<section class="card"><h2>F13 — 감사로그</h2>' +
    '<form class="inline" id="audit-form">' +
    '<input id="a-actor" type="number" placeholder="actor_id">' +
    '<input id="a-subject" placeholder="subject_type">' +
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
      table(["시각", "actor", "action", "subject", "subject_id"], body.items.map(a => [
        esc(a.created_at), a.actor_id, esc(a.action), esc(a.subject_type), a.subject_id === null ? "-" : a.subject_id,
      ]));
  };
  document.getElementById("audit-form").addEventListener("submit", (ev) => { ev.preventDefault(); load(); });
  await load();
}

/* ---------- 관리자 ---------- */

async function renderAdmin(content) {
  const users = await api("GET", "/api/users");
  content.innerHTML =
    '<section class="card"><h2>관리자 — 사용자 생성</h2>' +
    '<form class="inline" id="user-form">' +
    '<input id="u-username" placeholder="username" required>' +
    '<input id="u-password" type="password" placeholder="password" required>' +
    '<select id="u-role"><option value="admin">admin</option><option value="manager">manager</option>' +
    '<option value="member" selected>member</option><option value="viewer">viewer</option></select>' +
    '<button class="primary" type="submit">생성</button></form>' +
    "</section>" +
    '<section class="card"><h2>사용자 목록 / 상사(manager_id) 지정 — F7용</h2>' +
    table(["ID", "username", "role", "상사", "동작"], users.map(u => [
      u.id, esc(u.username), esc(u.role), u.manager_id === null ? "-" : u.manager_id,
      '<select id="mgr-' + u.id + '"><option value="">(없음)</option>' +
        users.filter(m => m.id !== u.id).map(m => '<option value="' + m.id + '"' + (m.id === u.manager_id ? " selected" : "") + ">" + esc(m.username) + "</option>").join("") +
      '</select> <button class="small" data-set-manager="' + u.id + '">저장</button>',
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
