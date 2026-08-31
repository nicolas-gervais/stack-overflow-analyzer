from datetime import UTC, datetime

import httpx
import pytest
import respx

from stack_overflow_analyzer.adapters.stack_exchange import StackExchangeClient
from stack_overflow_analyzer.domain.exceptions import QuotaExhaustedError, UpstreamResponseError

START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2025, 1, 2, tzinfo=UTC)


def make_client(http_client, sleeps, retries=2):
    async def sleep(seconds):
        sleeps.append(seconds)

    return StackExchangeClient(
        base_url="https://api.stackexchange.com/2.3",
        site="stackoverflow",
        timeout_seconds=1,
        max_retries=retries,
        client=http_client,
        sleep=sleep,
        jitter=lambda: 0,
    )


@pytest.mark.asyncio
@respx.mock
async def test_retries_5xx_then_returns_page():
    route = respx.get("https://api.stackexchange.com/2.3/questions").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"items": [], "has_more": False, "quota_remaining": 10}),
        ]
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        page = await client.fetch_questions("python", START, END, 1)

    assert route.call_count == 2
    assert sleeps == [0.5]
    assert page.quota_remaining == 10


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_network_error():
    route = respx.get("https://api.stackexchange.com/2.3/questions").mock(
        side_effect=[
            httpx.ConnectError("temporary"),
            httpx.Response(200, json={"items": [], "has_more": False}),
        ]
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        await client.fetch_questions("python", START, END, 1)

    assert route.call_count == 2
    assert sleeps == [0.5]


@pytest.mark.asyncio
@respx.mock
async def test_does_not_retry_deterministic_4xx():
    route = respx.get("https://api.stackexchange.com/2.3/questions").mock(
        return_value=httpx.Response(400, json={"error_name": "bad_parameter"})
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        with pytest.raises(UpstreamResponseError, match="bad_parameter"):
            await client.fetch_questions("python", START, END, 1)

    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.asyncio
@respx.mock
async def test_answer_pagination_and_batching():
    route = respx.get("https://api.stackexchange.com/2.3/questions/1;2/answers").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [{"answer_id": 10}], "has_more": True, "quota_remaining": 9},
            ),
            httpx.Response(
                200,
                json={"items": [{"answer_id": 11}], "has_more": False, "quota_remaining": 8},
            ),
        ]
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        answers, quota = await client.fetch_answers([1, 2], START, END)

    assert route.call_count == 2
    assert [item["answer_id"] for item in answers] == [10, 11]
    assert quota == 8


@pytest.mark.asyncio
@respx.mock
async def test_provider_backoff_is_applied_before_next_request():
    route = respx.get("https://api.stackexchange.com/2.3/questions").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [], "has_more": False, "quota_remaining": 9, "backoff": 2},
            ),
            httpx.Response(200, json={"items": [], "has_more": False, "quota_remaining": 8}),
        ]
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        await client.fetch_questions("python", START, END, 1)
        await client.fetch_questions("python", START, END, 2)

    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 2


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_quota_blocks_followup_without_http_call():
    route = respx.get("https://api.stackexchange.com/2.3/questions").mock(
        return_value=httpx.Response(
            200, json={"items": [], "has_more": False, "quota_remaining": 0}
        )
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        await client.fetch_questions("python", START, END, 1)
        with pytest.raises(QuotaExhaustedError):
            await client.fetch_questions("python", START, END, 2)

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_malformed_response_is_rejected():
    respx.get("https://api.stackexchange.com/2.3/questions").mock(
        return_value=httpx.Response(200, json={"not_items": []})
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        with pytest.raises(UpstreamResponseError, match="invalid shape"):
            await client.fetch_questions("python", START, END, 1)


@pytest.mark.asyncio
@respx.mock
async def test_all_time_tag_is_url_encoded():
    route = respx.get("https://api.stackexchange.com/2.3/tags/c%23/top-answerers/all_time").mock(
        return_value=httpx.Response(200, json={"items": [], "has_more": False})
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        result = await client.fetch_all_time_top_answerers("c#")

    assert route.called
    assert result.tag == "c#"
