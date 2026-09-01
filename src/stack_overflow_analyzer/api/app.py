import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from time import monotonic
from typing import Annotated
from uuid import uuid4

import structlog
from fastapi import FastAPI, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from stack_overflow_analyzer.adapters.database import SQLiteAnalyticsRepository
from stack_overflow_analyzer.adapters.openai_narrative import OpenAINarrativeGenerator
from stack_overflow_analyzer.adapters.stack_exchange import StackExchangeClient
from stack_overflow_analyzer.application.analytics import AnalyticsService
from stack_overflow_analyzer.application.narratives import NarrativeService
from stack_overflow_analyzer.application.sync import SyncService
from stack_overflow_analyzer.config import Settings, get_settings
from stack_overflow_analyzer.domain.exceptions import (
    ContributorNotFoundError,
    NarrativeUnavailableError,
    QuotaExhaustedError,
    UpstreamError,
)
from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    ContributorAnalysis,
    ContributorNarrative,
    DateRange,
    Leaderboard,
    SyncResult,
)
from stack_overflow_analyzer.logging import configure_logging
from stack_overflow_analyzer.ports.narrative import NarrativeGenerator
from stack_overflow_analyzer.ports.repository import AnalyticsRepository
from stack_overflow_analyzer.ports.stack_exchange import StackExchangeGateway

logger = structlog.get_logger(__name__)
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.+#-]{0,34}$")


class SyncRequest(BaseModel):
    tag: str
    start_date: date
    end_date: date


class NarrativeRequest(BaseModel):
    start_date: date
    end_date: date


class HealthResponse(BaseModel):
    status: str


def create_app(
    settings: Settings | None = None,
    *,
    repository: AnalyticsRepository | None = None,
    stack_exchange: StackExchangeGateway | None = None,
    narrative_generator: NarrativeGenerator | None = None,
) -> FastAPI:
    config = settings or get_settings()
    configure_logging(config.log_level)
    repo = repository or SQLiteAnalyticsRepository(config.database_url)
    gateway = stack_exchange or StackExchangeClient(
        base_url=config.stack_exchange_base_url,
        site=config.stack_exchange_site,
        timeout_seconds=config.stack_exchange_timeout_seconds,
        max_retries=config.stack_exchange_max_retries,
    )
    generator = narrative_generator or OpenAINarrativeGenerator(
        api_key=(
            config.openai_api_key.get_secret_value() if config.openai_api_key is not None else None
        ),
        model=config.openai_model,
        timeout_seconds=config.openai_timeout_seconds,
    )
    sync_service = SyncService(gateway, repo)
    analytics_service = AnalyticsService(repo, sync_service)
    narrative_service = NarrativeService(analytics_service, generator)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await repo.initialize()
        yield
        await generator.close()
        await gateway.close()
        await repo.close()

    app = FastAPI(
        title=config.app_name,
        version="0.1.0",
        description=(
            "Deterministic period-cohort analytics for Stack Overflow contributors. "
            "The LLM explains evidence; it never establishes metrics."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: object) -> JSONResponse:
        request_id = request.headers.get("x-request-id", str(uuid4()))[:100]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = monotonic()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((monotonic() - started) * 1000, 2)
            logger.info(
                "request_completed",
                operation="http_request",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(ContributorNotFoundError)
    async def contributor_not_found(_: Request, exc: ContributorNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(QuotaExhaustedError)
    async def quota_exhausted(_: Request, exc: QuotaExhaustedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": str(exc)},
            headers={"Retry-After": "86400"},
        )

    @app.exception_handler(UpstreamError)
    async def upstream_failure(_: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(NarrativeUnavailableError)
    async def narrative_failure(_: Request, exc: NarrativeUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post(
        "/v1/sync",
        response_model=SyncResult,
        status_code=status.HTTP_200_OK,
        tags=["synchronization"],
    )
    async def sync_period(payload: SyncRequest) -> SyncResult:
        tag = validate_tag(payload.tag)
        period = validated_period(payload.start_date, payload.end_date, config.max_period_days)
        return await sync_service.sync(tag, period)

    @app.get(
        "/v1/tags/{tag}/contributors",
        response_model=Leaderboard,
        tags=["analytics"],
    )
    async def contributors(
        tag: Annotated[str, Path()],
        from_date: Annotated[date, Query()],
        to_date: Annotated[date, Query()],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> Leaderboard:
        return await analytics_service.leaderboard(
            validate_tag(tag),
            validated_period(from_date, to_date, config.max_period_days),
            limit=limit,
        )

    @app.get(
        "/v1/tags/{tag}/contributors/{user_id}",
        response_model=ContributorAnalysis,
        tags=["analytics"],
    )
    async def contributor_analysis(
        tag: Annotated[str, Path()],
        user_id: Annotated[int, Path(gt=0)],
        from_date: Annotated[date, Query()],
        to_date: Annotated[date, Query()],
    ) -> ContributorAnalysis:
        return await analytics_service.analyze(
            validate_tag(tag),
            validated_period(from_date, to_date, config.max_period_days),
            user_id,
        )

    @app.post(
        "/v1/tags/{tag}/contributors/{user_id}/narrative",
        response_model=ContributorNarrative,
        tags=["narratives"],
    )
    async def contributor_narrative(
        payload: NarrativeRequest,
        tag: Annotated[str, Path()],
        user_id: Annotated[int, Path(gt=0)],
    ) -> ContributorNarrative:
        return await narrative_service.create(
            validate_tag(tag),
            validated_period(payload.start_date, payload.end_date, config.max_period_days),
            user_id,
        )

    @app.get(
        "/v1/tags/{tag}/top-answerers/all-time",
        response_model=AllTimeLeaderboard,
        tags=["analytics"],
    )
    async def all_time_top_answerers(
        tag: Annotated[str, Path()],
    ) -> AllTimeLeaderboard:
        return await gateway.fetch_all_time_top_answerers(validate_tag(tag))

    return app


def validate_tag(tag: str) -> str:
    normalized = tag.strip().lower()
    if not TAG_PATTERN.fullmatch(normalized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="tag must be 1-35 lowercase letters, digits, or . + # - characters",
        )
    return normalized


def validated_period(start_date: date, end_date: date, max_days: int = 31) -> DateRange:
    try:
        period = DateRange(start_date=start_date, end_date=end_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    inclusive_days = (end_date - start_date).days + 1
    if inclusive_days > max_days:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"date range cannot exceed {max_days} inclusive days; "
                "split longer analyses into monthly requests"
            ),
        )
    return period


app = create_app()
