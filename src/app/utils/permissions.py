"""Проверки прав доступа."""

import discord

import config


def is_staff(member: discord.Member | None) -> bool:
    """Модератор ли участник.

    Модератором считается тот, у кого есть право Discord
    (администратор / управление сервером / управление сообщениями)
    или одна из стафф-ролей по ID из .env (STAFF_ROLE_IDS в config).
    """
    if member is None:
        return False

    perms = getattr(member, "guild_permissions", None)
    if perms is not None and (perms.administrator or perms.manage_guild or perms.manage_messages):
        return True

    staff_ids = set(config.STAFF_ROLE_IDS)
    if staff_ids:
        member_role_ids = {getattr(role, "id", None) for role in getattr(member, "roles", [])}
        if staff_ids & member_role_ids:
            return True

    return False
