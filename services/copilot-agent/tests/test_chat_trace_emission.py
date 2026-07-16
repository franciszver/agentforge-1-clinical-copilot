"""Hermetic test: a ``POST /chat`` invocation writes request + verification
spans to the trace store (P4.2 live-wiring scope).

Everything is faked (planner, extractor, token validator) exactly as in
``test_chat_endpoint.py``; the only addition is a ``TraceStore`` pointed at a
``tmp_path`` database via the ``get_trace_store`` dependency override -- this
test never touches the configured ``trace_db_path``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.chat import get_claim_extractor, get_planner_factory, get_token_validator, get_trace_store
from app.main import app
from app.planner import PlannerResult, ToolCallTrace
from app.schemas.planner import ToolName
from app.trace_store import SpanType, TraceStore
from tests.test_chat_endpoint import FakeExtractor, FakePlanner


@pytest.fixture(autouse=True)
def _reset_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override_ok_validator() -> None:
    def _validator(token: str) -> None:
        return None

    app.dependency_overrides[get_token_validator] = lambda: _validator


client = TestClient(app)


def test_chat_invocation_writes_request_and_verification_spans(tmp_path: Path) -> None:
    trace_store = TraceStore(db_path=str(tmp_path / "traces.db"))
    trace = [ToolCallTrace(tool=ToolName.GET_MEDICATIONS, args={}, result={"count": 1}, error=None)]
    fake_planner = FakePlanner(trace=trace, answer="She is on lisinopril.")

    _override_ok_validator()
    app.dependency_overrides[get_planner_factory] = lambda: (lambda patient_id: fake_planner)
    app.dependency_overrides[get_claim_extractor] = lambda: FakeExtractor()
    app.dependency_overrides[get_trace_store] = lambda: trace_store

    response = client.post(
        "/chat",
        json={"message": "What meds is she on?", "patient_id": 1},
        headers={"Authorization": "Bearer good-token"},
    )
    assert response.status_code == 200
    correlation_id = response.headers["X-Correlation-ID"]

    spans = trace_store.get_spans(correlation_id)
    span_types = {span.span_type for span in spans}

    assert SpanType.REQUEST in span_types
    assert SpanType.VERIFICATION in span_types

    request_span = next(span for span in spans if span.span_type == SpanType.REQUEST)
    assert request_span.correlation_id == correlation_id
    assert request_span.duration_ms >= 0

    verification_span = next(span for span in spans if span.span_type == SpanType.VERIFICATION)
    assert verification_span.verdict is not None
