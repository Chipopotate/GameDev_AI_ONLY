"""Типы тайлов. Цвета и свойства — данные, а не код отрисовки."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Terrain(IntEnum):
    """Тип поверхности. Освещённость и заражение — отдельные слои, не тип."""

    GROUND = 0
    MOUNTAIN = 1
    WATER = 2


@dataclass(frozen=True)
class TerrainType:
    id: int
    name: str
    color: tuple[int, int, int]
    variants: tuple[tuple[int, int, int], ...]
    walkable: bool


TERRAIN_TYPES: dict[int, TerrainType] = {
    Terrain.GROUND: TerrainType(
        id=int(Terrain.GROUND),
        name="Земля",
        color=(72, 76, 58),
        variants=((64, 68, 52), (80, 84, 64), (70, 74, 56), (76, 80, 60)),
        walkable=True,
    ),
    Terrain.MOUNTAIN: TerrainType(
        id=int(Terrain.MOUNTAIN),
        name="Гора",
        color=(84, 80, 92),
        variants=((72, 68, 80), (96, 92, 104), (78, 74, 86), (90, 86, 98)),
        walkable=False,
    ),
    Terrain.WATER: TerrainType(
        id=int(Terrain.WATER),
        name="Вода",
        color=(38, 76, 112),
        variants=((32, 68, 104), (46, 86, 122), (36, 72, 108), (42, 82, 118)),
        walkable=False,
    ),
}


def terrain_type(value: int) -> TerrainType:
    """Описание типа тайла по числовому значению слоя."""
    return TERRAIN_TYPES[int(value)]
