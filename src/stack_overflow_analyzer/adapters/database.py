from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    case,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    AllTimeTopAnswerer,
    Answer,
    DateRange,
    Owner,
    Question,
    SyncStatus,
)
from stack_overflow_analyzer.ports.repository import (
    AnalyticsRepository,
    Checkpoint,
    StoredContributorRow,
)


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(Text)
    reputation: Mapped[int | None] = mapped_column(Integer)


class QuestionRecord(Base):
    __tablename__ = "questions"

    question_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(Text)
    link: Mapped[str] = mapped_column(Text)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"))


class QuestionTagRecord(Base):
    __tablename__ = "question_tags"

    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.question_id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)


class AnswerRecord(Base):
    __tablename__ = "answers"

    answer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.question_id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), index=True)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    score: Mapped[int] = mapped_column(Integer)
    is_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    link: Mapped[str | None] = mapped_column(Text)


class SyncRunRecord(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (UniqueConstraint("tag", "start_date", "end_date"),)

    sync_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    cursor_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_page: Mapped[int] = mapped_column(Integer, default=1)
    pages_completed: Mapped[int] = mapped_column(Integer, default=0)
    questions_upserted: Mapped[int] = mapped_column(Integer, default=0)
    answers_upserted: Mapped[int] = mapped_column(Integer, default=0)
    quota_remaining: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CohortSnapshotRecord(Base):
    __tablename__ = "cohort_snapshots"

    tag: Mapped[str] = mapped_column(String(64), primary_key=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quota_remaining: Mapped[int | None] = mapped_column(Integer)


class CohortMemberRecord(Base):
    __tablename__ = "cohort_members"
    __table_args__ = (UniqueConstraint("tag", "official_rank"),)

    tag: Mapped[str] = mapped_column(
        ForeignKey("cohort_snapshots.tag", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    official_rank: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    post_count: Mapped[int] = mapped_column(Integer)


class SQLiteAnalyticsRepository(AnalyticsRepository):
    def __init__(self, database_url: str, *, engine: AsyncEngine | None = None) -> None:
        self._engine = engine or create_async_engine(database_url)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            columns = {
                row[1] for row in (await connection.exec_driver_sql("PRAGMA table_info(sync_runs)"))
            }
            if "cursor_from" not in columns:
                await connection.exec_driver_sql(
                    "ALTER TABLE sync_runs ADD COLUMN cursor_from DATETIME"
                )

    async def get_or_create_checkpoint(
        self, tag: str, period: DateRange
    ) -> tuple[Checkpoint, bool]:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(SyncRunRecord).where(
                    SyncRunRecord.tag == tag,
                    SyncRunRecord.start_date == period.start_date,
                    SyncRunRecord.end_date == period.end_date,
                )
            )
            resumed = record is not None
            if record is None:
                record = SyncRunRecord(
                    sync_id=str(uuid4()),
                    tag=tag,
                    start_date=period.start_date,
                    end_date=period.end_date,
                    status=SyncStatus.RUNNING.value,
                    cursor_from=period.start_at,
                    next_page=1,
                    pages_completed=0,
                    questions_upserted=0,
                    answers_upserted=0,
                    updated_at=datetime.now(UTC),
                )
                session.add(record)
            elif record.status != SyncStatus.COMPLETED.value:
                if record.cursor_from is None:
                    record.cursor_from = period.start_at
                if record.next_page > 25:
                    record.next_page = 1
                record.status = SyncStatus.RUNNING.value
                record.error_type = None
                record.updated_at = datetime.now(UTC)
            return self._checkpoint(record), resumed

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
    ) -> None:
        async with self._sessions.begin() as session:
            run = await session.get(SyncRunRecord, sync_id)
            if run is None:
                raise RuntimeError(f"unknown sync run {sync_id}")
            if page < run.next_page:
                return
            if page != run.next_page:
                raise RuntimeError(f"expected page {run.next_page}, received {page}")

            users = {
                item.owner.user_id: item.owner
                for item in [*questions, *answers]
                if item.owner is not None
            }
            for owner in users.values():
                statement = sqlite_insert(UserRecord).values(
                    user_id=owner.user_id,
                    display_name=owner.display_name,
                    profile_url=owner.link,
                    reputation=owner.reputation,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[UserRecord.user_id],
                        set_={
                            "display_name": statement.excluded.display_name,
                            "profile_url": statement.excluded.profile_url,
                            "reputation": statement.excluded.reputation,
                        },
                    )
                )

            for question in questions:
                statement = sqlite_insert(QuestionRecord).values(
                    question_id=question.question_id,
                    creation_date=question.creation_date,
                    title=question.title,
                    link=question.link,
                    owner_user_id=question.owner.user_id if question.owner else None,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[QuestionRecord.question_id],
                        set_={
                            "creation_date": statement.excluded.creation_date,
                            "title": statement.excluded.title,
                            "link": statement.excluded.link,
                            "owner_user_id": statement.excluded.owner_user_id,
                        },
                    )
                )
                await session.execute(
                    delete(QuestionTagRecord).where(
                        QuestionTagRecord.question_id == question.question_id
                    )
                )
                session.add_all(
                    [
                        QuestionTagRecord(question_id=question.question_id, tag=tag)
                        for tag in question.tags
                    ]
                )

            for answer in answers:
                statement = sqlite_insert(AnswerRecord).values(
                    answer_id=answer.answer_id,
                    question_id=answer.question_id,
                    owner_user_id=answer.owner.user_id if answer.owner else None,
                    creation_date=answer.creation_date,
                    score=answer.score,
                    is_accepted=answer.is_accepted,
                    link=answer.link,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[AnswerRecord.answer_id],
                        set_={
                            "question_id": statement.excluded.question_id,
                            "owner_user_id": statement.excluded.owner_user_id,
                            "creation_date": statement.excluded.creation_date,
                            "score": statement.excluded.score,
                            "is_accepted": statement.excluded.is_accepted,
                            "link": statement.excluded.link,
                        },
                    )
                )

            run.cursor_from = next_cursor_from
            run.next_page = next_page
            run.pages_completed += 1
            run.questions_upserted += len(questions)
            run.answers_upserted += len(answers)
            run.quota_remaining = quota_remaining
            run.status = SyncStatus.COMPLETED.value if completed else SyncStatus.RUNNING.value
            run.updated_at = datetime.now(UTC)

    async def mark_sync_failed(self, sync_id: str, error_type: str) -> None:
        async with self._sessions.begin() as session:
            run = await session.get(SyncRunRecord, sync_id)
            if run is not None and run.status != SyncStatus.COMPLETED.value:
                run.status = SyncStatus.FAILED.value
                run.error_type = error_type[:255]
                run.updated_at = datetime.now(UTC)

    async def benchmark_contributor_rows(
        self, tag: str, period: DateRange, user_ids: list[int]
    ) -> list[StoredContributorRow]:
        if not user_ids:
            return []
        accepted_count = func.sum(case((AnswerRecord.is_accepted.is_(True), 1), else_=0))
        statement = (
            select(
                UserRecord.user_id,
                UserRecord.display_name,
                UserRecord.profile_url,
                func.count(AnswerRecord.answer_id),
                func.sum(AnswerRecord.score),
                accepted_count,
            )
            .join(AnswerRecord, AnswerRecord.owner_user_id == UserRecord.user_id)
            .join(QuestionRecord, QuestionRecord.question_id == AnswerRecord.question_id)
            .join(
                QuestionTagRecord,
                QuestionTagRecord.question_id == QuestionRecord.question_id,
            )
            .where(
                QuestionTagRecord.tag == tag,
                AnswerRecord.owner_user_id.in_(user_ids),
                AnswerRecord.creation_date >= period.start_at,
                AnswerRecord.creation_date < period.end_exclusive,
            )
            .group_by(UserRecord.user_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            StoredContributorRow(
                user_id=row[0],
                display_name=row[1],
                profile_url=row[2],
                answer_count=row[3],
                total_answer_score=row[4] or 0,
                accepted_answer_count=row[5] or 0,
            )
            for row in rows
        ]

    async def get_all_time_cohort(self, tag: str) -> AllTimeLeaderboard | None:
        async with self._sessions() as session:
            snapshot = await session.get(CohortSnapshotRecord, tag)
            if snapshot is None:
                return None
            rows = (
                await session.execute(
                    select(CohortMemberRecord)
                    .where(CohortMemberRecord.tag == tag)
                    .order_by(CohortMemberRecord.official_rank)
                )
            ).scalars()
            contributors = [
                AllTimeTopAnswerer(
                    rank=row.official_rank,
                    user_id=row.user_id,
                    display_name=row.display_name,
                    profile_url=row.profile_url,
                    score=row.score,
                    post_count=row.post_count,
                )
                for row in rows
            ]
        retrieved_at = snapshot.retrieved_at
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        return AllTimeLeaderboard(
            tag=tag,
            contributors=contributors,
            quota_remaining=snapshot.quota_remaining,
            retrieved_at=retrieved_at,
        )

    async def save_all_time_cohort(self, cohort: AllTimeLeaderboard) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                delete(CohortMemberRecord).where(CohortMemberRecord.tag == cohort.tag)
            )
            statement = sqlite_insert(CohortSnapshotRecord).values(
                tag=cohort.tag,
                retrieved_at=cohort.retrieved_at,
                quota_remaining=cohort.quota_remaining,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[CohortSnapshotRecord.tag],
                    set_={
                        "retrieved_at": statement.excluded.retrieved_at,
                        "quota_remaining": statement.excluded.quota_remaining,
                    },
                )
            )
            session.add_all(
                [
                    CohortMemberRecord(
                        tag=cohort.tag,
                        user_id=item.user_id,
                        official_rank=item.rank,
                        display_name=item.display_name,
                        profile_url=item.profile_url,
                        score=item.score,
                        post_count=item.post_count,
                    )
                    for item in cohort.contributors
                ]
            )

    async def get_user(self, user_id: int) -> Owner | None:
        async with self._sessions() as session:
            record = await session.get(UserRecord, user_id)
        if record is None:
            return None
        return Owner(
            user_id=record.user_id,
            display_name=record.display_name,
            link=record.profile_url,
            reputation=record.reputation,
        )

    async def save_user(self, user: Owner) -> None:
        async with self._sessions.begin() as session:
            statement = sqlite_insert(UserRecord).values(
                user_id=user.user_id,
                display_name=user.display_name,
                profile_url=user.link,
                reputation=user.reputation,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[UserRecord.user_id],
                    set_={
                        "display_name": statement.excluded.display_name,
                        "profile_url": statement.excluded.profile_url,
                        "reputation": statement.excluded.reputation,
                    },
                )
            )

    async def close(self) -> None:
        await self._engine.dispose()

    @staticmethod
    def _checkpoint(record: SyncRunRecord) -> Checkpoint:
        return Checkpoint(
            sync_id=record.sync_id,
            next_page=record.next_page,
            completed=record.status == SyncStatus.COMPLETED.value,
            cursor_from=record.cursor_from,
            pages_completed=record.pages_completed,
            questions_upserted=record.questions_upserted,
            answers_upserted=record.answers_upserted,
            quota_remaining=record.quota_remaining,
        )
