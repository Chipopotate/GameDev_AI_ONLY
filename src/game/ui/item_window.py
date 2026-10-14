"""Окно предметов: инвентарь с крафтом и контейнеры зданий (M3.5 → plan/02, plan/03).

Одно окно на два случая:
- **клавиша E** — слева сетка дрона (🔧 `DRONE_SLOTS` слотов) и под ней ряд
  хотбара, справа панель крафта (`ui/craft_panel.py`) — как в Minecraft и
  Factorio, без отдельной книги рецептов;
- **ПКМ по зданию** — слева его контейнеры из данных (`data/buildings.py`),
  справа сетка дрона с хотбаром. Панели крафта здесь нет.

Управление как в Minecraft (M3.5), и оно одно для всех сеток окна, хотбара
тоже:
- ЛКМ — взять стак «в руку» или положить его (такой же предмет досыпается до
  лимита, другой — меняется местами);
- ПКМ — взять половину стака; со стаком в руке — положить одну единицу;
- Shift+ЛКМ — перекинуть стак в соседнюю сетку;
- двойной ЛКМ — собрать в руку весь этот предмет из всех сеток окна;
- Q — выбросить стак из-под курсора на землю;
- «Сортировать» — разложить сетку дрона по типу и количеству.

Предмет «в руке» живёт, пока окно открыто. Закрывая окно, оно возвращает его
в контейнеры, а что не влезло — бросает на землю: иначе предмет пропадал бы
вместе с окном (plan/03). Окно только читает состояние симуляции; действия
отдаёт ей (`sim.throw_from`, `sim.return_stack`, `sim.craft`, `transfer`), сама
логика слотов живёт в `sim/resources.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pygame

from src.game import config
from src.game.data.buildings import WORK_CRAFT, building as building_def
from src.game.data.recipes import role as role_def
from src.game.render import theme
from src.game.render.icons import IconCache
from src.game.sim.buildings import (CRAFT_ITEMS, CRAFT_NONE, CRAFT_WORK,
                                    SLOT_ANALYZING, SLOT_EMPTY, SLOT_POWER,
                                    SLOT_STUDIED)
from src.game.sim.resources import Container, Stack, transfer
from src.game.sim.simulation import Simulation
from src.game.ui import widgets
from src.game.ui.craft_panel import CraftPanel
from src.game.ui.slot_grid import SlotGrid
from src.game.ui.state import UiState

MODE_DRONE = "drone"          # клавиша E: сетка дрона, хотбар и крафт
MODE_CONTAINER = "container"  # ПКМ по зданию: его контейнеры и сетка дрона

#: Сетка дрона: 10 в ряд (20 слотов = 2 строки), под ней ряд хотбара из 9 —
#: та же раскладка, что у инвентаря Minecraft.
DRONE_COLS = 10

PAD = 12
PANEL_GAP = 18             # между колонками окна
STACK_GAP = 8              # между сетками внутри колонки
PANEL_PAD = 10
PANEL_MIN_W = 176        # чтобы влезли строки состояния слотов вместе с полосой
HEADER_H = 22            # строка с именем окна и подсказкой клавиш
TITLE_H = 16             # заголовок сетки внутри окна
HINT_H = 34
STATUS_ROW = 18          # строка состояния слота под сеткой лаборатории
STATUS_BAR_W = 40        # полоса анализа справа в строке состояния
BUTTON_W = 108
BUTTON_H = 22
BUTTON_GAP = 6
HINT_DRONE = "ЛКМ — взять/положить · ПКМ — половину · Shift+ЛКМ — в хотбар и обратно · Q — выбросить"
HINT_CONTAINER = "Shift+ЛКМ — переложить · ПКМ — половину/одну · Q — выбросить"
SORT_LABEL = "Сортировать"
RECIPE_LABEL = "Рецепт: {name}"
NO_RECIPE_LABEL = "Выбрать рецепт"
INVENTORY_TITLE = "Инвентарь дрона"
HOTBAR_TITLE = "Хотбар (1–9)"
CRAFT_TITLE = "Крафт"

#: Что написать в строке состояния (значения — из `sim/buildings.py`).
STATUS_TEXT = {
    SLOT_EMPTY: "пусто",
    SLOT_ANALYZING: "анализ",
    SLOT_STUDIED: "изучено",
    SLOT_POWER: "нет энергии",
    CRAFT_NONE: "нет рецепта",
    CRAFT_ITEMS: "нет материалов",
    CRAFT_WORK: "сборка",
}

#: Состояния, при которых строка показывает полосу прогресса.
ACTIVE_STATUSES = (SLOT_ANALYZING, CRAFT_WORK)
#: Двойной клик: насколько быстро нужно кликнуть второй раз, мс.
DOUBLE_CLICK_MS = 320

#: Сетки кэшируются по числу колонок: объект маленький, но кадр не должен
#: создавать ничего нового (plan/07 §4).
_grids: dict[int, SlotGrid] = {}


def grid_for(cols: int) -> SlotGrid:
    """Сетка на `cols` слотов — одна на всё окно (сетки стабильны по размеру)."""
    grid = _grids.get(cols)
    if grid is None:
        grid = SlotGrid(cols)
        _grids[cols] = grid
    return grid


@dataclass
class Panel:
    """Одна сетка окна: чей контейнер, где стоит и что в нём подсветить."""

    title: str
    container: Container
    grid: SlotGrid
    origin: tuple[int, int] = (0, 0)
    #: Зона панели: по ней рисуются строки состояния и кнопка (заполняет раскладка).
    content: tuple[int, int] = (0, 0)
    active: int | None = None
    #: Строки состояния контейнера, над которым здание работает само:
    #: у лаборатории — по строке на слот, у сборщика — одна (статус, прогресс).
    statuses: tuple[tuple[str, float], ...] = ()
    #: Нумеровать ли строки состояния: у слотов лаборатории номер нужен, у
    #: единственной строки сборщика он был бы лишним.
    slot_rows: bool = False
    #: Кнопка сетки: у дрона «Сортировать», у сборщика — выбор рецепта.
    sortable: bool = False
    action: str | None = None
    button: pygame.Rect | None = field(default=None)
    #: Сетка дрона (инвентарь или хотбар), а не здания: по этому выбирается,
    #: куда Shift+ЛКМ перекидывает стак.
    drone: bool = False


class ItemWindow:
    """Окно с сетками слотов. Клавиша E (с крафтом) или ПКМ по зданию."""

    def __init__(self, icons: IconCache, ui: UiState | None = None) -> None:
        self.icons = icons
        self.ui = ui if ui is not None else UiState()
        self.craft = CraftPanel(icons, self.ui)
        self.mode: str | None = None
        self.building = None
        #: Стак «в руке»: предмет, который игрок несёт между слотами.
        self.held: Stack | None = None
        #: Последний клик по слоту: (время, номер сетки, номер слота) — для
        #: двойного клика. Своё время, а не событие pygame: окно работает и
        #: в тестах, где событий нет.
        self._last_click: tuple[int, int, int] | None = None
        self._rect = pygame.Rect(0, 0, 0, 0)

    # ------------------------------------------------------------- открытие
    @property
    def visible(self) -> bool:
        return self.mode is not None

    @property
    def title(self) -> str:
        if self.mode == MODE_CONTAINER and self.building is not None:
            return building_def(self.building.kind).name
        return "Инвентарь и крафт"

    @property
    def has_craft(self) -> bool:
        """Панель крафта — только в окне инвентаря (E), не в окне здания."""
        return self.mode == MODE_DRONE

    def open_drone(self, sim: Simulation) -> None:
        self.close(sim)
        self.mode = MODE_DRONE

    def open_container(self, sim: Simulation, building) -> None:
        self.close(sim)
        self.mode = MODE_CONTAINER
        self.building = building

    def toggle_drone(self, sim: Simulation) -> None:
        if self.mode == MODE_DRONE:
            self.close(sim)
        else:
            self.open_drone(sim)

    def close(self, sim: Simulation) -> None:
        """Закрывает окно, возвращая предмет из руки в контейнеры."""
        self._release_held(sim)
        self.craft.search.blur()
        self.mode = None
        self.building = None
        self._last_click = None

    def _release_held(self, sim: Simulation) -> None:
        """Возвращает стак из руки: в здание, потом дрону, иначе на землю."""
        if self.held is None:
            return
        stack, self.held = self.held, None
        sim.return_stack(stack, *self._containers(sim))

    def _containers(self, sim: Simulation) -> list:
        """Куда окно возвращает предмет: здание, потом дрон по правилу `Cargo`.

        Здание — только если оно ещё стоит на карте: снесённое (разбор, на M5 —
        враги) унесло бы предмет «в руке» вместе с собой, а закон сохранения
        (plan/03) этого не допускает.
        """
        out: list = []
        if self.building is not None and self.building in sim.buildings.buildings:
            out.extend(c for c in self.building.containers.values() if c is not None)
        out.append(sim.cargo)
        return out

    # -------------------------------------------------------------- раскладка
    def panels(self, sim: Simulation) -> list[Panel]:
        """Сетки окна: контейнеры здания (если это окно здания), затем дрон.

        У дрона две сетки: инвентарь и хотбар под ним. Перетаскивание между
        ними — та же общая логика, что и между любыми другими сетками.
        """
        drone = [
            Panel(INVENTORY_TITLE, sim.inventory, grid_for(DRONE_COLS), sortable=True,
                  drone=True),
            Panel(HOTBAR_TITLE, sim.hotbar, grid_for(sim.hotbar.size),
                  active=sim.selected_slot, drone=True),
        ]
        if self.mode != MODE_CONTAINER or self.building is None:
            return drone
        return self._building_panels(sim) + drone

    def _building_panels(self, sim: Simulation) -> list[Panel]:
        """По панели на каждый контейнер здания, состояние — у рабочего.

        Рабочий контейнер берётся из данных (`work_container_id`): у
        лаборатории это образцы, у сборщика — вход, по которому видно, чего
        ему не хватает. Там же живёт кнопка выбора рецепта.
        """
        definition = building_def(self.building.kind)
        panels: list[Panel] = []
        for container_def in definition.containers:
            container = self.building.container(container_def.id)
            if container is None:
                continue
            working = container_def.id == definition.work_container_id
            panels.append(Panel(
                container_def.name, container, grid_for(container_def.cols),
                statuses=sim.buildings.work_statuses(self.building, sim.knowledge,
                                                     sim.run.items)
                if working else (),
                slot_rows=working and definition.analyzes is not None,
                action=self._action_label(sim) if working
                and definition.work == WORK_CRAFT else None,
            ))
        return panels

    def _action_label(self, sim: Simulation) -> str:
        """Надпись на кнопке сборщика: какой рецепт он собирает сейчас."""
        recipe_id = self.building.recipe
        if recipe_id is None:
            return NO_RECIPE_LABEL
        recipe = sim.run.recipe(recipe_id)
        name = role_def(recipe.role).short + ("+" if recipe.amplified else "")
        return RECIPE_LABEL.format(name=name)

    def _has_button(self, panel: Panel) -> bool:
        return panel.sortable or panel.action is not None

    def _button_label(self, panel: Panel) -> str:
        return SORT_LABEL if panel.sortable else (panel.action or "")

    def _panel_w(self, panel: Panel) -> int:
        return max(PANEL_MIN_W, panel.grid.size(panel.container.size)[0] + PANEL_PAD * 2)

    def _panel_h(self, panel: Panel) -> int:
        """Высота панели. Место под строки и кнопку резервируется всегда:
        иначе сетка прыгала бы в момент начала анализа (plan/07 §10)."""
        _gw, gh = panel.grid.size(panel.container.size)
        h = TITLE_H + gh + PANEL_PAD
        if panel.statuses:
            h += STATUS_ROW * len(panel.statuses)
        if self._has_button(panel):
            h += BUTTON_H + BUTTON_GAP
        return h

    def _columns(self, panels: list[Panel]) -> list[list[Panel]]:
        """Колонки окна: здание слева, дрон (инвентарь над хотбаром) справа."""
        building = [p for p in panels if not p.drone]
        drone = [p for p in panels if p.drone]
        return [building, drone] if building else [drone]

    def _layout(self, sim: Simulation) -> tuple[tuple[int, int, int, int], list[Panel]]:
        """Считает окно и сетки. Раскладка детерминирована — её же читают клики."""
        panels = self.panels(sim)
        columns = self._columns(panels)
        widths = [max(self._panel_w(p) for p in col) for col in columns]
        heights = [sum(self._panel_h(p) for p in col) + STACK_GAP * (len(col) - 1)
                   for col in columns]
        if self.has_craft:
            craft_w, craft_h = self.craft.size(sim)
            widths.append(craft_w)
            heights.append(TITLE_H + craft_h)
        inner_h = max(heights)
        w = PAD * 2 + sum(widths) + PANEL_GAP * (len(widths) - 1)
        h = PAD * 2 + HEADER_H + inner_h + HINT_H
        x = config.WINDOW_W // 2 - w // 2
        y = config.WINDOW_H // 2 - h // 2
        top = y + PAD + HEADER_H
        cx = x + PAD
        for col, pw in zip(columns, widths):
            cy = top
            for panel in col:
                self._place_panel(panel, cx, cy, pw)
                cy += self._panel_h(panel) + STACK_GAP
            cx += pw + PANEL_GAP
        if self.has_craft:
            self.craft.place((cx, top + TITLE_H), sim)
        self._rect = pygame.Rect(x, y, w, h)
        return (x, y, w, h), panels

    def _place_panel(self, panel: Panel, cx: int, top: int, pw: int) -> None:
        gw, gh = panel.grid.size(panel.container.size)
        panel.content = (cx + PANEL_PAD, pw - PANEL_PAD * 2)
        panel.origin = (cx + (pw - gw) // 2, top + TITLE_H)
        below = top + TITLE_H + gh + 6
        if panel.statuses:
            below += STATUS_ROW * len(panel.statuses)
        panel.button = None
        if self._has_button(panel):
            # Ширина кнопки — по надписи: «Рецепт: Энергия» длиннее слова
            # «Сортировать», а обрезанная надпись в окне бесполезна.
            bw = max(BUTTON_W, widgets.text_width(self._button_label(panel),
                                                  config.UI_FONT_SIZE_SMALL) + 16)
            bw = min(bw, pw - PANEL_PAD * 2)
            panel.button = pygame.Rect(cx + (pw - bw) // 2, below, bw, BUTTON_H)

    def contains(self, pos: tuple[int, int], sim: Simulation) -> bool:
        """Попадает ли точка в окно (клики вне окна идут дальше — в хотбар)."""
        if not self.visible:
            return False
        x, y, w, h = self._layout(sim)[0]
        return pygame.Rect(x, y, w, h).collidepoint(pos)

    def _slot_at(self, pos: tuple[int, int],
                 sim: Simulation) -> tuple[int, int] | None:
        """(номер сетки, номер слота) под точкой или None."""
        for i, panel in enumerate(self._layout(sim)[1]):
            index = panel.grid.hit(panel.origin, panel.container.size, pos)
            if index is not None:
                return i, index
        return None

    # ------------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, sim: Simulation) -> None:
        if not self.visible:
            return
        rect, panels = self._layout(sim)
        x, y, w, h = rect
        widgets.draw_panel(target, rect, alpha=238)
        widgets.draw_text(target, self.title, (x + PAD, y + 8),
                          size=config.UI_FONT_SIZE)
        widgets.draw_text(target, self._hint(), (x + w - PAD, y + 10), align="right",
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)

        mouse = pygame.mouse.get_pos()
        hover = self._slot_at(mouse, sim)
        for i, panel in enumerate(panels):
            widgets.draw_text(target, panel.title,
                              (panel.origin[0], panel.origin[1] - TITLE_H),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
            panel.grid.draw(target, panel.origin, panel.container, self.icons,
                            active=panel.active,
                            hover=hover[1] if hover is not None and hover[0] == i
                            else None)
            self._draw_statuses(target, panel)
            self._draw_button(target, panel, mouse)
        if self.has_craft:
            ox, oy = self.craft.origin
            widgets.draw_text(target, CRAFT_TITLE, (ox, oy - TITLE_H),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
            self.craft.draw(target, sim, mouse if self.held is None else (-1, -1))

        widgets.draw_text(target, self._footer_hint(), (x + PAD, y + h - HINT_H + 4),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
        self._draw_tooltip(target, sim, panels, hover, mouse)
        self._draw_held(target, mouse)

    def _hint(self) -> str:
        if self.mode == MODE_DRONE:
            return "E или Esc — закрыть"
        return "Esc — закрыть"

    def _footer_hint(self) -> str:
        return HINT_DRONE if self.mode == MODE_DRONE else HINT_CONTAINER

    def _draw_statuses(self, target: pygame.Surface, panel: Panel) -> None:
        """Что происходит в контейнере, над которым здание работает само.

        У лаборатории у каждого слота свой прогресс — и своя строка; у сборщика
        строка одна: чего ему не хватает, а когда всё есть — полоса сборки.
        """
        if not panel.statuses:
            return
        cx, cw = panel.content
        cy = panel.origin[1] + panel.grid.size(panel.container.size)[1] + 6
        for i, (status, progress) in enumerate(panel.statuses):
            row_y = cy + i * STATUS_ROW
            text = STATUS_TEXT.get(status, status)
            label = f"{i + 1}. {text}" if panel.slot_rows else text
            active = status in ACTIVE_STATUSES
            if active:
                color = theme.UI_TEXT
            elif status == SLOT_STUDIED:
                color = theme.OK
            elif status == SLOT_POWER:
                color = theme.UI_TEXT_WARN
            else:
                color = theme.UI_TEXT_DIM
            if active:
                label = f"{label} {int(progress * 100)}%"
            widgets.draw_text(target, label, (cx, row_y), color=color,
                              size=config.UI_FONT_SIZE_SMALL)
            if progress > 0.0:
                widgets.draw_bar(target, (cx + cw - STATUS_BAR_W, row_y + 5,
                                          STATUS_BAR_W, 6),
                                 progress, theme.SCAN_BAR_FG)

    def _draw_button(self, target: pygame.Surface, panel: Panel,
                     mouse: tuple[int, int]) -> None:
        """Кнопка панели: «Сортировать» у дрона, выбор рецепта у сборщика."""
        if panel.button is None:
            return
        hovered = panel.button.collidepoint(mouse)
        widgets.draw_panel(target, panel.button, alpha=236 if hovered else 200)
        widgets.draw_text(target, self._button_label(panel),
                          (panel.button.centerx, panel.button.top + 4),
                          align="center",
                          color=theme.UI_TEXT if hovered else theme.UI_TEXT_DIM,
                          size=config.UI_FONT_SIZE_SMALL)

    def _draw_tooltip(self, target: pygame.Surface, sim: Simulation, panels: list[Panel],
                      hover: tuple[int, int] | None,
                      mouse: tuple[int, int]) -> None:
        """Подсказка под курсором: имя, количество и свойства предмета.

        Значки разных ресурсов бывают похожи, а свойства игрок иначе не сверит:
        открытые — цветным значком, закрытые — «?» (plan/02).
        """
        if hover is None or self.held is not None:
            return
        stack = panels[hover[0]].container.slot(hover[1])
        if stack is None:
            return
        name = sim.run.items[stack.item_id].name
        props = sim.prop_slots(stack.item_id)
        icon = self.icons.item_icon(stack.item_id)
        text = f"{name} ×{stack.count}"
        w = max(widgets.text_width(text, config.UI_FONT_SIZE) + 44,
                len(props) * 26 + 4) + 12
        h = config.UI_FONT_SIZE + 10 + (26 if props else 0)
        x = min(max(mouse[0] + 14, 0), max(0, config.WINDOW_W - w))
        y = min(max(mouse[1] + 16, 0), max(0, config.WINDOW_H - h))
        widgets.draw_panel(target, (x, y, w, h), alpha=236)
        target.blit(icon, (x + 6, y + 6))
        widgets.draw_text(target, text, (x + 34, y + 5))
        sx = x + 8
        for prop in props:
            mark = self.icons.unknown_icon() if prop is None else self.icons.prop_icon(prop)
            target.blit(mark, (sx, y + config.UI_FONT_SIZE + 8))
            sx += 26

    def _draw_held(self, target: pygame.Surface, mouse: tuple[int, int]) -> None:
        """Стак в руке едет за курсором — как в Minecraft."""
        if self.held is None:
            return
        size = 24
        icon = self.icons.item_icon_scaled(self.held.item_id, size)
        target.blit(icon, icon.get_rect(center=(mouse[0] + 8, mouse[1] + 8)))
        if self.held.count > 1:
            label = self.icons.mark_icon(f"×{self.held.count}", theme.UI_TEXT)
            target.blit(label, label.get_rect(bottomright=(mouse[0] + 20,
                                                           mouse[1] + 22)))

    # ------------------------------------------------------------------ ввод
    def click(self, pos: tuple[int, int], button: int, sim: Simulation,
              shift: bool = False, when: int | None = None,
              ctrl: bool = False) -> bool:
        """Клик по окну. True — если окно его поглотило (мимо мира).

        `when` — время клика в миллисекундах; по умолчанию берётся у pygame,
        но его можно задать явно (тесты двойного клика идут без событий).
        """
        if not self.visible:
            return False
        panels = self._layout(sim)[1]
        if self.has_craft and button == 1:
            # Поле поиска само решает, взять ли фокус: клик мимо него снимает
            # фокус (plan/10), клик по рецепту — крафт.
            if self.craft.click(pos, sim, shift=shift, ctrl=ctrl, when=when):
                return True
        elif self.has_craft:
            self.craft.search.blur()
        if button == 1 and self._click_button(pos, panels, sim):
            return True
        hit = self._slot_at(pos, sim)
        if hit is None:
            return True                      # мимо слотов: в мир клик не уходит
        now = pygame.time.get_ticks() if when is None else int(when)
        panel_i, slot = hit
        container = panels[panel_i].container
        if button == 3:
            if self.held is None:
                self._take_half(container, slot)
            else:
                self._put_one(container, slot)
            self._last_click = None
            return True
        if shift:
            self._quick_move(panels, panel_i, slot, sim)
            self._last_click = None
            return True
        if self._is_double(hit, now):
            self._collect_all(panels, sim)
            self._last_click = None
            return True
        self._last_click = (now, hit[0], hit[1])
        self._take_or_put(container, slot)
        return True

    def _click_button(self, pos: tuple[int, int], panels: list[Panel],
                      sim: Simulation) -> bool:
        """Нажата ли кнопка панели и что она делает.

        «Сортировать» раскладывает сетку, кнопка сборщика перебирает открытые
        рецепты — решение о том, что выйдет, принимает игрок, а не здание.
        """
        for panel in panels:
            if panel.button is None or not panel.button.collidepoint(pos):
                continue
            if panel.sortable:
                panel.container.sort()
            elif panel.action is not None:
                sim.cycle_recipe(self.building)
            return True
        return False

    def _is_double(self, hit: tuple[int, int], now: int) -> bool:
        """Второй клик по тому же слоту недавно — значит, собираем всё."""
        if self._last_click is None:
            return False
        last_when, panel_i, slot = self._last_click
        if (panel_i, slot) != hit:
            return False
        return 0 <= now - last_when <= DOUBLE_CLICK_MS

    def _take_or_put(self, container: Container, slot: int) -> None:
        """ЛКМ: пустая рука берёт стак, полная — кладёт (или меняет местами)."""
        if self.held is None:
            self.held = container.take_slot(slot)
            return
        self.held = container.put_slot(slot, self.held)

    def _take_half(self, container: Container, slot: int) -> None:
        """ПКМ пустой рукой: половина стака (с округлением вниз) — в руку.

        Нечётный стак делится как в Minecraft: большая половина остаётся в
        слоте, меньшая уходит в руку, и ни одна единица не теряется.
        """
        stack = container.slot(slot)
        if stack is None:
            return
        self.held = container.take_slot(slot, stack.count // 2)

    def _put_one(self, container: Container, slot: int) -> None:
        """ПКМ со стаком в руке: одна единица в слот (досыпать в такой же)."""
        if self.held is None:
            return
        target = container.slot(slot)
        if target is not None and (target.item_id != self.held.item_id
                                   or target.count >= container.max_for(target.item_id)):
            return
        container.put_slot(slot, Stack(self.held.item_id, 1))
        self.held.count -= 1
        if self.held.count <= 0:
            self.held = None

    def _collect_all(self, panels: list[Panel], sim: Simulation) -> None:
        """Двойной ЛКМ: этот предмет из всех сеток окна — в руку, до полного стака.

        Собираем именно тот предмет, что уже в руке: первый клик пары его и
        взял. Так двойной клик не «добирает» чужое и не зависит от того, в
        какой сетке лежит остальное. Рука, как и слот, вмещает не больше
        лимита стека (как в Minecraft): иначе стак больше лимита нельзя было
        бы ни положить, ни обменять без потерь.
        """
        if self.held is None:
            return
        item_id = self.held.item_id
        limit = sim.inventory.max_for(item_id)
        taken = self.held.count
        for panel in panels:
            container = panel.container
            for index in range(container.size):
                if taken >= limit:
                    break
                stack = container.slot(index)
                if stack is None or stack.item_id != item_id:
                    continue
                moved = container.take_slot(index, limit - taken)
                if moved is not None:
                    taken += moved.count
        self.held = Stack(item_id, taken)

    def _quick_move(self, panels: list[Panel], panel_i: int, slot: int,
                    sim: Simulation) -> None:
        """Shift+ЛКМ: стак уезжает в соседнюю сетку.

        - из сетки здания — дрону, по правилу `Cargo` (постройка в хотбар,
          ресурс в инвентарь);
        - из сетки дрона в окне здания — в первую сетку здания;
        - в окне инвентаря — между инвентарём и хотбаром.
        """
        source = panels[panel_i]
        building = [p for p in panels if not p.drone]
        if not source.drone:
            moved = transfer(source.container, sim.cargo, slot)
        elif building:
            moved = transfer(source.container, building[0].container, slot)
        else:
            other = sim.hotbar if source.container is sim.inventory else sim.inventory
            moved = transfer(source.container, other, slot)
        if moved == 0:
            sim.toast("Нет места")

    def drop_under_cursor(self, pos: tuple[int, int], sim: Simulation) -> bool:
        """Q: выбрасывает стак из-под курсора на землю. False — если слот пуст."""
        hit = self._slot_at(pos, sim)
        if hit is None:
            return False
        container = self._layout(sim)[1][hit[0]].container
        return sim.throw_from(container, hit[1])
