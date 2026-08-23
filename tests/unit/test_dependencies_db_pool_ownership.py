from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

import app.dependencies as dependencies

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_proxy_repository_context_uses_foreground_request_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_session = object()
    lifecycle: list[str] = []

    @asynccontextmanager
    async def request_scope() -> AsyncIterator[object]:
        lifecycle.append("entered")
        try:
            yield request_session
        finally:
            lifecycle.append("exited")

    monkeypatch.setattr(dependencies, "get_request_session", request_scope)

    async with dependencies._proxy_repo_context() as repositories:
        assert repositories.accounts.session is request_session
        assert lifecycle == ["entered"]

    assert lifecycle == ["entered", "exited"]


def test_application_proxy_service_injects_per_operation_refresh_repository() -> None:
    app = FastAPI()

    service = dependencies.get_proxy_service_for_app(app)

    assert service._repo_factory is dependencies._proxy_repo_context
    assert service._refresh_repo_factory is dependencies._accounts_refresh_repo_context
    assert dependencies.get_proxy_service_for_app(app) is service
