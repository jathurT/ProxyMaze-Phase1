from fastapi.routing import APIRoute

from app.api.routes import read_health, read_root
from app.config import Settings
from app.main import create_app


def test_app_registers_public_routes() -> None:
    app = create_app()

    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}

    assert "/" in paths
    assert "/health" in paths


def test_root_returns_app_metadata() -> None:
    response = read_root(Settings())

    assert response.model_dump() == {
        "name": "ProxyMaze API",
        "version": "0.1.0",
        "environment": "development",
    }


def test_health_returns_ok() -> None:
    response = read_health()

    assert response.model_dump() == {"status": "ok"}
