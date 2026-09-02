import asyncio
import hashlib
from collections import defaultdict
from datetime import UTC

import structlog
from pydantic import ValidationError

from stack_overflow_analyzer.domain.exceptions import (
    RequestBudgetExceededError,
    UpstreamResponseError,
)
from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    Answer,
    DateRange,
    Owner,
    Question,
    SyncResult,
    SyncStatus,
)
from stack_overflow_analyzer.ports.repository import AnalyticsRepository
from stack_overflow_analyzer.ports.stack_exchange import StackExchangeGateway

logger = structlog.get_logger(__name__)


class SyncService:
    def __init__(
        self,
        gateway: StackExchangeGateway,
        repository: AnalyticsRepository,
        *,
        max_answer_pages_per_period: int = 6,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._max_answer_pages_per_period = max_answer_pages_per_period

    async def get_all_time_cohort(self, tag: str) -> AllTimeLeaderboard:
        async with self._locks[f"cohort:{tag}"]:
            cached = await self._repository.get_all_time_cohort(tag)
            if cached is not None:
                return cached
            cohort = await self._gateway.fetch_all_time_top_answerers(tag)
            await self._repository.save_all_time_cohort(cohort)
            return cohort

    async def get_user(self, user_id: int) -> Owner | None:
        async with self._locks[f"user:{user_id}"]:
            cached = await self._repository.get_user(user_id)
            if cached is not None:
                return cached
            user = await self._gateway.fetch_user(user_id)
            if user is not None:
                await self._repository.save_user(user)
            return user

    async def sync_benchmark(self, tag: str, period: DateRange, user_ids: list[int]) -> SyncResult:
        normalized_ids = sorted(set(user_ids))
        digest = hashlib.sha256(
            ",".join(str(user_id) for user_id in normalized_ids).encode()
        ).hexdigest()[:12]
        scope = f"benchmark:{tag}:{digest}"
        lock_key = f"{scope}:{period.start_date}:{period.end_date}"
        async with self._locks[lock_key]:
            return await self._sync_benchmark_locked(scope, tag, period, normalized_ids)

    async def _sync_benchmark_locked(
        self, scope: str, tag: str, period: DateRange, user_ids: list[int]
    ) -> SyncResult:
        checkpoint, resumed = await self._repository.get_or_create_checkpoint(scope, period)
        if checkpoint.completed:
            return self._result(checkpoint, tag, period, resumed=True)

        page_number = checkpoint.next_page
        cursor_from = checkpoint.cursor_from or period.start_at
        if cursor_from.tzinfo is None:
            cursor_from = cursor_from.replace(tzinfo=UTC)
        pages_completed = checkpoint.pages_completed
        questions_upserted = checkpoint.questions_upserted
        answers_upserted = checkpoint.answers_upserted
        quota_remaining = checkpoint.quota_remaining
        try:
            while True:
                if pages_completed >= self._max_answer_pages_per_period:
                    raise RequestBudgetExceededError(
                        "benchmark request budget was reached; choose a shorter date range"
                    )
                page = await self._gateway.fetch_users_answers(
                    user_ids, cursor_from, period.end_exclusive, page_number
                )
                page_answers = [self._parse_answer(item) for item in page.items]
                question_payload, question_quota = await self._gateway.fetch_questions_by_ids(
                    [answer.question_id for answer in page_answers]
                )
                fetched_questions = [self._parse_question(item) for item in question_payload]
                questions = [question for question in fetched_questions if tag in question.tags]
                qualifying_ids = {question.question_id for question in questions}
                allowed_user_ids = set(user_ids)
                answers = [
                    answer
                    for answer in page_answers
                    if answer.question_id in qualifying_ids
                    and answer.owner is not None
                    and answer.owner.user_id in allowed_user_ids
                ]
                quota_remaining = (
                    question_quota if question_quota is not None else page.quota_remaining
                )
                completed = not page.has_more
                next_cursor_from = cursor_from
                next_page = page_number + 1
                await self._repository.save_sync_page(
                    checkpoint.sync_id,
                    page_number,
                    questions,
                    answers,
                    cursor_from=cursor_from,
                    next_cursor_from=next_cursor_from,
                    next_page=next_page,
                    completed=completed,
                    quota_remaining=quota_remaining,
                )
                pages_completed += 1
                questions_upserted += len(questions)
                answers_upserted += len(answers)
                logger.info(
                    "benchmark_sync_page_committed",
                    operation="sync_benchmark",
                    provider="stack_exchange",
                    sync_id=checkpoint.sync_id,
                    tag=tag,
                    cohort_size=len(user_ids),
                    page=page_number,
                    cursor_from=cursor_from.isoformat(),
                    answers_examined=len(page_answers),
                    qualifying_answers=len(answers),
                    quota_remaining=quota_remaining,
                )
                if completed:
                    return SyncResult(
                        sync_id=checkpoint.sync_id,
                        tag=tag,
                        period=period,
                        status=SyncStatus.COMPLETED,
                        pages_completed=pages_completed,
                        questions_upserted=questions_upserted,
                        answers_upserted=answers_upserted,
                        resumed=resumed,
                        quota_remaining=quota_remaining,
                    )
                cursor_from = next_cursor_from
                page_number = next_page
        except Exception as exc:
            await self._repository.mark_sync_failed(checkpoint.sync_id, type(exc).__name__)
            logger.error(
                "benchmark_sync_failed",
                operation="sync_benchmark",
                provider="stack_exchange",
                sync_id=checkpoint.sync_id,
                tag=tag,
                cohort_size=len(user_ids),
                page=page_number,
                error_type=type(exc).__name__,
            )
            raise

    @staticmethod
    def _parse_question(item: dict[str, object]) -> Question:
        try:
            return Question(
                question_id=item["question_id"],
                creation_date=item["creation_date"],
                title=item["title"],
                tags=item["tags"],
                link=item["link"],
                owner=SyncService._parse_owner(item.get("owner")),
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise UpstreamResponseError("malformed question in Stack Exchange response") from exc

    @staticmethod
    def _parse_answer(item: dict[str, object]) -> Answer:
        try:
            return Answer(
                answer_id=item["answer_id"],
                question_id=item["question_id"],
                creation_date=item["creation_date"],
                score=item["score"],
                is_accepted=item.get("is_accepted", False),
                link=item.get("link"),
                owner=SyncService._parse_owner(item.get("owner")),
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise UpstreamResponseError("malformed answer in Stack Exchange response") from exc

    @staticmethod
    def _parse_owner(value: object) -> Owner | None:
        if not isinstance(value, dict) or "user_id" not in value:
            return None
        try:
            return Owner(
                user_id=value["user_id"],
                display_name=value.get("display_name", "unknown"),
                link=value.get("link"),
                reputation=value.get("reputation"),
            )
        except ValidationError as exc:
            raise UpstreamResponseError("malformed owner in Stack Exchange response") from exc

    @staticmethod
    def _result(checkpoint: object, tag: str, period: DateRange, *, resumed: bool) -> SyncResult:
        return SyncResult(
            sync_id=checkpoint.sync_id,
            tag=tag,
            period=period,
            status=SyncStatus.COMPLETED,
            pages_completed=checkpoint.pages_completed,
            questions_upserted=checkpoint.questions_upserted,
            answers_upserted=checkpoint.answers_upserted,
            resumed=resumed,
            quota_remaining=checkpoint.quota_remaining,
        )
