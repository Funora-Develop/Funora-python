<p align="center">
  <img src="https://raw.githubusercontent.com/Funora-Develop/.github/main/assets/funora-python.svg" width="76" height="76" alt="">
</p>

<h1 align="center">Funora для Python</h1>

<p align="center"><em>Эталонная реализация контракта Funora.</em></p>

<p align="center">
  <img alt="status" src="https://img.shields.io/badge/status-design-6E7681?style=flat-square">
  <img alt="pypi" src="https://img.shields.io/badge/pypi-funora%20reserved-3B6FA0?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-2F7D95?style=flat-square">
  <img alt="FunPay" src="https://img.shields.io/badge/FunPay-unofficial-B4501E?style=flat-square">
</p>

<p align="center"><a href="README.en.md">English</a></p>

---

> **Неофициальный проект.** Funora не аффилирована с FunPay, не одобрена ею и никак с ней не связана.
> Работает с приватным веб-интерфейсом, который может измениться в любой момент без предупреждения.
> Использование может привести к блокировке аккаунта и заморозке средств - этот риск несёте вы.
> Прочитайте [DISCLAIMER.md](DISCLAIMER.md) прежде, чем строить на этом то, что приносит вам деньги.

## Статус: `design`

Репозиторий проектируется. Выпущенного пакета нет, устанавливать пока нечего.

## Что это

Асинхронный Python SDK и event-driven framework. Здесь протокол прорабатывается первым; остальные языки реализуют его заново по спецификации, а не портируют этот код построчно.

## Проект целиком

Funora - это один контракт, реализованный нативно на нескольких языках. Меняется язык,
но не ментальная модель: `Client`, сервисы, события, роутер, фильтры, middleware и
таксономия ошибок означают одно и то же везде.

| Репозиторий | Что это | Статус |
|---|---|---|
| [Funora](https://github.com/Funora-Develop/Funora) | Один контракт, один набор тестовых векторов, нативный SDK на каждый язык. | `design` |
| [Funora-spec](https://github.com/Funora-Develop/Funora-spec) | Канонический контракт, который реализует каждый SDK. | `design` |
| [Funora-codegen](https://github.com/Funora-Develop/Funora-codegen) | Генерирует скучную повторяющуюся часть каждого SDK. | `design` |
| [Funora-conformance](https://github.com/Funora-Develop/Funora-conformance) | Тестовый контракт между языками. | `design` |
| [Funora-python](https://github.com/Funora-Develop/Funora-python) | Эталонная реализация контракта Funora. | `design` |
| [Funora-javascript](https://github.com/Funora-Develop/Funora-javascript) | Исходник на TypeScript, на выходе JavaScript и декларации типов. | `planned` |
| [Funora-java](https://github.com/Funora-Develop/Funora-java) | Java SDK. | `planned` |
| [Funora-dotnet](https://github.com/Funora-Develop/Funora-dotnet) | .NET SDK. | `planned` |
| [Funora-cpp](https://github.com/Funora-Develop/Funora-cpp) | C++ SDK. | `planned` |
| [Funora-c](https://github.com/Funora-Develop/Funora-c) | C SDK - самый узкий контракт в проекте. | `planned` |
| [Funora-docs](https://github.com/Funora-Develop/Funora-docs) | Документация всех SDK из одного источника. | `design` |
| [Funora-examples](https://github.com/Funora-Develop/Funora-examples) | Сквозные примеры, которые реально прогоняет CI. | `planned` |

## Участие в разработке

Сначала прочитайте [CONTRIBUTING.md](https://github.com/Funora-Develop/.github/blob/main/CONTRIBUTING.md).

Сейчас полезнее всего не реализация, а наблюдения за протоколом, фикстуры и разбор спецификации.

## Безопасность

Никогда не вставляйте сессионный ключ, сырой HTML со страницы под авторизацией или содержимое
личной переписки в публичный issue. Сессионный ключ FunPay - это доступ ко всему аккаунту.
Сообщайте приватно через [Security Advisories](https://github.com/Funora-Develop/Funora/security/advisories/new),
подробности - в [SECURITY.md](https://github.com/Funora-Develop/.github/blob/main/SECURITY.md).

## Лицензия

[Apache-2.0](LICENSE) © Funora Contributors
