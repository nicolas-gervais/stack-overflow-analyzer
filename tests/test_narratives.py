import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError

from stack_overflow_analyzer.adapters.openai_narrative import OpenAINarrativeGenerator
from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.application.narratives import NarrativeService
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import (
    InvalidEvidenceError,
    NarrativeRateLimitError,
    NarrativeUnavailableError,
)
from stack_overflow_analyzer.domain.models import Confidence, DateRange, NarrativeOutput
from stack_overflow_analyzer.ports.repository import StoredContributorRow
from tests.fakes import FakeNarrativeGenerator, FakeRepository, FakeStackExchange


def output(evidence_ids, *, confidence=Confidence.MEDIUM, hypothesis=None):
    return NarrativeOutput(
        notable_contribution="Strong score.",
        ranking_explanation="Ranks first by score.",
        peer_comparison="Above mean.",
        period_change="Improved.",
        topic_fingerprint="Focused on pandas.",
        root_cause_hypothesis=hypothesis,
        confidence=confidence,
        evidence_ids=evidence_ids,
    )


def analytics():
    row = StoredContributorRow(
        user_id=1,
        display_name="Ada",
        profile_url=None,
        answer_count=2,
        total_answer_score=10,
        accepted_answer_count=1,
    )
    repo = FakeRepository(lambda _: [row])
    return AnalyticsService(repo, SyncService(FakeStackExchange(), repo))


@pytest.mark.asyncio
async def test_unknown_llm_evidence_id_is_rejected():
    service = NarrativeService(analytics(), FakeNarrativeGenerator(output(["invented.fact"])))

    with pytest.raises(InvalidEvidenceError, match=r"invented\.fact"):
        await service.create(
            "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 1
        )


@pytest.mark.asyncio
async def test_known_llm_evidence_ids_are_accepted():
    service = NarrativeService(analytics(), FakeNarrativeGenerator(output(["period.total_score"])))

    result = await service.create(
        "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 1
    )

    assert result.narrative.confidence is Confidence.MEDIUM
    assert result.analysis.contributor.period_benchmark_rank == 1
    assert (
        result.analysis.peer_comparison.peer_group
        == "active_official_all_time_top_20_excluding_subject"
    )


@pytest.mark.asyncio
async def test_sparse_evidence_caps_model_confidence():
    service = NarrativeService(
        analytics(),
        FakeNarrativeGenerator(output(["period.total_score"], confidence=Confidence.HIGH)),
    )

    result = await service.create(
        "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 1
    )

    assert result.narrative.confidence is Confidence.MEDIUM


@pytest.mark.asyncio
async def test_unsupported_root_cause_hypothesis_is_rejected():
    repository = FakeRepository()
    service = NarrativeService(
        AnalyticsService(repository, SyncService(FakeStackExchange(), repository)),
        FakeNarrativeGenerator(
            output(
                ["period.answer_count"],
                hypothesis="This may reflect a shift in priorities.",
            )
        ),
    )

    with pytest.raises(InvalidEvidenceError, match="without sufficient comparison evidence"):
        await service.create(
            "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 1
        )


@pytest.mark.asyncio
async def test_hypothesis_requires_contextual_evidence_id():
    service = NarrativeService(
        analytics(),
        FakeNarrativeGenerator(
            output(
                ["period.answer_count"],
                hypothesis="The higher score may reflect greater answer volume.",
            )
        ),
    )

    with pytest.raises(InvalidEvidenceError, match="without sufficient comparison evidence"):
        await service.create(
            "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 1
        )


class FakeResponses:
    def __init__(self, parsed, error=None):
        self.parsed = parsed
        self.error = error
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)


class FakeOpenAI:
    def __init__(self, parsed, error=None):
        self.responses = FakeResponses(parsed, error)


@pytest.mark.asyncio
async def test_openai_adapter_uses_responses_parse_and_pydantic_format():
    client = FakeOpenAI(output(["period.benchmark_rank"]))
    adapter = OpenAINarrativeGenerator(
        api_key="test", model="test-model", timeout_seconds=1, client=client
    )
    service = analytics()
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    analysis = await service.analyze("python", period, 1)

    result = await adapter.generate(analysis)

    assert result.evidence_ids == ["period.benchmark_rank"]
    assert client.responses.kwargs["model"] == "test-model"
    assert client.responses.kwargs["text_format"] is NarrativeOutput
    assert client.responses.kwargs["store"] is False
    assert (
        "what the contributor did and why it is meaningful"
        in client.responses.kwargs["instructions"]
    )
    assert (
        "do not produce a metric-by-metric recital"
        in client.responses.kwargs["instructions"].lower()
    )
    assert "never print evidence IDs" in client.responses.kwargs["instructions"]
    assert "Never begin with a categorical label" in client.responses.kwargs["instructions"]
    assert '"Very active"' in client.responses.kwargs["instructions"]
    context = json.loads(client.responses.kwargs["input"])
    assert "contributors" not in context
    assert "display_name" not in context["contributor"]
    assert "profile_url" not in context["contributor"]
    assert "Ada" not in client.responses.kwargs["input"]
    assert "root_cause_hypothesis" in NarrativeOutput.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_openai_adapter_rejects_missing_structured_output():
    adapter = OpenAINarrativeGenerator(
        api_key="test", model="test-model", timeout_seconds=1, client=FakeOpenAI(None)
    )
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    analysis = await analytics().analyze("python", period, 1)

    with pytest.raises(NarrativeUnavailableError, match="no structured narrative"):
        await adapter.generate(analysis)


@pytest.mark.asyncio
async def test_openai_adapter_can_be_disabled_without_key():
    adapter = OpenAINarrativeGenerator(api_key=None, model="test-model", timeout_seconds=1)
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    analysis = await analytics().analyze("python", period, 1)

    with pytest.raises(NarrativeUnavailableError, match="API_KEY"):
        await adapter.generate(analysis)


@pytest.mark.asyncio
async def test_openai_adapter_reports_quota_exhaustion():
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    error = RateLimitError(
        "quota exhausted",
        response=response,
        body={"code": "insufficient_quota"},
    )
    adapter = OpenAINarrativeGenerator(
        api_key="test",
        model="test-model",
        timeout_seconds=1,
        client=FakeOpenAI(None, error),
    )
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    analysis = await analytics().analyze("python", period, 1)

    with pytest.raises(NarrativeRateLimitError, match="quota exhausted"):
        await adapter.generate(analysis)
