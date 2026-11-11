"""통합 CLI — `hair` 하나로 서버 실행·최초 ADMIN 계정 생성·Gate 측정을 다 한다.

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
import re
import sys

from dotenv import load_dotenv

# 사용자 보고(2026-11-12, "아무 동작없이도 메시지가 출력되는 원인을
# 분석하고, 오류 등 꼭 필요한 메시지만 노출되도록 개선해줘") — 원인은
# `webui.py`의 멘션 알림 폴링(`startMentionNotificationPolling()`,
# 로그인하면 바로 시작해 20초마다 `/api/conversations/my-mentions`를
# 호출 — 사람이 뭘 하든 안 하든 계속 돈다)과 대화 메시지 폴링(그
# 대화를 보는 동안 6초마다 `/api/conversations/{id}/messages`)이다.
# 둘 다 "새 멘션/메시지가 왔는지 조용히 확인"하는 배경 작업이라
# 설계상 매번 성공(2xx)해야 정상이고, 실패(4xx/5xx)할 때만 사람이
# 알아야 할 신호다 — 그래서 성공한 호출만 uvicorn 접속 로그에서
# 걸러낸다(에러·그 외 모든 요청 로그는 그대로 남긴다).
_QUIET_POLLING_GET_PATTERNS = (
    re.compile(r"^/api/conversations/my-mentions(\?.*)?$"),
    re.compile(r"^/api/conversations/\d+/messages(\?.*)?$"),
)


class _QuietPollingAccessFilter(logging.Filter):
    """uvicorn access 로그 레코드의 `args`는 `(client_addr, method,
    path, http_version, status)` 순서다(uvicorn의 h11_impl.py
    `access_logger.info("%s - \"%s %s HTTP/%s\" %d", ...)` 호출과
    동일한 순서로 직접 확인함) — 그 외 형태(uvicorn.error 로거 등)는
    그대로 통과시킨다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method, path, status = args[1], args[2], args[4]
        if method != "GET" or not isinstance(status, int) or status >= 400:
            return True
        return not any(p.match(str(path)) for p in _QUIET_POLLING_GET_PATTERNS)


def _build_log_config() -> dict:
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {
        "quiet_polling": {"()": f"{__name__}._QuietPollingAccessFilter"},
    }
    config["handlers"]["access"]["filters"] = ["quiet_polling"]
    return config

_EPILOG_TOP = """\
예시:
  hair serve                              서버 실행 + 브라우저 자동 열기(http://127.0.0.1:8000, hair.db)
  hair serve --port 9000 --reload         개발 중 코드 변경 자동 반영
  hair serve --no-browser                 브라우저 자동 열기 끄기(서버만 실행)
  hair bootstrap-admin -u admin -p ***    최초 관리자 계정 생성(최초 1회만)
  hair reset-data                         운영 데이터 전부 초기화(Agent/Tool/Skill/파이프라인은 유지, 팀은 초기화)
  hair eval                               Gate A/B/D/E/G 측정 1회 실행

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
  serve             Web UI + API 서버 실행
  bootstrap-admin   최초 ADMIN 계정 생성(콜드스타트 전용)
  reset-data        운영 데이터 초기화(Agent/Tool/Skill/파이프라인은 유지, 팀은 초기화)
  eval              Gate A/B/D/E/G 측정 1회 실행

자세한 도움말:       hair --help
명령어별 도움말:     hair <command> --help
버전 확인:           hair --version
"""


def _get_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("hair-platform")
    except PackageNotFoundError:
        return "0.0.0"


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
            "  export HAIR_PLATFORM_SECRET_KEY=\"$(python3 -c "
            "'import secrets; print(secrets.token_hex(32))')\"\n"
            "  hair serve --host 0.0.0.0 --no-browser   다른 PC에서 "
            "http://<이 PC의 사내망 IP>:8000/\n"
            "  (키를 안 주면 기동 시 경고가 뜬다 — 소스에 공개된 고정 "
            "키로 세션이 서명돼 같은 망의\n"
            "   누구나 다른 사용자 세션을 위조할 수 있다. --reload는 "
            "공용 인스턴스에 쓰지 않는다 —\n"
            "   파일 변경마다 재시작되며 접속이 전부 끊긴다.)\n"
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
        help="운영 데이터 전부 초기화(Agent/Tool/Skill/파이프라인 템플릿은 유지, 팀은 초기화)",
        description=(
            "사용자 계정·사람 인력(human_pool)·업무 요청/할 일/배정/결재/\n"
            "결과물(+댓글)·대화·업로드 문서·VectorDB 지식 조각·AI 에이전트\n"
            "생성 Job·Crew/파이프라인 실행 이력·Gate 평가 기록·모델\n"
            "레지스트리·임베딩 설정·Tool-Agent 연결·팀/팀원·활동 기록\n"
            "(audit_log)을 전부 지운다. Agent(26종)/Tool·Skill Pool/\n"
            "파이프라인 템플릿 자체는 삭제하지 않는다 — hair.db 재기동 시\n"
            "항상 자동으로 다시 채워지는 레퍼런스 데이터이기 때문이다(단,\n"
            "Agent가 실행되며 쌓인 비용·성과점수·LLM provider/model 설정·\n"
            "마지막 산출물은 초기값으로 되돌아간다). 팀은 그렇게 재시드되지\n"
            "않아(사용자가 직접 만든 데이터) 함께 지운다 — 지워도 다시\n"
            "자동으로 생기지 않는다. 되돌릴 수 없다."
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
        help="Gate A/B/D/E/G 측정 1회 실행(results/에 저장, 비용 없음)",
        description=(
            "agent-evaluator SDK로 결정론적 스텁 생성 경로를 측정한다.\n"
            "results/agent_generation_eval.json에 저장(LLM 미호출, 비용 없음)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  hair eval\n"
            "  agent-eval gate results/*.json --tcr 85   측정 결과를 채점(agent-evaluator SDK)\n"
        ),
    )
    p_eval.set_defaults(func=_cmd_eval)

    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    # uvicorn의 `--reload`는 재시작마다 팩토리 함수를 인자 없이 다시
    # 호출한다 — 그 경로로 `db_path`를 전달할 수단이 환경변수 말고는
    # 없다(`create_app()`이 `HAIR_PLATFORM_DB_PATH`를 보는 이유).
    os.environ["HAIR_PLATFORM_DB_PATH"] = args.db_path

    # 사용자 요청(2026-11-03, "사내망을 통해 다른 사용자도 접속하도록
    # 개선 방안을 제안해줘" → "1번, 2번 진행") — `--host 0.0.0.0` 등
    # 루프백이 아닌 주소로 띄우면서 `HAIR_PLATFORM_SECRET_KEY`를 안 주면
    # `app.py`의 세션 서명 키가 소스에 공개된 고정값("dev-secret-
    # change-in-production")으로 폴백한다 — 같은 네트워크의 누구나 그
    # 값으로 다른 사용자(admin 포함)의 로그인 세션을 위조할 수 있다.
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
            "  세션 서명 키가 소스코드에 공개된 고정값으로 동작합니다 — 같은\n"
            "  네트워크의 누구나 다른 사용자의 로그인 세션을 위조할 수 있습니다.\n"
            "  다음처럼 랜덤 키를 지정한 뒤 다시 실행하세요:\n"
            "    export HAIR_PLATFORM_SECRET_KEY=\"$(python3 -c "
            "'import secrets; print(secrets.token_hex(32))')\"\n",
            file=sys.stderr,
        )

    import uvicorn

    if not args.no_browser:
        import threading
        import webbrowser

        # 0.0.0.0/:: 은 "모든 인터페이스에 바인딩"이라는 뜻이라 브라우저
        # 주소로는 못 쓴다 — 로컬 접속 주소인 127.0.0.1로 대신 연다.
        browser_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        url = f"http://{browser_host}:{args.port}/"
        # uvicorn.run()은 블로킹이라 "서버가 뜬 시점" 콜백을 못 받는다 —
        # 기동 시간을 감안한 고정 지연 후에 연다(--reload는 부모 프로세스
        # 기준 1회만 실행되므로 재시작마다 다시 열리지 않는다).
        threading.Timer(1.5, webbrowser.open, args=[url]).start()

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
            f"'{args.db_path}'의 운영 데이터(사용자·업무·대화·실행 이력·팀 등)를 "
            "전부 지웁니다 — 되돌릴 수 없습니다.\n"
            "Agent/Tool/Skill/파이프라인 템플릿은 그대로 유지됩니다(팀은 초기화됩니다). "
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
        f"초기화 완료 — {total}건 삭제. Agent/Tool/Skill/파이프라인 템플릿은 그대로입니다(팀은 초기화됨)."
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
