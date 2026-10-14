"""Примитивы отрисовки.

Правило проекта: вся графика — код, без внешних ассетов. Враги, здания и
ресурсы описывают себя полями `shape`/`color`/`size` в `data/`, а этот модуль
умеет рисовать каждую форму.
"""

from __future__ import annotations

import math

import pygame

Point = tuple[float, float]

_TWO_PI = math.pi * 2.0
OUTLINE_DEFAULT = (12, 14, 20)


def _ipt(pos: Point) -> tuple[int, int]:
    return int(round(pos[0])), int(round(pos[1]))


def _polygon(surf: pygame.Surface, color, pos: Point, size: float, angle: float,
             sides: int, rot: float, width: int = 0) -> None:
    verts: list[Point] = []
    for i in range(sides):
        a = angle + rot + _TWO_PI * i / sides
        verts.append((pos[0] + math.cos(a) * size, pos[1] + math.sin(a) * size))
    pygame.draw.polygon(surf, color, verts, width)


def draw_circle(surf: pygame.Surface, color, pos: Point, size: float,
                angle: float = 0.0, width: int = 0) -> None:
    pygame.draw.circle(surf, color, _ipt(pos), max(1, int(round(size))), width)


def draw_triangle(surf: pygame.Surface, color, pos: Point, size: float,
                  angle: float = 0.0, width: int = 0) -> None:
    _polygon(surf, color, pos, size, angle, 3, -math.pi / 2, width)


def draw_diamond(surf: pygame.Surface, color, pos: Point, size: float,
                 angle: float = 0.0, width: int = 0) -> None:
    _polygon(surf, color, pos, size, angle, 4, -math.pi / 2, width)


def draw_square(surf: pygame.Surface, color, pos: Point, size: float,
                angle: float = 0.0, width: int = 0) -> None:
    _polygon(surf, color, pos, size * 0.86, angle, 4, math.pi / 4, width)


def draw_hexagon(surf: pygame.Surface, color, pos: Point, size: float,
                 angle: float = 0.0, width: int = 0) -> None:
    _polygon(surf, color, pos, size, angle, 6, -math.pi / 2, width)


def draw_star(surf: pygame.Surface, color, pos: Point, size: float,
              angle: float = 0.0, width: int = 0, points: int = 5,
              inner: float = 0.46) -> None:
    verts: list[Point] = []
    for i in range(points * 2):
        r = size if i % 2 == 0 else size * inner
        a = angle - math.pi / 2 + math.pi * i / points
        verts.append((pos[0] + math.cos(a) * r, pos[1] + math.sin(a) * r))
    pygame.draw.polygon(surf, color, verts, width)


def draw_cluster(surf: pygame.Surface, color, pos: Point, size: float,
                 angle: float = 0.0, width: int = 0) -> None:
    """Кластер из трёх малых кристаллов — «залежь», а не одиночный камень."""
    for k, a in enumerate((-math.pi / 2, math.pi / 6, math.pi * 5 / 6)):
        cx = pos[0] + math.cos(a) * size * 0.5
        cy = pos[1] + math.sin(a) * size * 0.5
        r = size * (0.62 if k == 0 else 0.48)
        draw_diamond(surf, color, (cx, cy), r, angle, width)


def draw_cross(surf: pygame.Surface, color, pos: Point, size: float,
               angle: float = 0.0, width: int = 0) -> None:
    """Крест из двух перекладин — «поддержка»."""
    t = size * 0.34
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    for w, h in ((t, size), (size, t)):
        pts = []
        for sx, sy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
            pts.append((pos[0] + sx * cos_a - sy * sin_a,
                        pos[1] + sx * sin_a + sy * cos_a))
        pygame.draw.polygon(surf, color, pts, width)


_BOLT = ((0.10, -1.00), (-0.46, 0.08), (-0.06, 0.08),
         (-0.14, 1.00), (0.46, -0.10), (0.06, -0.10))


def draw_bolt(surf: pygame.Surface, color, pos: Point, size: float,
              angle: float = 0.0, width: int = 0) -> None:
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    pts = []
    for nx, ny in _BOLT:
        px, py = nx * size, ny * size
        pts.append((pos[0] + px * cos_a - py * sin_a, pos[1] + px * sin_a + py * cos_a))
    pygame.draw.polygon(surf, color, pts, width)


def draw_gear(surf: pygame.Surface, color, pos: Point, size: float,
              angle: float = 0.0, width: int = 0) -> None:
    """Круг с зубцами — свойство «режущий»."""
    teeth = 6
    for i in range(teeth):
        a = angle + _TWO_PI * i / teeth
        tx = pos[0] + math.cos(a) * size * 0.86
        ty = pos[1] + math.sin(a) * size * 0.86
        draw_square(surf, color, (tx, ty), size * 0.30, a, width)
    draw_circle(surf, color, pos, size * 0.66, angle, width)


SHAPES = {
    "circle": draw_circle,
    "triangle": draw_triangle,
    "diamond": draw_diamond,
    "square": draw_square,
    "hexagon": draw_hexagon,
    "star": draw_star,
    "cluster": draw_cluster,
    "cross": draw_cross,
    "bolt": draw_bolt,
    "gear": draw_gear,
}


def draw_shape(surf: pygame.Surface, name: str, color, pos: Point, size: float,
               angle: float = 0.0, width: int = 0) -> None:
    """Нарисовать форму по имени. Неизвестная форма — круг (безопасный дефолт)."""
    SHAPES.get(name, draw_circle)(surf, color, pos, size, angle, width)


def draw_shape_outlined(surf: pygame.Surface, name: str, color, pos: Point,
                        size: float, outline=OUTLINE_DEFAULT, angle: float = 0.0) -> None:
    """Форма с тёмным контуром — чтобы читалась на любом фоне."""
    draw_shape(surf, name, outline, pos, size * 1.3, angle)
    draw_shape(surf, name, color, pos, size, angle)
