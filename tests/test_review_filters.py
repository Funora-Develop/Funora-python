"""Оценка относится к запросу и курсору, включая пустой ответ и перезапуск."""

import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from test_aclient import _AsyncFakeFetcher
from test_client import _observation
from test_reviews_pagination import WHEN, Transport

from funora import AsyncClient, Client, ReviewsCursor
from funora._budget import Budget
from funora._cursor import encode_cursor
from funora._engine import Pause, Submit
from funora._result import Completeness
from funora._reviews import parse_reviews_page
from funora.errors import CursorIncompatibleError, ProtocolChangedError, ValidationError


def filtered_page(name, *, position=None):
    html = (
        Path(__file__).parent / "fixtures/pages" / f"reviews-rating-{name}.guest.ru.skeleton.txt"
    ).read_text()
    html = re.sub(r'(name="user_id"[^>]*value=")[^"]*', r"\g<1>123", html)
    if position is not None:
        html = re.sub(r'(name="continue"[^>]*value=")[^"]*', rf"\g<1>{position}", html)
    return html


@pytest.mark.parametrize("name,count", [("first", 25), ("next", 25), ("end", 12)])
def test_observed_filtered_pages_preserve_query_scope(name, count):
    result = parse_reviews_page(filtered_page(name), WHEN, user_id="123", rating=5)
    assert len(result) == count
    assert all(row.rating.value == 5 for row in result.rows(accept_incomplete=True))
    if name == "end":
        assert result.completeness is Completeness.COMPLETE
        assert result.next_cursor is None
    else:
        assert result.reason == "more_rows_available"
        assert result.next_cursor.rating == 5
        assert result.next_cursor.user_id == "123"


def test_filtered_cursor_resumes_in_a_new_client_without_losing_rating():
    first_transport = Transport([_observation(filtered_page("first", position="first"))])
    with Client(transport=first_transport, budget=Budget(names=())) as client:
        saved = client.reviews.get("123", rating=5).next_cursor.to_token()
    assert first_transport.forms[0][:2] == (
        "/users/reviews",
        {"user_id": "123", "continue": "", "filter": "5"},
    )
    next_transport = Transport(
        [
            _observation(filtered_page("next", position="next")),
            _observation(filtered_page("end")),
        ]
    )
    with Client(transport=next_transport, budget=Budget(names=())) as client:
        result = client.reviews.get("123", rating=5, cursor=saved)
        end = client.reviews.get("123", rating=5, cursor=result.next_cursor)
        assert end.completeness is Completeness.COMPLETE
        assert end.next_cursor is None
        assert not client.engine._state.session_ever_valid
    assert [form[:2] for form in next_transport.forms] == [
        ("/users/reviews", {"user_id": "123", "continue": "first", "filter": "5"}),
        ("/users/reviews", {"user_id": "123", "continue": "next", "filter": "5"}),
    ]


async def test_async_rating_uses_identical_request_and_empty_result():
    class AsyncTransport(_AsyncFakeFetcher):
        async def submit(self, path, fields, headers):
            assert path == "/users/reviews"
            assert fields == {"user_id": "123", "continue": "", "filter": "1"}
            assert headers["X-Requested-With"] == "XMLHttpRequest"
            return await self.fetch(path)

    async with AsyncClient(
        transport=AsyncTransport([_observation(filtered_page("empty"))])
    ) as client:
        page = await client.reviews.get("123", rating=1)
        assert page.completeness is Completeness.COMPLETE
        assert page.reason == "empty_filtered_list"
        assert page.rows() == ()
        assert page.next_cursor is None


@pytest.mark.parametrize("rating", [0, 6, -1, True, False, 1.0, Decimal(5), "5", [], {}])
def test_invalid_rating_fails_before_network(rating):
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(ValidationError):
        client.reviews.get("123", rating=rating)
    assert transport.calls == 0
    with pytest.raises(CursorIncompatibleError):
        ReviewsCursor("123", "position", rating).to_token()


@pytest.mark.parametrize("user_id", [None, 123, True, [], "", "../123", "\ud800"])
def test_invalid_review_owner_fails_before_network(user_id):
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(ValidationError):
        client.reviews.get(user_id, rating=5)
    assert transport.calls == 0


@pytest.mark.parametrize("rating", range(1, 6))
def test_all_supported_ratings_round_trip(rating):
    original = ReviewsCursor("123", "e\u0301/+", rating)
    assert ReviewsCursor.from_token(original.to_token()) == original


@pytest.mark.parametrize("cursor_rating,rating", [(5, None), (None, 5), (1, 5), (5, 1)])
@pytest.mark.parametrize("saved", [False, True])
def test_filter_switch_cannot_reuse_cursor(cursor_rating, rating, saved):
    cursor = ReviewsCursor("123", "position", cursor_rating)
    error = CursorIncompatibleError if saved else ValidationError
    if saved:
        cursor = cursor.to_token()
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(error):
        client.reviews.get("123", rating=rating, cursor=cursor)
    assert transport.calls == 0


@pytest.mark.parametrize("cursor_rating,rating", [(True, 1), (5.0, 5), ("5", 5)])
def test_object_cursor_does_not_bypass_strict_rating_types(cursor_rating, rating):
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(ValidationError):
        client.reviews.get(
            "123", rating=rating, cursor=ReviewsCursor("123", "position", cursor_rating)
        )
    assert transport.calls == 0


@pytest.mark.parametrize("scope", ["0", "6", "05", "5.0", "true", "٥", " 5", "all"])
def test_unknown_serialized_scope_is_rejected(scope):
    token = encode_cursor("reviews.get", "123", "position", scope=scope)
    with pytest.raises(CursorIncompatibleError):
        ReviewsCursor.from_token(token)


@pytest.mark.parametrize("name", ["first", "end"])
def test_rows_with_another_rating_cannot_confirm_or_continue_selection(name):
    result = parse_reviews_page(filtered_page(name), WHEN, user_id="123", rating=1)
    assert result.completeness is Completeness.PARTIAL
    assert result.reason == "page_defects"
    assert any(defect.code == "review_filter_mismatch" for defect in result.defects)
    assert result.next_cursor is None


@pytest.mark.parametrize("echo", ["", "5", "1"])
def test_nonempty_filter_echo_must_match_the_request(echo):
    html = filtered_page("first")
    # В части ответов поля filter нет вовсе; вставка моделирует явное эхо.
    html = re.sub(r'<input[^>]*name="filter"[^>]*>', "", html)
    html = html.replace("</form>", f'<input name="filter" value="{echo}"></form>')
    page = parse_reviews_page(html, WHEN, user_id="123", rating=5)
    assert (page.next_cursor is not None) == (echo != "1")
    assert any(d.code == "pagination_filter_mismatch" for d in page.defects) == (echo == "1")


def test_filtered_server_position_must_advance():
    transport = Transport([_observation(filtered_page("next", position="repeated"))])
    with (
        Client(transport=transport) as client,
        pytest.raises(ProtocolChangedError, match="повторил"),
    ):
        client.reviews.get("123", rating=5, cursor=ReviewsCursor("123", "repeated", 5))


def test_invalid_unicode_cursor_is_rejected_before_form_submission():
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(ValidationError):
        client.reviews.get("123", rating=5, cursor=ReviewsCursor("123", "\ud800", 5))
    assert transport.calls == 0


async def test_async_resumes_a_saved_filtered_cursor():
    class AsyncTransport(_AsyncFakeFetcher):
        async def submit(self, path, fields, headers):
            assert fields == {"user_id": "123", "continue": "saved", "filter": "5"}
            return await self.fetch(path)

    saved = ReviewsCursor("123", "saved", 5).to_token()
    async with AsyncClient(
        transport=AsyncTransport([_observation(filtered_page("end"))])
    ) as client:
        page = await client.reviews.get("123", rating=5, cursor=saved)
    assert page.completeness is Completeness.COMPLETE
    assert len(page.rows()) == 12


def test_safe_retry_keeps_the_filter_and_position(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("funora._engine.monotonic", lambda: clock[0])
    attempts = 0
    with Client(transport=Transport([]), budget=Budget(names=())) as client:
        core = client.engine.read_reviews("123", rating=5, cursor=ReviewsCursor("123", "saved", 5))
        request = next(core)
        for _ in range(10):
            if isinstance(request, Submit):
                attempts += 1
                assert request.path == "/users/reviews"
                assert request.fields == {"user_id": "123", "continue": "saved", "filter": "5"}
                response = (
                    replace(_observation("limited"), status=429, retry_after_ms=1)
                    if attempts == 1
                    else _observation(filtered_page("end"))
                )
            else:
                assert isinstance(request, Pause)
                clock[0] += max(1, request.ms) / 1000
                response = None
            try:
                request = core.send(response)
            except StopIteration as complete:
                assert complete.value.completeness is Completeness.COMPLETE
                break
        else:
            pytest.fail("безопасное чтение не завершилось после успешного ответа")
    assert attempts == 2


@pytest.mark.parametrize("rating", [1, None])
@pytest.mark.parametrize(
    "mutation", ["blank", "missing", "duplicate", "filter_missing", "rows", "form"]
)
def test_empty_requires_an_unambiguous_positive_marker(rating, mutation):
    html = filtered_page("empty")
    if mutation == "blank":
        html = re.sub(r'(<p class="pb20">).*?(</p>)', r"\1 \2", html, flags=re.S)
    elif mutation == "missing":
        html = html.replace('class="pb20"', 'class="changed"')
    elif mutation == "duplicate":
        html += html
    elif mutation == "filter_missing":
        html = html.replace("reviews-filter", "changed-filter")
    elif mutation == "rows":
        html = html.replace("</body>", '<div class="review-item"></div></body>')
    else:
        html = html.replace("</body>", '<form class="dyn-table-form"></form></body>')
    with pytest.raises(ProtocolChangedError):
        parse_reviews_page(html, WHEN, user_id="123", rating=rating)


def test_observed_filtered_empty_does_not_infer_an_unfiltered_empty_profile():
    with pytest.raises(ProtocolChangedError):
        parse_reviews_page(filtered_page("empty"), WHEN, user_id="123")


def test_empty_page_still_requires_transport_integrity():
    reply = replace(_observation(filtered_page("empty")), declared_length=None)
    with Client(transport=Transport([reply])) as client:
        page = client.reviews.get("123", rating=1)
    assert page.completeness is Completeness.PARTIAL
    assert any(d.code == "integrity_unverified" for d in page.defects)
