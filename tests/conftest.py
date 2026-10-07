"""Cấu hình chung cho test: mặc định tắt Postgres để fallback DB không gọi DB thật."""

import pytest

from src.config.settings import settings
from src.services.travel_db import travel_db


@pytest.fixture(autouse=True)
def _tat_db(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "")
    travel_db._cache.clear()
    yield
    travel_db._cache.clear()
