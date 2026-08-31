from abc import ABC, abstractmethod

from stack_overflow_analyzer.domain.models import Answer, DateRange, Question


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
        pages_completed: int = 0,
        questions_upserted: int = 0,
        answers_upserted: int = 0,
        quota_remaining: int | None = None,
    ) -> None:
        self.sync_id = sync_id
        self.next_page = next_page
        self.completed = completed
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
        has_more: bool,
        quota_remaining: int | None,
    ) -> None: ...

    @abstractmethod
    async def mark_sync_failed(self, sync_id: str, error_type: str) -> None: ...

    @abstractmethod
    async def contributor_rows(self, tag: str, period: DateRange) -> list[StoredContributorRow]: ...

    @abstractmethod
    async def related_tags(
        self, tag: str, period: DateRange, user_id: int
    ) -> list[tuple[str, int]]: ...

    @abstractmethod
    async def close(self) -> None: ...
