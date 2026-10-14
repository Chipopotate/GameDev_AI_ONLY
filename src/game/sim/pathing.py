"""Поле пути врагов (plan/05 «Правило целей», plan/07 §6). Не импортирует pygame.

Путь не считается для каждого врага: на тип врага одно поле. Это
мультиисточниковый Дейкстра **от целей**: стартовая стоимость цели
`FLOW_BASE − приоритет × K` (чем важнее цель, тем «ближе» она кажется),
шаг по свободному тайлу стоит 1, по тайлу с постройкой — `10 + hp/10`
(стена — дорогой тайл, а не непроходимый), горы и вода непроходимы. Враг
просто шагает к соседу с меньшим значением поля.

Так правило «путь с минимумом стен» и «слабое место» получаются сами: пока
есть обход, он дешевле стены; закрыл проходы — дешевле всего оставшийся.
"""

from __future__ import annotations

import heapq
import math

import numpy as np

NEIGHBORS = ((1, 0), (0, 1), (-1, 0), (0, -1))


class FlowField:
    """Значения поля на тайлах и шаг «куда идти» из любого тайла."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._flat: list[float] = [math.inf] * (width * height)
        #: Поле как массив (H, W) — его рисует отладочный слой F1.
        self.dist = np.full((height, width), np.inf, dtype=np.float32)
        self.computes = 0

    def compute(self, passable: np.ndarray, extra: dict[tuple[int, int], float],
                sources: list[tuple[int, int, float]]) -> None:
        """Пересчёт поля. `extra` — стоимость тайлов с постройками, `sources` —
        (x, y, стартовая стоимость) целей."""
        w, h = self.width, self.height
        walk = passable.ravel().tolist()
        dist = [math.inf] * (w * h)
        heap: list[tuple[float, int]] = []
        for x, y, start in sources:
            if 0 <= x < w and 0 <= y < h:
                i = y * w + x
                if start < dist[i]:
                    dist[i] = start
                    heapq.heappush(heap, (start, i))
        while heap:
            d, i = heapq.heappop(heap)
            if d > dist[i]:
                continue
            x, y = i % w, i // w
            for dx, dy in NEIGHBORS:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                j = ny * w + nx
                if not walk[j]:
                    continue
                nd = d + extra.get((nx, ny), 1.0)
                if nd < dist[j]:
                    dist[j] = nd
                    heapq.heappush(heap, (nd, j))
        self._flat = dist
        self.dist = np.array(dist, dtype=np.float32).reshape(h, w)
        self.computes += 1

    def value(self, x: int, y: int) -> float:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return math.inf
        return self._flat[y * self.width + x]

    def next_tile(self, x: int, y: int) -> tuple[int, int] | None:
        """Соседний тайл, куда шагнуть (с меньшим значением). None — идти некуда."""
        best = self.value(x, y)
        step = None
        for dx, dy in NEIGHBORS:
            v = self.value(x + dx, y + dy)
            if v < best:
                best, step = v, (x + dx, y + dy)
        return step
