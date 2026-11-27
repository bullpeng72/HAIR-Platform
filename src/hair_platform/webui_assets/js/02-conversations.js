/* ---------- 대화 중심 작업공간(Conversational Workspace,
   2026-10-08, 사용자 요청 "로그인하면 기본적으로 대화창이 주어지고...
   사람이나 에이전트(에이전트 그룹)를 호출하거나 skill을 사용하여
   업무를 수행"). TABS 배열 맨 앞(로그인 직후 첫 화면)에 둔다. ---------- */

let _conversationPollTimer = null;
// 사용자 요청(2026-11-11, "개발업무 Skill의 경우 Default로 대화창에
// 보이지 않도록 설정하고, 필요시 노출하도록") — 대화 화면을 나갔다
// 다시 들어와도 유지되도록 모듈 스코프에 둔다(기본값 false — 개발업무
// 스킬은 default-off, 체크박스로 켜면 그 세션 동안 유지).
let _convShowDevSkills = false;

function _stopConversationPolling() {
  if (_conversationPollTimer) {
    clearInterval(_conversationPollTimer);
    _conversationPollTimer = null;
  }
}

// 사용자 보고(2026-10-14, 스크린샷 — 메시지를 보낸 뒤 진행 중임을 알
// 수 없음) — 기존 "응답을 기다리는 중…"은 `#global-msg`(페이지 맨
// 위)에만 떴다. 대화를 스크롤해 입력창 근처를 보고 있는 사용자에게는
// 화면 밖이라 안 보였다 — 메시지 목록 바로 아래, 입력창 바로 위에
// 똑같은 역할을 하는 인라인 표시(+ 전송 버튼 비활성화)를 추가했다.
//
// 사용자 요청(2026-10-18, "에이전트가 실행되는 경우 어떤 에이전트가
// 어떤 동작을 하고 있는지 알 수 있도록... 펼침/닫힘 UI로... 에이전트
// 그룹 포함") — 메시지 하나로 여러 Job이 한꺼번에 트리거될 수 있다
// (복수 @멘션, 또는 @Team 멘션 하나가 그 팀 전원을 한 Job으로 묶어
// 실행). "N건 진행 중"이라는 뭉뚱그린 문구 대신, 어떤 Agent/Team이
// 각각 실행 중/완료/실패인지 펼쳐 볼 수 있는 목록으로 바꾼다. 다만
// CrewAI Crew(그룹) 실행은 `crew.kickoff()` 자체가 동기 호출이라
// 중간 단계 이벤트를 스트리밍해주지 않는다 — "그룹 안에서 지금 어느
// 멤버가 뭘 하는지"까지는 실측으로 확인 불가능해서, 있는 그대로
// "그룹 전체가 실행 중/완료"로만 정직하게 보여준다(없는 정보를
// 지어내지 않는다).
function _conversationJobLabel(triggered, participants) {
  if (triggered.trigger_type === "agent_mention") {
    const p = participants.find(p => p.participant_type === "agent" && p.participant_id === triggered.agent_id);
    return { icon: "🤖", text: (p && p.display_name) ? p.display_name : "Agent #" + triggered.agent_id };
  }
  if (triggered.trigger_type === "team_mention") {
    const p = participants.find(p => p.participant_type === "team" && p.participant_id === triggered.team_id);
    const name = (p && p.display_name) ? p.display_name : "Team #" + triggered.team_id;
    return { icon: "👥", text: name + " (그룹 전체 실행 — 멤버별 진행 상황은 CrewAI가 제공하지 않음)" };
  }
  return { icon: "🧠", text: "기본 AI" };
}

function _renderConversationPendingPanel(jobMeta) {
  const indicator = document.getElementById("conv-pending-indicator");
  if (!indicator) return;
  indicator.style.display = "";
  const rows = jobMeta.map(m =>
    '<li data-job-progress-id="' + m.jobId + '">' +
    '<span class="job-status-badge running">● 실행 중</span>' +
    m.icon + ' ' + esc(m.text) +
    '</li>'
  ).join("");
  indicator.innerHTML =
    '<details class="pending-indicator-panel" open>' +
    '<summary><span class="spinner"></span>응답 생성 중(' + jobMeta.length +
    '건)… Agent/Team 호출은 몇 분 걸릴 수 있습니다</summary>' +
    '<ul class="job-progress-list">' + rows + '</ul>' +
    '</details>';
  const sendBtn = document.querySelector('#conv-message-form button[type="submit"]');
  if (sendBtn) sendBtn.disabled = true;
}

function _updateConversationJobStatus(jobId, status, error) {
  const li = document.querySelector('[data-job-progress-id="' + jobId + '"]');
  if (!li) return;
  const badge = li.querySelector(".job-status-badge");
  if (!badge) return;
  if (status === "succeeded") {
    badge.className = "job-status-badge succeeded";
    badge.textContent = "✓ 완료";
    return;
  }
  // 사용자 보고(2026-10-18, "실패 메시지 노출시 깨져 보임") — 배지에
  // 에러 문구 전체를 넣지 않는다(배지는 "✗ 실패"로 짧게 유지). 자세한
  // 내용은 별도 줄(.job-error-detail, CSS에서 flex-basis:100%)로
  // 떨어뜨려 폭 안에서 정상적으로 줄바꿈되게 한다.
  badge.className = "job-status-badge failed";
  badge.textContent = "✗ 실패";
  if (error) {
    const detail = document.createElement("div");
    detail.className = "job-error-detail";
    detail.textContent = error;
    li.appendChild(detail);
  }
}

// 전부 끝나면 접어서(펼침/닫힘의 "닫힘") 이번 메시지에 어떤 Agent/Team이
// 관여했는지 기록만 남긴다 — 다음 메시지를 보내면 이 패널 전체가
// 새로 교체된다(무한히 쌓이지 않음).
function _finalizeConversationPendingPanel(allSucceeded) {
  const indicator = document.getElementById("conv-pending-indicator");
  const details = indicator && indicator.querySelector("details");
  if (details) {
    details.open = false;
    const summary = details.querySelector("summary");
    if (summary) {
      summary.textContent = (allSucceeded ? "✓ 응답 완료" : "⚠ 일부 실패") + " — 펼쳐서 진행 내역 보기";
    }
  }
  const sendBtn = document.querySelector('#conv-message-form button[type="submit"]');
  if (sendBtn) sendBtn.disabled = false;
}

// 사용자 보고(2026-10-08, "응답을 기다리는 중…으로 나오고 대화에
// 모델의 응답이 표시되지 않음") — 원래는 메시지를 보낸 뒤 "응답을
// 기다리는 중…" 문구만 한 번 띄우고 끝이라, 실제로 응답이 도착해도
// (Team 멘션처럼 CrewAI가 몇 분씩 걸리는 경우 특히, 실측 — 약
// 6분) 그 문구가 그대로 남아 있어 "멈췄다/안 된다"로 오인하게 했다.
// Task 워크스페이스의 `pollExecutionJob()`과 같은 패턴을 대화 쪽에도
// 적용해 완료/실패를 실제로 확인하고 상태 문구를 갱신한다.
// 실행 중 단계 표시(2026-11-23, P0-2) — 작업 행의 `progress`(지금 하는 일)와 시작 후 경과 시간을
// 진행 패널의 그 작업 줄에 보여 준다. `created_at`은 SQLite `datetime('now')`라 UTC다.
function _updateConversationJobProgress(jobId, job) {
  const li = document.querySelector('[data-job-progress-id="' + jobId + '"]');
  if (!li || !job || job.status !== "running") return;
  let el = li.querySelector(".job-progress-step");
  if (!el) {
    el = document.createElement("span");
    el.className = "job-progress-step muted";
    li.appendChild(el);
  }
  const started = Date.parse(String(job.created_at || "").replace(" ", "T") + "Z");
  const sec = isNaN(started) ? null : Math.max(0, Math.round((Date.now() - started) / 1000));
  const elapsed = sec === null ? "" : (sec >= 60 ? Math.floor(sec / 60) + "분 " + (sec % 60) + "초" : sec + "초");
  el.textContent = "— " + (job.progress || "준비 중") + (elapsed ? " · " + elapsed + " 경과" : "");
  _showPartialOutput(li, job.partial_output);
}

// 작성 중 미리보기(2026-11-24, P0-2 응답 스트리밍) — 작업 행의 `partial_output`(지금까지 받은 글)을
// 진행 줄 아래에 보여 준다. 마크다운 렌더링 없이 그대로(완성본은 응답 메시지로 따로 나간다), 끝부분만.
// 결과물에 표가 있는지(Excel 내보내기 버튼 표시용, 2026-11-24 P1-7) — 마크다운 표 구분선 또는 HTML 표.
function _hasTable(text) {
  const t = text || "";
  return t.indexOf("|---") !== -1 || t.indexOf("| ---") !== -1 || t.indexOf("|:--") !== -1 || t.indexOf("| :--") !== -1 ||
    t.toLowerCase().indexOf("<table") !== -1;
}

function _showPartialOutput(container, text) {
  let box = container.querySelector(".job-partial-output");
  if (!text) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement("div");
    box.className = "job-partial-output";
    box.innerHTML = '<div class="muted">작성 중… (완성되면 응답으로 바뀝니다)</div><pre></pre>';
    container.appendChild(box);
  }
  const pre = box.querySelector("pre");
  const shown = text.length > 2000 ? "…" + text.slice(-2000) : text;
  if (pre.textContent !== shown) {
    pre.textContent = shown;
    pre.scrollTop = pre.scrollHeight;
  }
}

async function pollConversationJob(conversationId, jobId, intervalMs, timeoutMs, onTick) {
  // 사용자 보고(2026-10-14, "일부 응답이 실패했습니다: timed out") 재발 —
  // 백엔드 Ollama 타임아웃을 300초에서 600초로 올리면서(`llm.py`의
  // `_call_ollama()` 참고, 실측: 느린 로컬 35B 모델이 337.7초 만에
  // 정상 완료됨을 확인) 이 폴링 포기 시한도 같이 올린다 — 백엔드가
  // 아직 기다리는 중인데 프런트가 먼저 포기하지 않도록 항상 백엔드
  // 타임아웃보다 여유 있게 길게 유지한다.
  // 재발(2026-10-19, "@자율 개발 크루" 실측) — 매니저가 로컬 Ollama
  // 모델로 실제 위임까지 하면(여러 차례 LLM 왕복) 단일 호출보다
  // 훨씬 오래 걸린다. 백엔드 Job 자체는 계속 돌고 있는데
  // (`conversation_jobs.status='running'`, 실측 12분 넘게도 진행 중)
  // 프런트가 700초 만에 먼저 포기해 "실패"로 잘못 보였다 — 백엔드는
  // 멈추지 않았으므로 폴링 포기 시한을 30분으로 넉넉히 늘린다.
  // 사용자 보고(2026-10-28, "프레젠테이션 자료 작성자" 응답 생성 15분
  // 초과 실패) 대응으로 백엔드가 deep_reasoning tier Agent에는 30분
  // (1800초)까지 기다리도록 늘렸다 — 이 폴링 포기 시한이 그와 똑같으면
  // 백엔드가 막 완료되는 순간 프런트가 먼저 포기할 수 있으므로, 항상
  // 백엔드 최댓값보다 여유 있게 길게 유지하는 기존 원칙대로 40분으로
  // 같이 올린다.
  const deadline = Date.now() + (timeoutMs || 2400000); // Team 멘션(CrewAI, 로컬 모델 위임 체인)은 수십 분 걸릴 수 있음
  while (Date.now() < deadline) {
    const jobs = await api("GET", "/api/conversations/" + conversationId + "/jobs");
    const job = jobs.find(j => j.id === jobId);
    if (job && job.status !== "running") return job;
    if (job && onTick) onTick(job);
    await new Promise(r => setTimeout(r, intervalMs || 2000));
  }
  throw new Error("응답 완료 확인 시간이 초과됐습니다 — 잠시 후 새로고침해 주세요.");
}

// 사용자 요청(2026-10-19, "대화를 닫고 다시 연 상태에서 아직 결과
// 완료 전인 상황에서 에이전트가 작업 중임을 알려주는 메시지가
// 출력되는지 확인하고 개선해줘") — 기존엔 이 진행 패널이 메시지를
// "보내는" 그 순간에만 떴다(`conv-message-form` submit 핸들러 안에서만
// 호출). 메시지를 보낸 뒤 탭을 닫거나 다른 화면으로 갔다가 Job이 아직
// 끝나기 전에 같은 대화를 다시 열면, 그 시점엔 이미 "보내는 순간"이
// 지났으므로 이 함수가 한 번도 안 불려 진행 중 표시가 전혀 없었다 —
// 화면만 보면 아무 일도 안 하는 것처럼 보였다(실측: `자율 개발 크루`
// 22분짜리 실행 도중 재현). 보내는 흐름과 "다시 열었더니 아직 실행
// 중"인 흐름이 완전히 같은 로직(패널 표시→폴링→완료 반영)을 쓰도록
// 공용 함수로 뺀다 — `renderConversationDetail()`이 대화를 열 때마다
// `GET .../jobs`로 실행 중인 Job이 있는지 직접 확인해 이 함수를 부른다.
function _watchConversationJobs(conversationId, jobEntries, participants, conv) {
  if (!jobEntries || !jobEntries.length) return;
  const jobMeta = jobEntries.map(j => {
    const label = _conversationJobLabel(j, participants);
    return { jobId: j.job_id, icon: label.icon, text: label.text };
  });
  _renderConversationPendingPanel(jobMeta);
  Promise.allSettled(
    jobMeta.map(m =>
      pollConversationJob(conversationId, m.jobId, undefined, undefined,
        job => _updateConversationJobProgress(m.jobId, job)).then(job => {
        _updateConversationJobStatus(m.jobId, job.status, job.error);
        refreshConversationMessages(conversationId, participants, conv);
        return job;
      }).catch(e => {
        _updateConversationJobStatus(m.jobId, "failed", e.message);
        throw e;
      })
    )
  ).then((settled) => {
    const failed = settled.filter(s => s.status === "rejected" || (s.value && s.value.status === "failed"));
    _finalizeConversationPendingPanel(failed.length === 0);
    refreshConversationMessages(conversationId, participants, conv);
    showMsg(
      "global-msg",
      failed.length
        ? "일부 응답이 실패했습니다 — 위 진행 내역을 펼쳐서 확인하세요."
        : "응답이 도착했습니다.",
      failed.length ? "error" : "ok"
    );
  });
}

// ---------- 전역 @멘션 브라우저 알림(사용자 요청, 2026-10-14, "호출
// 받은 사람에게 브라우저 알림이 오고, 알림 클릭시 대화 참가 가능하도록
// 개선해줘") — 특정 대화 화면을 보고 있지 않아도(다른 탭을 보고 있어도)
// 동작해야 하므로 `boot()`에서 로그인 성공 시 한 번만 시작하는 전역
// 폴링이다(개별 대화 화면의 `_conversationPollTimer`와는 별개). ----------

let _mentionNotifyTimer = null;
// 버그 수정(2026-10-14, 사용자 보고 "호출이 안 되는 거 같아" — 실측
// 재현) — 원래는 "최초 폴링은 알림 없이 기준점만 잡는다"는 클라이언트
// 메모리 커서로 중복 알림을 막았는데, 그 탭이 멘션이 이미 온 *뒤에*
// 열리면(실제 재현: admin이 먼저 세 번 호출하고 pm_kim은 그 뒤에 로그인)
// 그 멘션들이 전부 "최초 폴링"으로 분류돼 영영 알림이 안 떴다. 이제
// "안 읽음" 판정 자체를 서버가 들고 있어서(`conversation_participants.
// last_read_message_id` — 그 대화를 실제로 열면 갱신됨) 서버가 돌려주는
// 건 전부 "진짜 안 읽은 멘션"이다. 이 Set은 그래도 같은 폴링 주기
// 내에서(아직 그 대화를 안 열어 계속 "안 읽음"으로 돌아오는 동안) 20초
// 마다 같은 멘션을 또 알림으로 띄우지 않기 위한 세션 한정 중복 방지일
// 뿐이다(페이지 새로고침하면 리셋돼도 무해 — 서버가 "안 읽음"이면 다시
// 한 번 알림이 뜨는 정도).
let _notifiedMentionIds = new Set();

function _requestNotificationPermissionIfNeeded() {
  if (typeof Notification === "undefined") return; // 미지원 브라우저는 조용히 건너뜀
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

async function _openConversationFromNotification(conversationId) {
  window.focus();
  await selectTab("conversations");
  renderConversationDetail(conversationId, "conversation-detail");
}

async function _pollMyMentions() {
  let mentions;
  try {
    mentions = await api("GET", "/api/conversations/my-mentions");
  } catch (e) {
    return; // 네트워크 일시 단절 등 — 다음 주기에 다시 시도
  }
  if (!mentions.length) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  for (const m of mentions) {
    if (_notifiedMentionIds.has(m.message_id)) continue;
    _notifiedMentionIds.add(m.message_id);
    const notif = new Notification("💬 \"" + m.conversation_title + "\"에서 호출됨", {
      body: m.content,
      tag: "hair-mention-" + m.message_id,
    });
    notif.onclick = () => {
      _openConversationFromNotification(m.conversation_id);
      notif.close();
    };
  }
}

// 사용자 요청(2026-11-12, "업무를 요청하고, 협업공간에서 진행과정과
// 결과물 확인이 어려워(숨겨져 있는 느낌)") — "인박스" 탭을 직접 열어야만
// 결재 대기/실행 실패/완료 소식을 알 수 있었다(다른 탭에 있으면 전혀
// 모름). 기존 멘션 폴링과 같은 주기(20초)로 `GET /api/inbox`를 같이
// 불러 탭 버튼에 건수 배지를 띄운다 — 새 폴링 체계를 따로 만들지 않고
// 같은 타이머에 얹는다.
// 인박스 새 항목 데스크톱 알림(2026-11-25, P2-15) — 예전엔 건수 배지만 바뀌어 다른 탭·창에 있으면 결재 대기·실패·완료를
// 몰랐다. 처음 불러온 목록은 "이미 있던 것"으로만 기억하고(로그인 직후 알림 폭주 방지), 그 뒤 새로 생긴 항목만 알린다.
let _inboxSeenKeys = null;
function _inboxItemKey(i) {
  return i.type + ":" + (i.approval_id || i.job_id || i.run_id || i.deliverable_id || "") + ":" + (i.task_id || "") + ":" + (i.completed_at || i.failed_at || "");
}
function _inboxNotifyText(i) {
  if (i.type === "approval_pending") return ["🗳 결재 대기", (i.task_role_name || i.resource_name || "새 결재 요청")];
  if (i.type === "execution_failed") return ["⚠ AI 실행 실패", (i.role_name || "") + (i.error ? " — " + String(i.error).slice(0, 80) : "")];
  if (i.type === "pipeline_failed") return [i.interrupted ? "⏸ 파이프라인 중단(서버 재시작)" : "⚠ 파이프라인 실패", (i.template_name || "") + " · " + (i.role_name || "")];
  if (i.type === "low_gate_score") return ["🟡 품질 점검 미달", i.role_name || ""];
  if (i.type === "completed") return ["✅ 업무 완료", i.role_name || ""];
  return ["HAIR 알림", i.role_name || ""];
}
function _notifyNewInboxItems(items) {
  const keys = new Set(items.map(_inboxItemKey));
  if (_inboxSeenKeys === null) { _inboxSeenKeys = keys; return; }
  const fresh = items.filter(i => !_inboxSeenKeys.has(_inboxItemKey(i)));
  _inboxSeenKeys = keys;
  if (!fresh.length || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  fresh.slice(0, 3).forEach(i => {
    const [title, body] = _inboxNotifyText(i);
    const notif = new Notification(title, { body, tag: "hair-inbox-" + _inboxItemKey(i) });
    notif.onclick = () => { window.focus(); selectTab(i.type === "approval_pending" ? "approvals" : "inbox"); notif.close(); };
  });
  if (fresh.length > 3) new Notification("HAIR — 새 항목 " + fresh.length + "건", { body: "인박스에서 확인하세요.", tag: "hair-inbox-many" });
}

async function _pollInboxBadge() {
  const badge = document.getElementById("inbox-badge-count");
  if (!badge) return; // 아직 로그인 전/로그아웃 뒤 — nav가 없음
  let items;
  try {
    items = await api("GET", "/api/inbox");
  } catch (e) {
    return; // 네트워크 일시 단절 등 — 다음 주기에 다시 시도
  }
  _notifyNewInboxItems(items);
  if (items.length) {
    badge.textContent = items.length > 99 ? "99+" : String(items.length);
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

function startMentionNotificationPolling() {
  if (_mentionNotifyTimer) return; // 이미 돌고 있으면 중복 시작하지 않음
  _requestNotificationPermissionIfNeeded();
  _pollMyMentions();
  _pollInboxBadge();
  _mentionNotifyTimer = setInterval(() => { _pollMyMentions(); _pollInboxBadge(); }, 20000);
}

function stopMentionNotificationPolling() {
  _inboxSeenKeys = null;  // 다음에 로그인하는 사람에게 이전 사람의 "본 항목"이 이어지지 않게(2026-11-25, P2-15)
  if (_mentionNotifyTimer) {
    clearInterval(_mentionNotifyTimer);
    _mentionNotifyTimer = null;
  }
  _notifiedMentionIds = new Set(); // 다음 로그인(다른 계정일 수 있음)은 새로 시작
}

async function renderConversations(content) {
  _stopConversationPolling();
  const list = await api("GET", "/api/conversations");
  content.innerHTML =
    '<section class="card"><h2>대화</h2>' +
    helpBox(
      '<ul class="muted help-list">' +
      '<li>로그인하면 바로 쓸 수 있는 대화 공간입니다 — 사람·Agent·Agent 그룹(Team)을 불러 대화하듯 업무를 처리하세요.</li>' +
      '<li>결재·Gate 채점이 필요한 정식 업무로 넘기려면 대화 상단의 "📋 업무 요청으로 전환"을 쓰세요 — 대화 내용으로 업무 요청 초안을 만들고(AI 정리는 선택), 다듬어 제출하면 "업무 요청" 탭에 올라갑니다.</li>' +
      '</ul>'
    ) +
    '<form class="inline" id="new-conversation-form">' +
    '<input id="new-conversation-title" placeholder="새 대화 제목(선택)" style="flex:1">' +
    '<button class="primary" type="submit">새 대화 시작</button>' +
    '</form>' +
    table(["ID", "제목", "시작일", "동작"], list.map(c => [
      c.id,
      esc(c.title || "(제목 없음)") + (c.linked_task_id ? ' <span class="muted">(업무 #' + c.linked_task_id + ')</span>' : ''),
      esc(fmtTime(c.created_at)),
      '<button class="small" data-open-conversation="' + c.id + '">열기</button> ' +
      '<button class="small danger" data-leave-conversation="' + c.id + '">나가기</button> ' +
      (c.created_by === ME.id
        ? '<button class="small danger" data-delete-conversation="' + c.id + '">삭제</button>'
        : ''),
    ]), { empty: "아직 대화가 없습니다. 위에 제목(선택)을 적고 <b>새 대화 시작</b>을 누른 뒤 <b>@이름</b>으로 Agent·사람을 부르세요. <button class=\"small\" data-go-tab=\"home\">홈에서 AI에게 일 맡기기</button>" }) +
    '<div id="conversation-detail"></div>' +
    '</section>';

  document.getElementById("new-conversation-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const title = document.getElementById("new-conversation-title").value.trim();
    try {
      const conv = await api("POST", "/api/conversations", { title });
      showMsg("global-msg", "새 대화를 시작했습니다.", "ok");
      await renderConversations(content);
      renderConversationDetail(conv.id, "conversation-detail");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  content.querySelectorAll("[data-open-conversation]").forEach(btn => {
    btn.onclick = () => renderConversationDetail(Number(btn.dataset.openConversation), "conversation-detail");
  });
  content.querySelectorAll("[data-delete-conversation]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 대화를 메시지·참가자까지 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/conversations/" + btn.dataset.deleteConversation);
        showMsg("global-msg", "대화를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
  content.querySelectorAll("[data-leave-conversation]").forEach(btn => {
    btn.onclick = async () => {
      if (!(await confirmModal("이 대화에서 나갑니다. 계속할까요?"))) return;
      try {
        const result = await api("POST", "/api/conversations/" + btn.dataset.leaveConversation + "/leave");
        showMsg(
          "global-msg",
          result.conversation_deleted ? "대화에서 나갔습니다(마지막 참가자라 대화도 삭제됐습니다)." : "대화에서 나갔습니다.",
          "ok"
        );
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    };
  });
}

async function refreshConversationMessages(conversationId, participants, conv) {
  const box = document.getElementById("conv-messages");
  if (!box) return;
  // 사용자 보고(2026-11-16, "HTML 수정하려해도, HTML 결과물이
  // 리프레시되며 입력을 못하게 막아(AI 수정 요청 항목이 닫힘)") —
  // 이 함수가 6초마다 메시지 목록 전체를 innerHTML로 통째로
  // 갈아끼우는데(아래), "수정" 버튼으로 열어둔 편집 영역(텍스트/
  // CodeMirror·AI 피드백 입력창)도 그 DOM의 일부라 매번 같이
  // 사라지면서 입력 중이던 내용까지 날아갔다. 지금 열려 있는 편집
  // 영역이 하나라도 있으면 이번 갱신 주기는 건너뛴다 — 편집을
  // 끝내면(저장/취소) 다음 주기부터 다시 정상적으로 갱신된다. 새
  // 메시지 반영이 그만큼 늦어지는 트레이드오프는 받아들인다(입력
  // 중인 내용을 잃는 것보다 훨씬 낫다).
  const openEdit = Array.from(box.querySelectorAll(".artifact-edit"))
    .some(el => el.style.display !== "none");
  if (openEdit) return;
  try {
    const messages = await api("GET", "/api/conversations/" + conversationId + "/messages");
    box.innerHTML = messages.length ? messages.map(m => _renderConversationMessage(m, participants, conv)).join("")
      : '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>';
  } catch (e) {
    // 참가자가 아니게 됐으면(제거됨) 403 — 조용히 무시.
  }
}

// 사용자 보고(2026-10-14, "대화창에 사용중인 모델이 명시되도록 UI를
// 개선해줘") — Agent 멘션 응답은 지금까지 "(AI)"만 보이고 실제로 어떤
// provider/model이 썼는지 전혀 안 보였다(기본 AI만 conv.llm_model을
// 보여줬는데, 그나마도 "그 메시지를 만들 때 실제로 쓰인 값"이 아니라
// "지금 대화 설정값"이라 사용자가 나중에 모델을 바꾸면 과거 메시지
// 라벨까지 거슬러 바뀌는 부정확함이 있었다). 이제 메시지마다 실제로
// 쓰인 provider/model이 `conversation_messages.llm_provider/llm_model`
// 에 저장되므로(`conversations.py`의 `_finish_job_with_message()`/
// `run_team_mention_job()` 참고) 그 값을 우선 쓰고, 마이그레이션 이전
// 과거 메시지(그 컬럼이 비어 있음)만 기존처럼 대화 설정값으로 폴백한다.
function _conversationModelBadge(provider, model) {
  if (!model) return "";
  return provider ? provider + "/" + model : model;
}

function _conversationAuthorLabel(m, participants, convLlmModel) {
  const authorType = m.author_type, authorId = m.author_id;
  // 실측으로 발견한 비대칭(2026-10-13) — Agent 작성자는 participants에서
  // display_name을 찾아 보여주는데, 사람 작성자는 찾아보지도 않고 바로
  // "사용자 #3" 식으로 ID만 보여주고 있었다. 본인이면 로그인 아이디를,
  // 아니면 participants에서 같은 방식으로 찾는다(둘 다 없을 때만 ID로
  // 폴백 — 예: 대화를 나간 뒤에도 과거 메시지는 남는 경우).
  if (authorType === "human") {
    if (ME && authorId === ME.id) return esc(ME.username);
    const hp = participants.find(p => p.participant_type === "human" && p.participant_id === authorId);
    return hp && hp.display_name ? esc(hp.display_name) : "사용자 #" + authorId;
  }
  const badge = _conversationModelBadge(m.llm_provider, m.llm_model) ||
    (authorId === conversationsDefaultAiAuthorId ? (convLlmModel || "") : "");
  if (authorId === conversationsDefaultAiAuthorId) {
    return "AI" + (badge ? " (" + esc(badge) + ")" : "");
  }
  const p = participants.find(p => p.participant_type === "agent" && p.participant_id === authorId);
  if (!p) return "AI 에이전트 #" + authorId;
  return esc(p.display_name) + " (AI" + (badge ? " · " + esc(badge) : "") + ")";
}

// `conversation_messages.author_id`의 예약 sentinel — 실존 agent_pool
// 행이 없는 "대화 자체의 기본 AI"를 가리킨다(`conversations.py`의
// `DEFAULT_AI_AUTHOR_ID`와 반드시 같은 값이어야 한다 — agent_pool.id
// 는 AUTOINCREMENT라 0이 될 수 없어 안전한 sentinel이다).
const conversationsDefaultAiAuthorId = 0;

// 사용자 요청(2026-10-14, "단위 메시지 삭제 기능") — 본인이 보낸 사람
// 메시지는 본인이, 그 외(AI/Agent 응답 포함)는 대화 생성자가 지울 수
// 있다(`conversations.delete_message()`와 같은 권한 — 서버가 최종
// 검증하므로 여기선 버튼 노출 여부만 결정한다).
// 사용자 요청(2026-10-18, "@자율 개발 크루" 결과물 품질 분석 후속) —
// Gate A-G가 fail해도 대화창 경로에서는 조용히 버려지던 문제의 최소
// 안전장치. 측정된 Gate 중 몇 개가 통과했는지만 간단히 보여준다(상세
// 점수는 과하다 — "Crew 실행" 관리자 화면에만 그 수준 상세를 둔다).
function _conversationGateBadge(m) {
  if (!m.gate_scores) return "";
  const measured = Object.values(m.gate_scores).filter(g => !g.not_measured && !g.advisory);
  if (!measured.length) return "";
  const passed = measured.filter(g => g.passed).length;
  const allPass = passed === measured.length;
  return ' <span class="gate-badge ' + (allPass ? "gate-ok" : "gate-warn") + '" ' +
    'title="자동 품질 점검(Gate A-G) 중 통과한 수 — 참고용, 업무 워크스페이스의 정식 기록과는 별개">' +
    (allPass ? "품질 점검 " : "⚠ 품질 점검 ") + passed + "/" + measured.length + " 통과" +
    "</span>";
}

// 실측으로 확인한 문제(2026-10-18) — "자율 개발 크루"가 매니저를 통해
// 실행됐는데도 매니저가 위임 없이 혼자 처리한 경우가 있었다("자율
// 개발"이라는 이름과 달리 실제로는 1인 작업). 몇 건 위임됐는지를
// 투명하게 보여준다.
function _conversationDelegationBadge(m) {
  if (m.delegation_count === null || m.delegation_count === undefined) return "";
  if (m.delegation_count > 0) {
    return ' <span class="gate-badge gate-ok">위임 ' + m.delegation_count + '건</span>';
  }
  return ' <span class="muted" style="font-size:0.85em">위임 0건</span>';
}

// 사용자 요청(2026-11-17, "Human Agency Scale/ReAct 등 개념과 비교해
// HAIR-Platform 개선이 필요한 Insight 정리" → "구현 시작") — "기본
// AI"/"@Agent 멘션"(단일) 응답은 `agent_runtime.retrieve_context()`가
// 검색 결과를 1회만 프롬프트에 끼워 넣을 뿐 CrewAI처럼 Agent가 스스로
// 다시 검색을 호출하는 루프가 없다(`agent_runtime.py` 자체 주석에
// 이미 명시된 구조적 한계) — 사용자가 "검색 결과가 부족해 보이는데
// 왜 다시 안 찾아보지?"라고 오해하지 않도록 이 메시지가 재질의가
// 불가능한 1회성 응답이라는 걸 미리 알려준다. `delegation_count`가
// 있는 메시지(실제 CrewAI 위임 루프를 거친 팀 실행)와 `llm_provider`
// 조차 없는 메시지(파이프라인 진행 상황 같은 순수 알림, LLM 호출
// 자체가 없었음, `conversations.post_pipeline_run_status_message()`)
// 는 둘 다 이 배지가 해당하지 않는다 — 셋을 가르는 기준은 이미 DB에
// 있는 두 컬럼(`llm_provider`/`delegation_count`)뿐이라 서버 변경
// 없이 프런트에서만 구분할 수 있다.
function _conversationSingleShotBadge(m) {
  if (m.author_type !== "agent") return "";
  if (m.delegation_count !== null && m.delegation_count !== undefined) return "";
  if (!m.llm_provider) return "";
  return ' <span class="gate-badge gate-na" ' +
    'title="단발 실행으로 답했습니다 — 계산·차트 같은 커스텀 도구는 필요할 때 직접 호출하지만, 검색 결과는 요청 직전에 한 번 조회한 것만 반영하고 스스로 다시 찾아보지 않습니다(팀·Crew 실행과 다른 점입니다).">' +
    '단발 응답</span>';
}

// 사용자 요청(2026-10-19, "HTML 표시시 css, script 등 실행가능하도록
// 개선 가능한지 확인해줘") — 실측 재현: "자율 개발 크루"가 ```html
// 코드펜스 없이 `<!DOCTYPE html>`로 바로 시작하는 완성된 게임을 응답
// 하면, 대화 메시지는 항상 `renderContent(m.content)`(contentType
// 미지정 → 마크다운 경로)를 타서 marked가 이 raw HTML을 그대로
// 통과시키고 DOMPurify가 `<script>`(와 경우에 따라 `<style>`)를
// 안전을 위해 제거해버린다 — 결과물이 캔버스만 텅 빈 채 스타일도
// 동작도 없는 반쪽짜리로 보였다(스크린샷으로 실측). 메시지 본문이
// 통째로 HTML 문서(또는 그걸 감싼 코드펜스)로 "시작"하면
// `renderContent(raw, "html")`(Task 산출물이 이미 쓰는, `sandbox=
// "allow-scripts"` iframe 안에서 CSS/script까지 전부 실행하는 안전한
// 경로)로 보낸다 — 설명과 코드가 섞인 일반 응답(예: 기획서 §5에 코드가
// 일부 포함된 경우)은 `<!DOCTYPE html>`로 시작하지 않으므로 그대로
// 마크다운 경로를 탄다(오탐 거의 없음).
function _looksLikeFullHtmlDocument(raw) {
  const unwrapped = stripOuterFence((raw || "").trim());
  return /^<!DOCTYPE\s+html/i.test(unwrapped) || /^<html[\s>]/i.test(unwrapped);
}

// 사용자 보고(2026-10-19, 스크린샷 — "@자율 개발 크루 위 기획을
// 구현해줘"처럼 멘션 대상과 실제 요청 문장이 구분 없이 붙어 보임) —
// 본문 중 실제로 이 대화에 초대된 참가자를 가리키는 "@이름"만(원문
// 추측이 아니라 백엔드 `_detect_mentions()`와 똑같이 참가자 목록
// 기준) 받는 쪽 색과 맞춘 pill로 감싼다. 긴 이름부터 치환해야
// "자율 개발 크루 테스트"가 "자율 개발 크루"에 먼저 부분 매칭돼
// 잘리는 걸 막는다.
function _highlightMentions(content, participants) {
  const mentions = (participants || [])
    .filter(p => p.display_name)
    .map(p => ({
      marker: "@" + p.display_name,
      cls: p.participant_type === "human" ? "mention-human" : "mention-agent",
    }))
    .sort((a, b) => b.marker.length - a.marker.length);
  let result = content;
  mentions.forEach(({ marker, cls }) => {
    if (!result.includes(marker)) return;
    result = result.split(marker).join(
      '<span class="mention-tag ' + cls + '">' + esc(marker) + '</span>'
    );
  });
  return result;
}

// 사용자 요청(2026-10-22, "호출된 사람,에이전트가 대화 내용과
// 구분되도록 ... 입력창에서도 적용해줘") — `_highlightMentions()`와
// 같은 매칭 로직이지만 두 가지가 다르다: (1) 입력창은 보내기 전 raw
// 텍스트를 `marked`/DOMPurify 없이 곧장 mirror div의 innerHTML에
// 꽂으므로, 멘션이 아닌 나머지 텍스트도 먼저 전부 `esc()`해야 한다
// (`_highlightMentions()`는 이후 markdown 렌더링 파이프라인을 믿고
// 멘션이 아닌 부분은 이스케이프하지 않는다 — 입력창엔 그 안전망이
// 없다). (2) pill(.mention-tag, padding 있는 inline-block)을 쓰면
// 그 구간만 글자 폭이 달라져 투명 input 위의 실제 캐럿 위치와 mirror
// 텍스트가 어긋나므로, 폭을 안 바꾸는 font-weight+color만 쓴다.
function _highlightMentionsForInput(content, participants) {
  const escaped = esc(content || "");
  const mentions = (participants || [])
    .filter(p => p.display_name)
    .map(p => ({
      marker: esc("@" + p.display_name),
      cls: p.participant_type === "human" ? "mention-inline-human" : "mention-inline-agent",
    }))
    .sort((a, b) => b.marker.length - a.marker.length);
  let result = escaped;
  mentions.forEach(({ marker, cls }) => {
    if (!result.includes(marker)) return;
    result = result.split(marker).join('<span class="' + cls + '">' + marker + '</span>');
  });
  return result;
}

// 타이핑 중인 input 위에 겹친 투명 오버레이(mirror) div를 실시간으로
// 동기화한다 — IME(한글) 조합 중에도 `input` 이벤트가 그대로 발생하므로
// 별도 처리가 필요 없다. 가로 스크롤(긴 텍스트가 input 폭을 넘었을 때)도
// `scrollLeft`를 그대로 맞춰준다.
function setupMentionInputMirror(inputEl, mirrorEl, participants) {
  function sync() {
    mirrorEl.innerHTML = _highlightMentionsForInput(inputEl.value, participants);
    mirrorEl.scrollLeft = inputEl.scrollLeft;
  }
  inputEl.addEventListener("input", sync);
  inputEl.addEventListener("keyup", sync);
  inputEl.addEventListener("click", sync);
  inputEl.addEventListener("scroll", sync);
  sync();
  // `insertMention()`(드롭다운 클릭으로 멘션 삽입)은 `inputEl.value`를
  // 직접 대입하는데, 이건 `input` 이벤트를 발생시키지 않는다(vanilla JS
  // 속성 — 사용자 타이핑만 이벤트를 낸다) — 호출부가 `onMentionSelected`
  // 콜백에서 이 `sync`를 직접 불러줘야 드롭다운으로 멘션을 고른 직후에도
  // mirror가 갱신된다.
  return { sync };
}

// 사용자 요청(2026-10-21, "Layer 4") — `who`는 이미 `esc()` 처리된
// 표시 이름 문자열("AI (claude-sonnet-5)", "코드리뷰 Agent (AI ·
// gpt-4o-mini)", "사용자 #3" 등) — 괄호 뒤 provider/model 부가정보는
// 이니셜에 불필요해 `" ("` 앞부분만 쓴다. 기본 AI는 한 글자보다
// 식별하기 쉽게 "AI" 그대로 보여준다.
// 알려진 한계: `who`가 이미 escape된 문자열이라 이름이 `<`/`&`/`"`로
// 시작하면 이니셜이 "&"로 보일 수 있다(실제로는 거의 없는 edge case
// — 별도 처리 안 함).
function _conversationAuthorInitial(who) {
  const core = who.split(" (")[0].trim();
  return core === "AI" ? "AI" : core.slice(0, 1).toUpperCase();
}

function _renderConversationMessage(m, participants, conv) {
  const side = m.author_type === "agent" ? "chat-agent" : "chat-human";
  const who = _conversationAuthorLabel(m, participants, conv ? conv.llm_model : null);
  // 사용자 요청(2026-10-19, "대화 초대자... 대화 내용과 구분해서
  // 가독성을 높여줘") — 작성자 이름을 카드 왼쪽 테두리와 같은 색으로
  // 굵게 표시해 본문과 한눈에 구분되게 한다.
  const whoClass = m.author_type === "agent" ? "msg-author-agent" : "msg-author-human";
  const avatarClass = m.author_type === "agent" ? "msg-avatar-agent" : "msg-avatar-human";
  const skillBadge = m.used_skill
    ? ' <span class="gate-badge gate-ok">스킬: ' + esc(m.used_skill) + '</span>'
    : '';
  const canDelete = conv && ME && (
    (m.author_type === "human" && m.author_id === ME.id) || conv.created_by === ME.id
  );
  const deleteBtn = canDelete
    ? ' <button type="button" class="small danger" data-delete-message="' +
      m.conversation_id + ':' + m.id + '">삭제</button>'
    : '';
  // 사용자 요청(2026-11-10, "대화... 등에서 생성한 최종결과물(필요시
  // 지정한 결과물)이 갤러리에 가도록") — 대화의 모든 메시지가 자동으로
  // 결과물이 되지는 않는다(탐색적 잡담까지 쌓이면 갤러리가 지저분해짐)
  // — 참가자가 "이건 남길 만하다" 고른 메시지만 명시적으로 저장한다.
  // 사용자 요청(2026-11-10, "'결과물로 저장' 버튼을 '갤러리로 보내기'로
  // 이름을 바꿔야 함 — 어디로 가는지 이름에서 바로 알 수 있어야 한다"
  // — HAIR-Platform 전체에서 "OOO로 보내기" 명명을 통일한다: 갤러리
  // 행선지는 "갤러리로 보내기", 지식베이스 행선지는 "지식베이스로
  // 보내기"(아래), Agent 관리 화면의 "지식 색인"도 같은 라운드에서
  // "지식베이스로 보내기"로 맞춘다).
  const saveBtn = conv && ME
    ? ' <button type="button" class="small" data-save-deliverable="' +
      m.conversation_id + ':' + m.id + '">갤러리로 보내기</button>'
    : '';
  // 사용자 요청(2026-11-20, 지식베이스 등록 체계 개편) — 이전엔 admin/
  // manager가 누르면 즉시 색인했다(결재 없음). 이제 참여자 누구나(열람
  // 전용 제외) "제출"하고, admin 결재 후에만 색인된다(임베딩 비용도
  // 승인 뒤에만 발생). 버튼 문구/상태는 `_decorateKnowledgeButtons()`가
  // 최신 제출 상태로 바꿔 준다.
  const knowledgeBtn = conv && ME && ME.role !== "viewer"
    ? ' ' + knowledgeSubmitButton("conversation_message", m.id)
    : '';
  const isFullHtmlDoc = _looksLikeFullHtmlDocument(m.content);
  const contentType = isFullHtmlDoc ? "html" : undefined;
  const bodyContent = isFullHtmlDoc ? m.content : _highlightMentions(m.content, participants);
  // 사용자 요청(2026-11-17, "대화에서 HTML 결과물에 적용된 수정기능을
  // 참조해 모든 대화 결과물에서도 수정이 가능하도록") — 이전엔 완전한
  // HTML 문서로 인식된 메시지에만 "수정"을 붙였다(그 외엔 `bodyContent`
  // 가 원본이 아니라 @멘션 강조 처리가 섞인 표시용 HTML이라, 그대로
  // 저장하면 메시지가 깨질 위험이 있었다). `renderContent()`에 추가한
  // `rawForStorage` 인자(`m.content`, 강조 처리 전 진짜 원본)로 저장/
  // 수정은 항상 원본을 쓰게 분리했으므로, 이제 모든 메시지 타입에
  // "수정"을 붙일 수 있다. 권한은 삭제 버튼(`canDelete`)과 동일
  // (`conversations.edit_message()`가 서버에서 같은 규칙으로 재확인).
  const editTarget = canDelete
    ? { kind: "message", id: m.id, conversationId: m.conversation_id }
    : undefined;
  return '<div class="deliverable-card ' + side + '">' +
    '<div class="muted"><span class="msg-avatar ' + avatarClass + '">' +
    esc(_conversationAuthorInitial(who)) + '</span>' +
    '<strong class="' + whoClass + '">' + who + '</strong>' + skillBadge +
    _conversationGateBadge(m) + _conversationDelegationBadge(m) + _conversationSingleShotBadge(m) + _conversationContextBadge(m) + ' · ' + esc(fmtTime(m.created_at)) +
    // 평가 리포트(2026-11-25) — Agent-Evaluator가 만든 Gate A-G 종합 리포트(새 탭, 격리된 출처).
    (m.has_report ? ' · <a class="small" href="/api/conversations/' + m.conversation_id + '/messages/' + m.id + '/evaluation-report" target="_blank" rel="noopener" title="Agent-Evaluator가 만든 Gate A-G 종합 리포트">평가 리포트</a>' : "") +
    saveBtn + knowledgeBtn + deleteBtn + '</div>' +
    '<div>' + renderContent(bodyContent, contentType, editTarget, m.content) + '</div>' +
    '</div>';
}

async function renderConversationDetail(conversationId, containerId) {
  _stopConversationPolling();
  const detail = document.getElementById(containerId);
  if (!detail) return;
  detail.dataset.conversationId = String(conversationId);
  detail.innerHTML = '<p class="muted">불러오는 중…</p>';

  const modelsByProvider = await fetchModelsByProvider();
  let conv, participants, messages, skillNames, runningJobs;
  try {
    conv = await api("GET", "/api/conversations/" + conversationId);
    participants = await api("GET", "/api/conversations/" + conversationId + "/participants");
    messages = await api("GET", "/api/conversations/" + conversationId + "/messages");
    skillNames = await api(
      "GET",
      "/api/skill-pool/names?conversation=true" + (_convShowDevSkills ? "&include_dev=true" : "")
    );
    // 대화 컨텍스트 정책(2026-11-20) — "이전 대화" 칩이 스킬 기본값을 보여준다.
    try {
      _skillContextPolicies = await api("GET", "/api/skill-pool/context-policies");
    } catch (e) { _skillContextPolicies = {}; }
    // 사용자 요청(2026-10-19, "대화를 닫고 다시 연 상태에서 아직 결과
    // 완료 전인 상황에서 작업 중임을 알려주는 메시지가 출력되는지") —
    // 이 대화를 열 때마다 아직 안 끝난 Job이 있는지 직접 확인한다
    // (이전엔 메시지를 "보내는" 그 순간에만 진행 패널을 띄워서, 보낸
    // 뒤 탭을 닫았다가 Job이 끝나기 전에 다시 열면 진행 중이라는
        // 표시가 전혀 없었다).
    runningJobs = (await api("GET", "/api/conversations/" + conversationId + "/jobs"))
      .filter(j => j.status === "running");
  } catch (e) {
    detail.innerHTML = "";
    showMsg("global-msg", e.message, "error");
    return;
  }

  const participantRows = participants.map(p =>
    '<li>' + esc(label(p.participant_type)) + ' ' + esc(p.display_name || ("#" + p.participant_id)) + '</li>'
  ).join("");

  const messageCards = messages.map(m => _renderConversationMessage(m, participants, conv)).join("");

  detail.innerHTML =
    '<section class="card">' +
    '<h3>' + esc(conv.title || "(제목 없음)") +
    (conv.linked_task_id ? ' <span class="muted">업무 #' + conv.linked_task_id + ' 전용 대화</span>' : '') +
    // 사용자 요청(2026-10-10, "대화 세션 삭제/나가기 기능을 구현해줘")
    // — "삭제"(연쇄 삭제, 되돌릴 수 없음)는 만든 사람(created_by)만
    // 쓸 수 있다(`conversations.delete_conversation()`과 같은 권한
    // 경계). "나가기"는 만든 사람 포함 참가자 누구나 쓸 수 있다 —
    // 처음엔 만든 사람에게 삭제만 보여줬는데, 그러면 다른 참가자가
    // 남아 있어도 "그냥 빠지기"를 못 하고 삭제(전체 파괴)만 고를 수
    // 있는 게 됐다(Playwright로 실제 클릭해보며 발견) — 만든 사람도
    // `leave_conversation()`을 쓸 수 있으므로 버튼도 둘 다 보여준다.
    ' <button class="small danger" id="conv-leave-btn">나가기</button>' +
    (conv.created_by === ME.id
      ? ' <button class="small danger" id="conv-delete-btn">대화 삭제</button>'
      : '') +
    '</h3>' +
    '<div class="inline">' +
    // "AI 에이전트 관리"(provider-{id} select)와 같은 관례 — provider
    // 선택을 JS `.value =` 사후 대입이 아니라 마크업의 `selected`
    // 속성으로 미리 심어둔다(위 사용자 보고 버그의 재발을 막는 더
    // 견고한 방식 — `wireProviderModelSelect()`가 호출되는 시점에
    // 이미 올바른 provider가 선택돼 있어야 모델 목록을 제대로 만든다).
    '<select id="conv-provider">' +
    '<option value=""' + (!conv.llm_provider ? " selected" : "") + '>provider 자동 판별</option>' +
    '<option value="anthropic"' + (conv.llm_provider === "anthropic" ? " selected" : "") + '>Anthropic</option>' +
    '<option value="openai"' + (conv.llm_provider === "openai" ? " selected" : "") + '>OpenAI</option>' +
    '<option value="ollama"' + (conv.llm_provider === "ollama" ? " selected" : "") + '>Ollama(로컬)</option>' +
    '</select>' +
    modelSelectHtml("conv-model") +
    // "AI 에이전트 관리"처럼 매 변경마다 자동저장하지 않고 명시적
    // 버튼으로 한 번에 저장한다 — provider만 바꿔도(모델을 아직 안
    // 고른 상태) 즉시 저장되면 방금까지 고른 모델이 null로 덮어써지는
    // 문제가 있었다(실사용 확인).
    '<button class="small" id="conv-save-model-btn">모델 설정 저장</button>' +
    '<label><input type="checkbox" id="conv-auto-reply"' + (conv.ai_auto_reply ? " checked" : "") + '> AI 자동응답</label>' +
    '</div>' +
    '<p class="muted">AI 자동응답이 켜져 있으면 멘션 없는 메시지에도 매번 LLM 호출이 발생합니다(비용 발생). provider/model을 고른 뒤 "모델 설정 저장"을 눌러야 반영됩니다.</p>' +
    // 사용자 요청(2026-10-14, "대화창에 사용중인 모델이 명시되도록 UI를 개선해줘"). 2026-11-26 사용자 요청
    // ("안내에 오류가 없는지 확인")으로 코드와 대조해 고쳤다 — ① @Team은 "서버 자동 판별"이 아니라 팀 전체가 한
    // 모델을 쓴다(`conversations.run_team_mention_job`: 매니저가 있으면 매니저 Agent에 저장된 모델, 없으면
    // `llm.resolve_provider_and_model(conn=)`의 플랫폼 공통 기본 LLM). ② 이 설정은 기본 AI 응답 말고도 오래된
    // 메시지 요약(`_ensure_context_summary` — @Agent·@Team 응답의 맥락에도 들어감)과 "업무 요청으로 전환"의 AI
    // 정리(`brief_draft_from_conversation`)에 쓰인다. 메시지마다 실제 모델은 작성자 줄(`_conversationAuthorLabel`).
    '<p class="muted">⚠️ 이 모델 설정은 <b>멘션 없는 기본 AI 응답</b>에 쓰입니다(비워 두면 "LLM 설정"의 플랫폼 공통 기본 LLM). ' +
    '<code>@Agent이름</code>으로 부르면 그 Agent에 저장된 모델이, ' +
    '<code>@Team이름</code>으로 부르면 팀 전체가 한 모델(매니저가 있는 팀은 매니저 Agent의 모델, 없으면 플랫폼 공통 기본 LLM)을 씁니다. ' +
    '다만 대화가 10건을 넘으면 오래된 메시지를 요약하는 데와 "업무 요청으로 전환"의 AI 정리에는 멘션과 관계없이 이 설정이 쓰입니다. ' +
    '각 메시지에 실제로 쓰인 모델은 메시지 작성자 줄에 표시됩니다.</p>' +
    // 대화 → 업무 요청 전환(2026-11-23, P1-4) — 대화에서 정한 일을 결재·Gate가 붙는 정식 업무로
    // 넘길 때 내용을 직접 복사하지 않게 한다. 초안은 저장하지 않고, 다듬어 제출해야 만들어진다.
    '<details class="conv-to-brief" id="conv-to-brief"><summary>📋 업무 요청으로 전환</summary>' +
    '<p class="muted">이 대화 내용으로 업무 요청 초안을 만듭니다. 다듬어 제출하면 "업무 요청" 탭에서 할 일로 나누고 배정·결재를 거칩니다.</p>' +
    '<div class="inline" style="flex-wrap:wrap">' +
    '<label class="muted"><input type="checkbox" id="conv-brief-use-llm"> AI로 목표·요구사항·완료 기준 정리(비용 발생)</label>' +
    '<button type="button" class="small" id="conv-brief-draft-btn">초안 만들기</button>' +
    '</div>' +
    '<textarea id="conv-brief-text" rows="10" style="width:100%;margin-top:0.4rem" hidden></textarea>' +
    '<button type="button" class="small primary" id="conv-brief-submit-btn" hidden>업무 요청 제출</button>' +
    '</details>' +
    '<h4>참가자</h4>' +
    '<ul id="conv-participant-list">' + participantRows + '</ul>' +
    (participantRows ? '' : '<p class="muted" id="conv-participant-empty-hint">참가자가 없습니다.</p>') +
    // 사용자 요청(2026-10-14, "①(참가자 검색-초대 입력창) 없이
    // ②(메시지 중 @멘션 자동완성)만으로 동작 가능하다면 ①을 삭제") —
    // 아래 메시지창의 @멘션 자동완성이 이미 "아직 초대 안 된 조직
    // 전체 후보"까지 검색해서(`/api/conversations/mentionable`),
    // 드롭다운에서 고르는 순간 `_inviteAndTrackParticipant()`로 먼저
    // 초대한 뒤 멘션 텍스트를 끼워 넣는다 — 메시지를 실제로 보내지
    // 않아도 그 시점에 이미 초대가 끝나 있다(사람/Agent/Team 전부
    // 지원, 기존 이 입력창과 완전히 같은 검색·대상 범위). 그래서
    // 별도의 참가자 검색-초대 입력창은 기능이 완전히 겹쳐 제거한다
    // (아래 "대화"의 메시지창에서 "@"를 치면 같은 역할을 한다).
    '<p class="muted">아래 메시지창에서 "@"를 입력하면 참가자 검색·초대 자동완성이 뜹니다(아직 초대 안 된 사람·Agent·Team도 검색되고, 고르면 바로 초대됩니다).</p>' +
    '<h4>대화</h4>' +
    '<div id="conv-messages">' + (messageCards || '<p class="muted">아직 메시지가 없습니다 — 아래에 메시지를 입력해 보세요.</p>') + '</div>' +
    // 사용자 보고(2026-10-14, 스크린샷 — 메시지를 보낸 뒤 진행 중임을
    // 알 수 없음) — "응답을 기다리는 중…"은 이미 있었지만 `#global-msg`
    // (페이지 맨 위)에만 떴다. 대화를 스크롤해 내려와 입력창 근처를
    // 보고 있는 사용자에게는 화면 밖이라 안 보였다 — 메시지 목록
    // 바로 아래, 입력창 바로 위에 똑같은 역할의 인라인 표시를 추가한다.
    '<div id="conv-pending-indicator" style="display:none"></div>' +
    '<form class="inline" id="conv-message-form" style="margin-top:0.5rem;flex-wrap:wrap">' +
    // 사용자 요청(2026-11-11, "개발업무 Skill의 경우 Default로 대화창에
    // 보이지 않도록 설정하고, 필요시 노출하도록") — 완전히 숨기지 않고
    // default-off 체크박스로 켜면 보이게 한다. 켜면 즉시 목록을 다시
    // 불러온다(대화 자체를 다시 열지 않아도 바로 반영 —
    // `renderConversationDetail` 재호출).
    '<label class="muted" style="font-size:0.85rem;white-space:nowrap">' +
    '<input type="checkbox" id="conv-show-dev-skills"' + (_convShowDevSkills ? " checked" : "") + '> 개발업무 스킬도 보기</label>' +
    // 사용자 보고(2026-11-16, Firefox 스크린샷 — 스킬 선택 드롭다운이
    // 화면 오른쪽 위 엉뚱한 곳에 뜨며 클릭으로 선택이 안 됨) — 네이티브
    // <select>의 팝업 위치 계산이 Firefox 엔진 자체에서 어긋나는
    // 것으로 보임(페이지 CSS로 고칠 수 없는 네이티브 위젯 렌더링
    // 영역). 네이티브 select 대신 이미 같은 화면에서 쓰는
    // `.mention-dropdown`과 같은 패턴(버튼 + 문서 흐름 안에서 절대
    // 위치되는 목록)으로 바꿔 모든 브라우저에서 동일하게 동작하게 한다.
    '<div class="skill-select-wrap" style="position:relative;display:inline-block">' +
    '<button type="button" class="small" id="conv-skill-select-btn" data-skill-select-toggle>스킬 선택 안 함 ▾</button>' +
    '<input type="hidden" id="conv-skill-select" value="">' +
    '<ul class="mention-dropdown skill-select-list" id="conv-skill-select-list" style="display:none;top:100%;left:0;width:auto;white-space:nowrap">' +
    '<li data-skill-select-option="">스킬 선택 안 함</li>' +
    skillNames.map(s => '<li data-skill-select-option="' + esc(s) + '">' + esc(s) + '</li>').join("") +
    '</ul>' +
    '</div>' +
    // 대화 컨텍스트 정책(2026-11-20) — 이번 메시지에만 적용되는 "이전
    // 대화" 범위. 기본은 스킬 기본값(내부지식검색 = 사용 안 함 등), 클릭할
    // 때마다 기본 → 전체 → 최근 → 사용 안 함으로 바뀐다. 전송하면 다시
    // 기본으로 돌아간다(대화 단위가 아니라 메시지 단위 예외).
    '<input type="hidden" id="conv-context-select" value="">' +
    '<button type="button" class="small" id="conv-context-btn" data-context-cycle ' +
    'title="이 메시지에 대한 응답에서 이전 대화를 얼마나 참고할지 — 클릭해 바꾸기"></button>' +
    '<div class="mention-wrap" style="position:relative;flex:1;min-width:200px">' +
    '<div id="conv-message-highlight" aria-hidden="true"></div>' +
    '<input id="conv-message-input" placeholder="메시지(@참가자이름으로 호출 가능)" style="width:100%;box-sizing:border-box" autocomplete="off">' +
    '<ul class="mention-dropdown" id="conv-mention-dropdown" style="display:none"></ul>' +
    '</div>' +
    // 사용자 보고(2026-11-12, "자료 선택 시 대화입력·전송버튼 클릭
    // 없이 바로 미리보기가 진행된다") — 예전엔 파일을 고르는 즉시
    // 업로드·메시지 생성까지 끝나버려, 그 자료에 대한 설명을 같이
    // 적을 틈이 없었다. 이제는 파일을 골라도 **보류만** 하고(아래
    // pendingAttachmentFile), "전송"을 눌러야 비로소 — 입력창에 적은
    // 글이 있으면 그 메시지를 먼저, 그 뒤에 첨부를 별도 메시지로 —
    // 둘 다 한 번의 "전송" 클릭으로 올라간다. 멘션 감지는 여전히
    // 입력창 텍스트에만 걸린다(첨부 파일 내용은 별도 메시지라 전과
    // 동일하게 멘션 오탐 걱정이 없다).
    '<input type="file" id="conv-file-input" accept=".md,.markdown,.txt,.html,.htm,.pdf,.csv,.xlsx,.docx,.pptx" style="display:none">' +
    '<button type="button" class="small" id="conv-file-btn" title="자료 첨부(.md/.txt/.html/.pdf) — 전송 시 함께 올라갑니다">📎 자료</button>' +
    '<span class="muted" id="conv-pending-file-name" style="font-size:0.85rem"></span>' +
    '<button type="button" class="small danger" id="conv-pending-file-remove" style="display:none">자료 제거</button>' +
    '<button class="primary" type="submit" id="conv-send-btn">전송</button>' +
    '</form>' +
    '</section>';

  // 사용자 보고(2026-10-08, "대화 모델 선택시 provider 후 모델
  // 레지스트리에 등록된 모델 리스트 제공 안됨") — `wireProviderModelSelect()`
  // 의 초기 `rebuildOptions()`는 호출 시점의 `providerSelect.value`를
  // 그대로 쓴다. provider select의 `selected` 속성을 마크업에서 이미
  // 심어뒀으므로(위) 이 시점엔 `providerSelect.value`가 올바르다 —
  // JS로 뒤늦게 `.value =`를 대입하면 모델 목록이 빈 문자열(기본
  // 옵션) 기준으로 만들어져 항상 비어 보이는 게 원래 버그였다.
  wireProviderModelSelect("conv-provider", "conv-model", modelsByProvider, conv.llm_model);

  const mentionInput = document.getElementById("conv-message-input");
  const mentionDropdown = document.getElementById("conv-mention-dropdown");
  const mentionHighlight = document.getElementById("conv-message-highlight");
  const mentionMirror = setupMentionInputMirror(mentionInput, mentionHighlight, participants);
  // 사용자 요청(2026-10-10, "채팅에서 @로 호출하려면 미리 전체 이름을
  // 알고 있어야 해") — 메시지창 자동완성이 이미 초대된 참가자만 보던
  // 것을, 조직 전체 검색(`/api/conversations/mentionable`, 이미
  // "참가자 초대" 입력창이 쓰던 바로 그 엔드포인트)까지 함께 보도록
  // 확장한다. 미초대 대상을 드롭다운에서 고르면 별도 화면 이동 없이
  // 그 자리에서 바로 초대한 뒤 멘션을 끼워 넣는다(`_inviteAndTrackParticipant`).
  setupMentionAutocomplete(mentionInput, mentionDropdown, participants, {
    search: (q) => api("GET", qs("/api/conversations/mentionable", { q })),
    onInvited: (item) => _inviteAndTrackParticipant(conversationId, participants, item),
    onMentionSelected: () => {
      _setSkillSelectDisplay("", "");
      mentionMirror.sync();
    },
  });

  document.getElementById("conv-show-dev-skills").addEventListener("change", (ev) => {
    _convShowDevSkills = ev.target.checked;
    renderConversationDetail(conversationId, containerId);
  });

  // 삭제/나가기 뒤의 새로고침은 `refresh()`(전역, 현재 탭을 다시
  // 렌더)로 한다 — 이 함수는 독립 "대화" 탭과 Task 워크스페이스
  // 임베드(`_loadTaskConversation`) 양쪽에서 호출되므로, 둘 중 어느
  // 쪽에서 열렸는지 모르는 채로도 올바른 화면을 다시 그려준다.
  const deleteBtn = document.getElementById("conv-delete-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      if (!(await confirmModal("이 대화를 메시지·참가자까지 전부 지웁니다(되돌릴 수 없음). 계속할까요?"))) return;
      try {
        await api("DELETE", "/api/conversations/" + conversationId);
        showMsg("global-msg", "대화를 삭제했습니다.", "ok");
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }
  const leaveBtn = document.getElementById("conv-leave-btn");
  if (leaveBtn) {
    leaveBtn.addEventListener("click", async () => {
      if (!(await confirmModal("이 대화에서 나갑니다. 계속할까요?"))) return;
      try {
        const result = await api("POST", "/api/conversations/" + conversationId + "/leave");
        showMsg(
          "global-msg",
          result.conversation_deleted ? "대화에서 나갔습니다(마지막 참가자라 대화도 삭제됐습니다)." : "대화에서 나갔습니다.",
          "ok"
        );
        refresh();
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
  }

  document.getElementById("conv-save-model-btn").addEventListener("click", () => _saveConversationModel(conversationId));

  // 대화 → 업무 요청 전환(2026-11-23, P1-4).
  const briefDraftBtn = document.getElementById("conv-brief-draft-btn");
  const briefText = document.getElementById("conv-brief-text");
  const briefSubmitBtn = document.getElementById("conv-brief-submit-btn");
  briefDraftBtn.addEventListener("click", async () => {
    const useLlm = document.getElementById("conv-brief-use-llm").checked;
    briefDraftBtn.disabled = true;
    briefDraftBtn.textContent = useLlm ? "AI가 정리하는 중…" : "초안 만드는 중…";
    try {
      const draft = await api("POST", "/api/conversations/" + conversationId + "/brief-draft", { use_llm: useLlm });
      briefText.value = draft.raw_text;
      briefText.hidden = false;
      briefSubmitBtn.hidden = false;
      briefText.focus();
    } catch (e) { showMsg("global-msg", e.message, "error"); }
    finally {
      briefDraftBtn.disabled = false;
      briefDraftBtn.textContent = "초안 다시 만들기";
    }
  });
  briefSubmitBtn.addEventListener("click", async () => {
    if (!briefText.value.trim()) { showMsg("global-msg", "업무 요청 내용을 입력하세요.", "error"); return; }
    briefSubmitBtn.disabled = true;
    try {
      const created = await api("POST", "/api/briefs", { raw_text: briefText.value, source_conversation_id: conversationId });
      showMsg("global-msg", "업무 요청 #" + created.id + "로 전환했습니다 — 「업무 요청」 탭에서 할 일로 나누고 배정하세요.", "ok");
      briefText.value = "";
      briefText.hidden = true;
      briefSubmitBtn.hidden = true;
      document.getElementById("conv-to-brief").open = false;
      await refreshConversationMessages(conversationId, participants, conv);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
    finally { briefSubmitBtn.disabled = false; }
  });

  document.getElementById("conv-auto-reply").addEventListener("change", async (ev) => {
    try {
      await api("PATCH", "/api/conversations/" + conversationId + "/ai-auto-reply", {
        enabled: ev.target.checked,
      });
      showMsg("global-msg", "자동응답 설정을 저장했습니다.", "ok");
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });

  // 2026-10-14 — 참가자 검색-초대 전용 입력창을 제거했다(아래 메시지창의
  // "@" 자동완성이 완전히 같은 검색·초대를 포함한다, `setupMentionAutocomplete`
  // 호출부와 `_inviteAndTrackParticipant()` 참고).

  // 사용자 보고(2026-11-12) — 전송을 누르기 전까지 선택한 파일을
  // 들고만 있는다(아래 change 핸들러는 업로드하지 않는다 — 실제
  // 업로드는 "전송" 클릭 시에만 일어난다).
  let pendingAttachmentFile = null;
  const pendingFileNameEl = document.getElementById("conv-pending-file-name");
  const pendingFileRemoveBtn = document.getElementById("conv-pending-file-remove");
  function _clearPendingAttachment() {
    pendingAttachmentFile = null;
    pendingFileNameEl.textContent = "";
    pendingFileRemoveBtn.style.display = "none";
    document.getElementById("conv-file-input").value = "";
  }
  pendingFileRemoveBtn.addEventListener("click", _clearPendingAttachment);

  // 사용자 보고(2026-11-19, 스크린샷 — 첨부/메시지가 2번씩 찍힘) 원인
  // 수정 — 제출 핸들러가 비동기(업로드→전송→새로고침)인데 그동안
  // 아무 것도 막지 않아, 응답이 오기 전에 "전송"을 한 번 더 누르거나
  // 입력창에서 Enter를 두 번 치면 핸들러가 겹쳐 돌면서 아직 안 비워진
  // 입력값·첨부가 그대로 두 번째 요청으로 또 나갔다(지워지는 시점이
  // 첫 요청들이 전부 끝난 **뒤**라 그 사이엔 막을 게 없었음). 제출
  // 버튼을 요청이 끝날 때까지 비활성화해 막는다.
  _renderContextChip();
  let isSubmittingConvMessage = false;
  document.getElementById("conv-message-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (isSubmittingConvMessage) return;
    const input = document.getElementById("conv-message-input");
    const content = input.value.trim();
    if (!content && !pendingAttachmentFile) return;
    const skill = document.getElementById("conv-skill-select").value || undefined;
    isSubmittingConvMessage = true;
    const sendBtn = document.getElementById("conv-send-btn");
    if (sendBtn) sendBtn.disabled = true;
    try {
      // 첨부가 있으면 먼저 올린다(멘션 감지가 걸리지 않는 별도
      // 메시지로 남음 — 첨부 글 안에 우연히 "@참가자이름"이 있어도
      // 멘션 오탐이 안 난다는 기존 안전장치를 그대로 유지). 입력창
      // 텍스트가 있으면 그 뒤를 이어 두 번째 메시지로 보낸다 — 결과적으로
      // "전송" 한 번으로 둘 다 올라간다.
      if (pendingAttachmentFile) {
        const formData = new FormData();
        formData.append("file", pendingAttachmentFile);
        await apiUpload("/api/conversations/" + conversationId + "/attachments", formData);
        _clearPendingAttachment();
      }
      let result = null;
      if (content) {
        const payload = { content };
        if (skill) payload.skill = skill;
        const contextOverride = document.getElementById("conv-context-select").value;
        if (contextOverride) payload.context_override = contextOverride;
        result = await api("POST", "/api/conversations/" + conversationId + "/messages", payload);
      }
      input.value = "";
      document.getElementById("conv-context-select").value = "";
      _setSkillSelectDisplay("", "");
      mentionMirror.sync();
      await refreshConversationMessages(conversationId, participants, conv);
      // 사용자 요청(2026-10-18, "어떤 에이전트가 어떤 동작을 하고
      // 있는지... 펼침/닫힘 UI로") — 뭉뚱그린 "N건 진행 중" 대신
      // Agent/Team 이름별로 펼쳐 볼 수 있는 목록을 띄운다. 완료되는
      // 작업마다(전부 끝날 때까지 기다리지 않고) 그 자리에서 상태를
      // 갱신하고 메시지 목록도 바로 반영한다 — 먼저 끝난 결과를
      // 늦게 끝나는 Job 때문에 못 보고 기다리지 않게.
      if (result) _watchConversationJobs(conversationId, result.triggered_jobs, participants, conv);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
    finally {
      isSubmittingConvMessage = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  });

  document.getElementById("conv-file-btn").addEventListener("click", () => {
    document.getElementById("conv-file-input").click();
  });
  document.getElementById("conv-file-input").addEventListener("change", (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    pendingAttachmentFile = file;
    pendingFileNameEl.textContent = "📎 " + file.name + "(전송 시 함께 올라갑니다)";
    pendingFileRemoveBtn.style.display = "";
    ev.target.value = "";
  });

  // 경량 폴링 — 이 대화를 보는 동안 주기적으로
  // 새 메시지를 반영한다(다른 참가자/Agent/Team이 비동기로 남긴
  // 메시지도 수동 새로고침 없이 보이게). 다른 화면으로 이동하면
  // `_stopConversationPolling()`이 멈춘다.
  _conversationPollTimer = setInterval(
    () => refreshConversationMessages(conversationId, participants, conv),
    6000
  );

  // 이 대화를 열었을 때 아직 안 끝난 Job이 있으면(위에서 미리 조회해둔
  // `runningJobs`), 메시지를 "보내는" 흐름과 똑같이 진행 패널을 띄우고
  // 폴링을 이어간다 — 그래야 탭을 닫았다가 Job이 끝나기 전에 다시 열어도
  // "지금 작업 중"이라는 걸 바로 알 수 있다.
  if (runningJobs && runningJobs.length) {
    _watchConversationJobs(
      conversationId,
      runningJobs.map(j => ({
        trigger_type: j.trigger_type,
        job_id: j.id,
        agent_id: j.agent_id,
        team_id: j.team_id,
      })),
      participants,
      conv
    );
  }
}

// 메시지창 자동완성에서 미초대 대상을 고르면 호출된다 — 전체 화면
// 재렌더(`renderConversationDetail`) 없이 (1) 초대 API를 호출하고
// (2) 그 자리에서 쓰는 `participants` 배열과 "참가자" 목록 DOM만
// 갱신한다(전체 재렌더는 작성 중이던 메시지 초안을 지워버린다).
async function _inviteAndTrackParticipant(conversationId, participants, item) {
  try {
    await api("POST", "/api/conversations/" + conversationId + "/participants", {
      participant_type: item.type,
      participant_id: item.id,
    });
  } catch (e) {
    // 드롭다운이 갱신되기 전 같은 대상을 두 번 고르거나, 이번 세션에서
    // 이미 초대한 대상을 다시 멘션하는 등 드문 경합은 "이미 초대됨"으로
    // 조용히 넘기고 멘션만 그대로 진행한다 — 단, 이미 참가자이므로
    // 아래 participants.push/li 추가는 하지 않고 바로 끝낸다(사용자
    // 보고 2026-10-28, "참가자가 두 번 표기됨" — 예전엔 이 catch 분기도
    // 그대로 아래로 흘러 push/appendChild해 참가자 목록에 같은 이름이
    // 중복 표시됐다. 실제로는 서버에 중복 행이 생기지 않는다 — 이
    // 화면의 로컬 state/DOM만 중복이었다).
    if (!String(e.message || "").includes("이미 초대")) {
      showMsg("global-msg", e.message, "error");
      throw e;
    }
    return;
  }
  participants.push({
    participant_type: item.type,
    participant_id: item.id,
    display_name: item.name,
  });
  const emptyHint = document.getElementById("conv-participant-empty-hint");
  if (emptyHint) emptyHint.remove();
  const list = document.getElementById("conv-participant-list");
  if (list) {
    const li = document.createElement("li");
    li.textContent = label(item.type) + " " + item.name;
    list.appendChild(li);
  }
  showMsg("global-msg", item.name + "님을 대화에 초대했습니다.", "ok");
}

async function _saveConversationModel(conversationId) {
  const provider = document.getElementById("conv-provider").value || null;
  const model = modelSelectValue("conv-model");
  try {
    await api("PATCH", "/api/conversations/" + conversationId + "/model", { provider, model });
    showMsg("global-msg", "모델 설정을 저장했습니다.", "ok");
  } catch (e) { showMsg("global-msg", e.message, "error"); }
}

// 이 Task에 종속된
// 대화(`linked_task_id`)가 이미 있으면 그대로 임베드하고, 없으면
// "대화 시작" 버튼만 보여준다(결과물 작성 전에도 항상 쓸 수 있다 —
// 발견한 구조적 한계의 직접적인 해소).
async function _loadTaskConversation(taskId) {
  const box = document.getElementById("ws-conversation-" + taskId);
  if (!box) return;
  let mine;
  try {
    mine = await api("GET", "/api/conversations");
  } catch (e) {
    box.innerHTML = "";
    return;
  }
  const existing = mine.find(c => c.linked_task_id === taskId);
  if (existing) {
    box.innerHTML = '<div id="ws-conversation-detail-' + taskId + '"></div>';
    renderConversationDetail(existing.id, "ws-conversation-detail-" + taskId);
    return;
  }
  box.innerHTML =
    '<section class="card"><h4>대화</h4>' +
    '<p class="muted">이 업무에 대해 바로 대화를 시작할 수 있습니다 — 결과물이 아직 없어도 됩니다.</p>' +
    '<button class="small" id="ws-start-conversation-btn-' + taskId + '">대화 시작</button>' +
    '</section>';
  document.getElementById("ws-start-conversation-btn-" + taskId).onclick = async () => {
    try {
      await api("POST", "/api/conversations", { title: "", linked_task_id: taskId });
      await _loadTaskConversation(taskId);
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  };
}

