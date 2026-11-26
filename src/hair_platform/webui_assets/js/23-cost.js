/* ---------- 비용 ---------- */

// AI 사용 한도(2026-11-25, P2-14) — 이번 달 사용·한도. 변경은 admin.
function _aiBudgetCardHtml(b) {
  if (!b) return "";
  const c = b.config;
  const money = v => (v === null || v === undefined) ? "제한 없음" : "$" + Number(v).toFixed(2);
  const bar = r => (r === null || r === undefined) ? "" :
    '<div class="progress-bar" style="max-width:16rem"><div class="progress-bar-fill" style="width:' + Math.min(100, Math.round(r * 100)) + '%;' +
    (r >= 1 ? "background:var(--danger, #c0392b)" : (r >= c.warn_ratio ? "background:var(--warn, #d68910)" : "")) + '"></div></div>';
  const warn = (b.org_ratio !== null && b.org_ratio >= c.warn_ratio) ? ' <span class="gate-badge gate-warn">한도의 ' + Math.round(b.org_ratio * 100) + "%</span>" : "";
  const rows = (b.users || []).map(u => [
    esc(u.username || ("#" + u.user_id)), esc(u.role || ""), "$" + Number(u.cost).toFixed(4),
    u.ratio === null ? '<span class="muted">' + (u.role === "admin" ? "admin(사용자별 한도 제외)" : "-") + "</span>" : Math.round(u.ratio * 100) + "% " + bar(u.ratio),
  ]);
  const editable = ME.role === "admin";
  const inp = (id, v, ph) => '<input type="number" min="0" step="0.01" id="' + id + '" value="' + (v === null || v === undefined ? "" : v) + '" placeholder="' + ph + '" style="width:8rem"' + (editable ? "" : " disabled") + ">";
  return '<section class="card"><h2>AI 사용 한도(이번 달)</h2>' +
    '<p class="muted">이번 달(UTC 1일부터) 실제 AI 호출 비용 = 토큰 × "모델 레지스트리" 단가입니다. 단가를 등록하지 않은 모델(로컬 Ollama 등)은 0으로 셉니다. 한도에 도달하면 그 사람(또는 조직 전체)의 AI 호출을 하지 않고 이유를 보여 줍니다. admin은 사용자별 한도에서 빠지고 조직 전체 한도만 적용됩니다.</p>' +
    "<p><b>조직 전체</b> $" + Number(b.org_cost).toFixed(4) + " / " + money(c.org_monthly_usd) + warn + bar(b.org_ratio) + "</p>" +
    '<form id="aib-form" class="inline" style="flex-wrap:wrap;gap:0.6rem">' +
    "<label>사용자별 한 달 한도(USD) " + inp("aib-user", c.user_monthly_usd, "제한 없음") + "</label>" +
    "<label>조직 전체 한 달 한도(USD) " + inp("aib-org", c.org_monthly_usd, "제한 없음") + "</label>" +
    '<label>경고 비율 <input type="number" min="0.1" max="1" step="0.05" id="aib-warn" value="' + c.warn_ratio + '" style="width:5rem"' + (editable ? "" : " disabled") + "></label>" +
    '<label><input type="checkbox" id="aib-viewer"' + (c.viewer_ai_allowed ? " checked" : "") + (editable ? "" : " disabled") + "> viewer도 대화에서 AI 호출 허용</label>" +
    (editable ? '<button class="small primary" type="submit">저장</button>' : '<span class="muted">변경은 admin만 할 수 있습니다.</span>') +
    "</form>" +
    (rows.length ? table(["사용자", "역할", "이번 달 비용", "사용자별 한도 대비"], rows) : '<p class="muted">이번 달 사용자별 기록이 아직 없습니다.</p>') +
    "</section>";
}

async function renderCost(content) {
  const [report, aiBudget] = await Promise.all([
    api("GET", "/api/cost-report"),
    api("GET", "/api/ai-budget").catch(() => null),
  ]);
  content.innerHTML = _aiBudgetCardHtml(aiBudget) + '<section class="card"><h2>비용 리포트(총액: ' + report.total_cost + ')</h2>' +
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
    ])) + "</section>" +
    '<section class="card" id="work-usage-section"><h2>실제 AI 사용량 — 업무별·Agent별(최근 30일)</h2><p class="muted">불러오는 중…</p></section>';
  // 업무별 효율·비용(2026-11-23, P2-11 — `llm_usage.work_report`). 위 표(수동 단가 추정)와 달리
  // 실제 LLM 호출 기록을 업무·Agent에 붙여 센다.
  const box = document.getElementById("work-usage-section");
  try {
    const w = await api("GET", "/api/llm-usage/work?days=30");
    const money = r => r.cost_partial ? (r.cost > 0 ? r.cost.toFixed(2) + " + 단가 미입력분" : "단가 미입력") : r.cost.toFixed(2);
    const fmtTok = n => n.toLocaleString();
    box.innerHTML = "<h2>실제 AI 사용량 — 업무별·Agent별(최근 30일)</h2>" +
      helpBox(
        '<ul class="muted help-list">' +
        "<li>실제 LLM 호출 기록을 그 호출이 일어난 업무(할 일)·Agent에 붙여 셉니다 — 위 표의 수동 단가 추정과 다른, 실제 사용량입니다.</li>" +
        '<li>금액은 "모델 레지스트리"에 단가(100만 토큰당)를 입력한 모델만 계산합니다 — 단가가 없는 모델이 섞이면 "단가 미입력분"으로 표시합니다(금액을 지어내지 않습니다).</li>' +
        "<li>사람 결재는 이 업무의 결과물·파이프라인 단계 결재 중 실제로 승인/반려된 건수입니다 — 많을수록 사람 손이 많이 간 업무입니다.</li>" +
        "<li>이 기능이 생기기 전(2026-11-23 이전)의 사용량과, 업무 밖에서 쓴 호출(대화의 기본 AI 등)은 아래 '업무에 연결되지 않은 사용량'으로 따로 셉니다.</li>" +
        "</ul>"
      ) +
      "<h3>업무별</h3>" +
      (w.tasks.length ? table(["할 일", "업무 요청", "LLM 호출", "토큰", "금액", "파이프라인 실행", "평균 소요(분)", "사람 결재", "결과물"],
        w.tasks.map(t => [
          "#" + t.task_id + " " + esc(t.role_name || ""), esc(t.brief || ""), t.calls, fmtTok(t.tokens), money(t),
          t.pipeline_runs, t.avg_run_minutes === null ? "-" : t.avg_run_minutes, t.human_decisions, t.deliverables,
        ])) : '<p class="muted">업무에 연결된 사용량이 아직 없습니다.</p>') +
      "<h3>Agent별</h3>" +
      (w.agents.length ? table(["Agent", "LLM 호출", "토큰", "금액"],
        w.agents.map(a => [esc(a.name || ("#" + a.agent_id)), a.calls, fmtTok(a.tokens), money(a)]))
        : '<p class="muted">Agent에 연결된 사용량이 아직 없습니다.</p>') +
      '<p class="muted">업무에 연결되지 않은 사용량: 호출 ' + w.unattributed.calls + "건 · 토큰 " + fmtTok(w.unattributed.tokens) + "</p>";
  } catch (e) {
    box.innerHTML = '<h2>실제 AI 사용량</h2><p class="muted">' + esc(e.message) + "</p>";
  }
  const aibForm = document.getElementById("aib-form");
  if (aibForm && ME.role === "admin") aibForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const val = id => { const v = document.getElementById(id).value; return v === "" ? null : Number(v); };
    try {
      await api("PUT", "/api/ai-budget", {
        user_monthly_usd: val("aib-user"), org_monthly_usd: val("aib-org"),
        warn_ratio: Number(document.getElementById("aib-warn").value),
        viewer_ai_allowed: document.getElementById("aib-viewer").checked,
      });
      showMsg("global-msg", "AI 사용 한도를 저장했습니다 — 다음 AI 호출부터 적용됩니다.", "ok");
      renderCost(content);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

