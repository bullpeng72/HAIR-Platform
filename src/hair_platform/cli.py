"""통합 CLI — `hair` 하나로 환경변수(.env) 설정/점검·서버 실행·최초 ADMIN 계정 생성·Gate 측정을 다 한다.

사용자 요청(2026-10-10, "실행 명령어가 너무 길고 어려워. 명령어를
짧고 명료하게 정리하고, 현행화 해줘. 명령어에 대한 도움말(--help
도움말 포함)을 가독성 높게 구현해줘") — 이전까지 README가 안내하던
실행 방법은 세 가지 서로 다른 패턴이 뒤섞여 있었다:
`uvicorn hair_platform.app:create_app --factory --reload`(외부 uvicorn
CLI를 직접 호출 — 그리고 DB 경로를 지정할 방법이 아예 없어서 매번
`:memory:`로 뜨고 있었다는 걸 이번에 처음 발견),
`hair-platform-bootstrap-admin --db-path hair.db --username admin
--password '...'`(인자 4개짜리 별도 콘솔 스크립트), `python -m
hair_platform.eval_wiring`(모듈 직접 실행). 전부 `hair <subcommand>`
하나의 진입점 아래로 모은다 — 기존 `bootstrap.py`의 CLI/테스트는
그대로 두고(`bootstrap_admin()` 함수만 재사용), `hair-platform-
bootstrap-admin` 콘솔 스크립트는 `hair bootstrap-admin`으로 대체돼
`pyproject.toml`에서 제거한다(두 진입점을 동시에 유지하는 대신
하나로 정리 — 이 프로젝트의 "안 쓰는 경로를 호환성 명목으로
남기지 않는다" 관례 그대로)."""

from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


# 사용자 보고(2026-11-12, "아무 동작없이도 메시지가 출력되는 원인을
# 분석하고, 오류 등 꼭 필요한 메시지만 노출되도록 개선해줘") — 처음엔
# `webui.py`의 멘션 알림 폴링(`startMentionNotificationPolling()`)과
# 대화 메시지 폴링, 이 두 배경 요청 경로만 걸러냈었다. 이후 사용자
# 보고(2026-11-16, 실제 서버 로그 붙여넣기 — "200 OK 와 같은 성공
# 메시지는 노출되지 않도록 개선(에러, 경고 등만 노출)")로 범위를
# 넓힌다 — 성공(2xx/3xx) 요청은 어떤 메서드·경로든 전부 조용히 하고,
# 실패(4xx/5xx)만 사람이 봐야 할 신호로 남긴다. uvicorn.access
# 핸들러에만 거는 필터라 uvicorn.error 로거(시작/종료 메시지 등)는
# 그대로 영향받지 않는다.
class _QuietAccessFilter(logging.Filter):
    """uvicorn access 로그 레코드의 `args`는 `(client_addr, method,
    path, http_version, status)` 순서다(uvicorn의 h11_impl.py
    `access_logger.info("%s - \"%s %s HTTP/%s\" %d", ...)` 호출과
    동일한 순서로 직접 확인함) — 그 외 형태(uvicorn.error 로거 등)는
    그대로 통과시킨다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        status = args[4]
        return not isinstance(status, int) or status >= 400


def _build_log_config() -> dict:
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {
        "quiet_access": {"()": f"{__name__}._QuietAccessFilter"},
    }
    config["handlers"]["access"]["filters"] = ["quiet_access"]
    return config

_EPILOG_TOP = """\
예시:
  hair init                               .env 환경변수 설정(대화형, 최초 1회)
  hair check                              .env/환경변수 점검
  hair serve                              서버 실행 + 브라우저 자동 열기(http://127.0.0.1:8000, hair.db)
  hair serve --port 9000 --reload         개발 중 코드 변경 자동 반영
  hair serve --no-browser                 브라우저 자동 열기 끄기(서버만 실행)
  hair bootstrap-admin -u admin -p ***    최초 관리자 계정 생성(최초 1회만)
  hair reset-data                         운영 데이터 전부 초기화(Agent/Tool/Skill/파이프라인은 유지, 팀은 '초기화 시 보호' 팀만 유지)
  hair eval                               Gate A/B/D/E/G 측정 1회 + HTML 리포트(results/)

서브커맨드별 자세한 옵션: hair <subcommand> --help
"""

# 사용자 요청(2026-10-12, "hair 입력시 간단한 도움말 출력되도록 개선") —
# 인자 없이 `hair`만 치면 이전엔 argparse가 "command가 필요하다"는
# 에러(exit 2)를 띄웠다. 처음 쓰는 사람 입장에서는 에러가 아니라 뭘 할 수
# 있는지 짧게 보여주는 게 맞다 — `hair --help`(아래 _EPILOG_TOP 포함,
# 상세 도움말)와는 분량을 다르게 둔다.
_BRIEF_HELP = """\
HAIR-Platform 통합 CLI — 사람과 AI Agent를 하나의 조직도에서 관리한다.

명령어:
  init              .env 환경변수 설정(최초 1회, 대화형)
  check             .env/환경변수 점검
  serve             Web UI + API 서버 실행
  bootstrap-admin   최초 ADMIN 계정 생성(콜드스타트 전용)
  reset-data        운영 데이터 초기화(Agent/Tool/Skill/파이프라인은 유지, 팀은 '초기화 시 보호' 팀만 유지)
  eval              Gate A/B/D/E/G 측정 1회 + HTML 리포트 생성(results/)

자세한 도움말:       hair --help
명령어별 도움말:     hair <command> --help
버전 확인:           hair --version
"""


def _get_version() -> str:
    from hair_platform import get_version

    return get_version()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hair",
        description="HAIR-Platform 통합 CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG_TOP,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"hair {_get_version()}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser(
        "init",
        help=".env 환경변수 설정(최초 1회, 대화형)",
        description=(
            "LLM API 키·세션 서명 키·메일(SMTP) 알림 등 주요 환경변수를 하나씩 물어보고\n"
            ".env 파일로 저장한다. 전부 선택(optional)이다 — 아무것도 입력하지 않아도 서버는\n"
            "뜬다. 이미 .env가 있으면 기존 값을 보여주고 Enter로 그대로 둘 수 있다(덮어쓰지 않음)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  hair init                      대화형으로 하나씩 묻고 .env에 저장\n"
            "  hair init --yes                묻지 않고 저장(세션 서명 키가 없으면 자동 생성)\n"
            "  hair init --path config/.env   다른 경로에 저장\n"
            "\n"
            "저장한 값 확인: hair check\n"
        ),
    )
    p_init.add_argument("--path", default=".env", help=".env 파일 경로(기본: ./.env)")
    p_init.add_argument(
        "--yes", action="store_true",
        help="대화형 질문 없이 바로 저장(세션 서명 키가 없으면 자동 생성, 나머지는 기존 값 그대로)",
    )
    p_init.set_defaults(func=_cmd_init)

    p_check = sub.add_parser(
        "check",
        help=".env/환경변수 점검",
        description=(
            "현재 .env 파일과 셸 환경변수를 합쳐(셸 쪽이 우선 — python-dotenv 기본 동작과 동일) 주요\n"
            "환경변수가 어떻게 설정돼 있는지 보여주고, provider/API 키 불일치·SMTP 설정 누락 같은\n"
            "흔한 실수를 찾아낸다. 값을 바꾸지는 않는다(점검만)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  hair check                점검(기본: ./.env)\n"
            "  hair check --path config/.env\n"
            "\n"
            "문제가 있거나 값을 바꾸려면: hair init\n"
        ),
    )
    p_check.add_argument("--path", default=".env", help=".env 파일 경로(기본: ./.env)")
    p_check.set_defaults(func=_cmd_check)

    p_serve = sub.add_parser(
        "serve",
        help="Web UI + API 서버 실행",
        description=(
            "FastAPI 서버를 띄운다. http://<host>:<port>/ 가 뜨면 브라우저를 자동으로 연다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  hair serve\n"
            "  hair serve --no-browser                  브라우저 자동 열기 끄기\n"
            "  hair serve --db-path :memory: --reload   휘발성 DB로 임시 실행 + 자동 재시작\n"
            "\n"
            "사내망 접속(같은 네트워크의 다른 PC에서도 접속):\n"
            "  hair init   # HAIR_PLATFORM_SECRET_KEY를 물어보면 자동 생성해 .env에 저장\n"
            "  hair serve --host 0.0.0.0 --no-browser   다른 PC에서 "
            "http://<이 PC의 사내망 IP>:8000/\n"
            "  (키를 안 주면 기동 시 경고가 뜬다 — 첫 기동 때 만든 무작위 "
            "키를 DB 파일에 저장해 쓰므로\n"
            "   DB 파일 접근 권한이 곧 세션 위조 권한이 된다. --reload는 "
            "공용 인스턴스에 쓰지 않는다 —\n"
            "   파일 변경마다 재시작되며 접속이 전부 끊긴다.)\n"
            "\n"
            "관련 환경변수(.env에 넣어도 된다 — 설정은 hair init, 점검은 hair check):\n"
            "  HAIR_PLATFORM_SECRET_KEY      세션 서명 키(사내망 공개 시 필수)\n"
            "  HAIR_EVAL_HTML_REPORT=0       평가마다 만드는 Gate A-G HTML 리포트(결과물 카드 "
            "\"평가 리포트\") 끄기\n"
            "  HAIR_OLLAMA_AGENT_PLANNING=1  로컬(Ollama) 모델에서도 Agent 작업 전 계획 켜기"
            "(기본 꺼짐)\n"
            "  Gate별 통과 기준·응답 시간 SLA는 서버 화면 \"평가 기준\"(admin)에서 바꾼다.\n"
        ),
    )
    p_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "바인딩할 호스트(기본: 127.0.0.1=로컬만 접속 가능). "
            "0.0.0.0을 주면 같은 네트워크의 다른 PC에서도 접속 가능 — "
            "이땐 HAIR_PLATFORM_SECRET_KEY를 먼저 설정해야 한다(자세히는 "
            "아래 예시 참고)."
        ),
    )
    p_serve.add_argument(
        "--port", type=int, default=8000, help="바인딩할 포트(기본: 8000)"
    )
    p_serve.add_argument(
        "--db-path",
        default="hair.db",
        help="SQLite DB 파일 경로(기본: hair.db). ':memory:'를 주면 종료 시 전부 사라진다.",
    )
    p_serve.add_argument(
        "--reload",
        action="store_true",
        help="코드 변경 시 자동 재시작(개발용, 기본 꺼짐)",
    )
    p_serve.add_argument(
        "--no-browser",
        action="store_true",
        help="서버 실행 후 브라우저를 자동으로 열지 않음(기본: 자동으로 염)",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_boot = sub.add_parser(
        "bootstrap-admin",
        help="최초 ADMIN 계정 생성(콜드스타트 전용, 최초 1회만 동작)",
        description=(
            "users 테이블이 비어 있을 때만 동작한다(최초 1회).\n"
            "그 이후 계정 추가는 로그인해서 POST /api/users를 쓴다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예시:\n  hair bootstrap-admin -u admin -p 'change-me'\n",
    )
    p_boot.add_argument(
        "-u", "--username", required=True, help="생성할 관리자 계정 이름"
    )
    p_boot.add_argument(
        "-p", "--password", required=True, help="생성할 관리자 계정 비밀번호"
    )
    p_boot.add_argument(
        "-d", "--db-path", default="hair.db", help="SQLite DB 파일 경로(기본: hair.db)"
    )
    p_boot.set_defaults(func=_cmd_bootstrap_admin)

    p_reset = sub.add_parser(
        "reset-data",
        help="운영 데이터 전부 초기화(Agent/Tool/Skill/파이프라인 템플릿은 유지, '초기화 시 보호' 팀만 유지)",
        # 지우는 범위는 data_admin._FACTORY_RESET_WIPE_TABLES와 맞춘다(테이블을 추가하면 여기도 갱신).
        description=(
            "운영 중 쌓인 데이터를 지운다. 되돌릴 수 없다.\n"
            "\n"
            "지움:\n"
            "  - 사용자 계정·개인 API 키, 사람 인력(human_pool)\n"
            "  - 업무 요청·할 일·배정(이력)·결재·결과물(댓글·버전·부분 수정 제안·저장소 커밋 기록)\n"
            "  - 대화·업로드 문서·지식 조각(VectorDB)·지식 등록 요청\n"
            "  - AI 에이전트 생성 Job·Crew/파이프라인 실행 이력·실행 작업\n"
            "  - Gate 평가 기록·평가 HTML 리포트·LLM 사용량 기록\n"
            "  - 모델 레지스트리(단가·생성 옵션)·모델 성능 테스트 기록·임베딩 설정\n"
            "  - 코드 저장소 등록·Tool-Agent 연결·정기 실행·이벤트 트리거·알림 기록\n"
            "  - 활동 기록(audit_log), 팀/팀원('초기화 시 보호'를 켠 팀은 남김)\n"
            "\n"
            "남김:\n"
            "  - Agent(기본 36종)·Tool/Skill Pool·파이프라인 템플릿 — 재기동 시 다시\n"
            "    채워지는 레퍼런스 데이터. 단 Agent의 비용·성과점수·LLM provider/model\n"
            "    지정·마지막 산출물은 비우고, 기본 Agent는 활성으로 되돌린다(관리자가\n"
            "    만든 Agent는 상태 유지).\n"
            "  - 조직 정책(사번 형식·결과물 언어·알림 설정)·플랫폼 기본/보조 모델 설정\n"
            "  - 평가 기준 설정(Gate별 통과 기준·SLA)·세션 서명 키·보호 팀"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  hair reset-data\n"
            "  hair reset-data --db-path hair.db --yes   확인 프롬프트 생략\n\n"
            "보통 바로 이어서 다시 콜드스타트:\n"
            "  hair bootstrap-admin -u admin -p '...'\n"
        ),
    )
    p_reset.add_argument(
        "-d", "--db-path", default="hair.db", help="SQLite DB 파일 경로(기본: hair.db)"
    )
    p_reset.add_argument(
        "--yes", action="store_true", help="확인 프롬프트 없이 바로 실행"
    )
    p_reset.set_defaults(func=_cmd_reset_data)

    p_eval = sub.add_parser(
        "eval",
        help="Gate A/B/D/E/G 측정 1회 + HTML 리포트 생성(results/에 저장, 비용 없음)",
        description=(
            "Agent-Evaluator SDK로 AI 에이전트 생성 경로(결정론적 스텁)를 측정한다.\n"
            "LLM을 호출하지 않아 비용이 없다. 현재 폴더의 results/에 저장한다:\n"
            "  results/agent_generation_eval.json   측정 결과(agent-eval gate 입력)\n"
            "  results/agent_generation_eval.html   Gate A-G 종합 리포트(지표·원인 진단·개선 제안)\n"
            "HTML 리포트는 HAIR_EVAL_HTML_REPORT=0이면 만들지 않는다.\n"
            "서버에서 실제 결과물을 평가한 리포트는 결과물 카드의 \"평가 리포트\"로 본다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  hair eval\n"
            "  agent-eval gate results/agent_generation_eval.json --tcr 85\n"
            "      TCR 85% 이상인지 채점(결과 파일은 한 번에 하나만 받는다)\n"
            "  agent-eval gate results/agent_generation_eval.json --html-out results/gate.html\n"
            "      채점 결과도 HTML로 저장\n"
        ),
    )
    p_eval.set_defaults(func=_cmd_eval)

    return parser


BROWSER_WAIT_SECONDS = 120.0


def _open_browser_when_ready(url: str, timeout: float = BROWSER_WAIT_SECONDS, interval: float = 0.25) -> bool:
    """서버가 `url`에 HTTP로 응답하면 브라우저를 연다(응답 코드는 따지지 않음 — 서버가 떠 있으면 됨).
    `timeout` 안에 응답이 없어도 마지막에 한 번은 연다(브라우저 쪽에서 새로고침하면 되도록).
    반환값: 시간 안에 서버가 응답했는지."""
    import time
    import urllib.error
    import urllib.request
    import webbrowser

    deadline = time.monotonic() + timeout
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            ready = True
            break
        except urllib.error.HTTPError:  # 4xx/5xx라도 서버는 떠 있다
            ready = True
            break
        except OSError:  # 아직 포트가 안 열림(연결 거부) — 잠시 뒤 다시
            time.sleep(interval)
    webbrowser.open(url)
    return ready


def _cmd_serve(args: argparse.Namespace) -> int:
    # uvicorn의 `--reload`는 재시작마다 팩토리 함수를 인자 없이 다시
    # 호출한다 — 그 경로로 `db_path`를 전달할 수단이 환경변수 말고는
    # 없다(`create_app()`이 `HAIR_PLATFORM_DB_PATH`를 보는 이유).
    os.environ["HAIR_PLATFORM_DB_PATH"] = args.db_path
    # 자동화 루프(정기 실행·외부 알림, 2026-11-23 — `automation.py`)는 `hair serve`로 띄운 서버에서만
    # 돈다. 테스트·외부 uvicorn 실행에서는 이 값이 없어 돌지 않는다.
    os.environ.setdefault("HAIR_AUTOMATION", "1")

    # 사용자 요청(2026-11-03, "사내망을 통해 다른 사용자도 접속하도록
    # 개선 방안을 제안해줘" → "1번, 2번 진행") — `--host 0.0.0.0` 등
    # 루프백이 아닌 주소로 띄우면서 `HAIR_PLATFORM_SECRET_KEY`를 안 주면
    # `app.py`의 세션 서명 키가 소스에 공개된 고정값으로 폴백했다(2026-11-22부터는 첫 기동 때
    # 만든 무작위 키를 DB에 저장해 쓴다 — DB 파일 접근 권한이 곧 세션 위조 권한이라 경고는 유지).
    # 막지는 않는다(로컬 1인 개발 흐름까지 깨면 안 됨) — 조용히 넘어가지
    # 않고 기동 시점에 경고만 한다.
    #
    # 실측으로 발견한 버그(2026-11-03, "외부 접속 가능한 구조로 서버
    # 기동해줘" 직후) — `.env`에 키를 넣어뒀는데도 이 경고가 떴다.
    # `create_app()`은 `load_dotenv()`를 자기 안에서 호출해 `.env`를
    # 읽지만, 이 경고는 그보다 먼저(`uvicorn.run()` 호출 전) 평가돼
    # `.env`가 아직 안 읽힌 `os.environ`만 보고 있었다 — 실제로는
    # 안전한데 거짓 경고가 뜨는 상태. `create_app()`과 같은 시점에
    # `load_dotenv()`를 먼저 호출해 같은 값을 보고 판단하게 한다.
    load_dotenv()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(
        "HAIR_PLATFORM_SECRET_KEY"
    ):
        print(
            "경고: HAIR_PLATFORM_SECRET_KEY 환경변수가 설정되지 않았습니다.\n"
            f"  --host {args.host}로 외부에서 접속 가능한 상태로 서버를 띄우면서\n"
            "  세션 서명 키를 첫 기동 때 무작위로 만들어 DB 파일에 저장해 씁니다 —\n"
            "  DB 파일을 읽을 수 있는 사람은 그 키로 로그인 세션을 위조할 수 있으니,\n"
            "  운영에서는 다음처럼 키를 환경변수로 지정하세요:\n"
            "    export HAIR_PLATFORM_SECRET_KEY=\"$(python3 -c "
            "'import secrets; print(secrets.token_hex(32))')\"\n",
            file=sys.stderr,
        )

    import uvicorn

    if not args.no_browser:
        import threading

        # 0.0.0.0/:: 은 "모든 인터페이스에 바인딩"이라는 뜻이라 브라우저
        # 주소로는 못 쓴다 — 로컬 접속 주소인 127.0.0.1로 대신 연다.
        browser_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        url = f"http://{browser_host}:{args.port}/"
        # uvicorn.run()은 블로킹이라 "서버가 뜬 시점" 콜백을 못 받는다. 예전엔 1.5초 고정 지연 후
        # 열었는데, 기동이 길어지면서(실측 2026-11-25: 스키마 점검·기본 구성 시드·crewai import로
        # 약 4초) 브라우저가 먼저 열려 "연결할 수 없음"이 뜨고 매번 새로고침해야 했다(사용자 보고).
        # 서버가 실제로 응답할 때까지 기다렸다가 연다(--reload는 부모 프로세스 기준 1회만 실행).
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    uvicorn.run(
        "hair_platform.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=_build_log_config(),
    )
    return 0


def _cmd_bootstrap_admin(args: argparse.Namespace) -> int:
    from hair_platform.bootstrap import bootstrap_admin

    try:
        user_id = bootstrap_admin(args.db_path, args.username, args.password)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"ADMIN 계정 생성 완료: id={user_id}, username={args.username}")
    return 0


def _cmd_reset_data(args: argparse.Namespace) -> int:
    from hair_platform import data_admin, db

    if not args.yes:
        answer = input(
            f"'{args.db_path}'의 운영 데이터를 전부 지웁니다 — 되돌릴 수 없습니다.\n"
            "  지움: 사용자·업무·결재·대화·결과물·실행 이력·평가 기록/리포트·지식 베이스·정기 실행·트리거·API 키·"
            "모델 레지스트리·활동 기록·AI 사용량, 팀('초기화 시 보호' 팀 제외)\n"
            "  남김: Agent·스킬·도구·파이프라인 템플릿, 조직 정책(사번 형식·결과물 언어·알림 설정), "
            "플랫폼 기본·보조 모델 설정, 평가 기준 설정, 보호 팀\n"
            "  Agent는 모델 지정·비용·성과점수가 비워지고, 기본 Agent는 활성으로 돌아갑니다"
            "(관리자가 만든 Agent는 상태 유지).\n"
            "계속할까요? [y/N] "
        )
        if answer.strip().lower() != "y":
            print("취소했습니다.")
            return 1

    conn = db.get_connection(args.db_path)
    db.init_schema(conn)  # 기존 DB를 재사용할 때 스키마/레퍼런스 시드를 먼저 맞춘다.
    counts = data_admin.reset_to_factory_state(conn)
    conn.close()

    total = sum(counts.values())
    print(
        f"초기화 완료 — {total}건 삭제(팀 {counts.get('teams', 0)}개 포함). "
        "Agent·스킬·도구·파이프라인 템플릿·조직 정책·보호 팀은 그대로입니다."
    )
    print(
        f"다음 단계(필요하면): hair bootstrap-admin -u <계정명> -p <비밀번호> --db-path {args.db_path}"
    )
    return 0


def _cmd_eval(_args: argparse.Namespace) -> int:
    import runpy

    # eval_wiring.py 자체의 `if __name__ == "__main__":` 블록을 그대로
    # 재사용한다(측정 예시·저장·완료 메시지를 여기서 다시 베껴 쓰면
    # 그쪽이 바뀔 때 둘이 어긋날 수 있다).
    runpy.run_module("hair_platform.eval_wiring", run_name="__main__")
    return 0


def _prompt_env_value(var, current: str) -> str:
    """`hair init` 대화형 입력 한 줄. `var.generator`가 있으면(세션 서명 키) 자동 생성을 먼저 권한다."""
    if var.generator is not None:
        return _prompt_generated_value(var, current)
    from hair_platform import env_setup

    shown = env_setup.mask(current) if var.secret and current else (current or var.default)
    suffix = f" [{shown}]" if shown else " [비어있음, Enter=건너뜀]"
    answer = input(f"  {var.desc}{suffix}: ").strip()
    if not answer:
        return current
    if var.secret and answer in ("-", "지움"):
        return ""
    return answer


def _prompt_generated_value(var, current: str) -> str:
    if current:
        answer = input(f"  {var.desc} — 이미 설정되어 있습니다. 새로 만들까요? [y/N]: ").strip().lower()
        return var.generator() if answer == "y" else current
    answer = input(f"  {var.desc} — 지금 자동으로 생성할까요? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return var.generator()
    return input("  직접 입력(비우면 설정 안 함): ").strip()


def _run_init_wizard(values: dict) -> None:
    from hair_platform import env_setup

    print("HAIR-Platform .env 설정을 시작합니다. Enter만 누르면 지금 값을 그대로 둡니다.\n")
    for group, vars_ in env_setup.by_group().items():
        if group == "메일(SMTP) 알림":
            configured = any((values.get(v.key) or "").strip() for v in vars_)
            verb = "바꾸시겠습니까" if configured else "하시겠습니까"
            choices = "Y/n" if configured else "y/N"
            answer = input(
                f"[{group}] 결재·완료 등 알림을 메일로도 받으려면 설정하세요.\n"
                f"  설정을 {verb}? [{choices}]: "
            ).strip().lower()
            want = answer != "n" if configured else answer == "y"
            if not want:
                continue
        else:
            print(f"[{group}]")
        for var in vars_:
            values[var.key] = _prompt_env_value(var, values.get(var.key, ""))
        print()


def _cmd_init(args: argparse.Namespace) -> int:
    from hair_platform import env_setup

    path = Path(args.path)
    values = env_setup.parse_env_file(path)
    extra = env_setup.leftover_assignments(path)

    if args.yes:
        if not values.get("HAIR_PLATFORM_SECRET_KEY"):
            values["HAIR_PLATFORM_SECRET_KEY"] = env_setup.generate_secret_key()
    else:
        _run_init_wizard(values)

    path.write_text(env_setup.render_env_file(values, extra), encoding="utf-8")

    set_keys = [v.key for v in env_setup.ENV_VARS if (values.get(v.key) or "").strip()]
    print(f"{path}에 저장했습니다.")
    if set_keys:
        print(f"  설정됨({len(set_keys)}개): {', '.join(set_keys)}")
    else:
        print("  설정된 값 없음 — 전부 선택(optional)이니 비워 둬도 서버는 뜹니다.")
    print("다음: hair check   (설정 점검)")
    print("      hair serve   (서버 실행)")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from hair_platform import env_setup

    path = Path(args.path)
    values = env_setup.effective_values(path)

    exists_note = "" if path.exists() else " (파일 없음 — 프로세스 환경변수만 확인)"
    print(f"환경변수 점검 대상: {path}{exists_note}\n")
    for group, vars_ in env_setup.by_group().items():
        print(f"[{group}]")
        for var in vars_:
            value = (values.get(var.key) or "").strip()
            if value:
                shown = env_setup.mask(value) if var.secret else value
                print(f"  ✓ {var.key} = {shown}")
            else:
                print(f"  · {var.key} (설정 안 됨)")
        print()

    problems = env_setup.cross_check(values)
    if problems:
        print("[점검 결과]")
        for level, message in problems:
            icon = "✗" if level == "error" else "⚠"
            print(f"  {icon} {message}")
    else:
        print("[점검 결과] 특별한 문제가 없습니다.")
    print()
    print("설정 안 된 값은 전부 선택(optional)입니다 — `hair init`으로 채울 수 있습니다.")
    return 1 if any(level == "error" for level, _ in problems) else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # 서브커맨드 없이 `hair`만 쳤다 — 에러로 취급하지 않고(위 _BRIEF_HELP
        # 주석 참고) 짧은 안내만 보여주고 성공 종료한다.
        print(_BRIEF_HELP, end="")
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
