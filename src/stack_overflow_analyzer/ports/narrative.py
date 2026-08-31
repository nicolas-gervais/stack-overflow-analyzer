from abc import ABC, abstractmethod

from stack_overflow_analyzer.domain.models import ContributorAnalysis, NarrativeOutput


class NarrativeGenerator(ABC):
    @abstractmethod
    async def generate(self, analysis: ContributorAnalysis) -> NarrativeOutput:
        """Explain supplied facts without calculating new facts."""

    @abstractmethod
    async def close(self) -> None:
        """Release owned network resources."""
