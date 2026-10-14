"""Роботы, приказы и мастерская (M7, plan/04). Не импортирует pygame.

Управление полностью ручное: робот делает то, что ему приказали, а сам —
только самое очевидное для своей навески (боец стреляет в радиусе,
снабженец раздаёт боеприпасы соседям, курьер без приказа возит боеприпасы
со склада туда, где их не хватает). Приказы стоят в очереди: Shift
добавляет приказ в конец, без Shift — заменяет очередь.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from src.game import config
from src.game.data.buildings import TURRET, WORK_ROBOT, building as building_def
from src.game.data.robots import (CHASSIS, DESIGN_ORDER, ROLE_COURIER, ammo_capacity,
                                  carry_of, design, max_hp, transfers, weapon_of)
from src.game.sim.enemies import AMMO_ITEM, Shot

ORDER_GUARD = "guard"        # охранять точку
ORDER_PATROL = "patrol"      # ходить между двумя точками
ORDER_FOLLOW = "follow"      # идти за дроном
ORDER_ATTACK = "attack"      # атаковать врага

SHOT_ROBOT = "robot"


@dataclass(frozen=True)
class Order:
    kind: str
    point: tuple[float, float] | None = None
    #: Патруль: вторая точка (первая — `point`).
    other: tuple[float, float] | None = None
    target_id: int | None = None


@dataclass
class Robot:
    id: int
    design: str
    x: float
    y: float
    hp: float
    orders: deque = field(default_factory=deque)
    #: Боеприпасы в хранилище (единицы предмета) и выстрелы в «магазине».
    ammo: int = 0
    shots: int = 0
    cooldown: float = 0.0
    supply_timer: float = 0.0
    facing: float = 0.0
    #: Курьер: сколько везёт сейчас.
    cargo: int = 0
    #: Патруль: к какой из двух точек идёт.
    leg: int = 0
    group: int | None = None

    @property
    def max_hp(self) -> float:
        return max_hp(self.design)

    @property
    def order(self) -> Order | None:
        return self.orders[0] if self.orders else None


class RobotSystem:
    """Все роботы и сборка их в мастерских."""

    def __init__(self) -> None:
        self.robots: list[Robot] = []
        self._next_id = 0
        self.built = 0
        self.lost = 0

    # ------------------------------------------------------------- запросы
    def get(self, robot_id: int) -> Robot | None:
        return next((r for r in self.robots if r.id == robot_id), None)

    def at(self, x: float, y: float, radius: float = config.ROBOT_PICK_RADIUS) -> Robot | None:
        best, best_d = None, radius
        for robot in self.robots:
            d = math.hypot(robot.x - x, robot.y - y)
            if d <= best_d:
                best, best_d = robot, d
        return best

    def spawn(self, design_id: str, x: float, y: float) -> Robot:
        robot = Robot(self._next_id, design_id, x, y, max_hp(design_id))
        robot.orders.append(Order(ORDER_GUARD, (x, y)))
        self._next_id += 1
        self.robots.append(robot)
        return robot

    # ------------------------------------------------------------- приказы
    def order(self, robot_ids, order: Order, queue: bool = False) -> int:
        """Отдаёт приказ роботам. Shift (`queue`) — в конец очереди."""
        n = 0
        for robot in self.robots:
            if robot.id not in robot_ids:
                continue
            given = order
            if order.kind == ORDER_PATROL and order.other is None:
                # Патруль — между тем местом, где робот сейчас, и точкой приказа.
                given = Order(ORDER_PATROL, order.point, (robot.x, robot.y))
            if not queue:
                robot.orders.clear()
                robot.leg = 0
            robot.orders.append(given)
            n += 1
        return n

    # ---------------------------------------------------------------- шаг
    def update(self, sim, dt: float) -> None:
        self._update_workshops(sim, dt)
        for robot in self.robots:
            if robot.hp > 0.0:
                self._update_robot(sim, robot, dt)
        alive = [r for r in self.robots if r.hp > 0.0]
        self.lost += len(self.robots) - len(alive)
        self.robots = alive

    def _update_robot(self, sim, robot: Robot, dt: float) -> None:
        robot.cooldown = max(0.0, robot.cooldown - dt)
        order = robot.order
        target_enemy = None
        if order is not None and order.kind == ORDER_ATTACK:
            target_enemy = next((e for e in sim.enemies.enemies
                                 if e.id == order.target_id and e.hp > 0.0), None)
            if target_enemy is None:
                robot.orders.popleft()            # цель погибла — следующий приказ
                order = robot.order
        weapon = weapon_of(robot.design)
        if target_enemy is not None:
            d = math.hypot(target_enemy.x - robot.x, target_enemy.y - robot.y)
            if weapon is None or d > weapon.range:
                self._move(sim, robot, target_enemy.x, target_enemy.y, dt)
            else:
                self._shoot(sim, robot, target_enemy, weapon)
        else:
            self._follow_order(sim, robot, order, dt)
            if weapon is not None:
                enemy = sim.enemies.nearest(robot.x, robot.y, weapon.range,
                                            ground_only=True)
                if enemy is not None:
                    self._shoot(sim, robot, enemy, weapon)
        if transfers(robot.design):
            self._supply(sim, robot, dt)

    def _follow_order(self, sim, robot: Robot, order: Order | None, dt: float) -> None:
        if order is None:
            if robot.design == ROLE_COURIER:
                self._courier(sim, robot, dt)
            return
        if order.kind == ORDER_FOLLOW:
            if math.hypot(sim.drone.x - robot.x, sim.drone.y - robot.y) > config.ROBOT_FOLLOW_DIST:
                self._move(sim, robot, sim.drone.x, sim.drone.y, dt)
            return
        if order.kind == ORDER_PATROL:
            point = order.point if robot.leg == 0 else order.other
            if self._move(sim, robot, point[0], point[1], dt):
                robot.leg = 1 - robot.leg
            return
        # Охрана: дойти до точки и стоять. Если в очереди есть ещё приказы —
        # дошёл, значит, этот выполнен.
        if self._move(sim, robot, order.point[0], order.point[1], dt) and len(robot.orders) > 1:
            robot.orders.popleft()

    # ------------------------------------------------------------ движение
    def _move(self, sim, robot: Robot, tx: float, ty: float, dt: float) -> bool:
        """Шаг к точке; горы и вода не пускают (как дрона). True — уже на месте."""
        dx, dy = tx - robot.x, ty - robot.y
        dist = math.hypot(dx, dy)
        if dist <= config.ROBOT_ARRIVE:
            return True
        chassis = CHASSIS[design(robot.design).chassis]
        step = min(dist, chassis.speed * dt)
        ux, uy = dx / dist, dy / dist
        robot.facing = math.atan2(uy, ux)
        world = sim.world
        if chassis.flying:
            robot.x += ux * step
            robot.y += uy * step
            return False
        nx = robot.x + ux * step
        if not world.circle_hits_blocked(nx, robot.y, config.ROBOT_RADIUS):
            robot.x = nx
        ny = robot.y + uy * step
        if not world.circle_hits_blocked(robot.x, ny, config.ROBOT_RADIUS):
            robot.y = ny
        return False

    # -------------------------------------------------------------- бой
    def _shoot(self, sim, robot: Robot, enemy, weapon) -> None:
        robot.facing = math.atan2(enemy.y - robot.y, enemy.x - robot.x)
        if robot.cooldown > 0.0:
            return
        if robot.shots <= 0:
            if robot.ammo <= 0:
                return                            # нечем стрелять — ждёт снабженца
            robot.ammo -= 1
            robot.shots = weapon.shots_per_ammo
        robot.shots -= 1
        robot.cooldown = weapon.cooldown
        enemy.hp -= weapon.damage
        sim.enemies.shots.append(Shot(robot.x, robot.y, enemy.x, enemy.y, SHOT_ROBOT))

    # --------------------------------------------------------- логистика
    def _supply(self, sim, robot: Robot, dt: float) -> None:
        """Снабженец: берёт боеприпасы со склада рядом и раздаёт соседям (plan/04)."""
        robot.supply_timer -= dt
        if robot.supply_timer > 0.0:
            return
        robot.supply_timer = config.SUPPLY_INTERVAL
        cap = ammo_capacity(robot.design)
        if robot.ammo < cap:
            robot.ammo += self._take_ammo(sim, robot.x, robot.y, config.SUPPLY_RANGE,
                                          cap - robot.ammo)
        if robot.ammo <= 0:
            return
        for other in self.robots:
            if other is robot or weapon_of(other.design) is None:
                continue
            if math.hypot(other.x - robot.x, other.y - robot.y) > config.SUPPLY_RANGE:
                continue
            if other.ammo < ammo_capacity(other.design):
                other.ammo += 1
                robot.ammo -= 1
                sim.enemies.shots.append(Shot(robot.x, robot.y, other.x, other.y, "supply"))
                return
        turret = self._hungry_turret(sim, robot.x, robot.y, config.SUPPLY_RANGE)
        if turret is not None:
            turret.container(building_def(TURRET).input_id).add(AMMO_ITEM, 1)
            robot.ammo -= 1
            sim.enemies.shots.append(Shot(robot.x, robot.y, turret.x + 0.5,
                                          turret.y + 0.5, "supply"))

    def _courier(self, sim, robot: Robot, dt: float) -> None:
        """Курьер без приказа: склад → тот, кому не хватает боеприпасов."""
        cap = carry_of(robot.design)
        if robot.cargo <= 0:
            source = self._ammo_source(sim, robot.x, robot.y)
            if source is None:
                return
            if self._move(sim, robot, source.x + 0.5, source.y + 0.5, dt) or \
                    math.hypot(source.x + 0.5 - robot.x, source.y + 0.5 - robot.y) < 1.3:
                robot.cargo += self._take_ammo(sim, robot.x, robot.y, 1.5, cap)
            return
        consumer = self._needy(sim, robot)
        if consumer is None:
            return
        cx, cy = consumer
        if math.hypot(cx - robot.x, cy - robot.y) <= config.SUPPLY_RANGE:
            self._deliver(sim, robot, cx, cy)
        else:
            self._move(sim, robot, cx, cy, dt)

    def _deliver(self, sim, robot: Robot, x: float, y: float) -> None:
        for other in self.robots:
            if other is not robot and math.hypot(other.x - x, other.y - y) < 0.1:
                give = min(robot.cargo, ammo_capacity(other.design) - other.ammo)
                other.ammo += give
                robot.cargo -= give
                return
        turret = sim.buildings.at(int(x), int(y))
        if turret is not None and turret.kind == TURRET:
            container = turret.container(building_def(TURRET).input_id)
            give = max(0, min(robot.cargo, config.TURRET_AMMO_WANTED - container.count(AMMO_ITEM)))
            robot.cargo -= give - container.add(AMMO_ITEM, give)

    def _needy(self, sim, robot: Robot) -> tuple[float, float] | None:
        best, best_d = None, math.inf
        for other in self.robots:
            if other is robot or ammo_capacity(other.design) <= other.ammo:
                continue
            if weapon_of(other.design) is None and not transfers(other.design):
                continue
            d = math.hypot(other.x - robot.x, other.y - robot.y)
            if d < best_d:
                best, best_d = (other.x, other.y), d
        turret = self._hungry_turret(sim, robot.x, robot.y, math.inf)
        if turret is not None:
            d = math.hypot(turret.x + 0.5 - robot.x, turret.y + 0.5 - robot.y)
            if d < best_d:
                best = (turret.x + 0.5, turret.y + 0.5)
        return best

    @staticmethod
    def _hungry_turret(sim, x: float, y: float, radius: float):
        best, best_d = None, radius
        for b in sim.buildings.of_kind(TURRET):
            if b.container(building_def(TURRET).input_id).count(AMMO_ITEM) \
                    >= config.TURRET_AMMO_WANTED:
                continue
            d = math.hypot(b.x + 0.5 - x, b.y + 0.5 - y)
            if d <= best_d:
                best, best_d = b, d
        return best

    @staticmethod
    def _ammo_source(sim, x: float, y: float):
        """Ближайшее здание (не турель) с боеприпасами в контейнерах — склад и т.п."""
        best, best_d = None, math.inf
        for b in sim.buildings.buildings:
            if b.kind == TURRET or building_def(b.kind).work == WORK_ROBOT:
                continue
            if not any(c.count(AMMO_ITEM) for c in b.containers.values()):
                continue
            d = math.hypot(b.x + 0.5 - x, b.y + 0.5 - y)
            if d < best_d:
                best, best_d = b, d
        return best

    @staticmethod
    def _take_ammo(sim, x: float, y: float, radius: float, want: int) -> int:
        """Забирает до `want` боеприпасов из зданий (кроме турелей) в радиусе."""
        got = 0
        for b in sim.buildings.buildings:
            if got >= want:
                break
            if b.kind == TURRET or building_def(b.kind).work == WORK_ROBOT:
                continue
            if math.hypot(b.x + 0.5 - x, b.y + 0.5 - y) > radius:
                continue
            for container in b.containers.values():
                got += container.take(AMMO_ITEM, want - got)
                if got >= want:
                    break
        return got

    # ---------------------------------------------------------- мастерская
    def _update_workshops(self, sim, dt: float) -> None:
        """Мастерская собирает выбранного робота, пока хватает материалов во входе."""
        for b in sim.buildings.buildings:
            definition = building_def(b.kind)
            if definition.work != WORK_ROBOT or b.recipe is None:
                continue
            if definition.needs_power and not b.powered:
                continue
            if len(self.robots) >= config.ROBOT_CAP:
                continue
            container = b.container(definition.input_id)
            spec = design(b.recipe)
            if not all(container.has(item, n) for item, n in spec.cost):
                b.work_progress = 0.0
                continue
            b.work_progress += dt / spec.build_time
            if b.work_progress < 1.0:
                continue
            b.work_progress = 0.0
            ammo = 0
            for item, n in spec.cost:
                container.remove(item, n)
                if item == AMMO_ITEM:
                    ammo += n
            robot = self.spawn(spec.id, b.x + 0.5, b.y + 1.5)
            robot.ammo = min(ammo, ammo_capacity(spec.id))
            b.made += 1
            self.built += 1
            sim.toast(f"Мастерская: готов {spec.name.lower()}")

    @staticmethod
    def cycle_design(building) -> str:
        """Кнопка в окне мастерской: следующий робот по кругу."""
        order = DESIGN_ORDER
        index = order.index(building.recipe) + 1 if building.recipe in order else 0
        building.recipe = order[index % len(order)]
        building.work_progress = 0.0
        return building.recipe

    def state(self) -> tuple:
        return tuple((r.id, r.design, round(r.x, 4), round(r.y, 4), round(r.hp, 3),
                      r.ammo, len(r.orders)) for r in self.robots)
