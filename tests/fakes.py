from collections.abc import Callable

from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    AllTimeTopAnswerer,
    DateRange,
    NarrativeOutput,
    Owner,
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
        self.user_answer_pages: dict[int, StackPage | Exception] = {
            1: StackPage(items=[], has_more=False, quota_remaining=9999)
        }
        self.parent_questions: list[dict[str, object]] = []
        self.requested_user_answer_pages: list[int] = []
        self.requested_user_ids: list[list[int]] = []
        self.requested_question_ids: list[list[int]] = []
        self.all_time_contributors = [
            AllTimeTopAnswerer(
                rank=1,
                user_id=1,
                display_name="Ada",
                score=100,
                post_count=10,
            )
        ]
        self.all_time_requests = 0
        self.users: dict[int, Owner | None] = {}
        self.requested_users: list[int] = []
        self.closed = False

    async def fetch_users_answers(self, user_ids, from_date, to_date, page):
        self.requested_user_answer_pages.append(page)
        self.requested_user_ids.append(user_ids)
        result = self.user_answer_pages[page]
        if isinstance(result, Exception):
            raise result
        return result

    async def fetch_questions_by_ids(self, question_ids):
        self.requested_question_ids.append(question_ids)
        return self.parent_questions, 9998

    async def fetch_all_time_top_answerers(self, tag):
        self.all_time_requests += 1
        return AllTimeLeaderboard(
            tag=tag,
            contributors=self.all_time_contributors,
            quota_remaining=9999,
        )

    async def fetch_user(self, user_id):
        self.requested_users.append(user_id)
        return self.users.get(user_id)

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
        self.cohort: AllTimeLeaderboard | None = None
        self.users: dict[int, Owner] = {}
        self.closed = False

    async def initialize(self):
        return None

    async def get_or_create_checkpoint(self, tag, period):
        key = (tag, period.start_date, period.end_date)
        if key in self.checkpoints:
            return self.checkpoints[key], True
        self.checkpoint_count += 1
        checkpoint = Checkpoint(
            f"sync-{self.checkpoint_count}", 1, False, cursor_from=period.start_at
        )
        self.checkpoints[key] = checkpoint
        return checkpoint, False

    async def save_sync_page(
        self,
        sync_id,
        page,
        questions,
        answers,
        *,
        cursor_from,
        next_cursor_from,
        next_page,
        completed,
        quota_remaining,
    ):
        for checkpoint in self.checkpoints.values():
            if checkpoint.sync_id == sync_id:
                checkpoint.cursor_from = next_cursor_from
                checkpoint.next_page = next_page
                checkpoint.pages_completed += 1
                checkpoint.questions_upserted += len(questions)
                checkpoint.answers_upserted += len(answers)
                checkpoint.quota_remaining = quota_remaining
                checkpoint.completed = completed
                return

    async def mark_sync_failed(self, sync_id, error_type):
        return None

    async def benchmark_contributor_rows(self, tag, period, user_ids):
        return [row for row in self.rows_for_period(period) if row.user_id in user_ids]

    async def get_all_time_cohort(self, tag):
        return self.cohort

    async def save_all_time_cohort(self, cohort):
        self.cohort = cohort

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def save_user(self, user):
        self.users[user.user_id] = user

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
