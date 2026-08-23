def test_every_public_error_is_catchable_by_the_common_handler() -> None:
    """Требует, чтобы всякая публичная ошибка ловилась общим перехватом.

    Вызывающий пишет ``except FunoraError`` вокруг работы с площадкой - так
    сказано в описании иерархии. Ошибка, выпадающая мимо, роняет не операцию, а
    весь его процесс.

    Так и было с SecretNotFoundError: она наследовалась от RuntimeError, при
    этом экспортировалась в funora.__all__ и возникала на СБОРКЕ клиента.
    Пропавшая переменная окружения - обычное дело при переносе на другую машину.

    Проверка идёт по всему публичному перечню, а не по списку имён: список имён
    отстал бы от него молча.

    Returns:
        None
    """
    import funora
    from funora.errors import FunoraError

    public = [
        getattr(funora, name)
        for name in funora.__all__
        if isinstance(getattr(funora, name, None), type)
    ]
    errors = [one for one in public if issubclass(one, BaseException)]
    assert errors, "в публичном перечне не нашлось ни одного класса ошибки"

    stray = sorted(one.__name__ for one in errors if not issubclass(one, FunoraError))
    assert not stray, (
        f"публичные ошибки мимо общего перехвата: {stray}. Вызывающий пишет "
        "except FunoraError вокруг работы с площадкой, и выпавшая мимо роняет "
        "не операцию, а весь его процесс"
    )


def test_the_typing_marker_is_shipped() -> None:
    """Требует маркер типизации, раз он обещан классификатором.

    pyproject объявляет «Typing :: Typed». Без файла py.typed рядом с пакетом
    подсказки типов не видны потребителю: mypy и pyright молча считают пакет
    нетипизированным, и обещание классификатора остаётся обещанием.

    Returns:
        None
    """
    from pathlib import Path

    import funora

    marker = Path(funora.__file__).parent / "py.typed"
    assert marker.is_file(), (
        "нет файла py.typed, при том что pyproject объявляет Typing :: Typed. "
        "Потребитель не увидит подсказок типов, а классификатор говорит, что "
        "увидит"
    )
