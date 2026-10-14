"""Сетка слотов: раскладка, попадание мышью, отрисовка (M3.5, plan/03).

Одна сетка на все контейнеры: инвентарь дрона, слоты лаборатории, а дальше —
склад, конвейер, турель. Контейнер (`sim/resources.py`) не знает про интерфейс,
сетка не знает, чей это контейнер: ей передают размер и содержимое.

Слоты рисуются примитивами, значки берутся из кэша `IconCache` — кадр не
создаёт ни Surface, ни шрифт (plan/07 §4).
"""

from __future__ import annotations

import math

import pygame

from src.game import config
from src.game.render import theme
from src.game.render.icons import IconCache
from src.game.sim.resources import Container

SLOT_PX = 34               # сторона слота в пикселях
SLOT_GAP = 4               # зазор между слотами
SLOT_INSET = 3             # насколько значок меньше слота


class SlotGrid:
    """Сетка слотов фиксированной ширины: `cols` слотов в строке."""

    def __init__(self, cols: int, slot: int = SLOT_PX, gap: int = SLOT_GAP) -> None:
        if cols < 1:
            raise ValueError("в сетке должна быть хотя бы одна колонка")
        self.cols = int(cols)
        self.slot = int(slot)
        self.gap = int(gap)

    def rows(self, count: int) -> int:
        return max(1, math.ceil(count / self.cols))

    def size(self, count: int) -> tuple[int, int]:
        """Размер сетки под `count` слотов: (ширина, высота) в пикселях."""
        return (self.cols * self.slot + (self.cols - 1) * self.gap,
                self.rows(count) * self.slot + (self.rows(count) - 1) * self.gap)

    def rects(self, origin: tuple[int, int], count: int) -> list[pygame.Rect]:
        """Прямоугольники слотов по порядку индексов контейнера."""
        ox, oy = origin
        step = self.slot + self.gap
        out: list[pygame.Rect] = []
        for i in range(count):
            row, col = divmod(i, self.cols)
            out.append(pygame.Rect(ox + col * step, oy + row * step, self.slot,
                                   self.slot))
        return out

    def hit(self, origin: tuple[int, int], count: int,
            pos: tuple[int, int]) -> int | None:
        """Индекс слота под точкой или None, если мимо."""
        px, py = pos
        for i, rect in enumerate(self.rects(origin, count)):
            if rect.collidepoint(px, py):
                return i
        return None

    def draw(self, target: pygame.Surface, origin: tuple[int, int],
             container: Container, icons: IconCache,
             active: int | None = None,
             hover: int | None = None) -> None:
        """Рисует слоты контейнера: пустые — рамкой, занятые — значком и числом.

        `active` — слот, который прямо сейчас работает (образец в лаборатории);
        `hover` — слот под курсором.
        """
        for i, rect in enumerate(self.rects(origin, container.size)):
            stack = container.slot(i)
            pygame.draw.rect(target, theme.SLOT_BG, rect, border_radius=3)
            border = theme.SLOT_BORDER
            if i == active:
                border = theme.SLOT_ACTIVE
            elif i == hover:
                border = theme.SLOT_HOVER
            pygame.draw.rect(target, border, rect, 1, border_radius=3)
            if stack is None:
                continue
            size = self.slot - SLOT_INSET * 2
            icon = icons.item_icon_scaled(stack.item_id, size)
            target.blit(icon, icon.get_rect(center=rect.center))
            if stack.count > 1:
                label = icons.mark_icon(f"×{stack.count}", theme.UI_TEXT)
                target.blit(label, label.get_rect(
                    bottomright=(rect.right - 2, rect.bottom - 1)))
