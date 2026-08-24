"""Словари извлечения: статусы заказа и присутствие контрагента.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/extraction/orders.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Носителей статуса два, и оба структурные: цветовой класс ячейки и
модификатор строки. Читать надо ОБА. В наблюдении они совпали во всех
восьми строках, и это свойство здесь используется как проверка: два
независимых носителя ловят переименование любого из них, а один -
меняет ответ молча.

Перечисления открытые. Носитель, которого нет в словаре, даёт
ненаблюдённое значение, а не unknown: unknown означал бы, что состояние
прочитано и не опознано, тогда как оно не прочитано вовсе.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "OrderStatus",
    "STATUS_BY_CELL_CLASS",
    "ROW_MARKER_BY_STATUS",
    "PRESENCE_BY_CLASS",
    "CURRENCY_BY_SYMBOL",
    "AMBIGUOUS_CURRENCY_SYMBOLS",
    "ATTRIBUTES",
    "SELECTORS",
    "SELECTOR_GROUPS",
]


class OrderStatus(StrEnum):
    """Состояние заказа, каким его показывает список продаж.

    Значение совпадает с именем состояния в спецификации: оно уходит в
    событие и в журнал, где обязано совпадать между всеми реализациями.
    """

    PAID = "paid"
    CLOSED = "closed"


#: Статус по цветовому классу ячейки.
STATUS_BY_CELL_CLASS: Final[dict[str, OrderStatus]] = {
    "text-primary": OrderStatus.PAID,  # Оплачен
    "text-success": OrderStatus.CLOSED,  # Закрыт
}

#: Модификатор строки для состояний, у которых он наблюдался.
#:
#: Носитель односторонний. Модификатор стоит у оплаченного заказа, а
#: закрытый узнаётся по его отсутствию - и отсутствие само по себе не
#: свидетельство: под ним с равным успехом лежит переименование класса.
#: Поэтому модификатор служит проверкой в одну сторону, а состояние
#: берётся из класса ячейки.
ROW_MARKER_BY_STATUS: Final[dict[OrderStatus, str]] = {
    OrderStatus.PAID: "info",  # Оплачен
}

#: Присутствие контрагента по классу карточки пользователя.
#:
#: Словарь закрыт по наблюдению, но не по умолчанию: класса, которого
#: здесь нет, достаточно, чтобы признак стал ненаблюдённым. Правило
#: «нет offline, значит online» запрещено - переименуй площадка класс, и
#: каждый контрагент молча стал бы присутствующим.
PRESENCE_BY_CLASS: Final[dict[str, bool]] = {
    "online": True,
    "offline": False,
}


#: Селекторы разбора, объявленные спецификацией.
#:
#: Прежде каждый из них жил в двух местах: объявлением в
#: spec/extraction и литералом в коде. Площадка меняет разметку -
#: правят один файл из двух, и расхождение молчит: проверки гоняют
#: разбор по снимкам, а текст спецификации с кодом не сверял никто.
#:
#: Ключ выведен из пути внутри документа: он однозначен и не
#: зависит от языка реализации.
SELECTORS: Final[dict[str, str]] = {
    "chats.contact_list.fields.counterparty_name": ".media-user-name",
    "chats.contact_list.fields.preview_text": ".contact-item-message",
    "chats.contact_list.fields.time_text": ".contact-item-time",
    "chats.contact_list.item": "a.contact-item",
    "chats.list": ".contact-list",
    "chats.message.container": ".chat-message-list",
    "chats.message.fields.author_link": "a.chat-msg-author-link",
    "chats.message.fields.author_name": "a.chat-msg-author-link",
    "chats.message.fields.body": ".chat-msg-body",
    "chats.message.fields.text": ".chat-msg-text",
    "chats.message.fields.time_text": ".chat-msg-date",
    "chats.message.item": ".chat-msg-item",
    "chats.sending.form": ".chat-form form",
    "chats.unread_badge": "span.badge-chat",
    "chats.widget": ".chat-contacts",
    "order.chat.full_chat_link": "a.chat-control[href]",
    "order.chat.messages": ".chat-msg-item",
    "order.chat.widget": ".chat.chat-float",
    "order.counterparty.account_flags": ".chat-header .media-user",
    "order.counterparty.link": ".chat-header .media-user-name a",
    "order.identity.amount_block": ".param-item:has(> div > span.h1)",
    "order.identity.category_link": '.param-item:has(a[href*="/lots/"])',
    "order.identity.category_link.attributes.category_href": '.param-item a[href*="/lots/"]',
    "order.identity.order_number": ".review-container",
    "order.identity.refund_available": 'form[action$="/orders/refund"]',
    "order.identity.status_carrier": "h1.page-header > span",
    "order.params.container": ".param-list",
    "order.params.item": ".param-item",
    "order.params.label": ".param-item > h5",
    "order.review.author": ".review-item-row[data-author]",
    "order.review.container": ".review-container",
    "orders.container": ".orders-table",
    "orders.container.header": ".tc-header",
    "orders.empty_list.filter_stays": "form.orders-filter",
    "orders.empty_list.marker": "p.lead",
    "orders.fields.amount_text": ".tc-price",
    "orders.fields.category": ".order-desc .text-muted",
    "orders.fields.counterparty_link": ".tc-user [data-href]",
    "orders.fields.counterparty_name": ".tc-user .media-user-name",
    "orders.fields.counterparty_online": ".tc-user .media-user",
    "orders.fields.currency_symbol_text": ".tc-price .unit",
    "orders.fields.description": ".order-desc > div:first-child",
    "orders.fields.order_number_text": ".tc-order",
    "orders.fields.seller_sum_text": ".tc-seller-sum",
    "orders.fields.time_ago_text": ".tc-date-left",
    "orders.fields.time_text": ".tc-date-time",
    "orders.row": "a.tc-item",
    "orders.rows_container": ".dyn-table-body",
    "reviews.fields.author_href": ".media-user-name a",
    "reviews.fields.author_name": ".media-user-name a",
    "reviews.fields.author_photo_href": ".review-item-photo a",
    "reviews.fields.date_text": ".review-item-date",
    "reviews.fields.detail_text": ".review-item-detail",
    "reviews.fields.order_href": ".review-item-order a",
    "reviews.fields.rating": ".review-item-rating .rating > div",
    "reviews.fields.text": ".review-item-text",
    "reviews.filter": ".reviews-filter",
    "reviews.filter.endpoint": 'form[action$="/users/reviews"]',
    "reviews.pagination.continue_button": "button.dyn-table-continue",
    "reviews.pagination.form": "form.dyn-table-form",
    "reviews.row": ".review-item",
    "reviews.row.wrapper": ".review-container",
    "reviews.rows_container": ".dyn-table-body",
    "reviews.table": ".dyn-table",
    "session.locale": "html[lang]",
    "updates.positions": "a.contact-item",
}


#: Имена атрибутов разметки, объявленные спецификацией.
#:
#: Имя атрибута - такой же договор с площадкой, как и селектор, и
#: жило оно ровно так же в двух местах: объявлением в
#: spec/extraction и литералом в коде. Площадка переименует
#: атрибут - правят один файл из двух, и расхождение молчит.
#:
#: Ключ выведен из пути внутри документа, как и у селекторов.
ATTRIBUTES: Final[dict[str, str]] = {
    "chats.contact_list.attributes.last_message_position": "data-node-msg",
    "chats.contact_list.attributes.node_id": "data-id",
    "chats.contact_list.attributes.own_position": "data-user-msg",
    "order.chat.full_chat_link.attributes.chat_href": "href",
    "order.chat.messages.attributes.message_id": "id",
    "order.chat.widget.attributes.node_id": "data-id",
    "order.chat.widget.attributes.tag": "data-tag",
    "order.counterparty.link.attributes.profile_href": "href",
    "order.identity.category_link.attributes.category_href": "href",
    "order.identity.order_number.attributes.order_number": "data-order",
    "order.review.author.attributes.author_id": "data-author",
    "order.review.container.attributes.rating": "data-rating",
    "order.review.container.attributes.rating_max": "data-rating-max",
    "reviews.fields.author_href.attributes.author_profile": "href",
    "reviews.fields.author_photo_href.attributes.author_photo": "href",
    "reviews.fields.order_href.attributes.order_link": "href",
    "session.locale.attribute": "lang",
    "updates.positions.attributes.last_message": "data-node-msg",
    "updates.positions.attributes.own": "data-user-msg",
}


#: Перечни селекторов, объявленные спецификацией.
#:
#: Порядок значим: признаки проверяются по очереди, и две
#: реализации, проверившие их в разном порядке, разойдутся на
#: странице, где признаки противоречат друг другу.
#:
#: Кортежем, а не ключами с индексом: вставка одного элемента в
#: середину перечня переставила бы все последующие ключи.
SELECTOR_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "chats.system_message.markers": (
        "a.chat-msg-author-link",
        ".chat-msg-body .alert",
        ".chat-msg-author-label",
    ),
    "orders.fields.status.carriers": (".tc-status",),
    "session.content_markers": (
        ".orders-table",
        ".chat-contacts",
        ".chat-message-list",
        ".contact-list",
        ".content-account",
    ),
    "session.markers.challenge": (
        "#challenge-form",
        ".g-recaptcha",
        ".h-captcha",
        'script[src*="captcha"]',
    ),
    "session.markers.challenge_widget_on_login": (
        ".cf-turnstile",
        "[data-sitekey]",
    ),
    "session.markers.guest": (
        ".navbar-toggle-guest",
        ".menu-item-login",
        ".menu-item-register",
        ".content-account-login",
        ".modal-auth",
    ),
    "session.markers.logged_in": (
        ".navbar-toggle-logged",
        "div.hidden[data-orders]",
        "span.badge",
    ),
    "session.markers.login_form": (
        'input[type="password"]',
        'form[action*="login"]',
        'form[action*="auth"]',
    ),
    "updates.tags.locations": (
        "div.hidden[data-orders]",
        ".chat.chat-float",
        ".param-item.chat-panel",
    ),
}


#: Код валюты по знаку, которым площадка выводит цену.
#:
#: Таблица наблюдена, а не выведена. У площадки переключатель
#: отображаемой валюты, и сбор в каждом положении показал, каким
#: знаком выводятся цены; сам переключатель отдал код в data-cy.
#:
#: Перечень закрытый. Знак вне таблицы кодом не становится:
#: придуманное соответствие приписало бы чужую валюту чужому
#: заказу молча, и заметил бы это не разработчик, а продавец.
CURRENCY_BY_SYMBOL: Final[dict[str, str]] = {
    "$": "USD",
    "€": "EUR",
    "₽": "RUB",
}

#: Знаки, которые на этой площадке носят несколько валют.
#:
#: Объявляются отдельно от отсутствия. Отсутствие означает «знака
#: не видели», неоднозначность - «видели, и он не решает».
AMBIGUOUS_CURRENCY_SYMBOLS: Final[frozenset[str]] = frozenset({})
