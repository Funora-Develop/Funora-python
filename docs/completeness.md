# Полнота Python SDK

<!-- Порождено tools/completeness.py; рукописные связи: tests/fixtures/completeness.json. -->

Контракт 0.62.0: 35 операций доступны через Client и AsyncClient; 12 частично опираются на сторонний протокол. Порождаются 12 из 16 видов событий. Открытых пунктов реестра: 22, включая один пункт вне Python.

Это карта реализации и проверок, а не сертификат готовности площадки. Тесты на записанных и синтетических ответах не заменяют собственные наблюдения. 100% строк SDK не означает проверку всех ветвей или реализацию всего целевого API.

## Операции и проверки

Имена после `client.` одинаковы у обоих клиентов; у AsyncClient вызов ожидают через `await`. Матрица проверяет сигнатуры и передачу аргументов в Engine, результат и выбор транспорта. Ниже названа опорная проверка поведения каждого метода; остальные сценарии выполняет общий набор тестов.

| Операция | Метод клиента | Метод Engine | Результат | Проверка поведения |
| --- | --- | --- | --- | --- |
| `account.balance` | `account.balance` | `read_balance` | `BalancePage` | `tests/test_account.py::test_every_field_of_every_transaction_is_read` |
| `account.get` | `account.get` | `read_account` | `Account` | `tests/test_service_reads.py::test_account_reads_bind_identity_and_refresh_from_the_page` |
| `account.refresh` | `account.refresh` | `read_account` | `Account` | `tests/test_service_reads.py::test_account_reads_bind_identity_and_refresh_from_the_page` |
| `account.switch_currency` | `account.switch_currency` | `switch_currency` | `CurrencySwitch` | `tests/test_currency_switch.py::test_confirmation_is_never_given_on_the_users_behalf` |
| `capabilities` | `account.capabilities` | `capability_profile` | `CapabilityProfile` | `tests/test_capability_profile.py::test_profiles_do_not_probe_or_refresh_evidence` |
| `catalog.categories` | `catalog.categories` | `read_catalog` | `CatalogPage` | `tests/test_catalog_cache.py::test_cache_preserves_observation_and_refresh_bypasses_it` |
| `catalog.field_schema` | `catalog.field_schema` | `read_field_schema` | `FieldSchema` | `tests/test_field_schema.py::test_observed_schema_keeps_choices_ranges_and_empty_option` |
| `catalog.search` | `catalog.search` | `read_catalog_search` | `CatalogPage` | `tests/test_catalog_search.py::test_both_clients_use_real_public_post_without_account_cookie` |
| `chats.buyer_viewing` | `chats.buyer_viewing` | `read_buyer_viewing` | `BuyerViewing[]` | `tests/test_buyer_viewing.py::test_markup_that_does_not_parse_keeps_the_markup` |
| `chats.history` | `chats.thread` | `read_thread` | `Thread` | `tests/test_client.py::test_thread_reads_messages` |
| `chats.history_before` | `chats.history_before` | `read_history_before` | `ChatHistory` | `tests/test_chat_history.py::test_one_wrong_message_condemns_the_whole_reply` |
| `chats.list` | `chats.list` | `read_chats` | `ChatsPage` | `tests/test_client.py::test_chats_list_reads_the_dialog_list` |
| `chats.mark_read` | `chats.mark_read` | `mark_chat_read` | `void` | `tests/test_mark_read.py::test_the_request_carries_no_action_at_all` |
| `chats.send_image` | `chats.send_image` | `send_image` | `SendResult` | `tests/test_send_image.py::test_without_a_file_number_the_second_request_never_leaves` |
| `chats.send_text` | `chats.send_text` | `send_text` | `SendResult` | `tests/test_send_text_operation.py::test_a_confirmed_send_reads_the_page_then_submits_once` |
| `chips.calculate_prices` | `market.calculate_chip_prices` | `calculate_prices` | `PriceCalculation` | `tests/test_calc.py::test_the_two_addresses_take_two_different_arguments` |
| `chips.offers` | `market.chips` | `read_chips` | `ChipsPage` | `tests/test_chips.py::test_the_operation_reads_the_chips_address` |
| `lots.activate` | `lots.activate` | `set_lot_visible` | `LotForm` | `tests/test_lot_visibility.py::test_turning_on_sends_the_flag_as_on` |
| `lots.calculate_prices` | `lots.calculate_prices` | `calculate_prices` | `PriceCalculation` | `tests/test_calc.py::test_the_two_addresses_take_two_different_arguments` |
| `lots.deactivate` | `lots.deactivate` | `set_lot_visible` | `LotForm` | `tests/test_lot_visibility.py::test_a_cleared_flag_leaves_as_an_empty_string` |
| `lots.form` | `lots.form` | `read_lot_form` | `LotForm` | `tests/test_lot_revision.py::test_revision_matches_declared_json_frame` |
| `lots.list_own` | `lots.list_own` | `read_own_lots` | `OwnLotsPage` | `tests/test_service_reads.py::test_order_and_lots_services_return_readable_identifiers` |
| `lots.promote` | `lots.promote` | `promote_lots` | `RaiseResult` | `tests/test_promote.py::test_a_choice_url_cancels_the_success` |
| `lots.showcase` | `lots.showcase` | `read_showcase` | `ShowcasePage` | `tests/test_showcase.py::test_the_read_is_never_declared_complete` |
| `lots.update_price` | `lots.update_price` | `update_price` | `LotForm` | `tests/test_update_price.py::test_everything_read_is_sent_back_and_only_the_price_changes` |
| `market.offers` | `market.offers` | `read_market` | `MarketPage` | `tests/test_market.py::test_lazy_rows_are_read_like_the_others` |
| `market.snapshot` | `market.snapshot` | `read_market_snapshot` | `MarketSnapshot` | `tests/test_market_snapshot.py::test_an_incomplete_snapshot_never_reports_absences` |
| `orders.details` | `orders.details` | `read_order_details` | `OrderDetailsBatch` | `tests/test_order_details.py::test_the_two_sides_are_separated` |
| `orders.get` | `orders.get` | `read_order` | `OrderView` | `tests/test_order.py::test_every_anchored_field_is_read` |
| `orders.list` | `orders.list` | `read_orders` | `OrdersPage` | `tests/test_orders.py::test_intact_page_is_complete`<br>`tests/test_order_filters.py::test_filters_use_encoded_get_and_general_read_keeps_original_path` |
| `orders.refund` | `orders.refund` | `refund_order` | `RefundResult` | `tests/test_refund.py::test_only_one_request_ever_leaves` |
| `reviews.get` | `reviews.get` | `read_reviews` | `ReviewsPage` | `tests/test_reviews_pagination.py::test_sync_continuation_posts_the_exact_form_and_does_not_authenticate_guest`<br>`tests/test_review_filters.py::test_filtered_cursor_resumes_in_a_new_client_without_losing_rating` |
| `reviews.leave` | `reviews.leave` | `leave_review` | `ReviewResult` | `tests/test_reviews_write.py::test_a_matching_rating_confirms_the_outcome` |
| `reviews.remove` | `reviews.remove` | `remove_review` | `ReviewResult` | `tests/test_reviews_write.py::test_a_removal_that_left_the_rating_is_not_confirmed` |
| `session.health` | `account.health` | `read_health` | `SessionHealth` | `tests/test_whoami.py::test_a_stale_verdict_is_never_reported_as_a_fresh_check` |

## Возможности и границы протокола

`third_party_report` означает недостающее собственное наблюдение запроса, ответа либо его эффекта. `observed` ниже означает отсутствие этой пометки в контракте; известные ограничения сохраняются. Безопасность повтора (`safe`, `idempotent`, `unsafe`) не является разрешением на запись. Для сторонних записей сохраняется явный opt-in.

| Операция | Capability | Повтор | Основание | Связанные ограничения |
| --- | --- | --- | --- | --- |
| `account.balance` | `account.balance` | `safe` | observed | `dyn_table_continue_request` |
| `account.get` | `account.profile` | `safe` | observed | Общие ограничения ниже |
| `account.refresh` | `account.profile` | `safe` | observed | Общие ограничения ниже |
| `account.switch_currency` | `account.switch_currency` | `idempotent` | third_party_report | Общие ограничения ниже |
| `capabilities` | `account.profile` | `safe` | observed | Общие ограничения ниже |
| `catalog.categories` | `catalog.categories` | `safe` | observed | Общие ограничения ниже |
| `catalog.field_schema` | `catalog.field_schema` | `safe` | observed | Общие ограничения ниже |
| `catalog.search` | `catalog.search` | `safe` | observed | Общие ограничения ниже |
| `chats.buyer_viewing` | `chats.buyer_viewing` | `safe` | third_party_report | Общие ограничения ниже |
| `chats.history` | `chats.history` | `safe` | observed | Общие ограничения ниже |
| `chats.history_before` | `chats.history_pagination` | `safe` | third_party_report | Общие ограничения ниже |
| `chats.list` | `chats.list` | `safe` | observed | Общие ограничения ниже |
| `chats.mark_read` | `chats.mark_read` | `idempotent` | third_party_report | `runner_error_shape` |
| `chats.send_image` | `chats.send_image` | `unsafe` | observed | `runner_error_shape` |
| `chats.send_text` | `chats.send_text` | `unsafe` | observed | `runner_error_shape`, `chats_sending_form` |
| `chips.calculate_prices` | `chips.calculate_prices` | `safe` | third_party_report | Общие ограничения ниже |
| `chips.offers` | `chips.offers` | `safe` | observed | Общие ограничения ниже |
| `lots.activate` | `lots.activate` | `idempotent` | third_party_report | `lot_visibility_request_unobserved` |
| `lots.calculate_prices` | `lots.calculate_prices` | `safe` | third_party_report | Общие ограничения ниже |
| `lots.deactivate` | `lots.deactivate` | `idempotent` | third_party_report | `lot_visibility_request_unobserved`, `lots_deactivate_guards` |
| `lots.form` | `lots.form` | `safe` | observed | Общие ограничения ниже |
| `lots.list_own` | `lots.list_own` | `safe` | observed | Общие ограничения ниже |
| `lots.promote` | `lots.promote` | `unsafe` | observed | `promote_success_shape_unobserved` |
| `lots.showcase` | `lots.showcase` | `safe` | observed | `showcase_truncation` |
| `lots.update_price` | `lots.update_price` | `idempotent` | observed | `domain_conflict_error` |
| `market.offers` | `market.offers` | `safe` | observed | Общие ограничения ниже |
| `market.snapshot` | `market.snapshot` | `safe` | observed | Общие ограничения ниже |
| `orders.details` | `orders.details` | `safe` | third_party_report | Общие ограничения ниже |
| `orders.get` | `orders.get` | `safe` | observed | `order_chat_truncation`, `order_params_unnamed`, `domain_not_found` |
| `orders.list` | `orders.list` | `safe` | observed | `dyn_table_continue_request` |
| `orders.refund` | `orders.refund` | `unsafe` | third_party_report | Общие ограничения ниже |
| `reviews.get` | `reviews.get` | `safe` | observed | Общие ограничения ниже |
| `reviews.leave` | `reviews.leave` | `idempotent` | third_party_report | Общие ограничения ниже |
| `reviews.remove` | `reviews.remove` | `idempotent` | third_party_report | Общие ограничения ниже |
| `session.health` | `account.profile` | `safe` | observed | `single_flight_reauth` |

## Отсутствующие события

Регистрация обработчика каждого из этих событий отклоняется с ConfigurationError. Изменения цены рынка не заменяют события собственного лота.

| Событие | Оставшаяся работа |
| --- | --- |
| `review.changed` | E: источник изменений отзывов и восстановление доставки |
| `lot.price_changed` | E: изменения собственных лотов отдельно от рынка |
| `lot.stock_changed` | C, E: типизированный остаток и неизвестное значение |
| `seller.online_changed` | B, E: наблюдённый статус и смена присутствия |

## Открытые ограничения и этапы

Группы пересекаются. Запись закрывается после реализации и проверки её условий, а не после появления имени метода. Межъязыковой пункт не блокирует Python.

B - наблюдения; C - чтение и модели; D - управление лотами; E - события; F - сессия и runner; G - нагрузка и обработчики; H - ошибки и совместимость; I - финансовые операции. A - актуализация контрактов и этой матрицы.

| Пункт | Этап | Формулировка реестра |
| --- | --- | --- |
| `budget_request_preemption` | G | Вытеснение УЖЕ ИДУЩЕГО запроса: признаки preemptible deferrable и cancellable применяются на входе, но начатый запрос не отменяется и не откладывается. |
| `lane_dropping` | G | Признак droppable у полосы очереди доставки и сжатие событий полосы monitoring. |
| `handler_timeout_for_plain_calls` | G | Предел времени над НЕОТМЕНЯЕМЫМ вызовом обработчика - тем, который вернул не ожидаемое, а обычное значение. |
| `dyn_table_continue_request` | B, C | Догрузка dyn-table за пределами отзывов ещё не выполняется. |
| `showcase_truncation` | B, C | Показывает ли профиль всю витрину продавца или её начало. |
| `runner_error_shape` | B, F | Что канал /runner/ кладёт в response.error при неудаче. |
| `withdraw_stays_unwritten` | I | ВЫВОД СРЕДСТВ ОСТАЁТСЯ НЕНАПИСАННЫМ - решением, а не по недостатку сведений. Сведений как раз хватает. Форма вывода наблюдена нами; состав полей и ключи ответа известны от независимой реализации того же протокола - столько же, сколько было у возврата, который 31.08.2026 написан. |
| `promote_success_shape_unobserved` | B, D | Операция lots.promote написана и работает. Непроверен вид УСПЕШНОГО ответа: наблюдался ровно один ответ, и он, судя по сроку в теле, был отказом по неистёкшему остыванию. |
| `lot_visibility_request_unobserved` | B, D | ВСЕ ЧЕТЫРЕ ОПЕРАЦИИ ЗАПИСИ НАД ЛОТАМИ НАПИСАНЫ 31.08.2026: правка цены, поднятие, включение и выключение. Прежняя запись отсюда снята целиком. Эта - НЕ про отсутствие операции. Включение и выключение работают; непроверена ЧАСТЬ их запроса, и записана она здесь потому, что реестр обязан знать не только о ненаписанном, но и о написанном на чужом слове. |
| `order_params_unnamed` | C, H | Восемь параметров заказа отдаются перечнем «метка и значение» без имён. |
| `order_chat_truncation` | B, C | Полна ли переписка, показанная на странице заказа. |
| `money_mapping_for_absent_languages` | Вне Python | Отображение доменного типа money в пять языков, реализаций которых не существует: typescript - bigint, java - long с BigDecimal в помощнике, dotnet - long с decimal в помощнике, cpp и c - int64_t. |
| `domain_not_found` | B, H | EntityNotFoundError при обращении к несуществующей сущности. |
| `domain_conflict_error` | B, H | ConflictError - отказ при столкновении с параллельным изменением. |
| `parse_error_class` | H | Отказ ParseError при значении, не приводящемся к доменному типу. |
| `channel_replay_by_position` | B, F | Повтор пропущенного по позиции: узнать из канала не только «изменилось ли», но и что именно осталось непрочитанным за простой. |
| `single_flight_reauth` | F | Признак requires: single_flight_reauth у политики истёкшей сессии. Восстановление сессии однократное: десять параллельных запросов, одновременно получивших отказ, обязаны дождаться одного восстановления, а не запустить десять. |
| `event_identity_source` | F | Выбор источника идентичности события: preferred: remote_id, fallback: deterministic_fingerprint. Удалённый идентификатор площадки предпочитается вычисленному отпечатку. |
| `ordering_causality` | E, F | Правило причинности: события, причинно связанные с одним заказом, получают ключ заказа, даже если наблюдаются в переписке. |
| `lots_deactivate_guards` | E | Три предохранителя автоматической деактивации лота: известный нулевой остаток, два последовательных наблюдения из разных запросов и остановка автоматики при деградации с возобновлением только явным действием. |
| `instant_attributes` | C, H | Два атрибута момента с закрытыми перечислениями: precision - вправду ли метка точна или сведена из «вчера, 14:32», и origin - абсолютной она была или вычислена из относительной формулировки. |
| `chats_sending_form` | A, H | Адрес и метод формы отправки сообщения, наблюдённые в разметке, и утверждение об отсутствии скрытых полей. |

## Объём сверх текущих сервисных операций

- Создание, полная правка и удаление лотов; чтение и сохранение собственных chips.
- Реквизиты, кошельки и вывод: `account.withdraw` сейчас отклоняется типизированной ошибкой и не входит в контрактные операции. Нужны контракт, защита повторов и отдельная приёмка.
- Выбор разделов поднятия; проверка состава публичного профиля и страницы лота.
- Сверка составных сценариев Cardinal: пакетные истории чатов, загрузка изображения отдельно от отправки, lookup диалогов и logout. Новый метод нужен при самостоятельном поведении, а не ради копирования вспомогательных имён.

## Runtime и итоговая приёмка

`monitoring.plan/watch`, восстановление очередей, бюджеты, дедупликация и журнал исходящих проверяются отдельно от HTTP-операций: `tests/test_monitoring_history.py`, `tests/test_monitoring_contract.py`, `tests/test_watch_contract.py`, `tests/test_budget_concurrency.py`, `tests/test_outbound_restore.py`. Оставшиеся ограничения runtime перечислены в общей таблице.

Для выпуска нужны проверенные живые сценарии, ветви ошибок и отмены, длительный прогон, сборка и установка wheel/sdist и миграция состояния. См. [проверки](validation.md) и [ограничения](limits.md).

Обновление: `python tools/completeness.py`; проверка без записи: `python tools/completeness.py --check`. Обе команды требуют `FUNORA_SPEC_DIR`.
