/* ---------- 관리자 ---------- */

// 조직 정책 — 사번 형식(2026-11-21). 정규식은 서버(Python re)가 전체 일치로 검사한다.
function orgPolicyFormHtml() {
  const p = ORG_POLICY.employee_id || {};
  return '<section class="card"><h2>조직 정책 — 사번 형식</h2>' +
    '<p class="muted">사번 입력 검사에 쓰는 형식(정규식, 전체 일치)과 입력란에 보여 줄 설명입니다. ' +
    '비우고 저장하면 기본값(숫자 8~12자리)으로 돌아갑니다. 이미 등록된 사번은 바뀌지 않으며, 새 형식에 맞지 않는 건수를 알려 드립니다.</p>' +
    '<form class="inline" id="org-policy-form">' +
    '<input id="op-pattern" placeholder="정규식(예: ^EMP-\\d{6}$)" value="' + escAttr(p.is_default ? "" : (p.pattern || "")) + '" style="width:260px">' +
    '<input id="op-hint" placeholder="설명(예: EMP- 뒤 숫자 6자리)" value="' + escAttr(p.is_default ? "" : (p.hint || "")) + '" style="width:240px">' +
    '<button class="primary" type="submit">저장</button>' +
    '<span class="muted">현재: ' + esc(p.hint || "숫자 8~12자리") + (p.is_default ? " (기본값)" : " · " + esc(p.pattern || "")) + '</span>' +
    '</form>' +
    // 결과물 언어(2026-11-22, 하드코딩·스텁 점검 4순위)
    (() => {
      const lang = ORG_POLICY.output_language || { code: "ko", options: [{ code: "ko", name: "한국어" }] };
      return '<h3>결과물 언어</h3>' +
        '<p class="muted">에이전트가 결과물을 쓰는 언어입니다. 한국어가 아니면 모든 에이전트에 그 언어로 쓰라고 지시하고, ' +
        '한국어 전용 자동 교정(한자·가나 등을 한국어로 바꾸는 기능)은 꺼집니다.</p>' +
        '<form class="inline" id="output-language-form"><select id="op-language">' +
        lang.options.map(o => '<option value="' + escAttr(o.code) + '"' + (o.code === lang.code ? " selected" : "") + ">" + esc(o.name) + "</option>").join("") +
        '</select><button class="primary" type="submit">저장</button></form>';
    })() +
    '</section>';
}

// 기본 구성 점검(2026-11-23, P2-10 — `config_check.py`). 운영 DB를 지금 코드의 기본 구성과 비교해
// 빠진 것(단계 참여 역할·Agent 기본 스킬·기본 스킬/도구)은 골라 반영하고, 값이 다른 것은 보여만 준다.
async function renderConfigCheckSection() {
  const box = document.getElementById("config-check-section");
  if (!box) return;
  let items;
  try { items = await api("GET", "/api/admin/config-check"); }
  catch (e) { box.innerHTML = '<h2>기본 구성 점검</h2><p class="muted">' + esc(e.message) + "</p>"; return; }
  const fixable = items.filter(i => i.fixable);
  const rows = items.map(i =>
    "<tr><td>" + (i.fixable ? '<input type="checkbox" class="cfg-item" value="' + escAttr(i.id) + '" checked>' : "") + "</td>" +
    "<td>" + esc(i.target) + "</td><td>" + esc(i.detail) + "</td><td>" + (i.fixable ? "반영 가능" : "직접 확인") + "</td></tr>"
  ).join("");
  box.innerHTML = "<h2>기본 구성 점검</h2>" +
    '<p class="muted">운영 중인 파이프라인·Agent·스킬·도구를 지금 버전의 기본 구성과 비교합니다. 업그레이드 뒤 기본 구성에 추가된 것은 기존 데이터에 자동으로 들어가지 않으므로, 여기서 빠진 것을 골라 반영하세요. 값이 다른 항목은 관리자가 일부러 바꿨을 수 있어 표시만 합니다(고치려면 해당 관리 화면에서 직접).</p>' +
    (items.length
      ? '<div class="table-wrap"><table><thead><tr><th></th><th>대상</th><th>차이</th><th>처리</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
        (fixable.length ? '<button class="primary" id="cfg-apply">선택한 항목 반영</button>' : "")
      : '<p class="muted">기본 구성과 다른 곳이 없습니다.</p>');
  const btn = document.getElementById("cfg-apply");
  if (btn) btn.addEventListener("click", async () => {
    const ids = [...box.querySelectorAll(".cfg-item:checked")].map(c => c.value);
    if (!ids.length) { showMsg("global-msg", "반영할 항목을 고르세요.", "error"); return; }
    try {
      const r = await api("POST", "/api/admin/config-check/apply", { item_ids: ids });
      showMsg("global-msg", r.applied.length + "건을 반영했습니다.", "ok");
      renderConfigCheckSection();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// 평가 기준 설정(2026-11-25 — `evaluation_config.py`, admin). Agent-Evaluator로 결과물을 채점할 때 쓰는 기준을 바꾼다.
// 바꾸면 다음 평가부터 적용된다(이미 기록된 평가는 다시 채점하지 않음).
const _GATE_NAMES = { A: "목표 달성", B: "행동 무결성", C: "신뢰성", D: "성능 계약", E: "보안 경계", F: "다중 Agent 협업", G: "관측성" };
// 모델·파이프라인별 덮어쓰기 행(2026-11-25, P1-10)
function _ecOverrideRowHtml(ov, gateIds, templates) {
  const target = ov.provider ? "provider:" + ov.provider : (ov.pipeline_template_id ? "pipeline:" + ov.pipeline_template_id : "");
  const opts = [["", "대상 선택"], ["provider:ollama", "모델: 로컬(Ollama)"], ["provider:openai", "모델: OpenAI"], ["provider:anthropic", "모델: Anthropic"]]
    .concat((templates || []).map(t => ["pipeline:" + t.id, "파이프라인: " + t.name]));
  return '<tr class="ec-ov-row"><td><select class="ec-ov-target">' +
    opts.map(o => '<option value="' + escAttr(o[0]) + '"' + (o[0] === target ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") +
    "</select></td>" +
    '<td><input type="number" step="0.05" min="0" max="1" class="ec-ov-default" value="' + (ov.gate_default !== undefined ? ov.gate_default : "") + '" placeholder="그대로" style="width:5rem"></td>' +
    gateIds.map(g => '<td><input type="number" step="0.05" min="0" max="1" class="ec-ov-gate" data-gate="' + g + '" value="' +
      ((ov.gates || {})[g] !== undefined ? ov.gates[g] : "") + '" placeholder="-" style="width:4.2rem"></td>').join("") +
    '<td><button type="button" class="small danger ec-ov-del">삭제</button></td></tr>';
}

async function renderEvaluationConfig(content) {
  const [data, ecTemplates] = await Promise.all([
    api("GET", "/api/admin/evaluation-config"),
    api("GET", "/api/pipeline-templates").catch(() => []),
  ]);
  const cfg = data.config, def = data.defaults;
  const ovRows = (cfg.overrides || []).map(ov => _ecOverrideRowHtml(ov, data.gate_ids, ecTemplates)).join("");
  const gateRows = data.gate_ids.map(g =>
    "<tr><td><b>" + g + "</b> " + esc(_GATE_NAMES[g] || "") + "</td>" +
    '<td><input type="number" step="0.05" min="0" max="1" class="ec-gate" data-gate="' + g + '" value="' +
    (cfg.gates[g] !== undefined ? cfg.gates[g] : "") + '" placeholder="기본(' + cfg.gate_default + ')" style="width:7rem"></td></tr>'
  ).join("");
  content.innerHTML =
    '<section class="card"><h2>평가 기준</h2>' +
    '<p class="muted">Agent 결과물을 채점할 때(Gate A-G, 평가 도구 Agent-Evaluator) 쓰는 기준입니다. 저장하면 <b>다음 평가부터</b> 적용되고, 이미 기록된 평가는 다시 채점하지 않습니다. ' +
    'Gate 기준을 바꾸면 결과물의 Gate 배지(통과/미통과), 인박스의 "낮은 Gate 경고", 그 Gate를 판정에 쓰는 파이프라인 단계의 통과 여부가 함께 바뀝니다(기본 템플릿은 검토 단계가 Gate A를 씀).</p>' +
    '<form id="ec-form">' +
    '<h3>Gate별 통과 기준(0~1)</h3>' +
    '<p class="muted">비운 Gate는 기본 기준을 씁니다. 점수가 기준 이상이면 통과입니다.</p>' +
    '<label>기본 기준 <input type="number" step="0.05" min="0" max="1" id="ec-default" value="' + cfg.gate_default + '" style="width:7rem"></label>' +
    '<div class="table-wrap"><table><thead><tr><th>Gate</th><th>통과 기준</th></tr></thead><tbody>' + gateRows + "</tbody></table></div>" +
    '<h3>모델·파이프라인별 덮어쓰기</h3>' +
    '<p class="muted">특정 모델 종류나 파이프라인에서만 다른 통과 기준을 씁니다. 비운 칸은 위 기준 그대로입니다. 우선순위: 위 기준 &lt; 모델 &lt; 파이프라인. ' +
    '모델 덮어쓰기는 그 실행의 AI 호출이 <b>모두</b> 그 종류였을 때만 적용됩니다. 예: 로컬 모델은 호출 한 번이 수십 초~몇 분이라 응답 시간 Gate D가 늘 미달 → "모델: 로컬(Ollama)"의 D를 0으로.</p>' +
    '<div class="table-wrap"><table id="ec-ov-table"><thead><tr><th>대상</th><th>기본 기준</th>' +
    data.gate_ids.map(g => "<th>" + g + "</th>").join("") + "<th></th></tr></thead><tbody>" +
    (ovRows || '<tr class="table-empty"><td colspan="' + (data.gate_ids.length + 3) + '" class="muted">덮어쓰기가 없습니다 — 모든 실행에 위 기준을 씁니다.</td></tr>') +
    "</tbody></table></div>" +
    '<p><button type="button" class="small" id="ec-ov-add">덮어쓰기 추가</button></p>' +
    '<h3>응답 시간 SLA(Gate C·D)</h3>' +
    '<p class="muted">한 번의 AI 호출 기준입니다. 여러 호출로 이루어진 실행(파이프라인 단계·Crew)은 C·D를 재지 않습니다. 로컬 대형 모델이면 넉넉하게 잡으세요.</p>' +
    '<label>p95 <input type="number" min="1" step="1" id="ec-p95" value="' + (cfg.sla_p95_ms / 1000) + '" style="width:7rem"> 초</label> ' +
    '<label>p99 <input type="number" min="1" step="1" id="ec-p99" value="' + (cfg.sla_p99_ms / 1000) + '" style="width:7rem"> 초</label>' +
    '<h3>추론 표지(Gate G)</h3>' +
    '<p class="muted">결과물에 이런 낱말이 있으면 근거를 밝히며 썼다고 봅니다. 평가 도구의 기본 표지(영어 중심, "따라서"·"왜냐하면")에 더해집니다. 한 줄에 하나(최대 30개).</p>' +
    '<textarea id="ec-markers" rows="4" style="width:100%;max-width:28rem">' + esc(cfg.reasoning_markers.join(String.fromCharCode(10))) + "</textarea>" +
    '<h3>도구 범위 위반 감점</h3>' +
    '<p class="muted">허용 범위 밖 도구를 쓰면 해당 점수에서 이만큼 뺍니다(0~1).</p>' +
    '<input type="number" step="0.05" min="0" max="1" id="ec-penalty" value="' + cfg.scope_violation_penalty + '" style="width:7rem">' +
    '<p><button class="primary" type="submit">저장</button> <button type="button" class="small" id="ec-reset">기본값으로 되돌리기</button></p>' +
    "</form>" +
    '<h3>평가 도구 CLI와 맞추기(.aoo/targets.json)</h3>' +
    '<p class="muted">평가 도구의 <code>agent-eval gate</code>가 읽는 목표 파일 형식으로 Gate 기준을 내보내거나 가져옵니다(SLA·추론 표지·모델·파이프라인별 덮어쓰기는 포함되지 않음).</p>' +
    '<p><a class="small" href="/api/admin/evaluation-config/targets.json">targets.json 내려받기</a></p>' +
    '<textarea id="ec-targets" rows="4" style="width:100%;max-width:28rem" placeholder="targets.json 내용을 붙여 넣기"></textarea>' +
    '<p><button type="button" class="small" id="ec-import">Gate 기준 가져오기</button></p>' +
    '<p class="muted">기본값: 모든 Gate ' + def.gate_default + ", SLA p95 " + (def.sla_p95_ms / 1000) + "초·p99 " + (def.sla_p99_ms / 1000) + "초, 감점 " + def.scope_violation_penalty + ".</p>" +
    "</section>";
  const form = document.getElementById("ec-form");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const gates = {};
    form.querySelectorAll(".ec-gate").forEach(inp => { if (inp.value !== "") gates[inp.dataset.gate] = Number(inp.value); });
    try {
      await api("PUT", "/api/admin/evaluation-config", {
        gate_default: Number(document.getElementById("ec-default").value),
        gates,
        sla_p95_ms: Number(document.getElementById("ec-p95").value) * 1000,
        sla_p99_ms: Number(document.getElementById("ec-p99").value) * 1000,
        reasoning_markers: document.getElementById("ec-markers").value,
        scope_violation_penalty: Number(document.getElementById("ec-penalty").value),
        overrides: _ecCollectOverrides(form),
      });
      showMsg("global-msg", "평가 기준을 저장했습니다 — 다음 평가부터 적용됩니다.", "ok");
      renderEvaluationConfig(content);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  const ovBody = document.querySelector("#ec-ov-table tbody");
  const bindOvDelete = () => ovBody.querySelectorAll(".ec-ov-del").forEach(btn => { btn.onclick = () => btn.closest("tr").remove(); });
  bindOvDelete();
  document.getElementById("ec-ov-add").addEventListener("click", () => {
    ovBody.querySelectorAll("tr.table-empty").forEach(r => r.remove());
    ovBody.insertAdjacentHTML("beforeend", _ecOverrideRowHtml({}, data.gate_ids, ecTemplates));
    bindOvDelete();
  });
  document.getElementById("ec-reset").addEventListener("click", async () => {
    try {
      await api("POST", "/api/admin/evaluation-config/reset");
      showMsg("global-msg", "평가 기준을 기본값으로 되돌렸습니다.", "ok");
      renderEvaluationConfig(content);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("ec-import").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(document.getElementById("ec-targets").value || "{}"); }
    catch (e) { showMsg("global-msg", "targets.json 형식(JSON)이 올바르지 않습니다.", "error"); return; }
    try {
      await api("POST", "/api/admin/evaluation-config/targets", parsed);
      showMsg("global-msg", "Gate 기준을 가져왔습니다.", "ok");
      renderEvaluationConfig(content);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

function _ecCollectOverrides(form) {
  const rows = [];
  form.querySelectorAll(".ec-ov-row").forEach(tr => {
    const target = tr.querySelector(".ec-ov-target").value;
    const gates = {};
    tr.querySelectorAll(".ec-ov-gate").forEach(inp => { if (inp.value !== "") gates[inp.dataset.gate] = Number(inp.value); });
    const dv = tr.querySelector(".ec-ov-default").value;
    if (!target && !Object.keys(gates).length && dv === "") return;  // 빈 행은 무시
    const item = { gates };
    if (target.startsWith("provider:")) item.provider = target.slice(9);
    else if (target.startsWith("pipeline:")) item.pipeline_template_id = Number(target.slice(9));
    if (dv !== "") item.gate_default = Number(dv);
    rows.push(item);
  });
  return rows;
}

async function renderAdmin(content) {
  const users = await api("GET", "/api/users");
  content.innerHTML =
    '<section class="card"><h2>사용자 생성</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>4가지 권한(role)은 화면 접근 범위를 결정합니다.</li>' +
      '<li><b>관리자</b>: 전 기능.</li>' +
      '<li><b>매니저</b>: "운영 관리" 대부분에 접근 가능하지만, 사용자 생성/상사 지정·각종 삭제·Agent 온보딩 승인/오프보딩처럼 조직 구조에 직접 영향을 주는 기능과 "모델 관리"(LLM 설정 최적화·모델 레지스트리)는 관리자 전용.</li>' +
      '<li><b>팀원</b>: 업무 요청 제출·결재 요청 등 "내 업무" 화면.</li>' +
      '<li><b>뷰어</b>: 조회만.</li>' +
      '<li>생성 직후엔 상사가 지정 안 돼 있어(아래에서 지정), 이 사용자가 배정을 요청하면 결재가 단일 단계(승인자 미지정)로 처리됩니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="user-form">' +
    '<input id="u-username" placeholder="아이디" required>' +
    '<input id="u-password" type="password" placeholder="비밀번호" required>' +
    '<select id="u-role"><option value="admin">관리자</option><option value="manager">매니저</option>' +
    '<option value="member" selected>팀원</option><option value="viewer">뷰어</option></select>' +
    // 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및 표시되도록
    // UI개선") — human_pool과 같은 선택 입력(숫자 8~12자리).
    '<input id="u-employee-id"' + empIdInputAttrs(true) + ' style="width:160px">' +
    '<button class="primary" type="submit">생성</button></form>' +
    "</section>" +
    orgPolicyFormHtml() +
    // 기본 구성 점검(2026-11-23, P2-10) — 아래 함수가 이 자리를 채운다.
    '<section class="card" id="config-check-section"><h2>기본 구성 점검</h2><p class="muted">불러오는 중…</p></section>' +
    '<section class="card"><h2>사용자 목록</h2>' +
    // 사용자 요청(2026-09-29, "생성과 상사 지정만 가능... (1) 비밀번호
    // 조회/초기화 (2) 사용자 삭제 (3) '동작'의 의미 불명") — 비밀번호는
    // bcrypt 해시라 "조회"는 원리상 불가능하고(평문을 저장하지 않음)
    // "초기화"만 제공한다. "삭제"는 users.id가 audit_log/assignments/
    // approvals/human_pool.user_id에서 NOT NULL로 참조돼 하드 삭제가
    // FK 위반으로 불가능하다(append-only 감사 이력 보존 원칙과 충돌) — Human/Agent
    // Pool과 같은 "비활성화"(계정은 남기고 로그인만 막음) 방식을 쓴다.
    // "동작" 열은 실제로 상사 지정 하나뿐이었으므로 그대로 이름을 바꿨다.
    helpBox(
      '<ul class="muted help-list">' +
      '<li>비밀번호는 bcrypt 해시로 저장돼 "조회"는 할 수 없습니다 — <b>초기화</b>로 새 비밀번호를 지정한 뒤 별도 채널로 전달하세요.</li>' +
      '<li><b>계정 상태</b>는 하드 삭제 대신 비활성화 방식입니다(감사 이력·배정 이력이 이 계정을 참조하고 있어 완전히 지우면 그 이력이 깨집니다) — 비활성화된 계정은 즉시 로그인이 막히고 기존 세션도 끊깁니다.</li>' +
      '<li>상사를 지정하면 그 사람이 배정을 요청할 때 결재가 상사 체인을 따라 다단계로 자동 라우팅됩니다("결재" 탭 참고).</li>' +
      '<li>본인을 직접 상사로 지정하는 것만 막혀 있고, A→B→A 같은 두 단계 이상의 순환은 막지 않습니다.</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및 표시되도록
    // UI개선") — "ID" 열을 사번으로 바꾸고(미입력이면 "#id" 폴백,
    // human_pool과 같은 패턴), 기존 계정에도 "사번 저장" 인라인 입력을
    // 둔다(col-actions-compact라 폭 고정 문제 없음).
    table(["사번", "아이디", "권한", "계정 상태", "상사", "상사 지정", "비밀번호 재설정", "사번 변경", "계정 상태 변경"], users.map(u => {
      const isSelf = u.id === ME.id;
      // 사용자 보고(2026-10-27, 스크린샷 — "상사" 열이 username이 아니라
      // 정수 manager_id를 그대로 보여줌) — 같은 화면에 이미 전체
      // users 배열이 있으니 그 자리에서 이름을 찾는다.
      const managerUser = u.manager_id === null ? null : users.find(m => m.id === u.manager_id);
      return [
        esc(u.employee_id || ("#" + u.id)), esc(u.username), esc(u.role),
        u.is_active ? '<span class="gate-badge gate-ok">활성</span>' : '<span class="gate-badge gate-warn">비활성</span>',
        u.manager_id === null ? "-" : esc(managerUser ? managerUser.username : "#" + u.manager_id),
        '<div class="actions"><span class="pair"><select id="mgr-' + u.id + '" style="width:110px"><option value="">(없음)</option>' +
          users.filter(m => m.id !== u.id).map(m => '<option value="' + m.id + '"' + (m.id === u.manager_id ? " selected" : "") + ">" + esc(m.username) + "</option>").join("") +
        '</select><button class="small" data-set-manager="' + u.id + '">저장</button></span></div>',
        '<div class="actions"><span class="pair"><input type="password" id="pw-' + u.id + '" placeholder="새 비밀번호" style="width:120px">' +
          '<button class="small" data-reset-password="' + u.id + '">초기화</button></span></div>',
        '<div class="actions"><span class="pair"><input id="uempid-' + u.id + '"' + empIdInputAttrs(false) + ' value="' + escAttr(u.employee_id || "") + '" style="width:120px">' +
          '<button class="small" data-set-user-employee-id="' + u.id + '">저장</button></span></div>',
        isSelf
          ? '<span class="muted">본인 계정</span>'
          : (u.is_active
            ? '<button class="small danger" data-set-status="' + u.id + '" data-active="0">비활성화</button>'
            : '<button class="small" data-set-status="' + u.id + '" data-active="1">재활성화</button>'),
      ];
    })) + "</section>";

  // 2026-11-25 수정 — 예전엔 renderAdmin 맨 앞에서 setTimeout(…, 0)으로 불러, /api/users 응답보다 먼저 실행돼
  // 이 칸(config-check-section)이 아직 없어서 그냥 끝나고 "불러오는 중…"으로 멈췄다. 화면을 그린 뒤에 채운다.
  renderConfigCheckSection();
  document.getElementById("output-language-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const res = await api("PUT", "/api/org-policy/output-language", { code: document.getElementById("op-language").value });
      ORG_POLICY.output_language = res;
      showMsg("global-msg", "결과물 언어를 " + res.name + "(으)로 저장했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("org-policy-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const res = await api("PUT", "/api/org-policy", {
        employee_id_pattern: document.getElementById("op-pattern").value,
        employee_id_hint: document.getElementById("op-hint").value,
      });
      ORG_POLICY = Object.assign({}, ORG_POLICY, { employee_id: res.employee_id });
      const n = res.employee_id.existing_mismatched || 0;
      showMsg("global-msg", "사번 형식을 저장했습니다." + (n ? " 이미 등록된 사번 중 " + n + "건이 새 형식에 맞지 않습니다(값은 그대로 둠)." : ""), n ? "warn" : "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  document.getElementById("user-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const employeeIdValue = document.getElementById("u-employee-id").value.trim();
    try {
      await api("POST", "/api/users", Object.assign({
        username: document.getElementById("u-username").value,
        password: document.getElementById("u-password").value,
        role: document.getElementById("u-role").value,
      }, employeeIdValue ? { employee_id: employeeIdValue } : {}));
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
      if (!(await confirmModal("이 계정의 비밀번호를 초기화합니다. 새 비밀번호를 본인에게 안전하게 전달해야 합니다. 계속할까요?"))) return;
      try {
        await api("PATCH", "/api/users/" + btn.dataset.resetPassword + "/password", {
          password: newPassword,
        });
        showMsg("global-msg", "비밀번호를 초기화했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-set-user-employee-id]").forEach(btn => {
    btn.onclick = async () => {
      const input = document.getElementById("uempid-" + btn.dataset.setUserEmployeeId);
      const value = input.value.trim();
      if (!value) { showMsg("global-msg", "사번을 입력하세요.", "error"); return; }
      try {
        await api("PATCH", "/api/users/" + btn.dataset.setUserEmployeeId + "/employee-id", {
          employee_id: value,
        });
        showMsg("global-msg", "사번을 저장했습니다.", "ok");
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
      if (!(await confirmModal(msg))) return;
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

