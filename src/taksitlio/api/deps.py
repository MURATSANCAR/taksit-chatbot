from __future__ import annotations

from fastapi import Request

from taksitlio.app.container import AppContainer


def container_from(request: Request) -> AppContainer:
    return request.app.state.container
