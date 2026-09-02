from abc import ABC, abstractmethod
from datetime import datetime

from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    Answer,
    DateRange,
    Owner,
    Question,
)


class StoredContributorRow:
    def __init__(
        self,
        *,
        user_id: int,
        display_name: str,
        profile_url: str | None,
        answer_count: int,
        total_answer_score: int,
        accepted_answer_count: int,
    ) -> None:
        self.user_id = user_id
        self.display_name = display_name
        self.profile_url = profile_url
        self.answer_count = answer_count
        self.total_answer_score = total_answer_score
        self.accepted_answer_count = accepted_answer_count


class Checkpoint:
    def __init__(
        self,
        sync_id: str,
        next_page: int,
        completed: bool,
        *,
        cursor_from: datetime | None = None,
        pages_completed: int = 0,
        questions_upserted: int = 0,
        answers_upserted: int = 0,
        quota_remaining: int | None = None,
    ) -> None:
        self.sync_id = sync_id
        self.next_page = next_page
        self.completed = completed
        self.cursor_from = cursor_from
        self.pages_completed = pages_completed
        self.questions_upserted = questions_upserted
        self.answers_upserted = answers_upserted
        self.quota_remaining = quota_remaining


class AnalyticsRepository(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def get_or_create_checkpoint(
        self, tag: str, period: DateRange
    ) -> tuple[Checkpoint, bool]: ...

    @abstractmethod
    async def save_sync_page(
        self,
        sync_id: str,
        page: int,
        questions: list[Question],
        answers: list[Answer],
        *,
        cursor_from: datetime,
        next_cursor_from: datetime,
        next_page: int,
        completed: bool,
        quota_remaining: int | None,
    ) -> None: ...

    @abstractmethod
    async def mark_sync_failed(self, sync_id: str, error_type: str) -> None: ...

    @abstractmethod
    async def benchmark_contributor_rows(
        self, tag: str, period: DateRange, user_ids: list[int]
    ) -> list[StoredContributorRow]: ...

    @abstractmethod
    async def answer_period_related_tags(
        self, tag: str, period: DateRange, user_id: int
    ) -> list[tuple[str, int]]: ...

    @abstractmethod
    async def get_all_time_cohort(self, tag: str) -> AllTimeLeaderboard | None: ...

    @abstractmethod
    async def save_all_time_cohort(self, cohort: AllTimeLeaderboard) -> None: ...

    @abstractmethod
    async def get_user(self, user_id: int) -> Owner | None: ...

    @abstractmethod
    async def save_user(self, user: Owner) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
