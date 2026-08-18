# Disclaimer

*The English text is authoritative. Русская версия - ниже.*

## Not affiliated with FunPay

Funora is an independent, community-built project. It is **not affiliated with, endorsed by,
sponsored by, or connected to FunPay** or any of its operators. The name "FunPay" is used
only to describe what this software interoperates with.

## It works against an interface that is not meant for it

There is no public, documented FunPay API. Funora talks to a private web interface: HTML
pages, form fields and internal JSON payloads that can change at any time, without notice
and without a version number. A change on the site can break Funora in ways that are not
always loud - a page may still load and simply return nothing.

Funora is built to detect that and fail visibly rather than quietly, but no such mechanism is
perfect.

## The risk is yours

Automating a marketplace you do not own carries real consequences:

- your account may be suspended, temporarily or permanently;
- funds and pending payouts may be frozen while that is resolved;
- a parsing failure at the wrong moment can mean an order not delivered, a message not
  answered, or a price set incorrectly.

The account is yours. The money is yours. **No disclaimer moves that risk anywhere else**,
and this software is provided without any warranty - see the [LICENSE](LICENSE).

## What the published rules say, and what they do not

We quote FunPay's own rules so you can check every claim rather than trust ours.

Source: **[funpay.com/trade/info](https://funpay.com/trade/info)** (RU) ·
**[funpay.com/en/trade/info](https://funpay.com/en/trade/info)** (EN)

- **1.9** - *"Advertising, spamming, mass mailing to users."* Sanction: temporary or
  permanent suspension of the account. This is why Funora has no broadcast or mass-messaging
  feature and will not accept one.
- **2.2.9** - *"Продажа товаров и услуг, связанных со спамом и массовой рассылкой сообщений."*
  (Selling goods and services related to spam and mass messaging.)
- **2.1.8** - *"The 'Automatic delivery' function must not be used for products that require
  communication or additional services."* If you build automatic delivery on Funora,
  respecting this is your responsibility: the framework cannot know what your product needs.
- **3.4.3** - *"Account suspension due to poor-quality services (for example, due to the
  seller using bots or other software prohibited by the game publisher)."* Note the actual
  subject of this clause: software prohibited **by the game publisher**, not automation of
  FunPay itself.

**We did not find any published clause that either permits or forbids automating FunPay
itself.** We will not claim otherwise in either direction. Part of FunPay's documentation is
reachable only by signed-in users and we have not reviewed it. Read the rules and the
agreement that apply to your own account, and decide for yourself.

## What Funora will not do

These are permanent non-goals, not features postponed to a later release:

- CAPTCHA solving or bypass;
- anti-detection or browser fingerprint spoofing;
- proxy rotation intended to evade rate limits or bans;
- mass messaging, broadcast helpers, or anything that makes spam convenient;
- automatic generation of reviews or ratings.

Pull requests implementing any of the above will be closed.

## Your credentials and other people's data

A FunPay session key is not a scoped token - it is your entire account. Funora is designed to
keep it out of logs, exception text and diagnostics, but the guarantee ends at the edges of
this project: a third-party HTTP client with debug logging enabled, an APM agent, or an
in-process plugin can all read it. See
[SECURITY.md](https://github.com/Funora-Develop/.github/blob/main/SECURITY.md).

This software also processes data belonging to people who never installed it - your buyers'
messages, names and order details. Handle it accordingly.

---

# Отказ от ответственности

*Английский текст является основным; русский приведён для удобства.*

## Проект не аффилирован с FunPay

Funora - независимый проект, разрабатываемый сообществом. Он **не аффилирован с FunPay, не
одобрен ею, не спонсируется и никак с ней не связан**. Название «FunPay» используется только
для описания того, с чем это ПО взаимодействует.

## Он работает с интерфейсом, который для этого не предназначен

Публичного документированного API у FunPay нет. Funora обращается к приватному
веб-интерфейсу: HTML-страницам, полям форм и внутренним JSON-ответам, которые могут
измениться в любой момент, без предупреждения и без номера версии. Изменение на сайте может
сломать Funora негромко - страница загрузится и просто вернёт пустоту.

Framework спроектирован так, чтобы обнаруживать это и падать заметно, а не молчать. Но
идеальных механизмов такого рода не бывает.

## Риск несёте вы

У автоматизации чужой площадки есть реальные последствия:

- аккаунт могут заблокировать, временно или навсегда;
- средства и незавершённые выплаты могут быть заморожены на время разбирательства;
- сбой парсинга в неудачный момент означает невыданный заказ, неотвеченное сообщение или
  неверно выставленную цену.

Аккаунт ваш. Деньги ваши. **Никакой отказ от ответственности не переносит этот риск на
кого-то другого**, и программа предоставляется без каких-либо гарантий - см. [LICENSE](LICENSE).

## Что говорят опубликованные правила, а что - нет

Цитируем правила FunPay, чтобы вы могли проверить каждое утверждение, а не верить нам на слово.

Источник: **[funpay.com/trade/info](https://funpay.com/trade/info)**

- **1.9** - «Реклама, спам, массовая рассылка пользователям.» Санкция: временная или
  постоянная блокировка аккаунта. Поэтому в Funora нет массовых рассылок и не будет.
- **2.2.9** - «Продажа товаров и услуг, связанных со спамом и массовой рассылкой сообщений.»
- **2.1.8** - «Функцию "Автоматическая выдача" запрещается использовать для товаров, при
  продаже которых требуется общение или предоставление дополнительных услуг.» Если строите
  автовыдачу на Funora, соблюдение этого - на вас: framework не знает, что требует ваш товар.
- **3.4.3** - «Блокировка аккаунта из-за некачественно оказываемой услуги (например, из-за
  использования продавцом бота или другого запрещённого издателем игры ПО).» Обратите
  внимание, о чём пункт: о ПО, запрещённом **издателем игры**, а не об автоматизации FunPay.

**Пункта, который прямо разрешал или прямо запрещал бы автоматизацию самой FunPay, в
опубликованных правилах мы не нашли.** Утверждать обратное - ни в ту, ни в другую сторону -
не будем. Часть документации площадки доступна только авторизованным пользователям, и мы её
не просматривали. Прочитайте правила и соглашение, применимые к вашему аккаунту, и решите сами.

## Чего Funora делать не будет

Это постоянные не-цели, а не отложенные функции:

- решение или обход CAPTCHA;
- антидетект и подмена отпечатка браузера;
- ротация прокси для обхода ограничений или блокировок;
- массовые рассылки и всё, что делает спам удобным;
- автоматическая генерация отзывов и оценок.

Pull request с любым из перечисленного будет закрыт.

## Ваши учётные данные и чужие данные

Сессионный ключ FunPay - это не токен с ограниченными правами, это доступ ко всему аккаунту.
Funora проектируется так, чтобы он не попадал в логи, текст исключений и диагностику, но
гарантия заканчивается на границе проекта: сторонний HTTP-клиент с включённым отладочным
логированием, APM-агент или плагин внутри процесса прочитают его. См.
[SECURITY.md](https://github.com/Funora-Develop/.github/blob/main/SECURITY.md).

Эта программа также обрабатывает данные людей, которые её не устанавливали, - сообщения,
имена и детали заказов ваших покупателей. Обращайтесь с ними соответственно.
