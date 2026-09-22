# Архитектура

Как устроен код. Прикладная часть содержит около 2200 строк Python, тесты — около 3400 строк.

## Общая схема

Бот - обычное приложение на discord.py 2.x с префиксными командами. Вход - `main.py`, логика разбита на три модуля предметной области плюс два служебных:

```
src/app/
├── main.py              - точка входа: интенты, lifecycle и обработка ошибок
├── config.py            - настройки, тексты и fail-fast валидация
├── requirements.txt
├── .env.example
│
├── database/            - слой данных на sqlite3
│   ├── db.py            - get_db(): открыть соединение
│   ├── tickets_db.py    - таблицы tickets и stats, CRUD
│   └── afk_db.py        - таблицы afk_users/afk_cooldown/afk_stats
│
├── tickets/             - заявки
│   ├── commands.py      - !famqcore, !stats, !history + панель типов заявок
│   ├── create_ticket.py - форма TicketModal и создание тикета
│   ├── decision.py      - единый сценарий «Принять»/«Отказать»
│   ├── call_voice.py    - вызов на обзвон
│   ├── close_ticket.py  - закрытие тикета и транскрипт
│   └── views.py         - FullTicketView, собирает кнопки
│
├── afk/                 - AFK
│   ├── commands.py      - !afk, !afk_list, !afk_check, !afk_stats
│   ├── events.py        - on_message: автоответ на упоминание
│   ├── models.py        - бизнес-логика: set/remove, ники, format_duration
│   ├── tasks.py         - периодическое снятие истёкших AFK
│   └── views.py         - меню, формы, build_afk_embed, parse_return_time
│
├── utils/
│   ├── logger.py        - консоль + файл с ротацией
│   ├── logcenter.py     - приватный канал и тематические ветки логов
│   ├── permissions.py   - единая проверка staff-прав
│   └── resolve.py       - поиск ролей/каналов по ID с fallback по имени
│
└── tests/               - unittest + mock, покрытие ключевых сценариев БД, тикетов и AFK
```

## Жизненный цикл запуска

1. `FamqCoreBot.setup_hook()` один раз создаёт таблицы, загружает расширения `tickets`/`afk` и регистрирует persistent views.
2. AFK-расширение запускает фоновую проверку истёкших статусов после готовности бота.
3. `on_ready` только пишет успешное подключение в лог и безопасно может вызываться повторно при reconnect.
4. Перед `bot.run()` конфигурация проходит fail-fast валидацию; тесты выполняются отдельно локально и в CI.

## Поток заявки

```
!famqcore -> TicketTypeView (кнопки RP/CAPT)
        -> TicketModal (5 полей из config)
        -> create_ticket(): роль -> ЛС -> категория -> канал -> запись в БД
        -> FullTicketView в канале тикета
        -> Accept/Deny: DecisionReasonModal -> update_ticket_status -> лог-канал -> удаление канала
```

## Поток AFK-автоответа

```
on_message -> для каждого упомянутого: afk_db.get_afk_user
           -> check_and_reply (кулдаун 30с по паре упомянувший/AFK, таблица afk_cooldown)
           -> ответ в канал с delete_after=60
           -> bot.process_commands (чтобы работали команды)
```

## База данных

Один файл SQLite `src/app/database/database.db`, `row_factory = sqlite3.Row` — строки читаются как словари. Соединение открывается и закрывается на каждый запрос; это простой и надёжный вариант для небольших и средних сообществ.

### tickets

| Колонка | Тип | Комментарий |
|---------|-----|-------------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `guild_id` | INTEGER | ID Discord-сервера; все выборки тикетов идут через него |
| `channel_id` | INTEGER UNIQUE | Ключ поиска тикета |
| `user_id` | INTEGER | ID заявителя; `0` после анонимизации |
| `user_name` | TEXT | Имя на момент подачи |
| `topic` | TEXT | Заголовок формы |
| `type` | TEXT | `rp` или `capt` |
| `answers` | TEXT | JSON с ответами |
| `status` | TEXT | `open` / `accepted` / `denied`, по умолчанию `open` |
| `created_at` | TEXT | ISO-время |
| `closed_at` | TEXT | ISO-время закрытия |
| `closed_by` | INTEGER | Кто решил |
| `reason` | TEXT | Причина решения |

### stats (дневная сводка)

| Колонка | Тип | Комментарий |
|---------|-----|-------------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `guild_id` | INTEGER | ID Discord-сервера |
| `date` | TEXT | День YYYY-MM-DD; уникален в паре с `guild_id` |
| `total_applications` | INTEGER | Решений за день |
| `accepted` | INTEGER | |
| `denied` | INTEGER | |

### afk_users

| Колонка | Тип | Комментарий |
|---------|-----|-------------|
| `user_id` | INTEGER PK(1) | |
| `guild_id` | INTEGER PK(1) | Пара - первичный ключ, есть индекс по guild_id |
| `afk_reason` | TEXT | По умолчанию «Отошёл» |
| `afk_since` | TEXT | ISO-время старта |
| `estimated_return` | TEXT | Когда обещал вернуться |
| `is_afk` | INTEGER | Всегда 1 - колонка-пережиток, записи просто удаляются при возврате |

### afk_cooldown

Ключ (`guild_id`, `mentioner_id`, `afk_user_id`) хранит время последнего автоответа. Один и тот же пользователь на разных серверах получает независимый кулдаун. Фоновая AFK-задача удаляет старые записи.

### afk_stats

Ключ (`guild_id`, `user_id`) + счётчики: уходов, суммарные секунды, рекорд сессии. Статистика AFK не смешивается между серверами.

## Принципы, которые держим в коде

- Все тексты и настройки - в `config.py`.
- Один модуль - одна зона ответственности, логика не расползается по импортам.
- Функции маленькие, вложенность до двух уровней.
- Стиль - минимализм: без прослоек «на будущее».

## Известные архитектурные слабости

Честно перечисляем, чтобы новичок не изобретал:

- Часть бизнес-логики находится в UI-колбэках; при росте проекта её стоит переносить в сервисный слой.
- SQLite синхронный; для высоконагруженных сценариев потребуется выделенный слой хранения.
- Для будущих интеграций полезно согласовать единый стандарт часового пояса.
- В приоритете используются ID объектов, но fallback по имени остаётся хрупким.
