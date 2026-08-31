from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DateRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_date: date
    end_date: date

    @field_validator("end_date")
    @classmethod
    def end_not_before_start(cls, value: date, info: object) -> date:
        data = getattr(info, "data", {})
        start = data.get("start_date")
        if start is not None and value < start:
            raise ValueError("end_date must be on or after start_date")
        return value

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start_date, datetime.min.time(), tzinfo=UTC)

    @property
    def end_exclusive(self) -> datetime:
        return datetime.combine(self.end_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    @property
    def previous(self) -> "DateRange":
        days = (self.end_date - self.start_date).days + 1
        return DateRange(
            start_date=self.start_date - timedelta(days=days),
            end_date=self.start_date - timedelta(days=1),
        )


class Owner(BaseModel):
    user_id: int
    display_name: str
    link: str | None = None
    reputation: int | None = None


class Question(BaseModel):
    question_id: int
    creation_date: datetime
    title: str
    tags: list[str]
    link: str
    owner: Owner | None = None


class Answer(BaseModel):
    answer_id: int
    question_id: int
    creation_date: datetime
    score: int
    is_accepted: bool = False
    link: str | None = None
    owner: Owner | None = None


class StackPage(BaseModel):
    items: list[dict[str, object]]
    has_more: bool = False
    quota_remaining: int | None = None
    quota_max: int | None = None
    backoff: int | None = None


class SyncStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncResult(BaseModel):
    sync_id: str
    tag: str
    period: DateRange
    status: SyncStatus
    pages_completed: int
    questions_upserted: int
    answers_upserted: int
    resumed: bool
    quota_remaining: int | None = None


class ContributorMetrics(BaseModel):
    user_id: int
    display_name: str
    profile_url: str | None = None
    rank: int
    is_top_20: bool
    answer_count: int
    total_answer_score: int
    accepted_answer_count: int
    acceptance_rate: float
    average_answer_score: float


class MetricComparison(BaseModel):
    peer_median: float
    absolute_difference: float
    percent_difference: float | None


class PeerComparison(BaseModel):
    peer_group: str = "top_20_contributors_for_period"
    answer_count: MetricComparison
    total_answer_score: MetricComparison
    acceptance_rate: MetricComparison
    average_answer_score: MetricComparison


class PreviousPeriodComparison(BaseModel):
    period: DateRange
    rank: int | None
    answer_count: int
    answer_count_change: int
    total_answer_score: int
    total_answer_score_change: int
    acceptance_rate: float
    acceptance_rate_change: float


class RelatedTag(BaseModel):
    tag: str
    answered_question_count: int
    share_of_answers: float = Field(ge=0, le=1)


class Evidence(BaseModel):
    id: str
    label: str
    value: int | float | str | bool | None
    context: str


class MetricDefinition(BaseModel):
    name: str = "period_cohort_answer_score"
    description: str
    ranking_order: list[str]


class ContributorAnalysis(BaseModel):
    tag: str
    period: DateRange
    metric: MetricDefinition
    contributor: ContributorMetrics
    peer_comparison: PeerComparison
    previous_period: PreviousPeriodComparison
    related_tags: list[RelatedTag]
    evidence: list[Evidence]


class Leaderboard(BaseModel):
    tag: str
    period: DateRange
    metric: MetricDefinition
    contributors: list[ContributorMetrics]
    total_contributors: int


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NarrativeOutput(BaseModel):
    notable_contribution: str
    ranking_explanation: str
    peer_comparison: str
    period_change: str
    topic_fingerprint: str
    root_cause_hypothesis: str | None
    confidence: Confidence
    evidence_ids: list[str] = Field(min_length=1)


class ContributorNarrative(BaseModel):
    analysis: ContributorAnalysis
    narrative: NarrativeOutput


class AllTimeTopAnswerer(BaseModel):
    rank: int
    user_id: int
    display_name: str
    profile_url: str | None = None
    score: int
    post_count: int


class AllTimeLeaderboard(BaseModel):
    tag: str
    source: str = "Stack Exchange /tags/{tag}/top-answerers/all_time"
    contributors: list[AllTimeTopAnswerer]
    quota_remaining: int | None = None
