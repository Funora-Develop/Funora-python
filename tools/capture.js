/**
 * Сборщик наблюдений для Funora. Работает в консоли браузера.
 *
 * Ничего не отправляет наружу: единственный адресат - локальный сервер,
 * поднятый tools/capture.py на 127.0.0.1. Сырой HTML уходит туда и там же
 * превращается в скелет; на диск он не попадает.
 *
 * Что умеет:
 *   funora.page('имя')  - отдать структуру страницы;
 *   funora.currency()   - собрать символы валют без сумм;
 *   funora.watch()      - начать запись ФОРМЫ запросов (не значений);
 *   funora.stop()       - остановить запись и отдать собранное;
 *   funora.status()     - показать, что уже собрано.
 *
 * Значения не записываются нигде. Ни тела запроса, ни тела ответа, ни куки, ни
 * заголовки: сохраняются имена полей и ПОДПИСЬ значения - длина и состав
 * символов. Подпись устроена так же, как в скелете страницы, и обратного
 * преобразования у неё нет.
 *
 * Служебные слова в записи латинские нарочно. Принимающая сторона отвергает
 * наблюдение, в котором встретилось кириллическое слово: подписи кириллицы не
 * содержат, значит её появление означает, что куда-то просочился настоящий
 * текст. Свой словарь не должен ронять эту проверку.
 */

;(function () {
  'use strict'

  const ENDPOINT = '__FUNORA_ENDPOINT__'

  /**
   * Определяет класс одного символа.
   *
   * Классы те же, что в скелете страницы: d цифры, a латиница, c кириллица,
   * s пробельные, p пунктуация ASCII, o прочее.
   *
   * @param {string} ch Символ.
   * @returns {string} Односимвольный код класса.
   */
  function charClass(ch) {
    if (/\s/u.test(ch)) return 's'
    if (/\p{Nd}/u.test(ch)) return 'd'
    if (/[A-Za-z]/u.test(ch)) return 'a'
    if (ch >= 'Ѐ' && ch <= 'ӿ') return 'c'
    // eslint-disable-next-line no-control-regex
    if (/^[\x00-\x7f]$/u.test(ch) && !/[0-9A-Za-z]/u.test(ch)) return 'p'
    return 'o'
  }

  /**
   * Строит подпись значения: длину и состав, но не само значение.
   *
   * @param {string} text Исходное значение.
   * @returns {string} Подпись вида T14:dps либо пустая строка.
   */
  function signature(text) {
    const stripped = String(text === null || text === undefined ? '' : text).trim()
    if (!stripped) return ''
    const normalized = stripped.normalize('NFC')
    const classes = [...new Set([...normalized].map(charClass))].sort().join('')
    return `T${[...normalized].length}:${classes}`
  }

  /**
   * Обезличивает путь: сегменты с цифрами становятся номерами.
   *
   * Строка запроса сводится к перечню ИМЁН параметров - значения выбрасываются.
   *
   * @param {string} raw Исходный адрес.
   * @returns {object} Метод разбора: origin, path и имена параметров.
   */
  function maskUrl(raw) {
    let url
    try {
      url = new URL(raw, location.href)
    } catch {
      return { origin: '?', path: signature(raw), query: [] }
    }
    const path = url.pathname
      .split('/')
      .map((part) => (/[0-9]/.test(part) ? '{n}' : part))
      .join('/')
    return {
      origin: url.origin,
      path,
      query: [...url.searchParams.keys()].sort(),
    }
  }

  /**
   * Сводит тело запроса или ответа к форме: имена полей и подписи значений.
   *
   * @param {*} body Тело в любом виде: строка, FormData, URLSearchParams, объект.
   * @returns {object} Описание формы.
   */
  function shapeOf(body) {
    if (body === null || body === undefined || body === '') return { kind: 'empty' }

    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      const fields = {}
      for (const [key, value] of body.entries()) {
        fields[key] = typeof value === 'string' ? signature(value) : 'file'
      }
      return { kind: 'form', fields }
    }

    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) {
      const fields = {}
      for (const [key, value] of body.entries()) fields[key] = signature(value)
      return { kind: 'form', fields }
    }

    if (typeof body === 'string') {
      const text = body.trim()
      if (text.startsWith('{') || text.startsWith('[')) {
        try {
          return { kind: 'json', fields: shapeOfValue(JSON.parse(text)) }
        } catch {
          /* не JSON - разбирается ниже как форма либо как строка */
        }
      }
      if (text.includes('=')) {
        const fields = {}
        for (const [key, value] of new URLSearchParams(text).entries()) {
          fields[key] = signature(value)
        }
        return { kind: 'form', fields }
      }
      return { kind: 'string', value: signature(text) }
    }

    if (typeof body === 'object') return { kind: 'json', fields: shapeOfValue(body) }
    return { kind: typeof body }
  }

  /**
   * Сводит значение JSON к форме, сохраняя ключи и теряя значения.
   *
   * @param {*} value Значение любого вида.
   * @param {number} depth Текущая глубина, чтобы не уйти в бесконечность.
   * @returns {*} Значение той же формы, где строки заменены подписями.
   */
  function shapeOfValue(value, depth) {
    const level = depth || 0
    if (level > 6) return 'deeper-than-six'
    if (value === null) return null
    if (Array.isArray(value)) {
      return value.length === 0 ? [] : [shapeOfValue(value[0], level + 1), `...of ${value.length}`]
    }
    if (typeof value === 'object') {
      const out = {}
      for (const key of Object.keys(value).sort()) out[key] = shapeOfValue(value[key], level + 1)
      return out
    }
    if (typeof value === 'string') return signature(value)
    if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float'
    return typeof value
  }

  /**
   * Оставляет только ИМЕНА заголовков и выбрасывает опасные вовсе.
   *
   * Куки и заголовок авторизации не попадают в запись даже именем: их наличие
   * очевидно, а упоминание соблазняет однажды записать и значение.
   *
   * @param {*} headers Заголовки в любом виде.
   * @returns {string[]} Имена заголовков в нижнем регистре, по алфавиту.
   */
  function headerNames(headers) {
    const forbidden = new Set(['cookie', 'set-cookie', 'authorization', 'proxy-authorization'])
    let names = []
    if (!headers) return names
    if (typeof Headers !== 'undefined' && headers instanceof Headers) {
      names = [...headers.keys()]
    } else if (Array.isArray(headers)) {
      names = headers.map((pair) => pair[0])
    } else if (typeof headers === 'object') {
      names = Object.keys(headers)
    }
    return names
      .map((one) => String(one).toLowerCase())
      .filter((one) => !forbidden.has(one))
      .sort()
  }

  const recorded = []
  let watching = false

  /**
   * Записывает одно обращение к сети в виде формы.
   *
   * @param {string} method Метод запроса.
   * @param {string} url Адрес запроса.
   * @param {*} headers Заголовки запроса.
   * @param {*} body Тело запроса.
   * @param {number} status Код ответа.
   * @param {string} responseHeaders Имена заголовков ответа, через запятую.
   * @param {string} responseBody Тело ответа.
   * @returns {void}
   */
  function record(method, url, headers, body, status, responseHeaders, responseBody) {
    if (!watching) return
    const where = maskUrl(url)
    if (where.origin !== location.origin) return
    recorded.push({
      method: String(method || 'GET').toUpperCase(),
      origin: where.origin,
      path: where.path,
      query: where.query,
      request_headers: headerNames(headers),
      request: shapeOf(body),
      status,
      response_headers: responseHeaders,
      response: shapeOf(responseBody),
    })
  }

  const nativeFetch = window.fetch
  window.fetch = async function (input, init) {
    const options = init || {}
    const url = typeof input === 'string' ? input : input && input.url
    const response = await nativeFetch.apply(this, arguments)
    if (watching) {
      try {
        const copy = response.clone()
        const text = await copy.text()
        record(
          options.method || (input && input.method) || 'GET',
          url,
          options.headers || (input && input.headers),
          options.body,
          response.status,
          headerNames(response.headers),
          text,
        )
      } catch {
        /* тело уже прочитано либо непрочитаемо - запись пропускается */
      }
    }
    return response
  }

  const nativeOpen = XMLHttpRequest.prototype.open
  const nativeSend = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__funora = { method, url }
    return nativeOpen.apply(this, arguments)
  }
  XMLHttpRequest.prototype.send = function (body) {
    const mine = this.__funora || {}
    this.addEventListener('load', function () {
      try {
        record(
          mine.method,
          mine.url,
          null,
          body,
          this.status,
          (this.getAllResponseHeaders() || '')
            .split('\n')
            .map((one) => one.split(':')[0].trim().toLowerCase())
            .filter(Boolean)
            .sort(),
          this.responseText,
        )
      } catch {
        /* ответ непрочитаем - запись пропускается */
      }
    })
    return nativeSend.apply(this, arguments)
  }

  /**
   * Отправляет собранное локальному серверу.
   *
   * @param {string} kind Вид наблюдения.
   * @param {string} name Имя, под которым его сохранить.
   * @param {*} payload Содержимое.
   * @returns {Promise<string>} Что ответил сервер.
   */
  async function send(kind, name, payload) {
    const response = await nativeFetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({ kind, name, payload }),
    })
    const text = await response.text()
    console.log(`funora: ${text}`)
    return text
  }

  window.funora = {
    /**
     * Отдаёт структуру страницы локальному серверу.
     *
     * @param {string} name Имя снимка, например chat-thread.logged.ru.
     * @returns {Promise<string>} Что ответил сервер.
     */
    page(name) {
      if (!name) throw new Error('нужно имя снимка, например funora.page("order.logged.ru")')
      const where = maskUrl(location.href)
      return send('page', name, {
        html: document.documentElement.outerHTML,
        where: {
          path: where.path,
          query: where.query,
          lang: document.documentElement.lang || '',
          title: signature(document.title),
        },
      })
    },

    /**
     * Собирает символы валют без сумм.
     *
     * Символ - не персональные данные, а сумма и заказ - да. Поэтому берётся
     * только сам знак, число его появлений и код валюты, если он рядом есть.
     *
     * @returns {Promise<string>} Что ответил сервер.
     */
    currency() {
      const found = {}
      const money = /(?:^|[\s ])([\d  .,]{1,20})\s*([^\s\d\w.,]{1,3})(?:$|[\s ])/gu
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      let node = walker.nextNode()
      while (node) {
        const text = node.nodeValue || ''
        let match = money.exec(text)
        while (match) {
          const symbol = match[2]
          if (!found[symbol]) found[symbol] = { count: 0, near: [] }
          found[symbol].count += 1
          const holder = node.parentElement
          if (holder && found[symbol].near.length < 3) {
            found[symbol].near.push({
              tag: holder.tagName.toLowerCase(),
              class: holder.className || '',
              attrs: [...holder.attributes]
                .map((one) => one.name)
                .filter((one) => one !== 'style')
                .sort(),
            })
          }
          match = money.exec(text)
        }
        node = walker.nextNode()
      }
      return send('currency', location.pathname.replace(/[^a-z]/gi, '-'), {
        page: maskUrl(location.href),
        lang: document.documentElement.lang || '',
        symbols: found,
      })
    },

    /**
     * Начинает запись формы запросов.
     *
     * @returns {string} Что делать дальше.
     */
    watch() {
      recorded.length = 0
      watching = true
      return 'запись идёт. Сделайте нужное действие и вызовите funora.stop("имя")'
    },

    /**
     * Останавливает запись и отдаёт собранное.
     *
     * @param {string} name Имя наблюдения, например send-message.
     * @returns {Promise<string>} Что ответил сервер.
     */
    stop(name) {
      watching = false
      if (!name) throw new Error('нужно имя, например funora.stop("send-message")')
      if (recorded.length === 0) return Promise.resolve('записывать нечего: обращений не было')
      return send('network', name, recorded.slice())
    },

    /**
     * Показывает, что уже записано.
     *
     * @returns {object} Состояние сборщика.
     */
    status() {
      return { watching, recorded: recorded.length, endpoint: ENDPOINT }
    },
  }

  console.log(
    '%cfunora%c собран. Команды: funora.page("имя"), funora.currency(), '
      + 'funora.watch(), funora.stop("имя")',
    'font-weight:bold',
    '',
  )
})()
