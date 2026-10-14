"""Команды игрока для симуляции (plan/03, plan/10). Не импортирует pygame.

Интерфейс не меняет состояние сам: он создаёт команду и отдаёт её в
`Simulation.apply`. Проверка допустимости живёт в одном месте — в симуляции
(`Simulation.can_place`), поэтому «призрак зелёный» и «здание встало» не могут
разойтись.

Демонтаж — действие с удержанием ЛКМ: его ведёт `Simulation.update`, а
`DismantleBuilding` — это то, во что удержание превращается, когда кольцо
прогресса заполнилось (и вход для тестов и инструментов).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectHotbar:
    """Выбрать слот панели быстрого доступа (клавиши 1…9, клик по слоту)."""

    index: int


@dataclass(frozen=True)
class PlaceBuilding:
    """Поставить постройку из слота хотбара на тайл, потратив один предмет.

    `rotation` — индекс направления в `sim.buildings.DIRECTIONS`; None —
    «как показывает призрак» (накрученный R или «от дрона»).
    """

    slot: int
    tile: tuple[int, int]
    rotation: int | None = None


@dataclass(frozen=True)
class DismantleBuilding:
    """Разобрать свою постройку: она падает на землю предметом (plan/03)."""

    tile: tuple[int, int]


Command = SelectHotbar | PlaceBuilding | DismantleBuilding
