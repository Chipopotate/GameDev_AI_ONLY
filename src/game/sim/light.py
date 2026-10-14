"""Свет и тьма: лампы и слой освещённости (plan/01). Не импортирует pygame.

Правило из plan/01: свет пересчитывается **только** когда лампа построена,
снесена, включена или выключена. Поэтому все изменения ставят флаг `_dirty`,
а пересчёт слоя идёт при первом обращении к нему.

Тьма — это просто «не освещено»: отдельного слоя у неё нет, `lit` и есть ответ
на вопрос «виден ли тайл». Заражение (M8) — независимый слой, свет его не лечит.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.game import config
from src.game.sim.world import World


@dataclass
class Lamp:
    """Лампа на тайле. Светит кругом радиуса `radius` тайлов, стен не учитывает.

    Поле `on` появится в дело на M4 (питание от генератора) и M6 (москит гасит
    лампу): включение и выключение — такой же повод пересчитать свет, как
    постройка и снос.
    """

    id: int
    x: int
    y: int
    radius: float = config.LAMP_LIGHT_RADIUS
    on: bool = True


class LightSystem:
    """Лампы и слой `lit`: тайл освещён, если его накрывает хоть одна лампа."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.lamps: list[Lamp] = []
        self.rebuilds = 0        # сколько раз слой реально пересчитывался
        self._next_id = 0
        self._lit = np.zeros((world.height, world.width), dtype=bool)
        self._dirty = True
        # «Доступные тайлы» — проходимая земля, связанная с точкой старта.
        # Считается один раз: до обвала гор (M8) карта не меняется.
        self._reachable: np.ndarray | None = None
        self._reachable_count = 0

    # --------------------------------------------------------------- лампы
    def add_lamp(self, x: int, y: int, radius: float | None = None,
                 on: bool = True) -> Lamp:
        """Ставит лампу. Проверку «можно ли здесь» делает симуляция."""
        lamp = Lamp(self._next_id, int(x), int(y),
                    config.LAMP_LIGHT_RADIUS if radius is None else float(radius), on)
        self._next_id += 1
        self.lamps.append(lamp)
        self._dirty = True
        return lamp

    def remove_lamp(self, lamp: Lamp) -> bool:
        """Сносит лампу. False, если её уже нет."""
        if lamp not in self.lamps:
            return False
        self.lamps.remove(lamp)
        self._dirty = True
        return True

    def lamp_at(self, x: int, y: int) -> Lamp | None:
        for lamp in self.lamps:
            if lamp.x == x and lamp.y == y:
                return lamp
        return None

    def set_lamp_on(self, lamp: Lamp, on: bool) -> None:
        """Включает/выключает лампу. Свет пересчитается при следующем чтении."""
        if lamp.on != on:
            lamp.on = on
            self._dirty = True

    # ---------------------------------------------------------------- свет
    @property
    def lit(self) -> np.ndarray:
        """Слой освещённости (H, W), bool. Пересчитывается только при изменении."""
        if self._dirty:
            self._rebuild()
        return self._lit

    def is_lit(self, x: int, y: int) -> bool:
        """Освещён ли тайл. Вне карты — нет."""
        if not self.world.in_bounds(x, y):
            return False
        return bool(self.lit[y, x])

    def lit_ratio(self) -> float:
        """Доля освещённых доступных тайлов, 0..1.

        Доступные — проходимая земля, связанная со стартом (см. `reachable`).
        Собственный свет дрона сюда не входит: по plan/01 он «почти не считается
        как осветил», и для победы его недостаточно.
        """
        reachable = self.reachable
        if self._reachable_count == 0:
            return 0.0
        lit = self.lit
        return float(np.count_nonzero(lit & reachable)) / self._reachable_count

    @property
    def reachable(self) -> np.ndarray:
        """Маска доступных тайлов (H, W), bool. Считается один раз."""
        if self._reachable is None:
            self._reachable = self._compute_reachable()
            self._reachable_count = int(np.count_nonzero(self._reachable))
        return self._reachable

    def _compute_reachable(self) -> np.ndarray:
        """Обход в ширину от точки старта по проходимым тайлам."""
        mask = np.zeros_like(self._lit)
        sx, sy = self.world.spawn_point()
        start = (int(math.floor(sx)), int(math.floor(sy)))
        if not self.world.is_walkable_tile(*start):
            return mask
        mask[start[1], start[0]] = True
        queue: deque[tuple[int, int]] = deque([start])
        while queue:
            cx, cy = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if not self.world.in_bounds(nx, ny) or mask[ny, nx]:
                    continue
                if not self.world.is_walkable_tile(nx, ny):
                    continue
                mask[ny, nx] = True
                queue.append((nx, ny))
        return mask

    def _rebuild(self) -> None:
        """Полный пересчёт слоя по всем включённым лампам."""
        self._lit.fill(False)
        for lamp in self.lamps:
            if lamp.on:
                self._stamp(self._lit, lamp.x, lamp.y, lamp.radius)
        self._dirty = False
        self.rebuilds += 1

    def _stamp(self, layer: np.ndarray, x: int, y: int, radius: float) -> None:
        """Накладывает круг света в границах радиуса — векторно по тайлам."""
        h, w = layer.shape
        cx, cy = x + 0.5, y + 0.5
        x0 = max(0, int(math.floor(cx - radius)))
        x1 = min(w - 1, int(math.ceil(cx + radius)))
        y0 = max(0, int(math.floor(cy - radius)))
        y1 = min(h - 1, int(math.ceil(cy + radius)))
        if x0 > x1 or y0 > y1:
            return
        yy = np.arange(y0, y1 + 1, dtype=np.float32)[:, None] + 0.5
        xx = np.arange(x0, x1 + 1, dtype=np.float32)[None, :] + 0.5
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius * radius
        layer[y0:y1 + 1, x0:x1 + 1] |= mask
