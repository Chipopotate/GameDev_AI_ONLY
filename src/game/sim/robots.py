"""Роботы урезанного M7 (plan/04). Не импортирует pygame.

Приказов, групп и модулей нет: робот-боец держится рядом с дроном («идти за
дроном» — единственный приказ этого билда) и сам стреляет по ближайшему врагу
в радиусе. Враги бьют роботов в ответ; погибший робот потерян
(❓ plan/04 → 🔧 `ROBOTS_EXPENDABLE = True`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.game import config
from src.game.data.robots import ROBOT_TYPES, RobotType
from src.game.sim.enemies import EnemySystem
from src.game.sim.world import World


@dataclass
class Robot:
    id: int
    kind: str
    x: float
    y: float
    hp: float
    cooldown: float = 0.0
    facing: float = 0.0
    #: Куда был последний выстрел и сколько ещё виден его след (для рендера).
    shot: tuple[float, float] | None = None
    shot_time: float = 0.0

    @property
    def type(self) -> RobotType:
        return ROBOT_TYPES[self.kind]

    @property
    def max_hp(self) -> float:
        return self.type.hp


class RobotSystem:
    def __init__(self, world: World) -> None:
        self.world = world
        self.robots: list[Robot] = []
        self._next_id = 0

    def spawn(self, kind: str, x: float, y: float) -> Robot | None:
        if len(self.robots) >= config.ROBOT_MAX:
            return None
        robot = Robot(self._next_id, kind, x, y, ROBOT_TYPES[kind].hp)
        self._next_id += 1
        self.robots.append(robot)
        return robot

    def damage(self, robot: Robot, amount: float) -> bool:
        """Урон роботу. True — робот погиб и убран с карты."""
        robot.hp -= amount
        if robot.hp > 0 or not config.ROBOTS_EXPENDABLE:
            robot.hp = max(robot.hp, 1.0)
            return False
        if robot in self.robots:
            self.robots.remove(robot)
        return True

    def update(self, dt: float, drone_pos: tuple[float, float],
               enemies: EnemySystem) -> None:
        for i, robot in enumerate(self.robots):
            t = robot.type
            robot.cooldown = max(0.0, robot.cooldown - dt)
            robot.shot_time = max(0.0, robot.shot_time - dt)
            target = enemies.at(robot.x, robot.y, t.attack_range)
            if target is not None:
                robot.facing = math.atan2(target.y - robot.y, target.x - robot.x)
                if robot.cooldown <= 0.0:
                    robot.cooldown = t.attack_cooldown
                    robot.shot = (target.x, target.y)
                    robot.shot_time = config.ROBOT_SHOT_FLASH
                    target.hp -= t.damage
                    if target.hp <= 0:
                        enemies.remove(target)
                continue
            # Нет цели — держимся у дрона, каждый на своём месте по кругу,
            # чтобы отряд не слипался в одну точку.
            a = math.tau * i / max(1, len(self.robots))
            gx = drone_pos[0] + math.cos(a) * config.ROBOT_FOLLOW_DIST
            gy = drone_pos[1] + math.sin(a) * config.ROBOT_FOLLOW_DIST
            self._step(robot, gx, gy, t.speed * dt)

    def _step(self, robot: Robot, gx: float, gy: float, step: float) -> None:
        dx, dy = gx - robot.x, gy - robot.y
        dist = math.hypot(dx, dy)
        if dist < 0.2:
            return
        robot.facing = math.atan2(dy, dx)
        k = min(1.0, step / dist)
        nx, ny = robot.x + dx * k, robot.y + dy * k
        r = config.ROBOT_RADIUS
        if not self.world.circle_hits_blocked(nx, robot.y, r):
            robot.x = nx
        if not self.world.circle_hits_blocked(robot.x, ny, r):
            robot.y = ny

    def state(self) -> tuple:
        return tuple((r.kind, round(r.x, 4), round(r.y, 4), round(r.hp, 3))
                     for r in self.robots)
