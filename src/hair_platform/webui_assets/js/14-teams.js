/* ---------- 팀(사용자 요청, 2026-10-06, "복수의 사람이 복수의 Agent와
   또는 CrewAI 장점인 역할에 따른 Agent를 묶어 생성된 그룹과 협업하도록...
   다시 기획"). Phase 1+2 —
   human_pool/agent_pool 멤버를 역할명과 함께 묶어 등록하는 "등록→재사용"
   화면(tool_pool/skill_pool/model_registry와 같은 패턴). 팀 자체를 할 일에
   배정하는 동작("배정 요청")은 이 탭이 아니라 "업무 요청" 탭의 할 일 상세
   패널에서 한다(팀 정의와 배정은 관심사가 다르다 — teams.py 참고). */

async function renderTeams(content) {
  const [teams, people, agents, pipelineTemplates] = await Promise.all([
    api("GET", "/api/teams"),
    api("GET", "/api/human-pool"),
    api("GET", "/api/agent-pool"),
    api("GET", "/api/pipeline-templates").catch(() => []),
  ]);
  const isAdmin = ME.role === "admin";
  const memberOptionsHtml =
    '<option value="">멤버 선택</option>' +
    '<optgroup label="사람">' +
    people.map(p => '<option value="human:' + p.id + '">' + esc(p.name) + '(' + esc(p.role) + ')</option>').join("") +
    '</optgroup>' +
    '<optgroup label="Agent">' +
    agents.map(a => '<option value="agent:' + a.id + '">' + agentDisplayLabel(a) + '(#' + a.id + ')</option>').join("") +
    '</optgroup>';

  // 사용자 요청(2026-10-20, "고급 에이전트 관리") — 지금까지 "팀 Agent
  // 실행"을 누를 때마다 process·매니저를 매번 새로 골라야 했다. 매니저
  // 후보는 이 팀의 온보딩(active)된 Agent 멤버로만 좁힌다(서버도
  // `teams.update_team_defaults()`에서 같은 제약을 검증한다).
  // 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 재정렬
  // API(`PATCH .../members/order`)는 `team_members.id`(행 id)를 받지
  // `agent_pool.id`를 받지 않는다 — 캔버스가 둘 다 들고 있어야 해서
  // `agent_pool` 필드에 `teamMemberRowId`를 덧붙여 돌려준다.
  const activeAgentMembersOf = (t) => t.members
    .filter(m => m.member_type === "agent")
    .map(m => {
      const a = agents.find(x => x.id === m.member_id);
      return a && a.status === "active"
        ? Object.assign({}, a, { teamMemberRowId: m.id })
        : null;
    })
    .filter(Boolean);

  const teamDefaultsFormHtml = (t) => {
    const activeAgentMembers = activeAgentMembersOf(t);
    // 사용자 요청(2026-11-19, "CrewAI hierarchical 모드 신규 팀 생성
    // 차단") — 이미 hierarchical인 팀(이미 매니저가 지정됐거나
    // default_process가 이미 hierarchical)은 계속 쓸 수 있지만, 새로
    // hierarchical로 전환하는 건 서버가 막는다(`teams.update_team_
    // defaults()` 참고) — 백엔드가 400으로 거부하기 전에 UI에서도
    // 같은 경계를 보여준다.
    const alreadyHierarchical = t.default_process === "hierarchical" || t.default_manager_agent_id !== null;
    const hierarchicalDisabledAttr = alreadyHierarchical ? "" : " disabled";
    const hierarchicalTitle = alreadyHierarchical
      ? ""
      : ' title="CrewAI hierarchical 모드의 신뢰성 문제로 새 팀은 더는 전환할 수 없습니다 — sequential_relay 기반 파이프라인을 쓰세요"';
    return '<form class="inline" data-team-defaults-form="' + t.id + '" style="margin-top:0.5rem;flex-wrap:wrap">' +
      '<label>기본 process ' +
      '<select data-default-process>' +
      '<option value="">(미지정)</option>' +
      '<option value="sequential"' + (t.default_process === "sequential" ? " selected" : "") + '>sequential</option>' +
      '<option value="hierarchical"' + (t.default_process === "hierarchical" ? " selected" : "") + hierarchicalDisabledAttr + hierarchicalTitle + '>hierarchical' + (alreadyHierarchical ? "" : "(신규 전환 불가)") + '</option>' +
      '</select></label>' +
      '<label' + hierarchicalTitle + '>기본 매니저 ' +
      '<select data-default-manager' + (activeAgentMembers.length && alreadyHierarchical ? "" : " disabled") + '>' +
      '<option value="">(미지정 — 자동 추정)</option>' +
      activeAgentMembers.map(a => '<option value="' + a.id + '"' +
        (t.default_manager_agent_id === a.id ? " selected" : "") + '>' + agentDisplayLabel(a) + " (#" + a.id + ")</option>").join("") +
      '</select></label>' +
      '<label>연결 파이프라인 ' +
      '<select data-default-pipeline>' +
      '<option value="">(미지정 — @멘션 시 단발 실행)</option>' +
      pipelineTemplates.map(p => '<option value="' + p.id + '"' +
        (t.pipeline_template_id === p.id ? " selected" : "") + '>' + esc(p.name) + '</option>').join("") +
      '</select></label>' +
      '<button class="small" type="submit">기본값 저장</button></form>';
  };

  // 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 기존
  // 멤버 표(CRUD)는 전혀 안 건드리고, 그 위에 "구조 보기/순서·매니저
  // 편집" 캔버스를 추가로 얹는다. Human 멤버는 CrewAI 실행 그래프에
  // 포함되지 않으므로(`active_agent_member_ids()`) 캔버스 노드가
  // 아니라 별도 띠로만 보여준다(거짓 없는 시각화).
  const teamCardHtml = (t) => {
    const activeAgentMembers = activeAgentMembersOf(t);
    const humanMembers = t.members.filter(m => m.member_type === "human");
    const humanStripHtml = humanMembers.length
      ? '<p class="muted">결재/참여 인력: ' +
        humanMembers.map(m => esc(m.member_name || ("#" + m.member_id)) +
          (m.role_label ? "(" + esc(m.role_label) + ")" : "")).join(", ") +
        '</p>'
      : "";
    // 사용자 보고(2026-10-21, "노드 크기 변경 기능... 지금 노드 크기
    // 조절은 안되고 너무 커 모든 노드가 표시되지 않음") — 축소/확대/
    // 전체 보기 버튼을 캔버스 바깥(Drawflow가 관리하는 DOM 트리
    // 밖)에 둬서 Drawflow의 pan/zoom 변환에 영향받지 않게 한다.
    const canvasHtml = activeAgentMembers.length
      ? '<div class="team-canvas-wrap" style="margin-bottom:0.6rem">' +
        '<div class="team-canvas-toolbar" style="margin-bottom:0.3rem">' +
        '<button type="button" class="small" data-canvas-zoom-out="' + t.id + '">축소 −</button> ' +
        '<button type="button" class="small" data-canvas-zoom-in="' + t.id + '">확대 +</button> ' +
        '<button type="button" class="small" data-canvas-zoom-fit="' + t.id + '">전체 보기</button>' +
        '</div>' +
        '<div class="team-canvas" id="team-canvas-' + t.id + '" style="height:260px;border:1px solid var(--color-border);border-radius:6px;background:#fafbfc;overflow:hidden"></div>' +
        '<p class="muted" style="margin:0.3rem 0 0">노드를 좌우로 드래그하면 순차 릴레이 순서가 바뀝니다(hierarchical에서는 순서가 의미 없어 드래그가 반영되지 않습니다). 노드의 "매니저로 지정" 버튼으로 매니저를 바꿀 수 있습니다. Ctrl+스크롤(모바일은 두 손가락)로도 확대/축소할 수 있습니다.</p>' +
        '</div>'
      : '<p class="muted">온보딩(active)된 Agent 멤버가 있어야 워크플로우를 미리 볼 수 있습니다.</p>';
    // 사용자 요청(2026-10-29, "Team 온보딩 라이프사이클") — 상태 배지는
    // agent_pool에서 쓰던 라벨을 그대로 재사용한다(LABELS에 이미 있음).
    const statusBadgeClass = t.status === "active" ? "" : " gate-badge gate-warn";
    const linkedTaskHtml = (t.task_id || t.brief_id)
      ? '<p class="muted">연결된 업무: ' +
        (t.task_id ? "Task #" + t.task_id : "") +
        (t.task_id && t.brief_id ? ", " : "") +
        (t.brief_id ? "Brief #" + t.brief_id : "") +
        (t.completed_at ? " (미션 완료: " + esc(fmtTime(t.completed_at)) + ")" : "") +
        '</p>'
      : "";
    const lifecycleButtonsHtml =
      (t.status === "pending_onboarding"
        ? '<button class="small" data-onboard-team="' + t.id + '">온보딩 시작</button>'
        : "") +
      (t.status === "active" && (t.task_id || t.brief_id)
        ? '<button class="small" data-complete-mission="' + t.id + '">미션 완료</button>'
        : "");
    // 사용자 요청(2026-10-29, 개선 계획 Phase 3 "조직도 용어 재정리") —
    // 기존 "조직도" 메뉴(상사 체인 뷰)는 그대로 두고 이름도 안 바꾼다
    // (혼란 위험이 더 크다는 판단) — 대신 "TF팀"이라는 용어가 실제로
    // 쓰이는 자리인 여기서 배지로 명확히 구분한다. task_id/brief_id가
    // 있으면 범용 재사용 팀이 아니라 "TF팀"임을 바로 보여준다.
    const tfTeamBadgeHtml = (t.task_id || t.brief_id)
      ? ' <span class="gate-badge" title="업무에 종속된 조직 — 기존 조직도 메뉴(상사 체인 뷰)와는 다른 개념입니다">TF팀</span>'
      : "";
    return '<section class="card" data-team-card="' + t.id + '">' +
      '<h3>#' + t.id + " " + esc(t.name) + tfTeamBadgeHtml + ' <span class="gate-badge' + statusBadgeClass + '">' + esc(label(t.status)) + '</span></h3>' +
      (t.description ? '<p class="muted">' + esc(t.description) + '</p>' : "") +
      linkedTaskHtml +
      humanStripHtml +
      canvasHtml +
      table(["타입", "멤버", "역할 라벨", "동작"], t.members.map(m => [
        esc(label(m.member_type)), esc(m.member_name || ("#" + m.member_id)), esc(m.role_label),
        '<button class="small danger" data-remove-member="' + t.id + ':' + m.id + '">제외</button>',
      ])) +
      '<form class="inline" data-add-member-form="' + t.id + '">' +
      '<select data-member-select required style="min-width:180px">' + memberOptionsHtml + '</select>' +
      '<input data-role-label placeholder="역할 라벨(선택)" style="width:140px">' +
      '<button class="small" type="submit">멤버 추가</button></form>' +
      teamDefaultsFormHtml(t) +
      '<div class="actions" style="margin-top:0.5rem">' +
      lifecycleButtonsHtml +
      // 버그 수정(2026-11-12, 이용 가이드 검증 중 발견) — 이 범용 토글이
      // `is_active`만 바꾸고 `status`는 그대로 둔다(teams.set_team_active()
      // 참고). pending_onboarding 팀에서 이 버튼을 누르면 "활성화" 한
      // 클릭으로 온보딩 절차(data-onboard-team) 없이 is_active=1이 돼
      // @멘션·배정 후보 쿼리(`WHERE is_active = 1`만 봄)에 바로 노출된다 —
      // "온보딩 전엔 호출 후보에 안 뜬다"는 가이드의 전제를 깨는 실제
      // 우회 경로였다. pending_onboarding 동안은 숨기고 "온보딩 시작"
      // 버튼(위 lifecycleButtonsHtml)만 쓰게 한다 — 그 버튼은 status와
      // is_active를 항상 함께 맞춘다.
      (t.status === "pending_onboarding" ? "" :
      '<button class="small" data-toggle-active="' + t.id + '" data-next-active="' + (t.is_active ? "0" : "1") + '">' +
      (t.is_active ? "비활성화" : "활성화") + '</button>') +
      '<button class="small" data-view-team-history="' + t.id + '">실행 이력 보기</button>' +
      (isAdmin ? '<button class="small danger" data-delete-team="' + t.id + '">팀 삭제</button>' : "") +
      // 공장 초기화 시 보호(2026-11-21 설정화 — 예전엔 팀 이름으로만 보호).
      (isAdmin ? '<button class="small" data-toggle-reset-protect="' + t.id + '" data-next="' + (t.protect_on_reset ? "0" : "1") +
        '" title="켜면 공장 초기화(reset-data) 때 이 팀과 멤버 구성을 지우지 않습니다">초기화 보호: ' +
        (t.protect_on_reset ? "켜짐" : "꺼짐") + '</button>' : "") +
      '</div>' +
      '<div id="team-history-' + t.id + '"></div>' +
      '</section>';
  };

  content.innerHTML =
    '<section class="card"><h2>팀 — 복수 사람·Agent 그룹 등록</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>사람(human_pool)·Agent(agent_pool) 멤버를 역할 라벨과 함께 묶어두면, "업무 요청" 탭의 할 일 상세에서 이 팀 전체를 한 할 일에 배정(resource_type="team")할 수 있습니다.</li>' +
    '<li>팀 배정의 결재 주체는 팀 리더(기본값은 배정 요청자, 확정 전까지 변경 가능)입니다 — 배정이 승인되면 팀의 사람 멤버(계정 연결된 경우)·팀 리더 모두 그 할 일의 협업 워크스페이스에 결과물을 쓸 수 있습니다.</li>' +
    '<li>그 워크스페이스의 "팀 Agent 실행" 버튼으로 이 팀의 온보딩된 Agent 멤버 전원을 실제 CrewAI Crew로 함께 실행할 수 있습니다(결과는 Agent별 결과물+결재 체인으로 그대로 쌓입니다).</li>' +
    '<li><b>기본 process/매니저</b>를 저장해두면 "팀 Agent 실행"에서 매번 새로 고르지 않아도 이 값이 기본으로 쓰입니다(그 자리에서 여전히 바꿀 수 있습니다). 단 <b>hierarchical(매니저 위임)은 새로 지정할 수 없습니다</b> — 신뢰성 문제로 막혀 있어, 이미 위임형으로 쓰던 팀만 매니저를 바꾸거나 유지할 수 있습니다. 새 팀은 sequential로 두고, 단계가 필요한 업무는 "연결 파이프라인"을 쓰세요. 새 팀에는 역할 라벨에 "매니저"가 들어간 Agent 멤버도 추가할 수 없습니다(매니저로 인식돼 위임형이 되기 때문).</li>' +
    '<li>Agent를 이 탭에서 바로 만들려면 "AI 에이전트 생성"에서 여러 할 일을 한 번에 골라 생성한 뒤 그 결과 화면의 "팀으로 묶기"를 쓰는 게 더 빠릅니다.</li>' +
    '<li>Task ID/Brief ID를 지정해 만들면 "TF팀"(그 업무에 종속된 팀)이 됩니다 — 생성 즉시 "온보딩 대기" 상태로 시작해, "온보딩 시작"을 눌러야 @멘션/배정 후보에 노출됩니다. 비워두면 기존처럼 바로 쓸 수 있는 범용 팀이 됩니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="team-create-form">' +
    '<input id="team-name" placeholder="팀 이름" required>' +
    '<input id="team-description" placeholder="설명(선택)" style="flex:1;min-width:160px">' +
    '<input id="team-task-id" type="number" min="1" step="1" placeholder="Task ID(선택 — TF팀)" style="width:170px">' +
    '<input id="team-brief-id" type="number" min="1" step="1" placeholder="Brief ID(선택 — TF팀)" style="width:170px">' +
    '<button class="primary" type="submit">팀 등록</button></form>' +
    "</section>" +
    (teams.length ? teams.map(teamCardHtml).join("") : '<p class="muted">등록된 팀이 없습니다.</p>');

  document.getElementById("team-create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const taskIdVal = document.getElementById("team-task-id").value;
    const briefIdVal = document.getElementById("team-brief-id").value;
    try {
      await api("POST", "/api/teams", {
        name: document.getElementById("team-name").value,
        description: document.getElementById("team-description").value,
        task_id: taskIdVal ? Number(taskIdVal) : null,
        brief_id: briefIdVal ? Number(briefIdVal) : null,
      });
      showMsg("global-msg", "팀을 등록했습니다.", "ok");
      refresh();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-add-member-form]").forEach(form => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const teamId = form.dataset.addMemberForm;
      const sel = form.querySelector("[data-member-select]").value;
      if (!sel) { showMsg("global-msg", "멤버를 선택하세요.", "error"); return; }
      const [memberType, memberId] = sel.split(":");
      const roleLabel = form.querySelector("[data-role-label]").value;
      try {
        await api("POST", "/api/teams/" + teamId + "/members", {
          member_type: memberType, member_id: Number(memberId), role_label: roleLabel,
        });
        showMsg("global-msg", "멤버를 추가했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  content.querySelectorAll("[data-team-defaults-form]").forEach(form => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const teamId = form.dataset.teamDefaultsForm;
      const processVal = form.querySelector("[data-default-process]").value;
      const managerVal = form.querySelector("[data-default-manager]").value;
      const pipelineVal = form.querySelector("[data-default-pipeline]").value;
      try {
        await api("PATCH", "/api/teams/" + teamId, {
          default_process: processVal || null,
          default_manager_agent_id: managerVal ? Number(managerVal) : null,
          pipeline_template_id: pipelineVal ? Number(pipelineVal) : null,
        });
        showMsg("global-msg", "팀 기본값을 저장했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  });

  content.querySelectorAll("[data-remove-member]").forEach(btn => {
    btn.onclick = async () => {
      const [teamId, memberRowId] = btn.dataset.removeMember.split(":");
      try {
        await api("DELETE", "/api/teams/" + teamId + "/members/" + memberRowId);
        showMsg("global-msg", "멤버를 제외했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-toggle-reset-protect]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/teams/" + btn.dataset.toggleResetProtect + "/reset-protection", {
          protect_on_reset: btn.dataset.next === "1",
        });
        showMsg("global-msg", btn.dataset.next === "1" ? "공장 초기화 때 이 팀을 보호합니다." : "초기화 보호를 껐습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-toggle-active]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("PATCH", "/api/teams/" + btn.dataset.toggleActive + "/active", {
          is_active: btn.dataset.nextActive === "1",
        });
        showMsg("global-msg", "변경했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-onboard-team]").forEach(btn => {
    btn.onclick = async () => {
      try {
        await api("POST", "/api/teams/" + btn.dataset.onboardTeam + "/onboard");
        showMsg("global-msg", "온보딩 완료 — 이제 @멘션/배정 후보에 노출됩니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-complete-mission]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 팀의 미션을 완료로 표시합니다 — 이후 @멘션/배정 후보에서 제외됩니다. 계속할까요?"))) return;
      try {
        await api("POST", "/api/teams/" + btn.dataset.completeMission + "/complete-mission");
        showMsg("global-msg", "미션 완료로 표시했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-view-team-history]").forEach(btn => {
    btn.onclick = async () => {
      const teamId = btn.dataset.viewTeamHistory;
      const panel = document.getElementById("team-history-" + teamId);
      try {
        await _renderCrewHistory(panel, Number(teamId));
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  content.querySelectorAll("[data-delete-team]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 팀을 완전히 지웁니다(되돌릴 수 없음). 과거 배정 이력은 남습니다. 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/teams/" + btn.dataset.deleteTeam);
        showMsg("global-msg", "삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });

  // 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — innerHTML을
  // 다 채운 뒤(컨테이너가 실제 DOM에 붙은 뒤)에만 Drawflow를 그 자리에
  // 꽂을 수 있다. refresh()가 renderTeams 전체를 다시 부르는 기존
  // 관례 그대로 — 멤버 추가/제외/기본값 저장 등 어떤 변경도 항상
  // 깨끗한 최신 상태로 캔버스를 다시 그린다(특별한 재초기화 훅 불필요).
  teams.forEach(t => {
    const activeAgentMembers = activeAgentMembersOf(t);
    if (!activeAgentMembers.length) return;
    const canvasControls = _initTeamWorkflowCanvas(
      document.getElementById("team-canvas-" + t.id), t, activeAgentMembers
    );
    if (!canvasControls) return;
    const zoomOutBtn = content.querySelector('[data-canvas-zoom-out="' + t.id + '"]');
    const zoomInBtn = content.querySelector('[data-canvas-zoom-in="' + t.id + '"]');
    const zoomFitBtn = content.querySelector('[data-canvas-zoom-fit="' + t.id + '"]');
    if (zoomOutBtn) zoomOutBtn.onclick = canvasControls.zoomOut;
    if (zoomInBtn) zoomInBtn.onclick = canvasControls.zoomIn;
    if (zoomFitBtn) zoomFitBtn.onclick = canvasControls.fit;
  });
}

