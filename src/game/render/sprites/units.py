"""Дрон и роботы (plan/09 §6).

Дрон — белый корпус-дельта с циановым ядром: единственный объект в игре,
который рисуется белым, поэтому его видно и на тёмной земле, и в неоне ламп.
Корпус поворачивается по направлению движения (32 угловых шага кэша), а
«парит» он за счёт мягкой тени со смещением — она рисуется отдельно и
сдвигается вместе с покачиванием.

Корпуса роботов (M7) собираются из тела и **видимых модулей**: по картинке
должно быть понятно, что висит на роботе, ещё до открытия его окна. Пока это
только отрисовка — самих роботов в симуляции ещё нет (plan/09 §6, M7).
"""

from __future__ import annotations

import math

import pygame

from src.game.render import theme
from src.game.render.sprites import factory

#: Модули, которые видно на корпусе (plan/09 §6). Строки — те же id, что будут
#: в данных роботов на M7; здесь они только определяют вид.
WEAPON_FAST = "weapon_fast"
WEAPON_HEAVY = "weapon_heavy"
WEAPON_LONG = "weapon_long"
SHIELD = "shield"
RADAR = "radar"
TANK = "tank"
COURIER = "courier"
REPAIR = "repair"

CHASSIS_GROUND = "ground"
CHASSIS_FLYING = "flying"


# ------------------------------------------------------------------- корпус
def draw_drone(surf: pygame.Surface, s: float, angle: float) -> None:
    """Дрон: дельта-крыло, циановое ядро, два двигателя по бокам."""
    c = s / 2.0
    r = s * 0.34
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rot = lambda px, py: (c + px * cos_a - py * sin_a, c + px * sin_a + py * cos_a)  # noqa: E731

    # Корпус-дельта: нос впереди, два «крыла» сзади.
    body = [rot(r * 1.05, 0.0), rot(-r * 0.62, -r * 0.86), rot(-r * 0.24, 0.0),
            rot(-r * 0.62, r * 0.86)]
    pygame.draw.polygon(surf, theme.OUTLINE, _pad(body, c, r * 0.22))
    pygame.draw.polygon(surf, theme.NEON_WHITE, body)
    # Фаска: верхняя половина корпуса светлее, нижняя — в тени.
    pygame.draw.polygon(surf, theme.lighten(theme.NEON_ICE, 0.35),
                        [rot(r * 0.86, 0.0), rot(-r * 0.5, -r * 0.66), rot(-r * 0.24, 0.0)])
    # Двигатели: тёмные сопла по краям крыла.
    for side in (-1, 1):
        ex, ey = rot(-r * 0.48, side * r * 0.62)
        factory.circle(surf, theme.OUTLINE, ex, ey, r * 0.26)
        factory.circle(surf, theme.STEEL_DARK, ex, ey, r * 0.20)
    # Ядро.
    factory.circle(surf, theme.darken(theme.NEON_CYAN, 0.45), c, c, r * 0.36)
    factory.circle(surf, theme.NEON_CYAN, c, c, r * 0.27)
    factory.circle(surf, theme.NEON_WHITE, c, c, r * 0.12)


def _pad(pts, c, pad_):
    """Раздувает многоугольник — подложка тёмного контура (см. `factory.pad`)."""
    return factory.pad(pts, c, c, pad_)


def draw_shadow(surf: pygame.Surface, s: float) -> None:
    """Мягкая тень под парящим объектом: размытый эллипс, без резкого края."""
    shadow = pygame.Surface((int(s), int(s)), pygame.SRCALPHA)
    rx, ry = s * 0.34, s * 0.26
    steps = 7
    for i in range(steps):
        k = 1.0 - i / steps
        alpha = int(90 * (1.0 - k) / steps * 3)
        pygame.draw.ellipse(shadow, (0, 0, 0, alpha),
                            (s / 2 - rx * k, s / 2 - ry * k, rx * 2 * k, ry * 2 * k))
    surf.blit(shadow, (0, 0))


# --------------------------------------------------------- корпуса роботов
def draw_chassis(surf: pygame.Surface, s: float, chassis: str, angle: float,
                 modules: tuple[str, ...] = ()) -> None:
    """Корпус робота с модулями. Вид зависит и от корпуса, и от навески."""
    c = s / 2.0
    r = s * 0.32
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def rot(px: float, py: float) -> tuple[float, float]:
        return (c + px * cos_a - py * sin_a, c + px * sin_a + py * cos_a)

    if chassis == CHASSIS_FLYING:
        body = [rot(r * 1.1, 0.0), rot(-r * 0.7, -r * 0.95), rot(-r * 0.2, 0.0),
                rot(-r * 0.7, r * 0.95)]
        hull = theme.STEEL_LIGHT
    else:
        body = [rot(r * 0.95, -r * 0.7), rot(r * 0.95, r * 0.7),
                rot(-r * 0.8, r * 0.85), rot(-r * 0.8, -r * 0.85)]
        hull = theme.STEEL_MID
    pygame.draw.polygon(surf, theme.OUTLINE, _pad(body, c, r * 0.20))
    pygame.draw.polygon(surf, hull, body)
    pygame.draw.polygon(surf, theme.lighten(hull, 0.3),
                        [rot(r * 0.7, 0.0), rot(-r * 0.6, -r * 0.5), rot(-r * 0.4, 0.0)])
    if chassis == CHASSIS_GROUND:
        # Гусеницы по бортам — наземный корпус узнаётся по ним.
        for side in (-1, 1):
            a = rot(0.0, side * r * 0.78)
            b = rot(-r * 0.9, side * r * 0.78)
            pygame.draw.line(surf, theme.OUTLINE, a, b, max(2, int(r * 0.34)))
            pygame.draw.line(surf, theme.darken(theme.STEEL_DARK, 0.25), a, b,
                             max(1, int(r * 0.22)))
    factory.circle(surf, theme.darken(theme.NEON_CYAN, 0.5), c, c, r * 0.30)
    factory.circle(surf, theme.NEON_CYAN, c, c, r * 0.20)
    for module in modules:
        _draw_module(surf, c, r, rot, module)


def _draw_module(surf, c: float, r: float, rot, module: str) -> None:
    """Один модуль на корпусе: ствол, щит, тарелка, бак, ящик, манипулятор."""
    if module in (WEAPON_FAST, WEAPON_HEAVY, WEAPON_LONG):
        if module == WEAPON_FAST:
            barrels, w, length = ((-r * 0.22), (r * 0.22)), r * 0.10, r * 0.95
        elif module == WEAPON_HEAVY:
            barrels, w, length = (0.0,), r * 0.20, r * 0.85
        else:
            barrels, w, length = (0.0,), r * 0.08, r * 1.30
        for off in barrels:
            a = rot(r * 0.55, off)
            b = rot(r * 0.55 + length, off)
            pygame.draw.line(surf, theme.OUTLINE, a, b, max(2, int(w * 1.7)))
            pygame.draw.line(surf, theme.STEEL_LIGHT, a, b, max(1, int(w)))
        tip = rot(r * 0.55 + length, barrels[-1])
        factory.circle(surf, theme.mix(theme.NEON_AMBER, theme.NEON_WHITE, 0.3),
                       tip[0], tip[1], w * 0.7)
    elif module == SHIELD:
        pygame.draw.polygon(surf, theme.with_alpha(theme.NEON_CYAN, 255),
                            factory.polygon_points(c, c, r * 1.32, 6, -math.pi / 2),
                            max(1, int(r * 0.13)))
    elif module == RADAR:
        a = rot(-r * 0.55, 0.0)
        pygame.draw.line(surf, theme.STEEL_LIGHT, (c, c), a, max(1, int(r * 0.12)))
        pygame.draw.ellipse(surf, theme.lighten(theme.STEEL_LIGHT, 0.25),
                            (a[0] - r * 0.30, a[1] - r * 0.16, r * 0.60, r * 0.32))
        pygame.draw.ellipse(surf, theme.OUTLINE,
                            (a[0] - r * 0.30, a[1] - r * 0.16, r * 0.60, r * 0.32),
                            max(1, int(r * 0.06)))
    elif module in (TANK, COURIER):
        color = theme.NEON_AMBER if module == TANK else theme.hue_shift(theme.NEON_CYAN, 40)
        for side in (-1, 1):
            x, y = rot(-r * 0.35, side * r * 0.72)
            factory.circle(surf, theme.OUTLINE, x, y, r * 0.28)
            factory.circle(surf, theme.darken(color, 0.55), x, y, r * 0.22)
    elif module == REPAIR:
        for side in (-1, 1):
            a = rot(r * 0.35, side * r * 0.85)
            b = rot(r * 0.85, side * r * 1.05)
            pygame.draw.line(surf, theme.OUTLINE, a, b, max(2, int(r * 0.16)))
            pygame.draw.line(surf, theme.NEON_AMBER, a, b, max(1, int(r * 0.09)))
