/* ---------- 결재 ---------- */

async function renderApprovals(content) {
  const isDecider = ["admin", "manager"].includes(ME.role);
  let html = "";
  let inbox = [];
  if (isDecider) {
    html += helpBox(
      '<ul class="muted help-list">' +
      '<li>배정 요청은 요청자의 상사 체인(조직도 "상사" 지정)을 따라 자동으로 단계가 만들어집니다 — 1단계가 처리돼야 2단계가 열립니다(다단계 결재).</li>' +
      '<li>상사가 지정 안 돼 있으면(예: 관리자 본인 요청) 특정 승인자 없이 1단계만 만들어져 admin/manager 누구나 처리할 수 있습니다.</li>' +
      '<li>본인이 요청한 건은 본인이 승인할 수 없습니다(자기 승인 방지) — "내 승인 대기함"에는 애초에 안 뜹니다.</li>' +
      '</ul>'
    );
    inbox = await api("GET", qs("/api/approvals", { scope: "inbox" }));
    html += '<section class="card"><h2>내 승인 대기함</h2>' + approvalTable(inbox, true, "requester") +
      '<div id="approval-content-detail" style="margin-top:0.75rem"></div></section>';
  }
  const mine = await api("GET", qs("/api/approvals", { scope: "mine" }));
  html += '<section class="card"><h2>내가 요청한 건</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li><b>승인</b>하면 배정 상태가 confirmed(확정)로 바뀌어 조직도·비용·성과 리뷰에 실제로 반영됩니다.</li>' +
      '<li><b>반려</b>하면 그 배정 요청은 거기서 끝나고(재사용 불가), 다시 배정하려면 "리소스 매칭/배정"에서 새로 요청해야 합니다.</li>' +
      '</ul>'
    ) +
    approvalTable(mine, false, "approver") +
    (isDecider ? "" : '<div id="approval-content-detail" style="margin-top:0.75rem"></div>') +
    "</section>";
  content.innerHTML = html;
  content.querySelectorAll("[data-decide]").forEach(btn => {
    btn.onclick = async () => {
      const reasonInput = content.querySelector('[data-decide-reason-for="' + btn.dataset.decide + '"]');
      const reason = reasonInput ? reasonInput.value.trim() : "";
      try {
        await api("POST", "/api/approvals/" + btn.dataset.decide + "/decide", {
          approve: btn.dataset.approve === "1",
          reason: reason || null,
        });
        showMsg("global-msg", "처리했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  // "내용 보기" — 결재 대기함과 "내가 요청한 건" 둘 다 같은 id
  // 네임스페이스(approvals.id)를 쓰므로 합친 조회맵 하나로 충분하다.
  const contentById = {};
  [...inbox, ...mine].forEach(a => { contentById[a.id] = a; });
  const detailPanel = content.querySelector("#approval-content-detail");
  content.querySelectorAll("[data-approval-content-toggle]").forEach(btn => {
    btn.onclick = async () => {
      const a = contentById[Number(btn.dataset.approvalContentToggle)];
      if (!a || !detailPanel) return;
      detailPanel.innerHTML =
        '<div class="card" style="background:var(--bg-alt,#f7f7f7)">' +
        '<div class="actions" style="justify-content:space-between"><b>#' + a.id + " " +
        esc(a.resource_name || label(a.subject_type)) + '</b>' +
        '<button type="button" class="small" data-approval-content-close>닫기</button></div>' +
        '<div class="ab-slot"></div>' +
        renderContent(a.content, a.content_type || "text") + "</div>";
      detailPanel.querySelector("[data-approval-content-close]").onclick = () => { detailPanel.innerHTML = ""; };
      detailPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      // 결재 도우미(2026-11-27, AI Native 분석 3단계) — 파이프라인 단계·결과물 결재만. 실패해도 본문은 그대로 보인다.
      if (a.subject_type === "pipeline_run_stage" || a.subject_type === "deliverable") {
        try {
          const b = await api("GET", "/api/approvals/" + a.id + "/brief");
          const slot = detailPanel.querySelector(".ab-slot");
          if (slot && b.supported) slot.innerHTML = approvalBriefHtml(b);
        } catch (_) { /* 도우미 없이 본문만 */ }
      }
    };
  });
}

// 결재 도우미(2026-11-27, AI Native 분석 3단계 — 9번) — 품질 점검·이전 판과의 차이·확인할 점·과거 반려 사유·위험도 추천을
// 결재 내용 위에 한 덩어리로. 숫자는 서버가 기록에서 계산한다(LLM 없음).
function approvalBriefHtml(b) {
  const lvl = { low: ["낮음", "gate-ok"], medium: ["보통", "gate-warn"], high: ["높음", "gate-warn sys-error"] }[b.risk.level];
  const st = { ok: "✓", warn: "⚠", fail: "✗", not_measured: "–" };
  const s = b.checks_summary;
  const checks = b.checks.filter(c => c.status !== "not_measured");
  let html = '<div class="ab-box" role="region" aria-label="결재 도우미"><div class="ab-head"><b>결재 도우미</b> ' +
    '<span class="gate-badge ' + lvl[1] + '">위험 ' + lvl[0] + "</span> " + esc(b.risk.advice) + "</div>";
  html += '<p class="muted ab-ctx">' + esc(b.title) +
    (b.context.attempt ? " · " + b.context.attempt + "차 시도" + (b.context.max_retry ? "(최대 " + b.context.max_retry + ")" : "") : "") +
    (b.context.waiting_since ? " · 대기 시작 " + esc(fmtTime(b.context.waiting_since)) : "") + "</p>";
  html += '<div class="ab-grid"><div><h4>위험 근거</h4><ul class="ab-list">' + b.risk.factors.map(f => "<li>" + esc(f) + "</li>").join("") + "</ul></div>";
  html += "<div><h4>품질 점검 " + (s.measured ? s.ok + "/" + s.measured + " 통과" + (s.fail ? " · 미달 " + s.fail : "") + (s.warn ? " · 확인 " + s.warn : "") : "기록 없음") + "</h4>" +
    (checks.length ? '<ul class="ab-checks">' + checks.map(c => '<li class="ab-' + c.status + '"><span aria-hidden="true">' + st[c.status] + "</span> " +
      esc(c.label) + (c.note ? ' <span class="muted">— ' + esc(c.note) + "</span>" : "") + "</li>").join("") + "</ul>" : "") + "</div></div>";
  if (b.attention.length) html += "<h4>먼저 확인할 점</h4><ul class=\"ab-list\">" + b.attention.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>";
  if (b.changes) {
    html += "<h4>" + esc(b.changes.against) + "과 비교</h4><p>추가 " + (b.changes.added || 0) + "줄 · 삭제 " + (b.changes.removed || 0) + "줄" +
      (b.changes.similarity !== undefined ? " · 비슷한 정도 " + Math.round(b.changes.similarity * 100) + "%" : "") + "</p>" +
      (b.changes.previous_rejection_reason ? '<p class="muted">지난번 반려 사유: ' + esc(b.changes.previous_rejection_reason) + "</p>" : "") +
      ((b.changes.sample || []).length ? '<pre class="ab-diff">' + b.changes.sample.map(l => '<span class="' + (l[0] === "+" ? "ab-add" : "ab-del") + '">' + esc(l) + "</span>").join("\n") + "</pre>" : "");
  }
  if (b.past_rejections.length) html += "<h4>이 단계의 최근 반려 사유</h4><ul class=\"ab-list\">" + b.past_rejections.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>";
  if (b.active_lessons.length) html += '<p class="muted">이 단계에 적용 중인 배운 점 ' + b.active_lessons.length + "개가 지시문에 들어가 있습니다.</p>";
  if (b.action) html += approvalActionHtml(b);
  return html + "</div>";
}

// 결재 후 실행 동작(2026-11-27, AI Native 분석 4단계 — 5번) — 무엇을 어디로 보낼지 미리 보고, "승인하고 실행"을 눌러야 보낸다.
function approvalActionHtml(b) {
  const a = b.action;
  let html = '<div class="ab-action"><h4>승인하면 실행할 동작 — ' + esc(a.kind_label) + "</h4>";
  if (a.executed) {
    const st = { sent: "보냄", failed: "실패", skipped: "승인만 함(보내지 않음)" }[a.executed.status] || a.executed.status;
    return html + "<p>이미 처리했습니다: <b>" + esc(st) + "</b>" + (a.executed.error ? " — " + esc(a.executed.error) : "") + "</p></div>";
  }
  html += "<p>어디로: <b>" + esc(a.target || "(없음)") + "</b>" + (a.subject ? " · 제목: " + esc(a.subject) : "") + " · 본문 " + a.body_chars + "자(이 결과물 그대로)</p>" +
    '<pre class="ab-diff">' + esc(a.body_excerpt) + (a.body_chars > a.body_excerpt.length ? "\n…" : "") + "</pre>";
  if (!a.ready) return html + '<p class="msg warn">지금은 보낼 수 없습니다 — ' + esc(a.problem) + '. 승인만 할 수 있습니다.</p></div>';
  return html + '<p><input placeholder="사유(선택)" id="ab-run-reason" style="width:14rem"> <button class="small primary" data-approve-run="' + b.approval_id + '">승인하고 실행</button> ' +
    '<span class="muted">표의 "승인"은 승인만 하고 보내지 않습니다.</span></p></div>';
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest && ev.target.closest("[data-approve-run]");
  if (!btn) return;
  if (btn.dataset.confirming !== "1") { btn.dataset.confirming = "1"; btn.textContent = "정말 보내기"; return; }
  btn.disabled = true;
  const reasonEl = document.getElementById("ab-run-reason");
  try {
    const r = await api("POST", "/api/approvals/" + btn.dataset.approveRun + "/decide", {
      approve: true, reason: (reasonEl && reasonEl.value.trim()) || null, run_action: true });
    const a = r.action || {};
    showMsg("global-msg", a.status === "sent" ? "승인하고 보냈습니다(" + (a.target || "") + ")." : "승인했지만 보내지 못했습니다: " + (a.error || a.status), a.status === "sent" ? "ok" : "error");
    refresh();
  } catch (e) { btn.disabled = false; showMsg("global-msg", e.message, "error"); }
});

// 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나 에이전트명이
// 표시되지 않는 경우도 있어") — 원래는 "대상 종류"/"대상 ID"가
// `assignments.id`/`deliverables.id`를 그대로 보여줘, 승인권자가 "무엇을
// 승인하는지" 다른 탭을 또 열어야 알 수 있었다. `GET /api/approvals`가
// 이제 `task_id`/`task_role_name`/`resource_type`/`resource_name`을
// 같이 내려주므로(`approvals.describe_subject()`) 그걸 보여준다 —
// 대상이 지워졌으면(task_id가 null) 원래 종류+ID로 안전하게 되돌아간다.
// 사용자 보고(2026-10-28, "결재에서 승인을 요청하고 있으나, 해당
// 컨텐츠를 검토할 수 있는 UI가 없어") — 지금까지 이 표는 "무엇을
// 승인하는지"(업무/대상/단계)만 보여주고, 실제로 검토해야 할 내용
// (deliverable/pipeline_run_stage의 산출물 텍스트)은 전혀 안
// 보여줬다 — 결재자가 다른 탭을 또 열어 역추적해야 했다(그마저도
// pipeline_run_stage는 볼 수 있는 다른 화면 자체가 없었다). "내용"
// 열을 추가해 `a.content`가 있으면 보기 버튼을, 없으면(assignment
// 등 애초에 리뷰할 본문이 없는 유형) "-"를 보여준다.
// 사용자 요청(2026-11-03, "결재 내 승인 대기의 경우 요청자가, 내가
// 요청한 건의 경우 결재권자가 표시되도록 개선") — 화면마다 궁금한
// "사람"이 다르다: "내 승인 대기함"은 누가 이걸 요청했는지(요청자),
// "내가 요청한 건"은 누가 처리하는지/했는지(결재권자). `personColumn`
// 으로 어느 쪽을 보여줄지 고른다.
function approvalTable(rows, withActions, personColumn) {
  const headers = ["ID", "업무", "대상"];
  if (personColumn === "requester") headers.push("요청자");
  else if (personColumn === "approver") headers.push("결재권자");
  headers.push("단계", "상태", "내용");
  if (withActions) headers.push("동작");
  return table(headers, rows.map(a => {
    // 지식 등록(2026-11-20) — 대화에서 온 제출은 업무가 없는 게 정상이고
    // (지워진 게 아님), 대상 이름(resource_name)에 이미 "지식 등록 — '제목'
    // (출처)"가 다 들어 있다.
    const isKnowledge = a.subject_type === "knowledge_submission";
    const taskCell = a.task_id != null
      ? "#" + a.task_id + " " + esc(a.task_role_name || "")
      : isKnowledge && a.resource_name
        ? '<span class="muted">-</span>'
        : '<span class="muted">(지워진 ' + esc(label(a.subject_type)) + " #" + a.subject_id + ")</span>";
    const resourceCell = isKnowledge && a.resource_name
      ? esc(a.resource_name)
      : a.resource_name
        ? esc(label(a.subject_type)) + ": " + esc(label(a.resource_type)) + " " + esc(a.resource_name)
        : esc(label(a.subject_type));
    const contentCell = a.content
      ? '<button type="button" class="small" data-approval-content-toggle="' + a.id + '">내용 보기</button>'
      : '<span class="muted">-</span>';
    const row = [a.id, taskCell, resourceCell];
    if (personColumn === "requester") {
      row.push(esc(a.requested_by_username || ("#" + a.requested_by)));
    } else if (personColumn === "approver") {
      // 처리 완료됐으면 실제로 결정한 사람(approved_by)을, 아직
      // pending이면 배정된 결재권자(approver_id)를 보여준다 —
      // approver_id가 비어 있으면(상사 미지정) "Admin/매니저 누구나"가
      // 처리할 수 있다는 뜻이라 그대로 알려준다.
      if (a.status !== "pending") {
        row.push(a.approved_by_username ? esc(a.approved_by_username) : '<span class="muted">-</span>');
      } else if (a.approver_id != null) {
        row.push(esc(a.approver_username || ("#" + a.approver_id)));
      } else if (isKnowledge) {
        row.push('<span class="muted">Admin</span>');
      } else {
        row.push('<span class="muted">미지정(Admin/매니저 누구나)</span>');
      }
    }
    row.push(a.step, esc(label(a.status)), contentCell);
    if (withActions) {
      row.push(a.status === "pending"
        ? '<div class="actions">' +
          '<input placeholder="' + (isKnowledge ? "사유(반려 시 필수)" : "사유(선택)") + '" data-decide-reason-for="' + a.id + '" style="width:120px">' +
          '<button class="small" data-decide="' + a.id + '" data-approve="1">승인</button>' +
          '<button class="small danger" data-decide="' + a.id + '" data-approve="0">반려</button></div>'
        : "");
    }
    return row;
  }));
}

