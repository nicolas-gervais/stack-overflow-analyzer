from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.domain.exceptions import InvalidEvidenceError
from stack_overflow_analyzer.domain.models import ContributorNarrative, DateRange
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
        return ContributorNarrative(analysis=analysis, narrative=narrative)
