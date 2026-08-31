from collections.abc import Callable

from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    AllTimeTopAnswerer,
    DateRange,
    NarrativeOutput,
    StackPage,
)
from stack_overflow_analyzer.ports.narrative import NarrativeGenerator
from stack_overflow_analyzer.ports.repository import (
    AnalyticsRepository,
    Checkpoint,
    StoredContributorRow,
)
from stack_overflow_analyzer.ports.stack_exchange import StackExchangeGateway


class FakeStackExchange(StackExchangeGateway):
    def __init__(self) -> None:
        self.question_pages: dict[int, StackPage | Exception] = {
            1: StackPage(items=[], has_more=False, quota_remaining=9999)
        }
        self.answers: list[dict[str, object]] = []
        self.requested_pages: list[int] = []
        self.closed = False

    async def fetch_questions(self, tag, from_date, to_date, page):
        self.requested_pages.append(page)
        result = self.question_pages[page]
        if isinstance(result, Exception):
            raise result
        return result

    async def fetch_answers(self, question_ids, from_date, to_date):
        return self.answers, 9998

    async def fetch_all_time_top_answerers(self, tag):
        return AllTimeLeaderboard(
            tag=tag,
            contributors=[
                AllTimeTopAnswerer(
                    rank=1,
                    user_id=1,
                    display_name="Ada",
                    score=100,
                    post_count=10,
                )
            ],
            quota_remaining=9999,
        )

    async def close(self):
        self.closed = True


class FakeRepository(AnalyticsRepository):
    def __init__(
        self,
        rows_for_period: Callable[[DateRange], list[StoredContributorRow]] | None = None,
    ) -> None:
        self.rows_for_period = rows_for_period or (lambda _: [])
        self.checkpoints: dict[tuple[str, object, object], Checkpoint] = {}
        self.checkpoint_count = 0
        self.related: list[tuple[str, int]] = []
        self.closed = False

    async def initialize(self):
        return None

    async def get_or_create_checkpoint(self, tag, period):
        key = (tag, period.start_date, period.end_date)
        if key in self.checkpoints:
            return self.checkpoints[key], True
        self.checkpoint_count += 1
        checkpoint = Checkpoint(f"sync-{self.checkpoint_count}", 1, False)
        self.checkpoints[key] = checkpoint
        return checkpoint, False

    async def save_sync_page(
        self,
        sync_id,
        page,
        questions,
        answers,
        *,
        has_more,
        quota_remaining,
    ):
        for checkpoint in self.checkpoints.values():
            if checkpoint.sync_id == sync_id:
                checkpoint.next_page = page + 1
                checkpoint.pages_completed += 1
                checkpoint.questions_upserted += len(questions)
                checkpoint.answers_upserted += len(answers)
                checkpoint.quota_remaining = quota_remaining
                checkpoint.completed = not has_more
                return

    async def mark_sync_failed(self, sync_id, error_type):
        return None

    async def contributor_rows(self, tag, period):
        return self.rows_for_period(period)

    async def related_tags(self, tag, period, user_id):
        return self.related

    async def close(self):
        self.closed = True


class FakeNarrativeGenerator(NarrativeGenerator):
    def __init__(self, output: NarrativeOutput) -> None:
        self.output = output
        self.closed = False

    async def generate(self, analysis):
        return self.output

    async def close(self):
        self.closed = True
