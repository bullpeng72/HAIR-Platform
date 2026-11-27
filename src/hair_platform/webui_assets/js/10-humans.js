/* ---------- 사람 인력 ---------- */

async function renderHumanPool(content) {
  const [people, skillDatalist, userNames] = await Promise.all([
    api("GET", "/api/human-pool"),
    skillDatalistHtml(),
    api("GET", "/api/users/names").catch(() => []),
  ]);
  const isAdmin = ME.role === "admin";
  // 사용자 보고(2026-10-06, "사람 인력으로 등록되어 있지만 사용자 관리에
  // 등록되지 않은 경우 로그인도 안 돼 일을 할 수 없다") — 등록을 로그인
  // 계정과 무관하게 둬서 매칭·배정·승인이 경고 없이 통과되던 갭을
  // 메운다. 계정은 한 Human Pool 레코드에만 연결 가능하므로(백엔드가
  // 거부), 이미 다른 레코드에 연결된 계정은 "등록 가능한 계정" 목록에서
  // 제외한다.
  const linkedUserIds = new Set(people.filter(p => p.user_id !== null).map(p => p.user_id));
  const usersById = Object.fromEntries(userNames.map(u => [u.id, u]));
  const linkableOptionsHtml = (currentUserId) =>
    '<option value="">— 계정 선택 —</option>' +
    userNames
      .filter(u => u.id === currentUserId || !linkedUserIds.has(u.id))
      .map(u => '<option value="' + u.id + '"' + (u.id === currentUserId ? " selected" : "") +
        '>' + esc(u.username) + " (" + esc(u.role) + ")</option>")
      .join("");

  content.innerHTML =
    '<section class="card"><h2>사람 인력 등록</h2>' +
    '<form class="inline" id="hp-form">' +
    '<select id="hp-user-id" required>' + linkableOptionsHtml(null) + '</select>' +
    '<input id="hp-name" placeholder="이름(표시용, 계정 선택 시 자동 채움)" required>' +
    '<input id="hp-role" placeholder="역할" required>' +
    skillPickerHtml("hp-skills-picker", "hp-skills") +
    skillDatalist +
    '<input id="hp-cost" type="number" step="0.01" placeholder="비용" value="0">' +
    // 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번 적용 예정으로
    // 8자리~12자리 입력 및 표시 가능해야 함") — 아직 전사 사번 체계가
    // 없는 사람이 많아 선택 입력(비워도 등록 가능).
    '<input id="hp-employee-id"' + empIdInputAttrs(true) + ' style="width:160px">' +
    '<button class="primary" type="submit">등록</button></form>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>계정 선택</b>은 "사용자 관리"에서 먼저 만든 로그인 계정 중 아직 다른 사람 인력에 연결 안 된 것만 보여줍니다 — 계정 없이 등록하면 그 사람은 로그인할 수 없어 매칭·배정이 돼도 실제로 업무를 볼 수 없으니, 계정을 먼저 "사용자 관리"에서 만드세요.</li>' +
      '<li><b>사번</b>은 선택 입력입니다 — 비워두면 목록에 "#id"로 표시되고, 나중에 목록의 "사번 저장"으로 언제든 채울 수 있습니다(' + esc(empIdHint()) + ' 형식만 허용, 중복 불가 — 형식은 관리자가 "사용자 관리 → 조직 정책"에서 바꿀 수 있음).</li>' +
      '<li>스킬은 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(여러 개 등록 가능, 칩의 ×로 제거) — 목록에 없는 이름도 자유롭게 추가할 수 있습니다.</li>' +
      '<li><b>비용</b>은 인력 매칭 점수의 25%(또는 성과점수가 없으면 40%)를 차지합니다 — 낮을수록 유리합니다.</li>' +
      '<li>아직 성과 데이터가 없으니 초기엔 "가능" 상태여야 매칭 후보로 잡히고(가용성이 매칭 점수의 40~60%), 스킬은 할 일의 필요 스킬과 겹칠수록 유리합니다.</li>' +
      '</ul>'
    ) +
    '</section><section class="card"><h2>사람 인력 목록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>동의</b>는 성과 리뷰 사전 동의입니다 — 동의 없이는 성과점수를 절대 입력·노출하지 않고, 동의를 철회하면 기존 점수도 즉시 지워집니다.</li>' +
      '<li><b>오프보딩</b>은 가용성을 offboarded로 바꿔 매칭 후보에서 빠지게 하는데, 이 화면에서 다시 되돌리는 버튼은 없습니다(사실상 편도) — 실수로 눌렀다면 관리자가 DB를 직접 고쳐야 합니다.</li>' +
      '<li><b>삭제</b>(관리자 전용)는 오프보딩과 별개로 레코드 자체를 완전히 지웁니다(되돌릴 수 없음) — 확정된(confirmed) 배정이 있으면 삭제가 막힙니다.</li>' +
      '<li><b>계정 연결</b>은 이 사람을 로그인 계정과 이어줍니다 — 연결해야만 그 사람이 "내 업무" 탭에서 자기 업무를 볼 수 있습니다(연결 안 하면 구조적으로 절대 안 보입니다). 드롭다운엔 아직 다른 사람 인력에 연결 안 된 계정만 뜹니다.</li>' +
      '</ul>'
    ) +
    // 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번 적용 예정으로
    // 8자리~12자리 입력 및 표시 가능해야 함") — "ID" 열을 사번으로
    // 바꾸고(미입력이면 Agent/Tool/Skill과 같은 "#id" 폴백), 기존
    // 레코드에도 "사번 저장" 인라인 입력을 둔다(점수입력과 같은 패턴).
    table(["사번", "이름", "역할", "스킬", "가용성", "비용", "동의", "성과점수", "연결된 계정", "동작"], people.map(p => {
      const middleActions = p.availability === "offboarded" ? "" :
        '<button class="small" data-toggle-consent="' + p.id + '" data-consent="' + (p.performance_consent ? "0" : "1") + '">' +
          (p.performance_consent ? "동의 철회" : "동의") + "</button>" +
        '<span class="pair"><input type="number" step="0.01" min="0" max="1" placeholder="점수" id="score-' + p.id + '">' +
        '<button class="small" data-set-score="' + p.id + '">점수입력</button></span>' +
        '<span class="pair"><input' + empIdInputAttrs(false) + ' id="empid-' + p.id + '" value="' + escAttr(p.employee_id || "") + '" style="width:150px">' +
        '<button class="small" data-set-employee-id="' + p.id + '">사번 저장</button></span>' +
        '<span class="pair"><select id="linkuser-' + p.id + '">' + linkableOptionsHtml(p.user_id) + '</select>' +
        '<button class="small" data-link-user="' + p.id + '">계정 연결</button></span>' +
        '<button class="small danger" data-offboard="' + p.id + '">오프보딩</button>';
      const deleteAction = isAdmin ? '<button class="small danger" data-delete-human="' + p.id + '">삭제</button>' : "";
      const linkedAccount = p.user_id === null ? "-" :
        (usersById[p.user_id] ? esc(usersById[p.user_id].username) : "#" + p.user_id);
      return [esc(p.employee_id || ("#" + p.id)), esc(p.name), esc(p.role), esc(p.skill_tags.join(", ")), esc(label(p.availability)), p.cost_estimate,
        p.performance_consent ? "동의함" : "미동의",
        p.performance_score === null ? "-" : p.performance_score,
        linkedAccount,
        '<div class="actions">' + middleActions + deleteAction + "</div>"];
    })) + "</section>" + '<div id="rm-load-slot"></div>';
  _renderResourceLoad(content.querySelector("#rm-load-slot"));

  const skillPicker = initSkillPicker("hp-skills-picker", "hp-skills", []);
  document.getElementById("hp-user-id").addEventListener("change", (ev) => {
    const selected = userNames.find(u => String(u.id) === ev.target.value);
    const nameInput = document.getElementById("hp-name");
    if (selected && !nameInput.value.trim()) nameInput.value = selected.username;
  });
  document.getElementById("hp-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    skillPicker.flush();
    const userIdValue = document.getElementById("hp-user-id").value;
    if (!userIdValue) {
      showMsg("global-msg", '계정을 선택하세요 — 없으면 "사용자 관리"에서 먼저 만드세요.', "error");
      return;
    }
    const employeeIdValue = document.getElementById("hp-employee-id").value.trim();
    try {
      await api("POST", "/api/human-pool", Object.assign({
        name: document.getElementById("hp-name").value,
        role: document.getElementById("hp-role").value,
        skill_tags: document.getElementById("hp-skills").value.split(",").map(s => s.trim()).filter(Boolean),
        cost_estimate: Number(document.getElementById("hp-cost").value) || 0,
        user_id: Number(userIdValue),
      }, employeeIdValue ? { employee_id: employeeIdValue } : {}));
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
  content.querySelectorAll("[data-set-employee-id]").forEach(btn => {
    btn.onclick = async () => {
      const input = document.getElementById("empid-" + btn.dataset.setEmployeeId);
      const value = input.value.trim();
      if (!value) { showMsg("global-msg", "사번을 입력하세요.", "error"); return; }
      try {
        await api("PATCH", "/api/human-pool/" + btn.dataset.setEmployeeId, { employee_id: value });
        showMsg("global-msg", "사번을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-link-user]").forEach(btn => {
    btn.onclick = async () => {
      const select = document.getElementById("linkuser-" + btn.dataset.linkUser);
      const userId = Number(select.value);
      if (!Number.isInteger(userId) || userId < 1) {
        showMsg("global-msg", "연결할 계정을 선택하세요.", "error"); return;
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
      if (!(await confirmModal("정말 오프보딩하시겠습니까?"))) return;
      try {
        await api("POST", "/api/human-pool/" + btn.dataset.offboard + "/offboard");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-delete-human]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 사람 인력 레코드를 완전히 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/human-pool/" + btn.dataset.deleteHuman);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

/* ---------- 용량·부하·부재(2026-11-27, Resource Management Phase A — `resource_capacity`) ---------- */
async function _renderResourceLoad(slot) {
  if (!slot) return;
  const [load, absences] = await Promise.all([api("GET", "/api/resource-load"), api("GET", "/api/resource-absences")]);
  const humans = load.resources.filter(r => r.type === "human");
  const agents = load.resources.filter(r => r.type === "agent");
  const today = new Date().toISOString().slice(0, 10);
  const bar = r => {
    const u = r.utilization || 0;
    const cls = u >= 1 ? "rm-over" : u >= 0.67 ? "rm-high" : "";
    return '<span class="pm-share rm-bar ' + cls + '"><span style="width:' + Math.min(100, Math.round(u * 100)) + '%"></span></span> ' +
      (r.active_tasks || 0) + "/" + r.capacity;
  };
  const hRows = humans.map(r => [
    esc(r.name || r.role), esc(r.role), bar(r) + (r.over_capacity ? ' <span class="gate-badge gate-warn">' + ((r.active_tasks || 0) > r.capacity ? "용량 초과" : "꽉 참") + "</span>" : ""),
    r.absent_until ? '<span class="gate-badge gate-warn">부재 ~' + esc(r.absent_until) + "</span>" : (r.absent_from ? esc(r.absent_from) + "부터 부재" : '<span class="muted">-</span>'),
    '<span class="pair"><input type="number" min="1" max="50" id="rm-cap-' + r.id + '" placeholder="' + load.default_capacity + '" value="' + (r.capacity !== load.default_capacity ? r.capacity : "") + '" style="width:4.5rem" aria-label="동시 용량">' +
      '<button class="small" data-rm-cap="' + r.id + '">용량 저장</button></span> ' +
      '<span class="pair"><input type="date" id="rm-from-' + r.id + '" value="' + today + '" aria-label="부재 시작"><input type="date" id="rm-to-' + r.id + '" value="' + today + '" aria-label="부재 끝">' +
      '<input id="rm-why-' + r.id + '" placeholder="사유" maxlength="200" style="width:6rem"><button class="small" data-rm-abs="' + r.id + '">부재 추가</button></span>',
  ]);
  const aRows = agents.map(r => [
    esc(r.name && r.name !== r.role ? r.name + " (" + r.role + ")" : r.role), String(r.active_tasks || 0),
    r.queue_wait_seconds ? "약 " + Math.max(1, Math.round(r.queue_wait_seconds / 60)) + "분" : '<span class="muted">-</span>',
    r.performance_score === null || r.performance_score === undefined ? '<span class="muted">근거 부족</span>'
      : r.performance_score.toFixed(2) + '<div class="muted pm-note">' + esc(r.performance_source || "") + "</div>",
  ]);
  const absRows = absences.map(a => [esc(a.human_name), esc(a.starts_on) + " ~ " + esc(a.ends_on), esc(a.reason || ""),
    '<button class="small danger" data-rm-del="' + a.id + '">지우기</button>']);
  slot.innerHTML = '<section class="card"><h2>용량·부하·부재</h2>' +
    helpBox('<ul class="muted help-list">' +
      "<li><b>진행 중/용량</b> = 확정 배정 중 아직 끝나지 않은 할 일 수 / 동시에 맡을 수 있는 할 일 수(비우면 기본 " + load.default_capacity + "). 매칭은 남은 용량이 많을수록 가용성 점수를 높게 주고, 꽉 찬 사람은 후보 뒤로 보내며 '용량 초과'로 표시합니다.</li>" +
      "<li><b>부재</b>(휴가·출장·교육 등)를 적어 두면 그 기간에는 매칭에서 '불가'가 되고, 2주 안에 시작하는 부재는 후보 옆에 미리 알려 줍니다. 구성원은 \"내 업무\"에서 자기 부재를 직접 적을 수 있습니다.</li>" +
      "<li>배정 요청은 막지 않습니다 — 용량을 넘기거나 부재 중인 사람에게 요청하면 경고가 뜨고 활동 기록(<code>assignment_requested_with_warning</code>)에 남습니다.</li>" +
      "<li><b>Agent</b>는 맡은 할 일 수와, 로컬 모델(Ollama)을 쓰면 지금 맡길 때 예상 대기가 매칭에 반영됩니다. 적합도는 관리자 입력이 없으면 목표 달성 평균과 사람이 그 Agent 결과물을 승인한 비율로 자동 계산합니다(근거가 모자라면 비움).</li>" +
      "</ul>") +
    "<h3>사람</h3>" + '<div class="pm-wide">' + table(["이름", "역할", "진행 중/용량", "부재", "설정"], hRows, { empty: "등록된 사람이 없습니다." }) + "</div>" +
    "<h3>예정된 부재</h3>" + table(["이름", "기간", "사유", ""], absRows, { empty: "예정된 부재가 없습니다." }) +
    '<details class="rm-agents"><summary><h3 style="display:inline">Agent ' + agents.length + "개</h3> " +
      '<span class="muted">맡은 할 일 ' + agents.reduce((a, r) => a + (r.active_tasks || 0), 0) + "건 · 자동 적합도 근거 있음 " +
      agents.filter(r => r.performance_score !== null && r.performance_score !== undefined).length + "개</span></summary>" +
      '<div class="pm-wide-sm">' + table(["Agent", "맡은 할 일", "모델 대기", "적합도"], aRows, { empty: "온보딩된 Agent가 없습니다." }) + "</div></details>" +
    '<p class="muted" id="rm-msg"></p></section>';
  const msg = t => { slot.querySelector("#rm-msg").textContent = t; };
  slot.querySelectorAll("[data-rm-cap]").forEach(b => b.onclick = async () => {
    const raw = slot.querySelector("#rm-cap-" + b.dataset.rmCap).value.trim();
    try { await api("PUT", "/api/human-pool/" + b.dataset.rmCap + "/capacity", { max_concurrent_tasks: raw === "" ? null : Number(raw) }); _renderResourceLoad(slot); }
    catch (e) { msg("저장하지 못했습니다: " + (e.message || e)); }
  });
  slot.querySelectorAll("[data-rm-abs]").forEach(b => b.onclick = async () => {
    const id = b.dataset.rmAbs;
    try {
      await api("POST", "/api/human-pool/" + id + "/absences", { starts_on: slot.querySelector("#rm-from-" + id).value,
        ends_on: slot.querySelector("#rm-to-" + id).value, reason: slot.querySelector("#rm-why-" + id).value });
      _renderResourceLoad(slot);
    } catch (e) { msg("부재를 추가하지 못했습니다: " + (e.message || e)); }
  });
  slot.querySelectorAll("[data-rm-del]").forEach(b => b.onclick = async () => {
    try { await api("DELETE", "/api/resource-absences/" + b.dataset.rmDel); _renderResourceLoad(slot); }
    catch (e) { msg("지우지 못했습니다: " + (e.message || e)); }
  });
}

