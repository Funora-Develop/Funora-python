/**
 * Сборщик наблюдений для Funora. Работает в консоли браузера.
 *
 * Ничего не отправляет наружу: единственный адресат - локальный сервер,
 * поднятый tools/capture.py на 127.0.0.1. Сырой HTML уходит туда и там же
 * превращается в скелет; на диск он не попадает.
 *
 * Что умеет:
 *   funora.page('имя')  - отдать структуру страницы;
 *   funora.currency('метка') - собрать символы валют без сумм;
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

  //: Отпечаток редакции сборщика. Подставляется приёмником при выдаче.
  const BUILD = '__FUNORA_BUILD__'

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
  /**
   * Отличает имя метода в адресе от идентификатора.
   *
   * Начинается со строчной буквы, состоит из одних латинских букв, содержит
   * заглавную внутри. Восемь заглавных подряд - номер заказа - под это не
   * подходит: он начинается с заглавной.
   *
   * @param {string} part Сегмент пути.
   * @returns {boolean} Правда, если это имя метода.
   */
  function isRouteName(part) {
    return /^[a-z][A-Za-z]{0,30}$/.test(part)
  }

  /**
   * Описывает форму значения, не раскрывая его.
   *
   * Длина, набор РАЗЛИЧНЫХ знаков пунктуации, наличие заглавных, строчных и
   * цифр. Букв не сохраняется вовсе, порядок знаков не сохраняется, повторы
   * схлопнуты: восстановить значение по такой мерке нельзя.
   *
   * @param {string} value Значение.
   * @returns {object} Мерка.
   */
  function shapeHint(value) {
    return {
      length: value.length,
      punctuation: [...new Set(value.replace(/[\p{L}\p{N}]/gu, ''))].sort().join(''),
      has_upper: /[A-Z]/.test(value),
      has_lower: /[a-z]/.test(value),
      has_digit: /[0-9]/.test(value),
    }
  }

  function maskUrl(raw) {
    let url
    try {
      url = new URL(raw, location.href)
    } catch {
      return { origin: '?', path: signature(raw), query: [] }
    }
    // Цифра либо заглавная буква. Идентификаторы площадки бывают вовсе без
    // цифр - восемь заглавных латинских букв, - и правило по одним цифрам
    // пропускало их дословно.
    //
    // ИСКЛЮЧЕНИЕ ДЛЯ ИМЕНИ МЕТОДА, заведено 24.08.2026 по наблюдению. Загрузка
    // изображения идёт на POST /file/<имя метода>, и имя это писано горбатым
    // письмом - заглавные внутри. Прежнее правило маскировало его целиком, и
    // операцию по такой записи собрать было нельзя.
    //
    // Расширение доказуемо узкое. Сегмент из одних строчных букв правило
    // пропускало и раньше: /orders/trade писался дословно. Меняется ровно одно
    // - заглавная ВНУТРИ сегмента, который начинается со строчной и не имеет
    // цифр. Ни один наблюдённый идентификатор площадки такой формы не имеет:
    // восемь цифр у человека, девять у диалога, восемь ЗАГЛАВНЫХ у номера
    // заказа - последний начинается с заглавной и остаётся замаскированным.
    const path = url.pathname
      .split('/')
      .map((part) => (isRouteName(part) || !/[0-9A-Z]/.test(part) ? part : '{n}'))
      .join('/')
    // Подсказка о том, ЧТО именно замаскировано. Без неё отказ записать
    // значение молчит о причине, и следующее решение принимается наугад.
    const hidden = url.pathname
      .split('/')
      .map((part, at) => ({ at, part }))
      .filter((one) => one.part !== '' && !isRouteName(one.part) && /[0-9A-Z]/.test(one.part))
      .map((one) => ({ at: one.at, ...shapeHint(one.part) }))
    const out = {
      origin: url.origin,
      path,
      query: [...url.searchParams.keys()].sort(),
    }
    if (hidden.length > 0) out.masked_segments = hidden
    return out
  }

  //: Поля, значение которых записывается ДОСЛОВНО.
  //
  // Это протокольные константы - имена действия и вида объекта. Без них
  // операцию не завести: подпись говорит, что там двенадцать знаков латиницы с
  // пунктуацией, а какое это действие - нет.
  //
  // Список короткий и закрытый нарочно, и сверх него стоит второе условие:
  // значение записывается только если это строчный идентификатор без цифр и
  // дефисов. Идентификатор диалога, имя пользователя и всякий токен такой
  // формы не имеют, и случайно проскочить не могут.
  //: Коды валют по ISO 4217, которые вправду могут встретиться.
  //
  // Перечень, а не «три заглавные подряд»: список продаж принёс так GTA, NBA,
  // XKO и MIR - сокращения игр, обрывок номера заказа и платёжная система.
  // Перечень неполон намеренно, и это верная сторона ошибки: пропущенный код
  // стоит одного повторного сбора, лишний - придуманной таблицы валют.
  const ISO = new Set([
    'AED', 'AMD', 'AUD', 'AZN', 'BGN', 'BRL', 'BYN', 'CAD', 'CHF', 'CNY',
    'CZK', 'DKK', 'EUR', 'GBP', 'GEL', 'HKD', 'HUF', 'IDR', 'ILS', 'INR',
    'JPY', 'KGS', 'KRW', 'KZT', 'MDL', 'MXN', 'NOK', 'NZD', 'PLN', 'RON',
    'RSD', 'RUB', 'SEK', 'SGD', 'THB', 'TJS', 'TRY', 'UAH', 'USD', 'UZS',
    'VND', 'ZAR',
  ])

  const CONSTANTS = new Set(['action', 'type'])
  const CONSTANT_SHAPE = /^[a-z][a-z_]{1,30}$/

  /**
   * Возвращает значение поля дословно, если это протокольная константа.
   *
   * @param {string} key Имя поля.
   * @param {*} value Значение поля.
   * @returns {string|null} Значение либо null, если записывать его нельзя.
   */
  function constantOf(key, value) {
    if (!CONSTANTS.has(key)) return null
    if (typeof value !== 'string') return null
    return CONSTANT_SHAPE.test(value) ? value : null
  }

  /**
   * Говорит, ЧЕМ значение протокольного поля не подошло под образец.
   *
   * Три значения, без которых не собрать запрос отправки сообщения, остались
   * подписями T12:ap, T15:ap и T9:ap. Подпись говорит «латиница и пунктуация», а
   * какая пунктуация - не говорит, и расширять образец пришлось бы наугад.
   * Наугад расширенный образец однажды пропустил бы токен.
   *
   * Подсказка называет длину, набор РАЗЛИЧНЫХ знаков пунктуации и наличие
   * заглавных с цифрами. Буквой считается буква ЛЮБОГО письма, а не только
   * латиница: первая редакция выбрасывала только латиницу, и кириллица
   * оставалась в наборе знаков целиком. Проверка это поймала.
   *
   * ОБЪЯСНЕНИЙ В ЗАПИСИ НЕТ, и это не мелочь. Первая редакция клала сюда
   * поясняющую строку по-русски - «образец расширяется по измерению, а не по
   * догадке», сорок девять знаков. Принимающая сторона отвергает запись с
   * кириллицей целиком, потому что кириллица означает утёкший русский текст, и
   * механизм, объясняющий отказ записать значение, сам сделал наблюдение
   * несохраняемым. Три попытки снять его пропали.
   *
   * Объяснение живёт здесь, в описании. Запись несёт данные. Восстановить по ней значение нельзя: порядок знаков не
   * сохраняется, буквы не сохраняются вовсе, повторы схлопнуты. Полей всего два,
   * и оба несут имя действия протокола.
   *
   * @param {string} key Имя поля.
   * @param {*} value Значение поля.
   * @returns {object|null} Подсказка либо null, если поле не протокольное, либо
   *   значение и без того записано дословно.
   */
  function constantHint(key, value) {
    if (!CONSTANTS.has(key)) return null
    if (typeof value !== 'string' || value === '') return null
    if (CONSTANT_SHAPE.test(value)) return null
    return {
      length: value.length,
      punctuation: [...new Set(value.replace(/[\p{L}\p{N}]/gu, ''))].sort().join(''),
      has_upper: /[A-Z]/.test(value),
      has_digit: /[0-9]/.test(value),
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
        fields[key] = typeof value === 'string' ? shapeOfNested(value, 0) : 'file'
      }
      return { kind: 'form', fields }
    }

    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) {
      const fields = {}
      for (const [key, value] of body.entries()) {
        fields[key] = constantOf(key, value) || shapeOfNested(value, 0)
      }
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
          fields[key] = constantOf(key, value) || shapeOfNested(value, 0)
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
      if (value.length === 0) return []
      // Все РАЗЛИЧНЫЕ формы, а не только первая. Прежде записывалась одна, и
      // канал обновлений остался наполовину неизвестным: в подписке четыре
      // объекта разных видов, а знали мы про один. Различие считается по самой
      // форме, а не по значению: значений тут нет по устройству.
      const shapes = []
      const seen = new Set()
      for (const one of value) {
        const shape = shapeOfValue(one, level + 1)
        const key = JSON.stringify(shape)
        if (seen.has(key)) continue
        seen.add(key)
        shapes.push(shape)
        // Восьми хватит на любой наблюдённый случай, а бесконечность в записи
        // означала бы, что длинный список выгружается целиком.
        if (shapes.length >= 8) break
      }
      // Метка на латинице нарочно: кириллица в записи означает утёкший русский
      // текст, и принимающая сторона такую запись отвергает целиком. Читается
      // как «столько элементов, столько различных форм».
      return [...shapes, `...of ${value.length}, distinct ${seen.size}`]
    }
    if (typeof value === 'object') {
      const out = {}
      for (const key of Object.keys(value).sort()) {
        const literal = constantOf(key, value[key])
        if (literal !== null) {
          out[key] = literal
          continue
        }
        const hint = constantHint(key, value[key])
        out[key] = hint === null
          ? shapeOfValue(value[key], level + 1)
          : { signature: shapeOfValue(value[key], level + 1), hint }
      }
      return out
    }
    if (typeof value === 'string') return shapeOfNested(value, level)
    if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float'
    return typeof value
  }

  /**
   * Разбирает строку вглубь, если внутри неё лежит JSON.
   *
   * Без этого главное осталось бы неизвестным. Первое настоящее наблюдение
   * показало, что отправка сообщения идёт полем формы, внутри которого JSON:
   * снаружи видно только T125:acdps, и что там за поля - неизвестно.
   *
   * Значения при этом всё равно не сохраняются: вложенное проходит те же
   * правила, что и внешнее.
   *
   * @param {string} text Значение поля.
   * @param {number} level Текущая глубина.
   * @returns {*} Форма вложенного JSON либо подпись строки.
   */
  function shapeOfNested(text, level) {
    const trimmed = text.trim()
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        return { nested: shapeOfValue(JSON.parse(trimmed), level + 1) }
      } catch {
        /* не JSON - остаётся подписью */
      }
    }
    return signature(text)
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
    // Мерка замаскированных сегментов кладётся В ЗАПИСЬ, а не теряется по
    // дороге. Первая редакция считала её в maskUrl и не переносила сюда: запись
    // брала из адреса только путь и параметры, а проверка звала maskUrl напрямую
    // и пропажи не видела.
    const measured = where.masked_segments
    recorded.push({
      method: String(method || 'GET').toUpperCase(),
      origin: where.origin,
      path: where.path,
      ...(measured && measured.length > 0 ? { masked_segments: measured } : {}),
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
  const nativeHeader = XMLHttpRequest.prototype.setRequestHeader
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__funora = { method, url, headers: {} }
    return nativeOpen.apply(this, arguments)
  }
  // Заголовки запроса прежде объявлялись пустыми: у XHR их не спросить после
  // отправки. Собираются они при выставлении - ИМЕНАМИ, значение не берётся.
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (this.__funora) this.__funora.headers[String(name)] = ''
    return nativeHeader.apply(this, arguments)
  }
  XMLHttpRequest.prototype.send = function (body) {
    const mine = this.__funora || {}
    this.addEventListener('load', function () {
      try {
        record(
          mine.method,
          mine.url,
          mine.headers,
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
      // Код ответа берётся у самого браузера: он помнит, чем закончилась
      // загрузка этой страницы. Без него снимок не отличить от снимка страницы
      // отказа, а именно это описание происхождения и обязано говорить.
      const navigation = (performance.getEntriesByType('navigation') || [])[0] || {}
      return send('page', name, {
        html: document.documentElement.outerHTML,
        where: {
          path: where.path,
          query: where.query,
          lang: document.documentElement.lang || '',
          title: signature(document.title),
          http_status: navigation.responseStatus || 0,
          final_url: where.origin + where.path,
          redirects: navigation.redirectCount || 0,
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
    currency(label) {
      const found = {}
      const pairs = {}
      const seen = {}
      // Знак валюты по Unicode либо код по ISO 4217. Три заглавные подряд
      // кодом валюты НЕ считаются: список продаж принёс так GTA, NBA и MIR -
      // сокращения игр и платёжной системы, стоявшие рядом с числом.
      const money = /\p{Sc}/gu
      const code = new RegExp('\\b(' + [...ISO].join('|') + ')\\b', 'g')
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          const holder = node.parentElement
          if (!holder) return NodeFilter.FILTER_REJECT
          const tag = holder.tagName.toLowerCase()
          if (tag === 'script' || tag === 'style' || tag === 'noscript') {
            return NodeFilter.FILTER_REJECT
          }
          // Переписка не смотрится вовсе: знак валюты там если и есть, то в
          // чужом тексте, а разметку цены он не описывает.
          if (holder.closest('.chat-msg-text, .chat-message, .message')) {
            return NodeFilter.FILTER_REJECT
          }
          return NodeFilter.FILTER_ACCEPT
        },
      })
      let node = walker.nextNode()
      while (node) {
        const text = node.nodeValue || ''

        // Пара «знак и код в одном узле» - прямой ответ на вопрос, ради
        // которого сбор и делается. Придуманная таблица припишет чужую валюту
        // чужому заказу молча, а один и тот же знак носят несколько валют.
        const codes = text.match(code) || []
        const signs = text.match(money) || []
        if (codes.length === 1 && signs.length === 1) {
          const pair = `${signs[0]} ${codes[0]}`
          pairs[pair] = (pairs[pair] || 0) + 1
        }
        // Код сам по себе тоже записывается: страница, где валюта названа
        // кодом и не показана знаком, иначе выглядела бы пустой.
        for (const one of codes) seen[one] = (seen[one] || 0) + 1

        money.lastIndex = 0
        let match = money.exec(text)
        while (match) {
          const symbol = match[0]
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
      // Метка отличает сборы, сделанные на ОДНОЙ странице в разных
      // состояниях - например при разном положении переключателя валюты.
      // Без неё три сбора подряд ушли под одним именем и затёрли друг друга.
      const where = maskUrl(location.href).path.replace(/[^a-z]+/gi, '-')
      const base = where.replace(/^-|-$/g, '') || 'root'
      const mark = String(label || '').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '')
      const named = mark ? `${base}.${mark}` : base
      // Переключатель валюты: ссылки и пункты списка, в адресе либо значении
      // которых стоит код ISO 4217 целым словом. Он и даёт пару «знак - код»:
      // сам по себе знак неоднозначен, один и тот же носят несколько валют.
      //
      // Код в адресе сохраняется ДОСЛОВНО по тому же правилу, что имя действия:
      // это протокольная константа из закрытого перечня, а не значение.
      // Переключатель ищется НЕ по перечню ISO и не по угаданному имени
      // атрибута: две попытки так и вернули пусто. Перечень был заглавными, а
      // значение оказалось иного регистра; имена перебирались втроём, а код
      // лежал в data-cy.
      //
      // Правило теперь двойное и от догадок не зависит: элемент помечен классом
      // про валюту, а значение атрибута - ровно три буквы. Трёхбуквенный токен
      // на элементе переключателя валюты не может быть ни именем, ни токеном.
      const CURRENCY_ELEMENT = '[class*=curr i], [class*=-cy], [class*=cy-]'
      const THREE_LETTERS = /^[A-Za-z]{3}$/
      const switcher = []
      for (const one of document.querySelectorAll(CURRENCY_ELEMENT)) {
        const named = [...one.attributes]
          .filter((attribute) => THREE_LETTERS.test(attribute.value.trim()))
          .map((attribute) => ({ name: attribute.name, value: attribute.value.trim() }))
        if (named.length !== 1) continue
        switcher.push({
          code: named[0].value.toUpperCase(),
          attribute: named[0].name,
          tag: one.tagName.toLowerCase(),
          class: one.className || '',
          text: signature(one.textContent || ''),
          active: /active|selected|current/i.test(one.className)
            || one.hasAttribute('selected')
            || one.getAttribute('aria-current') !== null,
        })
      }

      return send('currency', named, {
        page: maskUrl(location.href),
        lang: document.documentElement.lang || '',
        symbols: found,
        codes: seen,
        pairs,
        switcher,
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
      // Отпечаток сборки кладётся В САМО НАБЛЮДЕНИЕ, а не только печатается в
      // приветствии. Вкладка держит ту редакцию сборщика, которую загрузила, и
      // по виду записи это не отличить: три прежних наблюдения отправки
      // сообщения сделаны старой редакцией, без механизма протокольных
      // констант, и я объяснил их подписи слишком узким образцом. Настоящей
      // причиной была старая вкладка, и узнать это было неоткуда.
      return send('network', name, {
        collector_build: BUILD,
        captured_at: new Date().toISOString(),
        records: recorded.slice(),
      })
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

  // Отпечаток нужен затем, что сборщик правится по ходу дела, а вкладка держит
  // ту его редакцию, которую загрузила. Один сбор уже ушёл со старой: константы
  // остались подписями, и понять это удалось только по данным.
  window.funora.build = BUILD
  if (BUILD.indexOf('FUNORA') >= 0) {
    // Приёмник не подставил отпечаток - значит он запущен из редакции, которая
    // о нём не знает. Сборщик при этом свежий, а приёмник старый, и расходятся
    // они молча: снимок сохранится, а половина полей в нём будет пустой. Один
    // сбор так и ушёл.
    console.error(
      'funora: ПРИЁМНИК УСТАРЕЛ. Остановите его (Ctrl+C), запустите заново и '
        + 'вставьте строку загрузки ещё раз. Пока этого не сделано, снимок '
        + 'сохранится неполным.',
    )
  }
  console.log(
    `%cfunora%c собран, сборка ${BUILD}. Команды: funora.page("имя"), `
      + 'funora.currency("метка"), funora.watch(), funora.stop("имя")',
    'font-weight:bold',
    '',
  )
})()
