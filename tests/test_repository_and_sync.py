from datetime import UTC, date, datetime

import pytest

from stack_overflow_analyzer.adapters.database import SQLiteAnalyticsRepository
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import UpstreamResponseError
from stack_overflow_analyzer.domain.models import DateRange, StackPage
from tests.fakes import FakeRepository, FakeStackExchange


def question(question_id=10, creation_date=1735689600):
    return {
        "question_id": question_id,
        "creation_date": creation_date,
        "title": "How?",
        "tags": ["python", "pandas"],
        "link": f"https://stackoverflow.com/q/{question_id}",
        "owner": {"user_id": 9, "display_name": "Questioner"},
    }


def answer(answer_id=20, score=4):
    return {
        "answer_id": answer_id,
        "question_id": 10,
        "creation_date": 1735689700,
        "score": score,
        "is_accepted": True,
        "link": f"https://stackoverflow.com/a/{answer_id}",
        "owner": {"user_id": 1, "display_name": "Ada", "link": "https://so/u/1"},
    }


@pytest.fixture
async def repository(tmp_path):
    path = (tmp_path / "test.db").as_posix()
    repo = SQLiteAnalyticsRepository(f"sqlite+aiosqlite:///{path}")
    await repo.initialize()
    yield repo
    await repo.close()


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_persists_related_tags(repository):
    gateway = FakeStackExchange()
    gateway.question_pages = {1: StackPage(items=[question()], has_more=False, quota_remaining=100)}
    gateway.answers = [answer()]
    service = SyncService(gateway, repository)
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))

    first = await service.sync("python", period)
    second = await service.sync("python", period)
    rows = await repository.contributor_rows("python", period)
    topics = await repository.related_tags("python", period, 1)

    assert first.answers_upserted == 1
    assert second.resumed is True
    assert gateway.requested_pages == [1]
    assert len(rows) == 1
    assert rows[0].total_answer_score == 4
    assert topics == [("pandas", 1)]


@pytest.mark.asyncio
async def test_interrupted_sync_resumes_at_uncommitted_page(repository):
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))
    first_gateway = FakeStackExchange()
    first_gateway.question_pages = {
        1: StackPage(items=[question()], has_more=True, quota_remaining=100),
        2: UpstreamResponseError("boom"),
    }
    first_gateway.answers = [answer()]

    with pytest.raises(UpstreamResponseError):
        await SyncService(first_gateway, repository).sync("python", period)

    resumed_gateway = FakeStackExchange()
    resumed_gateway.question_pages = {2: StackPage(items=[], has_more=False, quota_remaining=98)}
    result = await SyncService(resumed_gateway, repository).sync("python", period)
    rows = await repository.contributor_rows("python", period)

    assert first_gateway.requested_pages == [1, 2]
    assert resumed_gateway.requested_pages == [2]
    assert result.resumed is True
    assert result.pages_completed == 2
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_malformed_entity_does_not_advance_checkpoint(repository):
    gateway = FakeStackExchange()
    gateway.question_pages = {
        1: StackPage(items=[{"question_id": 1}], has_more=False, quota_remaining=10)
    }
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))

    with pytest.raises(UpstreamResponseError, match="malformed question"):
        await SyncService(gateway, repository).sync("python", period)
    checkpoint, resumed = await repository.get_or_create_checkpoint("python", period)

    assert resumed is True
    assert checkpoint.next_page == 1
    assert checkpoint.completed is False


@pytest.mark.asyncio
async def test_anonymous_page_limit_rolls_forward_time_cursor():
    period = DateRange(start_date=date(2020, 1, 1), end_date=date(2025, 1, 7))
    repository = FakeRepository()
    checkpoint, _ = await repository.get_or_create_checkpoint("tensorflow", period)
    checkpoint.next_page = 25
    rollover_timestamp = 1609459200
    gateway = FakeStackExchange()
    gateway.question_pages = {
        25: StackPage(
            items=[question(creation_date=rollover_timestamp)],
            has_more=True,
            quota_remaining=100,
        ),
        1: StackPage(items=[], has_more=False, quota_remaining=99),
    }

    result = await SyncService(gateway, repository).sync("tensorflow", period)

    assert result.status.value == "completed"
    assert gateway.requested_pages == [25, 1]
    assert gateway.requested_from_dates == [
        period.start_at,
        datetime.fromtimestamp(rollover_timestamp, tz=UTC),
    ]
