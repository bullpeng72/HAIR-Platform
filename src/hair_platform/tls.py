"""외부 서버 TLS 연결 설정(2026-11-26, 개선 계획 2-2 통합 테스트 설계 중 발견한 보안 문제).

파이썬 `imaplib.IMAP4_SSL`·`smtplib.SMTP_SSL`·`SMTP.starttls()`는 TLS 설정(context)을 넘기지 않으면 **서버 인증서를
검증하지 않는** 설정(`ssl._create_stdlib_context`)을 쓴다. 메일 수신함 읽기·메일 알림 발송이 그 기본값을 써서, 같은
네트워크의 공격자가 가짜 메일 서버로 앱 비밀번호와 메일 내용을 가로챌 수 있었다. 이제 인증서와 호스트 이름을 검증한다.

사내 메일 서버가 사내 인증 기관(CA)이나 자체 서명 인증서를 쓰면 그 인증서 파일 경로를 `HAIR_TLS_CA_FILE`에 넣는다
(시스템 기본 신뢰 목록에 더해 신뢰한다). 검증을 끄는 설정은 두지 않는다.
"""

from __future__ import annotations

import os
import ssl


def client_context() -> ssl.SSLContext:
    """인증서·호스트 이름을 검증하는 클라이언트 TLS 설정(+ `HAIR_TLS_CA_FILE`의 사내 인증서)."""
    ctx = ssl.create_default_context()
    ca_file = (os.environ.get("HAIR_TLS_CA_FILE") or "").strip()
    if ca_file:
        ctx.load_verify_locations(cafile=os.path.expanduser(ca_file))
    return ctx


def explain(exc: BaseException) -> str | None:
    """인증서 검증 실패면 사람이 할 일을 알려 주는 문장, 아니면 None."""
    if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return ("서버 인증서를 확인할 수 없습니다 — 사내 인증서(자체 서명·사내 CA)를 쓰는 서버면 그 인증서 파일 경로를 "
                "서버 환경변수 HAIR_TLS_CA_FILE에 지정하세요(hair init → 서버 운영)")
    return None
