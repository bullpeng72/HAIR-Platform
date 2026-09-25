"""Phase 0 부트스트랩 확인용 스모크 테스트 — 패키지가 임포트되는지만 검증한다."""

import hair_platform


def test_package_importable():
    assert hair_platform is not None
