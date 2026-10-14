"""Отладочные оверлеи (plan/07 §11).

F3 — основная панель: FPS, время кадра, счётчики, время суток, свет.
F2 — слой света: какие тайлы симуляция считает освещёнными.
F1 зарезервирован под поле пути (M5) — пока только сообщает, на каком этапе появится.
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.render.renderer import Renderer
from src.game.sim.simulation import Simulation
from src.game.sim.time_cycle import DAY, NIGHT
from src.game.ui import widgets

RESERVED = {
    "f1": "F1 — поле пути Ходоков: красное — куда стекаются враги",
}

PHASE_NAMES = {DAY: "день", NIGHT: "ночь"}


class DebugOverlay:
    """Панель отладки. Включается по F3."""

    def __init__(self) -> None:
        self.visible = False
        self.show_path = False    # F1 — заполнится на M5
        self.show_layers = False  # F2 — слой света (M2)

    def toggle(self, key: str) -> None:
        if key == "f1":
            self.show_path = not self.show_path
        elif key == "f2":
            self.show_layers = not self.show_layers
        elif key == "f3":
            self.visible = not self.visible

    def draw(self, target: pygame.Surface, sim: Simulation, camera: Camera,
             renderer: Renderer, fps: float, frame_ms: float) -> None:
        if not self.visible:
            return
        cycle = sim.time_cycle
        lines = [
            f"FPS {fps:5.1f}   кадр {frame_ms:5.2f} мс",
            f"симуляция t={sim.time:7.2f} с",
            f"день {cycle.day_number}, {PHASE_NAMES[cycle.phase]}, "
            f"до смены {cycle.t_left:5.1f} с, тьма {sim.darkness:.2f}",
            f"дрон {sim.drone.x:6.2f} {sim.drone.y:6.2f}",
            f"камера {camera.x:6.2f} {camera.y:6.2f} зум {camera.zoom:.1f}",
            f"освещено {sim.tiles_lit_ratio * 100:5.1f}%   "
            f"ламп {len(sim.light.lamps)}   пересчётов света {sim.light.rebuilds}",
            f"чанков нарисовано {renderer.chunks.chunks_drawn}",
            f"чанков масштабировано {renderer.chunks.scaled_drawn}",
            f"залежей в кадре {renderer.deposits_drawn}/{len(sim.world.deposits)}",
            f"активных залежей {len(sim.world.active_deposits())}",
            f"ресурсов забега {len(sim.run.items.natural)} "
            f"(редкий: {sim.run.items.get(sim.run.items.rare_id).name if sim.run.items.rare_id else 'нет'})",
            f"свойств открыто {sim.props_known[0]}/{sim.props_known[1]}   "
            f"рецептов {sim.knowledge.known_count}   сканов {sim.scans_done}",
            f"реликвий {renderer.relics_drawn}/{len(sim.relics)} в кадре, "
            f"изучено {sum(1 for r in sim.relics if r.studied)}",
            f"зданий {renderer.buildings_drawn}/{len(sim.buildings.buildings)} в кадре, "
            f"скрафчено {sim.crafted_total}",
            f"врагов {sim.enemies.count()}, убито {sim.enemies.killed}, "
            f"пересчётов поля {sim.enemies.fields['walker'].computes}",
        ]
        if self.show_path:
            lines.append(RESERVED["f1"])
        if self.show_layers:
            lines.append("F2 — слой света включён (жёлтый — освещено)")

        m = config.UI_MARGIN
        size = config.UI_FONT_SIZE_SMALL
        w = max(widgets.text_width(t, size) for t in lines) + 24
        h = len(lines) * 17 + 14
        x = config.WINDOW_W - m - w
        y = m
        widgets.draw_panel(target, (x, y, w, h), alpha=220)
        for i, line in enumerate(lines):
            widgets.draw_text(target, line, (x + 12, y + 7 + i * 17),
                              color=theme.DEBUG_TEXT, size=size)
