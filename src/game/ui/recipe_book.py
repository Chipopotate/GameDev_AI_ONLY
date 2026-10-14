"""Книга рецептов (plan/02, раздел «Интерфейс»).

Одно окно на две колонки:
- **слева рецепты ролей**: известные — с требованием и кнопкой крафта,
  неизвестные — «???» с подсказкой нужного свойства (какую роль открывает
  какая реликвия, игрок узнаёт, изучив её);
- **справа материалы забега**: значок, название, слоты свойств (открытые —
  значком свойства, закрытые — `?`) и количество в инвентаре. Колонка только
  справка: образцы игрок кладёт в лабораторию сам, через её слоты
  (ПКМ по лаборатории, см. `ui/item_window.py`, M3.5).

Окно только читает состояние симуляции; действие отдаёт ей же (`sim.craft`).
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.data.buildings import LAB
from src.game.data.recipes import ROLES, role as role_def
from src.game.data.resources import PROP_NAMES
from src.game.render import theme
from src.game.render.icons import IconCache
from src.game.sim.research import plan_craft
from src.game.sim.simulation import Simulation
from src.game.ui import widgets

WIN_W = 880
ROW_H = 30
PAD = 14
COL_GAP = 18
HEAD_H = 70      # заголовок окна плюс подписи колонок
FOOT_H = 56
LEFT_W = 430
RIGHT_W = WIN_W - PAD * 3 - COL_GAP - LEFT_W
UNKNOWN_TEXT = "???"
#: Как подписана вторая (усиленная) строка роли.
AMPLIFIED_MARK = "усил. "

#: Сколько строк нужно колонке: рецептов (по два на роль) или материалов
#: (обычные ресурсы + редкий — ровно столько их делает генератор забега).
COL_ROWS = max(len(ROLES) * 2, config.RESOURCES_PER_RUN + 1)


class RecipeBook:
    """Окно книги рецептов. Открывается клавишей R (см. app.py)."""

    def __init__(self, icons: IconCache) -> None:
        self.icons = icons
        self.visible = False
        # Прямоугольники кликов прошлого кадра: раскладка стабильна, поэтому
        # попадание считается по ним (immediate mode, как у остального UI).
        self._craft: dict[tuple[int, int, int, int], str] = {}

    @property
    def rows(self) -> int:
        """Высота колонок в строках: рецептов и материалов должно хватить обоим."""
        return COL_ROWS

    def window_rect(self) -> tuple[int, int, int, int]:
        h = HEAD_H + self.rows * ROW_H + FOOT_H + PAD
        return (config.WINDOW_W // 2 - WIN_W // 2,
                config.WINDOW_H // 2 - h // 2, WIN_W, h)

    def toggle(self) -> None:
        self.visible = not self.visible

    def close(self) -> None:
        self.visible = False

    # ------------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, sim: Simulation) -> None:
        if not self.visible:
            return
        x, y, w, h = self.window_rect()
        widgets.draw_panel(target, (x, y, w, h), alpha=238)
        widgets.draw_text(target, "Книга рецептов", (x + PAD, y + 10),
                          size=config.UI_FONT_SIZE_LARGE)
        widgets.draw_text(target, "R — закрыть", (x + w - PAD, y + 22), align="right",
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)

        left = (x + PAD, y + HEAD_H, LEFT_W, self.rows * ROW_H)
        right = (left[0] + LEFT_W + COL_GAP, y + HEAD_H, RIGHT_W, self.rows * ROW_H)
        self._draw_recipes(target, left, sim)
        self._draw_materials(target, right, sim)
        self._draw_footer(target, (x + PAD, left[1] + left[3] + 8, w - PAD * 2, FOOT_H),
                          sim)

    # --------------------------------------------------------- колонка рецептов
    def _draw_recipes(self, target: pygame.Surface, rect: tuple[int, int, int, int],
                      sim: Simulation) -> None:
        x, y, w, _h = rect
        self._craft.clear()
        widgets.draw_text(target, "Рецепты", (x, y - 22), color=theme.UI_TEXT_DIM,
                          size=config.UI_FONT_SIZE_SMALL)
        row = 0
        for role in ROLES:
            for recipe in (role.base, role.amplified):
                ry = y + row * ROW_H
                row += 1
                known = sim.knowledge.is_known(recipe.id)
                self._draw_recipe_row(target, (x, ry, w, ROW_H), sim, recipe, known)

    def _draw_recipe_row(self, target: pygame.Surface, rect: tuple[int, int, int, int],
                         sim: Simulation, recipe, known: bool) -> None:
        x, y, w, _h = rect
        prop = recipe.props[0]
        icon_x = x + 2
        icon = self.icons.prop_icon(prop) if known else self.icons.unknown_icon()
        target.blit(icon, (icon_x, y + 3))
        # Усиленный рецепт отличается словом, а не «+»: стрелки и плюсы-маркеры
        # в шрифте pygame читаются плохо, а слово видно сразу.
        mark = AMPLIFIED_MARK if recipe.amplified else ""
        name = f"{mark}{role_def(recipe.role).short if known else UNKNOWN_TEXT}"
        name_color = theme.UI_TEXT if known else theme.UI_TEXT_DIM
        widgets.draw_text(target, name, (icon_x + 28, y + 6), color=name_color,
                          size=config.UI_FONT_SIZE)
        if not known:
            # Подсказка plan/02: у неизвестной роли видно, какое свойство нужно.
            widgets.draw_text(target, f"нужно: {PROP_NAMES[prop]}", (x + 170, y + 6),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
            return
        need = ", ".join(f"×{count}" for _p, count in recipe.requires)
        widgets.draw_text(target, f"{need} = {recipe.outputs} шт", (x + 170, y + 6),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
        self._draw_craft_button(target, (x + w - 96, y + 2, 92, ROW_H - 4), sim, recipe)

    def _draw_craft_button(self, target: pygame.Surface, rect: tuple[int, int, int, int],
                           sim: Simulation, recipe) -> None:
        """Кнопка крафта с отметкой «можно сейчас» (plan/02)."""
        plan = plan_craft(recipe, sim.inventory, sim.run.items)
        ready = plan is not None
        x, y, w, h = rect
        if ready:
            widgets.draw_panel(target, rect, alpha=200)
            widgets.draw_text(target, "крафт", (x + w // 2, y + 5), align="center",
                              color=theme.OK, size=config.UI_FONT_SIZE_SMALL)
            self._craft[rect] = recipe.id
        else:
            # Не хватает материалов: кнопка гаснет, но строка остаётся —
            # игрок должен видеть, к чему стремится.
            widgets.draw_text(target, "мало", (x + w // 2, y + 5), align="center",
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)

    # -------------------------------------------------------- колонка материалов
    def _draw_materials(self, target: pygame.Surface, rect: tuple[int, int, int, int],
                        sim: Simulation) -> None:
        x, y, w, _h = rect
        widgets.draw_text(target, "Материалы", (x, y - 22), color=theme.UI_TEXT_DIM,
                          size=config.UI_FONT_SIZE_SMALL)
        for row, item in enumerate(sim.run.items.natural):
            ry = y + row * ROW_H
            self._draw_material_row(target, (x, ry, w, ROW_H), sim, item)

    def _draw_material_row(self, target: pygame.Surface, rect: tuple[int, int, int, int],
                           sim: Simulation, item) -> None:
        x, y, w, _h = rect
        target.blit(self.icons.item_icon(item.id), (x + 2, y + 3))
        widgets.draw_text(target, item.name, (x + 30, y + 6), size=config.UI_FONT_SIZE)
        # Слоты свойств: сколько их — видно всегда, что именно — только открытое.
        sx = x + 158
        for slot in sim.prop_slots(item.id):
            icon = self.icons.unknown_icon() if slot is None else self.icons.prop_icon(slot)
            target.blit(icon, (sx, y + 3))
            sx += 26
        count = sim.inventory.count(item.id)
        widgets.draw_text(target, f"×{count}", (x + w - 12, y + 6), align="right",
                          color=theme.OK if count else theme.UI_TEXT_DIM,
                          size=config.UI_FONT_SIZE)

    # --------------------------------------------------------------- подвал
    def _draw_footer(self, target: pygame.Surface, rect: tuple[int, int, int, int],
                     sim: Simulation) -> None:
        x, y, w, _h = rect
        widgets.draw_text(target, "ЛКМ по рецепту — крафт · образцы кладут в "
                                  "лабораторию: ПКМ по ней", (x, y),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
        known, total = sim.props_known
        studied = sum(1 for relic in sim.relics if relic.studied)
        labs = sim.buildings.of_kind(LAB)
        analyzing = sum(1 for lab in labs if lab.busy)
        widgets.draw_text(
            target,
            f"свойств открыто {known}/{total} · рецептов "
            f"{sim.knowledge.known_count}/{len(ROLES) * 2} · реликвий "
            f"{studied}/{len(sim.relics)} · лабораторий {len(labs)} "
            f"(в работе {analyzing})",
            (x, y + 20), color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)

    # ---------------------------------------------------------------- клики
    def click(self, pos: tuple[int, int], sim: Simulation) -> bool:
        """Клик по окну. True — если окно его поглотило (мимо мира)."""
        if not self.visible:
            return False
        hit = self._hit(self._craft, pos)
        if hit is not None:
            sim.craft(hit)
            return True
        return True  # окно открыто: клики по нему в мир не проходят

    @staticmethod
    def _hit(rects: dict[tuple[int, int, int, int], str],
             pos: tuple[int, int]) -> str | None:
        px, py = pos
        for (x, y, w, h), value in rects.items():
            if x <= px < x + w and y <= py < y + h:
                return value
        return None
