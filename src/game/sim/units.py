"""Юниты симуляции. В M1 — только дрон игрока."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.game import config
from src.game.sim.world import World

#: Виды работы дрона руками. Таблица «вид → цвет луча» живёт в `render/theme.py`,
#: поэтому здесь только имена: рендер читает `drone.action.kind` и больше ничего.
ACTION_MINE = "mine"            # добыча залежи (ЛКМ зажата)
ACTION_DISMANTLE = "dismantle"  # разбор своей постройки (ЛКМ зажата)
ACTION_STUDY = "study"          # изучение реликвии (ЛКМ зажата)
ACTION_SCAN = "scan"            # скан свойств залежи (ПКМ зажата)


@dataclass(frozen=True)
class DroneAction:
    """Что дрон делает прямо сейчас — только для отрисовки (plan/09).

    Симуляция не принимает по этому полю решений: оно собирается из уже
    существующих состояний (`mine_target`, `study_target`, …) и ничего не
    меняет. `kind` — один из `ACTION_*` или None; `target_world_pos` — точка, в
    которую бьёт луч, в мировых координатах (центр залежи, реликвии, здания);
    `progress` — 0..1, по нему рендер усиливает свечение.
    """

    kind: str | None = None
    target_world_pos: tuple[float, float] | None = None
    progress: float = 0.0

    @property
    def active(self) -> bool:
        return self.kind is not None and self.target_world_pos is not None


@dataclass
class Drone:
    """Дрон игрока: ходит, добывает. Смерть дрона = поражение (M5)."""

    x: float
    y: float
    hp: int = config.DRONE_HP
    radius: float = config.DRONE_RADIUS
    speed: float = config.DRONE_SPEED
    facing: float = 0.0  # радианы, для отрисовки
    alive: bool = True
    #: Текущее действие для рендера (луч лазера). Ставит симуляция каждый шаг.
    action: DroneAction = field(default_factory=DroneAction)
    #: Перезарядка слабого оружия дрона (M5), с.
    gun_cooldown: float = 0.0

    @classmethod
    def spawn(cls, world: World) -> "Drone":
        x, y = world.spawn_point()
        return cls(x=x, y=y)

    def move(self, world: World, input_x: float, input_y: float, dt: float) -> float:
        """Двигает дрон по осям раздельно, чтобы он скользил вдоль стен.

        Возвращает пройденное расстояние в тайлах.
        """
        if input_x == 0.0 and input_y == 0.0:
            return 0.0
        length = math.hypot(input_x, input_y)
        dx = input_x / length
        dy = input_y / length
        self.facing = math.atan2(dy, dx)
        step = self.speed * max(dt, 0.0)
        moved = 0.0
        if dx:
            nx = self.x + dx * step
            if not world.circle_hits_blocked(nx, self.y, self.radius):
                self.x = nx
                moved += abs(dx * step)
        if dy:
            ny = self.y + dy * step
            if not world.circle_hits_blocked(self.x, ny, self.radius):
                self.y = ny
                moved += abs(dy * step)
        return moved

    def tile(self) -> tuple[int, int]:
        return int(math.floor(self.x)), int(math.floor(self.y))

    def distance_to_tile(self, tx: int, ty: int) -> float:
        """Расстояние от центра дрона до центра тайла."""
        return math.hypot(self.x - (tx + 0.5), self.y - (ty + 0.5))
