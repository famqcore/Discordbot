"""Политика упоминаний (mentions) и обработка недоверенного текста.

Правило проекта: бот никогда не рассылает пинги из текста, введённого
участником (причины AFK, ответы форм, причины решений). Весь такой текст
перед публикацией в обычном сообщении проходит через escape_user_text().

Доверенные служебные пинги формируются только самим ботом из объектов
Discord (роли рекрутёров из .env, участники тикета) и отправляются с явным
адресным AllowedMentions через mentions_for().
"""

from __future__ import annotations

from collections.abc import Iterable

import discord

# Клиентский default: без массовых пингов и без ролей. user-пинги разрешены,
# потому что все пользовательские строки экранируются до отправки, а служебные
# упоминания участников (заявитель в тикете, AFK-пользователь в автоответе)
# — ожидаемое поведение.
DEFAULT_ALLOWED_MENTIONS = discord.AllowedMentions(
    everyone=False,
    roles=False,
    users=True,
    replied_user=False,
)


def mentions_for(
    *,
    users: Iterable[discord.abc.Snowflake] = (),
    roles: Iterable[discord.abc.Snowflake] = (),
) -> discord.AllowedMentions:
    """Адресный AllowedMentions: разрешены только перечисленные участники/роли.

    Используется в send-вызовах, где бот сам составляет упоминания
    (пинг рекрутёров в новом тикете, вызов на обзвон, автоответ AFK).
    """
    return discord.AllowedMentions(
        everyone=False,
        roles=list(roles),
        users=list(users),
        replied_user=False,
    )


def escape_user_text(text: str | None) -> str:
    """Экранирует пользовательский текст так, чтобы он не мог пинговать.

    discord.utils.escape_mentions гасит @everyone, @here, упоминания
    участников (<@…>, <@!…>) и ролей (<@&…>) за счёт zero-width space.
    """
    if not text:
        return ""
    return discord.utils.escape_mentions(text)
