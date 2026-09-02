from datetime import date

import pytest

from stack_overflow_analyzer.adapters.database import SQLiteAnalyticsRepository
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import (
    RequestBudgetExceededError,
    UpstreamResponseError,
)
from stack_overflow_analyzer.domain.models import DateRange, Owner, StackPage
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
async def test_benchmark_sync_batches_users_and_parent_questions(repository):
    gateway = FakeStackExchange()
    gateway.user_answer_pages = {
        1: StackPage(
            items=[answer(), {**answer(answer_id=21), "question_id": 11}],
            has_more=False,
            quota_remaining=100,
        )
    }
    gateway.parent_questions = [
        question(creation_date=1609459200),
        {**question(question_id=11, creation_date=1609459200), "tags": ["java"]},
    ]
    service = SyncService(gateway, repository)
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 2, 1))

    first = await service.sync_benchmark("python", period, [1, 2])
    second = await service.sync_benchmark("python", period, [1, 2])
    rows = await repository.benchmark_contributor_rows("python", period, [1, 2])

    assert first.answers_upserted == 1
    assert second.resumed is True
    assert gateway.requested_user_answer_pages == [1]
    assert gateway.requested_user_ids == [[1, 2]]
    assert gateway.requested_question_ids == [[10, 11]]
    assert len(rows) == 1
    assert rows[0].answer_count == 1
    assert rows[0].total_answer_score == 4


@pytest.mark.asyncio
async def test_benchmark_sync_deduplicates_upstream_objects(repository):
    gateway = FakeStackExchange()
    gateway.user_answer_pages = {
        1: StackPage(items=[answer(), answer()], has_more=False, quota_remaining=100)
    }
    gateway.parent_questions = [question(), question()]
    service = SyncService(gateway, repository)
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 2, 1))

    result = await service.sync_benchmark("python", period, [1])
    rows = await repository.benchmark_contributor_rows("python", period, [1])

    assert result.answers_upserted == 1
    assert result.questions_upserted == 1
    assert rows[0].answer_count == 1


@pytest.mark.asyncio
async def test_repository_query_enforces_half_open_date_boundaries(repository):
    gateway = FakeStackExchange()
    gateway.user_answer_pages = {
        1: StackPage(
            items=[
                {**answer(answer_id=20), "creation_date": 1735689600},
                {**answer(answer_id=21), "creation_date": 1735775999},
                {**answer(answer_id=22), "creation_date": 1735776000},
            ],
            has_more=False,
        )
    }
    gateway.parent_questions = [question()]
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))

    await SyncService(gateway, repository).sync_benchmark("python", period, [1])
    rows = await repository.benchmark_contributor_rows("python", period, [1])

    assert rows[0].answer_count == 2


@pytest.mark.asyncio
async def test_benchmark_sync_stops_at_hard_page_budget():
    repository = FakeRepository()
    gateway = FakeStackExchange()
    gateway.user_answer_pages = {
        1: StackPage(items=[answer()], has_more=True, quota_remaining=100),
    }
    gateway.parent_questions = [question()]
    service = SyncService(gateway, repository, max_answer_pages_per_period=1)
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))

    with pytest.raises(RequestBudgetExceededError, match="shorter date range"):
        await service.sync_benchmark("python", period, [1])

    assert gateway.requested_user_answer_pages == [1]


@pytest.mark.asyncio
async def test_interrupted_benchmark_sync_resumes_at_uncommitted_page(repository):
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    first_gateway = FakeStackExchange()
    first_gateway.user_answer_pages = {
        1: StackPage(items=[answer()], has_more=True, quota_remaining=100),
        2: UpstreamResponseError("boom"),
    }
    first_gateway.parent_questions = [question()]

    with pytest.raises(UpstreamResponseError):
        await SyncService(first_gateway, repository).sync_benchmark("python", period, [1, 2])

    resumed_gateway = FakeStackExchange()
    resumed_gateway.user_answer_pages = {2: StackPage(items=[], has_more=False, quota_remaining=98)}
    result = await SyncService(resumed_gateway, repository).sync_benchmark("python", period, [1, 2])

    assert first_gateway.requested_user_answer_pages == [1, 2]
    assert resumed_gateway.requested_user_answer_pages == [2]
    assert result.resumed is True
    assert result.pages_completed == 2


@pytest.mark.asyncio
async def test_malformed_parent_question_does_not_advance_benchmark_checkpoint(repository):
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    malformed_gateway = FakeStackExchange()
    malformed_gateway.user_answer_pages = {
        1: StackPage(items=[answer()], has_more=False, quota_remaining=100)
    }
    malformed_gateway.parent_questions = [{"question_id": 10}]

    with pytest.raises(UpstreamResponseError, match="malformed question"):
        await SyncService(malformed_gateway, repository).sync_benchmark("python", period, [1])

    fixed_gateway = FakeStackExchange()
    fixed_gateway.user_answer_pages = {1: StackPage(items=[], has_more=False, quota_remaining=99)}
    result = await SyncService(fixed_gateway, repository).sync_benchmark("python", period, [1])

    assert fixed_gateway.requested_user_answer_pages == [1]
    assert result.resumed is True


@pytest.mark.asyncio
async def test_all_time_cohort_is_persisted_and_reused(repository):
    gateway = FakeStackExchange()
    service = SyncService(gateway, repository)

    first = await service.get_all_time_cohort("python")
    second = await service.get_all_time_cohort("python")

    assert first.contributors[0].display_name == "Ada"
    assert second.retrieved_at == first.retrieved_at
    assert gateway.all_time_requests == 1


@pytest.mark.asyncio
async def test_user_identity_is_persisted_and_reused(repository):
    gateway = FakeStackExchange()
    gateway.users[16923803] = Owner(
        user_id=16923803,
        display_name="Example User",
        link="https://stackoverflow.com/users/16923803/example-user",
        reputation=123,
    )
    service = SyncService(gateway, repository)

    first = await service.get_user(16923803)
    second = await service.get_user(16923803)

    assert first is not None
    assert second is not None
    assert second.display_name == "Example User"
    assert gateway.requested_users == [16923803]
