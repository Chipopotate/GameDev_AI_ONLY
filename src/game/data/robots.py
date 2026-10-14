"""Роботы урезанного M7 (plan/04): данными, а не классом на каждый тип.

Пока есть одна роль — **боец** на наземном корпусе с быстрым оружием: модулей,
приказов и групп в этом билде нет. Робот — предмет, как постройка: крафтится
обычным рецептом «свойство × количество» (plan/02) у запитанной мастерской,
кладётся в хотбар и ставится из него на землю.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.game import config
from src.game.data.recipes import Recipe
from src.game.data.resources import ItemDef, Prop

FIGHTER = "robot_fighter"


@dataclass(frozen=True)
class RobotType:
    id: str                    # он же id предмета и рецепта
    name: str
    chassis: str               # силуэт корпуса (render/sprites/units.py)
    modules: tuple[str, ...]   # модули для отрисовки
    hp: float
    speed: float               # тайлов в секунду
    damage: float              # за выстрел
    attack_range: float        # тайлы
    attack_cooldown: float     # с
    recipe: Recipe
    #: Реликвия какой роли открывает рецепт — вместе с мастерской.
    unlock_role: str
    color: tuple[int, int, int] = (120, 200, 240)


# 🔧 Цена: корпус — 🪨, электроника — ⚡, оружие — 💥 (plan/04: модуль требует
# своё свойство). Дороже любой постройки: робот — вложение, а не расходник.
ROBOT_TYPES: dict[str, RobotType] = {
    FIGHTER: RobotType(
        FIGHTER, "Робот-боец", chassis="ground", modules=("weapon_fast",),
        hp=60, speed=4.2, damage=6, attack_range=4.5, attack_cooldown=0.5,
        recipe=Recipe(id=FIGHTER, role=FIGHTER,
                      requires=((Prop.TOUGH, 3), (Prop.CONDUCTIVE, 2), (Prop.UNSTABLE, 2)),
                      outputs=1, craft_time=4.0),
        unlock_role="automation",
    ),
}

ROBOT_RECIPES: tuple[Recipe, ...] = tuple(t.recipe for t in ROBOT_TYPES.values())


def robot_item(kind: str) -> ItemDef:
    t = ROBOT_TYPES[kind]
    return ItemDef(id=t.id, name=t.name, props=frozenset(), color=t.color,
                   shape="hexagon", crafted=True, robot=t.id,
                   stack_max=config.BUILDING_STACK)


ROBOT_ITEMS: tuple[ItemDef, ...] = tuple(robot_item(k) for k in ROBOT_TYPES)


def robot(kind: str) -> RobotType:
    return ROBOT_TYPES[kind]


def unlocked_robots(role_id: str) -> tuple[str, ...]:
    return tuple(k for k, t in ROBOT_TYPES.items() if t.unlock_role == role_id)
