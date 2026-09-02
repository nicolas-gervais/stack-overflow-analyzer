import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from pydantic import ValidationError

from stack_overflow_analyzer.domain.exceptions import (
    QuotaExhaustedError,
    UpstreamResponseError,
)
from stack_overflow_analyzer.domain.models import (
    AllTimeLeaderboard,
    AllTimeTopAnswerer,
    Owner,
    StackPage,
)
from stack_overflow_analyzer.ports.stack_exchange import StackExchangeGateway

logger = structlog.get_logger(__name__)


class StackExchangeClient(StackExchangeGateway):
    def __init__(
        self,
        *,
        base_url: str,
        site: str,
        timeout_seconds: float,
        max_retries: int,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._owns_client = client is None
        self._site = site
        self._max_retries = max_retries
        self._sleep = sleep
        self._jitter = jitter
        self._quota_remaining: int | None = None
        self._backoff_until = 0.0

    async def fetch_users_answers(
        self, user_ids: list[int], from_date: datetime, to_date: datetime, page: int
    ) -> StackPage:
        unique_ids = list(dict.fromkeys(user_ids))
        if not unique_ids:
            raise ValueError("at least one user ID is required")
        if len(unique_ids) > 100:
            raise ValueError("Stack Exchange accepts at most 100 user IDs per batch")
        payload = await self._request(
            f"/users/{';'.join(str(item) for item in unique_ids)}/answers",
            {
                "fromdate": int(from_date.timestamp()),
                "todate": int(to_date.timestamp()) - 1,
                "sort": "creation",
                "order": "asc",
                "page": page,
                "pagesize": 100,
            },
        )
        return StackPage.model_validate(payload)

    async def fetch_questions_by_ids(
        self, question_ids: list[int]
    ) -> tuple[list[dict[str, object]], int | None]:
        unique_ids = list(dict.fromkeys(question_ids))
        if not unique_ids:
            return [], self._quota_remaining
        if len(unique_ids) > 100:
            raise ValueError("Stack Exchange accepts at most 100 question IDs per batch")
        payload = await self._request(
            f"/questions/{';'.join(str(item) for item in unique_ids)}",
            {"pagesize": 100},
        )
        page = StackPage.model_validate(payload)
        return page.items, page.quota_remaining

    async def fetch_all_time_top_answerers(self, tag: str) -> AllTimeLeaderboard:
        encoded_tag = quote(tag, safe="")
        payload = await self._request(
            f"/tags/{encoded_tag}/top-answerers/all_time", {"pagesize": 20}
        )
        page = StackPage.model_validate(payload)
        contributors: list[AllTimeTopAnswerer] = []
        for rank, item in enumerate(page.items[:20], start=1):
            user = item.get("user")
            if not isinstance(user, dict) or "user_id" not in user:
                continue
            contributors.append(
                AllTimeTopAnswerer(
                    rank=rank,
                    user_id=int(user["user_id"]),
                    display_name=str(user.get("display_name", "unknown")),
                    profile_url=str(user["link"]) if user.get("link") else None,
                    score=int(item.get("score", 0)),
                    post_count=int(item.get("post_count", 0)),
                )
            )
        return AllTimeLeaderboard(
            tag=tag, contributors=contributors, quota_remaining=page.quota_remaining
        )

    async def fetch_user(self, user_id: int) -> Owner | None:
        payload = await self._request(f"/users/{user_id}", {})
        page = StackPage.model_validate(payload)
        if not page.items:
            return None
        try:
            return Owner.model_validate(page.items[0])
        except ValidationError as exc:
            raise UpstreamResponseError("malformed user in Stack Exchange response") from exc

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._quota_remaining == 0:
            raise QuotaExhaustedError("Stack Exchange quota is exhausted")
        request_params = {"site": self._site, **params}

        for attempt in range(self._max_retries + 1):
            await self._respect_provider_backoff()
            started = monotonic()
            try:
                response = await self._client.get(path, params=request_params)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise UpstreamResponseError("Stack Exchange network request failed") from exc
                await self._retry_wait(path, attempt, type(exc).__name__)
                continue

            duration_ms = round((monotonic() - started) * 1000, 2)
            if response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise UpstreamResponseError(
                        f"Stack Exchange returned HTTP {response.status_code}"
                    )
                await self._retry_wait(path, attempt, f"http_{response.status_code}")
                continue
            if response.status_code >= 400:
                try:
                    error = response.json()
                except ValueError:
                    error = {}
                if not isinstance(error, dict):
                    error = {}
                if error.get("error_name") == "throttle_violation":
                    raise QuotaExhaustedError(
                        "Stack Exchange rejected the request for quota reasons"
                    )
                raise UpstreamResponseError(
                    f"Stack Exchange returned HTTP {response.status_code}: "
                    f"{error.get('error_name', 'request_failed')}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamResponseError("Stack Exchange returned malformed JSON") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise UpstreamResponseError("Stack Exchange response has an invalid shape")

            quota = payload.get("quota_remaining")
            self._quota_remaining = int(quota) if quota is not None else self._quota_remaining
            provider_backoff = payload.get("backoff")
            if provider_backoff is not None:
                self._backoff_until = max(
                    self._backoff_until, monotonic() + max(0, int(provider_backoff))
                )
            logger.info(
                "provider_request_completed",
                operation="stack_exchange_request",
                provider="stack_exchange",
                path=path,
                duration_ms=duration_ms,
                retry_attempt=attempt,
                quota_remaining=self._quota_remaining,
                quota_max=payload.get("quota_max"),
                provider_backoff=provider_backoff,
            )
            return payload
        raise AssertionError("retry loop terminated unexpectedly")

    async def _respect_provider_backoff(self) -> None:
        wait_seconds = self._backoff_until - monotonic()
        if wait_seconds > 0:
            await self._sleep(wait_seconds)

    async def _retry_wait(self, path: str, attempt: int, error_type: str) -> None:
        wait_seconds = (0.5 * (2**attempt)) + (self._jitter() * 0.25)
        logger.warning(
            "provider_request_retry",
            operation="stack_exchange_request",
            provider="stack_exchange",
            path=path,
            retry_attempt=attempt + 1,
            wait_seconds=round(wait_seconds, 3),
            error_type=error_type,
            quota_remaining=self._quota_remaining,
        )
        await self._sleep(wait_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
