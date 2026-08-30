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
 *   funora.probe()      - один опрос канала с пустой подпиской;
 *   funora.probeTag()   - опрос канала с ВЫДУМАННОЙ меткой: отвечает на
 *                         вопрос, нужна ли для подписки настоящая;
 *   funora.listen([..]) - опрос канала с произвольной подпиской;
 *   funora.holdToken()  - запомнить защитный токен ДО выхода из аккаунта,
 *                         не показывая его; после выхода - probeDead();
 *   funora.probeSend('текст') - ОТПРАВЛЯЕТ настоящее сообщение с пустой
 *                       подпиской. Единственная команда, которая меняет
 *                       что-то на площадке. Отменить нельзя;
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
  // Дефис добавлен 24.08.2026 ПО ИЗМЕРЕНИЮ, а не по догадке. Четвёртый вид
  // объекта подписки канала не прошёл прежний образец, и мерка сказала чем:
  // длина пять, знаки строчные, пунктуация «-», цифр и заглавных нет.
  //
  // Расширение ничего не ослабляет, и это проверяется. Цифры по-прежнему
  // запрещены, заглавные по-прежнему запрещены, длина по-прежнему от двух. Все
  // наблюдённые виды идентификаторов площадки остаются за бортом: восемь цифр у
  // человека, девять у диалога, восемь ЗАГЛАВНЫХ у номера заказа, токен вида
  // a1b2c3d4 - с цифрами, имя диалога - с цифрами и пунктуацией.
  const CONSTANT_SHAPE = /^[a-z][a-z_-]{1,30}$/

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

  //: Атрибуты, значения которых сверяются с полями запроса.
  //
  // Перечень ЗАКРЫТ и написан здесь, в исходнике, а не собирается со страницы.
  // Собранный со страницы, он принёс бы в наблюдение имена атрибутов, которых
  // никто не читал, - а имя атрибута на странице продавца бывает и говорящим.
  //
  // Первая редакция знала три имени и сверяла одно поле - метку раздела. Этого
  // хватило, чтобы выяснить: метки разных видов подписки - РАЗНЫЕ значения. Но
  // тем же приёмом отвечается и следующий вопрос: откуда берётся то, что
  // площадка кладёт в поля действия. Ответить на него иначе нельзя по той же
  // причине - маскирование у снимка и у записи независимое.
  const PAGE_ATTRIBUTES = [
    'data-orders',
    'data-chat',
    'data-message',
    'data-id',
    'data-tag',
    'data-name',
    'data-node-msg',
    'data-user-msg',
    'data-user',
    'data-bookmarks-tag',
    'data-type',
  ]

  //: Поля запроса, значение которых стоит сверить со страницей.
  //
  // Перечень тоже закрыт. Сверять ВСЁ подряд нельзя: содержимое сообщения -
  // текст человека, и «совпало с атрибутом» о нём говорить нечего, а вот
  // отрицательный ответ сузил бы круг поиска для того, кто читает запись.
  const CHECKED_FIELDS = ['tag', 'node', 'last_message', 'id']

  //: Значения меток, снятые ПЕРЕД уходом запроса.
  //
  // Ключ - значение атрибута, значение - его имя. Таблица живёт от снятия до
  // записи одного запроса и наружу не уходит: в наблюдение попадает только имя.
  let tagsBefore = null

  /**
   * Снимает значения меток со страницы.
   *
   * Зовётся ДО ухода запроса, и это существенно. Запись делается после ответа,
   * а метки к тому времени приложение вправе уже обновить - в этом их
   * назначение. Сверка с обновлённой меткой дала бы «не совпало» там, где
   * совпадало.
   *
   * @returns {object|null} Таблица «значение -> имя атрибута» либо null.
   */
  function snapshotTags() {
    // Отказ здесь НЕ ДОЛЖЕН выходить наружу, и это не перестраховка. Снимок
    // берётся в подменённом window.fetch, до ухода запроса, - то есть на пути
    // ЧУЖОГО запроса, который делает сама страница. Урони сборщик исключение
    // здесь, и он сломал бы работу площадки в браузере пользователя.
    //
    // Наблюдатель обязан быть незаметен для наблюдаемого. Не вышло снять
    // метки - сверки не будет, и только.
    try {
      const table = {}
      let any = false
      for (const name of PAGE_ATTRIBUTES) {
        const found = document.querySelectorAll('[' + name + ']')
        for (let index = 0; index < found.length; index += 1) {
          const value = found[index].getAttribute(name)
          if (!value) continue
          // Первое имя побеждает: два атрибута с одним значением - это одно
          // значение, и называть его дважды нечем.
          if (!(value in table)) table[value] = name
          any = true
        }
      }
      return any ? table : null
    } catch {
      return null
    }
  }

  /**
   * Говорит, из какого атрибута страницы взято значение.
   *
   * Наружу уходит ИМЯ атрибута либо false. Ни одного значения: сверка целиком
   * происходит здесь, в живой вкладке, где обе стороны видны разом.
   *
   * @param {*} value Значение поля запроса.
   * @returns {string|boolean|null} Имя атрибута, false либо null, если сверять
   *   не с чем.
   */
  function tagOrigin(value) {
    if (tagsBefore === null) return null
    // Число сверяется по своей записи: last_message приходит числом, а в
    // атрибуте страницы лежит строка тех же цифр. Правило «только строка»
    // отвечало бы «сверять нечем» там, где сверить можно.
    const text = typeof value === 'number' ? String(value) : value
    if (typeof text !== 'string' || text === '') return null
    return Object.prototype.hasOwnProperty.call(tagsBefore, text) ? tagsBefore[text] : false
  }

  //: Как выглядит ИМЯ ПОЛЯ - ключ, который можно записать дословно.
  //
  // Проверка заведена 24.08.2026 после утечки. Ответ на догрузку строк - это
  // HTML, а сборщик разобрал его как форму: знак равенства есть в каждом
  // атрибуте разметки, разбор строки запроса сделал ключами куски HTML, и
  // ключи записались дословно - вместе с настоящими суммами операций.
  //
  // Допущение «ключи структурны» верно ровно до тех пор, пока ключи вправду
  // являются именами полей. Теперь это проверяется, а не предполагается.
  const FIELD_NAME = /^[A-Za-z_][A-Za-z0-9_.[\]-]{0,64}$/

  /**
   * Говорит, похожа ли строка на строку запроса, а не на что угодно со знаком
   * равенства внутри.
   *
   * @param {string} text Тело запроса строкой.
   * @returns {boolean} Правда, если каждый ключ имеет вид имени поля.
   */
  function looksLikeAForm(text) {
    if (text.includes('<') || text.includes('>') || /\s/.test(text.trim())) return false
    const parts = text.split('&')
    if (parts.length === 0) return false
    return parts.every((one) => {
      const at = one.indexOf('=')
      if (at <= 0) return false
      return FIELD_NAME.test(decodeURIComponent(one.slice(0, at)))
    })
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
      if (looksLikeAForm(text)) {
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
        if (!FIELD_NAME.test(key)) {
          // Ключ не похож на имя поля. Записать его дословно значило бы
          // повторить утечку с другого конца: ключом бывает и то, что написал
          // человек, - в словаре, собранном из данных.
          out[signature(key)] = shapeOfValue(value[key], level + 1)
          continue
        }
        const literal = constantOf(key, value[key])
        if (literal !== null) {
          out[key] = literal
          continue
        }
        // Значение сверяется со страницей ПРЯМО ЗДЕСЬ, рядом с самим полем.
        // Ответ - имя атрибута либо false; значения не уходит.
        if (CHECKED_FIELDS.indexOf(key) >= 0) {
          const origin = tagOrigin(value[key])
          if (origin !== null) {
            out[key] = { signature: shapeOfValue(value[key], level + 1), from_attribute: origin }
            continue
          }
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
  function record(method, url, headers, body, status, responseHeaders, responseBody, tags) {
    if (!watching) return
    // Снимок меток ставится на время разбора тела и снимается сразу после:
    // разбор синхронный, и перепутать его с чужим запросом нечему.
    tagsBefore = tags === undefined ? null : tags
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
    tagsBefore = null
  }

  const nativeFetch = window.fetch
  window.fetch = async function (input, init) {
    const options = init || {}
    const url = typeof input === 'string' ? input : input && input.url
    // Метки снимаются ДО ухода запроса: приложение обновляет их по ответу, и
    // сверка с обновлёнными дала бы «не совпало» там, где совпадало.
    const tags = watching ? snapshotTags() : null
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
          tags,
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
    // Метки снимаются здесь, а не в обработчике load: там запрос уже прошёл, и
    // приложение вправе было их обновить.
    const tags = watching ? snapshotTags() : null
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
          tags,
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
     * Делает ОДИН опрос канала с пустой подпиской.
     *
     * Отвечает на вопрос, который иначе решался бы догадкой: принимает ли канал
     * пустое поле objects. Во всех сорока пяти записанных запросах оно непусто,
     * пустым его не видели ни разу, и отправка сообщения упирается ровно в это.
     *
     * ЧТО ОНА ДЕЛАЕТ НА ПЛОЩАДКЕ - НИЧЕГО. Поле request несёт false, то есть
     * действия в запросе нет: это тот же самый опрос, который страница делает
     * сама каждые пять секунд. Один опрос вместо одного опроса.
     *
     * Ответ записывается обычным перехватом, поэтому вызывать её надо между
     * watch и stop - иначе запрос уйдёт, а записи не будет.
     *
     * @returns {Promise<string>} Что вышло.
     */
    probe() {
      if (!watching) {
        return Promise.reject(
          new Error('сперва funora.watch(): иначе опрос уйдёт, а записи не будет'),
        )
      }
      const carrier = document.querySelector('body[data-app-data]')
      if (carrier === null) {
        return Promise.reject(new Error('на странице нет носителя настроек body[data-app-data]'))
      }
      let token = ''
      try {
        token = String((JSON.parse(carrier.getAttribute('data-app-data')) || {})['csrf-token'] || '')
      } catch {
        return Promise.reject(new Error('настройки страницы не разбираются как JSON'))
      }
      if (!token) return Promise.reject(new Error('в настройках страницы нет защитного токена'))

      const form = new URLSearchParams()
      form.set('objects', '[]')
      form.set('request', 'false')
      form.set('csrf_token', token)

      return window
        .fetch('/runner/', {
          method: 'POST',
          headers: {
            Accept: 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: form.toString(),
        })
        .then((response) => 'опрос с пустой подпиской сделан, код ' + response.status)
    },

    /**
     * Опрашивает канал с ПРОИЗВОЛЬНОЙ подпиской, ничего не делая на площадке.
     *
     * Отличается от probe одним: подписку задаёт вызывающий. Поле request
     * по-прежнему несёт false, то есть действия в запросе нет - это тот же
     * опрос, который страница делает сама каждые несколько секунд.
     *
     * РАДИ ЧЕГО. Наблюдение сегодня читает две полные страницы за шаг. Канал
     * отдаёт то же самое одним небольшим ответом, и если подписаться можно, не
     * зная настоящих меток, наблюдение переводится на него целиком. Это и
     * проверяется: подписка с ВЫДУМАННОЙ меткой либо даст изменения, либо не
     * даст, и оба ответа одинаково полезны.
     *
     * Ответ записывается обычным перехватом, поэтому звать надо между watch и
     * stop - иначе запрос уйдёт, а записи не будет.
     *
     * @param {object[]} objects Подписка: перечень объектов как есть.
     * @returns {Promise<string>} Что вышло.
     */
    listen(objects) {
      if (!Array.isArray(objects)) {
        return Promise.reject(
          new Error('нужен массив объектов подписки: funora.listen([{type: "...", ...}])')
        )
      }

      const carrier = document.querySelector('body[data-app-data]')
      if (carrier === null) {
        return Promise.reject(new Error('на странице нет носителя настроек body[data-app-data]'))
      }
      let token = ''
      try {
        token = String((JSON.parse(carrier.getAttribute('data-app-data')) || {})['csrf-token'] || '')
      } catch {
        return Promise.reject(new Error('настройки страницы не разбираются как JSON'))
      }
      if (!token) return Promise.reject(new Error('в настройках страницы нет защитного токена'))

      const form = new URLSearchParams()
      form.set('objects', JSON.stringify(objects))
      form.set('request', 'false')
      form.set('csrf_token', token)

      return window
        .fetch('/runner/', {
          method: 'POST',
          headers: {
            Accept: 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: form.toString(),
        })
        .then(
          (response) =>
            'опрос с подпиской из ' + objects.length + ' объектов сделан, код ' + response.status
        )
    },

    /**
     * Опрашивает канал подпиской с ВЫДУМАННОЙ меткой.
     *
     * ГЛАВНЫЙ ОПЫТ ПЛАНА НАБЛЮДЕНИЙ, и вот почему. Сегодня, чтобы обратиться к
     * каналу, приходится сперва прочитать страницу диалога - метки лежат на
     * ней. Если площадка принимает выдуманную метку и отвечает «вот что
     * изменилось», то читать страницу больше не нужно: хватит защитного токена
     * и собственного идентификатора.
     *
     * Ничего не ломает и ничего не провоцирует: это обычный опрос с полем
     * request равным false. Выдуманная метка - не подделка защиты, а значение
     * «я ничего не видел», ровно как при первом обращении страницы.
     *
     * Идентификатор берётся со страницы, а не задаётся вызывающим: собственный
     * номер лежит в атрибуте data-user, и просить его руками значило бы
     * приглашать вписать чужой.
     *
     * ТРИ ЗАПРОСА ОДНОЙ КОМАНДОЙ, И ЭТО НЕ УДОБСТВО. Метки сменяются от ответа
     * к ответу: второй запрос надо послать с теми, что вернулись в первом, и
     * сделать это позже уже нельзя - они устареют. Раньше здесь был один
     * запрос, а метки предлагалось перенести руками; перенести их руками
     * НЕЛЬЗЯ, потому что ответ канала в консоль не показывается и показываться
     * не должен: в нём разметка списка диалогов, то есть имена собеседников.
     *
     * Возвращается сводка БЕЗ содержимого: виды объектов, число их и признак
     * пустоты. Ни текста, ни разметки, ни имён. Сам ответ уходит на диск
     * обычным перехватом и превращается там в скелет.
     *
     * @param {string} tag Метка. По умолчанию заведомо несуществующая.
     * @returns {Promise<object>} Сводка по трём опросам.
     */
    probeTag(tag) {
      const label = String(tag === undefined ? '0000000000' : tag)
      const holder = document.querySelector('[data-user]')
      if (holder === null) {
        return Promise.reject(
          new Error('на странице нет узла с data-user: собственный номер брать неоткуда')
        )
      }
      const own = String(holder.getAttribute('data-user') || '')
      if (!own) return Promise.reject(new Error('атрибут data-user пуст'))

      const carrier = document.querySelector('body[data-app-data]')
      if (carrier === null) {
        return Promise.reject(new Error('на странице нет носителя настроек body[data-app-data]'))
      }
      let token = ''
      try {
        token = String((JSON.parse(carrier.getAttribute('data-app-data')) || {})['csrf-token'] || '')
      } catch {
        return Promise.reject(new Error('настройки страницы не разбираются как JSON'))
      }
      if (!token) return Promise.reject(new Error('в настройках страницы нет защитного токена'))

      /**
       * Шлёт один опрос и возвращает разобранный ответ.
       *
       * @param {object[]} objects Подписка.
       * @returns {Promise<object>} Код ответа и разобранное тело.
       */
      const ask = (objects) => {
        const form = new URLSearchParams()
        form.set('objects', JSON.stringify(objects))
        form.set('request', 'false')
        form.set('csrf_token', token)
        return window
          .fetch('/runner/', {
            method: 'POST',
            headers: {
              Accept: 'application/json, text/javascript, */*; q=0.01',
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-Requested-With': 'XMLHttpRequest',
            },
            body: form.toString(),
          })
          .then((response) =>
            response.text().then((body) => {
              let parsed = null
              try {
                parsed = JSON.parse(body)
              } catch {
                parsed = null
              }
              return { status: response.status, parsed }
            })
          )
      }

      /**
       * Сводит ответ к безопасному описанию: без единого чужого слова.
       *
       * @param {object} answer Код и разобранное тело.
       * @returns {object} Сводка.
       */
      const digest = (answer) => {
        const objects = answer.parsed && Array.isArray(answer.parsed.objects)
          ? answer.parsed.objects
          : []
        return {
          код: answer.status,
          разобрано: answer.parsed !== null,
          объектов: objects.length,
          виды: objects.map((one) => String((one || {}).type || '?')),
          // Поля перечисляются ИМЕНАМИ, а не значениями: имя поля говорит о
          // площадке, значение - о собеседнике.
          поля: objects.map((one) =>
            Object.keys(((one || {}).data && typeof one.data === 'object' ? one.data : {}) || {})
          ),
          метки_сменились: null,
        }
      }

      const invented = [
        { type: 'chat_bookmarks', id: own, tag: label, data: false },
        { type: 'orders_counters', id: own, tag: label, data: false },
      ]

      const report = { выдуманная_метка: null, вернувшиеся_метки: null, одиннадцать_узлов: null }

      return ask(invented)
        .then((first) => {
          report.выдуманная_метка = digest(first)

          // Второй опрос - с метками, ВЕРНУВШИМИСЯ в первом. Отвечает на
          // отдельный вопрос: подтверждает ли площадка метку как квитанцию,
          // то есть перестаёт ли отдавать то же самое во второй раз.
          const objects = first.parsed && Array.isArray(first.parsed.objects)
            ? first.parsed.objects
            : []
          const returned = new Map()
          for (const one of objects) {
            if (one && one.type && one.tag !== undefined) returned.set(String(one.type), one.tag)
          }
          report.выдуманная_метка.метки_сменились = returned.size > 0

          const second = invented.map((one) =>
            returned.has(one.type) ? { ...one, tag: returned.get(one.type) } : one
          )
          return ask(second)
        })
        .then((second) => {
          report.вернувшиеся_метки = digest(second)

          // Третий опрос - одиннадцать узлов диалога с выдуманными метками.
          // Проверяет чужую константу «не больше десяти объектов в подписке»:
          // если одиннадцатый молча отбрасывается, об этом надо знать заранее.
          const rows = Array.from(document.querySelectorAll('a.contact-item[data-id]')).slice(0, 11)
          if (rows.length < 11) {
            report.одиннадцать_узлов = {
              пропущено: 'на странице ' + rows.length + ' строк диалогов, нужно одиннадцать',
            }
            return null
          }
          return ask(
            rows.map((row) => ({
              type: 'chat_node',
              id: String(row.getAttribute('data-id') || ''),
              tag: label,
              data: false,
            }))
          )
        })
        .then((third) => {
          if (third !== null) {
            report.одиннадцать_узлов = digest(third)
            report.одиннадцать_узлов.послано = 11
          }
          report.дальше = 'funora.stop("runner-invented-tag")'
          return report
        })
    },

    /**
     * Запоминает защитный токен страницы, НЕ показывая его.
     *
     * ЗАЧЕМ. Единственное оставшееся наблюдение канала - что он отвечает при
     * истёкшей сессии. Чтобы его снять, надо выйти из аккаунта, а выход
     * перезагружает страницу: сборщик вместе с ней исчезает, и токен взять
     * потом уже неоткуда.
     *
     * Прежде это решалось так: «скопируйте токен со страницы». Решение
     * оказалось негодным, и негодным опасно. Человек, отправленный искать
     * токен, идёт в инструменты разработчика - а там рядом лежат ключ сессии и
     * прочие куки, и один снимок экрана отдаёт аккаунт целиком.
     *
     * ПРАВИЛО, КОТОРОЕ ИЗ ЭТОГО СЛЕДУЕТ: человек не переносит руками ничего,
     * что похоже на секрет. Не потому, что не справится, а потому, что путь к
     * значению ведёт мимо настоящих секретов.
     *
     * Токен кладётся в хранилище ТОЙ ЖЕ вкладки и того же источника, где он и
     * так живёт: наружу он не уходит, в консоль не печатается, и стирается
     * сразу после того, как пригодился.
     *
     * @returns {string} Подтверждение без значения.
     */
    holdToken() {
      // ПРОВЕРКА ПОРЯДКА, а не придирка. Токен есть и у гостевой страницы, и
      // выглядит он точно так же. Запомненный после выхода, он отвечает на
      // другой вопрос: «что канал скажет гостю», а спрашивали мы «что он
      // скажет тому, у кого сессия истекла».
      //
      // Отличить эти два случая по записи наблюдения потом НЕЛЬЗЯ: в ней
      // подписи, а не значения. Значит проверять надо здесь и сейчас.
      //
      // Признак тот же, по которому личность читает разбор страницы: ссылка на
      // собственный профиль в меню вошедшего.
      if (document.querySelector('a.user-link-dropdown') === null) {
        return (
          'ОТКАЗ: страница гостевая, вы уже вышли. Токен гостя запоминать незачем - ' +
          'опыт с ним отвечает на другой вопрос, и отличить это потом по записи будет ' +
          'нельзя. Порядок такой: войдите, вызовите funora.holdToken(), ПОТОМ выходите'
        )
      }

      const carrier = document.querySelector('body[data-app-data]')
      if (carrier === null) return 'на странице нет носителя настроек body[data-app-data]'
      let token = ''
      try {
        token = String((JSON.parse(carrier.getAttribute('data-app-data')) || {})['csrf-token'] || '')
      } catch {
        return 'настройки страницы не разбираются как JSON'
      }
      if (!token) return 'в настройках страницы нет защитного токена'

      try {
        window.localStorage.setItem('funora.held-token', token)
      } catch {
        return 'хранилище вкладки недоступно: токен не запомнен'
      }
      return 'токен запомнен во вкладке. Значение не показано и показано не будет. Теперь выйдите из аккаунта, вставьте сборщик заново и вызовите funora.probeDead()'
    },

    /**
     * Опрашивает канал ПОСЛЕ выхода из аккаунта, запомненным токеном.
     *
     * Отвечает на последний невыясненный вопрос о канале: что он делает при
     * истёкшей сессии. Ради этого случая в SDK написаны политика повторов и
     * распознавание состояния сессии, а видел его ноль раз.
     *
     * Токен берётся из хранилища и СТИРАЕТСЯ сразу, пригодился он или нет:
     * лежать ему там незачем.
     *
     * @returns {Promise<object>} Код ответа и форма тела, без содержимого.
     */
    probeDead() {
      let token = ''
      try {
        token = String(window.localStorage.getItem('funora.held-token') || '')
        window.localStorage.removeItem('funora.held-token')
      } catch {
        return Promise.reject(new Error('хранилище вкладки недоступно'))
      }
      if (!token) {
        return Promise.reject(
          new Error('запомненного токена нет: вызовите funora.holdToken() ДО выхода из аккаунта')
        )
      }

      const form = new URLSearchParams()
      form.set('objects', '[]')
      form.set('request', 'false')
      form.set('csrf_token', token)

      return window
        .fetch('/runner/', {
          method: 'POST',
          headers: {
            Accept: 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: form.toString(),
        })
        .then((response) =>
          response.text().then((body) => {
            let parsed = null
            try {
              parsed = JSON.parse(body)
            } catch {
              parsed = null
            }
            return {
              код: response.status,
              // Перенаправление важнее прочего: по нему видно, гонит ли
              // площадка на вход или отвечает по существу.
              перенаправлен: response.redirected === true,
              разобрано_как_json: parsed !== null,
              // Только ИМЕНА полей верхнего уровня. Значения не выносятся:
              // правило то же, что у сводки опроса с выдуманной меткой.
              поля: parsed && typeof parsed === 'object' ? Object.keys(parsed) : [],
              длина_тела: body.length,
              дальше: 'funora.stop("runner-dead-session")',
            }
          })
        )
    },

    /**
     * Собирает со страницы всё нужное для обращения к каналу.
     *
     * Места взяты из наблюдения, а не из догадки: имя диалога сверено с
     * атрибутом data-name, позиция - с data-node-msg АКТИВНОЙ строки списка.
     *
     * @returns {object} Поля обращения либо {error} с объяснением.
     */
    context() {
      const carrier = document.querySelector('body[data-app-data]')
      if (carrier === null) return { error: 'на странице нет носителя настроек body[data-app-data]' }
      let token = ''
      try {
        token = String((JSON.parse(carrier.getAttribute('data-app-data')) || {})['csrf-token'] || '')
      } catch {
        return { error: 'настройки страницы не разбираются как JSON' }
      }
      if (!token) return { error: 'в настройках страницы нет защитного токена' }

      const widget = document.querySelector('.chat.chat-float')
      if (widget === null) return { error: 'на странице нет виджета переписки' }

      const tag = widget.getAttribute('data-tag')
      const node = widget.getAttribute('data-name')
      if (!node) {
        return {
          error:
            'у виджета нет имени диалога (data-name). Так выглядит список без '
            + 'открытого собеседника: откройте диалог и вставьте сборщик заново',
        }
      }

      // Строка открытого диалога ищется по идентификатору, а не по подсветке:
      // класс - чужое решение об оформлении.
      const wanted = widget.getAttribute('data-id') || ''
      const rows = [...document.querySelectorAll('a.contact-item')]
      let row = wanted ? rows.find((one) => (one.getAttribute('data-id') || '') === wanted) : null
      if (!row) row = rows.find((one) => one.classList.contains('active')) || null
      if (!row) return { error: 'строка открытого диалога не нашлась в списке' }

      const last = row.getAttribute('data-node-msg')
      if (!last) return { error: 'у строки диалога нет позиции последнего сообщения' }

      return { token, node, last, tag }
    },

    /**
     * ОТПРАВЛЯЕТ НАСТОЯЩЕЕ СООБЩЕНИЕ с пустой подпиской.
     *
     * Команда единственная во всём сборщике, которая что-то МЕНЯЕТ на площадке.
     * Прочие только смотрят. Отменить отправленное нельзя.
     *
     * Зачем она нужна. Наблюдено, что канал принимает пустое поле objects при
     * ОПРОСЕ. Выполняет ли он при этом ДЕЙСТВИЕ - другое утверждение, и оно не
     * наблюдалось. Это последняя догадка, которая осталась бы в написанной
     * отправке, и снять её иначе нельзя: страница площадки всегда шлёт подписку
     * целиком.
     *
     * Текст обязателен и вводится руками. Значения по умолчанию нет нарочно:
     * отправка не должна случаться от вызова без доводов.
     *
     * ДОВЕСОК subscribe. Наблюдено 30.08.2026: ответ канала несёт изменения
     * ТОЛЬКО подписанных объектов. При пустой подписке отправка проходит, а
     * ответ приходит пустым - подтверждать нечем.
     *
     * С довеском подписка ставится ровно одна - на узел этого диалога. Она
     * собирается со страницы целиком: идентификатор из data-name, метка из
     * data-tag, данные те же, что у действия. Догадок в ней нет.
     *
     * @param {string} text Текст сообщения. Пишите туда, где лишнее сообщение
     *   никому не помешает.
     * @param {object} [options] Довески. Поле subscribe включает подписку на
     *   узел диалога.
     * @returns {Promise<string>} Что вышло.
     */
    probeSend(text, options) {
      if (!watching) {
        return Promise.reject(
          new Error('сперва funora.watch(): иначе сообщение уйдёт, а записи не будет'),
        )
      }
      if (typeof text !== 'string' || text.trim() === '') {
        return Promise.reject(
          new Error(
            'нужен текст: funora.probeSend("проба"). Команда ОТПРАВЛЯЕТ настоящее '
              + 'сообщение, и отменить его нельзя',
          ),
        )
      }

      const where = this.context()
      if (where.error) return Promise.reject(new Error(where.error))

      const data = { node: where.node, last_message: Number(where.last), content: text }

      // Подписка либо пуста, либо состоит РОВНО ИЗ ОДНОГО узла - того самого
      // диалога. Второго вида объектов здесь нет нарочно: метка закладок не
      // наблюдалась, и собрать её было бы догадкой.
      let objects = []
      if (options && options.subscribe) {
        if (!where.tag) {
          return Promise.reject(
            new Error('у виджета нет метки диалога (data-tag): подписку не собрать'),
          )
        }
        objects = [{ type: 'chat_node', id: where.node, tag: where.tag, data }]
      }

      const form = new URLSearchParams()
      form.set('objects', JSON.stringify(objects))
      form.set('request', JSON.stringify({ action: 'chat_message', data }))
      form.set('csrf_token', where.token)

      return window
        .fetch('/runner/', {
          method: 'POST',
          headers: {
            Accept: 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: form.toString(),
        })
        .then((response) =>
          'отправка сделана ('
            + (objects.length === 0 ? 'подписка пуста' : 'подписка на узел диалога')
            + '), код '
            + response.status
            + '. Посмотрите в переписку: пришло ли сообщение',
        )
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
      + 'funora.currency("метка"), funora.watch(), funora.probe(), funora.probeTag(), funora.listen([...]), funora.holdToken(), funora.stop("имя")\n  ВНИМАНИЕ: funora.probeSend("текст") ОТПРАВЛЯЕТ настоящее сообщение',
    'font-weight:bold',
    '',
  )
})()
