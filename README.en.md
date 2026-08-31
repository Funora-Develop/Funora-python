<p align="center">
  <img src="https://raw.githubusercontent.com/Funora-Develop/.github/main/assets/funora-python.svg" width="76" height="76" alt="">
</p>

<h1 align="center">Funora for Python</h1>

<p align="center"><em>Reference implementation of the Funora contract.</em></p>

<p align="center">
  <img alt="status" src="https://img.shields.io/badge/status-draft-6E7681?style=flat-square">
  <img alt="pypi" src="https://img.shields.io/badge/pypi-not%20published-6E7681?style=flat-square">
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

There is no released package and nothing to install yet. The contract is not
stabilised and still changes.

Twenty-four operations work: seventeen reads and seven writes - sending text and images, marking a chat read, changing a lot price, raising offers, and activating or deactivating a lot.

**The guide lives in [docs/index.md](docs/index.md).** It builds into a site
(`mkdocs serve`) and is checked by the same run as the code: examples are parsed
by the interpreter, links are resolved, and every operation it mentions is looked
up on a real client.

## What this is

A Python SDK for FunPay. This is where the protocol is worked out first; the
other languages reimplement from the spec rather than porting this code line by
line.

```python
from funora import Client, EnvSecretProvider

# The secret comes from FUNORA_GOLDEN_KEY and never appears in the code.
with Client(EnvSecretProvider()) as client:
    page = client.orders.list()
    for order in page.rows():
        print(order.order_id, order.description_text)
```

The same asynchronously. Two facades, one core: the normative step order, the
retry policy, budget spending and the cursor rules are written once and handed to
both ready-made. Porting a bot comes down to `await`.

```python
from funora import AsyncClient, EnvSecretProvider

async with AsyncClient(EnvSecretProvider()) as client:
    page = await client.orders.list()
    for order in page.rows():
        print(order.order_id, order.description_text)
```

## What already works

| Operation | Returns |
|---|---|
| `client.orders.list()` | the sales list as reduced records |
| `client.orders.get(order_id)` | one order in full |
| `client.chats.list()` | the dialog list |
| `client.chats.thread(node_id)` | a conversation with message origin resolved |
| `client.chats.send_text(node_id, text)` | a send receipt carrying the outcome |
| `client.chats.mark_read(node_id)` | the chat marked as read |
| `client.chats.send_image(node_id, content, ...)` | an image in the chat |
| `client.lots.list_own(node_id)` | your own lots in a section, with offer ids |
| `client.lots.form(node_id, offer_id)` | a lot edit form, and whether the lot is listed |
| `client.lots.update_price(...)` | the lot with a new price and nothing else touched |
| `client.lots.promote(game_id, node_id)` | raising every offer in a section |
| `client.lots.activate(...)` | the lot shown in the listing |
| `client.lots.deactivate(...)` | the lot taken off the listing |
| `client.lots.showcase(user_id)` | a seller showcase, by section |
| `client.market.offers(node_id)` | the public listing of a section: rivals' prices and sellers |
| `client.market.snapshot(node_id)` | a snapshot of the listing, for comparing over time |
| `client.market.chips(node_id)` | the second marketplace: offers sold by quantity |
| `client.reviews.get(user_id)` | reviews |
| `client.account.get()` | the account identity |
| `client.account.refresh()` | the same, re-read |
| `client.account.health()` | whether the session is usable |
| `client.account.balance()` | balance and transactions |
| `client.account.capabilities()` | which of the declared capabilities are available |
| `client.catalog.categories()` | the marketplace sections |

There are two write operations, and each carries its own cost of getting it
wrong.

**Sending text** - [its own guide chapter](docs/guide/sending.md): a send has
three outcomes rather than two, and the third one, "unknown", is what the chapter
is about.

**Changing a price** - [the lots chapter](docs/guide/lots.md): the form is sent
back as it was read, exactly one field changes, and the previous price is written
to a durable journal before the request leaves. Without a state file the operation
refuses: the marketplace keeps no price history and offers no undo, so what the
price used to be is known only from our own record.

On top of that there is a bot layer, `funora.bot`. It provides an outbox you can
post to **from any thread** - a Telegram handler, say - while the actual sending
is done by the same thread that runs the watch loop. Calling directly from
another thread corrupts the outbound governor's count silently, so it is refused
out loud.

There is a second queue too - **a directory of files** - for a Telegram bot
started as a SEPARATE command: an in-memory queue is out of its reach entirely.
A command claimed by a process that then died is never sent again: its fate is
unknown, and a person decides about it. The whole picture is in the [bot
chapter](docs/guide/bot.md).

The public section listing is parsed too, but has no operation: the `Offer` model
requires an id, a price and a category, and the page carries none of the three.

Sending an image, marking read, paging chat history backwards, the section field
schema, the account transaction history, the order event feed, the interface
locale and two lot write operations - activate and deactivate - are declared by
the contract and not written: nobody has observed the requests the marketplace
makes for them. Calling them raises `NotImplementedOperationError` - a refusal
from Funora itself, not a built-in Python error.

The full list, with a reason on every row, lives in the registry at
`spec/conformance/not-implemented.yaml`. It is not a reference but a build
condition: an operation that is declared and silently absent does not pass the
gate.

## Observed, but not an operation

Three of the marketplace's write endpoints have been observed as **forms** - the
address and the fields are visible, but nobody has sent the request:

| Endpoint | What it is | What is missing |
|---|---|---|
| `POST /orders/refund` | refunding an order | the response |
| `POST /withdraw/withdraw` | withdrawing funds | the response; needs 2FA |
| `POST /users/transactions` | paging the account ledger | the meaning of `continue` |

A write operation that cannot tell success from refusal will not be added here:
it would report success always. Refunds and withdrawals are also irreversible and
both are about money.

## What the SDK cannot do, and why that is stated here

Sections like this are usually buried. It sits in plain view because everything
listed affects whether this library is worth taking today.

**It tells apart two order states out of however many exist.** It reads `paid`
and `closed`; refunds, disputes and rejections exist but never made it into a
snapshot, and we have seen no carriers for them. An order in a third state yields
an unobserved value - not the nearest match and not `unknown`. The latter would
claim we read the status and failed to recognise it, when in fact we did not read
it at all.

The practical consequence: a handler shaped like «if not `closed`, we owe
delivery» will behave on such an order in a way its author did not intend. Ask
about a specific state, and handle separately the case where the state was not
read.

And `paid` itself is not financial confirmation. It means the marketplace shows
the paid state in the sales section; it can be reversed after the fact, and it
does not replace your own check where the cost of being wrong is high.

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
- [docs/limits.md](docs/limits.md) - what Funora cannot do, and why code will not fix it.
- [docs/protocol-questions.md](docs/protocol-questions.md) - what remains open.
- [tests/fixtures/pages/README.md](tests/fixtures/pages/README.md) - the snapshot
  format and why publishing it is safe.

## The wider project

Funora is one contract implemented natively in several languages. You change the language,
not the mental model: `Client`, services, events, router and the error
taxonomy mean the same thing everywhere.

| Repository | What it is | Status |
|---|---|---|
| [Funora](https://github.com/Funora-Develop/Funora) | One contract, one set of test vectors, native SDKs per language. | `design` |
| [Funora-spec](https://github.com/Funora-Develop/Funora-spec) | The canonical contract every SDK implements. | `design` |
| [Funora-codegen](https://github.com/Funora-Develop/Funora-codegen) | Generates the boring, repetitive part of every SDK. | `design` |
| [Funora-conformance](https://github.com/Funora-Develop/Funora-conformance) | The test contract between languages. | `design` |
| [Funora-python](https://github.com/Funora-Develop/Funora-python) | Reference implementation of the Funora contract. | `draft` |
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

Snapshots of pages in states we do not have: an order in refund or dispute, an
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
