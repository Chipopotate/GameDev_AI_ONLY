"""Враги, турели и выстрелы (M5, plan/05). Не импортирует pygame.

Один класс `Enemy` на всех: тип — строка таблицы `data/enemies.py`. ИИ нет:
враг шагает по полю своего типа (`sim/pathing.py`) и бьёт то, во что упёрся —
цель, стену на пути или дрона рядом. Слабые места обороны рождаются из поля,
а не из кода врага.

Турель (стационарный корпус + модуль оружия, plan/04) и слабое оружие дрона
стреляют сами по ближайшему врагу в радиусе; выстрел мгновенный, а след
(`Shot`) — только для отрисовки.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from src.game import config
from src.game.data.buildings import LAMP, building as building_def
from src.game.data.enemies import (ENEMY_TYPES, SPECIAL_ARTILLERY, EnemyType,
                                   enemy_type)
from src.game.data.recipes import role as role_def
from src.game.sim.pathing import FlowField

#: Предмет, который турель тратит на выстрелы (предмет роли «боеприпас»).
AMMO_ITEM = role_def("ammo").id

SHOT_TURRET = "turret"
SHOT_DRONE = "drone"
SHOT_ENEMY = "enemy"
SHOT_ARTILLERY = "artillery"
#: След залпа артиллерии виден дольше обычного: он объясняет, откуда прилетело.
ARTILLERY_TRACER = 0.3


@dataclass
class Enemy:
    """Враг на карте. Координаты — мировые (дробные), как у дрона."""

    id: int
    kind: str
    x: float
    y: float
    hp: float
    cooldown: float = 0.0
    facing: float = 0.0
    #: Артиллерия: по чему бьёт с позиции и сколько уже развернулась, с.
    siege: object | None = None
    deploy: float = 0.0

    @property
    def deployed(self) -> bool:
        return self.siege is not None and self.deploy >= self.type.deploy_time

    @property
    def type(self) -> EnemyType:
        return enemy_type(self.kind)

    @property
    def tile(self) -> tuple[int, int]:
        return int(math.floor(self.x)), int(math.floor(self.y))


@dataclass
class Shot:
    """След выстрела или удара — для отрисовки, живёт `SHOT_TRACER_TIME`."""

    x0: float
    y0: float
    x1: float
    y1: float
    source: str
    ttl: float = config.SHOT_TRACER_TIME


class EnemySystem:
    """Все враги забега, их поля пути и стрельба обороны."""

    def __init__(self, world, rng: random.Random) -> None:
        self.world = world
        self.rng = rng
        self.enemies: list[Enemy] = []
        self.shots: list[Shot] = []
        self.fields = {kind: FlowField(world.width, world.height) for kind in ENEMY_TYPES}
        self.killed = 0
        self._next_id = 0
        self._key: tuple | None = None
        self._timer = 0.0
        self._candidates: list[tuple[int, int]] | None = None

    # ------------------------------------------------------------ спавн
    def spawn(self, kind: str, x: float, y: float) -> Enemy:
        enemy = Enemy(self._next_id, kind, float(x), float(y), enemy_type(kind).hp)
        self._next_id += 1
        self.enemies.append(enemy)
        return enemy

    def spawn_dark(self, sim, kind: str, count: int) -> int:
        """Ставит врагов на тёмные доступные тайлы подальше от дрона и построек.

        До инкубаторов (M8) и обвала гор это единственный источник врагов:
        «рождаются в темноте» (plan/05, Тень) — и Ходоки первой ночи тоже.
        """
        made = 0
        for _ in range(max(0, count)):
            tile = self._dark_tile(sim)
            if tile is None:
                break
            self.spawn(kind, tile[0] + 0.5, tile[1] + 0.5)
            made += 1
        return made

    def _dark_tile(self, sim) -> tuple[int, int] | None:
        if self._candidates is None:
            ys, xs = sim.light.reachable.nonzero()
            self._candidates = list(zip(xs.tolist(), ys.tolist()))
        if not self._candidates:
            return None
        lit = sim.light.lit
        posts = [(b.x, b.y) for b in sim.buildings.buildings]
        posts += [(lamp.x, lamp.y) for lamp in sim.light.lamps]
        far = config.ENEMY_SPAWN_MIN_DIST
        for _ in range(config.ENEMY_SPAWN_TRIES):
            x, y = self._candidates[self.rng.randrange(len(self._candidates))]
            if lit[y, x]:
                continue
            if math.hypot(x + 0.5 - sim.drone.x, y + 0.5 - sim.drone.y) < far:
                continue
            if any(math.hypot(x - px, y - py) < far for px, py in posts):
                continue
            return x, y
        return None

    def remove_kind(self, kind: str) -> int:
        """Убирает всех врагов вида (Тени тают на рассвете)."""
        before = len(self.enemies)
        self.enemies = [e for e in self.enemies if e.kind != kind]
        return before - len(self.enemies)

    def count(self, kind: str | None = None) -> int:
        return sum(1 for e in self.enemies if kind is None or e.kind == kind)

    # ---------------------------------------------------------------- шаг
    def update(self, sim, dt: float) -> None:
        for shot in self.shots:
            shot.ttl -= dt
        self.shots = [s for s in self.shots if s.ttl > 0.0]
        self._update_fields(sim, dt)
        for enemy in self.enemies:
            if enemy.hp > 0.0 and not sim.defeated:
                self._update_enemy(sim, enemy, dt)
        self._update_turrets(sim, dt)
        self._update_drone_gun(sim, dt)
        alive = [e for e in self.enemies if e.hp > 0.0]
        self.killed += len(self.enemies) - len(alive)
        self.enemies = alive

    # ---------------------------------------------------------- поле пути
    def _targets(self, sim) -> list[tuple[int, int, str, float]]:
        """(x, y, ключ приоритета, базовый приоритет) всех целей врагов."""
        out = [(b.x, b.y, b.kind, float(building_def(b.kind).enemy_priority))
               for b in sim.buildings.buildings]
        lamp_priority = float(building_def(LAMP).enemy_priority)
        out += [(lamp.x, lamp.y, "lamp", lamp_priority) for lamp in sim.light.lamps]
        dx, dy = sim.drone.tile()
        out.append((dx, dy, "drone", float(config.DRONE_TARGET_PRIORITY)))
        return out

    def _update_fields(self, sim, dt: float) -> None:
        """Пересчёт полей — только при изменении целей и не чаще интервала."""
        self._timer += dt
        key = (tuple((b.id, b.kind) for b in sim.buildings.buildings),
               tuple(lamp.id for lamp in sim.light.lamps), sim.drone.tile())
        if key == self._key:
            return
        if self._key is not None and self._timer < config.FLOW_RECALC_INTERVAL:
            return
        self._key = key
        self._timer = 0.0
        self.recompute(sim)

    def recompute(self, sim) -> None:
        targets = self._targets(sim)
        extra: dict[tuple[int, int], float] = {}
        for b in sim.buildings.buildings:
            extra[(b.x, b.y)] = config.FLOW_WALL_BASE + \
                building_def(b.kind).hp / config.FLOW_WALL_HP_DIV
        for lamp in sim.light.lamps:
            extra[(lamp.x, lamp.y)] = config.FLOW_WALL_BASE + \
                building_def(LAMP).hp / config.FLOW_WALL_HP_DIV
        passable = sim.light.reachable
        for kind, field in self.fields.items():
            etype = enemy_type(kind)
            if etype.flying:
                continue                 # летающие идут напрямую, без поля (plan/07 §6)
            sources = []
            for x, y, target_key, base in targets:
                mult = etype.priority_mult(target_key)
                if mult <= 0.0:
                    continue
                start = max(0.0, config.FLOW_BASE - base * mult * config.FLOW_PRIORITY_K)
                sources.append((x, y, start))
            field.compute(passable, extra, sources)

    # -------------------------------------------------------------- враг
    def _update_enemy(self, sim, enemy: Enemy, dt: float) -> None:
        etype = enemy.type
        enemy.cooldown = max(0.0, enemy.cooldown - dt)
        if etype.flying:
            self._update_flyer(sim, enemy, dt)
            return
        if etype.special == SPECIAL_ARTILLERY:
            self._update_artillery(sim, enemy, dt)
            return
        drone = sim.drone
        if etype.priority_mult("drone") > 0.0 and \
                math.hypot(drone.x - enemy.x, drone.y - enemy.y) <= etype.attack_range:
            self._attack(sim, enemy, drone.x, drone.y, lambda: sim.damage_drone(
                etype.damage, enemy))
            return
        self._walk(sim, enemy, dt)

    def _walk(self, sim, enemy: Enemy, dt: float) -> None:
        """Шаг по полю своего типа; упёрся в постройку — бьёт её (plan/05, п. 4)."""
        etype = enemy.type
        x, y = enemy.tile
        step = self.fields[enemy.kind].next_tile(x, y)
        if step is None:
            return
        occupant = sim.buildings.at(*step) or sim.light.lamp_at(*step)
        cx, cy = step[0] + 0.5, step[1] + 0.5
        if occupant is not None:
            if math.hypot(cx - enemy.x, cy - enemy.y) <= min(etype.attack_range, 1.0) + 0.5:
                self._attack(sim, enemy, cx, cy,
                             lambda: sim.damage_structure(occupant, etype.damage))
                return
        self._move(enemy, cx, cy, etype.speed * dt)

    def _targets_scored(self, sim, enemy: Enemy, radius: float | None = None):
        """Цели врага с оценкой «приоритет с поправкой на расстояние» (plan/05, п. 2).

        Меньше — лучше; та же мера, что у поля пути. `radius` — только в нём.
        """
        etype = enemy.type
        out = []
        lamp_priority = float(building_def(LAMP).enemy_priority)
        candidates = [(b, b.kind, float(building_def(b.kind).enemy_priority))
                      for b in sim.buildings.buildings]
        candidates += [(lamp, "lamp", lamp_priority) for lamp in sim.light.lamps]
        for obj, key, base in candidates:
            mult = etype.priority_mult(key)
            if mult <= 0.0:
                continue
            tx, ty = obj.x + 0.5, obj.y + 0.5
            d = math.hypot(tx - enemy.x, ty - enemy.y)
            if radius is not None and d > radius:
                continue
            out.append((config.FLOW_BASE - base * mult * config.FLOW_PRIORITY_K + d,
                        obj, tx, ty))
        mult = etype.priority_mult("drone")
        if mult > 0.0:
            d = math.hypot(sim.drone.x - enemy.x, sim.drone.y - enemy.y)
            if radius is None or d <= radius:
                out.append((config.FLOW_BASE - config.DRONE_TARGET_PRIORITY * mult
                            * config.FLOW_PRIORITY_K + d, None, sim.drone.x, sim.drone.y))
        return out

    def _update_flyer(self, sim, enemy: Enemy, dt: float) -> None:
        """Москит: летит прямо к лучшей цели, стены не замечает (plan/05)."""
        scored = self._targets_scored(sim, enemy)
        if not scored:
            return
        _score, obj, tx, ty = min(scored, key=lambda item: item[0])
        etype = enemy.type
        if math.hypot(tx - enemy.x, ty - enemy.y) <= etype.attack_range + 0.3:
            if obj is None:
                self._attack(sim, enemy, tx, ty,
                             lambda: sim.damage_drone(etype.damage, enemy))
            else:
                self._attack(sim, enemy, tx, ty,
                             lambda: sim.damage_structure(obj, etype.damage))
            return
        self._move(enemy, tx, ty, etype.speed * dt)

    def _update_artillery(self, sim, enemy: Enemy, dt: float) -> None:
        """Артиллерия: к позиции, разворот 🔧 `deploy_time`, затем редкий сильный залп."""
        etype = enemy.type
        siege = enemy.siege
        alive = siege is not None and (any(b is siege for b in sim.buildings.buildings)
                                       or any(lamp is siege for lamp in sim.light.lamps))
        if alive and math.hypot(siege.x + 0.5 - enemy.x,
                                siege.y + 0.5 - enemy.y) <= etype.attack_range:
            if enemy.deploy < etype.deploy_time:
                enemy.deploy += dt
                return
            tx, ty = siege.x + 0.5, siege.y + 0.5
            enemy.facing = math.atan2(ty - enemy.y, tx - enemy.x)
            if enemy.cooldown <= 0.0:
                enemy.cooldown = etype.attack_cooldown
                self.shots.append(Shot(enemy.x, enemy.y, tx, ty, SHOT_ARTILLERY,
                                       ARTILLERY_TRACER))
                sim.damage_structure(siege, etype.damage)
            return
        enemy.siege, enemy.deploy = None, 0.0
        in_range = [t for t in self._targets_scored(sim, enemy, etype.attack_range)
                    if t[1] is not None]
        if in_range:
            enemy.siege = min(in_range, key=lambda item: item[0])[1]
            return
        self._walk(sim, enemy, dt)

    @staticmethod
    def _move(enemy: Enemy, tx: float, ty: float, step: float) -> None:
        dx, dy = tx - enemy.x, ty - enemy.y
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        enemy.facing = math.atan2(dy, dx)
        k = min(1.0, step / length)
        enemy.x += dx * k
        enemy.y += dy * k

    def _attack(self, sim, enemy: Enemy, tx: float, ty: float, hit) -> None:
        enemy.facing = math.atan2(ty - enemy.y, tx - enemy.x)
        if enemy.cooldown > 0.0:
            return
        enemy.cooldown = enemy.type.attack_cooldown
        self.shots.append(Shot(enemy.x, enemy.y, tx, ty, SHOT_ENEMY))
        hit()

    # ------------------------------------------------------------ оборона
    def nearest(self, x: float, y: float, radius: float,
                ground_only: bool = False) -> Enemy | None:
        best, best_d = None, radius
        for enemy in self.enemies:
            if enemy.hp <= 0.0 or (ground_only and enemy.type.flying):
                continue
            d = math.hypot(enemy.x - x, enemy.y - y)
            if d <= best_d:
                best, best_d = enemy, d
        return best

    def _update_turrets(self, sim, dt: float) -> None:
        for building in sim.buildings.buildings:
            weapon = building_def(building.kind).weapon
            if weapon is None:
                continue
            building.weapon_cooldown = max(0.0, building.weapon_cooldown - dt)
            if building.weapon_cooldown > 0.0:
                continue
            cx, cy = building.x + 0.5, building.y + 0.5
            # Наземная турель по летающим не стреляет: против москитов — ПВО
            # (модули, M7), ремонт и оружие дрона (plan/05).
            target = self.nearest(cx, cy, weapon.range, ground_only=True)
            if target is None:
                continue
            if building.ammo_shots <= 0:
                ammo = building.container(building_def(building.kind).input_id)
                if ammo is None or not ammo.remove(AMMO_ITEM, 1):
                    continue                    # нет боеприпасов — молчит
                building.ammo_shots = weapon.shots_per_ammo
            building.ammo_shots -= 1
            building.weapon_cooldown = weapon.cooldown
            building.aim = math.atan2(target.y - cy, target.x - cx)
            target.hp -= weapon.damage
            self.shots.append(Shot(cx, cy, target.x, target.y, SHOT_TURRET))

    def _update_drone_gun(self, sim, dt: float) -> None:
        drone = sim.drone
        drone.gun_cooldown = max(0.0, drone.gun_cooldown - dt)
        if drone.gun_cooldown > 0.0:
            return
        target = self.nearest(drone.x, drone.y, config.DRONE_GUN_RANGE)
        if target is None:
            return
        drone.gun_cooldown = config.DRONE_GUN_COOLDOWN
        target.hp -= config.DRONE_GUN_DAMAGE
        self.shots.append(Shot(drone.x, drone.y, target.x, target.y, SHOT_DRONE))

    def state(self) -> tuple:
        return tuple((e.id, e.kind, round(e.x, 4), round(e.y, 4), round(e.hp, 3))
                     for e in self.enemies)
