from abc import ABC, abstractmethod
from datetime import datetime

from stack_overflow_analyzer.domain.models import AllTimeLeaderboard, Owner, StackPage


class StackExchangeGateway(ABC):
    @abstractmethod
    async def fetch_users_answers(
        self, user_ids: list[int], from_date: datetime, to_date: datetime, page: int
    ) -> StackPage:
        """Fetch one creation-sorted page of answers posted by up to 100 users."""

    @abstractmethod
    async def fetch_questions_by_ids(
        self, question_ids: list[int]
    ) -> tuple[list[dict[str, object]], int | None]:
        """Fetch up to 100 parent questions by stable IDs."""

    @abstractmethod
    async def fetch_all_time_top_answerers(self, tag: str) -> AllTimeLeaderboard:
        """Fetch the official Stack Exchange all-time Top-20."""

    @abstractmethod
    async def fetch_user(self, user_id: int) -> Owner | None:
        """Fetch one user identity, or return None when the user does not exist."""

    @abstractmethod
    async def close(self) -> None:
        """Release owned network resources."""
