from abc import ABC, abstractmethod
from datetime import datetime

from stack_overflow_analyzer.domain.models import AllTimeLeaderboard, StackPage


class StackExchangeGateway(ABC):
    @abstractmethod
    async def fetch_questions(
        self, tag: str, from_date: datetime, to_date: datetime, page: int
    ) -> StackPage:
        """Fetch one page of questions created in a UTC half-open interval."""

    @abstractmethod
    async def fetch_answers(
        self, question_ids: list[int], from_date: datetime, to_date: datetime
    ) -> tuple[list[dict[str, object]], int | None]:
        """Fetch every answer in the interval for a batch of questions."""

    @abstractmethod
    async def fetch_all_time_top_answerers(self, tag: str) -> AllTimeLeaderboard:
        """Fetch the official Stack Exchange all-time Top-20."""

    @abstractmethod
    async def close(self) -> None:
        """Release owned network resources."""
