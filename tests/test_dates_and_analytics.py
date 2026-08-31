from datetime import date

import pytest

from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import ContributorNotFoundError
from stack_overflow_analyzer.domain.models import DateRange
from stack_overflow_analyzer.ports.repository import StoredContributorRow
from tests.fakes import FakeRepository, FakeStackExchange


def row(user_id, score, accepted, answers, name=None):
    return StoredContributorRow(
        user_id=user_id,
        display_name=name or f"user-{user_id}",
        profile_url=None,
        answer_count=answers,
        total_answer_score=score,
        accepted_answer_count=accepted,
    )


def test_date_range_is_inclusive_and_previous_has_equal_length():
    period = DateRange(start_date=date(2025, 1, 10), end_date=date(2025, 1, 12))

    assert period.start_at.isoformat() == "2025-01-10T00:00:00+00:00"
    assert period.end_exclusive.isoformat() == "2025-01-13T00:00:00+00:00"
    assert period.previous == DateRange(start_date=date(2025, 1, 7), end_date=date(2025, 1, 9))


def test_invalid_date_range_is_rejected():
    with pytest.raises(ValueError, match="end_date"):
        DateRange(start_date=date(2025, 1, 2), end_date=date(2025, 1, 1))


def test_ranking_uses_documented_lexicographic_tiebreakers():
    ranked = AnalyticsService._rank(
        [
            row(4, score=10, accepted=1, answers=2),
            row(3, score=10, accepted=1, answers=3),
            row(2, score=10, accepted=2, answers=2),
            row(1, score=11, accepted=0, answers=1),
        ]
    )

    assert [item.user_id for item in ranked] == [1, 2, 3, 4]
    assert ranked[2].average_answer_score == pytest.approx(3.3333)
    assert ranked[2].acceptance_rate == pytest.approx(0.3333)


@pytest.mark.asyncio
async def test_analysis_compares_peers_previous_period_and_topics():
    period = DateRange(start_date=date(2025, 2, 1), end_date=date(2025, 2, 2))

    def rows_for_period(candidate):
        if candidate == period:
            return [row(1, 20, 1, 2, "Ada"), row(2, 10, 1, 4)]
        return [row(1, 5, 0, 1, "Ada")]

    repository = FakeRepository(rows_for_period)
    repository.related = [("keras", 2), ("python", 1)]
    service = AnalyticsService(repository, SyncService(FakeStackExchange(), repository))

    analysis = await service.analyze("tensorflow", period, 1)

    assert analysis.contributor.rank == 1
    assert analysis.contributor.is_top_20 is True
    assert analysis.peer_comparison.total_answer_score.peer_median == 15
    assert analysis.previous_period.total_answer_score_change == 15
    assert analysis.previous_period.answer_count_change == 1
    assert analysis.related_tags[0].share_of_answers == 1
    assert "topics.keras" in {evidence.id for evidence in analysis.evidence}


@pytest.mark.asyncio
async def test_analysis_returns_not_found_for_user_without_period_answers():
    repository = FakeRepository(lambda _: [row(1, 2, 0, 1)])
    service = AnalyticsService(repository, SyncService(FakeStackExchange(), repository))

    with pytest.raises(ContributorNotFoundError):
        await service.analyze(
            "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1)), 99
        )
