"""Реликвии (plan/02). Изучение открывает рецепт роли.

Базовые реликвии открывают базовый рецепт роли, усилители — более выгодный
вариант той же роли. Значок не рандомизируется: реликвия должна узнаваться
на карте с первого взгляда, в отличие от ресурсов.
"""

from __future__ import annotations

from dataclasses import dataclass

BASE = "base"
AMPLIFIER = "amplifier"


@dataclass(frozen=True)
class RelicDef:
    kind: str
    name: str
    shape: str
    color: tuple[int, int, int]
    #: Подпись на карте — по ней видно, что это ещё не изучено.
    mark: str


RELIC_TYPES: dict[str, RelicDef] = {
    BASE: RelicDef(BASE, "Реликвия", "hexagon", (150, 200, 255), "?"),
    AMPLIFIER: RelicDef(AMPLIFIER, "Реликвия-усилитель", "hexagon", (255, 196, 120), "+"),
}


def relic_def(amplified: bool) -> RelicDef:
    return RELIC_TYPES[AMPLIFIER if amplified else BASE]
