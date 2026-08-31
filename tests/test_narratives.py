from datetime import date
from types import SimpleNamespace

import pytest

from stack_overflow_analyzer.adapters.openai_narrative import OpenAINarrativeGenerator
from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.application.narratives import NarrativeService
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import (
    InvalidEvidenceError,
    NarrativeUnavailableError,
)
from stack_overflow_analyzer.domain.models import Confidence, DateRange, NarrativeOutput
from stack_overflow_analyzer.ports.repository import StoredContributorRow
from tests.fakes import FakeNarrativeGenerator, FakeRepository, FakeStackExchange


def output(evidence_ids):
    return NarrativeOutput(
        notable_contribution="Strong score.",
        ranking_explanation="Ranks first by score.",
        peer_comparison="Above median.",
        period_change="Improved.",
        topic_fingerprint="Focused on pandas.",
        root_cause_hypothesis="The topic mix may have helped.",
        confidence=Confidence.MEDIUM,
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
            "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1)), 1
        )


@pytest.mark.asyncio
async def test_known_llm_evidence_ids_are_accepted():
    service = NarrativeService(
        analytics(), FakeNarrativeGenerator(output(["period.rank", "period.total_score"]))
    )

    result = await service.create(
        "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1)), 1
    )

    assert result.narrative.confidence is Confidence.MEDIUM


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class FakeOpenAI:
    def __init__(self, parsed):
        self.responses = FakeResponses(parsed)


@pytest.mark.asyncio
async def test_openai_adapter_uses_responses_parse_and_pydantic_format():
    client = FakeOpenAI(output(["period.rank"]))
    adapter = OpenAINarrativeGenerator(
        api_key="test", model="test-model", timeout_seconds=1, client=client
    )
    service = analytics()
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))
    analysis = await service.analyze("python", period, 1)

    result = await adapter.generate(analysis)

    assert result.evidence_ids == ["period.rank"]
    assert client.responses.kwargs["model"] == "test-model"
    assert client.responses.kwargs["text_format"] is NarrativeOutput
    assert client.responses.kwargs["store"] is False


@pytest.mark.asyncio
async def test_openai_adapter_rejects_missing_structured_output():
    adapter = OpenAINarrativeGenerator(
        api_key="test", model="test-model", timeout_seconds=1, client=FakeOpenAI(None)
    )
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))
    analysis = await analytics().analyze("python", period, 1)

    with pytest.raises(NarrativeUnavailableError, match="no structured narrative"):
        await adapter.generate(analysis)


@pytest.mark.asyncio
async def test_openai_adapter_can_be_disabled_without_key():
    adapter = OpenAINarrativeGenerator(api_key=None, model="test-model", timeout_seconds=1)
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))
    analysis = await analytics().analyze("python", period, 1)

    with pytest.raises(NarrativeUnavailableError, match="API_KEY"):
        await adapter.generate(analysis)
