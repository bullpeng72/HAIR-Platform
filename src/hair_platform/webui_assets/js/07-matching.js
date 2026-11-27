/* ---------- 인력 매칭·배정 ---------- */

// 사용자 보고(2026-10-13, "할일 ID의 경우 의미없는 숫자로 할일을 찾아야
// 해(할일 아이디를 사전에 모르면 사용 불가)") — 원래는 숫자 입력칸에
// Task ID를 직접 타이핑해야 했다(어디서도 그 ID를 보여주지 않음).
// `GET /api/briefs/{id}/tasks`의 docstring조차 "Web UI가 매칭/배분
// 화면에서 브리프별 Task 목록을 골라야 한다"고 적어뒀는데 이 화면만
// 그 엔드포인트를 안 쓰고 있었다 — 브리프별로 묶은 select로 바꾼다.
async function _fetchAllTasksGroupedByBrief() {
  const briefs = await api("GET", "/api/briefs");
  const taskLists = await Promise.all(
    briefs.map(b => api("GET", "/api/briefs/" + b.id + "/tasks").catch(() => []))
  );
  return briefs.map((b, i) => ({ brief: b, tasks: taskLists[i] }));
}

function _taskPickerOptionsHtml(groups) {
  return groups.map(g => {
    if (!g.tasks.length) return "";
    const briefLabel = "#" + g.brief.id + " " + (g.brief.raw_text.length > 24 ? g.brief.raw_text.slice(0, 24) + "…" : g.brief.raw_text);
    const options = g.tasks.map(t =>
      '<option value="' + t.id + '">#' + t.id + " " + esc(t.role_name) + " (" + esc(label(t.status)) + ")</option>"
    ).join("");
    return '<optgroup label="' + escAttr(briefLabel) + '">' + options + '</optgroup>';
  }).join("");
}

async function renderMatch(content) {
  // 2026-10-14 — _renderBoardScope()와 같은 이유로 조기 블랭크를
  // 제거한다(아래에서 데이터를 다 가져온 뒤 한 번에 교체).
  const groups = await _fetchAllTasksGroupedByBrief();
  const hasTasks = groups.some(g => g.tasks.length);
  content.innerHTML =
    '<section class="card"><h2>할 일에 맞는 리소스 찾기</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>아래 목록은 "업무 요청" 탭에서 "할 일로 나누기"로 만든 할 일 전체입니다(브리프별로 묶음).</li>' +
      '<li>후보는 역할명이 서로 포함 관계(부분일치 — 예: "프론트엔드 개발" ↔ "프론트엔드 개발자")이거나, 역할명은 달라도 필요 스킬(2개 이상)을 전부 가진 사람·Agent입니다(예: "Backend Engineer" ↔ "백엔드 개발자"). 역할명이 맞고 필요 스킬까지 다 가진 후보가 있으면 그것부터 보여주고, 없으면 가용성40%·적합도35%·비용25% 가중치로 점수를 매겨 상위 3명을 제시합니다(적합도 점수가 없는 후보는 적합도를 빼고 가용성60%·비용40%로, 같은 점수면 필요 스킬을 더 많이 가진 후보가 앞). Agent의 적합도는 관리자가 입력한 성과점수, 없으면 그 Agent 결과물의 Gate A 평균(평가 3건 이상)입니다.</li>' +
      '<li>위 기준으로 후보가 하나도 없으면(예: "콘텐츠 카피라이팅" ↔ "광고 문안 담당"처럼 이름도 스킬도 겹치지 않는 경우), "AI로 추천 받기"로 LLM이 전체 Pool에서 의미상 맞는 후보를 찾아줍니다(비용 발생 가능, 명시적으로 눌러야만 호출됨).</li>' +
      '<li>"배정 요청"을 누르면 즉시 확정되는 게 아니라 "결재" 탭에서 승인 절차를 거쳐야 확정(confirmed)됩니다(단, 본인 셀프배정은 결재 없이 즉시 확정됩니다).</li>' +
      '</ul>'
    ) +
    (hasTasks
      ? '<form class="inline" id="match-form">' +
        '<select id="match-task-id" required style="min-width:280px"><option value="">— 할 일 선택 —</option>' +
        _taskPickerOptionsHtml(groups) + '</select>' +
        '<button class="primary" type="submit">찾기</button></form>' +
        '<div id="match-result"></div>'
      : '<p class="msg error">아직 할 일이 없습니다 — "업무 요청" 탭에서 먼저 "할 일로 나누기"를 해주세요.</p>') +
    "</section>";
  const form = document.getElementById("match-form");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const taskId = Number(document.getElementById("match-task-id").value);
    if (!Number.isInteger(taskId) || taskId < 1) {
      showMsg("global-msg", "할 일을 선택하세요.", "error"); return;
    }
    try {
      const result = await api("POST", "/api/tasks/" + taskId + "/match");
      renderMatchResult(taskId, result);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

// containerId/onAssigned: 사용자 요청(2026-10-06, "팀 구성과 협업
// 프로세스를 하나의 화면으로 합치는 방안" → 옵션 C) — "업무 요청" 탭의
// 할 일 상세 패널에서도 같은 매칭 결과 표를 재사용하기 위해 일반화했다.
// 기본값을 기존 id로 둬서 "인력 매칭/배정" 탭의 기존 호출부는 그대로
// 둔다. `onAssigned`는 배정 요청 성공 직후 호출돼, 임베드한 쪽(할 일
// 상세 패널)이 자기 상태를 새로고침할 수 있게 한다(이 함수 자체는 그
// 화면 구조를 몰라도 된다).
// 사용자 요청(2026-11-02, "LLM judge로 추천 인력을 찾도록 개선,
// 추천인력이 없는 경우 기존의 사람/에이전트를 지정해 찾을 수 있도록
// 개선") — 배정 요청 버튼 클릭 처리를 공용 헬퍼로 뺐다. 원래
// `renderMatchResult()`의 매칭 결과 테이블에서만 쓰던 로직인데, 아래
// 수동 검색 결과 테이블에도 똑같이 필요해져 중복을 피하려고 분리했다
// (동작은 그대로 — 계정 미연결 경고 confirm, 배정 요청 POST, 성공 시
// onAssigned 콜백).
function _wireMatchAssignButtons(container, taskId, onAssigned) {
  container.querySelectorAll("[data-assign-id]").forEach(btn => {
    btn.onclick = async () => {
      if (btn.dataset.unlinked === "1" && !(await confirmModal(
        "이 사람은 로그인 계정이 연결돼 있지 않습니다 — 배정이 승인돼도 본인이 \"내 업무\"에서 확인할 방법이 없습니다. \"사람 인력 관리\"에서 계정을 먼저 연결하는 걸 권장합니다. 그래도 배정 요청하시겠습니까?"
      ))) return;
      try {
        const result = await api("POST", "/api/tasks/" + taskId + "/assignments", {
          resource_type: btn.dataset.assignType, resource_id: Number(btn.dataset.assignId),
        });
        // 사용자 요청(2026-11-05, 로드맵 7번 "셀프배정 결재 단순화") —
        // 본인 셀프배정은 결재 없이 즉시 confirmed로 돌아온다.
        showMsg("global-msg",
          (result.status === "confirmed"
            ? "셀프배정이 결재 없이 바로 확정됐습니다 — 바로 협업 워크스페이스에서 작업을 시작할 수 있습니다."
            : "배정 요청을 보냈습니다 — \"결재\" 탭에서 처리하세요.") +
            // 용량·부재 경고(2026-11-27, Phase A) — 막지 않고 알려 준다.
            ((result.warnings || []).length ? " ⚠ " + result.warnings.join(" ") : ""),
          (result.warnings || []).length ? "error" : "ok");
        // 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서
        // 진행과정과 결과물 확인이 어려워") — 결과(특히 셀프배정
        // 즉시확정 여부)를 콜백에 넘겨, 호출부가 "방금 확정됐으면
        // 바로 워크스페이스를 보여준다" 같은 판단을 할 수 있게 한다.
        if (onAssigned) onAssigned(result);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

function renderMatchResult(taskId, result, containerId = "match-result", onAssigned) {
  const box = document.getElementById(containerId);
  // 사용자 보고(2026-11-02, 스크린샷 — "Cannot set properties of null
  // (setting 'innerHTML')") — 실측 원인: "AI로 추천 받기"에 진행 중
  // 표시가 없어 사용자가 응답을 기다리다 여러 번 눌렀고(서버 로그에
  // use_llm_judge=true 요청이 연달아 5건 찍힘), 그중 하나가 응답을
  // 기다리는 동안 다른 탭으로 이동해 이 컨테이너가 DOM에서 사라진
  // 뒤에 뒤늦게 응답이 와서 `box.innerHTML = ...`가 null에 쓰려다
  // 터졌다. 화면을 이미 벗어났으면 반영할 대상이 없으니 조용히
  // 무시한다(에러 아님).
  if (!box) return;
  if (result.generate_new) {
    const triedLlm = result.match_mode === "llm_judge_failed";
    box.innerHTML =
      '<p class="msg error">' + (triedLlm ? "AI 추천도 적합한 후보를 찾지 못했습니다." : "매칭되는 후보가 없습니다.") + '</p>' +
      (triedLlm
        ? ""
        : '<button class="small" id="' + containerId + '-llm-judge-btn">AI로 추천 받기(LLM 호출, 비용 발생 가능)</button>') +
      '<p class="muted">또는 "AI 에이전트 생성" 탭에서 새 Agent를 만들거나, "사람 인력 관리" 탭에서 이 역할의 사람을 등록해보세요.</p>';
    const llmBtn = document.getElementById(containerId + "-llm-judge-btn");
    if (llmBtn) {
      llmBtn.onclick = async () => {
        // 위 버그의 근본 원인 수정 — 요청 중엔 버튼을 비활성화하고
        // 진행 중임을 표시해, 응답을 못 받았다고 오해해 여러 번
        // 누르는 일을 막는다.
        llmBtn.disabled = true;
        llmBtn.textContent = "AI 추천 받는 중…";
        try {
          const r2 = await api("POST", "/api/tasks/" + taskId + "/match?use_llm_judge=true");
          renderMatchResult(taskId, r2, containerId, onAssigned);
        } catch (e) {
          showMsg("global-msg", e.message, "error");
          llmBtn.disabled = false;
          llmBtn.textContent = "AI로 추천 받기(LLM 호출, 비용 발생 가능)";
        }
      };
    }
    return;
  }
  // 사용자 보고(2026-10-06, "매칭/배정 화면에도 계정 미연결 경고
  // 추가해줘") — 사람 후보가 로그인 계정과 연결 안 돼 있으면 배정·승인이
  // 돼도 당사자가 "내 업무"에서 그 일을 볼 방법이 없다. Agent는
  // 로그인 계정 개념이 없어 "해당없음"으로 둔다.
  const accountCell = (c) => {
    if (c.type !== "human") return '<span class="muted">해당없음</span>';
    return c.user_id != null
      ? "연결됨"
      : '<span class="gate-badge gate-warn">⚠ 미연결</span>';
  };
  // 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
  // 에이전트명이 표시되지 않는 경우도 있어") — `role`은 직무 타이틀이라
  // 같은 역할의 사람이 여럿이면 ID 숫자로만 구분해야 했다. "인력 검색"
  // 탭은 이미 사람 이름을 보여주고 있었는데(백엔드가 따로 조회해 끼워
  // 넣음) 이 매칭 결과 테이블만 빠져 있었다 — `ResourceCandidate.name`을
  // 추가해(`models.py`/`pool.py`) 똑같이 보여준다. Agent는 역할명이
  // 곧 식별자라 "-"로 둔다.
  // 사용자 요청(2026-10-27, "다룬 테이블도 ID 표기되는 것이 남아
  // 있는지 확인") — ResourceCandidate.code(human=employee_id,
  // agent=code)로 교체.
  box.innerHTML = table(
    ["순위", "타입", "코드", "이름", "역할", "가용", "비용", "점수", "근거", "계정", "동작"],
    result.top.map((sc, i) => [
      i + 1, esc(label(sc.candidate.type)), esc(sc.candidate.code || ("#" + sc.candidate.id)), esc(sc.candidate.name || sc.candidate.role), esc(sc.candidate.role),
      // 부하·부재·모델 대기(2026-11-27, Resource Management Phase A)
      (sc.candidate.is_available ? "가능" : "불가") +
        (sc.over_capacity ? ' <span class="gate-badge gate-warn">용량 초과</span>' : "") +
        ((sc.notes || []).length ? '<div class="muted pm-note">' + sc.notes.map(esc).join(" · ") + "</div>" : ""),
      sc.candidate.cost,
      sc.score.toFixed(3) + (sc.exact_match ? " (역할 일치)" : (sc.rationale ? " (AI 추천)" : "")),
      // 사용자 요청(2026-11-02, "LLM judge로 추천 인력을 찾도록 개선") —
      // LLM이 근거(rationale)를 줬으면 그걸 사람이 읽는 문장으로
      // 보여주고, 기존 결정론적 매칭(rationale 없음)은 그대로 점수
      // breakdown JSON을 보여준다.
      esc(sc.rationale || JSON.stringify(sc.breakdown)) +
        (sc.candidate.performance_source ? '<div class="muted pm-note">적합도: ' + esc(sc.candidate.performance_source) + "</div>" : ""),
      accountCell(sc.candidate),
      '<div class="actions">' +
      '<button class="small" data-assign-type="' + esc(sc.candidate.type) + '" data-assign-id="' + sc.candidate.id + '" data-unlinked="' + (sc.candidate.type === "human" && sc.candidate.user_id == null ? "1" : "0") + '">배정 요청</button>' +
      // 사용자 결정(2026-10-12, "위임은 Gate A-G 그대로 타고, audit_log
      // 에도 남겨줘") — Agent 후보에만 "위임"(결재 없이 즉시 확정)을
      // 추가로 보여준다. 사람/팀은 Linear식 "위임" 개념의 대상이 아니다.
      (sc.candidate.type === "agent"
        ? '<button class="small" data-delegate-agent-id="' + sc.candidate.id + '" title="결재 없이 즉시 연결 — 바로 실행 가능">위임</button>'
        : "") +
      '</div>',
    ])
  );
  _wireMatchAssignButtons(box, taskId, onAssigned);
  box.querySelectorAll("[data-delegate-agent-id]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal(
        "결재 없이 이 Agent를 바로 이 업무에 연결합니다(즉시 실행 가능) — 계속하시겠습니까?"
      ))) return;
      try {
        await api("POST", "/api/tasks/" + taskId + "/delegate", {
          agent_id: Number(btn.dataset.delegateAgentId),
        });
        showMsg("global-msg", "위임했습니다 — 결재 없이 바로 실행할 수 있습니다.", "ok");
        if (onAssigned) onAssigned();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

