"""Разбор каталога с корня площадки.

Страница крупная и ловушек в ней больше, чем на любой другой из наблюдённых.
Разобрана пятьюдесятью одним независимым чтением; из сорока семи утверждений об
адресуемости устояло тринадцать. Ниже - те решения, которые из этого вышли.

ЧИТАЕТСЯ ТОЛЬКО ОСНОВНОЙ КАТАЛОГ. Списков на странице два, и все восемь карточек
избранного повторяют карточки основного - пересечение восемь из восьми. Обход по
документу удвоил бы восемь игр, молча, при полноте и нуле повреждений.

ВАРИАНТЫ ИГРЫ НЕ ОТБРАСЫВАЮТСЯ. Заголовков 872 при 842 карточках: четырнадцать
карточек несут по нескольку, и лишние помечены классом hidden. Это настоящие
варианты - у каждого свой идентификатор и свой список разделов. Класс hidden
говорит, какой вариант показан переключателем сейчас, а не что узел служебный.

СПИСОК РАЗДЕЛОВ СОПОСТАВЛЯЕТСЯ С ЗАГОЛОВКОМ ПО data-id, а не по соседству.
Порядок детей карточки - сначала все заголовки, потом переключатель, потом все
списки; соседство держится лишь в 836 случаях из 872.

ГЛАВНЫЙ РАЗДЕЛ ПОМЕЧАЕТСЯ, А НЕ ВЫБРАСЫВАЕТСЯ. Адрес заголовка игры совпадает с
адресом первой ссылки её списка - 864 раза из 864. Выброшенный, он унёс бы с
собой сведение о том, какой раздел у игры главный.

ВИД РАЗДЕЛА ЧИТАЕТСЯ ИЗ АДРЕСА, и это наблюдение, а не удобство: у самого узла
ссылки нет ни класса, ни data-атрибута, и структурного признака вида не
существует.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import SELECTORS

__all__ = ["CatalogGame", "CatalogPage", "CatalogSection", "parse_catalog"]

_CATALOG: Final[str] = SELECTORS["catalog.lists.catalog"]
_FAVOURITES: Final[str] = SELECTORS["catalog.lists.favourites"]
_LETTER_GROUP: Final[str] = SELECTORS["catalog.lists.letter_groups"]

_CARD: Final[str] = SELECTORS["catalog.game.card"]
_CARD_IN_LIST: Final[str] = SELECTORS["catalog.game.card.within_list"]
_TITLE_IN_CARD: Final[str] = SELECTORS["catalog.game.title.within_card"]
_TITLE: Final[str] = SELECTORS["catalog.game.title"]
_SWITCHER: Final[str] = SELECTORS["catalog.game.switcher"]

_SECTION_LIST: Final[str] = SELECTORS["catalog.sections.list"]
_LIST_IN_CARD: Final[str] = SELECTORS["catalog.sections.list.within_card"]
_SECTION_LINK: Final[str] = SELECTORS["catalog.sections.link"]

_ALPHABET: Final[str] = SELECTORS["catalog.totality.alphabet_index"]

#: Вид раздела по сегменту пути.
#:
#: Наблюдены два: /lots/ и /chips/. Структурного признака вида не существует - у
#: узла ссылки нет ни класса, ни data-атрибута, - и различать их можно только по
#: адресу.
_SECTION_KIND: Final[re.Pattern[str]] = re.compile(r"https?://[^/]+/([a-z]+)/([^/?#]+)/?")


@dataclass(frozen=True, slots=True)
class CatalogSection:
    """Раздел игры в каталоге.

    Attributes:
        href (Observed[str]): Адрес раздела целиком.
        kind (Observed[str]): Вид раздела из сегмента пути. Наблюдены lots и
            chips; смысла второму контракт не приписывает.
        section_id (Observed[str]): Идентификатор раздела из адреса.
        title_text (Observed[str]): Название раздела.
        is_main (bool): Совпадает ли адрес раздела с адресом самой игры.
        position (int): Место в списке, считая с нуля.
    """

    href: Observed[str]
    kind: Observed[str]
    section_id: Observed[str]
    title_text: Observed[str]
    is_main: bool
    position: int


@dataclass(frozen=True, slots=True)
class CatalogGame:
    """Игра либо один её вариант.

    Вариантов у карточки бывает несколько, и каждый - самостоятельная запись со
    своим идентификатором и своим списком разделов. Скрытые варианты
    отбрасывать нельзя: класс hidden говорит, какой из них показан
    переключателем сейчас.

    Attributes:
        game_id (Observed[str]): Идентификатор игры из атрибута.
        title_text (Observed[str]): Название игры.
        href (Observed[str]): Адрес главного раздела игры.
        is_shown (bool): Показан ли этот вариант переключателем сейчас.
        variant_index (int): Место варианта в карточке, считая с нуля.
        sections (tuple[CatalogSection, ...]): Разделы этого варианта.
    """

    game_id: Observed[str]
    title_text: Observed[str]
    href: Observed[str]
    is_shown: bool
    variant_index: int
    sections: tuple[CatalogSection, ...]


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """Результат чтения каталога.

    Attributes:
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        cards_total (int): Сколько карточек нашлось в основном каталоге, штук.
        games_total (int): Сколько игр и вариантов собрано, штук.
        sections_total (int): Сколько разделов собрано, штук.
        letter_groups (int): Сколько буквенных групп на странице, штук.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    completeness: Completeness
    reason: str
    observed_at: datetime
    cards_total: int
    games_total: int
    sections_total: int
    letter_groups: int
    defects: tuple[Defect, ...] = ()
    _games: tuple[CatalogGame, ...] = field(repr=False, default=())

    def games(self, *, accept_incomplete: bool = False) -> tuple[CatalogGame, ...]:
        """Возвращает игры каталога вместе с их вариантами.

        Args:
            accept_incomplete (bool): Признание того, что результат может быть
                неполным. Без него неполный результат не выдаётся.

        Returns:
            tuple[CatalogGame, ...]: Игры в порядке появления.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"результат неполон ({self.completeness}, причина: {self.reason}), "
                f"карточек {self.cards_total}, игр и вариантов {self.games_total}, "
                f"разделов {self.sections_total}, повреждений {len(self.defects)}. "
                "Передайте accept_incomplete=True, если готовы работать с неполными данными"
            )
        return self._games


def _text(node: Node | None, name: str) -> Observed[str]:
    """Извлекает текст узла как наблюдение.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _attribute(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Читает атрибут, различая три исхода.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя атрибута.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{field_name}")
    attributes = node.attributes or {}
    if name not in attributes:
        return Observed.missing(f"attribute_absent:{field_name}")
    value = (attributes.get(name) or "").strip()
    return Observed.present(value) if value else Observed.empty("")


def _section(link: Node, index: int, game_href: str | None) -> CatalogSection:
    """Собирает один раздел.

    Вид раздела читается из сегмента пути. Структурного признака вида не
    существует: у узла ссылки нет ни класса, ни data-атрибута.

    Args:
        link (Node): Узел ссылки.
        index (int): Место в списке, считая с нуля.
        game_href (str | None): Адрес самой игры, чтобы пометить главный раздел.

    Returns:
        CatalogSection: Раздел.
    """
    href = _attribute(link, "href", "section_href")
    kind: Observed[str] = Observed.missing("href_unreadable")
    section_id: Observed[str] = Observed.missing("href_unreadable")

    if href.is_observed:
        match = _SECTION_KIND.match(href.value)
        if match is None:
            kind = Observed.missing("section_href_shape_unknown")
            section_id = Observed.missing("section_href_shape_unknown")
        else:
            kind = Observed.present(match.group(1))
            section_id = Observed.present(match.group(2))

    return CatalogSection(
        href=href,
        kind=kind,
        section_id=section_id,
        title_text=_text(link, "section_title"),
        # Главный раздел ПОМЕЧАЕТСЯ, а не выбрасывается: выброшенный, он унёс бы
        # с собой сведение о том, какой раздел у игры главный.
        is_main=bool(game_href) and href.or_none() == game_href,
        position=index,
    )


def _card(card: Node) -> tuple[list[CatalogGame], list[Defect]]:
    """Собирает одну карточку - игру либо несколько её вариантов.

    Список разделов сопоставляется с заголовком ПО data-id, а не по соседству:
    порядок детей карточки - сначала все заголовки, потом переключатель, потом
    все списки, и соседство держится лишь у одновариантных карточек.

    Args:
        card (Node): Узел карточки.

    Returns:
        tuple[list[CatalogGame], list[Defect]]: Варианты и повреждения.
    """
    titles = card.css(_TITLE_IN_CARD)
    lists_by_id: dict[str, Node] = {}
    for node in card.css(_LIST_IN_CARD):
        key = ((node.attributes or {}).get("data-id") or "").strip()
        if key:
            lists_by_id[key] = node

    games: list[CatalogGame] = []
    defects: list[Defect] = []

    for index, title in enumerate(titles):
        game_id = _attribute(title, "data-id", "game_id")
        link = title.css_first("a")
        href = _attribute(link, "href", "game_href")

        section_list = lists_by_id.get(game_id.or_none() or "")
        if section_list is None:
            defects.append(
                Defect(
                    severity=Severity.ROW,
                    code="section_list_not_paired",
                    detail=(
                        f"варианту с идентификатором {game_id.or_none()!r} не нашлось "
                        "списка разделов с тем же идентификатором. Брать соседний "
                        "нельзя: порядок детей карточки этого не обещает"
                    ),
                )
            )
            sections: tuple[CatalogSection, ...] = ()
        else:
            sections = tuple(
                _section(one, at, href.or_none())
                for at, one in enumerate(section_list.css("li > a"))
            )

        games.append(
            CatalogGame(
                game_id=game_id,
                title_text=_text(link, "game_title"),
                href=href,
                # Класс hidden говорит, какой вариант показан переключателем
                # сейчас, а не что узел служебный.
                is_shown="hidden" not in ((title.attributes or {}).get("class") or "").split(),
                variant_index=index,
                sections=sections,
            )
        )

    return games, defects


def parse_catalog(html: str, observed_at: datetime) -> CatalogPage:
    """Разбирает каталог с корня площадки.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        CatalogPage: Игры каталога с их разделами.

    Raises:
        ProtocolChangedError: Если основного каталога на странице нет: пустой
            каталог вернуть нельзя, он неотличим от смены разметки.
    """
    tree = HTMLParser(html)
    catalog = tree.css_first(_CATALOG)

    if catalog is None:
        raise ProtocolChangedError(
            f"на корне площадки нет основного каталога ({_CATALOG}). "
            "Пустой каталог вернуть нельзя: он неотличим от смены разметки"
        )

    defects: list[Defect] = []
    cards = catalog.css(_CARD_IN_LIST)
    if not cards:
        raise ProtocolChangedError(
            "в каталоге нет ни одной карточки игры. Это изменение разметки, а не пустой каталог"
        )

    games: list[CatalogGame] = []
    for card in cards:
        found, card_defects = _card(card)
        games.extend(found)
        defects.extend(card_defects)

    # Избранное читается НЕ ради игр - все его карточки повторяют основной
    # каталог, - а ради проверки, что оно и вправду ничего не добавляет.
    favourites = tree.css_first(_FAVOURITES)
    if favourites is not None:
        known = {one.game_id.or_none() for one in games if one.game_id.is_observed}
        extra = {
            ((node.attributes or {}).get("data-id") or "").strip()
            for node in favourites.css(_TITLE_IN_CARD)
        } - known
        if extra:
            defects.append(
                Defect(
                    severity=Severity.PAGE,
                    code="favourites_add_games",
                    detail=(
                        f"в избранном {len(extra)} игр, которых нет в основном каталоге. "
                        "Прежде избранное повторяло его целиком, и читать его было незачем"
                    ),
                )
            )

    letter_groups = len(tree.css(_LETTER_GROUP))
    alphabet = tree.css_first(_ALPHABET)
    index_links = len(alphabet.css("a")) if alphabet is not None else 0

    # Полнота по ПОЛОЖИТЕЛЬНОМУ доводу: указатель по буквам не обещает ни одной
    # группы, которой на странице нет. Ненаходка кнопки догрузки доводом не
    # является и здесь не нужна.
    if alphabet is None:
        completeness, reason = Completeness.PARTIAL, "alphabet_index_missing"
    elif index_links != letter_groups:
        completeness, reason = Completeness.PARTIAL, "alphabet_index_disagrees"
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="alphabet_index_disagrees",
                detail=(
                    f"в указателе {index_links} ссылок, а групп на странице {letter_groups}: "
                    "указатель обещает то, чего на странице нет, либо наоборот"
                ),
            )
        )
    elif any(one.severity is Severity.PAGE for one in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    else:
        completeness, reason = Completeness.COMPLETE, "alphabet_index_agrees"

    return CatalogPage(
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        cards_total=len(cards),
        games_total=len(games),
        sections_total=sum(len(one.sections) for one in games),
        letter_groups=letter_groups,
        defects=tuple(defects),
        _games=tuple(games),
    )
