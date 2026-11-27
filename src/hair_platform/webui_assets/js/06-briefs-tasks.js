/* ---------- 업무 요청/할 일 ---------- */

async function renderBriefs(content) {
  const canWrite = ["admin", "manager", "member"].includes(ME.role);
  const isAdmin = ME.role === "admin";
  const briefs = await api("GET", "/api/briefs");
  // 일 맡기는 입구는 홈 하나로(2026-11-27, 사용성 평가 2번) — 홈·대화·업무 요청 세 곳이 같은 일을 다른 방식으로 받아
  // 무엇부터 할지 헷갈렸고, 여기 입력칸은 "한 줄에 역할 하나씩"처럼 시스템 구조를 먼저 알라고 했다. 새 일은 홈에서
  // 방식(빠른 질문·정식 업무·여러 단계·정기)을 추천받아 시작하고, 이 화면은 목록·진행 확인 중심으로 둔다. 역할을 이미 아는
  // 사람을 위한 직접 등록 폼은 "직접 등록(고급)"으로 접어 둔다.
  let html = "<section class=\"card\"><h2>업무 요청</h2>";
  if (canWrite) {
    html += '<div class="start-here"><p>새 일은 <b>홈</b>에서 맡기세요. 할 일을 한두 문장으로 적으면 빠른 질문·정식 업무·여러 단계 파이프라인·정기 실행 중 맞는 방식을 추천하고 바로 시작합니다.</p>' +
      '<button class="primary" data-go-tab="home">홈에서 일 맡기기</button></div>' +
      '<details class="brief-direct"><summary>직접 등록(고급) — 필요한 역할을 이미 알 때</summary>';
    html += helpBox(
      '<ul class="muted help-list">' +
      '<li>여기서 하는 일은 "제안된 역할" 칸을 채우는 것뿐입니다 — 짧은 줄로 역할을 나열하면 그 줄들이 그대로, 문장으로 업무를 적으면 업무 내용과 역할·스킬이 맞는 기존 Agent 역할이 제안됩니다(LLM을 쓰지 않는 규칙 기반이라 비용이 없고 즉시 끝납니다 — 맞는 역할이 없으면 줄 단위로 나눈 것이 뜹니다).</li>' +
      '<li>이 제안은 참고용이고, 실제 인원 배정 단위인 "할 일(Task)"은 아래 "할 일로 나누기"에서 직접 만들어야 합니다.</li>' +
      '<li>뷰어(viewer) role은 조회만 가능하고 제출은 못 합니다.</li>' +
      '</ul>'
    ) +
      '<form class="inline" id="brief-form">' +
      '<textarea id="brief-text" placeholder="맡길 일을 문장으로 적으세요. 예: 2027년 상반기 신입 개발자 채용 공고 2건 작성(백엔드·프론트엔드)&#10;필요한 역할을 이미 안다면 한 줄에 하나씩 적어도 됩니다(예: 백엔드 개발자 1명)." rows="3" style="flex:1;min-width:240px"></textarea>' +
      '<button class="primary" type="submit">등록</button></form></details>';
  }
  html += "</section><section class=\"card\"><h2>업무 요청 목록</h2>" +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"할 일로 나누기"로 만든 할 일(Task)이 "리소스 매칭/배정"·"AI 에이전트 생성" 탭에서 쓸 단위입니다.</li>' +
      '<li><b>삭제</b>(관리자 전용)는 이 업무 요청과 그 아래 할 일·배정·이력까지 전부 되돌릴 수 없게 지웁니다 — 확정된(confirmed) 배정이 하나라도 있으면 막힙니다. 이땐 "할 일 보기 → 상세"에서 그 배정을 먼저 <b>"배정 취소"</b>하세요.</li>' +
      '</ul>'
    );
  html += table(["ID", "요청자", "내용", "상태", "제안된 역할", "동작"], briefs.map(b => {
    let actions = '<button class="small" data-decompose="' + b.id + '">할 일로 나누기</button>' +
      '<button class="small" data-viewtasks="' + b.id + '">진행 보기</button>';
    if (isAdmin) {
      actions += '<button class="small danger" data-delete-brief="' + b.id + '">삭제</button>';
    }
    return [b.id, esc(b.submitted_by_username || ("#" + b.submitted_by)), esc(b.raw_text), esc(label(b.status)), esc(b.stub_roles.join(", ")),
      canWrite ? '<div class="actions">' + actions + "</div>" : ""];
  }), { empty: "아직 업무 요청이 없습니다. 위 칸에 맡길 일을 적어 제출하면 할 일로 나눠 사람·Agent에게 배정할 수 있습니다. 어떤 방식이 맞는지 모르겠으면 <button class=\"small\" data-go-tab=\"home\">홈에서 AI에게 일 맡기기</button>" });
  html += '<div id="brief-detail"></div></section>';
  content.innerHTML = html;

  const form = document.getElementById("brief-form");
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const rawText = document.getElementById("brief-text").value;
      if (!rawText.trim()) {
        showMsg("global-msg", "업무 요청 내용을 입력하세요.", "error");
        return;
      }
      try {
        await api("POST", "/api/briefs", { raw_text: rawText });
        showMsg("global-msg", "업무 요청을 제출했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
  content.querySelectorAll("[data-decompose]").forEach(btn => {
    btn.onclick = () => {
      const brief = briefs.find(b => b.id === Number(btn.dataset.decompose));
      renderDecomposeForm(btn.dataset.decompose, brief ? brief.stub_roles : []);
    };
  });
  content.querySelectorAll("[data-viewtasks]").forEach(btn => {
    btn.onclick = () => renderTaskList(btn.dataset.viewtasks);
  });
  content.querySelectorAll("[data-delete-brief]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 업무 요청과 그 아래 할 일·배정·결과물(산출물 평가 이력 포함)을 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/briefs/" + btn.dataset.deleteBrief);
        showMsg("global-msg", "업무 요청을 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

async function renderDecomposeForm(briefId, stubRoles) {
  const detail = document.getElementById("brief-detail");
  const suggestions = (stubRoles || []).length
    ? '<p class="muted">제안된 역할(클릭하면 아래 입력창에 채워집니다 — 업무 내용과 역할·스킬이 맞는 기존 Agent 역할이며, 맞는 역할이 없으면 요청을 줄 단위로 나눈 것입니다): ' +
      stubRoles.map(r => '<button type="button" class="small" data-fill-role="' + esc(r) + '">' + esc(r) + '</button>').join(" ") +
      "</p>"
    : "";
  const skillDatalist = await skillDatalistHtml();
  detail.innerHTML =
    '<h3>업무 요청 #' + esc(briefId) + ' — 할 일 나누기</h3>' +
    suggestions +
    '<form class="inline" id="decompose-form">' +
    '<input id="d-role" placeholder="필요한 역할(예: 백엔드 개발자)" required>' +
    skillPickerHtml("d-skills-picker", "d-skills") +
    '<input id="d-headcount" type="number" min="1" value="1" style="width:70px" title="인원 수">' +
    '<button class="primary" type="submit">할 일 추가</button></form>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>필요 스킬은 하나씩 입력 후 Enter 또는 "추가"로 칩에 담습니다(여러 개 등록 가능, 칩의 ×로 제거).</li>' +
      '<li>자동완성 중 "실시간웹검색"(Tavily)은 대화창·Crew 실행 양쪽에서 동작하고, "일반웹검색"(Parallel Search, MCP)도 대화창·Crew 실행 양쪽에서 동작합니다(MCP 서버에 접속할 수 있을 때).</li>' +
      '<li>목록에 없는 이름도 자유롭게 추가할 수 있습니다.</li>' +
      '</ul>'
    ) +
    skillDatalist +
    // 사용자 요청(2026-11-04, "업무 분해 단위 가이드" 설계) — 복잡한
    // 업무를 통째로 할 일 하나에 몰아넣지 않도록, LLM이 독립 검증
    // 가능한 단위 + 선행관계를 제안하면 admin이 검토·수정 후 한
    // 배치로 제출한다. 위 단건 폼은 그대로 둔다(간단한 경우엔 그게
    // 더 빠름) — 이건 복잡한 업무에 한해 쓰는 보조 흐름이다.
    '<div id="decompose-batch">' +
    '<button type="button" class="small" id="decompose-suggest-btn">AI로 분해 제안 받기(복잡한 업무용)</button>' +
    '<div id="decompose-batch-rows"></div>' +
    '</div>';
  const skillPicker = initSkillPicker("d-skills-picker", "d-skills", []);
  detail.querySelectorAll("[data-fill-role]").forEach(btn => {
    btn.onclick = () => { document.getElementById("d-role").value = btn.dataset.fillRole; };
  });
  document.getElementById("decompose-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    skillPicker.flush();
    const skills = document.getElementById("d-skills").value.split(",").map(s => s.trim()).filter(Boolean);
    try {
      await api("POST", "/api/briefs/" + briefId + "/tasks", {
        tasks: [{
          role_name: document.getElementById("d-role").value,
          required_skills: skills,
          headcount: Number(document.getElementById("d-headcount").value) || 1,
        }],
      });
      showMsg("global-msg", "할 일을 추가했습니다.", "ok");
      renderTaskList(briefId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 배치(LLM 제안) 행 상태 — "즉시 모드" UI: 배열을 고치고 매번
  // innerHTML을 통째로 다시 그린다(행 추가/삭제 시 선행업무 드롭다운의
  // 보기 목록 자체가 바뀌어야 하므로 부분 갱신보다 단순하고 안전함).
  let batchItems = [];

  function syncBatchItemsFromInputs() {
    batchItems = batchItems.map((item, i) => ({
      role_name: document.getElementById("db-role-" + i)?.value ?? item.role_name,
      required_skills: (document.getElementById("db-skills-" + i)?.value ?? "")
        .split(",").map(s => s.trim()).filter(Boolean),
      depends_on_index: (() => {
        const v = document.getElementById("db-dep-" + i)?.value ?? "";
        return v === "" ? null : Number(v);
      })(),
    }));
  }

  function renderBatchRows() {
    const container = document.getElementById("decompose-batch-rows");
    if (!batchItems.length) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML =
      table(["역할", "필요 스킬(쉼표로 구분)", "선행 업무", "동작"], batchItems.map((item, i) => [
        '<input id="db-role-' + i + '" value="' + escAttr(item.role_name) + '" style="width:100%">',
        '<input id="db-skills-' + i + '" value="' + escAttr(item.required_skills.join(", ")) + '" style="width:100%">',
        '<select id="db-dep-' + i + '">' +
          '<option value="">(없음)</option>' +
          batchItems.map((other, j) => j === i ? "" :
            '<option value="' + j + '"' + (item.depends_on_index === j ? " selected" : "") + '>' +
            esc(other.role_name || ("#" + (j + 1))) + '</option>').join("") +
          '</select>',
        '<button type="button" class="small danger" data-batch-remove="' + i + '">삭제</button>',
      ])) +
      '<div class="inline">' +
      '<button type="button" class="small" id="decompose-batch-add-row">행 추가</button>' +
      '<button type="button" class="primary small" id="decompose-batch-submit">이 배치로 할 일 추가</button>' +
      '</div>';
    container.querySelectorAll("[data-batch-remove]").forEach(btn => {
      btn.onclick = () => {
        syncBatchItemsFromInputs();
        const removedIndex = Number(btn.dataset.batchRemove);
        batchItems.splice(removedIndex, 1);
        // 삭제된 행을 선행업무로 가리키던 항목은 다시 "(없음)"으로,
        // 뒤쪽 인덱스를 가리키던 항목은 한 칸씩 당겨준다.
        batchItems = batchItems.map(item => {
          if (item.depends_on_index === null) return item;
          if (item.depends_on_index === removedIndex) return { ...item, depends_on_index: null };
          if (item.depends_on_index > removedIndex) {
            return { ...item, depends_on_index: item.depends_on_index - 1 };
          }
          return item;
        });
        renderBatchRows();
      };
    });
    document.getElementById("decompose-batch-add-row").onclick = () => {
      syncBatchItemsFromInputs();
      batchItems.push({ role_name: "", required_skills: [], depends_on_index: null });
      renderBatchRows();
    };
    document.getElementById("decompose-batch-submit").onclick = async () => {
      syncBatchItemsFromInputs();
      if (batchItems.some(item => !item.role_name.trim())) {
        showMsg("global-msg", "역할명이 비어있는 행이 있습니다.", "error");
        return;
      }
      try {
        await api("POST", "/api/briefs/" + briefId + "/tasks", { tasks: batchItems });
        showMsg("global-msg", "배치로 할 일 " + batchItems.length + "건을 추가했습니다.", "ok");
        renderTaskList(briefId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  }

  document.getElementById("decompose-suggest-btn").addEventListener("click", async () => {
    try {
      const result = await api("POST", "/api/briefs/" + briefId + "/decompose-suggest", {});
      batchItems = result.tasks || [];
      if (!batchItems.length) {
        showMsg("global-msg", "AI 제안을 받지 못했습니다 — 수동으로 입력해주세요.", "error");
      }
      renderBatchRows();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// 업무 한 건의 흐름 한눈에(2026-11-27, 사용성 평가 8번) — 요청 → 할 일 → 배정 → 결과물 → 결재 단계를 한 줄로 보이고,
// 할 일마다 "다음에 할 일" 버튼을 둔다. 예전엔 진행을 보려면 업무 요청·리소스 매칭/배정·내 업무·결재 화면을 오가야 했다.
function _briefStepperHtml(tasks) {
  const n = tasks.length;
  const assigned = tasks.filter(t => t.assigned_count > 0).length;
  const produced = tasks.filter(t => t.deliverable_count > 0 || t.active_job).length;
  const approved = tasks.filter(t => t.has_final_result).length;
  const step = (label, done, total) => {
    const state = total === null ? "done" : (total > 0 && done === total ? "done" : (done > 0 ? "part" : "todo"));
    return '<li class="step step-' + state + '"><span class="step-dot"></span>' + label +
      (total === null ? "" : " <b>" + done + "/" + total + "</b>") + "</li>";
  };
  return '<ol class="brief-stepper" aria-label="업무 진행 단계">' +
    step("요청", 0, null) +
    '<li class="step step-' + (n ? "done" : "todo") + '"><span class="step-dot"></span>할 일 <b>' + n + "</b></li>" +
    step("배정", assigned, n) + step("결과물", produced, n) + step("결재", approved, n) + "</ol>";
}

function _taskNextAction(t) {
  const canAssign = ["admin", "manager"].includes(ME.role);
  const ws = (label, primary) => '<button class="small' + (primary ? " primary" : "") + '" data-task-workspace="' + t.id + '">' + label + "</button>";
  if (t.has_final_result) return '<span class="gate-badge gate-ok">✓ 완료</span> ' + ws("결과 보기");
  if (t.active_job === "paused_for_approval") return ws("승인할 단계 보기", true);
  if (t.active_job) return ws("진행 보기");
  if (t.latest_status === "pending_review") return ws("결과물 검토·결재", true);
  if (t.deliverable_count > 0) return ws("결과물 보기");
  if (t.assigned_count > 0) return ws("작업 시작(실행)", true);
  if (t.assignment_pending_count > 0) return '<button class="small" data-go-tab="approvals">배정 결재 대기</button>';
  return canAssign ? '<button class="small primary" data-task-detail="' + t.id + '">배정하기</button>'
    : '<span class="muted">배정 전 — 매니저가 배정합니다</span>';
}

async function renderTaskList(briefId) {
  const detail = document.getElementById("brief-detail");
  const isAdmin = ME.role === "admin";
  const tasks = await api("GET", "/api/briefs/" + briefId + "/tasks");
  detail.innerHTML = "<h3>업무 요청 #" + esc(briefId) + " — 진행</h3>" + _briefStepperHtml(tasks) +
    // 사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
    // 분석하고 부족한 기능을 개선해줘") — 이전엔 이 화면에서 할 일의
    // 결과물 진행 상태를 전혀 알 수 없어(id/역할/스킬/인원뿐), 확인하려면
    // "내 업무"에서 찾거나 배정·워크스페이스를 따로 추적해야 했다.
    table(["역할", "배정", "상태", "최종 결과물", "다음에 할 일", "동작"], tasks.map(t => [
      '<b>' + esc(t.role_name) + '</b><div class="muted">할 일 #' + t.id + (t.required_skills.length ? " · " + esc(t.required_skills.join(", ")) : "") +
        (t.headcount > 1 ? " · " + t.headcount + "명" : "") + "</div>",
      t.assigned_count > 0 ? "✓ " + t.assigned_count + "명 확정" : (t.assignment_pending_count > 0 ? '<span class="gate-badge gate-warn">결재 대기</span>' : '<span class="muted">배정 안 됨</span>'),
      // 사용자 요청(2026-11-12) — 요청을 막 제출한 바로 이 화면에서도
      // "실행 중"을 바로 볼 수 있어야 "내 업무" 탭까지 따로 찾아가지
      // 않아도 된다(renderMyWork()의 같은 로직, 백엔드 집계도 공유).
      t.active_job === "running" ? '<span class="gate-badge gate-warn">⏳ 실행 중</span>'
        : t.active_job === "paused_for_approval" ? '<span class="gate-badge gate-warn">🕐 승인 대기 중</span>'
        : t.latest_status ? esc(label(t.latest_status)) : '<span class="muted">아직 없음</span>',
      t.has_final_result ? '<span class="gate-badge gate-ok">✓ 승인됨</span>' : '<span class="muted">-</span>',
      _taskNextAction(t),
      '<div class="actions"><button class="small" data-task-detail="' + t.id + '">상세</button>' +
        (isAdmin ? '<button class="small danger" data-delete-task="' + t.id + '">삭제</button>' : "") +
        "</div>",
    ])) +
    // 사용자 요청(2026-10-06, "팀 구성과 협업 프로세스를 하나의 화면으로
    // 합치는 방안" → 옵션 C 채택) — "상세"를 누르면 이 할 일의 배정
    // 현황(+admin/manager는 매칭·배정 요청, 확정됐으면 협업 워크스페이스
    // 요약까지)을 아래에 바로 펼친다. "인력 매칭/배정"·"결재"·"내 업무"는
    // 여러 업무를 가로지르는 일괄 처리용으로 그대로 남겨둔다 — 완전
    // 통합은 매니저의 일괄 처리 효율을 떨어뜨릴 위험이 있어 기각했다.
    helpBox(
      '<ul class="muted help-list">' +
      '<li>"상세"에서 배정 현황을 바로 확인할 수 있습니다.</li>' +
      '<li>"리소스 매칭/배정"·"결재"는 여러 할 일을 한꺼번에 처리할 때, "AI 에이전트 생성"은 새 Agent가 필요할 때 씁니다.</li>' +
      '</ul>'
    ) +
    '<div id="task-detail"></div>';
  detail.querySelectorAll("[data-delete-task]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 할 일과 그 배정·결과물(산출물 평가 이력 포함)을 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/briefs/" + briefId + "/tasks/" + btn.dataset.deleteTask);
        showMsg("global-msg", "할 일을 삭제했습니다.", "ok");
        renderTaskList(briefId);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  detail.querySelectorAll("[data-task-workspace]").forEach(btn => {
    btn.onclick = () => {
      renderCollabWorkspace(Number(btn.dataset.taskWorkspace), "task-detail");
      document.getElementById("task-detail").scrollIntoView({ block: "start", behavior: "smooth" });
    };
  });
  detail.querySelectorAll("[data-task-detail]").forEach(btn => {
    btn.onclick = () => {
      const task = tasks.find(t => t.id === Number(btn.dataset.taskDetail));
      renderTaskDetailPanel(task);
    };
  });
}

/* 할 일 하나의 생애주기(배정 현황 → 매칭/배정 요청 → 확정 후 협업
   워크스페이스)를 한 화면에서 보여준다(옵션 C).
   매칭 폼·워크스페이스는 기존 "인력 매칭/배정"·"내 업무" 탭의 렌더
   함수를 containerId 파라미터로 그대로 재사용한다(중복 구현 금지) —
   admin/manager만 노출한다: 매칭 결과가 다른 후보의 비용을 보여주고,
   deliverables 조회는 admin/manager가 아니면 참가자 본인만 허용돼
   (403) 있어, 그 둘은 감독 권한이 있는 role에만 안전하게 임베드할 수
   있다. member/viewer는 배정 현황만 읽기 전용으로 본다(자기 업무
   처리는 기존처럼 "내 업무" 탭에서). */
async function renderTaskDetailPanel(task, forceWorkspace = false) {
  const container = document.getElementById("task-detail");
  const isSupervisor = ["admin", "manager"].includes(ME.role);
  container.innerHTML = '<p class="muted">불러오는 중...</p>';
  const assignments = await api("GET", "/api/tasks/" + task.id + "/assignments");
  // 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) — "다중
  // confirmed 허용"으로 바뀌면서 이 패널도 더 이상 .find()로 하나만
  // 골라 보여줄 수 없다. confirmed/requested 전부를 보여주고, 각 건에
  // 배정 ID를 노출해 팀 리더 변경·협업 워크스페이스 진입에 쓴다.
  const confirmedList = assignments.filter(a => a.status === "confirmed");
  const pendingList = assignments.filter(a => a.status === "requested");
  const historyList = assignments.filter(a => a.status !== "confirmed" && a.status !== "requested");

  const resourceLabel = (a) => esc(label(a.resource_type)) + " " +
    (a.resource_name ? esc(a.resource_name) : "#" + a.resource_id) +
    (a.resource_type === "team" ? ' <span class="muted">(팀 리더: #' + a.team_leader_id + ')</span>' : "");

  // 사용자 보고(2026-10-19, "업무 요청 삭제가 '확정된 배정이 있어
  // 삭제할 수 없습니다 — 먼저 정리하세요'로 막히는데 정리할 방법이
  // 없다") — 지금까지 이 에러 메시지만 "먼저 정리하세요"라 안내하고
  // 실제로 배정을 되돌릴 버튼이 화면 어디에도 없었다. 배정 현황을
  // 보여주는 바로 이 자리(admin/manager 전용)에 "배정 취소" 버튼을
  // 추가한다 — task/brief 삭제를 막는 confirmed 배정도 여기서 바로
  // cancelled로 되돌릴 수 있다.
  const cancelBtnHtml = (a) => isSupervisor
    ? ' <button class="small danger" data-cancel-assignment="' + a.id + '">배정 취소</button>'
    : "";
  const confirmedHtml = confirmedList.length
    ? confirmedList.map(a => '<p><span class="gate-badge gate-ok">확정됨</span> ' + resourceLabel(a) + "에게 배정됨" + cancelBtnHtml(a) + "</p>").join("")
    : "";
  const pendingHtml = pendingList.map(a =>
    '<p><span class="gate-badge gate-warn">결재 대기</span> ' + resourceLabel(a) +
    ' — <button class="small" data-goto-approvals="1">결재 탭에서 처리</button>' +
    cancelBtnHtml(a) +
    (isSupervisor && a.resource_type === "team"
      ? ' <span class="inline" style="display:inline-flex;gap:0.25rem">' +
        '<input type="number" min="1" step="1" placeholder="새 팀 리더 user ID" data-team-leader-input="' + a.id + '" style="width:140px">' +
        '<button class="small" data-change-team-leader="' + a.id + '">팀 리더 변경</button></span>'
      : "") +
    "</p>"
  ).join("");
  const statusHtml = (confirmedHtml || pendingHtml)
    ? confirmedHtml + pendingHtml
    : '<p class="muted">아직 배정 요청이 없습니다.</p>';
  const historyHtml = historyList.length
    ? '<details class="muted"><summary>이전 배정 이력(' + historyList.length + '건)</summary>' +
      table(["타입", "대상", "상태"], historyList.map(a => [esc(label(a.resource_type)), esc(a.resource_name || ("#" + a.resource_id)), esc(label(a.status))])) +
      "</details>"
    : "";

  // 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) — 사람/Agent
  // 후보 매칭("인력 매칭/배정"과 공유)에 더해, 등록된 팀 전체를 한 할
  // 일에 배정 요청하는 선택지를 추가한다("팀 관리" 탭에서 미리 구성).
  let teamOptionsHtml = "";
  let pipelineOptionsHtml = "";
  if (isSupervisor) {
    const teams = await api("GET", "/api/teams").catch(() => []);
    const activeTeams = teams.filter(t => t.is_active);
    teamOptionsHtml = activeTeams.length
      ? '<form class="inline" id="task-detail-team-assign-form">' +
        '<select id="task-detail-team-select" required>' +
        activeTeams.map(t => '<option value="' + t.id + '">' + esc(t.name) + '</option>').join("") +
        '</select><button class="small" type="submit">이 팀에 배정 요청</button></form>'
      : '<p class="muted">등록된 활성 팀이 없습니다 — "팀 관리" 탭에서 먼저 만드세요.</p>';
    // 사용자 요청(2026-10-29, "파이프라인을 assignments.resource_type에
    // 추가") — 팀 배정과 같은 패턴으로, 파이프라인도 사람/Agent/Team처럼
    // 결재를 거쳐 배정할 수 있다. 승인되면 그 즉시 실행이 시작된다
    // (`app.py`의 `decide_approval()` 참고).
    const pipelineTemplates = await api("GET", "/api/pipeline-templates").catch(() => []);
    const activePipelines = pipelineTemplates.filter(p => p.is_active);
    pipelineOptionsHtml = activePipelines.length
      ? '<form class="inline" id="task-detail-pipeline-assign-form">' +
        '<select id="task-detail-pipeline-select" required>' +
        activePipelines.map(p => '<option value="' + p.id + '">' + esc(p.name) + '</option>').join("") +
        '</select><button class="small" type="submit">이 파이프라인에 배정 요청</button></form>'
      : '<p class="muted">등록된 활성 파이프라인이 없습니다 — "파이프라인 관리" 탭에서 먼저 만드세요.</p>';
  }

  // 실사용 리뷰로 발견한 갭(2026-10-07) — "다중 confirmed 허용" 이후
  // 이미 배정이 확정된 할 일에도 "후보 매칭하기"가 변함없이 1순위로
  // 노출돼, 이미 담당자가 있는 일도 아직 비어있는 것처럼 보였다(배정을
  // *추가*하는 것과 *처음* 배정하는 건 의도가 다르다). confirmed가
  // 하나라도 있으면 "<details>" 뒤로 접어 넣고 문구를 "추가 배정"으로
  // 바꿔 이게 보충 동작임을 분명히 한다.
  const matchAndTeamHtml = isSupervisor
    ? '<button class="small" id="task-detail-match-btn">후보 매칭하기</button><div id="task-detail-match"></div>' +
      '<details><summary>팀에 배정 요청</summary>' + teamOptionsHtml + '</details>' +
      '<details><summary>파이프라인에 배정 요청</summary>' + pipelineOptionsHtml + '</details>'
    : "";
  container.innerHTML =
    '<section class="card"><h4>할 일 #' + task.id + " — " + esc(task.role_name) + " 상세</h4>" +
    statusHtml + historyHtml +
    (isSupervisor && confirmedList.length
      ? '<details><summary>추가 배정(이미 확정된 배정이 있습니다 — 사람/Agent/팀을 더 투입하려면)</summary>' + matchAndTeamHtml + '</details>'
      : matchAndTeamHtml) +
    (isSupervisor && confirmedList.length ? '<div id="task-detail-workspace"></div>' : "") +
    "</section>";

  container.querySelectorAll("[data-goto-approvals]").forEach(btn => {
    btn.onclick = () => selectTab("approvals");
  });
  container.querySelectorAll("[data-cancel-assignment]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 배정을 취소합니다 — 되돌릴 수 없고, 다시 배정하려면 새로 요청해야 합니다. 계속할까요?"))) return;
      try {
        await api("POST", "/api/assignments/" + btn.dataset.cancelAssignment + "/cancel", {});
        showMsg("global-msg", "배정을 취소했습니다.", "ok");
        renderTaskDetailPanel(task);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  container.querySelectorAll("[data-change-team-leader]").forEach(btn => {
    btn.onclick = async () => {
      const assignmentId = btn.dataset.changeTeamLeader;
      const input = container.querySelector('[data-team-leader-input="' + assignmentId + '"]');
      const newLeaderId = Number(input.value);
      if (!Number.isInteger(newLeaderId) || newLeaderId < 1) {
        showMsg("global-msg", "새 팀 리더의 user ID를 입력하세요.", "error"); return;
      }
      try {
        await api("PATCH", "/api/assignments/" + assignmentId + "/team-leader", { team_leader_id: newLeaderId });
        showMsg("global-msg", "팀 리더를 변경했습니다.", "ok");
        renderTaskDetailPanel(task);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  if (isSupervisor) {
    document.getElementById("task-detail-match-btn").onclick = async () => {
      try {
        const result = await api("POST", "/api/tasks/" + task.id + "/match");
        renderMatchResult(task.id, result, "task-detail-match",
          (assignResult) => renderTaskDetailPanel(task, !!(assignResult && assignResult.status === "confirmed")));
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
    const teamAssignForm = document.getElementById("task-detail-team-assign-form");
    if (teamAssignForm) {
      teamAssignForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const teamId = Number(document.getElementById("task-detail-team-select").value);
        try {
          await api("POST", "/api/tasks/" + task.id + "/assignments", {
            resource_type: "team", resource_id: teamId,
          });
          showMsg("global-msg", "팀 배정 요청을 보냈습니다 — \"결재\" 탭에서 처리하세요.", "ok");
          renderTaskDetailPanel(task);
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    }
    const pipelineAssignForm = document.getElementById("task-detail-pipeline-assign-form");
    if (pipelineAssignForm) {
      pipelineAssignForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const pipelineId = Number(document.getElementById("task-detail-pipeline-select").value);
        try {
          await api("POST", "/api/tasks/" + task.id + "/assignments", {
            resource_type: "pipeline", resource_id: pipelineId,
          });
          showMsg("global-msg", "파이프라인 배정 요청을 보냈습니다 — \"결재\" 탭에서 승인하면 바로 실행이 시작됩니다.", "ok");
          renderTaskDetailPanel(task);
        } catch (e) { showMsg("global-msg", e.message, "error"); }
      });
    }
  }
  // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
  // 결과물 확인이 어려워(숨겨져 있는 느낌)") — admin/manager는 확정된
  // 배정이 하나라도 있으면 항상 워크스페이스를 바로 보여줬지만, member가
  // 셀프배정으로 그 자리에서 즉시 confirmed가 될 때는(`forceWorkspace`)
  // 보여주지 않았다 — "결재" 탭조차 안 거치고 확정됐는데 "바로
  // 협업 워크스페이스에서 작업을 시작할 수 있습니다"라는 안내만 뜨고
  // 실제로는 "내 업무" 탭을 따로 찾아가야 했다. member가 참가자가 아닌
  // 남의 할 일까지 무조건 열면(권한 없음 에러만 뜸) 오히려 혼란이라,
  // "방금 내가 확정시킨 배정"일 때만 좁혀서 연다.
  if ((isSupervisor || forceWorkspace) && confirmedList.length) {
    renderCollabWorkspace(task.id, "task-detail-workspace");
  }
}

