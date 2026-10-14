"""HUD: инвентарь, подсказки, сообщения, пауза."""

from __future__ import annotations

import pygame

from src.game import config
from src.game.data.buildings import building as building_def
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.render.icons import IconCache
from src.game.sim.simulation import ENERGY_ITEM, Simulation
from src.game.ui import widgets
from src.game.ui.controls import hint_lines
from src.game.ui.hotbar import HotbarBar

#: Подсказка управления — из единой таблицы (`ui/controls.py` = plan/10).
CONTROLS = hint_lines()

ROW_H = 26
#: Таблица клавиш: ширина колонки текста, шаг строк, отступ продолжения строки
#: и зазор до хотбара (пиксели).
CONTROLS_W = 300
CONTROLS_ROW = 16
CONTROLS_INDENT = 14
CONTROLS_GAP = 8
#: Строка подсказки висит над хотбаром на столько пикселей (место под его подпись).
PROMPT_ABOVE_HOTBAR = 62
TOAST_TOP = 96             # ниже панели времени суток
TIME_PANEL_W = 210

_pause_overlays: dict[tuple[int, int], pygame.Surface] = {}


def _wrap(text: str, width: int, size: int) -> list[str]:
    """Переносит строку по словам так, чтобы каждая часть влезала в ширину."""
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        probe = f"{current} {word}" if current else word
        if current and widgets.text_width(probe, size) > width:
            lines.append(current)
            current = word
        else:
            current = probe
    if current:
        lines.append(current)
    return lines


def _fit(text: str, width: int, size: int) -> str:
    """Обрезает строку с многоточием, чтобы она влезла в ширину."""
    if widgets.text_width(text, size) <= width:
        return text
    while text and widgets.text_width(text + "…", size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _pause_overlay(size: tuple[int, int]) -> pygame.Surface:
    """Затемнение под паузу. Кэшируется — не создаём Surface каждый кадр."""
    surf = _pause_overlays.get(size)
    if surf is None:
        surf = pygame.Surface(size, pygame.SRCALPHA)
        surf.fill((0, 0, 0, 110))
        _pause_overlays[size] = surf
    return surf


class Hud:
    """Рисует интерфейс поверх мира. Состояние только читает."""

    def __init__(self, icons: IconCache) -> None:
        self.icons = icons
        #: Панель быстрого доступа видна всегда — и поверх открытых окон.
        self.hotbar = HotbarBar(icons)
        self._controls_cache: list[tuple[str, int]] | None = None
        #: Таблица клавиш статична — рисуется один раз в свою поверхность.
        self._controls_surface: pygame.Surface | None = None
        #: Панели, нарисованные в последнем кадре: над ними мир не получает ни
        #: кликов, ни призрака (plan/10: «курсор над картой, не над UI»).
        self._panels: list[pygame.Rect] = []

    def draw(self, target: pygame.Surface, sim: Simulation, camera: Camera,
             fps: float, window_open: bool = False) -> None:
        """Панели HUD. Пауза рисуется отдельно (`draw_pause`) — поверх окон."""
        self._panels = []
        self._draw_status(target, sim, camera, fps)
        self._draw_time(target, sim)
        self._draw_inventory(target, sim)
        if not window_open:
            # В окне свои подсказки, и таблица клавиш легла бы поверх него.
            self._draw_controls(target)
        self._draw_toasts(target, sim)
        if not window_open:
            self._draw_prompt(target, sim)

    def draw_defeat(self, target: pygame.Surface, sim: Simulation) -> None:
        """Экран поражения (M5): причина и точка прорыва — кольцо на карте."""
        target.blit(_pause_overlay(target.get_size()), (0, 0))
        cx = config.WINDOW_W // 2
        widgets.draw_text(target, "ПОРАЖЕНИЕ", (cx, 90), color=theme.DANGER,
                          size=config.UI_FONT_SIZE_LARGE, align="center")
        lines = [sim.defeat_reason or "Дрон уничтожен",
                 f"День {sim.time_cycle.day_number}, потеряно построек: {sim.losses}, "
                 f"убито врагов: {sim.enemies.killed}"]
        if sim.breach_point is not None:
            bx, by = sim.breach_point
            lines.append(f"Точка прорыва: тайл {bx}, {by} (красное кольцо)")
        lines.append("Esc — выход")
        for i, line in enumerate(lines):
            widgets.draw_text(target, line, (cx, 128 + i * 22), color=theme.UI_TEXT,
                              size=config.UI_FONT_SIZE, align="center")

    def draw_pause(self, target: pygame.Surface) -> None:
        """Плашка паузы — самым верхним слоем: иначе её закрывало окно предметов."""
        self._draw_pause(target)

    def draw_hotbar(self, target: pygame.Surface, sim: Simulation,
                    tooltip: bool = True) -> None:
        """Хотбар — последним слоем: он виден и поверх окна инвентаря."""
        self.hotbar.draw(target, sim, pygame.mouse.get_pos(), tooltip=tooltip)

    def over_ui(self, pos: tuple[int, int]) -> bool:
        """Над интерфейсом ли курсор: хотбар или любая панель HUD прошлого кадра.

        Над панелью мир не виден, поэтому и призрак, и клик «под панель» были бы
        действием вслепую (plan/10).
        """
        if self.hotbar.contains(pos):
            return True
        return any(rect.collidepoint(pos) for rect in self._panels)

    def _panel(self, target: pygame.Surface, rect) -> None:
        """Рисует подложку панели и запоминает её место для `over_ui`."""
        widgets.draw_panel(target, rect)
        self._panels.append(pygame.Rect(rect))

    # --------------------------------------------------------- левый верх
    def _draw_status(self, target: pygame.Surface, sim: Simulation,
                     camera: Camera, fps: float) -> None:
        m = config.UI_MARGIN
        known, total = sim.props_known
        supply, demand = sim.power_summary
        lines = (
            f"Сид: {sim.seed}",
            f"Тайл: {sim.drone.tile()[0]}, {sim.drone.tile()[1]}",
            f"Зум: {camera.zoom:.1f}x",
            f"Дрон: {max(0, int(sim.drone.hp))}/{config.DRONE_HP}",
            f"Врагов: {sim.enemies.count()}   убито: {sim.enemies.killed}",
            f"Добыто: {sim.mined_total}",
            f"Слот {sim.selected_slot + 1}: {self._slot_name(sim)}",
            # Энергия сети (M4): выработка/спрос. Если выработки меньше, часть
            # зданий стоит — это видно по числам без отдельной лампочки.
            f"Энергия: {supply:.0f}/{demand:.0f}",
            f"Свойств: {known}/{total}",
            # Только сколько открыто: «из скольких» выдало бы число неоткрытого.
            f"Рецептов: {sim.knowledge.known_count}",
        )
        w = 190
        h = len(lines) * 18 + 12
        self._panel(target, (m, m, w, h))
        for i, line in enumerate(lines):
            widgets.draw_text(target, line, (m + 10, m + 6 + i * 18),
                              size=config.UI_FONT_SIZE_SMALL)

    @staticmethod
    def _slot_name(sim: Simulation) -> str:
        stack = sim.selected_stack()
        if stack is None:
            return "пусто"
        return sim.run.items[stack.item_id].name.lower()

    # -------------------------------------------------------- верх по центру
    def _draw_time(self, target: pygame.Surface, sim: Simulation) -> None:
        """Часы забега и счётчик освещённости (plan/07 §10)."""
        cycle = sim.time_cycle
        night = cycle.is_night
        head = f"День {cycle.day_number} · {'НОЧЬ' if night else 'день'}"
        left = f"до {'рассвета' if night else 'заката'} {cycle.t_left:4.1f} с"
        lines = (
            (head, theme.ACCENT if night else theme.UI_TEXT),
            (left, theme.UI_TEXT_DIM),
            (f"освещено {sim.tiles_lit_ratio * 100:.0f}%", theme.LIGHT_ACCENT),
        )
        w = TIME_PANEL_W
        h = len(lines) * 18 + 12
        x = config.WINDOW_W // 2 - w // 2
        y = config.UI_MARGIN
        self._panel(target, (x, y, w, h))
        for i, (text, color) in enumerate(lines):
            widgets.draw_text(target, text, (config.WINDOW_W // 2, y + 6 + i * 18),
                              color=color, size=config.UI_FONT_SIZE_SMALL, align="center")

    # -------------------------------------------------------- левый низ
    def _draw_inventory(self, target: pygame.Surface, sim: Simulation) -> None:
        """Сводка по инвентарю: что и сколько. Полная сетка слотов — по клавише E."""
        items = sim.inventory.items()
        inv = sim.inventory
        m = config.UI_MARGIN
        w = 232
        h = 30 + max(1, len(items)) * ROW_H + 8
        y = config.WINDOW_H - m - h
        self._panel(target, (m, y, w, h))
        widgets.draw_text(target, f"Инвентарь {inv.used_slots}/{inv.size}",
                          (m + 10, y + 8), size=config.UI_FONT_SIZE)
        if not items:
            widgets.draw_text(target, "пусто (E — инвентарь)", (m + 12, y + 36),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
            return
        for i, (rid, count) in enumerate(items):
            ry = y + 32 + i * ROW_H
            target.blit(self.icons.item_icon(rid), (m + 10, ry))
            item = sim.run.items[rid]
            # Длинное имя («Конструкционный материал») наезжало на число.
            room = w - 40 - 12 - widgets.text_width(str(count), config.UI_FONT_SIZE) - 8
            widgets.draw_text(target, _fit(item.name, room, config.UI_FONT_SIZE),
                              (m + 40, ry + 4), size=config.UI_FONT_SIZE)
            widgets.draw_text(target, str(count), (m + w - 12, ry + 4), align="right",
                              color=theme.OK, size=config.UI_FONT_SIZE)

    # ------------------------------------------------------- правый низ
    def _draw_controls(self, target: pygame.Surface) -> None:
        """Таблица клавиш у правого края, над хотбаром (не налезает на него).

        Строки длинные, поэтому они переносятся по ширине 🔧 `CONTROLS_W`:
        узкая колонка оставляет центр экрана карте.
        """
        surf = self._controls_panel()
        w, h = surf.get_size()
        x = config.WINDOW_W - config.UI_MARGIN - w
        y = self.hotbar.rect.top - CONTROLS_GAP - h
        target.blit(surf, (x, y))
        self._panels.append(pygame.Rect(x, y, w, h))

    def _controls_panel(self) -> pygame.Surface:
        """Таблица клавиш целиком — одна поверхность на всю игру.

        Таблица не меняется, а это ~27 строк текста: рендерить их каждый кадр
        значило бы создавать столько же поверхностей (CLAUDE.md, «кэши»).
        """
        if self._controls_surface is None:
            size = config.UI_FONT_SIZE_SMALL
            lines = self._control_lines()
            w = CONTROLS_W + 24
            h = len(lines) * CONTROLS_ROW + 12
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            widgets.draw_panel(surf, (0, 0, w, h))
            for i, (line, indent) in enumerate(lines):
                widgets.draw_text(surf, line, (12 + indent, 6 + i * CONTROLS_ROW),
                                  color=theme.UI_TEXT_DIM, size=size)
            self._controls_surface = surf
        return self._controls_surface

    def _control_lines(self) -> list[tuple[str, int]]:
        """Строки таблицы, перенесённые по словам. Считаются один раз."""
        if self._controls_cache is None:
            size = config.UI_FONT_SIZE_SMALL
            out: list[tuple[str, int]] = []
            for line in CONTROLS:
                indent = 0
                for part in _wrap(line, CONTROLS_W - CONTROLS_INDENT, size):
                    out.append((part, indent))
                    indent = CONTROLS_INDENT
            self._controls_cache = out
        return self._controls_cache

    # ------------------------------------------------------- сообщения
    def _draw_toasts(self, target: pygame.Surface, sim: Simulation) -> None:
        y = TOAST_TOP
        for toast in reversed(sim.toasts):
            ratio = toast.alpha_ratio
            color = theme.UI_TEXT if ratio > 0.35 else theme.UI_TEXT_DIM
            rect = widgets.draw_text(target, toast.text, (config.WINDOW_W // 2, y),
                                     color=color, size=config.UI_FONT_SIZE, align="center")
            y = rect.bottom + 4

    # ------------------------------------- подсказка у курсора
    def _draw_prompt(self, target: pygame.Surface, sim: Simulation) -> None:
        """Что можно сделать с тем, на что смотрит игрок: залежь, реликвия, лаб.

        Приоритет тот же, что у симуляции: реликвия → залежь → брошенный предмет.
        Строка одна, поэтому при возможности двух действий (добыть и
        сканировать) они перечисляются через запятую.
        """
        text, color = self._prompt_for(sim)
        if text is None:
            return
        w = widgets.text_width(text, config.UI_FONT_SIZE) + 20
        x = config.WINDOW_W // 2 - w // 2
        # Над хотбаром, с местом под его подпись: они не должны налезать.
        y = self.hotbar.rect.top - PROMPT_ABOVE_HOTBAR
        widgets.draw_panel(target, (x, y, w, 26))
        widgets.draw_text(target, text, (config.WINDOW_W // 2, y + 5),
                          color=color, size=config.UI_FONT_SIZE, align="center")

    @staticmethod
    def _prompt_for(sim: Simulation) -> tuple[str | None, tuple[int, int, int]]:
        """Подсказка у курсора. Порядок — тот же, что у симуляции (plan/10)."""
        relic = sim.cursor_relic
        if relic is not None and not relic.studied:
            if sim.study_target is relic:
                return "Изучаю реликвию", theme.STUDY_BAR_FG
            if not sim.in_study_range(relic):
                return "Реликвия: слишком далеко", theme.UI_TEXT_DIM
            return "ЛКМ (держать) — изучить реликвию", theme.STUDY_BAR_FG
        own = Hud._own_building_prompt(sim)
        if own is not None:
            return own
        kind = sim.selected_building()
        d = sim.cursor_deposit
        if kind is not None and sim.cursor_tile is not None:
            # С постройкой в руке ПКМ ставит, а не сканирует (plan/10).
            name = building_def(kind).name
            if sim.can_place(kind, *sim.cursor_tile):
                return f"ПКМ — поставить: {name}", theme.OK
            if d is None:
                return f"{name}: здесь не поставить", theme.UI_TEXT_DIM
        if d is None:
            drop = sim.drops.at(*sim.cursor_tile) if sim.cursor_tile else None
            if drop is not None:
                # Подбор автоматический: подсказка объясняет, почему предмет
                # лежит, а не летит в инвентарь (далеко или ещё нельзя).
                name = sim.run.items[drop.item_id].name
                return (f"{name} ×{drop.count} — подберётся при подходе",
                        theme.UI_TEXT_DIM)
            return None, theme.UI_TEXT
        name = sim.run.items[d.resource_id].name
        if d.depleted:
            return f"{name}: истощено", theme.UI_TEXT_DIM
        if sim.mine_target is d:
            return f"Добываю {name}", theme.MINE_BAR_FG
        if sim.scan_target is d:
            # Скан достаёт дальше добычи (🔧 SCAN_RANGE > MINE_RANGE): пока он
            # идёт, «слишком далеко» было бы неправдой.
            return f"Сканирую {name}", theme.SCAN_BAR_FG
        if not sim.in_mine_range(d):
            return f"{name}: слишком далеко", theme.UI_TEXT_DIM
        text = f"ЛКМ — добывать {name}"
        if kind is not None:
            pass                               # ПКМ занята постройкой: скана нет
        elif sim.knowledge.unknown(d.resource_id):
            # С M4 скан стоит энергии: подсказка говорит об этом заранее, иначе
            # игрок просто держал бы ПКМ и ждал.
            energy = sim.cargo.has(ENERGY_ITEM, config.SCAN_ENERGY_COST)
            text += ", ПКМ — скан свойств" if energy else ", ПКМ — скан (нужна энергия)"
        else:
            text += " (свойства открыты)"
        return text, theme.MINE_BAR_FG

    @staticmethod
    def _own_building_prompt(sim: Simulation) -> tuple[str, tuple[int, int, int]] | None:
        """Своя постройка под курсором: разбор удержанием ЛКМ и окно по ПКМ."""
        if sim.cursor_tile is None:
            return None
        building = sim.buildings.at(*sim.cursor_tile)
        lamp = sim.light.lamp_at(*sim.cursor_tile)
        target = building if building is not None else lamp
        if target is None:
            return None
        name = building_def(building.kind if building is not None else "lamp").name
        if sim.dismantle_target is target:
            return f"Разбираю: {name}", theme.DISMANTLE_RING
        if sim.dismantle_candidate() is None:
            return f"{name}: слишком далеко", theme.UI_TEXT_DIM
        text = f"{name}: ЛКМ (держать) — разобрать"
        if building is not None and building.containers:
            text += ", ПКМ — контейнеры"
        return text, theme.UI_TEXT

    # ---------------------------------------------------------- пауза
    def _draw_pause(self, target: pygame.Surface) -> None:
        target.blit(_pause_overlay(target.get_size()), (0, 0))
        widgets.draw_text(target, "ПАУЗА", (config.WINDOW_W // 2, config.WINDOW_H // 2 - 30),
                          color=theme.UI_TEXT, size=config.UI_FONT_SIZE_LARGE,
                          align="center")
        widgets.draw_text(target, "Пробел — продолжить",
                          (config.WINDOW_W // 2, config.WINDOW_H // 2 + 8),
                          color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE,
                          align="center")
