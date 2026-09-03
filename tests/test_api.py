from fastapi.testclient import TestClient

from stack_overflow_analyzer.api.app import create_app
from stack_overflow_analyzer.config import Settings
from stack_overflow_analyzer.domain.exceptions import NarrativeUnavailableError
from stack_overflow_analyzer.domain.models import Confidence, NarrativeOutput, Owner
from stack_overflow_analyzer.ports.repository import StoredContributorRow
from tests.fakes import FakeNarrativeGenerator, FakeRepository, FakeStackExchange


def app_client(repository=None, stack_exchange=None, narrative_generator=None):
    narrative = NarrativeOutput(
        notable_contribution="None",
        ranking_explanation="None",
        peer_comparison="None",
        period_change="None",
        confidence=Confidence.LOW,
        evidence_ids=["period.benchmark_rank"],
    )
    app = create_app(
        Settings(database_url="sqlite+aiosqlite:///:memory:"),
        repository=repository or FakeRepository(),
        stack_exchange=stack_exchange or FakeStackExchange(),
        narrative_generator=narrative_generator or FakeNarrativeGenerator(narrative),
    )
    return TestClient(app)


def test_health_and_request_id():
    with app_client() as client:
        response = client.get("/health", headers={"x-request-id": "review-123"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"] == "review-123"


def test_unsafe_request_id_is_replaced():
    with app_client() as client:
        response = client.get("/health", headers={"x-request-id": "not allowed/value"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "not allowed/value"
    assert len(response.headers["x-request-id"]) == 36


def test_root_serves_self_contained_reviewer_ui():
    with app_client() as client:
        response = client.get("/")
        stylesheet = client.get("/ui-assets/styles.css")
        script = client.get("/ui-assets/app.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Stack Overflow profile URL or user ID" in response.text
    assert 'value="https://stackoverflow.com/users/10908375/nicolas-gervais"' in response.text
    assert 'name="tag" type="text" value="keras"' in response.text
    assert 'id="include-narrative"' in response.text
    assert 'id="narrative-confidence"' in response.text
    assert "checked" in response.text
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "fetch(url, options)" in script.text
    assert "narrative.confidence" in script.text
    assert "narrative.root_cause_hypothesis" in script.text
    assert "narrative.topic_fingerprint" not in script.text
    assert "Topic fingerprint" not in script.text
    assert "What may explain the result" in script.text
    assert "Root-cause hypothesis" not in script.text
    assert "if (narrative.root_cause_hypothesis)" in script.text
    assert 'monthInput.value = "2020-08"' in script.text
    assert "benchmarkRank(value)" in script.text
    assert "No qualifying ${analysis.tag} answers were found" in script.text


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


def test_user_id_larger_than_storage_type_is_rejected():
    with app_client() as client:
        response = client.get(
            "/v1/tags/python/contributors/9223372036854775808",
            params={"from_date": "2025-01-01", "to_date": "2025-01-02"},
        )

    assert response.status_code == 422


def test_first_of_month_half_open_period_is_allowed():
    with app_client() as client:
        response = client.get(
            "/v1/tags/tensorflow/contributors",
            params={"from_date": "2025-01-01", "to_date": "2025-02-01"},
        )

    assert response.status_code == 200


def test_period_longer_than_31_days_is_rejected_before_sync():
    with app_client() as client:
        response = client.get(
            "/v1/tags/tensorflow/contributors",
            params={"from_date": "2025-01-01", "to_date": "2025-02-02"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "half-open date range cannot exceed 31 days; "
            "choose an end date no more than one month after the start date"
        )
    }


def test_contributor_detail_uses_the_same_31_day_limit():
    with app_client() as client:
        response = client.get(
            "/v1/tags/tensorflow/contributors/1",
            params={"from_date": "2024-01-01", "to_date": "2025-01-01"},
        )

    assert response.status_code == 422


def test_all_time_endpoint_uses_official_gateway_feature():
    with app_client() as client:
        response = client.get("/v1/tags/python/top-answerers/all-time")

    assert response.status_code == 200
    assert response.json()["contributors"][0]["display_name"] == "Ada"


def test_contributor_detail_returns_benchmark_metrics_and_cohort_context():
    contributor = StoredContributorRow(
        user_id=1,
        display_name="Ada",
        profile_url="https://stackoverflow.com/users/1",
        answer_count=2,
        total_answer_score=10,
        accepted_answer_count=1,
    )
    repository = FakeRepository(lambda _: [contributor])

    with app_client(repository) as client:
        response = client.get(
            "/v1/tags/tensorflow/contributors/1",
            params={"from_date": "2025-01-01", "to_date": "2025-02-01"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metric"]["name"] == "all_time_top20_period_benchmark"
    assert payload["contributor"]["period_benchmark_rank"] == 1
    assert payload["contributor"]["official_all_time_rank"] == 1
    assert payload["cohort"]["subject_added_to_cohort"] is False
    assert payload["period"]["end_date"] == "2025-02-01"


def test_valid_contributor_without_period_answers_returns_zero_result():
    gateway = FakeStackExchange()
    gateway.users[99] = Owner(
        user_id=99,
        display_name="Grace",
        link="https://stackoverflow.com/users/99/grace",
    )

    with app_client(stack_exchange=gateway) as client:
        response = client.get(
            "/v1/tags/tensorflow/contributors/99",
            params={"from_date": "2025-01-01", "to_date": "2025-02-01"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contributor"] == {
        "user_id": 99,
        "display_name": "Grace",
        "profile_url": "https://stackoverflow.com/users/99/grace",
        "period_benchmark_rank": None,
        "official_all_time_rank": None,
        "is_official_all_time_top_20": False,
        "has_qualifying_answers": False,
        "answer_count": 0,
        "total_answer_score": 0,
        "accepted_answer_count": 0,
        "acceptance_rate": 0.0,
        "average_answer_score": 0.0,
    }
    assert payload["previous_period"]["period_benchmark_rank_change"] is None
    assert "related_tags" not in payload
    assert all(not item["id"].startswith("topics.") for item in payload["evidence"])
    assert gateway.requested_users == [99]


def test_narrative_endpoint_returns_structured_grounded_output():
    contributor = StoredContributorRow(
        user_id=1,
        display_name="Ada",
        profile_url=None,
        answer_count=2,
        total_answer_score=10,
        accepted_answer_count=1,
    )
    repository = FakeRepository(lambda _: [contributor])

    with app_client(repository) as client:
        response = client.post(
            "/v1/tags/python/contributors/1/narrative",
            json={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )

    assert response.status_code == 200
    assert response.json()["narrative"]["confidence"] == "low"
    assert response.json()["narrative"]["root_cause_hypothesis"] is None
    assert response.json()["narrative"]["evidence_ids"] == ["period.benchmark_rank"]


def test_narrative_provider_failure_returns_controlled_502():
    class FailingNarrative(FakeNarrativeGenerator):
        async def generate(self, analysis):
            raise NarrativeUnavailableError("narrative provider unavailable")

    fallback = NarrativeOutput(
        notable_contribution="None",
        ranking_explanation="None",
        peer_comparison="None",
        period_change="None",
        confidence=Confidence.LOW,
        evidence_ids=["period.benchmark_rank"],
    )

    with app_client(narrative_generator=FailingNarrative(fallback)) as client:
        response = client.post(
            "/v1/tags/python/contributors/1/narrative",
            json={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "narrative provider unavailable"}
