# Асинхронный клиент

Фасада два, ядро одно.

```python
import asyncio

from funora import AsyncClient, EnvSecretProvider


async def main() -> None:
    """Читает список продаж асинхронно.

    Возвращает:
        None
    """
    async with AsyncClient(EnvSecretProvider()) as client:
        page = await client.orders.list()
        for order in page.rows():
            print(order.order_id, order.description_text.or_none())


asyncio.run(main())
```

Разница с синхронным клиентом ровно одна: `async with` вместо `with` и `await`
перед операцией.

## Почему это не два разных SDK

Нормативный порядок шагов, политика повторов, расход бюджета и правила курсора
написаны **один раз** и обоим фасадам достаются готовыми. Ядро не вызывает
ввод-вывод - оно просит о нём и ждёт ответа; исполняет просьбу драйвер, и
драйвера два.

Практическое следствие для вас: перевод бота на асинхронность - это дописать
`await`, а не переписать логику.

Практическое следствие для проекта: расхождение фасадов ловится прогоном.
Появись операция в одном и не появись в другом - сборка упадёт, а не вы при
переводе.

## События

```python
import asyncio

from funora import AsyncClient, EnvSecretProvider, EventType, Router

router = Router()


@router.on(EventType.MESSAGE_CREATED)
async def on_message(event) -> None:
    """Отвечает на новое сообщение.

    Аргументы:
        event (Event): Событие.

    Возвращает:
        None
    """
    print("сообщение", event.entity_id)


async def main() -> None:
    """Запускает наблюдение.

    Возвращает:
        None
    """
    async with AsyncClient(EnvSecretProvider()) as client:
        await client.watch(router)


asyncio.run(main())
```

Обработчики могут быть и обычными функциями, и корутинами.

## Чего ждать не стоит

Асинхронность **не** ускоряет чтение площадки: пределы те же, бюджет тот же,
ведро хоста общее. Она нужна затем, чтобы бот, ждущий ответа площадки, мог в это
время заниматься чем-то ещё, - а не затем, чтобы читать чаще.
