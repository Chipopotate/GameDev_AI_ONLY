"""Ростки, инкубаторы и заражение (plan/06, урезанный M8). Не импортирует pygame.

- Ростки стоят **за горами**: 🔧 `SPROUT_COUNT` штук вне стартового кольца.
- Перед каждой ночью росток получает очки роста (база + бонус за номер дня) и
  тратит их случайно с весами: новый инкубатор, улучшение инкубатора, шире
  заражение. Атакованный росток не растёт — очки уходят на лечение.
- Заражение — свой слой, свет его не лечит. После гибели ростка его тайлы
  остаются заражёнными, но неактивными (`infected` без `active`).
- Возрождение (❓ таймер или Спороносец → таймер, 🔧 `SPROUT_RESPAWN`): через
  1–2 суток на **тёмном** тайле. Тайл выбирает симуляция — она знает свет.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from src.game import config
from src.game.data.enemies import INCUBATOR_TYPES, incubators_for_day
from src.game.data.terrain import Terrain
from src.game.sim.world import World

Tile = tuple[int, int]


@dataclass
class Incubator:
    id: int
    kind: str                  # ключ `INCUBATOR_TYPES`
    x: int
    y: int
    hp: float
    level: int = 1

    @property
    def max_hp(self) -> float:
        return INCUBATOR_TYPES[self.kind].hp

    @property
    def spawn_count(self) -> int:
        """Сколько врагов за ночь: растёт с уровнем (plan/06)."""
        t = INCUBATOR_TYPES[self.kind]
        return t.spawn_base + t.spawn_per_level * (self.level - 1)


@dataclass
class Sprout:
    id: int
    x: int
    y: int
    hp: float = config.SPROUT_HP
    radius: int = config.SPROUT_START_RADIUS
    level: int = 1             # уровень заражения: от него потолок инкубаторов
    incubators: list[Incubator] = field(default_factory=list)
    attacked: int = 0          # сколько ночей росток ещё «помнит» штурм
    alive: bool = True

    @property
    def max_hp(self) -> float:
        return config.SPROUT_HP

    @property
    def incubator_cap(self) -> int:
        return config.INCUBATOR_CAP_BASE + config.INCUBATOR_CAP_PER_LEVEL * self.level


@dataclass
class Respawn:
    """Споры в полёте: через `timer` секунд на тайле `target` вырастет росток."""

    timer: float
    target: Tile | None


class BaseSystem:
    """Все ростки забега, их инкубаторы, слой заражения и ожидание возрождения."""

    def __init__(self, world: World, rng: random.Random) -> None:
        self.world = world
        self.rng = rng
        self.sprouts: list[Sprout] = []
        self.respawns: list[Respawn] = []
        self.infected = np.zeros((world.height, world.width), dtype=bool)
        self.active = np.zeros_like(self.infected)
        self.version = 0           # растёт при каждом изменении слоя (рендер)
        self._next_id = 0

    # ------------------------------------------------------------ генерация
    def generate(self, blocked: Callable[[int, int], bool]) -> None:
        """Ставит 🔧 `SPROUT_COUNT` ростков за горным кольцом.

        `blocked(x, y)` — занятость тайла по мнению симуляции (реликвии и т.п.).
        """
        sx, sy, sw, sh = self.world.start_rect
        gap = config.MOUNTAIN_RING_THICKNESS + config.SPROUT_MIN_RING_GAP
        ys, xs = np.nonzero(self.world.terrain == int(Terrain.GROUND))
        far = (xs < sx - gap) | (xs > sx + sw - 1 + gap) | \
              (ys < sy - gap) | (ys > sy + sh - 1 + gap)
        # Не вплотную к краю карты: вокруг ростка должно быть место инкубаторам.
        edge = 2
        far &= (xs >= edge) & (ys >= edge) & \
               (xs < self.world.width - edge) & (ys < self.world.height - edge)
        candidates = list(zip(xs[far].tolist(), ys[far].tolist()))
        want = self.rng.randint(*config.SPROUT_COUNT)
        for _ in range(want):
            for _ in range(200):
                if not candidates:
                    break
                x, y = candidates[self.rng.randrange(len(candidates))]
                if blocked(x, y) or self.world.deposit_at(x, y) is not None:
                    continue
                if any(max(abs(x - s.x), abs(y - s.y)) < config.SPROUT_MIN_SPACING
                       for s in self.sprouts):
                    continue
                self.add_sprout(x, y, self.rng.randint(*config.SPROUT_START_INCUBATORS),
                                day=1, blocked=blocked)
                break
        self.refresh_infection()

    def add_sprout(self, x: int, y: int, incubators: int, day: int,
                   blocked: Callable[[int, int], bool]) -> Sprout:
        sprout = Sprout(self._next_id, x, y)
        self._next_id += 1
        self.sprouts.append(sprout)
        for _ in range(incubators):
            self._add_incubator(sprout, day, blocked)
        self.refresh_infection()
        return sprout

    def _add_incubator(self, sprout: Sprout, day: int,
                       blocked: Callable[[int, int], bool]) -> Incubator | None:
        kinds = incubators_for_day(day)
        if not kinds or len(sprout.incubators) >= sprout.incubator_cap:
            return None
        spots = []
        r = sprout.radius
        for ty in range(sprout.y - r, sprout.y + r + 1):
            for tx in range(sprout.x - r, sprout.x + r + 1):
                d2 = (tx - sprout.x) ** 2 + (ty - sprout.y) ** 2
                if d2 < 4 or d2 > r * r:
                    continue
                if not self.world.is_walkable_tile(tx, ty) or self.at(tx, ty) is not None:
                    continue
                if self.world.deposit_at(tx, ty) is not None or blocked(tx, ty):
                    continue
                spots.append((tx, ty))
        if not spots:
            return None
        x, y = spots[self.rng.randrange(len(spots))]
        kind = kinds[self.rng.randrange(len(kinds))]
        inc = Incubator(self._next_id, kind, x, y, INCUBATOR_TYPES[kind].hp)
        self._next_id += 1
        sprout.incubators.append(inc)
        return inc

    # -------------------------------------------------------------- запросы
    @property
    def live(self) -> list[Sprout]:
        return [s for s in self.sprouts if s.alive]

    def incubators(self):
        for sprout in self.sprouts:
            if sprout.alive:
                yield from sprout.incubators

    def at(self, x: int, y: int) -> Sprout | Incubator | None:
        for sprout in self.sprouts:
            if not sprout.alive:
                continue
            if sprout.x == x and sprout.y == y:
                return sprout
            for inc in sprout.incubators:
                if inc.x == x and inc.y == y:
                    return inc
        return None

    def owner(self, inc: Incubator) -> Sprout | None:
        for sprout in self.sprouts:
            if inc in sprout.incubators:
                return sprout
        return None

    # ---------------------------------------------------------------- рост
    def grow(self, day: int, blocked: Callable[[int, int], bool]) -> int:
        """Очки роста перед ночью (plan/06). Возвращает, сколько ростков выросло."""
        points = config.GROWTH_BASE + config.GROWTH_PER_DAY * day
        grown = 0
        for sprout in self.live:
            if sprout.attacked > 0:
                # Базу штурмуют: роста нет, все очки — на оборону (лечение).
                sprout.attacked -= 1
                heal = points * config.SPROUT_HEAL_PER_POINT
                sprout.hp = min(sprout.max_hp, sprout.hp + heal)
                for inc in sprout.incubators:
                    inc.hp = min(inc.max_hp, inc.hp + heal)
                continue
            left = points
            while True:
                options = self._growth_options(sprout, day, left)
                if not options:
                    break
                total = sum(w for _, _, w in options)
                roll = self.rng.random() * total
                for option, cost, weight in options:
                    roll -= weight
                    if roll <= 0:
                        break
                if option == "incubator":
                    if self._add_incubator(sprout, day, blocked) is None:
                        break
                elif option == "upgrade":
                    pool = [i for i in sprout.incubators
                            if i.level < config.INCUBATOR_MAX_LEVEL]
                    pool[self.rng.randrange(len(pool))].level += 1
                else:
                    sprout.radius += 1
                    sprout.level += 1
                left -= cost
            grown += 1
        self.refresh_infection()
        return grown

    def _growth_options(self, sprout: Sprout, day: int,
                        points: int) -> list[tuple[str, int, float]]:
        out = []
        for option, (cost, weight) in config.GROWTH_OPTIONS.items():
            if cost > points or weight <= 0:
                continue
            if option == "incubator" and (len(sprout.incubators) >= sprout.incubator_cap
                                          or not incubators_for_day(day)):
                continue
            if option == "upgrade" and not any(
                    i.level < config.INCUBATOR_MAX_LEVEL for i in sprout.incubators):
                continue
            if option == "infect" and sprout.radius >= config.SPROUT_MAX_RADIUS:
                continue
            out.append((option, cost, weight))
        return out

    # ---------------------------------------------------------------- урон
    def damage(self, target: Sprout | Incubator, amount: float) -> bool:
        """Бьёт росток или инкубатор. True — цель погибла."""
        sprout = target if isinstance(target, Sprout) else self.owner(target)
        if sprout is None or not sprout.alive:
            return False
        sprout.attacked = config.SPROUT_ATTACK_MEMORY
        target.hp -= amount
        if target.hp > 0:
            return False
        if isinstance(target, Sprout):
            # Гибель ростка гасит базу: инкубаторов больше нет (plan/06).
            sprout.alive = False
            sprout.incubators.clear()
        else:
            sprout.incubators.remove(target)
        self.refresh_infection()
        return True

    # ------------------------------------------------------------ заражение
    def refresh_infection(self) -> None:
        """Активное заражение — диски живых ростков по проходимым тайлам."""
        self.active.fill(False)
        h, w = self.active.shape
        for sprout in self.live:
            r = sprout.radius
            x0, x1 = max(0, sprout.x - r), min(w - 1, sprout.x + r)
            y0, y1 = max(0, sprout.y - r), min(h - 1, sprout.y + r)
            yy = np.arange(y0, y1 + 1)[:, None]
            xx = np.arange(x0, x1 + 1)[None, :]
            disc = (xx - sprout.x) ** 2 + (yy - sprout.y) ** 2 <= r * r
            self.active[y0:y1 + 1, x0:x1 + 1] |= disc
        self.active &= self.world.terrain == int(Terrain.GROUND)
        self.infected |= self.active
        self.version += 1

    # ----------------------------------------------------------- возрождение
    def schedule_respawn(self, delay: float, target: Tile | None) -> Respawn:
        respawn = Respawn(delay, target)
        self.respawns.append(respawn)
        return respawn

    def state(self) -> tuple:
        return (tuple((s.x, s.y, round(s.hp, 3), s.radius, s.level, s.alive,
                       tuple((i.kind, i.x, i.y, round(i.hp, 3), i.level)
                             for i in s.incubators))
                      for s in self.sprouts),
                tuple((round(r.timer, 3), r.target) for r in self.respawns))
