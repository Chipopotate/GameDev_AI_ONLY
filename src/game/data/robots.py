"""Корпуса, модули и роботы (plan/04). Логика — `sim/robots.py`.

Единая модульность: корпус говорит, где и как стоит модуль, модуль — что он
делает. Робот — это корпус плюс набор модулей; его роль (боец, танк,
снабженец, курьер) — просто удачная навеска, а не отдельный класс.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.game import config
from src.game.data.buildings import WeaponDef

CHASSIS_STATIC = "static"
CHASSIS_GROUND = "ground"
CHASSIS_FLYING = "flying"

ROLE_FIGHTER = "fighter"
ROLE_TANK = "tank"
ROLE_SUPPLIER = "supplier"
ROLE_COURIER = "courier"


@dataclass(frozen=True)
class Chassis:
    id: str
    name: str
    slots: int
    movable: bool
    flying: bool
    hp: float
    speed: float = 0.0          # тайлов в секунду


@dataclass(frozen=True)
class Module:
    """Модуль. Работает на любом корпусе (plan/04); поля — что он даёт."""

    id: str
    name: str
    slot_size: int = 1
    weapon: WeaponDef | None = None
    armor: float = 0.0          # прибавка к прочности
    ammo_capacity: int = 0      # сколько «боеприпасов» хранит
    transfer: bool = False      # передаёт боеприпасы соседям
    carry: int = 0              # курьер: сколько везёт за ходку


@dataclass(frozen=True)
class RobotDesign:
    """Робот = корпус + модули; цена — в предметах ролей (собирает мастерская)."""

    id: str
    name: str
    chassis: str
    modules: tuple[str, ...]
    cost: tuple[tuple[str, int], ...]
    build_time: float


CHASSIS: dict[str, Chassis] = {
    CHASSIS_STATIC: Chassis(CHASSIS_STATIC, "Стационарный", 6, False, False, 100.0),
    CHASSIS_GROUND: Chassis(CHASSIS_GROUND, "Наземный", 3, True, False, 50.0, 2.6),
    CHASSIS_FLYING: Chassis(CHASSIS_FLYING, "Летающий", 2, True, True, 30.0, 4.0),
}

# 🔧 числа — догадки «чтобы работало», балансировка — после M7.
MODULES: dict[str, Module] = {
    "weapon_fast": Module("weapon_fast", "Быстрое оружие", weapon=WeaponDef(
        "weapon_fast", range=4.5, damage=7.0, cooldown=0.5, shots_per_ammo=4)),
    "armor": Module("armor", "Броня", armor=60.0),
    "tank": Module("tank", "Хранилище боеприпасов", ammo_capacity=6),
    "courier": Module("courier", "Грузовой отсек", carry=4),
    "transfer": Module("transfer", "Передача боеприпасов", transfer=True),
}

DESIGNS: dict[str, RobotDesign] = {
    ROLE_FIGHTER: RobotDesign(ROLE_FIGHTER, "Боец", CHASSIS_GROUND,
                              ("weapon_fast", "tank"),
                              (("structure", 2), ("automation", 2), ("ammo", 2)), 6.0),
    ROLE_TANK: RobotDesign(ROLE_TANK, "Танк", CHASSIS_GROUND, ("armor", "armor"),
                           (("structure", 4), ("automation", 1)), 6.0),
    ROLE_SUPPLIER: RobotDesign(ROLE_SUPPLIER, "Снабженец", CHASSIS_GROUND,
                               ("tank", "transfer"),
                               (("structure", 2), ("automation", 2), ("ammo", 4)), 6.0),
    ROLE_COURIER: RobotDesign(ROLE_COURIER, "Курьер", CHASSIS_GROUND, ("courier",),
                              (("structure", 1), ("automation", 2)), 5.0),
}

DESIGN_ORDER = (ROLE_FIGHTER, ROLE_TANK, ROLE_SUPPLIER, ROLE_COURIER)


def design(design_id: str) -> RobotDesign:
    return DESIGNS[design_id]


def modules_of(design_id: str) -> tuple[Module, ...]:
    return tuple(MODULES[m] for m in DESIGNS[design_id].modules)


def weapon_of(design_id: str) -> WeaponDef | None:
    return next((m.weapon for m in modules_of(design_id) if m.weapon), None)


def max_hp(design_id: str) -> float:
    d = DESIGNS[design_id]
    return CHASSIS[d.chassis].hp + sum(m.armor for m in modules_of(design_id))


def ammo_capacity(design_id: str) -> int:
    return sum(m.ammo_capacity for m in modules_of(design_id))


def carry_of(design_id: str) -> int:
    return sum(m.carry for m in modules_of(design_id))


def transfers(design_id: str) -> bool:
    return any(m.transfer for m in modules_of(design_id))


# Проверка данных при импорте: модули влезают в корпус (plan/04, слоты корпуса).
for _d in DESIGNS.values():
    assert sum(MODULES[m].slot_size for m in _d.modules) <= CHASSIS[_d.chassis].slots, _d.id
assert config.ROBOT_CAP > 0
