"""Панель быстрого доступа внизу по центру экрана (plan/03, plan/10).

Хотбар — обычный контейнер дрона (`sim.hotbar`), а здесь только его вид:
слоты со значком и числом стака, цифра-подсказка 1…9, подсветка выбранного
слота и подпись с именем при наведении. Панель видна всегда, пока идёт игра,
в том числе поверх открытых окон.

Выбор слота — команда симуляции (`SelectHotbar`), которую создаёт приложение;
панель только отвечает, в какой слот попал курсор.
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.render import theme
from src.game.render.icons import IconCache
from src.game.ui import widgets

SLOT = 44                 # сторона слота, пиксели
GAP = 4                   # зазор между слотами
PAD = 6                   # отступ слотов от края панели
ICON_INSET = 6            # насколько значок меньше слота
TOOLTIP_GAP = 6           # от подписи до верхнего края панели
SELECT_WIDTH = 2          # толщина рамки выбранного слота


class HotbarBar:
    """Вид хотбара. Раскладка детерминирована: её же читают клики."""

    def __init__(self, icons: IconCache, slots: int = config.HOTBAR_SLOTS) -> None:
        self.icons = icons
        self.slots = int(slots)

    # ------------------------------------------------------------- раскладка
    @property
    def rect(self) -> pygame.Rect:
        """Вся панель: по центру снизу, над краем экрана на `UI_MARGIN`."""
        w = PAD * 2 + self.slots * SLOT + (self.slots - 1) * GAP
        h = PAD * 2 + SLOT
        x = config.WINDOW_W // 2 - w // 2
        y = config.WINDOW_H - config.UI_MARGIN - h
        return pygame.Rect(x, y, w, h)

    def slot_rects(self) -> list[pygame.Rect]:
        panel = self.rect
        return [pygame.Rect(panel.x + PAD + i * (SLOT + GAP), panel.y + PAD, SLOT, SLOT)
                for i in range(self.slots)]

    def hit(self, pos: tuple[int, int]) -> int | None:
        """Номер слота под точкой или None."""
        for i, rect in enumerate(self.slot_rects()):
            if rect.collidepoint(pos):
                return i
        return None

    def contains(self, pos: tuple[int, int]) -> bool:
        """Над панелью ли курсор: тогда мир под ней не получает ни клик, ни призрак."""
        return self.rect.collidepoint(pos)

    # ------------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, sim, mouse: tuple[int, int] | None = None,
             tooltip: bool = True) -> None:
        widgets.draw_panel(target, self.rect, alpha=236)
        hover = self.hit(mouse) if mouse is not None else None
        selected = sim.selected_slot
        for i, rect in enumerate(self.slot_rects()):
            pygame.draw.rect(target, theme.HOTBAR_SLOT, rect, border_radius=4)
            if i == selected:
                pygame.draw.rect(target, theme.HOTBAR_SELECT, rect.inflate(2, 2),
                                 SELECT_WIDTH, border_radius=5)
            else:
                border = theme.SLOT_HOVER if i == hover else theme.SLOT_BORDER
                pygame.draw.rect(target, border, rect, 1, border_radius=4)
            stack = sim.hotbar.slot(i)
            if stack is not None:
                icon = self.icons.item_icon_scaled(stack.item_id, SLOT - ICON_INSET * 2)
                target.blit(icon, icon.get_rect(center=rect.center))
                if stack.count > 1:
                    label = self.icons.mark_icon(f"×{stack.count}", theme.UI_TEXT)
                    target.blit(label, label.get_rect(
                        bottomright=(rect.right - 2, rect.bottom - 1)))
            digit = self.icons.mark_icon(str(i + 1), theme.HOTBAR_KEY)
            target.blit(digit, (rect.x + 3, rect.y + 1))
        if tooltip and hover is not None:
            self._draw_tooltip(target, sim, hover)

    def _draw_tooltip(self, target: pygame.Surface, sim, index: int) -> None:
        """Имя предмета над слотом: внизу экрана подсказке под курсором нет места."""
        stack = sim.hotbar.slot(index)
        if stack is None:
            return
        text = sim.run.items[stack.item_id].name
        size = config.UI_FONT_SIZE_SMALL
        w = widgets.text_width(text, size) + 16
        h = size + 10
        slot = self.slot_rects()[index]
        x = min(max(slot.centerx - w // 2, 0), config.WINDOW_W - w)
        y = self.rect.top - h - TOOLTIP_GAP
        widgets.draw_panel(target, (x, y, w, h), alpha=236)
        widgets.draw_text(target, text, (x + 8, y + 4), size=size)
