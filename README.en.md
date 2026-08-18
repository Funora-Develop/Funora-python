<p align="center">
  <img src="https://raw.githubusercontent.com/Funora-Develop/.github/main/assets/funora-python.svg" width="76" height="76" alt="">
</p>

<h1 align="center">Funora для Python</h1>

<p align="center"><em>Reference implementation of the Funora contract.</em></p>

<p align="center">
  <img alt="status" src="https://img.shields.io/badge/status-design-6E7681?style=flat-square">
  <img alt="pypi" src="https://img.shields.io/badge/pypi-funora%20reserved-3B6FA0?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-2F7D95?style=flat-square">
  <img alt="FunPay" src="https://img.shields.io/badge/FunPay-unofficial-B4501E?style=flat-square">
</p>

<p align="center"><a href="README.md">Русский</a></p>

---

> **Unofficial project.** Funora is not affiliated with, endorsed by, or connected to FunPay.
> It works against a private web interface that can change at any time without notice.
> Using it may lead to your account being suspended and your funds frozen - that risk is yours.
> Read [DISCLAIMER.md](DISCLAIMER.md) before relying on this for anything that earns you money.

## Status: `design`

This repository is being designed. There is no released package and nothing to install yet.

## What this is

Async-first Python SDK and event-driven framework. This is where the protocol is worked out first; the other languages reimplement from the spec rather than porting this code line by line.

## The wider project

Funora is one contract implemented natively in several languages. You change the language,
not the mental model: `Client`, services, events, router, filters, middleware and the error
taxonomy mean the same thing everywhere.

| Repository | What it is | Status |
|---|---|---|
| [Funora](https://github.com/Funora-Develop/Funora) | One contract, one set of test vectors, native SDKs per language. | `design` |
| [Funora-spec](https://github.com/Funora-Develop/Funora-spec) | The canonical contract every SDK implements. | `design` |
| [Funora-codegen](https://github.com/Funora-Develop/Funora-codegen) | Generates the boring, repetitive part of every SDK. | `design` |
| [Funora-conformance](https://github.com/Funora-Develop/Funora-conformance) | The test contract between languages. | `design` |
| [Funora-python](https://github.com/Funora-Develop/Funora-python) | Reference implementation of the Funora contract. | `design` |
| [Funora-javascript](https://github.com/Funora-Develop/Funora-javascript) | TypeScript source, JavaScript and type declarations on output. | `planned` |
| [Funora-java](https://github.com/Funora-Develop/Funora-java) | Java SDK. | `planned` |
| [Funora-dotnet](https://github.com/Funora-Develop/Funora-dotnet) | .NET SDK. | `planned` |
| [Funora-cpp](https://github.com/Funora-Develop/Funora-cpp) | C++ SDK. | `planned` |
| [Funora-c](https://github.com/Funora-Develop/Funora-c) | C SDK - the narrowest contract in the project. | `planned` |
| [Funora-docs](https://github.com/Funora-Develop/Funora-docs) | Documentation for every SDK, from one source. | `design` |
| [Funora-examples](https://github.com/Funora-Develop/Funora-examples) | End-to-end examples that CI actually runs. | `planned` |

## Contributing

Read [CONTRIBUTING.md](https://github.com/Funora-Develop/.github/blob/main/CONTRIBUTING.md) first.

The most useful contributions right now are protocol observations, fixtures and
specification review - not implementation.

## Security

Never paste a session key, raw signed-in HTML or private chat contents into a public issue.
A FunPay session key is your entire account. Report privately through
[Security Advisories](https://github.com/Funora-Develop/Funora/security/advisories/new) and read
[SECURITY.md](https://github.com/Funora-Develop/.github/blob/main/SECURITY.md).

## License

[Apache-2.0](LICENSE) © Funora Contributors
