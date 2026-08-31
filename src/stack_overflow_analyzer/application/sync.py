import asyncio
from collections import defaultdict
from datetime import UTC

import structlog
from pydantic import ValidationError

from stack_overflow_analyzer.domain.exceptions import UpstreamResponseError
from stack_overflow_analyzer.domain.models import (
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
    def __init__(self, gateway: StackExchangeGateway, repository: AnalyticsRepository) -> None:
        self._gateway = gateway
        self._repository = repository
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def sync(self, tag: str, period: DateRange) -> SyncResult:
        lock_key = f"{tag}:{period.start_date}:{period.end_date}"
        async with self._locks[lock_key]:
            return await self._sync_locked(tag, period)

    async def _sync_locked(self, tag: str, period: DateRange) -> SyncResult:
        checkpoint, resumed = await self._repository.get_or_create_checkpoint(tag, period)
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
                page = await self._gateway.fetch_questions(
                    tag, cursor_from, period.end_exclusive, page_number
                )
                questions = [self._parse_question(item) for item in page.items]
                answers_payload, answer_quota = await self._gateway.fetch_answers(
                    [question.question_id for question in questions],
                    period.start_at,
                    period.end_exclusive,
                )
                answers = [self._parse_answer(item) for item in answers_payload]
                quota_remaining = answer_quota if answer_quota is not None else page.quota_remaining
                completed = not page.has_more
                next_cursor_from = cursor_from
                next_page = page_number + 1
                if page.has_more and page_number == 25:
                    if not questions:
                        raise UpstreamResponseError(
                            "Stack Exchange returned an empty page with has_more=true"
                        )
                    next_cursor_from = max(question.creation_date for question in questions)
                    next_page = 1
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
                    "sync_page_committed",
                    operation="sync_period",
                    provider="stack_exchange",
                    sync_id=checkpoint.sync_id,
                    tag=tag,
                    page=page_number,
                    cursor_from=cursor_from.isoformat(),
                    next_cursor_from=next_cursor_from.isoformat(),
                    questions=len(questions),
                    answers=len(answers),
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
                "sync_failed",
                operation="sync_period",
                provider="stack_exchange",
                sync_id=checkpoint.sync_id,
                tag=tag,
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
