"""Враги и инкубаторы — данными, а не классами (plan/05, plan/06).

Урезанный M8: из полей `plan/05` заведены только те, что использует простая
логика этого билда (здоровье, скорость, урон, дальность, полёт, приоритеты).
Тень приходит из тьмы, Ходок, Москит и Артиллерия — **только** из инкубаторов.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHADOW = "shadow"
WALKER = "walker"
MOSQUITO = "mosquito"
ARTILLERY = "artillery"

SOURCE_DARK = "dark_tile"
SOURCE_INCUBATOR = "incubator"


@dataclass(frozen=True)
class EnemyType:
    id: str
    name: str
    hp: float
    speed: float               # тайлов в секунду
    damage: float              # за один удар
    attack_range: float        # тайлы
    attack_cooldown: float     # с
    flying: bool
    spawn_source: str
    #: Множители целей поверх `enemy_priority` здания (plan/05).
    priorities: dict[str, float] = field(default_factory=dict)
    sprite: str = "shadow"     # силуэт из render/sprites/organic.py
    size: float = 0.8          # доля тайла


ENEMY_TYPES: dict[str, EnemyType] = {
    SHADOW: EnemyType(SHADOW, "Тень", hp=12, speed=1.2, damage=2, attack_range=1.0,
                      attack_cooldown=1.2, flying=False, spawn_source=SOURCE_DARK,
                      sprite="shadow", size=0.6),
    WALKER: EnemyType(WALKER, "Ходок", hp=30, speed=1.6, damage=5, attack_range=1.1,
                      attack_cooldown=1.0, flying=False, spawn_source=SOURCE_INCUBATOR,
                      priorities={"drill": 2.0, "assembler": 2.0},
                      sprite="crawler", size=0.8),
    MOSQUITO: EnemyType(MOSQUITO, "Москит", hp=10, speed=3.2, damage=3, attack_range=0.8,
                        attack_cooldown=0.8, flying=True, spawn_source=SOURCE_INCUBATOR,
                        priorities={"conveyor": 10.0, "wire": 10.0},
                        sprite="mosquito", size=0.55),
    ARTILLERY: EnemyType(ARTILLERY, "Артиллерия", hp=60, speed=0.8, damage=25,
                         attack_range=7.0, attack_cooldown=6.0, flying=False,
                         spawn_source=SOURCE_INCUBATOR, sprite="artillery", size=1.0),
}


@dataclass(frozen=True)
class IncubatorType:
    """Инкубатор: кого выводит, сколько за ночь и с какого дня появляется."""

    id: str
    enemy: str
    hp: float
    spawn_base: int            # врагов за ночь на уровне 1
    spawn_per_level: int       # прибавка за каждый уровень улучшения
    min_day: int               # с какого дня росток может его построить (plan/05)


INCUBATOR_TYPES: dict[str, IncubatorType] = {
    WALKER: IncubatorType("inc_walker", WALKER, hp=150, spawn_base=2,
                          spawn_per_level=1, min_day=1),
    MOSQUITO: IncubatorType("inc_mosquito", MOSQUITO, hp=120, spawn_base=3,
                            spawn_per_level=1, min_day=3),
    ARTILLERY: IncubatorType("inc_artillery", ARTILLERY, hp=200, spawn_base=1,
                             spawn_per_level=1, min_day=5),
}


def enemy(enemy_id: str) -> EnemyType:
    return ENEMY_TYPES[enemy_id]


def incubators_for_day(day: int) -> tuple[str, ...]:
    """Типы инкубаторов, доступные ростку в этот день (ключи `INCUBATOR_TYPES`)."""
    return tuple(k for k, t in INCUBATOR_TYPES.items() if t.min_day <= day)
