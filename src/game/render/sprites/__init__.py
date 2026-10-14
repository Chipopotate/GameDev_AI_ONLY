"""Спрайты мира: фабрика, кэш и «язык форм» (plan/09 §3, §14).

Каждый объект получает вид через данные (`shape`, роль, состояние), а не через
отдельный класс: новое здание — строка в `data/buildings.py`, и `SpriteCache`
нарисует его сам. Здесь лежит только **фасад над кэшем** — сами формы рисуют
`buildings.py`, `units.py`, `gems.py` и `organic.py`.

Один `SpriteCache` живёт в `Renderer` и переживает кадры. После прогрева
`factory.rendered` в игровом кадре не растёт — это проверяет `tests/test_visual.py`.
"""

from __future__ import annotations

import math

import pygame

from src.game.render import theme
from src.game.render.sprites import buildings, factory, gems, organic, units
from src.game.render.sprites.factory import SpriteFactory

__all__ = ["SpriteCache", "SpriteFactory", "buildings", "factory", "gems",
           "organic", "units"]


class SpriteCache:
    """Готовые спрайты всех объектов мира по ключу «вид + состояние + размер»."""

    def __init__(self, items=None, supersample: int = 0) -> None:
        self.factory = SpriteFactory(supersample)
        self.gems = gems.GemFactory(items)

    # ------------------------------------------------------------ здания
    def building(self, kind: str, state: str, size: int, damage: int = 0) -> pygame.Surface:
        key = ("building", kind, state, int(damage))
        return self.factory.get(
            key, size,
            lambda surf, s: buildings.draw_building(surf, s, kind, state, damage))

    def wire(self, size: int, mask: tuple[bool, bool, bool, bool], live: bool) -> pygame.Surface:
        """Провод по маске соседей (восток, юг, запад, север): 16 вариантов × 2."""
        role = buildings.ROLE_COLOR[buildings.WIRE] if live else theme.POWER_OFF
        key = ("wire", tuple(bool(m) for m in mask), bool(live))
        return self.factory.get(
            key, size, lambda surf, s: buildings.draw_wire(surf, s, mask, role))

    def no_power(self, size: int, bright: bool = True) -> pygame.Surface:
        """Значок «нет питания» — отдельный спрайт поверх мира (plan/09 §5)."""
        return self.factory.get(
            ("no_power", bool(bright)), size,
            lambda surf, s: buildings.draw_no_power_mark(surf, s, bright))

    def rotor(self, size: int, angle: float, role, active: bool) -> pygame.Surface:
        """Ротор бура: 32 угловых шага кэша (plan/09 §3.2)."""
        bucket = buildings.nearest_bucket(angle)
        step = math.tau * bucket / buildings.ANGLE_BUCKETS
        key = ("rotor", tuple(role), bool(active), bucket)
        return self.factory.get(
            key, size, lambda surf, s: buildings.draw_rotor(surf, s, step, role, active))

    def chevron(self, size: int, direction: tuple[int, int], color) -> pygame.Surface:
        """Шеврон конвейера под направление ленты (4 варианта)."""
        angle = math.atan2(direction[1], direction[0])
        key = ("chevron", tuple(color), int(round(angle * 4 / math.tau)))
        return self.factory.get(
            key, size, lambda surf, s: buildings.draw_chevron(surf, s, color, angle))

    # ------------------------------------------------------------- дрон
    def drone(self, size: int, angle: float) -> pygame.Surface:
        bucket = buildings.nearest_bucket(angle)
        step = math.tau * bucket / buildings.ANGLE_BUCKETS
        return self.factory.get(
            ("drone", bucket), size,
            lambda surf, s: units.draw_drone(surf, s, step))

    def drone_shadow(self, size: int) -> pygame.Surface:
        return self.factory.get(("drone_shadow",), size,
                                lambda surf, s: units.draw_shadow(surf, s))

    def chassis(self, size: int, chassis: str, angle: float,
                modules: tuple[str, ...] = ()) -> pygame.Surface:
        bucket = buildings.nearest_bucket(angle)
        step = math.tau * bucket / buildings.ANGLE_BUCKETS
        key = ("chassis", chassis, tuple(modules), bucket)
        return self.factory.get(
            key, size,
            lambda surf, s: units.draw_chassis(surf, s, chassis, step, modules))

    # ---------------------------------------------------------- ресурсы
    def gem(self, item_id: str, size: int, color, glow: bool = False) -> pygame.Surface:
        """Самоцвет ресурса. Форма берётся из генератора огранок (plan/09 §7).

        Цвет входит в ключ кэша: истощённая залежь и изученная реликвия — тот же
        силуэт, но другой цвет, и подменять друг друга в кэше они не должны.
        """
        shape = self.gems.shape(item_id)
        key = ("gem", item_id, tuple(color), bool(glow))
        return self.factory.get(
            key, size, lambda surf, s: gems.draw_gem(surf, s, shape, color, glow))

    def chip(self, item_id: str, size: int, color) -> pygame.Surface:
        """Крафченый предмет роли: стальной жетон, а не самоцвет."""
        key = ("chip", item_id, tuple(color))
        return self.factory.get(key, size,
                                lambda surf, s: gems.draw_chip(surf, s, color))

    # ------------------------------------------------------- заражение
    def creature(self, kind: str, size: int, phase: float) -> pygame.Surface:
        """Враг. Фаза пульсации кэшируется по 🔧 16 ступеням (plan/09 §8)."""
        step = int((phase % math.tau) / math.tau * 16) % 16
        key = ("creature", kind, step)
        return self.factory.get(
            key, size,
            lambda surf, s: organic.draw_creature(surf, s, kind, math.tau * step / 16))

    def clear(self) -> None:
        self.factory.clear()
