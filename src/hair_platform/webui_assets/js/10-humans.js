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
      // 인계 점검(2026-11-27, Phase C) — 남은 일이 있으면 먼저 알려 준다.
      let note = "";
      try {
        const h = await api("GET", "/api/human-pool/" + btn.dataset.offboard + "/handover");
        if (h.open_count) note = " 아직 남은 일이 " + h.open_count + "건 있습니다(진행 중 배정 " + h.work.length + " · 팀 " + h.teams.length +
          " · 대기 결재 " + h.approvals.length + " · 보고 대상 " + h.reports.length + " · 자동화 " + h.automations.length + "). 아래 '용량·부하·부재'의 \"인계 점검\"으로 먼저 넘기는 것을 권합니다.";
      } catch (_) { /* 점검 실패해도 오프보딩 확인은 띄운다 */ }
      if (!(await confirmModal("정말 오프보딩하시겠습니까?" + note))) return;
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
      '<span class="pair"><input type="number" min="1" max="80" id="rm-wh-' + r.id + '" placeholder="40" style="width:4.5rem" aria-label="주당 시간">' +
      '<button class="small" data-rm-wh="' + r.id + '">주당 시간</button></span> ' +
      (ME.role === "admin" ? '<span class="pair"><input type="number" min="0" step="1" id="rm-rate-' + r.id + '" placeholder="$/h" style="width:4.5rem" aria-label="시간당 단가(USD)">' +
        '<button class="small" data-rm-rate="' + r.id + '">단가</button></span> ' : "") +
      '<button class="small" data-rm-handover="' + r.id + '">인계 점검</button> ' +
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
    '<p class="muted" id="rm-msg"></p><div id="rm-handover"></div></section>';
  const msg = t => { slot.querySelector("#rm-msg").textContent = t; };
  slot.querySelectorAll("[data-rm-rate]").forEach(b => b.onclick = async () => {
    const raw = slot.querySelector("#rm-rate-" + b.dataset.rmRate).value.trim();
    try { await api("PUT", "/api/human-pool/" + b.dataset.rmRate + "/hourly-rate", { hourly_rate: raw === "" ? null : Number(raw) }); msg("시간당 단가를 저장했습니다(자원 운용의 비용·예산에 반영)."); }
    catch (e) { msg("저장하지 못했습니다: " + (e.message || e)); }
  });
  slot.querySelectorAll("[data-rm-handover]").forEach(b => b.onclick = () => _renderHandover(slot.querySelector("#rm-handover"), Number(b.dataset.rmHandover)));
  slot.querySelectorAll("[data-rm-cap]").forEach(b => b.onclick = async () => {
    const raw = slot.querySelector("#rm-cap-" + b.dataset.rmCap).value.trim();
    try { await api("PUT", "/api/human-pool/" + b.dataset.rmCap + "/capacity", { max_concurrent_tasks: raw === "" ? null : Number(raw) }); _renderResourceLoad(slot); }
    catch (e) { msg("저장하지 못했습니다: " + (e.message || e)); }
  });
  slot.querySelectorAll("[data-rm-wh]").forEach(b => b.onclick = async () => {
    const raw = slot.querySelector("#rm-wh-" + b.dataset.rmWh).value.trim();
    try { await api("PUT", "/api/human-pool/" + b.dataset.rmWh + "/weekly-hours", { weekly_hours: raw === "" ? null : Number(raw) }); msg("주당 시간을 저장했습니다(자원 운용 화면의 활용도에 반영)."); }
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

/* ---------- 자원 운용(2026-11-27, Resource Management Phase B — 7번 활용도, 2번 기한·인원·수요) ---------- */
async function renderResourcePlanning(content) {
  const d = await api("GET", "/api/resource-planning");
  const u = d.utilization, s = u.summary;
  const pct = v => v === null || v === undefined ? "—" : Math.round(v * 100) + "%";
  const heat = c => {
    if (c.ratio === null) return '<td class="rp-cell muted">—</td>';
    const r = c.ratio;
    const cls = r > 1 ? "rp-over" : r >= u.thresholds.high ? "rp-high" : r >= u.thresholds.bench ? "rp-mid" : "rp-low";
    return '<td class="rp-cell ' + cls + '" title="배분 ' + c.allocated_hours + "시간 / 용량 " + c.capacity_hours + "시간 · 할 일 " + c.tasks + "건" +
      (c.absent_days ? " · 부재 " + c.absent_days + "일" : "") + '">' + Math.round(r * 100) + "%" +
      '<div class="rp-sub">' + c.allocated_hours + "/" + c.capacity_hours + "h" + (c.absent_days ? " · 부재" + c.absent_days : "") + "</div></td>";
  };
  const weekLabel = w => w.slice(5).replace("-", "/") + "주";
  const heatRows = u.people.map(p => "<tr><th scope=\"row\">" + esc(p.name) + '<div class="muted pm-note">' + esc(p.role) + " · 주 " + p.weekly_hours + "h</div></th>" +
    p.cells.map(heat).join("") +
    "<td>" + (p.overdue_tasks ? '<span class="gate-badge gate-warn">지연 ' + p.overdue_tasks + "</span> " : "") +
    (p.unscheduled_tasks ? '<span class="muted">기한·추정 없음 ' + p.unscheduled_tasks + "건</span>" : "") + "</td></tr>").join("");
  const dl = (list, overdue) => table(["할 일", "역할", "기한", "상태", "맡은 사람·Agent", "예상"], list.map(t => [
    "#" + t.id, esc(t.role_name), esc(t.due_date) + ' <span class="gate-badge ' + (overdue ? "gate-warn" : "gate-info") + '">' +
      (overdue ? (-t.days_left) + "일 지남" : t.days_left === 0 ? "오늘" : t.days_left + "일 남음") + "</span>",
    esc(label(t.status)), t.assignees ? esc(t.assignees) : '<span class="gate-badge gate-warn">배정 없음</span>', t.estimate_hours ? t.estimate_hours + "h" : "-"]),
    { empty: overdue ? "기한이 지난 할 일이 없습니다." : "7일 안에 기한인 할 일이 없습니다." });
  content.innerHTML = '<section class="card"><h2>자원 운용</h2>' +
    helpBox('<ul class="muted help-list">' +
      "<li><b>활용도</b> = 앞으로 4주, 사람마다 주별로 배분된 시간 ÷ 그 주 용량. 끝나지 않은 할 일의 <b>예상 시간</b>을 배정된 사람 수로 나눠 오늘부터 <b>기한</b>까지 평일에 고르게 놓습니다. 기한이 지난 일은 이번 주에 모읍니다. 용량은 <b>주당 시간</b>(사람 인력 관리, 기본 40)에서 부재일만큼 뺍니다.</li>" +
      "<li>기한이나 예상 시간이 없는 할 일은 달력에 놓지 않고 <b>기한·추정 없음</b>으로 따로 셉니다 — 협업 워크스페이스에서 기한·예상 시간을 넣으면 반영됩니다.</li>" +
      "<li>색: 초록 30% 미만(여유) · 파랑 30~85% · 주황 85% 이상 · 빨강 100% 초과. 칸에 마우스를 올리면 시간·할 일 수가 보입니다.</li>" +
      "<li><b>역할별 수요</b>의 '최근 4주 시작'은 처음 배정 요청·파이프라인 실행이 4주 안인 할 일 수(할 일에는 만든 시각이 없어 시작 기준), '정기 실행 예정'은 앞으로 4주 정기 실행이 만들 할 일 수입니다.</li>" +
      "</ul>") +
    '<div class="pm-tiles">' +
      _rpTile("평균 활용도(4주)", pct(s.avg_utilization), "사람 " + s.people + "명") +
      _rpTile("용량 초과", String(s.over_allocated_cells), "사람·주 칸 수(100% 초과)") +
      _rpTile("지연", String(d.deadlines.overdue.length), "기한 지난 할 일") +
      _rpTile("7일 안 기한", String(d.deadlines.due_soon.length), "임박한 할 일") +
      _rpTile("인원 부족", String(d.headcount_gaps.length), "필요 인원보다 배정이 적은 할 일") +
      _rpTile("여유 인력", String(s.bench.length), s.bench.length ? esc(s.bench.slice(0, 4).join(", ")) + (s.bench.length > 4 ? " 외" : "") : "앞으로 2주 30% 미만") +
    "</div>" +
    "<h3>사람별 활용도(앞으로 4주)</h3>" +
    '<div class="table-wrap rp-wrap"><table class="rp-heat"><thead><tr><th>사람</th>' + u.weeks.map(w => "<th>" + weekLabel(w) + "</th>").join("") +
      "<th>참고</th></tr></thead><tbody>" + (heatRows || '<tr><td colspan="6" class="muted">등록된 사람이 없습니다.</td></tr>') + "</tbody></table></div>" +
    "<h3>기한 지남</h3>" + '<div class="pm-wide-sm">' + dl(d.deadlines.overdue, true) + "</div>" +
    "<h3>7일 안 기한</h3>" + '<div class="pm-wide-sm">' + dl(d.deadlines.due_soon, false) + "</div>" +
    "<h3>인원 부족</h3>" + '<div class="pm-wide-sm">' + table(["할 일", "역할", "필요", "확정", "요청 중", "부족", "기한"], d.headcount_gaps.map(g => [
      "#" + g.id, esc(g.role_name), String(g.headcount || 1), String(g.assigned), String(g.requested), "<b>" + g.short + "</b>", g.due_date ? esc(g.due_date) : "-"]),
      { empty: "인원이 모자란 할 일이 없습니다." }) + "</div>" +
    "<h3>역할별 수요와 공급</h3>" + '<div class="pm-wide-sm">' + table(["역할", "열린 할 일", "배정 안 됨", "예상 시간 합", "최근 4주 시작(주당)", "정기 실행 예정(4주)", "가용 사람", "Agent"], d.demand.map(r => [
      esc(r.role) + (r.no_supply ? ' <span class="gate-badge gate-warn">맡을 자원 없음</span>' : ""), String(r.open), r.unassigned ? "<b>" + r.unassigned + "</b>" : "0",
      r.open_hours ? r.open_hours + "h" : "-", r.recent_per_week ? r.recent_per_week.toFixed(1) : "0", String(r.scheduled_next), String(r.people), String(r.agents)]),
      { empty: "열린 할 일이 없습니다." }) + "</div>" +
    '<div id="rp-cost-slot"></div>' +
    "</section>";
  _renderResourceCosts(content.querySelector("#rp-cost-slot"));
}

/* ---------- 비용·예산(2026-11-27, Resource Management Phase C — 5번 시간 기반 비용) ---------- */
async function _renderResourceCosts(slot) {
  if (!slot) return;
  const c = await api("GET", "/api/resource-costs");
  const st = { over: '<span class="gate-badge gate-warn sys-error">초과</span>', warn: '<span class="gate-badge gate-warn">주의</span>', ok: '<span class="gate-badge gate-ok">여유</span>' };
  const money = v => "$" + (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(2));
  slot.innerHTML = "<h3>비용·예산</h3>" +
    '<p class="muted">업무 요청마다 <b>사람 인건비(추정)</b> = 배정된 사람의 시간당 단가 × 그 사람 몫의 예상 시간(할 일 예상 시간 ÷ 배정 인원), <b>AI(실측)</b> = 그 할 일들로 쓴 실제 AI 사용 금액(모델 레지스트리 단가). 예산을 넣으면 ' +
    Math.round(c.budget_warn * 100) + "% 이상 주의, 넘으면 초과로 표시합니다." +
    (c.people_missing_rate ? " 시간당 단가가 없는 사람 " + c.people_missing_rate + "명 — '사람 인력 관리'에서 admin이 넣으면 인건비에 들어갑니다." : "") + "</p>" +
    '<div class="pm-wide">' + table(["업무 요청", "할 일", "인건비(추정)", "AI(실측)", "합계", "예산", "상태", "설정"], c.briefs.map(b => [
      "#" + b.brief_id + " " + esc(b.title), String(b.tasks),
      money(b.labor_estimated) + (b.missing_rate || b.missing_estimate ? '<div class="muted pm-note">' + (b.missing_rate ? "단가 없음 " + b.missing_rate : "") +
        (b.missing_rate && b.missing_estimate ? " · " : "") + (b.missing_estimate ? "예상 시간 없음 " + b.missing_estimate : "") + "</div>" : ""),
      money(b.ai_cost) + (b.ai_partial ? '<div class="muted pm-note">단가 없는 모델 포함</div>' : ""),
      "<b>" + money(b.total) + "</b>", b.budget_usd ? money(b.budget_usd) + '<div class="muted pm-note">' + Math.round(b.ratio * 100) + "%</div>" : '<span class="muted">-</span>',
      b.status ? st[b.status] : "-",
      '<span class="pair"><input type="number" min="1" step="1" id="rp-bud-' + b.brief_id + '" placeholder="USD" style="width:5.5rem" aria-label="예산(USD)"><button class="small" data-rp-bud="' + b.brief_id + '">예산</button></span>',
    ]), { empty: "비용이 생긴 업무 요청이 없습니다." }) + "</div>" +
    (c.teams.length ? "<h4>팀별 비용</h4>" + table(["팀", "할 일", "인건비(추정)", "AI(실측)"], c.teams.map(t => [esc(t.name), String(t.tasks), money(t.labor_estimated), money(t.ai_cost)])) : "") +
    '<p class="muted" id="rp-cost-msg"></p>';
  slot.querySelectorAll("[data-rp-bud]").forEach(b => b.onclick = async () => {
    const raw = slot.querySelector("#rp-bud-" + b.dataset.rpBud).value.trim();
    try { await api("PUT", "/api/briefs/" + b.dataset.rpBud + "/budget", { budget_usd: raw === "" ? null : Number(raw) }); _renderResourceCosts(slot); }
    catch (e) { slot.querySelector("#rp-cost-msg").textContent = e.message || String(e); }
  });
}

function _rpTile(label, value, sub) {
  return '<div class="pm-tile"><div class="pm-tile-label">' + esc(label) + '</div><div class="pm-tile-value">' + value +
    '</div><div class="pm-tile-sub muted">' + sub + "</div></div>";
}

/* ---------- 인계 점검(2026-11-27, Resource Management Phase C — 퇴사·장기 부재 전에) ---------- */
async function _renderHandover(box, humanId) {
  const h = await api("GET", "/api/human-pool/" + humanId + "/handover");
  const users = await api("GET", "/api/approval-delegations").then(d => d.candidates).catch(() => []);
  const workRows = h.work.map(w => [
    "#" + w.task_id, esc(w.role_name), esc(label(w.status)) + (w.due_date ? '<div class="muted pm-note">기한 ' + esc(w.due_date) + "</div>" : ""),
    w.suggestion ? esc(w.suggestion.name) + ' <span class="muted">(' + esc(label(w.suggestion.type)) + " · " + w.suggestion.score + ")</span>" +
      ((w.suggestion.notes || []).length ? '<div class="muted pm-note">' + w.suggestion.notes.map(esc).join(" · ") + "</div>" : "")
      : (w.status === "requested" ? '<span class="muted">요청 중 — 결재함에서 반려하면 끝납니다</span>' : '<span class="muted">맞는 후보 없음</span>'),
    w.suggestion ? '<button class="small" data-ho-replace="' + w.assignment_id + '" data-task="' + w.task_id + '" data-type="' + w.suggestion.type + '" data-id="' + w.suggestion.id + '">이 후보로 교체 요청</button>' : "",
  ]);
  box.innerHTML = '<div class="card ab-box"><div class="ab-head"><b>인계 점검 — ' + esc(h.human.name) + "</b> " +
    (h.open_count ? '<span class="gate-badge gate-warn">남은 일 ' + h.open_count + "건</span>" : '<span class="gate-badge gate-ok">남은 일 없음</span>') +
    ' <button class="small" id="ho-close">닫기</button></div>' +
    "<h4>맡고 있는 할 일</h4>" + table(["할 일", "역할", "상태", "대신 맡을 후보(매칭 1순위, 본인 제외)", ""], workRows, { empty: "끝나지 않은 배정이 없습니다." }) +
    '<p class="muted">교체 요청은 평소 배정처럼 결재를 거치고, 확정되면 원래 배정을 대신합니다.</p>' +
    "<h4>팀 소속</h4>" + (h.teams.length ? "<ul>" + h.teams.map(t => "<li>" + esc(t.name) + (t.role_label ? " — " + esc(t.role_label) : "") + "</li>").join("") + "</ul><p class=\"muted\">\"팀 관리\"에서 구성원을 바꾸세요.</p>" : '<p class="muted">없음</p>') +
    "<h4>이 사람이 승인자인 대기 결재 " + h.approvals.length + "건</h4>" +
    (h.approvals.length && h.human.user_id ? '<div class="inline"><select id="ho-to"><option value="">admin·manager 누구나</option>' +
      users.filter(u => u.id !== h.human.user_id).map(u => '<option value="' + u.id + '">' + esc(u.username) + "</option>").join("") +
      '</select> <button class="small" id="ho-transfer">모두 넘기기</button></div>' : '<p class="muted">없음</p>') +
    "<h4>보고 대상(이 사람이 상사)</h4>" + (h.reports.length ? "<ul>" + h.reports.map(r => "<li>" + esc(r.name) + "</li>").join("") + "</ul><p class=\"muted\">새 상사는 \"사용자 관리\"·\"사람 인력 관리\"에서 지정하세요(결재 체인이 상사를 따릅니다).</p>" : '<p class="muted">없음</p>') +
    "<h4>이 사람 이름으로 도는 자동화</h4>" + (h.automations.length ? "<ul>" + h.automations.map(a => "<li>" + esc(a.kind) + " — " + esc(a.name) + "</li>").join("") + "</ul><p class=\"muted\">\"내 설정(연동 설정)\"에서 끄거나 다른 사람이 다시 만드세요 — 그대로 두면 퇴사자 이름으로 계속 실행됩니다.</p>" : '<p class="muted">없음</p>') +
    '<p class="muted" id="ho-msg"></p></div>';
  box.querySelector("#ho-close").onclick = () => { box.innerHTML = ""; };
  box.querySelectorAll("[data-ho-replace]").forEach(b => b.onclick = async () => {
    try {
      const r = await api("POST", "/api/tasks/" + b.dataset.task + "/assignments", { resource_type: b.dataset.type,
        resource_id: Number(b.dataset.id), replaces_assignment_id: Number(b.dataset.hoReplace) });
      box.querySelector("#ho-msg").textContent = "교체 요청을 보냈습니다" + (r.status === "confirmed" ? "(바로 확정)" : " — 결재함에서 처리하세요") + ".";
      b.disabled = true;
    } catch (e) { box.querySelector("#ho-msg").textContent = e.message || String(e); }
  });
  const tr = box.querySelector("#ho-transfer");
  if (tr) tr.onclick = async () => {
    const to = box.querySelector("#ho-to").value;
    try {
      const r = await api("POST", "/api/users/" + h.human.user_id + "/transfer-approvals", { to_user_id: to ? Number(to) : null });
      box.querySelector("#ho-msg").textContent = "대기 결재 " + r.transferred + "건을 넘겼습니다.";
      _renderHandover(box, humanId);
    } catch (e) { box.querySelector("#ho-msg").textContent = e.message || String(e); }
  };
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

