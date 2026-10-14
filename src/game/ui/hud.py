"""HUD: инвентарь, подсказки, сообщения, пауза."""

from __future__ import annotations

import pygame

from src.game import config
from src.game.data.buildings import BUILDING_TYPES, BUILD_ORDER
from src.game.data.recipes import ROLES
from src.game.data.resources import PROP_NAMES
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.render.icons import IconCache
from src.game.sim.simulation import ENERGY_ITEM, Simulation
from src.game.ui import widgets

#: Названия для строки HUD — из данных здания, чтобы новая строка таблицы
#: появлялась в интерфейсе сама (plan/03: типы описываются данными).
BUILD_NAMES = {kind: definition.name.lower() for kind, definition in BUILDING_TYPES.items()}

#: Подсказка «какая клавиша что строит» — порядок из данных (`BUILD_ORDER`),
#: по 🔧 четыре здания в строке: одной строкой она была бы шире экрана.
BUILD_KEYS_HINT = tuple(
    ", ".join(f"{i + 1} — {BUILD_NAMES[kind]}"
              for i, kind in enumerate(BUILD_ORDER[start:start + 4], start=start))
    for start in range(0, len(BUILD_ORDER), 4)
)

CONTROLS = (
    "WASD — движение",
    "ЛКМ (держать) — добывать / изучать",
    "ПКМ (держать) — скан свойств (энергия)",
    "ЛКМ (клик) — поставить/снять постройку",
    "ПКМ по зданию — его контейнеры",
    "T — повернуть конвейер под курсором",
) + BUILD_KEYS_HINT + (
    "I или Tab — инвентарь, R — рецепты",
    "Пробел — пауза, колесо — зум",
    "F2 — слой света, F3 — отладка",
    "Esc — выход",
)

ROW_H = 26
TOAST_TOP = 96             # ниже панели времени суток
TIME_PANEL_W = 210

_pause_overlays: dict[tuple[int, int], pygame.Surface] = {}


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

    def draw(self, target: pygame.Surface, sim: Simulation, camera: Camera,
             fps: float, paused: bool) -> None:
        self._draw_status(target, sim, camera, fps)
        self._draw_time(target, sim)
        self._draw_inventory(target, sim)
        self._draw_controls(target)
        self._draw_toasts(target, sim)
        self._draw_prompt(target, sim)
        if paused:
            self._draw_pause(target)

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
            f"Добыто: {sim.mined_total}",
            f"Стройка: {BUILD_NAMES.get(sim.selected_building, sim.selected_building)}",
            # Энергия сети (M4): выработка/спрос. Если выработки меньше, часть
            # зданий стоит — это видно по числам без отдельной лампочки.
            f"Энергия: {supply:.0f}/{demand:.0f}",
            f"Свойств: {known}/{total}",
            f"Рецептов: {sim.knowledge.known_count}/{len(ROLES) * 2}",
        )
        w = 190
        h = len(lines) * 18 + 12
        widgets.draw_panel(target, (m, m, w, h))
        for i, line in enumerate(lines):
            widgets.draw_text(target, line, (m + 10, m + 6 + i * 18),
                              size=config.UI_FONT_SIZE_SMALL)

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
            # M8: сколько баз осталось до победы и сколько врагов на карте.
            (f"ростков {len(sim.bases.live)} · врагов {len(sim.enemies.enemies)}",
             theme.UI_TEXT_DIM),
        )
        w = TIME_PANEL_W
        h = len(lines) * 18 + 12
        x = config.WINDOW_W // 2 - w // 2
        y = config.UI_MARGIN
        widgets.draw_panel(target, (x, y, w, h))
        for i, (text, color) in enumerate(lines):
            widgets.draw_text(target, text, (config.WINDOW_W // 2, y + 6 + i * 18),
                              color=color, size=config.UI_FONT_SIZE_SMALL, align="center")

    # -------------------------------------------------------- левый низ
    def _draw_inventory(self, target: pygame.Surface, sim: Simulation) -> None:
        """Сводка по инвентарю: что и сколько. Полная сетка слотов — по клавише I."""
        items = sim.inventory.items()
        inv = sim.inventory
        m = config.UI_MARGIN
        w = 232
        h = 30 + max(1, len(items)) * ROW_H + 8
        y = config.WINDOW_H - m - h
        widgets.draw_panel(target, (m, y, w, h))
        widgets.draw_text(target, f"Инвентарь {inv.used_slots}/{inv.size}",
                          (m + 10, y + 8), size=config.UI_FONT_SIZE)
        if not items:
            widgets.draw_text(target, "пусто (I — слоты)", (m + 12, y + 36),
                              color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL)
            return
        for i, (rid, count) in enumerate(items):
            ry = y + 32 + i * ROW_H
            target.blit(self.icons.item_icon(rid), (m + 10, ry))
            item = sim.run.items[rid]
            widgets.draw_text(target, item.name, (m + 40, ry + 4), size=config.UI_FONT_SIZE)
            widgets.draw_text(target, str(count), (m + w - 12, ry + 4), align="right",
                              color=theme.OK, size=config.UI_FONT_SIZE)

    # ------------------------------------------------------- правый низ
    def _draw_controls(self, target: pygame.Surface) -> None:
        m = config.UI_MARGIN
        size = config.UI_FONT_SIZE_SMALL
        w = max(widgets.text_width(t, size) for t in CONTROLS) + 24
        h = len(CONTROLS) * 18 + 12
        x = config.WINDOW_W - m - w
        y = config.WINDOW_H - m - h
        widgets.draw_panel(target, (x, y, w, h))
        for i, line in enumerate(CONTROLS):
            widgets.draw_text(target, line, (x + 12, y + 6 + i * 18),
                              color=theme.UI_TEXT_DIM, size=size)

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
        y = config.WINDOW_H - 96
        widgets.draw_panel(target, (x, y, w, 26))
        widgets.draw_text(target, text, (config.WINDOW_W // 2, y + 5),
                          color=color, size=config.UI_FONT_SIZE, align="center")

    @staticmethod
    def _prompt_for(sim: Simulation) -> tuple[str | None, tuple[int, int, int]]:
        relic = sim.cursor_relic
        if relic is not None and not relic.studied:
            if sim.study_target is relic:
                return "Изучаю реликвию", theme.STUDY_BAR_FG
            if not sim.in_study_range(relic):
                return "Реликвия: слишком далеко", theme.UI_TEXT_DIM
            return "ЛКМ — изучить реликвию", theme.STUDY_BAR_FG
        d = sim.cursor_deposit
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
        if not sim.in_mine_range(d):
            return f"{name}: слишком далеко", theme.UI_TEXT_DIM
        text = f"ЛКМ — добывать {name}"
        if sim.scan_target is d:
            text += ", ПКМ — сканирую"
        elif sim.knowledge.unknown(d.resource_id):
            # С M4 скан стоит энергии: подсказка говорит об этом заранее, иначе
            # игрок просто держал бы ПКМ и ждал.
            energy = sim.inventory.has(ENERGY_ITEM, config.SCAN_ENERGY_COST)
            text += ", ПКМ — скан свойств" if energy else ", ПКМ — скан (нужна энергия)"
        else:
            text += " (свойства открыты)"
        return text, theme.MINE_BAR_FG

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
