"""Панель крафта в окне инвентаря (plan/02): поиск и сетка известных рецептов.

Как в Minecraft и Factorio: крафт живёт рядом с инвентарём, а не в отдельной
книге. Панель показывает **только изученные рецепты** — неизвестных нет
вообще: ни «???», ни подсказок, ни счётчика «сколько ещё не открыто». Всё,
что она знает, приходит из `sim.craft_options` — объекта, в котором нет ни
id ресурсов, ни сырых свойств: «есть M» считается только по открытым игроку
свойствам.

- поиск — фильтр по названию без учёта регистра, обновляется при вводе;
- рецепт, который игрок может скрафтить сейчас, яркий, недостающий —
  приглушённый, но видимый; сначала доступные, затем по названию;
- клик — скрафтить 1, Shift — ×5, Ctrl — сколько хватит; не вышло — короткая
  красная вспышка рамки, ничего не меняется.

Крафт — команда симуляции (`sim.craft`): логика подбора материалов прежняя.
"""

from __future__ import annotations

import math

import pygame

from src.game import config
from src.game.render import theme
from src.game.render.icons import IconCache
from src.game.ui import widgets
from src.game.ui.state import UiState

CELL = 40                  # сторона ячейки рецепта
GAP = 4
COLS = 5
SEARCH_H = 26
SEARCH_GAP = 8
CLEAR_W = 22               # кнопка «×» очистки поиска
ICON_INSET = 5
#: Сколько Shift-клик крафтит за раз (plan/02).
SHIFT_TIMES = 5
#: Вспышка «не хватает»: сколько миллисекунд горит красная рамка.
FLASH_MS = 320
SEARCH_MAX = 24            # длина строки поиска, символов
PLACEHOLDER = "Поиск…"
EMPTY_TEXT = "Пока нечего крафтить"
NOT_FOUND_TEXT = "Ничего не найдено"
HELP_TEXT = "Клик — 1, Shift — ×5, Ctrl — максимум"
AMPLIFIED_NAME = "{name} (усил.)"
DIM_ALPHA = 150            # насколько приглушён недоступный рецепт

_dims: dict[int, pygame.Surface] = {}


def _dim_overlay(size: int) -> pygame.Surface:
    """Тёмная вуаль поверх недоступного рецепта. Кэшируется по размеру."""
    surf = _dims.get(size)
    if surf is None:
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        surf.fill((*theme.UI_BG, DIM_ALPHA))
        _dims[size] = surf
    return surf


class SearchField:
    """Строка поиска. Фокус — через общий `UiState` (флаг ввода текста)."""

    def __init__(self, ui: UiState) -> None:
        self.ui = ui
        self.text = ""
        self.rect = pygame.Rect(0, 0, 0, 0)

    @property
    def focused(self) -> bool:
        return self.ui.focused is self

    @property
    def clear_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - CLEAR_W, self.rect.y, CLEAR_W, self.rect.height)

    def focus(self) -> None:
        self.ui.focus(self)

    def blur(self) -> None:
        self.ui.blur(self)

    def insert(self, text: str) -> None:
        """Ввод с клавиатуры (событие TEXTINPUT). Без фокуса — ничего."""
        if not self.focused or not text:
            return
        clean = "".join(ch for ch in text if ch.isprintable())
        self.text = (self.text + clean)[:SEARCH_MAX]

    def backspace(self) -> None:
        if self.focused and self.text:
            self.text = self.text[:-1]

    def clear(self) -> None:
        self.text = ""

    def click(self, pos: tuple[int, int]) -> bool:
        """Клик по полю: «×» очищает, остальное поле берёт фокус. Мимо — снимает."""
        if self.text and self.clear_rect.collidepoint(pos):
            self.clear()
            self.focus()
            return True
        if self.rect.collidepoint(pos):
            self.focus()
            return True
        self.blur()
        return False

    def draw(self, target: pygame.Surface) -> None:
        widgets.draw_panel(target, self.rect, alpha=250 if self.focused else 210,
                           radius=4)
        if self.focused:
            pygame.draw.rect(target, theme.ACCENT, self.rect, 1, border_radius=4)
        size = config.UI_FONT_SIZE_SMALL
        x, y = self.rect.x + 8, self.rect.y + (self.rect.height - size) // 2
        if self.text:
            shown = self.text + ("|" if self.focused else "")
            widgets.draw_text(target, shown, (x, y), size=size)
            widgets.draw_text(target, "×", (self.clear_rect.centerx, y - 1),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE,
                              align="center")
        else:
            text = "|" if self.focused else PLACEHOLDER
            widgets.draw_text(target, text, (x, y), color=theme.UI_TEXT_DIM, size=size)


class CraftPanel:
    """Сетка известных рецептов с поиском. Раскладку задаёт окно инвентаря."""

    def __init__(self, icons: IconCache, ui: UiState) -> None:
        self.icons = icons
        self.search = SearchField(ui)
        self.origin = (0, 0)
        #: Когда (мс) в последний раз не вышло скрафтить рецепт — для вспышки.
        self._flash: dict[str, int] = {}

    # ------------------------------------------------------------- раскладка
    @staticmethod
    def rows(sim) -> int:
        """Высота сетки — под **все** рецепты забега, чтобы окно не прыгало.

        Это не подсказка о неоткрытом: высота одна и та же у любого игрока и в
        любой момент забега, видно только то, что уже изучено.
        """
        return max(1, math.ceil(len(sim.run.recipes) / COLS))

    def size(self, sim) -> tuple[int, int]:
        grid_w = COLS * CELL + (COLS - 1) * GAP
        grid_h = self.rows(sim) * CELL + (self.rows(sim) - 1) * GAP
        return grid_w, SEARCH_H + SEARCH_GAP + grid_h + SEARCH_GAP + config.UI_FONT_SIZE_SMALL

    def place(self, origin: tuple[int, int], sim) -> None:
        """Ставит панель в точку окна: поле поиска сверху, сетка под ним."""
        self.origin = origin
        w, _h = self.size(sim)
        self.search.rect = pygame.Rect(origin[0], origin[1], w, SEARCH_H)

    def cell_rects(self, count: int) -> list[pygame.Rect]:
        ox = self.origin[0]
        oy = self.origin[1] + SEARCH_H + SEARCH_GAP
        out = []
        for i in range(count):
            row, col = divmod(i, COLS)
            out.append(pygame.Rect(ox + col * (CELL + GAP), oy + row * (CELL + GAP),
                                   CELL, CELL))
        return out

    def options(self, sim):
        return sim.craft_options(self.search.text)

    def hit(self, pos: tuple[int, int], sim):
        """Рецепт под точкой или None."""
        options = self.options(sim)
        for option, rect in zip(options, self.cell_rects(len(options))):
            if rect.collidepoint(pos):
                return option
        return None

    # ------------------------------------------------------------------ ввод
    def click(self, pos: tuple[int, int], sim, shift: bool = False,
              ctrl: bool = False, when: int | None = None) -> bool:
        """Клик по панели. True — если он пришёлся на поиск или на рецепт."""
        if self.search.click(pos):
            return True
        option = self.hit(pos, sim)
        if option is None:
            return False
        times: int | None = None if ctrl else (SHIFT_TIMES if shift else 1)
        if not sim.craft(option.recipe_id, times):
            now = pygame.time.get_ticks() if when is None else int(when)
            self._flash[option.recipe_id] = now
        return True

    def flashing(self, recipe_id: str, now: int) -> bool:
        started = self._flash.get(recipe_id)
        return started is not None and 0 <= now - started <= FLASH_MS

    # ------------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, sim, mouse: tuple[int, int],
             now: int | None = None) -> None:
        now = pygame.time.get_ticks() if now is None else int(now)
        self.search.draw(target)
        options = self.options(sim)
        rects = self.cell_rects(len(options))
        if not options:
            text = NOT_FOUND_TEXT if self.search.text.strip() and \
                sim.knowledge.known_count else EMPTY_TEXT
            widgets.draw_text(target, text, (self.origin[0],
                                             self.origin[1] + SEARCH_H + SEARCH_GAP + 4),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
        hover = None
        for option, rect in zip(options, rects):
            self._draw_cell(target, option, rect, now)
            if rect.collidepoint(mouse):
                hover = option
        w, h = self.size(sim)
        widgets.draw_text(target, HELP_TEXT, (self.origin[0],
                                              self.origin[1] + h - config.UI_FONT_SIZE_SMALL),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
        if hover is not None:
            self.draw_tooltip(target, hover, mouse)

    def _draw_cell(self, target: pygame.Surface, option, rect: pygame.Rect,
                   now: int) -> None:
        pygame.draw.rect(target, theme.SLOT_BG, rect, border_radius=4)
        icon = self.icons.item_icon_scaled(option.role, CELL - ICON_INSET * 2)
        target.blit(icon, icon.get_rect(center=rect.center))
        if option.outputs > 1:
            label = self.icons.mark_icon(f"×{option.outputs}", theme.UI_TEXT)
            target.blit(label, label.get_rect(bottomright=(rect.right - 2,
                                                           rect.bottom - 1)))
        if option.amplified:
            plus = self.icons.mark_icon("+", theme.NEON_AMBER)
            target.blit(plus, plus.get_rect(topright=(rect.right - 3, rect.top + 1)))
        if not option.available:
            target.blit(_dim_overlay(CELL), rect.topleft)
        if self.flashing(option.recipe_id, now):
            border, width = theme.DANGER, 2
        elif option.available:
            border, width = theme.OK, 1
        else:
            border, width = theme.SLOT_BORDER, 1
        pygame.draw.rect(target, border, rect, width, border_radius=4)

    def draw_tooltip(self, target: pygame.Surface, option,
                     mouse: tuple[int, int]) -> None:
        """Название, результат и требования «свойство ×N (есть M)».

        M — только по открытым свойствам: какие именно ресурсы подойдут и
        сколько их искать, панель не подсказывает.
        """
        name = AMPLIFIED_NAME.format(name=option.name) if option.amplified else option.name
        lines: list[tuple[str, tuple[int, int, int]]] = [
            (name, theme.UI_TEXT),
            (f"Результат: {option.name} ×{option.outputs}", theme.UI_TEXT_DIM),
        ]
        for prop_name, need, have in option.requires:
            color = theme.OK if have >= need else theme.UI_TEXT_WARN
            lines.append((f"{prop_name} ×{need} (есть {have})", color))
        size = config.UI_FONT_SIZE_SMALL
        w = max(widgets.text_width(text, size) for text, _c in lines) + 18
        h = len(lines) * (size + 4) + 10
        x = min(max(mouse[0] + 14, 0), max(0, config.WINDOW_W - w))
        y = min(max(mouse[1] + 16, 0), max(0, config.WINDOW_H - h))
        widgets.draw_panel(target, (x, y, w, h), alpha=240)
        for i, (text, color) in enumerate(lines):
            widgets.draw_text(target, text, (x + 9, y + 6 + i * (size + 4)),
                              color=color, size=size)
