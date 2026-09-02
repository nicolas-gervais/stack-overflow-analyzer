from statistics import fmean

from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.domain.exceptions import ContributorNotFoundError
from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    CohortDefinition,
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
        "Ranks Stack Exchange's official all-time Top-20 answerers for the tag, plus the "
        "requested user when necessary, when they have qualifying answers. Ranking uses native "
        "scores on answers created in the half-open UTC period whose parent questions carry the "
        "tag. Contributors without qualifying answers are unranked. This is a benchmark-cohort "
        "rank, not a global rank for the period."
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
        cohort = await self._sync_service.get_all_time_cohort(tag)
        self._require_cohort(cohort)
        identities = self._official_identities(cohort)
        user_ids = [identity.user_id for identity in identities]
        await self._sync_service.sync_benchmark(tag, period, user_ids)
        rows = await self._repository.benchmark_contributor_rows(tag, period, user_ids)
        metrics = self._rank(rows, identities)
        return Leaderboard(
            tag=tag,
            period=period,
            metric=METRIC,
            cohort=self._cohort_definition(cohort, subject_added=False),
            contributors=metrics[:limit],
            total_contributors=len(metrics),
        )

    async def analyze(self, tag: str, period: DateRange, user_id: int) -> ContributorAnalysis:
        cohort = await self._sync_service.get_all_time_cohort(tag)
        self._require_cohort(cohort)
        official_identities = self._official_identities(cohort)
        official_ids = {identity.user_id for identity in official_identities}
        user_ids = [identity.user_id for identity in official_identities]
        if user_id not in official_ids:
            user_ids.append(user_id)

        await self._sync_service.sync_benchmark(tag, period, user_ids)
        current_rows = await self._repository.benchmark_contributor_rows(tag, period, user_ids)
        subject_row = next((row for row in current_rows if row.user_id == user_id), None)

        identities = list(official_identities)
        if user_id not in official_ids:
            if subject_row is None:
                subject = await self._sync_service.get_user(user_id)
                if subject is None:
                    raise ContributorNotFoundError(f"Stack Overflow user {user_id} does not exist")
                display_name = subject.display_name
                profile_url = subject.link
            else:
                display_name = subject_row.display_name
                profile_url = subject_row.profile_url
            identities.append(
                _Identity(
                    user_id=user_id,
                    display_name=display_name,
                    profile_url=profile_url,
                    official_all_time_rank=None,
                )
            )
        current = self._rank(current_rows, identities)
        contributor = next(item for item in current if item.user_id == user_id)

        await self._sync_service.sync_benchmark(tag, period.previous, user_ids)
        previous_rows = await self._repository.benchmark_contributor_rows(
            tag, period.previous, user_ids
        )
        previous = self._rank(previous_rows, identities)
        previous_contributor = next(item for item in previous if item.user_id == user_id)
        official_peers = [
            item
            for item in current
            if item.is_official_all_time_top_20
            and item.user_id != user_id
            and item.answer_count > 0
        ]
        peer_comparison = self._peer_comparison(contributor, official_peers)
        previous_comparison = self._previous_comparison(
            contributor, previous_contributor, period.previous
        )
        related_rows = (
            await self._repository.answer_period_related_tags(tag, period, user_id)
            if contributor.has_qualifying_answers
            else []
        )
        related_tags = [
            RelatedTag(
                tag=related_tag,
                answered_question_count=count,
                share_of_answers=round(count / contributor.answer_count, 4),
            )
            for related_tag, count in related_rows
        ]
        evidence = self._evidence(
            cohort, contributor, peer_comparison, previous_comparison, related_tags
        )
        return ContributorAnalysis(
            tag=tag,
            period=period,
            metric=METRIC,
            cohort=self._cohort_definition(cohort, subject_added=user_id not in official_ids),
            contributor=contributor,
            peer_comparison=peer_comparison,
            previous_period=previous_comparison,
            related_tags=related_tags,
            evidence=evidence,
            contributors=current,
        )

    @staticmethod
    def _official_identities(cohort: AllTimeLeaderboard) -> list["_Identity"]:
        return [
            _Identity(
                user_id=item.user_id,
                display_name=item.display_name,
                profile_url=item.profile_url,
                official_all_time_rank=item.rank,
            )
            for item in cohort.contributors
        ]

    @staticmethod
    def _require_cohort(cohort: AllTimeLeaderboard) -> None:
        if not cohort.contributors:
            raise ContributorNotFoundError(
                f"Stack Exchange returned no official all-time Top-20 cohort for {cohort.tag}"
            )

    @staticmethod
    def _rank(
        rows: list[StoredContributorRow], identities: list["_Identity"]
    ) -> list[ContributorMetrics]:
        row_by_user = {row.user_id: row for row in rows}
        values = []
        for identity in identities:
            row = row_by_user.get(identity.user_id)
            values.append(
                ContributorMetrics(
                    user_id=identity.user_id,
                    display_name=row.display_name if row else identity.display_name,
                    profile_url=row.profile_url if row else identity.profile_url,
                    period_benchmark_rank=None,
                    official_all_time_rank=identity.official_all_time_rank,
                    is_official_all_time_top_20=identity.official_all_time_rank is not None,
                    has_qualifying_answers=row is not None and row.answer_count > 0,
                    answer_count=row.answer_count if row else 0,
                    total_answer_score=row.total_answer_score if row else 0,
                    accepted_answer_count=row.accepted_answer_count if row else 0,
                    acceptance_rate=(
                        round(row.accepted_answer_count / row.answer_count, 4) if row else 0
                    ),
                    average_answer_score=(
                        round(row.total_answer_score / row.answer_count, 4) if row else 0
                    ),
                )
            )
        active = sorted(
            (item for item in values if item.has_qualifying_answers),
            key=lambda item: (
                -item.total_answer_score,
                -item.accepted_answer_count,
                -item.answer_count,
                item.user_id,
            ),
        )
        ranked = [
            item.model_copy(update={"period_benchmark_rank": rank})
            for rank, item in enumerate(active, start=1)
        ]
        inactive = sorted(
            (item for item in values if not item.has_qualifying_answers),
            key=lambda item: item.user_id,
        )
        return [*ranked, *inactive]

    @classmethod
    def _peer_comparison(
        cls, contributor: ContributorMetrics, peer_group: list[ContributorMetrics]
    ) -> PeerComparison:
        return PeerComparison(
            peer_count=len(peer_group),
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
        peer_mean = fmean(peer_values) if peer_values else 0.0
        difference = float(value - peer_mean)
        percent = None if peer_mean == 0 else round(difference / abs(peer_mean), 4)
        return MetricComparison(
            peer_mean=round(peer_mean, 4),
            absolute_difference=round(difference, 4),
            percent_difference=percent,
        )

    @staticmethod
    def _previous_comparison(
        current: ContributorMetrics,
        previous: ContributorMetrics,
        period: DateRange,
    ) -> PreviousPeriodComparison:
        return PreviousPeriodComparison(
            period=period,
            period_benchmark_rank=previous.period_benchmark_rank,
            period_benchmark_rank_change=(
                previous.period_benchmark_rank - current.period_benchmark_rank
                if previous.period_benchmark_rank is not None
                and current.period_benchmark_rank is not None
                else None
            ),
            answer_count=previous.answer_count,
            answer_count_change=current.answer_count - previous.answer_count,
            total_answer_score=previous.total_answer_score,
            total_answer_score_change=current.total_answer_score - previous.total_answer_score,
            acceptance_rate=previous.acceptance_rate,
            acceptance_rate_change=round(current.acceptance_rate - previous.acceptance_rate, 4),
        )

    @staticmethod
    def _cohort_definition(cohort: AllTimeLeaderboard, *, subject_added: bool) -> CohortDefinition:
        return CohortDefinition(
            snapshot_at=cohort.retrieved_at,
            official_cohort_size=len(cohort.contributors),
            subject_added_to_cohort=subject_added,
            comparison_cohort_size=len(cohort.contributors) + int(subject_added),
        )

    @staticmethod
    def _evidence(
        cohort: AllTimeLeaderboard,
        contributor: ContributorMetrics,
        peers: PeerComparison,
        previous: PreviousPeriodComparison,
        related_tags: list[RelatedTag],
    ) -> list[Evidence]:
        evidence = [
            Evidence(
                id="cohort.snapshot_at",
                label="Benchmark cohort snapshot",
                value=cohort.retrieved_at.isoformat(),
                context="When the official all-time tag Top-20 cohort was retrieved.",
            ),
            Evidence(
                id="period.benchmark_rank",
                label="Period benchmark rank",
                value=contributor.period_benchmark_rank,
                context=(
                    "Rank within the documented benchmark cohort, not a global period rank. "
                    "Null when the contributor has no qualifying answers."
                ),
            ),
            Evidence(
                id="period.answer_count",
                label="Answers",
                value=contributor.answer_count,
                context="Answers created in the half-open period on questions carrying the tag.",
            ),
            Evidence(
                id="period.total_score",
                label="Total answer score",
                value=contributor.total_answer_score,
                context="Sum of native Stack Overflow scores; not an LLM-derived score.",
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
                id="peers.mean_total_score",
                label="Official cohort mean total score",
                value=peers.total_answer_score.peer_mean,
                context="Arithmetic mean among active official peers, excluding the subject.",
            ),
            Evidence(
                id="peers.mean_answer_count",
                label="Official cohort mean answer count",
                value=peers.answer_count.peer_mean,
                context="Arithmetic mean among active official peers, excluding the subject.",
            ),
            Evidence(
                id="previous.benchmark_rank_change",
                label="Benchmark rank improvement",
                value=previous.period_benchmark_rank_change,
                context="Previous rank minus current rank; positive means improvement.",
            ),
            Evidence(
                id="previous.total_score_change",
                label="Total score change",
                value=previous.total_answer_score_change,
                context="Current less previous equal-length half-open period.",
            ),
            Evidence(
                id="previous.answer_count_change",
                label="Answer count change",
                value=previous.answer_count_change,
                context="Current less previous equal-length half-open period.",
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


class _Identity:
    def __init__(
        self,
        *,
        user_id: int,
        display_name: str,
        profile_url: str | None,
        official_all_time_rank: int | None,
    ) -> None:
        self.user_id = user_id
        self.display_name = display_name
        self.profile_url = profile_url
        self.official_all_time_rank = official_all_time_rank
