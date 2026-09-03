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
        if start is not None and value <= start:
            raise ValueError("end_date must be after start_date")
        return value

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start_date, datetime.min.time(), tzinfo=UTC)

    @property
    def end_exclusive(self) -> datetime:
        return datetime.combine(self.end_date, datetime.min.time(), tzinfo=UTC)

    @property
    def previous(self) -> "DateRange":
        days = (self.end_date - self.start_date).days
        return DateRange(
            start_date=self.start_date - timedelta(days=days),
            end_date=self.start_date,
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
    quota_remaining: int | None = Field(default=None, ge=0)
    quota_max: int | None = Field(default=None, ge=0)
    backoff: int | None = Field(default=None, ge=0)


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
    period_benchmark_rank: int | None
    official_all_time_rank: int | None
    is_official_all_time_top_20: bool
    has_qualifying_answers: bool
    answer_count: int
    total_answer_score: int
    accepted_answer_count: int
    acceptance_rate: float
    average_answer_score: float


class MetricComparison(BaseModel):
    peer_mean: float
    absolute_difference: float
    percent_difference: float | None


class PeerComparison(BaseModel):
    peer_group: str = "active_official_all_time_top_20_excluding_subject"
    peer_count: int
    answer_count: MetricComparison
    total_answer_score: MetricComparison
    acceptance_rate: MetricComparison
    average_answer_score: MetricComparison


class PreviousPeriodComparison(BaseModel):
    period: DateRange
    period_benchmark_rank: int | None
    period_benchmark_rank_change: int | None
    answer_count: int
    answer_count_change: int
    total_answer_score: int
    total_answer_score_change: int
    acceptance_rate: float
    acceptance_rate_change: float


class Evidence(BaseModel):
    id: str
    label: str
    value: int | float | str | bool | None
    context: str


class MetricDefinition(BaseModel):
    name: str = "all_time_top20_period_benchmark"
    description: str
    ranking_order: list[str]


class CohortDefinition(BaseModel):
    source: str = "Stack Exchange official all-time tag Top-20"
    snapshot_at: datetime
    official_cohort_size: int
    subject_added_to_cohort: bool
    comparison_cohort_size: int


class ContributorAnalysis(BaseModel):
    tag: str
    period: DateRange
    metric: MetricDefinition
    cohort: CohortDefinition
    contributor: ContributorMetrics
    peer_comparison: PeerComparison
    previous_period: PreviousPeriodComparison
    evidence: list[Evidence]
    contributors: list[ContributorMetrics]


class Leaderboard(BaseModel):
    tag: str
    period: DateRange
    metric: MetricDefinition
    cohort: CohortDefinition
    contributors: list[ContributorMetrics]
    total_contributors: int


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NarrativeOutput(BaseModel):
    notable_contribution: str = Field(
        max_length=600,
        description="Concise prose synthesizing participation, significance, and overall change.",
    )
    ranking_explanation: str = Field(
        max_length=600,
        description="Concise prose interpreting the main measured driver of benchmark position.",
    )
    peer_comparison: str = Field(
        max_length=600,
        description="Concise prose explaining the significance of comparison with benchmark peers.",
    )
    period_change: str = Field(
        max_length=600,
        description="Concise prose describing the trajectory from the previous equivalent period.",
    )
    root_cause_hypothesis: str | None = Field(
        default=None,
        max_length=600,
        description=(
            "A cautiously worded hypothesis based only on supplied metric relationships, or null "
            "when the supplied evidence does not support one."
        ),
    )
    confidence: Confidence = Field(
        description=(
            "Confidence in the narrative's descriptive interpretation: low, medium, or high. "
            "The application caps this using deterministic evidence-sufficiency rules."
        )
    )
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=16,
        description=(
            "Machine-readable supporting evidence IDs; these identifiers must not appear in prose."
        ),
    )


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
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    quota_remaining: int | None = None
