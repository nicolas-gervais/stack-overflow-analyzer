from fastapi.testclient import TestClient

from stack_overflow_analyzer.api.app import create_app
from stack_overflow_analyzer.config import Settings
from stack_overflow_analyzer.domain.models import Confidence, NarrativeOutput
from tests.fakes import FakeNarrativeGenerator, FakeRepository, FakeStackExchange


def app_client():
    narrative = NarrativeOutput(
        notable_contribution="None",
        ranking_explanation="None",
        peer_comparison="None",
        period_change="None",
        topic_fingerprint="None",
        root_cause_hypothesis=None,
        confidence=Confidence.LOW,
        evidence_ids=["period.rank"],
    )
    app = create_app(
        Settings(database_url="sqlite+aiosqlite:///:memory:"),
        repository=FakeRepository(),
        stack_exchange=FakeStackExchange(),
        narrative_generator=FakeNarrativeGenerator(narrative),
    )
    return TestClient(app)


def test_health_and_request_id():
    with app_client() as client:
        response = client.get("/health", headers={"x-request-id": "review-123"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"] == "review-123"


def test_invalid_tag_and_date_range_return_422():
    with app_client() as client:
        bad_tag = client.get(
            "/v1/tags/not%20valid/contributors",
            params={"from_date": "2025-01-01", "to_date": "2025-01-02"},
        )
        bad_dates = client.get(
            "/v1/tags/python/contributors",
            params={"from_date": "2025-01-02", "to_date": "2025-01-01"},
        )

    assert bad_tag.status_code == 422
    assert bad_dates.status_code == 422


def test_all_time_endpoint_uses_official_gateway_feature():
    with app_client() as client:
        response = client.get("/v1/tags/python/top-answerers/all-time")

    assert response.status_code == 200
    assert response.json()["contributors"][0]["display_name"] == "Ada"
