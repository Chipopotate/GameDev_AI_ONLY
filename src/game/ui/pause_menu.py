"""Меню паузы на Esc: «Продолжить» и «Выйти» (просьба автора, M6).

Esc больше не закрывает игру сразу: случайное нажатие стоило бы забега.
Меню останавливает симуляцию, как пауза, и выходит из игры только кнопкой.
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.render import theme
from src.game.ui import widgets

ACTION_RESUME = "resume"
ACTION_QUIT = "quit"
BUTTONS = (("Продолжить", ACTION_RESUME), ("Выйти", ACTION_QUIT))
BUTTON_W = 220
BUTTON_H = 40
BUTTON_GAP = 12
TITLE_GAP = 56


class PauseMenu:
    """Окно меню паузы. Раскладка детерминирована — её же читают клики."""

    def __init__(self) -> None:
        self.visible = False

    def toggle(self) -> None:
        self.visible = not self.visible

    def buttons(self) -> list[tuple[str, str, pygame.Rect]]:
        total = len(BUTTONS) * BUTTON_H + (len(BUTTONS) - 1) * BUTTON_GAP
        x = config.WINDOW_W // 2 - BUTTON_W // 2
        y = config.WINDOW_H // 2 - total // 2 + TITLE_GAP // 2
        return [(label, action, pygame.Rect(x, y + i * (BUTTON_H + BUTTON_GAP),
                                            BUTTON_W, BUTTON_H))
                for i, (label, action) in enumerate(BUTTONS)]

    def click(self, pos: tuple[int, int]) -> str | None:
        """Какая кнопка нажата (ACTION_*) или None."""
        for _label, action, rect in self.buttons():
            if rect.collidepoint(pos):
                return action
        return None

    def draw(self, target: pygame.Surface, mouse: tuple[int, int]) -> None:
        if not self.visible:
            return
        buttons = self.buttons()
        top = buttons[0][2].top - TITLE_GAP
        widgets.draw_text(target, "ПАУЗА", (config.WINDOW_W // 2, top),
                          color=theme.UI_TEXT, size=config.UI_FONT_SIZE_LARGE,
                          align="center")
        for label, _action, rect in buttons:
            hovered = rect.collidepoint(mouse)
            widgets.draw_panel(target, rect, alpha=245 if hovered else 215)
            if hovered:
                pygame.draw.rect(target, theme.ACCENT, rect, 1, border_radius=6)
            widgets.draw_text(target, label, (rect.centerx, rect.centery - 8),
                              color=theme.UI_TEXT if hovered else theme.UI_TEXT_DIM,
                              size=config.UI_FONT_SIZE, align="center")
        widgets.draw_text(target, "Esc — продолжить",
                          (config.WINDOW_W // 2, buttons[-1][2].bottom + 14),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL,
                          align="center")
