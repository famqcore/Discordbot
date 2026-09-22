"""Пакет тестов.

Политика предупреждений (issue #16): собственные DeprecationWarning и
RuntimeWarning — это ошибки, иначе «coroutine was never awaited» тихо
маскирует неработающий тест. Исключение одно: discord.py 2.7.1 на
Python 3.11 импортирует стандартный модуль ``audioop``, помеченный к
удалению в 3.13. Это предупреждение третьей стороны, чинить его в этом
репозитории нечем — оно подавляется точечно, по тексту сообщения.

Фильтры ставятся здесь, потому что пакет импортируется раньше любого
тестового модуля и раньше первого ``import discord``.
"""

import warnings

warnings.simplefilter("error", DeprecationWarning)
warnings.simplefilter("error", RuntimeWarning)

# Третья сторона: discord.player -> import audioop (удалён в Python 3.13).
warnings.filterwarnings("ignore", message=r".*audioop.*")
