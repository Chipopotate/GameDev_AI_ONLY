"""Типы врагов (plan/05, поля данных — § «Поля данных врага»).

Один класс `Enemy` в `sim/enemies.py` на всех: тип описан здесь строкой
таблицы. `priorities` — множители поверх базового приоритета здания
(`BuildingDef.enemy_priority`); ключ — вид здания, «lamp», «drone» или
«default» (для всего остального).
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHADOW = "shadow"
WALKER = "walker"

SPAWN_DARK = "dark_tile"


@dataclass(frozen=True)
class EnemyType:
    id: str
    name: str
    tier: int
    hp: float
    speed: float            # тайлов в секунду
    damage: float
    attack_range: float     # тайлы
    attack_cooldown: float  # с
    flying: bool = False
    ignores_walls: bool = False
    priorities: dict[str, float] = field(default_factory=dict)
    spawn_source: str = SPAWN_DARK
    #: Вид для рендера: форма из `render/sprites/organic.py`, размер в тайлах.
    shape: str = "crawler"
    size: float = 0.8
    special: str | None = None

    def priority_mult(self, key: str) -> float:
        return self.priorities.get(key, self.priorities.get("default", 1.0))


# 🔧 числа — догадки «чтобы работало», балансировка ночей — после M5.
ENEMY_TYPES: dict[str, EnemyType] = {
    # Тень: масса и фон, медленная и слабая, тянется к дрону (plan/05).
    SHADOW: EnemyType(
        SHADOW, "Тень", 1, hp=20.0, speed=1.2, damage=4.0, attack_range=0.9,
        attack_cooldown=1.0, priorities={"drone": 2.0, "default": 1.0},
        shape="shadow", size=0.7,
    ),
    # Ходок: основная масса волн; турели ×3, буры/сборщики ×2, стены ×0.5.
    WALKER: EnemyType(
        WALKER, "Ходок", 1, hp=45.0, speed=1.6, damage=8.0, attack_range=0.9,
        attack_cooldown=0.9,
        priorities={"turret": 3.0, "drill": 2.0, "assembler": 2.0, "wall": 0.5,
                    "drone": 1.0, "default": 1.0},
        shape="crawler", size=0.85,
    ),
}


def enemy_type(enemy_id: str) -> EnemyType:
    return ENEMY_TYPES[enemy_id]
