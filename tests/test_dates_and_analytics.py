from datetime import date

import pytest

from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import ContributorNotFoundError
from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    AllTimeTopAnswerer,
    DateRange,
    Owner,
)
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


def cohort(*user_ids):
    return AllTimeLeaderboard(
        tag="tensorflow",
        contributors=[
            AllTimeTopAnswerer(
                rank=rank,
                user_id=user_id,
                display_name=f"user-{user_id}",
                score=100 - rank,
                post_count=10,
            )
            for rank, user_id in enumerate(user_ids, start=1)
        ],
    )


def test_date_range_is_half_open_and_previous_has_equal_length():
    period = DateRange(start_date=date(2025, 2, 1), end_date=date(2025, 3, 1))

    assert period.start_at.isoformat() == "2025-02-01T00:00:00+00:00"
    assert period.end_exclusive.isoformat() == "2025-03-01T00:00:00+00:00"
    assert period.previous == DateRange(start_date=date(2025, 1, 4), end_date=date(2025, 2, 1))


def test_empty_or_reversed_date_range_is_rejected():
    with pytest.raises(ValueError, match="after start_date"):
        DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 1))
    with pytest.raises(ValueError, match="after start_date"):
        DateRange(start_date=date(2025, 1, 2), end_date=date(2025, 1, 1))


def test_ranking_uses_documented_lexicographic_tiebreakers():
    identities = AnalyticsService._official_identities(cohort(4, 3, 2, 1))
    ranked = AnalyticsService._rank(
        [
            row(4, score=10, accepted=1, answers=2),
            row(3, score=10, accepted=1, answers=3),
            row(2, score=10, accepted=2, answers=2),
            row(1, score=11, accepted=0, answers=1),
        ],
        identities,
    )

    assert [item.user_id for item in ranked] == [1, 2, 3, 4]
    assert ranked[2].period_benchmark_rank == 3
    assert ranked[2].average_answer_score == pytest.approx(3.3333)
    assert ranked[2].acceptance_rate == pytest.approx(0.3333)


def test_contributors_without_answers_are_unranked_below_active_negative_scores():
    identities = AnalyticsService._official_identities(cohort(1, 2))

    ranked = AnalyticsService._rank(
        [row(1, score=-2, accepted=0, answers=1)],
        identities,
    )

    assert [item.user_id for item in ranked] == [1, 2]
    assert ranked[0].period_benchmark_rank == 1
    assert ranked[0].has_qualifying_answers is True
    assert ranked[1].period_benchmark_rank is None
    assert ranked[1].has_qualifying_answers is False


def test_peer_comparison_uses_mean_of_active_official_peers_excluding_subject():
    identities = AnalyticsService._official_identities(cohort(1, 2, 3, 4))
    ranked = AnalyticsService._rank(
        [
            row(1, score=20, accepted=1, answers=2),
            row(2, score=4, accepted=0, answers=1),
        ],
        identities,
    )
    contributor = next(item for item in ranked if item.user_id == 1)
    active_peers = [item for item in ranked if item.user_id != 1 and item.answer_count > 0]

    comparison = AnalyticsService._peer_comparison(contributor, active_peers)

    assert comparison.peer_count == 1
    assert comparison.total_answer_score.peer_mean == 4
    assert comparison.total_answer_score.absolute_difference == 16
    assert comparison.total_answer_score.percent_difference == 4


@pytest.mark.asyncio
async def test_analysis_compares_official_peers_previous_period_and_topics():
    period = DateRange(start_date=date(2025, 2, 1), end_date=date(2025, 2, 3))

    def rows_for_period(candidate):
        if candidate == period:
            return [row(1, 20, 1, 2, "Ada"), row(2, 10, 1, 4)]
        return [row(1, 5, 0, 1, "Ada")]

    repository = FakeRepository(rows_for_period)
    repository.related = [("keras", 2), ("python", 1)]
    gateway = FakeStackExchange()
    gateway.all_time_contributors = cohort(1, 2).contributors
    service = AnalyticsService(repository, SyncService(gateway, repository))

    analysis = await service.analyze("tensorflow", period, 1)

    assert analysis.contributor.period_benchmark_rank == 1
    assert analysis.contributor.official_all_time_rank == 1
    assert analysis.peer_comparison.peer_count == 1
    assert analysis.peer_comparison.total_answer_score.peer_mean == 10
    assert analysis.previous_period.total_answer_score_change == 15
    assert analysis.previous_period.period_benchmark_rank_change == 0
    assert analysis.related_tags[0].share_of_answers == 1
    assert len(analysis.contributors) == 2
    assert "topics.keras" in {evidence.id for evidence in analysis.evidence}


@pytest.mark.asyncio
async def test_subject_outside_official_top20_is_added_to_comparison_cohort():
    period = DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    repository = FakeRepository(lambda _: [row(99, 5, 1, 1, "Grace")])
    gateway = FakeStackExchange()
    service = AnalyticsService(repository, SyncService(gateway, repository))

    analysis = await service.analyze("tensorflow", period, 99)

    assert analysis.cohort.subject_added_to_cohort is True
    assert analysis.cohort.comparison_cohort_size == 2
    assert analysis.contributor.official_all_time_rank is None
    assert analysis.contributor.is_official_all_time_top_20 is False


@pytest.mark.asyncio
async def test_analysis_returns_zero_metrics_for_valid_user_without_period_answers():
    repository = FakeRepository(lambda _: [row(1, 2, 0, 1)])
    repository.related = [("irrelevant", 1)]
    gateway = FakeStackExchange()
    gateway.users[99] = Owner(
        user_id=99,
        display_name="No Answers",
        link="https://stackoverflow.com/users/99/no-answers",
    )
    service = AnalyticsService(repository, SyncService(gateway, repository))

    analysis = await service.analyze(
        "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 99
    )

    assert analysis.contributor.display_name == "No Answers"
    assert analysis.contributor.has_qualifying_answers is False
    assert analysis.contributor.period_benchmark_rank is None
    assert analysis.contributor.answer_count == 0
    assert analysis.contributor.total_answer_score == 0
    assert analysis.contributor.acceptance_rate == 0
    assert analysis.previous_period.period_benchmark_rank is None
    assert analysis.previous_period.period_benchmark_rank_change is None
    assert analysis.related_tags == []
    assert gateway.requested_users == [99]
    assert repository.users[99].display_name == "No Answers"


@pytest.mark.asyncio
async def test_analysis_returns_not_found_when_user_does_not_exist():
    repository = FakeRepository(lambda _: [row(1, 2, 0, 1)])
    gateway = FakeStackExchange()
    service = AnalyticsService(repository, SyncService(gateway, repository))

    with pytest.raises(ContributorNotFoundError, match="does not exist"):
        await service.analyze(
            "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 99
        )


@pytest.mark.asyncio
async def test_official_cohort_user_without_answers_needs_no_identity_request():
    repository = FakeRepository()
    gateway = FakeStackExchange()
    service = AnalyticsService(repository, SyncService(gateway, repository))

    analysis = await service.analyze(
        "python", DateRange(start_date=date(2025, 1, 1), end_date=date(2025, 1, 2)), 1
    )

    assert analysis.contributor.period_benchmark_rank is None
    assert analysis.contributor.official_all_time_rank == 1
    assert gateway.requested_users == []
