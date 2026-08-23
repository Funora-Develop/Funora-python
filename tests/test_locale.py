"""Проверяет наблюдение локали интерфейса.

Локаль привязана к аккаунту, а не к адресу: запрос с префиксом /en/ отдаёт ту
же страницу на том же языке. Переключить её запросом нельзя, и единственное,
что остаётся, - узнать, какая она.

Прежде не узнавали вовсе. Страница на чужой локали разбиралась молча, и
вызывающий получал английские тексты, полагая их русскими.
"""

from __future__ import annotations

from pathlib import Path

from funora._extract import observe_locale
from funora.capabilities import Capability, CapabilityState
from funora.contract import SUPPORTED_LOCALES

#: Каталог со снимками страниц.
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"


def _page(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (PAGES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_a_masked_locale_is_not_passed_off_as_a_locale() -> None:
    """Требует, чтобы подпись вместо метки давала ненаблюдённое значение.

    Прежде эта проверка утверждала обратное: «значение в снимке замаскировано,
    но само наличие атрибута наблюдено, и этого довольно». Довольно не было.
    Разбор объявлял локаль прочитанной, значением подписи, и дальше вело так:
    подпись не совпадала ни с одной объявленной локалью, возможность
    protocol.locale переводилась в неподдержанную, а в журнал уходило
    предупреждение об интерфейсе на локали «T2:a#1».

    То есть собственные снимки проекта заставляли его говорить неправду, и
    закрепляла это проверка, написанная под уже сломанное поведение.

    Returns:
        None
    """
    checked = 0
    for path in sorted(PAGES.glob("*.skeleton.txt")):
        name = path.name.split(".skeleton")[0]
        observed = observe_locale(_page(name))

        assert not observed.is_observed, (
            f"{name}: подпись выдана за локаль {observed.or_none()!r}. Возможность "
            "protocol.locale станет неподдержанной из-за собственной фикстуры"
        )
        assert observed.reason == "locale_not_a_language_tag", (
            f"{name}: причина названа как {observed.reason!r}, а узел с атрибутом в "
            "снимке есть - значит дело не в том, что его не нашли"
        )
        checked += 1

    assert checked, "снимков не нашлось - проверять нечего"


def test_a_real_language_tag_is_read() -> None:
    """Проверяет, что настоящая метка читается.

    Без этой проверки предыдущая ничего не значила бы: чтение, отвергающее
    всякое значение, тоже отвергает подпись.

    Returns:
        None
    """
    for tag in ("ru", "en", "ru-RU", "zh-Hans-CN"):
        observed = observe_locale(f'<html lang="{tag}"></html>')
        assert observed.or_none() == tag, f"метка {tag!r} не прочиталась: {observed}"


def test_a_page_without_the_attribute_says_so() -> None:
    """Проверяет, что отсутствие атрибута не выдаётся за пустую локаль.

    Три исхода чтения атрибута различаются и здесь: селектор не нашёл узла,
    узел есть - атрибута нет, атрибут есть и пуст.

    Returns:
        None
    """
    absent = observe_locale("<html><body></body></html>")
    assert not absent.is_observed, "страница без атрибута выдала локаль"
    assert absent.reason == "selector_no_match:locale", (
        f"причина отсутствия названа как {absent.reason!r} - вызывающему не по "
        "чему понять, чего именно не нашлось"
    )

    # Пустой атрибут - это ФАКТ О СТРАНИЦЕ, а не наше незнание: площадка
    # отдала пустую локаль. Сводить его с отсутствием значило бы отбирать у
    # вызывающего единственный способ их различить.
    empty = observe_locale('<html lang=""></html>')
    assert empty.is_observed, "пустой атрибут выдан за отсутствующий"
    assert empty.or_none() == ""


def test_a_foreign_locale_does_not_refuse_the_page() -> None:
    """Проверяет, что чужая локаль не отменяет чтение.

    Разбор структурный: он опирается на классы разметки, а не на текст, и от
    смены языка не ломается. Отказать из-за локали значило бы отвергнуть
    страницу, которую реализация читает целиком и верно.

    Returns:
        None
    """
    from funora._budget import Budget
    from funora._engine import Engine
    from funora._transport import TransportSettings

    engine = Engine(TransportSettings(), Budget())
    engine.note_locale('<html lang="de"><body></body></html>')

    assert engine._state.locale.value == "de", "локаль не запомнилась"
    assert engine._state.capabilities[Capability.PROTOCOL_LOCALE] is CapabilityState.UNSUPPORTED, (
        "чужая локаль не опустила возможность protocol.locale"
    )


def test_a_declared_locale_keeps_the_capability() -> None:
    """Проверяет обратную половину: объявленная локаль возможность не роняет.

    Опускающий на всём подряд неотличим от неработающего.

    Returns:
        None
    """
    from funora._budget import Budget
    from funora._engine import Engine
    from funora._transport import TransportSettings

    engine = Engine(TransportSettings(), Budget())
    engine.note_locale(f'<html lang="{SUPPORTED_LOCALES[0]}"><body></body></html>')

    assert engine._state.capabilities[Capability.PROTOCOL_LOCALE] is CapabilityState.SUPPORTED
