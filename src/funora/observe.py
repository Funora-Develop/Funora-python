"""Инструмент наблюдения за протоколом.

Это не SDK и не его часть. Это инструмент, который отвечает на четыре вопроса,
без ответа на которые спецификацию нельзя перевести из состояния draft:

  1. Отдаёт ли канал обновлений устойчивую позицию, по которой можно переигрывать
     пропущенное. От ответа зависит, остаётся ли гарантия at-least-once или
     раздел о событиях переписывается вокруг сверки состояния.
  2. Есть ли структурный признак системного сообщения, отличный от текста. Если
     нет, покупатель может подделать событие оплаты и получить товар.
  3. Локализован ли интерфейс и меняются ли тексты системных сообщений при смене
     языка. Смена языка ломает распознавание событий, не ломая отпечаток
     страницы: отказ получается тихим.
  4. Как выглядит протухшая сессия, страница блокировки и страница проверки.

Что инструмент делает и чего не делает. Он выполняет одно чтение, классифицирует
ответ и сохраняет структурный скелет страницы вместе с описанием происхождения.
Он не выполняет операций записи, не сохраняет сырой HTML и не ходит по ссылкам
дальше запрошенной страницы.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ._classify import DEFAULT_IDENTITY_CSS, classify
from ._secret import EnvSecretProvider, FileSecretProvider, SecretNotFoundError, SecretProvider
from ._skeleton import SkeletonError, skeletonize
from ._transport import Fetcher, Observation, TransportSettings

__all__ = ["main", "observe", "build_provenance"]


def build_provenance(
    *,
    path: str,
    observation: Observation,
    verdict_cls: str,
    verdict_reason: str,
    provisional: bool,
    locale: str,
) -> dict[str, object]:
    """Собирает описание происхождения фикстуры.

    Описание отвечает на вопрос «откуда это взялось» через полгода, когда
    выяснится, что фикстура больше не соответствует странице. Без него
    единственный способ это узнать - снимать заново.

    Args:
        path (str): Запрошенный путь.
        observation (Observation): Результат обращения. Конечный URL записывается
            обязательно: без него нельзя понять, куда привёл редирект, и запрос
            английской версии выглядит успешным, хотя вернул русскую страницу.
        verdict_cls (str): Класс ответа по классификатору.
        verdict_reason (str): Причина решения классификатора.
        provisional (bool): Было ли решение принято непроверенной сигнатурой.
        locale (str): Локаль интерфейса, под которой сделано наблюдение.

    Returns:
        dict[str, object]: Описание, пригодное для сохранения рядом с фикстурой.
        Персональных данных и содержимого страницы не содержит.
    """
    return {
        "path": path,
        "final_url": observation.final_url,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "locale": locale,
        "http_status": observation.status,
        "redirects": observation.redirects,
        "content_length": observation.content_length,
        "elapsed_ms": observation.elapsed_ms,
        "classification": verdict_cls,
        "classification_reason": verdict_reason,
        "classification_provisional": provisional,
        "format": "structural-skeleton-v1",
        "note": (
            "Структурный скелет: текст заменён подписями, сегменты путей с "
            "идентификаторами обезличены. Сырой HTML не сохраняется."
        ),
    }


def observe(
    *,
    path: str,
    out_dir: Path,
    provider: SecretProvider,
    secret_name: str = "golden_key",
    identity_css: str | None = DEFAULT_IDENTITY_CSS,
    locale: str = "ru",
    settings: TransportSettings | None = None,
) -> int:
    """Выполняет одно наблюдение и сохраняет результат.

    Args:
        path (str): Путь страницы относительно базового адреса.
        out_dir (Path): Каталог для сохранения скелета и описания.
        provider (SecretProvider): Источник сессионного секрета.
        secret_name (str): Логическое имя секрета в источнике.
        identity_css (str | None): Селектор маркера вошедшего пользователя.
        locale (str): Локаль интерфейса, под которой делается наблюдение.
        settings (TransportSettings | None): Настройки транспорта.

    Returns:
        int: Код возврата процесса: 0 - страница получена и пригодна для разбора,
        2 - получена, но классифицирована как перехватчик или неизвестная,
        1 - обращение не удалось.
    """
    cfg = settings or TransportSettings()

    try:
        secret = provider.get(secret_name)
    except SecretNotFoundError as exc:
        print(f"секрет недоступен: {exc}", file=sys.stderr)
        return 1

    try:
        with Fetcher(secret, settings=cfg) as fetcher:
            obs = fetcher.fetch(path)
    except Exception as exc:
        print(f"обращение не удалось: {type(exc).__name__}", file=sys.stderr)
        return 1

    host = cfg.base_url.split("//", 1)[-1].split("/", 1)[0]
    verdict = classify(
        status=obs.status,
        final_url=obs.final_url,
        html=obs.html,
        expected_host=host,
        identity_css=identity_css,
    )

    try:
        skeleton = skeletonize(obs.html)
    except SkeletonError as exc:
        print(f"скелет не построен: {exc}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.strip("/").replace("/", "_") or "root"
    stem = f"{stem}.{locale}"

    (out_dir / f"{stem}.skeleton.txt").write_text(skeleton, encoding="utf-8")
    provenance = build_provenance(
        path=path,
        observation=obs,
        verdict_cls=str(verdict.cls),
        verdict_reason=verdict.reason,
        provisional=verdict.provisional,
        locale=locale,
    )
    (out_dir / f"{stem}.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"класс ответа:  {verdict.cls}")
    print(f"причина:       {verdict.reason}")
    if verdict.provisional:
        print(
            "ВНИМАНИЕ:      решение принято непроверенной сигнатурой, "
            "подтвердите вручную"
        )
    print(f"код HTTP:      {obs.status}, переходов: {obs.redirects}")
    print(f"скелет:        {out_dir / (stem + '.skeleton.txt')}")
    print(f"происхождение: {out_dir / (stem + '.provenance.json')}")

    return 0 if verdict.is_ok else 2


def main(argv: list[str] | None = None) -> int:
    """Точка входа командной строки.

    Args:
        argv (list[str] | None): Аргументы командной строки. По умолчанию
            берутся из sys.argv.

    Returns:
        int: Код возврата процесса.
    """
    parser = argparse.ArgumentParser(
        prog="funora-observe",
        description=(
            "Однократное наблюдение за страницей площадки. Сохраняет структурный "
            "скелет, сырой HTML не сохраняется никогда."
        ),
    )
    parser.add_argument("path", help="путь страницы, например /orders/trade")
    parser.add_argument("--out", type=Path, default=Path("observations"), help="каталог результата")
    parser.add_argument(
        "--secret-file",
        type=Path,
        default=None,
        help="каталог с файлами секретов; по умолчанию секрет читается из окружения",
    )
    parser.add_argument("--secret-name", default="golden_key", help="имя секрета")
    parser.add_argument(
        "--identity-css",
        default=DEFAULT_IDENTITY_CSS,
        help="селектор маркера вошедшего пользователя",
    )
    parser.add_argument("--locale", default="ru", help="локаль интерфейса наблюдения")
    parser.add_argument(
        "--base-url", default=TransportSettings().base_url, help="базовый адрес"
    )

    args = parser.parse_args(argv)

    provider: SecretProvider
    if args.secret_file is not None:
        provider = FileSecretProvider(args.secret_file)
    else:
        provider = EnvSecretProvider()

    settings = TransportSettings(base_url=args.base_url)
    return observe(
        path=args.path,
        out_dir=args.out,
        provider=provider,
        secret_name=args.secret_name,
        identity_css=args.identity_css,
        locale=args.locale,
        settings=settings,
    )


if __name__ == "__main__":
    raise SystemExit(main())
