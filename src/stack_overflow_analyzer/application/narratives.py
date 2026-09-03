from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.domain.exceptions import InvalidEvidenceError
from stack_overflow_analyzer.domain.models import (
    Confidence,
    ContributorAnalysis,
    ContributorNarrative,
    DateRange,
)
from stack_overflow_analyzer.ports.narrative import NarrativeGenerator


class NarrativeService:
    def __init__(self, analytics: AnalyticsService, generator: NarrativeGenerator) -> None:
        self._analytics = analytics
        self._generator = generator

    async def create(self, tag: str, period: DateRange, user_id: int) -> ContributorNarrative:
        analysis = await self._analytics.analyze(tag, period, user_id)
        narrative = await self._generator.generate(analysis)
        allowed_ids = {item.id for item in analysis.evidence}
        invalid_ids = set(narrative.evidence_ids) - allowed_ids
        if invalid_ids:
            invalid = ", ".join(sorted(invalid_ids))
            raise InvalidEvidenceError(f"model returned unknown evidence IDs: {invalid}")
        if narrative.root_cause_hypothesis is not None and not self._supports_hypothesis(
            analysis, narrative.evidence_ids
        ):
            raise InvalidEvidenceError(
                "model returned a root-cause hypothesis without sufficient comparison evidence"
            )
        confidence = self._cap_confidence(analysis, narrative.confidence)
        narrative = narrative.model_copy(update={"confidence": confidence})
        return ContributorNarrative(analysis=analysis, narrative=narrative)

    @staticmethod
    def _supports_hypothesis(analysis: ContributorAnalysis, evidence_ids: list[str]) -> bool:
        has_active_peer_evidence = analysis.peer_comparison.peer_count > 0 and any(
            evidence_id.startswith("peers.") for evidence_id in evidence_ids
        )
        has_previous_period_evidence = analysis.previous_period.answer_count > 0 and any(
            evidence_id.startswith("previous.") for evidence_id in evidence_ids
        )
        return analysis.contributor.has_qualifying_answers and (
            has_active_peer_evidence or has_previous_period_evidence
        )

    @staticmethod
    def _cap_confidence(analysis: ContributorAnalysis, requested: Confidence) -> Confidence:
        if not analysis.contributor.has_qualifying_answers:
            maximum = Confidence.LOW
        elif (
            analysis.contributor.answer_count >= 5
            and analysis.peer_comparison.peer_count >= 3
            and analysis.previous_period.answer_count > 0
        ):
            maximum = Confidence.HIGH
        else:
            maximum = Confidence.MEDIUM
        order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
        return requested if order[requested] <= order[maximum] else maximum
