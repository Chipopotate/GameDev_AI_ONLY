"""Враги урезанного M8 (plan/05). Не импортирует pygame.

Полноценные правила целей и флоу-филд со стоимостью стен — это M5. Здесь
простейшее, что даёт штурм работать:

- наземные идут по **полю расстояний** (обход в ширину от всех построек и ламп
  по проходимым тайлам) к ближайшей постройке; поле пересчитывается, только
  когда меняются постройки или рельеф;
- летающие (Москит) летят по прямой к цели «приоритет / расстояние»;
- в радиусе атаки враг бьёт постройку с лучшим счётом по приоритетам из данных;
- если до построек пути нет, враг идёт к дрону по прямой (дрону урона нет —
  🔧 `ENEMIES_HURT_DRONE = False`, поражение появится на M5).
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.game import config
from src.game.data.buildings import building as building_def
from src.game.data.enemies import ENEMY_TYPES, EnemyType
from src.game.data.terrain import Terrain
from src.game.sim.world import World

Tile = tuple[int, int]

#: Сколько тайлов дрон «тянет» к себе врагов, у которых нет пути к постройкам.
DRONE_PULL = 20.0


@dataclass
class Enemy:
    id: int
    kind: str
    x: float
    y: float
    hp: float
    max_hp: float
    damage: float
    cooldown: float = 0.0
    target: Tile | None = None     # цель летающего
    retarget: float = 0.0
    facing: float = 0.0

    @property
    def type(self) -> EnemyType:
        return ENEMY_TYPES[self.kind]


class EnemySystem:
    def __init__(self, world: World, rng: random.Random) -> None:
        self.world = world
        self.rng = rng
        self.enemies: list[Enemy] = []
        self.field: np.ndarray | None = None
        self.structures: dict[Tile, str] = {}
        self._field_key: tuple | None = None
        self._field_timer = 0.0
        self._next_id = 0
        self.killed = 0

    # ----------------------------------------------------------------- спаун
    def spawn(self, kind: str, x: float, y: float, day: int) -> Enemy | None:
        if len(self.enemies) >= config.ENEMY_MAX:
            return None
        t = ENEMY_TYPES[kind]
        scale = 1.0 + config.ENEMY_DAY_SCALE * day
        enemy = Enemy(self._next_id, kind, x, y, t.hp * scale, t.hp * scale,
                      t.damage * scale)
        self._next_id += 1
        self.enemies.append(enemy)
        return enemy

    def spawn_around(self, kind: str, cx: int, cy: int, count: int, day: int) -> int:
        """Вылупление из инкубатора: враги ложатся вокруг него."""
        made = 0
        flying = ENEMY_TYPES[kind].flying
        for _ in range(count):
            for _ in range(12):
                a = self.rng.random() * math.tau
                r = self.rng.random() * config.INCUBATOR_SPAWN_RADIUS
                x, y = cx + 0.5 + math.cos(a) * r, cy + 0.5 + math.sin(a) * r
                if flying or self.world.is_walkable_tile(int(x), int(y)):
                    if self.spawn(kind, x, y, day) is not None:
                        made += 1
                    break
        return made

    def remove(self, enemy: Enemy) -> None:
        if enemy in self.enemies:
            self.enemies.remove(enemy)
            self.killed += 1

    def at(self, wx: float, wy: float, radius: float) -> Enemy | None:
        best, best_d = None, radius
        for enemy in self.enemies:
            d = math.hypot(enemy.x - wx, enemy.y - wy)
            if d <= best_d:
                best, best_d = enemy, d
        return best

    # ------------------------------------------------------------- обновление
    def update(self, dt: float, structures_fn, drone_pos: tuple[float, float],
               units=()) -> list[tuple[object, float]]:
        """Шаг врагов. Возвращает удары: (тайл постройки или юнит, урон).

        `units` — роботы игрока (M7): робот в радиусе атаки бьётся первым,
        иначе отряд был бы бессмертным.
        """
        if not self.enemies:
            return []
        self._field_timer -= dt
        if self._field_timer <= 0.0 or self.field is None:
            self._field_timer = config.ENEMY_FIELD_REFRESH
            self.structures = structures_fn()
            self._refresh_field()
        hits: list[tuple[object, float]] = []
        for enemy in self.enemies:
            enemy.cooldown = max(0.0, enemy.cooldown - dt)
            t = enemy.type
            goal = self._unit_in_range(enemy, t, units) or \
                self._target_in_range(enemy, t)
            if goal is not None:
                if enemy.cooldown <= 0.0:
                    enemy.cooldown = t.attack_cooldown
                    hits.append((goal, enemy.damage))
                continue
            if t.flying:
                self._fly(enemy, t, dt)
            else:
                self._walk(enemy, t, dt, drone_pos)
        return hits

    def _score(self, t: EnemyType, kind: str, dist: float) -> float:
        prio = building_def(kind).enemy_priority * t.priorities.get(kind, 1.0)
        return prio / (1.0 + dist * 0.1)

    @staticmethod
    def _unit_in_range(enemy: Enemy, t: EnemyType, units):
        for unit in units:
            if math.hypot(unit.x - enemy.x, unit.y - enemy.y) <= t.attack_range + 0.3:
                return unit
        return None

    def _target_in_range(self, enemy: Enemy, t: EnemyType) -> Tile | None:
        if not self.structures:
            return None
        if not t.flying and self.field is not None:
            tx, ty = int(enemy.x), int(enemy.y)
            if self.world.in_bounds(tx, ty):
                d = int(self.field[ty, tx])
                # Поле дешевле перебора: дальше радиуса атаки построек нет.
                if d < 0 or d > math.ceil(t.attack_range) + 1:
                    return None
        best, best_score = None, -1.0
        reach = t.attack_range + 0.5
        for (sx, sy), kind in self.structures.items():
            d = math.hypot(sx + 0.5 - enemy.x, sy + 0.5 - enemy.y)
            if d > reach:
                continue
            score = self._score(t, kind, d)
            if score > best_score:
                best, best_score = (sx, sy), score
        return best

    def _fly(self, enemy: Enemy, t: EnemyType, dt: float) -> None:
        enemy.retarget -= dt
        if enemy.retarget <= 0.0 or enemy.target not in self.structures:
            enemy.retarget = config.ENEMY_RETARGET
            best, best_score = None, -1.0
            for (sx, sy), kind in self.structures.items():
                d = math.hypot(sx + 0.5 - enemy.x, sy + 0.5 - enemy.y)
                score = self._score(t, kind, d)
                if score > best_score:
                    best, best_score = (sx, sy), score
            enemy.target = best
        if enemy.target is not None:
            self._step_towards(enemy, enemy.target[0] + 0.5, enemy.target[1] + 0.5,
                               t.speed * dt, solid=False)

    def _walk(self, enemy: Enemy, t: EnemyType, dt: float,
              drone_pos: tuple[float, float]) -> None:
        tx, ty = int(enemy.x), int(enemy.y)
        field = self.field
        if field is not None and self.world.in_bounds(tx, ty) and field[ty, tx] > 0:
            cur = int(field[ty, tx])
            best = None
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = tx + dx, ty + dy
                if self.world.in_bounds(nx, ny) and 0 <= field[ny, nx] < cur:
                    cur, best = int(field[ny, nx]), (nx, ny)
            if best is not None:
                self._step_towards(enemy, best[0] + 0.5, best[1] + 0.5, t.speed * dt,
                                   solid=True)
                return
        # Пути к постройкам нет: тянемся к дрону, если он недалеко.
        if math.hypot(drone_pos[0] - enemy.x, drone_pos[1] - enemy.y) <= DRONE_PULL:
            self._step_towards(enemy, drone_pos[0], drone_pos[1], t.speed * dt, solid=True)

    def _step_towards(self, enemy: Enemy, gx: float, gy: float, step: float,
                      solid: bool) -> None:
        dx, dy = gx - enemy.x, gy - enemy.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return
        enemy.facing = math.atan2(dy, dx)
        k = min(1.0, step / dist)
        nx, ny = enemy.x + dx * k, enemy.y + dy * k
        if not solid:
            enemy.x, enemy.y = nx, ny
            return
        # Скольжение вдоль препятствия: оси по очереди.
        if self.world.is_walkable_tile(int(math.floor(nx)), int(math.floor(enemy.y))):
            enemy.x = nx
        if self.world.is_walkable_tile(int(math.floor(enemy.x)), int(math.floor(ny))):
            enemy.y = ny

    # ----------------------------------------------------------------- поле
    def _refresh_field(self) -> None:
        key = (self.world.version, tuple(sorted(self.structures)))
        if key == self._field_key and self.field is not None:
            return
        self._field_key = key
        h, w = self.world.height, self.world.width
        # Обход на плоских списках: поэлементный доступ к numpy в цикле Python
        # в разы медленнее, а поле считается целиком на 96×96.
        walkable = (self.world.terrain == int(Terrain.GROUND)).ravel().tolist()
        dist = [-1] * (w * h)
        queue: deque[int] = deque()
        for (x, y) in self.structures:
            if self.world.in_bounds(x, y):
                dist[y * w + x] = 0
                queue.append(y * w + x)
        while queue:
            i = queue.popleft()
            nd = dist[i] + 1
            x = i % w
            for j in (i - 1 if x > 0 else -1, i + 1 if x < w - 1 else -1,
                      i - w, i + w):
                if 0 <= j < w * h and dist[j] < 0 and walkable[j]:
                    dist[j] = nd
                    queue.append(j)
        field = np.array(dist, dtype=np.int32).reshape(h, w)
        self.field = field

    def state(self) -> tuple:
        return tuple((e.kind, round(e.x, 4), round(e.y, 4), round(e.hp, 3))
                     for e in self.enemies)
