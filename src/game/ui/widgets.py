"""Простые элементы интерфейса: панели, полосы, текст.

Панели кэшируются по размеру — Surface не создаётся каждый кадр.
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.render import theme
from src.game.ui.fonts import get_font

_panel_cache: dict[tuple[int, int, int, int], pygame.Surface] = {}


def panel_surface(w: int, h: int, alpha: int = theme.UI_BG_ALPHA,
                  radius: int = 6) -> pygame.Surface:
    """Полупрозрачная панель с рамкой. Кэшируется по размеру."""
    key = (w, h, alpha, radius)
    surf = _panel_cache.get(key)
    if surf is None:
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (*theme.UI_BG, alpha), (0, 0, w, h), border_radius=radius)
        pygame.draw.rect(surf, (*theme.UI_BORDER, 220), (0, 0, w, h), 1,
                         border_radius=radius)
        _panel_cache[key] = surf
    return surf


def clear_cache() -> None:
    _panel_cache.clear()


def draw_panel(target: pygame.Surface, rect: tuple[int, int, int, int],
               alpha: int = theme.UI_BG_ALPHA, radius: int = 6) -> None:
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return
    target.blit(panel_surface(w, h, alpha, radius), (x, y))


def draw_text(target: pygame.Surface, text: str, pos: tuple[int, int], color=theme.UI_TEXT,
              size: int = 16, align: str = "left") -> pygame.Rect:
    """Рисует текст. align: left | center | right."""
    surf = get_font(size).render(text, True, color)
    rect = surf.get_rect()
    if align == "center":
        rect.midtop = pos
    elif align == "right":
        rect.topright = pos
    else:
        rect.topleft = pos
    target.blit(surf, rect)
    return rect


def text_width(text: str, size: int = 16) -> int:
    return get_font(size).size(text)[0]


def draw_tooltip(target: pygame.Surface, text: str, pos: tuple[int, int],
                 size: int = config.UI_FONT_SIZE_SMALL) -> pygame.Rect:
    """Подсказка у курсора (plan/07 §10): имя предмета, число единиц.

    Прижимается к экрану, чтобы не уехать за край, но сама подсказка не
    переворачивается: игрок ищет её всегда в одном месте — под курсором.
    """
    w = text_width(text, size) + 16
    h = size + 10
    x = min(max(pos[0] + 14, 0), max(0, config.WINDOW_W - w))
    y = min(max(pos[1] + 16, 0), max(0, config.WINDOW_H - h))
    draw_panel(target, (x, y, w, h), alpha=236)
    draw_text(target, text, (x + 8, y + 4), size=size)
    return pygame.Rect(x, y, w, h)


def draw_bar(target: pygame.Surface, rect: tuple[int, int, int, int], frac: float,
             color=theme.OK, bg=theme.MINE_BAR_BG) -> None:
    """Полоса заполнения 0..1 с рамкой."""
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return
    pygame.draw.rect(target, bg, rect, border_radius=2)
    filled = int(w * max(0.0, min(1.0, frac)))
    if filled > 0:
        pygame.draw.rect(target, color, (x, y, filled, h), border_radius=2)
    pygame.draw.rect(target, theme.UI_BORDER, rect, 1, border_radius=2)
