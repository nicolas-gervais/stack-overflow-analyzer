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
    route = respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"items": [], "has_more": False, "quota_remaining": 10}),
        ]
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        page = await client.fetch_users_answers([1], START, END, 1)

    assert route.call_count == 2
    assert sleeps == [0.5]
    assert page.quota_remaining == 10


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_network_error():
    route = respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        side_effect=[
            httpx.ConnectError("temporary"),
            httpx.Response(200, json={"items": [], "has_more": False}),
        ]
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        await client.fetch_users_answers([1], START, END, 1)

    assert route.call_count == 2
    assert sleeps == [0.5]


@pytest.mark.asyncio
@respx.mock
async def test_does_not_retry_deterministic_4xx():
    route = respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        return_value=httpx.Response(400, json={"error_name": "bad_parameter"})
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        with pytest.raises(UpstreamResponseError, match="bad_parameter"):
            await client.fetch_users_answers([1], START, END, 1)

    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.asyncio
@respx.mock
async def test_http_429_is_translated_to_quota_error_without_retry():
    route = respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        return_value=httpx.Response(429, json={"error_name": "rate_limit"})
    )
    sleeps = []
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, sleeps)
        with pytest.raises(QuotaExhaustedError, match="quota reasons"):
            await client.fetch_users_answers([1], START, END, 1)

    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.asyncio
@respx.mock
async def test_benchmark_user_answers_and_parent_question_batch():
    answer_route = respx.get("https://api.stackexchange.com/2.3/users/42;43/answers").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"answer_id": 10, "question_id": 1}],
                "has_more": False,
                "quota_remaining": 9,
            },
        )
    )
    question_route = respx.get("https://api.stackexchange.com/2.3/questions/1;2").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"question_id": 1}], "quota_remaining": 8},
        )
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        answers = await client.fetch_users_answers([42, 43, 42], START, END, 1)
        questions, quota = await client.fetch_questions_by_ids([1, 2, 1])

    assert answer_route.call_count == 1
    assert answer_route.calls[0].request.url.params["pagesize"] == "100"
    assert answer_route.calls[0].request.url.params["fromdate"] == str(int(START.timestamp()))
    assert answer_route.calls[0].request.url.params["todate"] == str(int(END.timestamp()) - 1)
    assert question_route.call_count == 1
    assert answers.items[0]["answer_id"] == 10
    assert questions[0]["question_id"] == 1
    assert quota == 8


@pytest.mark.asyncio
@respx.mock
async def test_provider_backoff_is_applied_before_next_request():
    route = respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
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
        await client.fetch_users_answers([1], START, END, 1)
        await client.fetch_users_answers([1], START, END, 2)

    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 2


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_quota_blocks_followup_without_http_call():
    route = respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        return_value=httpx.Response(
            200, json={"items": [], "has_more": False, "quota_remaining": 0}
        )
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        await client.fetch_users_answers([1], START, END, 1)
        with pytest.raises(QuotaExhaustedError):
            await client.fetch_users_answers([1], START, END, 2)

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_malformed_response_is_rejected():
    respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        return_value=httpx.Response(200, json={"not_items": []})
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        with pytest.raises(UpstreamResponseError, match="invalid shape"):
            await client.fetch_users_answers([1], START, END, 1)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_wrapper_metadata_is_rejected():
    respx.get("https://api.stackexchange.com/2.3/users/1/answers").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "has_more": False, "quota_remaining": "not-a-number"},
        )
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        with pytest.raises(UpstreamResponseError, match="invalid shape"):
            await client.fetch_users_answers([1], START, END, 1)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_all_time_member_is_rejected():
    respx.get("https://api.stackexchange.com/2.3/tags/python/top-answerers/all_time").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"score": 10, "post_count": 2}], "has_more": False},
        )
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        with pytest.raises(UpstreamResponseError, match="malformed tag score"):
            await client.fetch_all_time_top_answerers("python")


@pytest.mark.asyncio
@respx.mock
async def test_duplicate_all_time_member_is_rejected():
    item = {
        "user": {"user_id": 1, "display_name": "Ada"},
        "score": 10,
        "post_count": 2,
    }
    respx.get("https://api.stackexchange.com/2.3/tags/python/top-answerers/all_time").mock(
        return_value=httpx.Response(200, json={"items": [item, item], "has_more": False})
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        with pytest.raises(UpstreamResponseError, match="duplicate tag score user"):
            await client.fetch_all_time_top_answerers("python")


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


@pytest.mark.asyncio
@respx.mock
async def test_fetch_user_returns_identity_or_none():
    existing_route = respx.get("https://api.stackexchange.com/2.3/users/16923803").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": 16923803,
                        "display_name": "Example User",
                        "link": "https://stackoverflow.com/users/16923803/example-user",
                        "reputation": 123,
                    }
                ],
                "quota_remaining": 7,
            },
        )
    )
    missing_route = respx.get("https://api.stackexchange.com/2.3/users/999999999").mock(
        return_value=httpx.Response(200, json={"items": [], "quota_remaining": 6})
    )
    async with httpx.AsyncClient(base_url="https://api.stackexchange.com/2.3") as http_client:
        client = make_client(http_client, [])
        existing = await client.fetch_user(16923803)
        missing = await client.fetch_user(999999999)

    assert existing_route.called
    assert missing_route.called
    assert existing is not None
    assert existing.display_name == "Example User"
    assert missing is None
