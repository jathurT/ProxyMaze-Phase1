"""FastAPI dependency providers."""

from __future__ import annotations

from app.core.state import AppState, get_state


def state_dependency() -> AppState:
    return get_state()
