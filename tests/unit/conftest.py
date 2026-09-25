"""v0 테스트 픽스처 — 4개 역할 사용자를 시드한 인메모리 앱 인스턴스."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hair_platform.app import create_app
from hair_platform.auth import Role, create_user


@pytest.fixture
def client_and_conn():
    app = create_app(":memory:")
    conn = app.state.db
    create_user(conn, "admin1", "adminpass", Role.ADMIN)
    create_user(conn, "manager1", "managerpass", Role.MANAGER)
    create_user(conn, "member1", "memberpass", Role.MEMBER)
    create_user(conn, "viewer1", "viewerpass", Role.VIEWER)
    with TestClient(app) as client:
        yield client, conn
