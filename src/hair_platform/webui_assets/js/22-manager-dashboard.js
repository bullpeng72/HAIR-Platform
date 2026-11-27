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

