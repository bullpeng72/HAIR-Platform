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
      labels: gates.map(g => GATE_SHORT_NAMES[g]),  // 축 이름은 뜻으로(2026-11-27, 사용성 평가 4번)
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
              return GATE_SHORT_NAMES[gates[ctx.dataIndex]] + "(Gate " + gates[ctx.dataIndex] + "): " + (v === null ? "미측정" : v.toFixed(2));
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
              return label(t.subject_type) + " #" + t.subject_id + " · " + fmtTime(t.created_at) + " · " + t.cost_estimate;
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
    return '<div class="stat-row"><span title="Gate ' + g + '">' + GATE_SHORT_NAMES[g] + '</span><span>' + (v === null ? "-" : v.toFixed(2)) + '</span></div>';
  }).join("");

  // 사용자 요청(2026-10-27, "다룬 테이블도 ID 표기되는 것이 남아
  // 있는지 확인") — agent_pool/human_pool 변경 이력이라 code로 교체.
  const trendRows = d.cost_trend.map(t => [
    esc(fmtTime(t.created_at)), esc(label(t.subject_type)), esc(t.subject_code || ("#" + t.subject_id)), t.cost_estimate,
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
    '건 연속 품질 점검 미달</li>'
  ).join("") || '<p class="muted">최근 품질 점검에서 연속으로 미달한 Agent가 없습니다.</p>';

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


/* ---------- 프로세스 성과(2026-11-27, AI Native 분석 1단계 — 1번 성과 측정·8번 전체 흐름) ---------- */
// 파이프라인(=업무 프로세스)마다 "얼마나 빨리·한 번에·사람 손을 얼마나 덜 거쳐" 끝나는지와, 어느 단계에서 시간을
// 쓰고 되돌아가는지를 보여 준다. 숫자는 이미 쌓인 실행 기록에서 계산한다(`process_metrics.py`). 새로 적는 값은
// "기준 시간"(예전 방식으로 한 건에 걸리던 시간) 하나뿐이다.
let _pmDays = 30;
let _pmTemplateId = null;
let _pmChart = null;

function _pmDur(sec) {
  if (sec === null || sec === undefined) return "—";
  const m = Math.round(sec / 60);
  if (m < 1) return Math.round(sec) + "초";
  if (m < 60) return m + "분";
  const h = Math.floor(m / 60), mm = m % 60;
  if (h < 48) return h + "시간" + (mm ? " " + mm + "분" : "");
  return Math.round(h / 24) + "일";
}

function _pmPct(v) { return v === null || v === undefined ? "—" : Math.round(v * 100) + "%"; }

function _pmCost(v, partial) {
  if (v === null || v === undefined) return "—";
  if (v === 0 && partial) return "단가 없음";
  return "$" + (v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)) + (partial ? "+" : "");
}

// 리드타임 구성 막대 — AI 작업 / 사람 대기 / 그 밖(작업 순서 대기 등)
function _pmTimeBar(ai, wait, other) {
  const a = ai || 0, w = wait || 0, o = other || 0, total = a + w + o;
  if (!total) return '<span class="muted">—</span>';
  const seg = (v, cls, label) => v > 0
    ? '<span class="pm-seg ' + cls + '" style="width:' + (v / total * 100).toFixed(1) + '%" title="' + label + " " + _pmDur(v) + '"></span>' : "";
  return '<span class="pm-bar" role="img" aria-label="AI ' + _pmDur(a) + ", 사람 대기 " + _pmDur(w) + ", 그 밖 " + _pmDur(o) + '">' +
    seg(a, "pm-ai", "AI 작업") + seg(w, "pm-wait", "사람 대기") + seg(o, "pm-other", "그 밖") + "</span>";
}

function _pmSignals(list) {
  if (!list || !list.length) return '<span class="muted">—</span>';
  return list.map(s => '<span class="gate-badge ' + (s.level === "warn" ? "gate-warn" : "gate-info") + ' pm-signal">' + esc(s.text) + "</span>").join(" ");
}

function _pmTile(label, value, sub) {
  return '<div class="pm-tile"><div class="pm-tile-label">' + esc(label) + '</div><div class="pm-tile-value">' + value +
    "</div>" + (sub ? '<div class="pm-tile-sub muted">' + sub + "</div>" : "") + "</div>";
}

function _pmTiles(s, withBaseline) {
  return '<div class="pm-tiles">' +
    _pmTile("완료", String(s.completed), "실행 " + s.runs + "건 · 실패 " + s.failed + "건") +
    _pmTile("완료까지(중앙값)", _pmDur(s.lead_median_seconds), "AI " + _pmDur(s.ai_mean_seconds) + " · 사람 대기 " + _pmDur(s.wait_mean_seconds)) +
    _pmTile("한 번에 완료", _pmPct(s.first_pass_rate), "다시 작성·사람 검토 없이 끝난 비율") +
    _pmTile("자동 처리율", _pmPct(s.automation_rate), "사람 결재 없이 넘어간 단계 비율") +
    _pmTile("지금 결재 대기", String(s.waiting_for_human), "진행 중 " + s.running + "건") +
    (withBaseline ? _pmTile("AI가 맡은 작업량", s.baseline_hours_covered ? s.baseline_hours_covered.toFixed(1) + "시간" : "—",
      s.baseline_hours_covered ? "기준 시간 × 완료 건수(추정)" : "기준 시간을 넣으면 계산됩니다") : "") +
    "</div>";
}

function _pmPeriodButtons() {
  return '<div class="pm-period" role="group" aria-label="기간">' + [[7, "7일"], [30, "30일"], [90, "90일"], [0, "전체"]].map(([d, l]) =>
    '<button class="small' + (d === _pmDays ? " active" : "") + '" data-pm-days="' + d + '" aria-pressed="' + (d === _pmDays) + '">' + l + "</button>").join("") + "</div>";
}

function _pmInitTrend(trend) {
  const canvas = document.getElementById("pm-trend-canvas");
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    canvas.insertAdjacentHTML("afterend", '<p class="muted">차트 라이브러리를 불러오지 못했습니다.</p>');
    return;
  }
  if (_pmChart) { try { _pmChart.destroy(); } catch (_) { /* 이미 사라진 canvas */ } }
  _pmChart = new Chart(canvas, {
    data: {
      labels: trend.map(w => w.week_start.slice(5) + "주"),
      datasets: [
        { type: "bar", label: "완료 건수", data: trend.map(w => w.completed), backgroundColor: "rgba(74,144,217,0.55)", yAxisID: "y" },
        { type: "line", label: "완료까지(분, 중앙값)", data: trend.map(w => w.lead_median_seconds === null ? null : Math.round(w.lead_median_seconds / 60)),
          borderColor: "#fca311", backgroundColor: "#fca311", spanGaps: true, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "건" } },
        y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "분" } },
      },
    },
  });
}

async function renderProcessMetrics(content) {
  if (_pmTemplateId !== null) return renderProcessMetricsDetail(content, _pmTemplateId);
  const d = await api("GET", "/api/process-metrics?days=" + _pmDays);
  const t = d.total;
  content.innerHTML =
    '<section class="card"><h2>프로세스 성과</h2>' +
    helpBox('<ul class="muted help-list">' +
      "<li>파이프라인(업무 프로세스)마다 <b>얼마나 빨리, 한 번에, 사람 손을 얼마나 덜 거쳐</b> 끝나는지 보여 줍니다. 숫자는 지금까지의 실행 기록에서 계산합니다.</li>" +
      "<li><b>완료까지</b> = 실행 시작부터 완료까지. <b>AI 작업</b>(단계가 실제로 돈 시간)·<b>사람 대기</b>(결재를 기다린 시간)·<b>그 밖</b>(작업 순서 대기·단계 사이 준비)으로 나눠 봅니다.</li>" +
      "<li><b>한 번에 완료</b> = 품질 검사 실패로 다시 쓰거나 사람에게 넘어가지 않고 끝난 비율. <b>자동 처리율</b> = 통과한 단계 중 사람 결재 없이 넘어간 비율.</li>" +
      "<li><b>기준 시간</b>(예전 방식으로 한 건에 걸리던 시간)을 파이프라인 자세히 화면에서 넣으면 AI가 맡은 작업량과 속도 비교가 나옵니다.</li>" +
      "<li>완료 3건 이상 쌓이면 <b>먼저 손볼 곳</b>(실패·재작업·결재 병목 등)을 근거 숫자와 함께 알려 줍니다.</li>" +
      "<li><b>개선 제안</b> = 사람이 반려하며 적은 사유와 자주 미달하는 품질 기준에서 나온 '배운 점' 후보. 자세히 화면에서 적용하면 다음 실행부터 그 단계 지시문에 들어갑니다.</li>" +
      "</ul>") +
    '<div class="pm-head">' + _pmPeriodButtons() + '<span class="muted">기준 ' + esc(fmtTime(d.generated_at)) + "</span></div>" +
    _pmTiles(t, true) +
    '<div class="pm-legend muted"><span class="pm-seg pm-ai"></span> AI 작업 <span class="pm-seg pm-wait"></span> 사람 대기 <span class="pm-seg pm-other"></span> 그 밖</div>' +
    "<h3>파이프라인별</h3>" +
    '<div class="pm-wide">' + table(["파이프라인", "실행", "완료율", "완료까지", "시간 구성", "한 번에 완료", "재작업/건", "자동 처리율", "건당 비용", "기준 시간", "먼저 손볼 곳", "개선 제안", "동작"],
      d.templates.map(p => [
        esc(p.name) + (p.is_active ? "" : ' <span class="muted">(비활성)</span>'),
        String(p.runs), _pmPct(p.completion_rate), _pmDur(p.lead_median_seconds),
        _pmTimeBar(p.ai_mean_seconds, p.wait_mean_seconds, p.other_mean_seconds),
        _pmPct(p.first_pass_rate), p.rework_per_run === null ? "—" : p.rework_per_run.toFixed(1),
        _pmPct(p.automation_rate), p.tokens_per_run ? _pmCost(p.cost_per_run, p.cost_partial) : "—",
        p.baseline_minutes ? _pmDur(p.baseline_minutes * 60) : '<span class="muted">없음</span>',
        _pmSignals(p.signals),
        p.lesson_candidates ? '<span class="gate-badge gate-info">' + p.lesson_candidates + "건</span>" : '<span class="muted">—</span>',
        '<button class="small" data-pm-open="' + p.template_id + '">자세히</button>',
      ]),
      { empty: '이 기간에 실행된 파이프라인이 없습니다. <button class="small" data-go-tab="home">홈에서 AI에게 일 맡기기</button>' }) + "</div>" +
    "<h3>지금 결재를 기다리는 실행</h3>" +
    '<div class="pm-wide-sm">' + table(["파이프라인", "단계", "기다린 시간", "구분", "동작"],
      d.waiting.map(w => [
        esc(w.template_name), esc(w.stage_name), _pmDur(w.waiting_seconds),
        w.escalated ? '<span class="gate-badge gate-warn">자동 재시도 실패 → 사람 검토</span>' : "승인 대기",
        '<button class="small" data-go-tab="approvals">결재함</button>',
      ]),
      { empty: "결재를 기다리는 실행이 없습니다." }) + "</div>" +
    "<h3>주별 추이(최근 8주)</h3>" +
    '<div class="pm-chart"><canvas id="pm-trend-canvas"></canvas></div>' +
    "</section>";
  _pmBindCommon(content);
  content.querySelectorAll("[data-pm-open]").forEach(b => b.onclick = () => { _pmTemplateId = Number(b.dataset.pmOpen); refresh(); });
  _pmInitTrend(d.trend);
}

function _pmBindCommon(content) {
  content.querySelectorAll("[data-pm-days]").forEach(b => b.onclick = () => { _pmDays = Number(b.dataset.pmDays); refresh(); });
}

async function renderProcessMetricsDetail(content, templateId) {
  let d;
  try {
    d = await api("GET", "/api/process-metrics/templates/" + templateId + "?days=" + _pmDays);
    [d.lessons, d.changes] = await Promise.all([
      api("GET", "/api/process-metrics/templates/" + templateId + "/lessons"),
      api("GET", "/api/process-metrics/templates/" + templateId + "/changes"),
    ]);
  } catch (e) {
    _pmTemplateId = null;
    return renderProcessMetrics(content);
  }
  const s = d.summary;
  const flowRows = d.stages.map(f => [
    f.seq_order + ". " + esc(f.name) + (f.requires_human_approval ? ' <span class="muted">(사람 승인)</span>' : "") +
      (f.bottleneck ? ' <span class="gate-badge gate-warn">병목</span>' : ""),
    String(f.runs_reached), String(f.attempts),
    f.attempts ? f.first_try_passed + "건" : "—",
    f.rework ? '<b>' + f.rework + "</b>건 (" + _pmPct(f.rework_rate) + ")" : "0",
    f.human_reviews ? f.human_reviews + "건" + (f.human_rejections ? " · 반려 " + f.human_rejections : "") + (f.escalated ? " · 넘어감 " + f.escalated : "") : "0",
    _pmDur(f.ai_mean_seconds), _pmDur(f.wait_mean_seconds),
    f.time_share === null ? "—" : '<span class="pm-share"><span style="width:' + Math.round(f.time_share * 100) + '%"></span></span> ' + _pmPct(f.time_share),
    (f.now_running ? "진행 " + f.now_running : "") + (f.now_running && f.now_waiting ? " · " : "") + (f.now_waiting ? '<b>대기 ' + f.now_waiting + "</b>" : "") || '<span class="muted">—</span>',
  ]);
  const statusName = { completed: "완료", failed: "실패", cancelled: "취소", running: "진행 중", paused_for_approval: "결재 대기" };
  content.innerHTML =
    '<section class="card"><p><button class="small" id="pm-back">← 프로세스 성과</button></p>' +
    "<h2>" + esc(d.name) + ' <span class="muted pm-domain">' + esc(d.domain) + "</span></h2>" +
    '<div class="pm-head">' + _pmPeriodButtons() + '<span class="muted">기준 ' + esc(fmtTime(d.generated_at)) + "</span></div>" +
    _pmTiles(s, true) +
    (s.signals.length ? '<p class="pm-signals"><b>먼저 손볼 곳</b> ' + _pmSignals(s.signals) + "</p>" : "") +
    '<div class="pm-baseline"><label for="pm-baseline-input"><b>기준 시간</b> — 예전 방식(사람이 직접)으로 이 업무 한 건에 걸리던 시간</label> ' +
    '<input id="pm-baseline-input" type="number" min="1" step="1" inputmode="numeric" value="' + (s.baseline_minutes || "") + '" placeholder="예: 120"> 분 ' +
    '<button class="small" id="pm-baseline-save">저장</button> ' +
    (s.baseline_minutes ? '<button class="small" id="pm-baseline-clear">지우기</button> ' : "") +
    '<span class="muted" id="pm-baseline-msg">' +
    (s.baseline_minutes && s.lead_vs_baseline !== null
      ? "완료까지 걸린 시간(중앙값)은 기준의 " + Math.round(s.lead_vs_baseline * 100) + "%입니다" + (s.lead_vs_baseline < 1 ? " — 더 빠릅니다." : " — 더 느립니다.") +
        " (사람이 결과를 검토한 시간은 빼지 않은 추정입니다)"
      : "") + "</span></div>" +
    "<h3>단계별 흐름</h3>" +
    '<p class="muted">어느 단계에서 시간을 쓰고, 다시 쓰고, 사람에게 넘어가는지 봅니다. 시간 비중이 40% 이상인 단계는 <b>병목</b>으로 표시합니다.</p>' +
'<div class="pm-wide">' + table(["단계", "지나간 실행", "시도", "처음에 통과", "다시 작성", "사람 검토", "AI 작업(평균)", "사람 대기(평균)", "시간 비중", "지금"], flowRows,
      { empty: "단계가 없습니다." }) + "</div>" +
    _pmLessonsHtml(d) +
    _pmChangesHtml(d.changes) +
    "<h3>최근 실행</h3>" +
'<div class="pm-wide">' + table(["실행", "상태", "시작", "완료까지", "시간 구성", "다시 작성", "사람 결정", "토큰"],
      d.recent_runs.map(r => [
        "#" + r.run_id, esc(statusName[r.status] || r.status) + (r.first_pass ? ' <span class="gate-badge gate-ok">한 번에</span>' : ""),
        esc(fmtTime(r.created_at)), _pmDur(r.lead_seconds),
        _pmTimeBar(r.ai_seconds, r.wait_seconds, r.lead_seconds === null ? 0 : Math.max(0, r.lead_seconds - r.ai_seconds - r.wait_seconds)),
        String(r.rework), String(r.human_decisions), r.tokens ? r.tokens.toLocaleString() : "—",
      ]),
      { empty: "이 기간에 실행이 없습니다." }) + "</div>" +
    "<h3>주별 추이(최근 8주)</h3>" +
    '<div class="pm-chart"><canvas id="pm-trend-canvas"></canvas></div>' +
    "</section>";
  _pmBindCommon(content);
  document.getElementById("pm-back").onclick = () => { _pmTemplateId = null; refresh(); };
  const save = async (minutes) => {
    const msg = document.getElementById("pm-baseline-msg");
    try {
      await api("PUT", "/api/process-metrics/templates/" + templateId + "/baseline", { minutes });
      refresh();
    } catch (e) {
      msg.textContent = "저장하지 못했습니다: " + (e.message || e);
    }
  };
  document.getElementById("pm-baseline-save").onclick = () => {
    const raw = document.getElementById("pm-baseline-input").value.trim();
    const n = Number(raw);
    if (!raw || !Number.isInteger(n) || n <= 0) {
      document.getElementById("pm-baseline-msg").textContent = "1 이상의 분 단위 숫자를 넣으세요.";
      return;
    }
    save(n);
  };
  const clear = document.getElementById("pm-baseline-clear");
  if (clear) clear.onclick = () => save(null);
  _pmBindLearning(content);
  _pmInitTrend(d.trend);
}

/* ---------- 배운 점·변경 기록(2026-11-27, AI Native 분석 2단계 — 2번 학습 루프·10번 변경 효과) ---------- */
function _pmLessonSource(l) {
  const ev = l.evidence || {};
  if (l.source === "human_rejection") return "사람 반려 사유" + (l.hits > 1 ? " · " + l.hits + "번" : "");
  if (l.source === "gate_pattern") return "품질 기준 '" + esc(ev.gate_name || ev.gate || "") + "' 미달 " + (ev.failures || l.hits) + "번 / 시도 " + (ev.attempts || "?") + "번(최근 " + (ev.days || 60) + "일)";
  return "직접 입력";
}

function _pmLessonsHtml(d) {
  const L = d.lessons || { candidates: [], active: [] };
  const cand = L.candidates.map(l =>
    '<div class="pm-lesson" data-lesson="' + l.id + '"><div class="pm-lesson-head"><b>' + l.seq_order + ". " + esc(l.stage_name) + "</b> " +
    '<span class="muted">' + _pmLessonSource(l) + "</span></div>" +
    '<textarea class="pm-lesson-text" rows="2" maxlength="300" aria-label="적용할 글">' + esc(l.text) + "</textarea>" +
    '<div class="pm-lesson-actions"><button class="small" data-lesson-apply="' + l.id + '">적용</button> ' +
    '<button class="small" data-lesson-dismiss="' + l.id + '">무시</button> <span class="muted pm-lesson-msg"></span></div></div>').join("");
  const active = L.active.map(l => [
    l.seq_order + ". " + esc(l.stage_name), esc(l.text), _pmLessonSource(l),
    esc(fmtTime(l.decided_at)) + (l.decided_by_name ? " · " + esc(l.decided_by_name) : ""),
    '<button class="small" data-lesson-retire="' + l.id + '">끄기</button>',
  ]);
  const stageOpts = (d.stages || []).map(st => '<option value="' + st.stage_id + '">' + st.seq_order + ". " + esc(st.name) + "</option>").join("");
  return "<h3>배운 점</h3>" +
    '<p class="muted">사람이 반려하며 적은 사유와 자주 미달하는 품질 기준에서 <b>개선 제안</b>을 모읍니다. <b>적용</b>하면(글을 고쳐도 됩니다) 다음 실행부터 그 단계 지시문 끝에 들어가고, 아래 변경 기록에서 효과를 비교할 수 있습니다. 단계마다 ' + (L.max_active_per_stage || 8) + "개까지.</p>" +
    (cand ? '<h4>개선 제안 <span class="gate-badge gate-info">' + L.candidates.length + "건</span></h4>" + cand
      : '<p class="muted">아직 개선 제안이 없습니다. 결재함에서 파이프라인 단계를 반려할 때 사유를 구체적으로 적으면 여기에 모입니다.</p>') +
    "<h4>적용 중</h4>" +
    '<div class="pm-wide pm-wrap">' + table(["단계", "배운 점", "출처", "적용", "동작"], active, { empty: "적용 중인 배운 점이 없습니다." }) + "</div>" +
    '<div class="pm-lesson-add"><label for="pm-lesson-stage">직접 추가</label> <select id="pm-lesson-stage">' + stageOpts + "</select> " +
    '<input id="pm-lesson-new" maxlength="300" placeholder="예: 금액은 천 원 단위로 반올림해 표기하세요"> ' +
    '<button class="small" id="pm-lesson-add-btn">바로 적용</button> <span class="muted" id="pm-lesson-add-msg"></span></div>';
}

function _pmWindowCell(w, withStage) {
  if (!w || !w.runs) return '<span class="muted">실행 없음</span>';
  return w.runs + "건 · 한 번에 " + _pmPct(w.first_pass_rate) + " · 완료까지 " + _pmDur(w.lead_median_seconds) +
    (withStage && w.stage_attempts ? '<br><span class="muted">이 단계 다시 작성 ' + _pmPct(w.stage_rework_rate) + "</span>" : "");
}

function _pmChangesHtml(changes) {
  const lv = { ok: "gate-ok", warn: "gate-warn", muted: "" };
  const rows = (changes || []).map(c => [
    esc(fmtTime(c.created_at)),
    esc(c.summary) + (c.reverted_by ? ' <span class="muted">(되돌림)</span>' : ""),
    esc(c.actor_name || ""),
    _pmWindowCell(c.before, !!c.stage_id),
    _pmWindowCell(c.after, !!c.stage_id) + (c.after_window_ends_at_next_change ? '<br><span class="muted">다음 변경 전까지</span>' : ""),
    '<span class="gate-badge ' + (lv[c.verdict.level] || "") + '">' + esc(c.verdict.label) + "</span>" +
      (c.verdict.note ? '<div class="muted pm-note">' + esc(c.verdict.note) + "</div>" : ""),
    c.revertible ? '<button class="small" data-change-revert="' + c.id + '">되돌리기</button>' : '<span class="muted">—</span>',
  ]);
  return "<h3>변경 기록과 효과</h3>" +
    '<p class="muted">단계 설정을 바꾸거나 배운 점을 켜고 끈 기록입니다. 변경마다 <b>그 전 30일</b>과 <b>그 뒤(다음 변경 전까지, 최대 30일)</b>에 시작한 실행을 같은 기준으로 비교합니다. 양쪽 모두 끝난 실행이 3건 이상일 때만 좋아짐·나빠짐을 판정합니다(다른 요인도 섞일 수 있는 참고값).</p>' +
    '<div class="pm-wide pm-wrap">' + table(["시각", "변경", "누가", "전(30일)", "후", "판정", "동작"], rows,
      { empty: "아직 변경 기록이 없습니다. 파이프라인 관리에서 단계 설정을 바꾸거나 배운 점을 적용하면 여기에 남습니다." }) + "</div>";
}

function _pmBindLearning(content) {
  const act = async (fn, msgEl) => {
    try { await fn(); refresh(); } catch (e) { if (msgEl) msgEl.textContent = "처리하지 못했습니다: " + (e.message || e); }
  };
  content.querySelectorAll("[data-lesson-apply]").forEach(b => b.onclick = () => {
    const box = b.closest(".pm-lesson");
    const text = box.querySelector(".pm-lesson-text").value.trim();
    act(() => api("POST", "/api/process-lessons/" + b.dataset.lessonApply + "/activate", { text }), box.querySelector(".pm-lesson-msg"));
  });
  content.querySelectorAll("[data-lesson-dismiss]").forEach(b => b.onclick = () =>
    act(() => api("POST", "/api/process-lessons/" + b.dataset.lessonDismiss + "/dismiss"), b.closest(".pm-lesson").querySelector(".pm-lesson-msg")));
  content.querySelectorAll("[data-lesson-retire]").forEach(b => b.onclick = () =>
    act(() => api("POST", "/api/process-lessons/" + b.dataset.lessonRetire + "/retire"), null));
  // 되돌리기는 두 번 눌러 확정(브라우저 confirm 창 대신 — 화면 안에서 확인).
  content.querySelectorAll("[data-change-revert]").forEach(b => b.onclick = () => {
    if (b.dataset.confirming !== "1") {
      b.dataset.confirming = "1";
      b.textContent = "정말 되돌리기";
      return;
    }
    act(() => api("POST", "/api/process-changes/" + b.dataset.changeRevert + "/revert"), b.parentElement);
  });
  const add = document.getElementById("pm-lesson-add-btn");
  if (add) add.onclick = () => {
    const text = document.getElementById("pm-lesson-new").value.trim();
    const msg = document.getElementById("pm-lesson-add-msg");
    if (text.length < 4) { msg.textContent = "네 글자 이상 적어 주세요."; return; }
    act(() => api("POST", "/api/process-metrics/stages/" + document.getElementById("pm-lesson-stage").value + "/lessons", { text }), msg);
  };
}
