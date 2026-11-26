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

