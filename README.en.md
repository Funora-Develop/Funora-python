<p align="center">
  <img src="https://raw.githubusercontent.com/Funora-Develop/.github/main/assets/funora-python.svg" width="76" height="76" alt="">
</p>

<h1 align="center">Funora для Python</h1>

<p align="center"><em>Reference implementation of the Funora contract.</em></p>

<p align="center">
  <img alt="status" src="https://img.shields.io/badge/status-draft-6E7681?style=flat-square">
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

## Status: `draft`

There is no released package and nothing to install yet. Three read operations
work; the contract is not stabilised and still changes.

## What this is

A Python SDK for FunPay. This is where the protocol is worked out first; the
other languages reimplement from the spec rather than porting this code line by
line.

```python
from funora import Client

with Client(secret) as client:
    page = client.orders.list()
    for order in page.rows():
        print(order.order_id, order.description_text)
```

## What already reads

| Operation | Returns |
|---|---|
| `client.orders.list()` | the order list as reduced records |
| `client.chats.list()` | the dialog list |
| `client.chats.thread(id)` | a conversation with message origin resolved |

Write operations are not implemented. This is a read-only SDK for now.

## What the SDK cannot do, and why that is stated here

Sections like this are usually buried. It sits in plain view because everything
listed affects whether this library is worth taking today.

**It does not answer whether an order is paid.** We have not observed how markup
classes map to statuses, so the status field is reported as unobserved rather
than as `unknown`. The latter would claim we read the status and failed to
recognise it, when in fact we did not read it at all.

**It gives neither a numeric amount nor an exact time.** There is no
machine-readable time on the orders page at all, and no currency was observed.
Only display text.

**It does not treat a chat message as proof of payment.** Even a correctly
identified platform message is not proof: it could belong to another order, be
stale, or follow a reversed payment. The platform itself warns about this as the
first message in every dialog. The sales list is the only source of truth.

**It does not page through long lists.** No pagination markup was observed, and
promising a cursor the adapter cannot produce is worse than promising nothing.

## How it works

Three decisions visible in the very first call.

**The result is a page, not a list.** Records come from `rows()`, and an
incomplete result requires an explicit `accept_incomplete=True`. An incomplete
list handed over silently is indistinguishable from a complete one, and the
caller will decide on data that is not there.

**Fields distinguish "empty" from "not observed".** `None` looks identical for
both, yet the decisions are opposite: an empty description needs no re-read, a
missing one suggests the markup changed. Reading `.value` on an unobserved field
raises instead of returning `None`.

**Mechanical parts are generated from the spec.** Errors, capabilities, retry
policies, the budget and the verdict-to-error table are not hand-written in any
of the six SDKs. The build fails when generated output falls behind its source.

More in [docs/architecture.md](docs/architecture.md).

## Protocol observations

The package ships `funora-observe`, the tool that produced every protocol fact
the specification rests on. It stores a structural skeleton of a page: full
markup, with text and attribute values replaced by signatures.

- [docs/observations.md](docs/observations.md) - what is established and how to
  verify it.
- [docs/protocol-questions.md](docs/protocol-questions.md) - what remains open.
- [tests/fixtures/pages/README.md](tests/fixtures/pages/README.md) - the snapshot
  format and why publishing it is safe.

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

Three things help most right now.

Snapshots of pages in states we do not have: orders in different statuses, an
unread dialog, a long list with pagination. Each one closes an item in
[docs/protocol-questions.md](docs/protocol-questions.md).

Review of [Funora-spec](https://github.com/Funora-Develop/Funora-spec): it is
verified by use, and the first attempt to apply it surfaced eighteen places where
it contradicted itself.

Implementing read operations against the extraction rules already written.

## Security

Never paste a session key, raw signed-in HTML or private chat contents into a public issue.
A FunPay session key is your entire account. Report privately through
[Security Advisories](https://github.com/Funora-Develop/Funora/security/advisories/new) and read
[SECURITY.md](https://github.com/Funora-Develop/.github/blob/main/SECURITY.md).

## License

[Apache-2.0](LICENSE) © Funora Contributors
