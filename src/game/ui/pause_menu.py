"""Меню паузы на Esc: «Продолжить», «Помощь» и «Выйти» (просьбы автора).

Esc больше не закрывает игру сразу: случайное нажатие стоило бы забега.
Меню останавливает симуляцию, как пауза, и выходит из игры только кнопкой.
«Помощь» — окно с таблицей управления (`ui/controls.py` = plan/10): раньше она
постоянно висела в HUD и закрывала угол карты.
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.render import theme
from src.game.ui import widgets
from src.game.ui.controls import CONTROLS

ACTION_RESUME = "resume"
ACTION_HELP = "help"
ACTION_BACK = "back"
ACTION_QUIT = "quit"
BUTTONS = (("Продолжить", ACTION_RESUME), ("Помощь", ACTION_HELP), ("Выйти", ACTION_QUIT))
#: Окно помощи: ширина колонки клавиш и действий, шаг строк, отступы.
HELP_KEY_W = 320
HELP_ACTION_W = 600
HELP_ROW = 22
HELP_PAD = 18
BUTTON_W = 220
BUTTON_H = 40
BUTTON_GAP = 12
TITLE_GAP = 56


class PauseMenu:
    """Окно меню паузы. Раскладка детерминирована — её же читают клики."""

    def __init__(self) -> None:
        self.visible = False
        #: Открыто ли окно «Помощь» (таблица управления) вместо кнопок меню.
        self.help = False
        self._help_surface: pygame.Surface | None = None

    def toggle(self) -> None:
        """Esc в меню: из «Помощи» — назад к кнопкам, иначе закрыть меню."""
        if self.visible and self.help:
            self.help = False
            return
        self.visible = not self.visible
        self.help = False

    def buttons(self) -> list[tuple[str, str, pygame.Rect]]:
        if self.help:
            panel = self._help_rect()
            rect = pygame.Rect(0, 0, BUTTON_W, BUTTON_H)
            rect.midbottom = (panel.centerx, panel.bottom - HELP_PAD // 2)
            return [("Назад", ACTION_BACK, rect)]
        total = len(BUTTONS) * BUTTON_H + (len(BUTTONS) - 1) * BUTTON_GAP
        x = config.WINDOW_W // 2 - BUTTON_W // 2
        y = config.WINDOW_H // 2 - total // 2 + TITLE_GAP // 2
        return [(label, action, pygame.Rect(x, y + i * (BUTTON_H + BUTTON_GAP),
                                            BUTTON_W, BUTTON_H))
                for i, (label, action) in enumerate(BUTTONS)]

    def click(self, pos: tuple[int, int]) -> str | None:
        """Какая кнопка нажата (ACTION_*) или None. «Помощь» и «Назад» — здесь же."""
        for _label, action, rect in self.buttons():
            if rect.collidepoint(pos):
                if action == ACTION_HELP:
                    self.help = True
                elif action == ACTION_BACK:
                    self.help = False
                return action
        return None

    # ------------------------------------------------------------ помощь
    def _help_rect(self) -> pygame.Rect:
        w = HELP_KEY_W + HELP_ACTION_W + HELP_PAD * 3
        h = HELP_PAD * 2 + 40 + len(CONTROLS) * HELP_ROW + BUTTON_H + HELP_PAD
        rect = pygame.Rect(0, 0, min(w, config.WINDOW_W - 20), min(h, config.WINDOW_H - 20))
        rect.center = (config.WINDOW_W // 2, config.WINDOW_H // 2)
        return rect

    def _help_panel(self) -> pygame.Surface:
        """Таблица управления — одна поверхность на всю игру (таблица статична)."""
        if self._help_surface is None:
            rect = self._help_rect()
            surf = pygame.Surface(rect.size, pygame.SRCALPHA)
            widgets.draw_panel(surf, (0, 0, rect.width, rect.height), alpha=248)
            widgets.draw_text(surf, "Помощь — управление", (HELP_PAD, HELP_PAD),
                              size=config.UI_FONT_SIZE_LARGE)
            top = HELP_PAD + 40
            for i, (key, action) in enumerate(CONTROLS):
                y = top + i * HELP_ROW
                widgets.draw_text(surf, key, (HELP_PAD, y), color=theme.ACCENT,
                                  size=config.UI_FONT_SIZE)
                widgets.draw_text(surf, action, (HELP_PAD * 2 + HELP_KEY_W, y),
                                  color=theme.UI_TEXT, size=config.UI_FONT_SIZE)
            self._help_surface = surf
        return self._help_surface

    def draw(self, target: pygame.Surface, mouse: tuple[int, int]) -> None:
        if not self.visible:
            return
        if self.help:
            target.blit(self._help_panel(), self._help_rect().topleft)
            for label, _action, rect in self.buttons():
                self._draw_button(target, label, rect, mouse)
            return
        buttons = self.buttons()
        top = buttons[0][2].top - TITLE_GAP
        widgets.draw_text(target, "ПАУЗА", (config.WINDOW_W // 2, top),
                          color=theme.UI_TEXT, size=config.UI_FONT_SIZE_LARGE,
                          align="center")
        for label, _action, rect in buttons:
            self._draw_button(target, label, rect, mouse)
        widgets.draw_text(target, "Esc — продолжить",
                          (config.WINDOW_W // 2, buttons[-1][2].bottom + 14),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL,
                          align="center")

    @staticmethod
    def _draw_button(target: pygame.Surface, label: str, rect: pygame.Rect,
                     mouse: tuple[int, int]) -> None:
        hovered = rect.collidepoint(mouse)
        widgets.draw_panel(target, rect, alpha=245 if hovered else 215)
        if hovered:
            pygame.draw.rect(target, theme.ACCENT, rect, 1, border_radius=6)
        widgets.draw_text(target, label, (rect.centerx, rect.centery - 8),
                          color=theme.UI_TEXT if hovered else theme.UI_TEXT_DIM,
                          size=config.UI_FONT_SIZE, align="center")
