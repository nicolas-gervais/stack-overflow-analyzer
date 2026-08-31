from statistics import median

from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import ContributorNotFoundError
from stack_overflow_analyzer.domain.models import (
    ContributorAnalysis,
    ContributorMetrics,
    DateRange,
    Evidence,
    Leaderboard,
    MetricComparison,
    MetricDefinition,
    PeerComparison,
    PreviousPeriodComparison,
    RelatedTag,
)
from stack_overflow_analyzer.ports.repository import AnalyticsRepository, StoredContributorRow

METRIC = MetricDefinition(
    description=(
        "Ranks answerers by the sum of Stack Overflow answer scores for answers created "
        "during the inclusive UTC date range, limited to questions created during that same "
        "range and tagged with the requested technology. This period-cohort definition is "
        "complete using the public Stack Exchange API."
    ),
    ranking_order=[
        "total_answer_score descending",
        "accepted_answer_count descending (tie-breaker)",
        "answer_count descending (tie-breaker)",
        "user_id ascending (stable final tie-breaker)",
    ],
)


class AnalyticsService:
    def __init__(self, repository: AnalyticsRepository, sync_service: SyncService) -> None:
        self._repository = repository
        self._sync_service = sync_service

    async def leaderboard(self, tag: str, period: DateRange, *, limit: int = 20) -> Leaderboard:
        await self._sync_service.sync(tag, period)
        rows = await self._repository.contributor_rows(tag, period)
        metrics = self._rank(rows)
        return Leaderboard(
            tag=tag,
            period=period,
            metric=METRIC,
            contributors=metrics[:limit],
            total_contributors=len(metrics),
        )

    async def analyze(self, tag: str, period: DateRange, user_id: int) -> ContributorAnalysis:
        await self._sync_service.sync(tag, period)
        await self._sync_service.sync(tag, period.previous)
        current = self._rank(await self._repository.contributor_rows(tag, period))
        previous = self._rank(await self._repository.contributor_rows(tag, period.previous))
        contributor = next((item for item in current if item.user_id == user_id), None)
        if contributor is None:
            raise ContributorNotFoundError(
                f"user {user_id} has no qualifying answers for {tag} in this period"
            )
        previous_contributor = next((item for item in previous if item.user_id == user_id), None)
        peer_group = current[:20]
        peers = self._peer_comparison(contributor, peer_group)
        previous_comparison = self._previous_comparison(
            contributor, previous_contributor, period.previous
        )
        related_rows = await self._repository.related_tags(tag, period, user_id)
        related_tags = [
            RelatedTag(
                tag=related_tag,
                answered_question_count=count,
                share_of_answers=round(count / contributor.answer_count, 4),
            )
            for related_tag, count in related_rows
        ]
        evidence = self._evidence(contributor, peers, previous_comparison, related_tags)
        return ContributorAnalysis(
            tag=tag,
            period=period,
            metric=METRIC,
            contributor=contributor,
            peer_comparison=peers,
            previous_period=previous_comparison,
            related_tags=related_tags,
            evidence=evidence,
        )

    @staticmethod
    def _rank(rows: list[StoredContributorRow]) -> list[ContributorMetrics]:
        ordered = sorted(
            rows,
            key=lambda row: (
                -row.total_answer_score,
                -row.accepted_answer_count,
                -row.answer_count,
                row.user_id,
            ),
        )
        return [
            ContributorMetrics(
                user_id=row.user_id,
                display_name=row.display_name,
                profile_url=row.profile_url,
                rank=rank,
                is_top_20=rank <= 20,
                answer_count=row.answer_count,
                total_answer_score=row.total_answer_score,
                accepted_answer_count=row.accepted_answer_count,
                acceptance_rate=round(row.accepted_answer_count / row.answer_count, 4),
                average_answer_score=round(row.total_answer_score / row.answer_count, 4),
            )
            for rank, row in enumerate(ordered, start=1)
        ]

    @classmethod
    def _peer_comparison(
        cls, contributor: ContributorMetrics, peer_group: list[ContributorMetrics]
    ) -> PeerComparison:
        return PeerComparison(
            answer_count=cls._comparison(
                contributor.answer_count, [item.answer_count for item in peer_group]
            ),
            total_answer_score=cls._comparison(
                contributor.total_answer_score,
                [item.total_answer_score for item in peer_group],
            ),
            acceptance_rate=cls._comparison(
                contributor.acceptance_rate,
                [item.acceptance_rate for item in peer_group],
            ),
            average_answer_score=cls._comparison(
                contributor.average_answer_score,
                [item.average_answer_score for item in peer_group],
            ),
        )

    @staticmethod
    def _comparison(value: int | float, peer_values: list[int | float]) -> MetricComparison:
        peer_median = float(median(peer_values))
        difference = float(value - peer_median)
        percent = None if peer_median == 0 else round(difference / abs(peer_median), 4)
        return MetricComparison(
            peer_median=round(peer_median, 4),
            absolute_difference=round(difference, 4),
            percent_difference=percent,
        )

    @staticmethod
    def _previous_comparison(
        current: ContributorMetrics,
        previous: ContributorMetrics | None,
        period: DateRange,
    ) -> PreviousPeriodComparison:
        if previous is None:
            return PreviousPeriodComparison(
                period=period,
                rank=None,
                answer_count=0,
                answer_count_change=current.answer_count,
                total_answer_score=0,
                total_answer_score_change=current.total_answer_score,
                acceptance_rate=0,
                acceptance_rate_change=current.acceptance_rate,
            )
        return PreviousPeriodComparison(
            period=period,
            rank=previous.rank,
            answer_count=previous.answer_count,
            answer_count_change=current.answer_count - previous.answer_count,
            total_answer_score=previous.total_answer_score,
            total_answer_score_change=current.total_answer_score - previous.total_answer_score,
            acceptance_rate=previous.acceptance_rate,
            acceptance_rate_change=round(current.acceptance_rate - previous.acceptance_rate, 4),
        )

    @staticmethod
    def _evidence(
        contributor: ContributorMetrics,
        peers: PeerComparison,
        previous: PreviousPeriodComparison,
        related_tags: list[RelatedTag],
    ) -> list[Evidence]:
        evidence = [
            Evidence(
                id="period.rank",
                label="Period rank",
                value=contributor.rank,
                context="Rank under the documented period-cohort metric.",
            ),
            Evidence(
                id="period.answer_count",
                label="Answers",
                value=contributor.answer_count,
                context="Qualifying answers in the requested period.",
            ),
            Evidence(
                id="period.total_score",
                label="Total answer score",
                value=contributor.total_answer_score,
                context="Sum of Stack Overflow scores; not an LLM-derived score.",
            ),
            Evidence(
                id="period.acceptance_rate",
                label="Acceptance rate",
                value=contributor.acceptance_rate,
                context="Accepted qualifying answers divided by qualifying answers.",
            ),
            Evidence(
                id="period.average_score",
                label="Average answer score",
                value=contributor.average_answer_score,
                context="Total answer score divided by qualifying answers.",
            ),
            Evidence(
                id="peers.median_total_score",
                label="Top-20 median total score",
                value=peers.total_answer_score.peer_median,
                context="Median among the period's first 20 ranked contributors.",
            ),
            Evidence(
                id="peers.median_answer_count",
                label="Top-20 median answer count",
                value=peers.answer_count.peer_median,
                context="Median among the period's first 20 ranked contributors.",
            ),
            Evidence(
                id="previous.total_score_change",
                label="Total score change",
                value=previous.total_answer_score_change,
                context="Current less previous equivalent period.",
            ),
            Evidence(
                id="previous.answer_count_change",
                label="Answer count change",
                value=previous.answer_count_change,
                context="Current less previous equivalent period.",
            ),
            Evidence(
                id="previous.acceptance_rate_change",
                label="Acceptance-rate change",
                value=previous.acceptance_rate_change,
                context="Current rate less previous equivalent-period rate.",
            ),
        ]
        evidence.extend(
            Evidence(
                id=f"topics.{item.tag}",
                label=f"Co-occurring tag: {item.tag}",
                value=item.answered_question_count,
                context=(
                    f"Appears on {item.share_of_answers:.1%} of the contributor's "
                    "qualifying answered questions."
                ),
            )
            for item in related_tags
        )
        return evidence
