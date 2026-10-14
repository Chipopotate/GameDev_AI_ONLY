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
MOSQUITO = "mosquito"
ARTILLERY = "artillery"

#: Особое поведение (`EnemyType.special`): разворот и дальний обстрел.
SPECIAL_ARTILLERY = "artillery"

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
    #: Артиллерия: сколько секунд «разворачивается» на позиции перед стрельбой.
    deploy_time: float = 0.0

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
    # Москит (M6): летит напрямую, стены и наземная оборона ему не помеха;
    # цели — конвейеры и провода ×10, остальное игнорирует (дрон — запасная).
    MOSQUITO: EnemyType(
        MOSQUITO, "Москит", 1, hp=12.0, speed=3.4, damage=5.0, attack_range=0.7,
        attack_cooldown=0.7, flying=True, ignores_walls=True,
        priorities={"conveyor": 10.0, "wire": 10.0, "drone": 0.5, "default": 0.0},
        shape="mosquito", size=0.6,
    ),
    # Артиллерия (M6): идёт к позиции, разворачивается, затем бьёт редко и
    # сильно издалека — дальше, чем достаёт турель. Цели — дорогие постройки.
    ARTILLERY: EnemyType(
        ARTILLERY, "Артиллерия", 1, hp=140.0, speed=0.9, damage=45.0,
        attack_range=9.0, attack_cooldown=4.0,
        priorities={"generator": 2.0, "assembler": 2.0, "lab": 2.0, "turret": 1.5,
                    "storage": 1.5, "wall": 0.2, "drone": 0.3, "default": 1.0},
        shape="artillery", size=1.1, special=SPECIAL_ARTILLERY, deploy_time=3.0,
    ),
}


def enemy_type(enemy_id: str) -> EnemyType:
    return ENEMY_TYPES[enemy_id]
