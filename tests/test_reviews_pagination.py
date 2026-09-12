"""Пагинация отзывов на обезличенных публичных ответах сервера."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_client import _FakeFetcher, _observation

from funora import AsyncClient, Client, ReviewsCursor
from funora._budget import Budget
from funora._engine import Fetch, Pause
from funora._reviews import parse_reviews_page
from funora.capabilities import Capability
from funora.errors import ProtocolChangedError, RateLimitedError, ValidationError
from funora.retry import RETRY_POLICIES

WHEN = datetime(2026, 9, 6, tzinfo=UTC)


def page(name):
    html = (
        Path(__file__).parent / "fixtures/pages" / f"reviews-{name}.guest.ru.skeleton.txt"
    ).read_text()
    return html.replace(
        'name="user_id" type="hidden" value="T7:d#1"', 'name="user_id" type="hidden" value="123"'
    )


class Transport(_FakeFetcher):
    def __init__(self, responses):
        super().__init__(responses)
        self.forms = []

    def submit(self, path, fields, headers):
        self.forms.append((path, fields, headers))
        return self.fetch(path)


def test_observed_pages_expose_opaque_cursor_and_terminal_state():
    first = parse_reviews_page(page("first"), WHEN, user_id="123")
    next_page = parse_reviews_page(page("next"), WHEN, user_id="123")
    last = parse_reviews_page(page("last"), WHEN, user_id="123")
    assert (len(first), len(next_page), len(last)) == (25, 25, 1)
    assert first.reason == next_page.reason == "more_rows_available"
    assert first.next_cursor and next_page.next_cursor
    # Скелет маскирует значения: сравнение живых курсоров здесь невозможно.
    assert last.next_cursor is None
    assert last.completeness.value == "complete"


def test_sync_continuation_posts_the_exact_form_and_does_not_authenticate_guest():
    transport = Transport([_observation(page("next"))])
    with Client(transport=transport, budget=Budget(names=())) as client:
        result = client.reviews.get("123", cursor=ReviewsCursor("123", "previous"))
        assert result.next_cursor
        assert transport.forms[0][0:2] == (
            "/users/reviews",
            {"user_id": "123", "continue": "previous", "filter": ""},
        )
        assert transport.forms[0][2]["X-Requested-With"] == "XMLHttpRequest"
        assert not client.engine._state.session_ever_valid


async def test_async_continuation_uses_same_core():
    from test_aclient import _AsyncFakeFetcher

    class AsyncTransport(_AsyncFakeFetcher):
        async def submit(self, path, fields, headers):
            assert fields["continue"] == "previous"
            return await self.fetch(path)

    async with AsyncClient(transport=AsyncTransport([_observation(page("last"))])) as client:
        result = await client.reviews.get("123", cursor=ReviewsCursor("123", "previous"))
        assert result.next_cursor is None
        assert len(result.rows()) == 1


@pytest.mark.parametrize(
    "cursor", [ReviewsCursor("456", "a"), ReviewsCursor("123", ""), ReviewsCursor("123", " ")]
)
def test_invalid_cursor_never_reaches_network(cursor):
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(ValidationError):
        client.reviews.get("123", cursor=cursor)
    assert transport.calls == 0


def test_same_cursor_and_wrong_owner_fail_explicitly():
    html = page("next")
    cursor = parse_reviews_page(html, WHEN).next_cursor
    transport = Transport([_observation(html)])
    with (
        Client(transport=transport) as client,
        pytest.raises(ProtocolChangedError, match="повторил"),
    ):
        client.reviews.get("123", cursor=cursor)
    with pytest.raises(ProtocolChangedError, match="другому продавцу"):
        parse_reviews_page(html, WHEN, user_id="456")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h.replace('name="continue"', 'name="missing"'),
        lambda h: h.replace('dyn-table-continue"', 'dyn-table-continue hidden"'),
        lambda h: h.replace("</form>", '<input name="continue" value="duplicate"/></form>'),
        lambda h: h.replace("</form>", '</form><form class="dyn-table-form"></form>'),
        lambda h: h.replace(
            'name="filter" type="hidden" value', 'name="filter" type="hidden" value="1" ignored'
        ),
    ],
)
def test_broken_pagination_cannot_be_claimed_complete(mutate):
    result = parse_reviews_page(mutate(page("next")), WHEN)
    assert result.completeness.value == "partial"
    assert result.defects
    assert result.next_cursor is None


def test_rate_limit_counts_each_http_attempt_once(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("funora._engine.monotonic", lambda: clock[0])
    monkeypatch.setitem(
        RETRY_POLICIES,
        "funora.transport.rate_limited",
        replace(RETRY_POLICIES["funora.transport.rate_limited"], max_attempts=4),
    )
    limit = RETRY_POLICIES["funora.transport.rate_limited"].max_attempts
    calls = 0
    with Client(transport=Transport([]), budget=Budget(names=())) as client:
        core = client.engine.fetch_ok(Capability.REVIEWS_GET, "/users/123/")
        request = next(core)
        with pytest.raises(RateLimitedError):
            while True:
                if isinstance(request, Fetch):
                    calls += 1
                    request = core.send(
                        replace(_observation("limited"), status=429, retry_after_ms=1)
                    )
                else:
                    assert isinstance(request, Pause)
                    clock[0] += max(1, request.ms) / 1000
                    request = core.send(None)
    assert calls == limit


def test_review_text_cannot_impersonate_a_block_page():
    html = page("next").replace("T19:cs", "captcha доступ запрещён maintenance")
    transport = Transport([_observation(html)])
    with Client(transport=transport, budget=Budget(names=())) as client:
        result = client.reviews.get("123", cursor=ReviewsCursor("123", "previous"))
        assert len(result) == 25
