/* ---------- 통합 인박스(Phase 5, 사용자 요청 2026-09-28) ---------- */

async function renderInbox(content) {
  const items = await api("GET", "/api/inbox");
  const labelOf = {
    approval_pending: "결재 대기",
    execution_failed: "AI 에이전트 실행 실패",
    pipeline_failed: "파이프라인 실행 실패",
    low_gate_score: "품질 점검 미달 경고",
    completed: "✅ 완료됨",
    assigned_to_me: "📌 새로 맡은 일",
    due_soon: "⏰ 기한",
  };
  const cards = items.map(item => {
    if (item.type === "approval_pending") {
      // 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
      // 에이전트명이 표시되지 않는 경우도 있어") — "결재 대기 · 배정
      // #12"만 보여줘 뭘 승인하는지 결재 탭을 또 열어야 알 수 있었다.
      // `/api/inbox`가 이제 task_role_name/resource_name도 주므로
      // (`approvals.describe_subject()`) 그대로 보여준다.
      const detail = item.subject_type === "knowledge_submission" && item.resource_name
        ? esc(item.resource_name)
        : item.task_id != null
        ? "#" + item.task_id + " " + esc(item.task_role_name || "") +
          (item.resource_name ? " · " + esc(label(item.resource_type)) + " " + esc(item.resource_name) : "")
        : esc(label(item.subject_type)) + " #" + item.subject_id;
      return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + detail + '</div>' +
        '<button class="small" data-goto-approvals>결재 화면으로 이동</button></div>';
    }
    if (item.type === "execution_failed") {
      return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(item.role_name) + '</div>' +
        '<div>' + esc(item.error || "") + '</div>' +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    if (item.type === "pipeline_failed") {
      // 파이프라인 실패·서버 재시작 중단(2026-11-25, P2-15·P1-6) — 중단된 실행은 업무에서 "이어서 실행"할 수 있다.
      return '<div class="deliverable-card"><div class="muted">' + (item.interrupted ? "⏸ 서버 재시작으로 중단됨" : labelOf[item.type]) +
        " · " + esc(item.template_name || "") + " · " + esc(String(item.stage_seq)) + "단계 · " + esc(item.role_name) + "</div>" +
        (item.interrupted ? '<div class="muted">업무를 열어 "이어서 실행"을 누르면 끝난 단계는 건너뛰고 이어 갑니다(admin·manager).</div>' : "") +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    // 새로 맡은 일·기한(2026-11-28)
    if (item.type === "due_soon") {
      return '<div class="deliverable-card"><div><span class="gate-badge ' + (item.overdue ? "gate-warn" : "gate-info") + '">' +
        (item.overdue ? "⏰ 기한 지남" : "⏰ 기한 임박") + "</span> · " + esc(item.role_name) + " · " + esc(item.due_date) + "</div>" +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    if (item.type === "assigned_to_me") {
      return '<div class="deliverable-card"><div><b>' + labelOf[item.type] + "</b> · " + esc(item.role_name) +
        (item.due_date ? ' <span class="muted">기한 ' + esc(item.due_date) + "</span>" : "") + "</div>" +
        '<div class="muted">' + esc(fmtTime(item.assigned_at)) + " 배정</div>" +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    if (item.type === "completed") {
      // 사용자 요청(2026-11-12) — 결재가 필요 없는 성공 완료도 요청한
      // 사람이 알 수 있게 한다(지금까지 조용히 묻혔음, 위 inbox.py
      // docstring 참고). 다른 경고성 카드와 톤이 다르다는 걸 보여주려고
      // gate-ok 배지를 쓴다(나머지는 muted 텍스트뿐).
      return '<div class="deliverable-card"><div><span class="gate-badge gate-ok">' + labelOf[item.type] + '</span> · ' + esc(item.role_name) + '</div>' +
        '<div class="muted">' + esc(fmtTime(item.completed_at)) + '</div>' +
        '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
    }
    return '<div class="deliverable-card"><div class="muted">' + labelOf[item.type] + " · " + esc(item.role_name) + '</div>' +
      '<button class="small" data-goto-task="' + item.task_id + '">업무 열기</button></div>';
  }).join("");

  content.innerHTML =
    '<section class="card"><h2>내게 필요한 조치</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>📌 새로 맡은 일(최근 3일 안에 내게 확정된 일)·⏰ 기한 지남·내일까지 기한인 내 일도 여기 뜹니다.</li>' +
      '<li>결재 대기·AI 에이전트 실행 실패·파이프라인 실패(서버 재시작 중단 포함)·품질 점검 미달 경고(승인/반려 전인 것만)·✅ 완료됨(결재가 필요 없는 성공, 최근 24시간 이내만)을 한 곳에 모았습니다.</li>' +
      '<li>브라우저 알림을 허용하면 다른 탭·창에 있어도 새 항목이 생길 때 데스크톱 알림이 뜹니다(약 20초 간격으로 확인).</li>' +
      '<li>완전한 시간순 통합은 아니고 종류별로 묶여 있습니다 — 처리는 각 항목의 원래 화면(결재/내 업무)에서 합니다.</li>' +
      '</ul>'
    ) +
    (items.length ? cards : '<p class="muted">지금 조치가 필요한 항목이 없습니다 — 아직 업무를 만들어본 적이 없다면 "이용 가이드" 탭에서 가장 빠른 시작 방법을 확인하세요.</p>') +
    '</section>';

  content.querySelectorAll("[data-goto-approvals]").forEach(btn => {
    btn.onclick = () => selectTab("approvals");
  });
  content.querySelectorAll("[data-goto-task]").forEach(btn => {
    btn.onclick = async () => {
      await selectTab("mywork");
      renderCollabWorkspace(Number(btn.dataset.gotoTask));
    };
  });
}

