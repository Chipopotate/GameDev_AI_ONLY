"""Кэш шрифтов.

Создавать шрифт каждый кадр — типовая ошибка производительности (docs/07 §13).
Шрифт берётся встроенный (FreeSans) — в нём есть кириллица, внешние файлы не нужны.
"""

from __future__ import annotations

import pygame

_cache: dict[int, pygame.font.Font] = {}


def get_font(size: int) -> pygame.font.Font:
    if not pygame.font.get_init():
        # После font.quit() старые Font-объекты недействительны — кэш сбрасываем.
        pygame.font.init()
        _cache.clear()
    font = _cache.get(size)
    if font is None:
        font = pygame.font.Font(None, int(size))
        _cache[size] = font
    return font


def clear_cache() -> None:
    _cache.clear()
