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

