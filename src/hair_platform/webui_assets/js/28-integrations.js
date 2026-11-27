/* ---------- 연동 설정(2026-11-23, "P1 진행해줘" — P1-5 사람이 누르지 않아도 도는 자동화) ----------
   ① 내 API 키 — 외부 시스템(cron·메일 규칙·지라)이 내 계정·권한으로 HAIR API를 부른다.
   ② 정기 실행(admin/manager) — 정해진 시각에 업무 요청 → 할 일 → 파이프라인 실행을 만든다.
   ③ 외부 알림(admin) — 결재 대기·파이프라인 완료/실패 등을 Slack·Teams 수신 웹훅으로 보낸다.
   (2026-11-26 P2-13부터 이 파일은 파이썬 문자열이 아니라 그대로 전달된다 — 역슬래시를 써도 된다.) */
async function renderIntegrations(content) {
  const isAdmin = ME.role === "admin";
  const canSchedule = ["admin", "manager"].includes(ME.role);
  const fmt = v => v ? esc(String(v).replace("T", " ")) : "-";
  const [keys, schedules, templates, notify, repos, trig, dataConns, mailBoxes, calFeeds, health] = await Promise.all([
    api("GET", "/api/api-keys"),
    canSchedule ? api("GET", "/api/pipeline-schedules") : Promise.resolve([]),
    canSchedule ? api("GET", "/api/pipeline-templates") : Promise.resolve([]),
    isAdmin ? api("GET", "/api/integrations/notify") : Promise.resolve(null),
    isAdmin ? api("GET", "/api/code-repositories") : Promise.resolve([]),
    canSchedule ? api("GET", "/api/automation-triggers") : Promise.resolve({ triggers: [], events: {} }),
    isAdmin ? api("GET", "/api/data-connections").catch(() => []) : Promise.resolve([]),  // 사내 DB(2026-11-26, P1-9)
    isAdmin ? api("GET", "/api/mail-inboxes").catch(() => []) : Promise.resolve([]),  // 메일 수신함(2026-11-26, P1-9 2단계)
    isAdmin ? api("GET", "/api/calendar-feeds").catch(() => []) : Promise.resolve([]),  // 캘린더(2026-11-26, P1-9 3단계)
    isAdmin ? api("GET", "/api/admin/connection-status").catch(() => null) : Promise.resolve(null),  // 연결 상태(2026-11-26, 2-3)
  ]);
  // 마지막 확인(연결 확인 버튼·실제 조회 모두) — kind:id → 표 칸
  const healthBy = {};
  ((health && health.items) || []).forEach(h => { healthBy[h.kind + ":" + h.id] = h; });
  const lastCheck = (kind, id) => {
    const h = healthBy[kind + ":" + id];
    if (!h || h.status === "unchecked") return '<span class="muted">확인 안 함</span>';
    return h.status === "ok"
      ? '<span title="' + escAttr(h.checked_at || "") + '">✓ ' + fmt(h.checked_at) + "</span>"
      : '<span class="gate-badge gate-warn" title="' + escAttr(h.error || "") + '">실패</span> ' + fmt(h.checked_at);
  };
  const visLabel = v => ({ all: "모든 사람", manager: "manager 이상", admin: "admin만" })[v] || (String(v).startsWith("team:") ? "팀 #" + String(v).slice(5) : v);
  const mbRows = (mailBoxes || []).map(b =>
    "<tr><td>" + esc(b.name) + "</td><td>" + esc(b.username) + '<br><span class="muted">' + esc(b.host) + ":" + b.port + " · " + esc(b.folder) + "</span></td><td>" +
    esc(visLabel(b.visibility)) + "</td><td>" + (b.password_set ? "✓ " : "⚠ 비어 있음 ") + "<code>" + esc(b.password_env) + "</code></td><td>" +
    (b.is_active ? "켜짐" : "꺼짐") + "</td><td>" + lastCheck("mail_inboxes", b.id) + "</td><td>" +
    '<button class="small" data-test-mb="' + b.id + '">연결 확인</button> ' +
    '<button class="small" data-toggle-mb="' + b.id + '" data-active="' + (b.is_active ? "0" : "1") + '">' + (b.is_active ? "끄기" : "켜기") + "</button></td></tr>"
  ).join("");
  const dcRows = (dataConns || []).map(d =>
    "<tr><td>" + esc(d.name) + "</td><td>" + esc(d.kind) + "</td><td><code>" + esc(d.target) + "</code>" +
    (d.username ? " · " + esc(d.username) : "") + "</td><td>" + esc(String(d.row_limit)) + "행</td><td>" +
    (d.password_env ? (d.password_set ? "✓ " : "⚠ 비어 있음 ") + "<code>" + esc(d.password_env) + "</code>" : "-") + "</td><td>" +
    (d.is_active ? "켜짐" : "꺼짐") + "</td><td>" + lastCheck("data_connections", d.id) + "</td><td>" +
    '<button class="small" data-test-dc="' + d.id + '">연결 확인</button> ' +
    '<button class="small" data-toggle-dc="' + d.id + '" data-active="' + (d.is_active ? "0" : "1") + '">' + (d.is_active ? "끄기" : "켜기") + "</button></td></tr>"
  ).join("");
  const cfRows = (calFeeds || []).map(f =>
    "<tr><td>" + esc(f.name) + "</td><td>" + (f.source_kind === "url" ? "iCal 주소(환경변수)" : "ICS 파일") + "<br><code>" + esc(f.source) + "</code> " +
    (f.source_ready ? "✓" : "⚠ 비어 있음") + "</td><td>" + esc(visLabel(f.visibility)) + "</td><td>" + (f.is_active ? "켜짐" : "꺼짐") + "</td><td>" + lastCheck("calendar_feeds", f.id) + "</td><td>" +
    '<button class="small" data-test-cf="' + f.id + '">연결 확인</button> ' +
    '<button class="small" data-toggle-cf="' + f.id + '" data-active="' + (f.is_active ? "0" : "1") + '">' + (f.is_active ? "끄기" : "켜기") + "</button></td></tr>"
  ).join("");
  // 이벤트 트리거(2026-11-24, P1-5).
  const tplOptions = templates.filter(t => t.is_active !== 0).map(t => '<option value="' + t.id + '">' + esc(t.name) + "</option>").join("");
  const trigRows = trig.triggers.map(t =>
    "<tr><td>" + esc(t.name) + "</td><td>" + esc(t.event_label) + (t.source_template_name ? " — " + esc(t.source_template_name) : "") +
    "</td><td>" + esc(t.action_template_name) + "</td><td>" + (t.is_active ? "켜짐" : "꺼짐") + " · " + t.fired_count + "회" +
    (t.last_run_id ? " (마지막 실행 #" + t.last_run_id + ")" : "") +
    (t.last_error ? '<div class="muted">⚠ ' + esc(t.last_error) + "</div>" : "") + "</td><td>" +
    '<button class="small" data-toggle-trigger="' + t.id + '" data-active="' + (t.is_active ? "0" : "1") + '">' + (t.is_active ? "끄기" : "켜기") + "</button> " +
    '<button class="small danger" data-delete-trigger="' + t.id + '">삭제</button></td></tr>'
  ).join("");
  // 코드 저장소(2026-11-24, P0-3 1단계 — 읽기 전용).
  const repoRows = repos.map(r =>
    "<tr><td><code>" + esc(r.name) + "</code></td><td><code>" + esc(r.path) + "</code></td><td>" + esc(r.description || "-") +
    "</td><td>" + (r.is_active ? "사용 중" : "꺼짐") + "</td><td>" +
    '<button class="small" data-toggle-repo="' + r.id + '" data-active="' + (r.is_active ? "0" : "1") + '">' +
    (r.is_active ? "끄기" : "켜기") + "</button></td></tr>"
  ).join("");
  const origin = location.origin;
  const keyRows = keys.map(k =>
    "<tr><td>" + esc(k.name) + "</td><td><code>" + esc(k.key_prefix) + "…</code></td><td>" + fmt(k.created_at) +
    "</td><td>" + fmt(k.last_used_at) + "</td><td>" + (k.revoked_at ? "폐기됨" : "사용 중") + "</td><td>" +
    (k.revoked_at ? "" : '<button class="small danger" data-revoke-key="' + k.id + '">폐기</button>') + "</td></tr>"
  ).join("");
  const WD = ["월", "화", "수", "목", "금", "토", "일"];
  const schedRows = schedules.map(s =>
    "<tr><td>" + esc(s.name) + "</td><td>" + esc(s.template_name) + "</td><td>" + esc(s.when) + "</td><td>" +
    (s.is_active ? fmt(s.next_run_at) : "꺼짐") + "</td><td>" + fmt(s.last_run_at) +
    (s.last_task_id ? " (할 일 #" + s.last_task_id + ")" : "") +
    (s.last_error ? '<div class="muted">⚠ ' + esc(s.last_error) + "</div>" : "") + "</td><td>" +
    '<button class="small" data-toggle-schedule="' + s.id + '" data-active="' + (s.is_active ? "0" : "1") + '">' +
    (s.is_active ? "끄기" : "켜기") + "</button> " +
    '<button class="small danger" data-delete-schedule="' + s.id + '">삭제</button></td></tr>'
  ).join("");
  const eventBoxes = notify ? Object.entries(notify.available_events).map(([key, label]) =>
    '<label class="muted" style="margin-right:0.8rem"><input type="checkbox" class="notify-event" value="' + key + '"' +
    (notify.events.includes(key) ? " checked" : "") + "> " + esc(label) + "</label>"
  ).join("") : "";

  content.innerHTML =
    "<h2>연동 설정</h2>" +
    helpBox(
      '<ul class="muted help-list">' +
      "<li>사람이 버튼을 누르지 않아도 HAIR가 돌게 하는 설정입니다 — 외부 시스템이 API를 부르게 하거나(API 키), 정해진 시각에 파이프라인을 돌리거나(정기 실행), 결재 대기·완료를 메신저로 알립니다(외부 알림).</li>" +
      "<li>정기 실행과 외부 알림은 <code>hair serve</code>로 띄운 서버에서 30초마다 확인합니다(서버가 꺼져 있거나 PC가 잠자기면 그동안은 돌지 않습니다).</li>" +
      "</ul>"
    ) +
    "<h3>내 API 키</h3>" +
    '<p class="muted">외부 시스템이 <b>내 계정·권한 그대로</b> HAIR API를 부를 때 씁니다 — 요청 헤더에 <code>Authorization: Bearer 발급한 키</code>를 넣습니다. 예: <code>curl -X POST ' + esc(origin) +
    '/api/briefs -H "Authorization: Bearer hair_..." -H "Content-Type: application/json" -d &#39;{"raw_text": "업무 내용"}&#39;</code></p>' +
    '<form class="inline" id="apikey-form"><input id="apikey-name" placeholder="키 이름(용도, 예: 주간 실적 cron)" style="min-width:260px" required>' +
    '<button class="primary" type="submit">발급</button></form>' +
    '<div id="apikey-new" hidden></div>' +
    (keys.length ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>앞자리</th><th>발급</th><th>마지막 사용</th><th>상태</th><th></th></tr></thead><tbody>' + keyRows + "</tbody></table></div>"
      : '<p class="muted">발급한 키가 없습니다.</p>') +
    (canSchedule ?
      "<h3>정기 실행</h3>" +
      '<p class="muted">정해진 시각마다 아래 내용으로 업무 요청과 할 일을 만들고 파이프라인을 시작합니다 — <b>만든 사람의 이름으로</b> 실행돼 결재·사람 승인 단계도 직접 실행했을 때와 같습니다. 시각은 서버 PC의 현지 시각입니다.</p>' +
      '<form id="schedule-form" style="display:grid;gap:0.4rem;margin-bottom:0.6rem">' +
      '<div class="inline" style="flex-wrap:wrap">' +
      '<input id="sch-name" placeholder="이름(예: 주간 실적 보고)" required>' +
      '<select id="sch-template">' + templates.filter(t => t.is_active !== false && t.is_active !== 0).map(t => '<option value="' + t.id + '">' + esc(t.name) + "</option>").join("") + "</select>" +
      '<input id="sch-role" placeholder="할 일 역할(예: 실적 보고서 작성자)" required>' +
      "</div>" +
      '<textarea id="sch-brief" rows="3" placeholder="매번 만들 업무 요청 내용(예: 지난주 매출·영업이익·신규고객 실적 보고서를 써줘)" required></textarea>' +
      '<div class="inline" style="flex-wrap:wrap">' +
      '<select id="sch-interval"><option value="weekly">매주</option><option value="weekdays">평일(월~금)</option><option value="daily">매일</option></select>' +
      '<select id="sch-weekday">' + WD.map((d, i) => '<option value="' + i + '">' + d + "요일</option>").join("") + "</select>" +
      '<input id="sch-time" type="time" value="09:00" required>' +
      '<button class="primary" type="submit">정기 실행 추가</button></div></form>' +
      (schedules.length ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>파이프라인</th><th>주기</th><th>다음 실행</th><th>마지막 실행</th><th></th></tr></thead><tbody>' + schedRows + "</tbody></table></div>"
        : '<p class="muted">등록된 정기 실행이 없습니다.</p>')
      : "") +
    (canSchedule ?
      "<h3>이벤트 트리거(admin/manager)</h3>" +
      '<p class="muted">시각이 아니라 <b>일이 생기면</b> 파이프라인을 시작합니다 — 예: "재무 분석 보고 파이프라인이 끝나면 → 발표자료를 만드는 파이프라인 시작". 앞 실행의 최종 결과물(또는 승인된 결과물)을 새 업무 요청에 붙여 넘기고, <b>만든 사람 이름으로</b> 실행합니다(결재·사람 승인은 그대로). 만들기 전에 이미 있던 일로는 실행하지 않고, 트리거끼리 서로 부르는 연쇄는 ' + trig.max_chain_depth + '단계까지만 갑니다.</p>' +
      '<form id="trig-form" style="display:grid;gap:0.4rem">' +
      '<div class="inline" style="flex-wrap:wrap"><input id="trig-name" placeholder="이름(예: 재무 보고 → 발표자료)" required maxlength="100">' +
      '<select id="trig-event"><option value="pipeline_completed">파이프라인이 끝나면</option><option value="deliverable_approved">결과물이 승인되면</option></select>' +
      '<label class="muted" id="trig-source-wrap">끝난 파이프라인 <select id="trig-source">' + tplOptions + "</select></label></div>" +
      '<div class="inline" style="flex-wrap:wrap"><label class="muted">→ 시작할 파이프라인 <select id="trig-action" required>' + tplOptions + "</select></label>" +
      '<input id="trig-role" placeholder="새 할 일 역할(예: 발표자료 작성)" required maxlength="100"></div>' +
      '<textarea id="trig-brief" rows="2" required placeholder="새 업무 요청 내용(예: 아래 이전 결과물로 경영진 발표자료 10장을 만들어 줘)"></textarea>' +
      '<div><button class="primary" type="submit">트리거 추가</button></div></form>' +
      (trig.triggers.length ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>언제</th><th>시작할 파이프라인</th><th>상태</th><th></th></tr></thead><tbody>' + trigRows + "</tbody></table></div>"
        : '<p class="muted">등록된 트리거가 없습니다.</p>')
      : "") +
    (isAdmin ?
      "<h3>외부 알림(admin)</h3>" +
      '<p class="muted">Slack·Teams의 수신 웹훅(Incoming Webhook) URL을 넣으면 고른 일이 생길 때마다 한 번씩 메시지를 보냅니다. 처음 저장할 때 이미 있던 일은 보내지 않습니다.</p>' +
      '<div style="display:grid;grid-template-columns:minmax(0,1fr);gap:0.4rem"><input id="notify-url" type="url" placeholder="https://hooks.slack.com/services/..." style="width:100%;box-sizing:border-box" value="' + escAttr(notify.webhook_url || "") + '">' +
      "<div>" + eventBoxes + "</div>" +
      // 메일(2026-11-24, P1-5) — 같은 이벤트를 메일로도. SMTP 접속 정보는 서버 환경변수(HAIR_SMTP_*)에서만 읽는다.
      '<label class="muted">메일로도 받을 사람(한 줄에 하나 또는 쉼표로, 최대 20명)</label>' +
      '<textarea id="notify-emails" rows="2" placeholder="ops@example.com, lead@example.com">' + esc((notify.emails || []).join(", ")) + "</textarea>" +
      '<span class="muted">' + (notify.smtp_configured
        ? "메일 서버: " + esc(notify.smtp_host)
        : "⚠ 메일 서버가 설정되지 않았습니다 — 서버 PC에서 환경변수 HAIR_SMTP_HOST·HAIR_SMTP_PORT·HAIR_SMTP_USER·HAIR_SMTP_PASSWORD(·HAIR_SMTP_FROM, 465면 HAIR_SMTP_SSL=1)를 지정하세요(hair init으로 설정, hair check로 확인).") +
      (notify.email_last_error ? " · ⚠ " + esc(notify.email_last_error) : "") + "</span>" +
      '<div class="inline"><button class="primary" id="notify-save">저장</button><button class="small" id="notify-test">테스트 전송</button>' +
      '<span class="muted">' + (notify.last_sent_at ? "마지막 전송 " + fmt(notify.last_sent_at) : "") +
      (notify.last_error ? " · ⚠ " + esc(notify.last_error) : "") + "</span></div></div>" +
      "<h3>코드 저장소(admin)</h3>" +
      '<p class="muted">이 서버 PC에 있는 Git 저장소(클론한 폴더)를 등록하면, <b>코드저장소조회</b> 스킬이 있는 개발 Agent(코드베이스 분석가·개발자·코드 검증가·AA)가 파일 목록·내용·코드 검색·최근 커밋을 직접 읽습니다. ' +
      '<b>읽기 전용</b>이고 <b>커밋된 파일만</b> 보입니다 — 미커밋 변경, <code>.gitignore</code>로 빠진 파일, <code>.env</code>·키 같은 비밀 파일은 읽지 않습니다. 최신 코드를 보게 하려면 그 폴더에서 <code>git pull</code>을 해 두세요. 승인된 결과물은 결과물 카드의 "저장소에 커밋"으로 점검을 모두 통과할 때만 main에 로컬 커밋할 수 있습니다(push 없음).</p>' +
      '<form id="repo-form" class="inline" style="flex-wrap:wrap">' +
      '<input id="repo-name" placeholder="이름(예: shop-api)" required maxlength="60">' +
      '<input id="repo-path" placeholder="서버 경로(예: /Users/me/src/shop-api)" required style="min-width:18rem">' +
      '<input id="repo-desc" placeholder="설명(선택)" maxlength="500">' +
      '<button class="primary" type="submit">저장소 등록</button></form>' +
      (repos.length ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>경로</th><th>설명</th><th>상태</th><th></th></tr></thead><tbody>' + repoRows + "</tbody></table></div>"
        : '<p class="muted">등록된 코드 저장소가 없습니다.</p>') +
      // 사내 DB 조회(2026-11-26, P1-9 — `data_connectors.py`).
      "<h3>사내 DB(admin)</h3>" +
      '<p class="muted">업무 DB를 등록하면 <b>사내DB조회</b> 스킬이 있는 Agent(데이터 분석가·재무 분석가·영업 분석가·실적 보고서 작성자)가 테이블 목록·열 구조를 확인하고 <b>SELECT로 직접 조회</b>합니다. ' +
      '<b>읽기 전용</b>입니다 — SQL 검사(한 문장, SELECT·WITH·EXPLAIN만)와 읽기 전용 연결 두 겹으로 쓰기를 막고, 행 수 상한·10초 시간 제한을 둡니다. 조회마다 요청자·SQL·행 수가 활동 기록(<code>data_query_executed</code>)에 남습니다. ' +
      '<b>비밀번호는 저장하지 않습니다</b> — 서버 PC의 환경변수에 넣고 그 <b>이름</b>만 적으세요. PostgreSQL·MySQL은 서버에 <code>pip install "hair-platform[dbconnect]"</code>가 필요합니다. 가능하면 <b>조회 전용 DB 계정</b>을 쓰세요.</p>' +
      '<form id="dc-form" class="inline" style="flex-wrap:wrap">' +
      '<input id="dc-name" placeholder="이름(예: sales_db)" required maxlength="60">' +
      '<select id="dc-kind"><option value="sqlite">SQLite(파일)</option><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option></select>' +
      '<input id="dc-target" placeholder="SQLite 파일 경로 또는 host:port/DB이름" required style="min-width:16rem">' +
      '<input id="dc-user" placeholder="DB 계정(선택)" maxlength="100">' +
      '<input id="dc-pwenv" placeholder="비밀번호 환경변수 이름(예: HAIR_DB_SALES_PASSWORD)" maxlength="64" style="min-width:14rem">' +
      '<input id="dc-limit" type="number" min="1" max="2000" value="200" style="width:6rem" title="한 번에 돌려줄 최대 행 수">' +
      '<input id="dc-desc" placeholder="설명(Agent가 어떤 DB인지 알 수 있게)" maxlength="500" style="min-width:14rem">' +
      '<button class="primary" type="submit">DB 연결 등록</button></form>' +
      (dcRows ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>종류</th><th>대상</th><th>행 수 상한</th><th>비밀번호</th><th>상태</th><th>마지막 확인</th><th></th></tr></thead><tbody>' + dcRows + "</tbody></table></div>"
        : '<p class="muted">등록된 DB 연결이 없습니다.</p>') +
      // 메일 수신함(2026-11-26, P1-9 2단계 — `mail_inbox.py`).
      "<h3>메일 수신함(admin)</h3>" +
      '<p class="muted">공용 업무 메일함(고객지원·영업 등)을 IMAP으로 등록하면 <b>메일수신함조회</b> 스킬이 있는 Agent(고객지원 상담원·영업 제안 작성자)가 최근 메일을 검색하고 본문을 읽습니다. ' +
      '<b>읽기 전용</b>입니다 — 보내기·지우기·이동은 하지 않고 <b>읽음 표시도 바꾸지 않습니다</b>. 첨부는 이름·크기만 봅니다. ' +
      '<b>공개 범위</b>로 누구의 요청일 때 읽을지 정합니다(기본 admin만 — 지식 베이스 문서와 같은 규칙). 조회마다 활동 기록(<code>mail_inbox_read</code>)이 남습니다. ' +
      '<b>비밀번호는 저장하지 않습니다</b> — 메일 서비스의 <b>앱 비밀번호</b>를 서버 PC 환경변수에 넣고 그 이름만 적으세요(Gmail: imap.gmail.com, Microsoft 365: outlook.office365.com, 포트 993).</p>' +
      '<form id="mb-form" class="inline" style="flex-wrap:wrap">' +
      '<input id="mb-name" placeholder="이름(예: support)" required maxlength="60">' +
      '<input id="mb-host" placeholder="IMAP 서버(예: imap.gmail.com)" required>' +
      '<input id="mb-port" type="number" min="1" max="65535" value="993" style="width:6rem" title="IMAP SSL 포트">' +
      '<input id="mb-user" placeholder="메일 계정(예: support@회사.com)" required>' +
      '<input id="mb-pwenv" placeholder="앱 비밀번호 환경변수 이름(예: HAIR_MAIL_SUPPORT_PASSWORD)" required maxlength="64" style="min-width:16rem">' +
      '<input id="mb-folder" placeholder="폴더(기본 INBOX)" style="width:9rem">' +
      '<select id="mb-vis"><option value="admin">admin만</option><option value="manager">manager 이상</option><option value="all">모든 사람</option></select>' +
      '<input id="mb-desc" placeholder="설명(Agent가 어떤 메일함인지 알 수 있게)" maxlength="500" style="min-width:14rem">' +
      '<button class="primary" type="submit">메일함 등록</button></form>' +
      (mbRows ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>계정</th><th>공개 범위</th><th>비밀번호</th><th>상태</th><th>마지막 확인</th><th></th></tr></thead><tbody>' + mbRows + "</tbody></table></div>"
        : '<p class="muted">등록된 메일함이 없습니다.</p>') +
      // 캘린더(2026-11-26, P1-9 3단계 — `calendar_feeds.py`).
      "<h3>캘린더(admin)</h3>" +
      '<p class="muted">업무 캘린더의 <b>iCal(ICS) 주소</b>(Google 캘린더 설정 → "비공개 iCal 주소", Outlook → 캘린더 게시 → ICS 링크) 또는 서버의 <code>.ics</code> 파일을 등록하면 <b>캘린더조회</b> 스킬이 있는 Agent(PM·개발 계획 수립가·실적 보고서 작성자)가 기간 안의 일정을 읽습니다(반복 일정 펼침, 취소 일정 제외). ' +
      '<b>읽기 전용</b>이고 <b>공개 범위</b>로 누구의 요청일 때 읽을지 정합니다(기본 admin만). 조회마다 활동 기록(<code>calendar_read</code>)이 남습니다. ' +
      '비공개 iCal 주소는 그 자체가 비밀 키라 <b>저장하지 않습니다</b> — 서버 PC 환경변수에 넣고 그 이름만 적으세요(https만). 시간대는 환경변수 <code>HAIR_TIMEZONE</code>(예: Asia/Seoul), 없으면 서버 PC 시간대입니다.</p>' +
      '<form id="cf-form" class="inline" style="flex-wrap:wrap">' +
      '<input id="cf-name" placeholder="이름(예: sales_team)" required maxlength="60">' +
      '<select id="cf-kind"><option value="url">iCal 주소(환경변수)</option><option value="file">서버의 .ics 파일</option></select>' +
      '<input id="cf-source" placeholder="환경변수 이름(예: HAIR_CAL_SALES_URL) 또는 .ics 경로" required style="min-width:18rem">' +
      '<select id="cf-vis"><option value="admin">admin만</option><option value="manager">manager 이상</option><option value="all">모든 사람</option></select>' +
      '<input id="cf-desc" placeholder="설명(Agent가 어떤 캘린더인지 알 수 있게)" maxlength="500" style="min-width:14rem">' +
      '<button class="primary" type="submit">캘린더 등록</button></form>' +
      (cfRows ? '<div class="table-wrap"><table><thead><tr><th>이름</th><th>출처</th><th>공개 범위</th><th>상태</th><th>마지막 확인</th><th></th></tr></thead><tbody>' + cfRows + "</tbody></table></div>"
        : '<p class="muted">등록된 캘린더가 없습니다.</p>')
      : "") +
    (canSchedule ? '<div id="ext-flow-slot"></div>' : "");
  if (canSchedule) _renderExternalFlow(content.querySelector("#ext-flow-slot"), isAdmin, templates);

  document.getElementById("apikey-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    try {
      const created = await api("POST", "/api/api-keys", { name: document.getElementById("apikey-name").value });
      const box = document.getElementById("apikey-new");
      box.hidden = false;
      box.innerHTML = '<p style="font-weight:600">키를 발급했습니다 — <b>지금만 볼 수 있습니다.</b> 안전한 곳에 복사해 두세요.</p>' +
        '<div class="inline"><code id="apikey-token" style="user-select:all;word-break:break-all">' + esc(created.token) + "</code>" +
        '<button class="small" id="apikey-copy">복사</button></div>';
      document.getElementById("apikey-copy").addEventListener("click", () => {
        navigator.clipboard.writeText(created.token).then(
          () => showMsg("global-msg", "키를 복사했습니다.", "ok"),
          () => showMsg("global-msg", "복사하지 못했습니다 — 키를 직접 선택해 복사하세요.", "error"));
      });
    } catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  content.querySelectorAll("[data-revoke-key]").forEach(btn => btn.addEventListener("click", async () => {
    if (!(await confirmModal("이 키를 폐기합니다 — 이 키를 쓰는 외부 시스템은 즉시 막힙니다. 계속할까요?"))) return;
    try { await api("DELETE", "/api/api-keys/" + btn.dataset.revokeKey); renderIntegrations(content); }
    catch (e) { showMsg("global-msg", e.message, "error"); }
  }));
  if (canSchedule) {
    const intervalSel = document.getElementById("sch-interval");
    const weekdaySel = document.getElementById("sch-weekday");
    intervalSel.addEventListener("change", () => { weekdaySel.hidden = intervalSel.value !== "weekly"; });
    document.getElementById("schedule-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      try {
        await api("POST", "/api/pipeline-schedules", {
          name: document.getElementById("sch-name").value,
          pipeline_template_id: Number(document.getElementById("sch-template").value),
          brief_text: document.getElementById("sch-brief").value,
          role_name: document.getElementById("sch-role").value,
          interval: intervalSel.value,
          weekday: intervalSel.value === "weekly" ? Number(weekdaySel.value) : null,
          time_hhmm: document.getElementById("sch-time").value,
        });
        showMsg("global-msg", "정기 실행을 추가했습니다.", "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    content.querySelectorAll("[data-toggle-schedule]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        await api("PATCH", "/api/pipeline-schedules/" + btn.dataset.toggleSchedule, { active: btn.dataset.active === "1" });
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
    content.querySelectorAll("[data-delete-schedule]").forEach(btn => btn.addEventListener("click", async () => {
      if (!(await confirmModal("이 정기 실행을 삭제할까요? 이미 만들어진 업무·실행 이력은 남습니다."))) return;
      try { await api("DELETE", "/api/pipeline-schedules/" + btn.dataset.deleteSchedule); renderIntegrations(content); }
      catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
  }
  if (canSchedule) {
    const evSel = document.getElementById("trig-event");
    const srcWrap = document.getElementById("trig-source-wrap");
    evSel.onchange = () => { srcWrap.hidden = evSel.value !== "pipeline_completed"; };
    document.getElementById("trig-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      try {
        await api("POST", "/api/automation-triggers", {
          name: document.getElementById("trig-name").value.trim(),
          event: evSel.value,
          source_template_id: evSel.value === "pipeline_completed" ? Number(document.getElementById("trig-source").value) : null,
          action_template_id: Number(document.getElementById("trig-action").value),
          role_name: document.getElementById("trig-role").value.trim(),
          brief_text: document.getElementById("trig-brief").value.trim(),
        });
        showMsg("global-msg", "트리거를 추가했습니다 — 지금부터 생기는 일에만 반응합니다.", "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    content.querySelectorAll("[data-toggle-trigger]").forEach(btn => btn.addEventListener("click", async () => {
      try { await api("PATCH", "/api/automation-triggers/" + btn.dataset.toggleTrigger, { active: btn.dataset.active === "1" }); renderIntegrations(content); }
      catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
    content.querySelectorAll("[data-delete-trigger]").forEach(btn => btn.addEventListener("click", async () => {
      try { await api("DELETE", "/api/automation-triggers/" + btn.dataset.deleteTrigger); renderIntegrations(content); }
      catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
  }
  if (isAdmin) {
    document.getElementById("notify-save").addEventListener("click", async () => {
      try {
        await api("PUT", "/api/integrations/notify", {
          webhook_url: document.getElementById("notify-url").value,
          events: [...content.querySelectorAll(".notify-event:checked")].map(c => c.value),
          emails: document.getElementById("notify-emails").value.split(/[,;\s]+/).map(v => v.trim()).filter(Boolean),
        });
        showMsg("global-msg", "외부 알림 설정을 저장했습니다.", "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    document.getElementById("notify-test").addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/integrations/notify/test");
        showMsg("global-msg", r.ok ? "테스트 메시지를 보냈습니다." : "전송 실패: " + r.error, r.ok ? "ok" : "error");
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    document.getElementById("repo-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      try {
        const r = await api("POST", "/api/code-repositories", {
          name: document.getElementById("repo-name").value.trim(),
          path: document.getElementById("repo-path").value.trim(),
          description: document.getElementById("repo-desc").value.trim(),
        });
        showMsg("global-msg", "코드 저장소 '" + r.name + "'을(를) 등록했습니다 — " + r.path, "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    document.getElementById("dc-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      const v = id => document.getElementById(id).value.trim();
      try {
        const r = await api("POST", "/api/data-connections", {
          name: v("dc-name"), kind: v("dc-kind"), target: v("dc-target"), username: v("dc-user") || null,
          password_env: v("dc-pwenv") || null, row_limit: Number(v("dc-limit") || 200), description: v("dc-desc"),
        });
        showMsg("global-msg", "DB 연결 '" + r.name + "'을(를) 등록했습니다 — '연결 확인'으로 테이블이 보이는지 확인하세요.", "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    content.querySelectorAll("[data-test-dc]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/data-connections/" + btn.dataset.testDc + "/test");
        showMsg("global-msg", "연결 성공 — " + r.tables.split(String.fromCharCode(10)).length + "줄의 테이블 목록을 읽었습니다.", "ok");
      } catch (e) { showMsg("global-msg", "연결 실패: " + e.message, "error"); }
    }));
    document.getElementById("mb-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      const v = id => document.getElementById(id).value.trim();
      try {
        const r = await api("POST", "/api/mail-inboxes", {
          name: v("mb-name"), host: v("mb-host"), port: Number(v("mb-port") || 993), username: v("mb-user"),
          password_env: v("mb-pwenv"), folder: v("mb-folder") || "INBOX", visibility: v("mb-vis"), description: v("mb-desc"),
        });
        showMsg("global-msg", "메일함 '" + r.name + "'을(를) 등록했습니다 — '연결 확인'으로 접속을 확인하세요.", "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    content.querySelectorAll("[data-test-mb]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/mail-inboxes/" + btn.dataset.testMb + "/test");
        showMsg("global-msg", "연결 성공 — 최근 1일 메일 " + r.recent + "통을 찾았습니다(읽음 표시 변경 없음).", "ok");
      } catch (e) { showMsg("global-msg", "연결 실패: " + e.message, "error"); }
    }));
    document.getElementById("cf-form").addEventListener("submit", async ev => {
      ev.preventDefault();
      const v = id => document.getElementById(id).value.trim();
      try {
        const r = await api("POST", "/api/calendar-feeds", {
          name: v("cf-name"), source_kind: v("cf-kind"), source: v("cf-source"), visibility: v("cf-vis"), description: v("cf-desc"),
        });
        showMsg("global-msg", "캘린더 '" + r.name + "'을(를) 등록했습니다 — '연결 확인'으로 일정을 읽을 수 있는지 확인하세요.", "ok");
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    });
    content.querySelectorAll("[data-test-cf]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/calendar-feeds/" + btn.dataset.testCf + "/test");
        showMsg("global-msg", "연결 성공 — 전체 일정 " + r.events + "건, 앞으로 30일 " + r.upcoming + "건(" + r.tz + ").", "ok");
      } catch (e) { showMsg("global-msg", "연결 실패: " + e.message, "error"); }
    }));
    content.querySelectorAll("[data-toggle-cf]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        await api("PATCH", "/api/calendar-feeds/" + btn.dataset.toggleCf, { is_active: btn.dataset.active === "1" });
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
    content.querySelectorAll("[data-toggle-mb]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        await api("PATCH", "/api/mail-inboxes/" + btn.dataset.toggleMb, { is_active: btn.dataset.active === "1" });
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
    content.querySelectorAll("[data-toggle-dc]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        await api("PATCH", "/api/data-connections/" + btn.dataset.toggleDc, { is_active: btn.dataset.active === "1" });
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
    content.querySelectorAll("[data-toggle-repo]").forEach(btn => btn.addEventListener("click", async () => {
      try {
        await api("PATCH", "/api/code-repositories/" + btn.dataset.toggleRepo, { is_active: btn.dataset.active === "1" });
        renderIntegrations(content);
      } catch (e) { showMsg("global-msg", e.message, "error"); }
    }));
  }
}

// 이용 가이드는 절 하나씩(2026-11-27, 사용성 평가 10번) — 56,000자·화면 높이 34,000px 한 페이지라 어디를 읽는지 놓치기
// 쉬웠다. 절(h2) 목록을 옆에 두고 한 절만 보이며, 이전/다음으로 넘긴다. ❓ 도움말·🔍 메뉴 찾기·본문 안 링크(#id)는 그 절을
// 열고 해당 위치로 간다(`openManualAt`). "전체 한 페이지로"를 켜면 예전처럼 다 보인다(이 브라우저에 기억).
let _manualChapter = "toc";
function _manualAll() { try { return localStorage.getItem("hair_manual_all") === "1"; } catch (e) { return false; } }

async function renderManual(content) {
  content.innerHTML = '<section class="card manual-card"><div class="manual-layout">' +
    '<nav class="manual-toc" aria-label="이용 가이드 목차"></nav>' +
    '<div class="manual-main"><div class="md-content manual-content">' + _renderMarkdown(MANUAL_MARKDOWN) + "</div>" +
    '<div class="manual-pager"></div></div></div></section>';
  const root = content.querySelector(".manual-content");
  // h2마다 절로 묶는다(첫 h2 앞의 내용은 첫 절에 넣는다).
  const chapters = [];
  let cur = null;
  [...root.childNodes].forEach(node => {
    if (node.nodeType === 1 && node.tagName === "H2") {
      cur = document.createElement("section");
      cur.className = "manual-chapter";
      cur.dataset.ch = node.id || ("ch" + chapters.length);
      chapters.push({ id: cur.dataset.ch, title: node.textContent.trim(), el: cur });
      root.appendChild(cur);
    }
    if (cur) cur.appendChild(node);
  });
  const toc = content.querySelector(".manual-toc");
  // 목차는 짧은 제목만(2026-11-27 사용자 요청 — 왼쪽 목차가 너무 넓어 본문 폭이 좁았다). "—" 뒤 설명·괄호를 떼고 전체 제목은 툴팁으로.
  const shortTitle = t => t.split(" — ")[0].replace(/\s*\([^)]*\)/g, "").trim();
  // 1부 이용자 편 / 2부 운영자 편(2026-11-27 재구성) — 각 부의 첫 절 앞에 작은 구분 표시.
  const partLabel = { ov: "이용자 편", agent: "운영자 편" };
  toc.innerHTML = chapters.map(c => (partLabel[c.id] ? '<span class="manual-part">' + partLabel[c.id] + "</span>" : "") +
    '<button type="button" class="small" data-manual-ch="' + escAttr(c.id) + '" title="' + escAttr(c.title) + '">' + esc(shortTitle(c.title)) + "</button>").join("") +
    '<label class="manual-all"><input type="checkbox" id="manual-all"' + (_manualAll() ? " checked" : "") + "> 전체 한 페이지로</label>";
  const show = (id, scroll) => {
    if (!chapters.some(c => c.id === id)) id = chapters[0] && chapters[0].id;
    _manualChapter = id;
    const all = content.querySelector("#manual-all").checked;
    chapters.forEach(c => { c.el.hidden = !all && c.id !== id; });
    toc.querySelectorAll("[data-manual-ch]").forEach(b => b.classList.toggle("active", b.dataset.manualCh === id));
    const i = chapters.findIndex(c => c.id === id);
    const prev = chapters[i - 1], next = chapters[i + 1];
    content.querySelector(".manual-pager").innerHTML = all ? "" :
      (prev ? '<button type="button" class="small" data-manual-ch="' + escAttr(prev.id) + '">← ' + esc(prev.title) + "</button>" : "<span></span>") +
      (next ? '<button type="button" class="small primary" data-manual-ch="' + escAttr(next.id) + '">' + esc(next.title) + " →</button>" : "");
    if (scroll) content.querySelector(".manual-card").scrollIntoView({ block: "start" });
  };
  window._showManualChapter = show;
  content.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-manual-ch]");
    if (b) { show(b.dataset.manualCh, true); return; }
    const a = ev.target.closest('.manual-content a[href^="#"]');
    if (a && a.getAttribute("href").length > 1) {
      const el = root.querySelector(a.getAttribute("href"));
      if (el) { ev.preventDefault(); _manualReveal(el); }
    }
  });
  content.querySelector("#manual-all").onchange = (ev) => {
    try { localStorage.setItem("hair_manual_all", ev.target.checked ? "1" : "0"); } catch (e) { /* 기억 못 해도 동작 */ }
    show(_manualChapter, false);
  };
  show(_manualChapter, false);
}

// 가이드 안의 한 위치를 보이게 — 그 절을 열고 스크롤해 잠깐 강조한다.
function _manualReveal(el) {
  const ch = el.closest(".manual-chapter");
  if (ch && typeof window._showManualChapter === "function") window._showManualChapter(ch.dataset.ch, false);
  el.scrollIntoView({ block: "start" });
  el.classList.add("manual-target");
  setTimeout(() => el.classList.remove("manual-target"), 2500);
}

boot();

/* ---------- 외부 입력 트리거·결재 후 실행 동작(2026-11-27, AI Native 분석 4단계 — 6번·5번) ---------- */
// 연동 설정 맨 아래 — 자기 데이터를 따로 불러 그린다(다른 구역과 독립).
async function _renderExternalFlow(slot, isAdmin, templates) {
  if (!slot) return;
  const [inb, acts, mbs, cals] = await Promise.all([
    api("GET", "/api/inbound-triggers").catch(() => ({ triggers: [], sources: {} })),
    isAdmin ? api("GET", "/api/stage-actions").catch(() => null) : Promise.resolve(null),
    isAdmin ? api("GET", "/api/mail-inboxes").catch(() => []) : Promise.resolve([]),
    isAdmin ? api("GET", "/api/calendar-feeds").catch(() => []) : Promise.resolve([]),
  ]);
  const tplOpts = (templates || []).filter(t => t.is_active !== 0 && t.is_active !== false)
    .map(t => '<option value="' + t.id + '">' + esc(t.name) + "</option>").join("");
  const cond = t => t.source === "mail" ? "메일함 " + esc(t.config.mailbox) + (t.config.query ? " · '" + esc(t.config.query) + "'" : "")
    : t.source === "calendar" ? "캘린더 " + esc(t.config.calendar) + " · " + t.config.hours_before + "시간 전" + (t.config.query ? " · '" + esc(t.config.query) + "'" : "")
    : '<code>POST /api/inbound/' + t.id + "</code>";
  const inbRows = inb.triggers.map(t => [
    esc(t.name), esc(t.source_label) + "<br>" + cond(t), esc(t.action_template_name || ""),
    "시작 " + t.started_count + " · 대기 " + t.queued_count + (t.failed_count ? " · 실패 " + t.failed_count : "") + '<div class="muted pm-note">시간당 최대 ' + t.max_per_hour + "</div>",
    (t.last_error ? '<span class="gate-badge gate-warn" title="' + escAttr(t.last_error) + '">오류</span> ' : "") + (t.last_fired_at ? esc(fmtTime(t.last_fired_at)) : '<span class="muted">-</span>'),
    '<button class="small" data-inb-toggle="' + t.id + '" data-active="' + (t.is_active ? 0 : 1) + '">' + (t.is_active ? "끄기" : "켜기") + "</button> " +
      '<button class="small" data-inb-events="' + t.id + '">최근</button> <button class="small danger" data-inb-del="' + t.id + '">삭제</button>',
  ]);
  let html = "<h3>외부 입력 트리거(admin/manager)</h3>" +
    '<p class="muted">밖에서 일이 들어오면 파이프라인을 시작합니다 — <b>메일</b>(등록된 메일함에 조건에 맞는 새 메일, 2분마다 확인) · <b>웹훅</b>(티켓·폼 시스템이 JSON을 보냄, 비밀 토큰) · <b>일정</b>(등록된 캘린더 일정이 N시간 안으로 다가오면, 10분마다 확인). ' +
    "들어온 내용은 <b>외부 데이터</b>로 표시돼 업무 요청에 붙고(그 안의 지시는 따르지 않음), 그 실행에서는 쓰기·실행 도구가 막힙니다. 만들 때 이미 있던 메일·일정으로는 실행하지 않고, 트리거마다 시간당 상한이 있습니다. 만든 사람 이름으로 실행됩니다.</p>" +
    '<form id="inb-form" style="display:grid;gap:0.4rem"><div class="inline" style="flex-wrap:wrap">' +
    '<input id="inb-name" placeholder="이름(예: 고객 문의 메일 → 회신 초안)" required maxlength="100">' +
    '<select id="inb-source"><option value="mail">메일 도착</option><option value="webhook">외부 시스템 호출(웹훅)</option><option value="calendar">다가오는 일정</option></select>' +
    '<span id="inb-mail-cfg"><input id="inb-mailbox" list="inb-mailboxes" placeholder="메일함 이름"><datalist id="inb-mailboxes">' + (mbs || []).map(m => '<option value="' + escAttr(m.name) + '">').join("") + "</datalist> " +
    '<input id="inb-query" placeholder="보낸 사람·제목 낱말(선택)" maxlength="100"></span>' +
    '<span id="inb-cal-cfg" hidden><input id="inb-calendar" list="inb-calendars" placeholder="캘린더 이름"><datalist id="inb-calendars">' + (cals || []).map(c => '<option value="' + escAttr(c.name) + '">').join("") + "</datalist> " +
    '<input id="inb-hours" type="number" min="1" max="168" value="24" style="width:5rem"> 시간 전 <input id="inb-cquery" placeholder="일정 제목 낱말(선택)" maxlength="100"></span></div>' +
    '<div class="inline" style="flex-wrap:wrap"><label class="muted">→ 시작할 파이프라인 <select id="inb-action" required>' + tplOpts + "</select></label>" +
    '<input id="inb-role" placeholder="새 할 일 역할(예: 문의 회신)" required maxlength="100"> <label class="muted">시간당 최대 <input id="inb-mph" type="number" min="1" max="200" value="20" style="width:5rem"></label></div>' +
    '<textarea id="inb-brief" rows="2" required placeholder="들어온 일로 할 업무(예: 아래 고객 문의에 제품 매뉴얼을 근거로 회신 초안을 써 줘. 환불·보상은 약속하지 마.)"></textarea>' +
    '<div><button class="primary" type="submit">외부 입력 트리거 추가</button> <span class="muted" id="inb-msg"></span></div></form>' +
    '<div id="inb-token"></div>' +
    table(["이름", "들어오는 경로", "시작할 파이프라인", "처리", "마지막 시작", ""], inbRows, { empty: "등록된 외부 입력 트리거가 없습니다." }) +
    '<div id="inb-events"></div>';
  if (isAdmin && acts) {
    const actRows = acts.actions.map(a => {
      const c = a.config || {};
      const target = a.kind === "email" ? (c.to_mode === "inbound_sender" ? "시작한 메일의 보낸 사람에게 회신" : esc((c.to || []).join(", "))) + (c.subject ? " · 제목 '" + esc(c.subject) + "'" : "")
        : (c.url_env ? "환경변수 " + esc(c.url_env) : esc(c.url || "")) + (c.token_env ? " · 토큰 " + esc(c.token_env) : "");
      return [esc(a.template_name) + " — " + a.seq_order + ". " + esc(a.stage_name), esc(a.kind_label), target,
        a.is_active ? "켜짐" : '<span class="muted">꺼짐</span>', '<button class="small danger" data-act-del="' + a.stage_id + '">삭제</button>'];
    });
    const exRows = acts.executions.map(e => [esc(fmtTime(e.created_at)), esc(e.stage_name || ""), esc(e.kind), esc(e.target || ""),
      ({ sent: '<span class="gate-badge gate-ok">보냄</span>', failed: '<span class="gate-badge gate-warn">실패</span>', skipped: '<span class="muted">승인만</span>' }[e.status] || esc(e.status)) +
        (e.error ? '<div class="muted pm-note">' + esc(e.error) + "</div>" : ""), esc(e.decided_by_name || "")]);
    html += "<h3>결재 후 실행 동작(admin)</h3>" +
      '<p class="muted">파이프라인 단계에 동작을 붙이면, 그 단계 결과를 결재할 때 결재 도우미에 <b>무엇을 어디로 보낼지</b>가 보이고 <b>"승인하고 실행"</b>을 누를 때만 실제로 보냅니다(표의 "승인"은 승인만). 결과 하나당 한 번, 성공·실패 모두 활동 기록에 남습니다. ' +
      "동작이 붙은 단계는 낮은 위험 자동 승인 대상에서 빠집니다. 메일은 서버 SMTP 설정(HAIR_SMTP_*)" + (acts.smtp_configured ? '이 있습니다.' : '이 <b>없어</b> 지금은 보낼 수 없습니다.') +
      " 웹훅 주소·토큰은 환경변수 이름으로 주면 DB에 남지 않습니다.</p>" +
      '<form id="act-form" style="display:grid;gap:0.4rem"><div class="inline" style="flex-wrap:wrap">' +
      '<select id="act-tpl"><option value="">파이프라인 고르기</option>' + tplOpts + '</select> <select id="act-stage" required><option value="">단계</option></select>' +
      '<select id="act-kind"><option value="email">메일 보내기</option><option value="chat">Slack·Teams에 올리기</option><option value="json_webhook">외부 시스템에 보내기(JSON)</option></select></div>' +
      '<div class="inline" style="flex-wrap:wrap" id="act-email-cfg"><select id="act-tomode"><option value="fixed">정해 둔 주소로</option><option value="inbound_sender">시작한 메일의 보낸 사람에게 회신</option></select>' +
      '<input id="act-to" placeholder="받는 사람(쉼표로)" style="min-width:16rem"> <input id="act-subject" placeholder="제목(비우면 Re: 원래 제목 또는 파이프라인 — 단계)" style="min-width:16rem"></div>' +
      '<div class="inline" style="flex-wrap:wrap" id="act-hook-cfg" hidden><input id="act-url" placeholder="https://… (또는 오른쪽에 환경변수 이름)" style="min-width:16rem"> <input id="act-urlenv" placeholder="주소 환경변수(예: HAIR_TICKET_URL)"> <input id="act-tokenenv" placeholder="토큰 환경변수(JSON만, 선택)"></div>' +
      '<div><button class="primary" type="submit">실행 동작 붙이기</button> <span class="muted" id="act-msg"></span></div></form>' +
      table(["단계", "동작", "어디로", "상태", ""], actRows, { empty: "붙인 실행 동작이 없습니다." }) +
      "<h4>최근 실행</h4>" + table(["시각", "단계", "종류", "어디로", "결과", "결재자"], exRows, { empty: "아직 실행한 동작이 없습니다." });
  }
  slot.innerHTML = html;
  const $ = id => slot.querySelector("#" + id);
  const src = $("inb-source");
  src.onchange = () => { $("inb-mail-cfg").hidden = src.value !== "mail"; $("inb-cal-cfg").hidden = src.value !== "calendar"; };
  $("inb-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const s = src.value;
    const config = s === "mail" ? { mailbox: $("inb-mailbox").value.trim(), query: $("inb-query").value.trim() }
      : s === "calendar" ? { calendar: $("inb-calendar").value.trim(), hours_before: Number($("inb-hours").value), query: $("inb-cquery").value.trim() } : {};
    try {
      const t = await api("POST", "/api/inbound-triggers", {
        name: $("inb-name").value, source: s, config, action_template_id: Number($("inb-action").value),
        role_name: $("inb-role").value, brief_text: $("inb-brief").value, max_per_hour: Number($("inb-mph").value),
      });
      if (t.token) {
        const box = $("inb-token");
        await _renderExternalFlow(slot, isAdmin, templates);
        slot.querySelector("#inb-token").innerHTML = '<div class="msg warn"><b>웹훅 토큰 — 지금 한 번만 보입니다.</b> 외부 시스템 설정에 넣으세요.<br>' +
          '주소: <code>' + esc(location.origin + t.webhook_path) + "</code><br>헤더: <code>X-HAIR-Token: " + esc(t.token) + "</code><br>" +
          '본문 예: <code>{"id": "TICKET-123", "title": "환불 문의", "text": "…"}</code> <button class="small" id="inb-copy">토큰 복사</button></div>';
        const copy = slot.querySelector("#inb-copy");
        copy.onclick = () => navigator.clipboard.writeText(t.token).then(() => { copy.textContent = "복사됨"; }).catch(() => {});
        void box;
        return;
      }
      _renderExternalFlow(slot, isAdmin, templates);
    } catch (e) { $("inb-msg").textContent = e.message || String(e); }
  });
  slot.querySelectorAll("[data-inb-toggle]").forEach(b => b.onclick = async () => {
    try { await api("PATCH", "/api/inbound-triggers/" + b.dataset.inbToggle, { active: b.dataset.active === "1" }); _renderExternalFlow(slot, isAdmin, templates); }
    catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  slot.querySelectorAll("[data-inb-del]").forEach(b => b.onclick = async () => {
    if (b.dataset.confirming !== "1") { b.dataset.confirming = "1"; b.textContent = "정말 삭제"; return; }
    try { await api("DELETE", "/api/inbound-triggers/" + b.dataset.inbDel); _renderExternalFlow(slot, isAdmin, templates); }
    catch (e) { showMsg("global-msg", e.message, "error"); }
  });
  slot.querySelectorAll("[data-inb-events]").forEach(b => b.onclick = async () => {
    const evs = await api("GET", "/api/inbound-triggers/" + b.dataset.inbEvents + "/events");
    const st = { queued: "대기", started: "시작함", failed: "실패", seeded: "기존 것" };
    $("inb-events").innerHTML = "<h4>최근 들어온 일</h4>" + table(["받은 시각", "제목", "상태", "실행", "비고"], evs.map(e => [
      esc(fmtTime(e.received_at)), esc(e.title || e.event_key), esc(st[e.status] || e.status), e.run_id ? "#" + e.run_id : "-", esc(e.note || "")]),
      { empty: "아직 들어온 일이 없습니다." });
  });
  if (!(isAdmin && acts)) return;
  const kind = $("act-kind");
  kind.onchange = () => { $("act-email-cfg").hidden = kind.value !== "email"; $("act-hook-cfg").hidden = kind.value === "email"; };
  $("act-tpl").onchange = async () => {
    const id = $("act-tpl").value;
    $("act-stage").innerHTML = '<option value="">단계</option>';
    if (!id) return;
    const d = await api("GET", "/api/pipeline-templates/" + id);
    $("act-stage").innerHTML = (d.stages || []).map(s => '<option value="' + s.id + '">' + s.seq_order + ". " + esc(s.name) + (s.requires_human_approval ? " (사람 승인)" : "") + "</option>").join("");
  };
  $("act-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const k = kind.value;
    const config = k === "email"
      ? { to_mode: $("act-tomode").value, to: $("act-to").value.split(",").map(x => x.trim()).filter(Boolean), subject: $("act-subject").value.trim() }
      : { url: $("act-url").value.trim(), url_env: $("act-urlenv").value.trim(), token_env: $("act-tokenenv").value.trim() };
    try {
      await api("PUT", "/api/pipeline-stages/" + $("act-stage").value + "/action", { kind: k, config, is_active: true });
      _renderExternalFlow(slot, isAdmin, templates);
    } catch (e) { $("act-msg").textContent = e.message || String(e); }
  });
  slot.querySelectorAll("[data-act-del]").forEach(b => b.onclick = async () => {
    if (b.dataset.confirming !== "1") { b.dataset.confirming = "1"; b.textContent = "정말 삭제"; return; }
    try { await api("DELETE", "/api/pipeline-stages/" + b.dataset.actDel + "/action"); _renderExternalFlow(slot, isAdmin, templates); }
    catch (e) { showMsg("global-msg", e.message, "error"); }
  });
}

