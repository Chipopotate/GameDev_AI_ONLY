"""Фабрика спрайтов и кэш (plan/09 §3.1–3.2).

Все значки мира рисуются **один раз** в увеличенную в 🔧 4 раза поверхность и
сжимаются `smoothscale`: это убирает «рваные» края, которых не избежать, если
рисовать многоугольник сразу в 24 пикселя. Готовый спрайт живёт в кэше по
ключу `(вид, вариант, размер, состояние, угол)` — после прогрева в игровом
кадре не создаётся ни одной новой `Surface` (это проверяет тест plan/09 §13).

Функции рисования получают сторону квадрата `s` в пикселях **увеличенного**
холста и работают только с долями `s`. Благодаря этому один и тот же код
рисует и значок 20 px в мире, и карточку 96 px в галерее (plan/09 §12).
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from src.game import config
from src.game.render import theme

#: Сколько сторон у «круга» при рисовании через `pygame.draw.polygon`.
#: Увеличенный холст сглаживает углы, поэтому 24 сторон достаточно.
CIRCLE_SIDES = 24


def polygon_points(cx: float, cy: float, r: float, sides: int,
                   angle: float = 0.0) -> list[tuple[float, float]]:
    """Вершины правильного многоугольника — базовая операция всех значков."""
    return [(cx + math.cos(angle + math.tau * i / sides) * r,
             cy + math.sin(angle + math.tau * i / sides) * r) for i in range(sides)]


def circle(surf: pygame.Surface, color, cx: float, cy: float, r: float,
           width: int = 0) -> None:
    """Круг через многоугольник: `pygame.draw.circle` не сглаживается зумом."""
    pygame.draw.polygon(surf, color, polygon_points(cx, cy, r, CIRCLE_SIDES), width)


def pad(pts, cx: float, cy: float, extra: float,
        scale: float = 1.0) -> list[tuple[float, float]]:
    """Раздувает многоугольник от центра — подложка тёмного контура.

    Контур рисуется как тот же многоугольник, сдвинутый наружу на `extra`
    пикселей: так у любой фигуры появляется обводка одинаковой толщины, и
    отдельная логика «нарисовать контур» не нужна.
    """
    out: list[tuple[float, float]] = []
    for x, y in pts:
        dx, dy = x - cx, y - cy
        length = math.hypot(dx, dy) or 1.0
        out.append((cx + dx * scale + dx / length * extra,
                    cy + dy * scale + dy / length * extra))
    return out


def ring(surf: pygame.Surface, color, cx: float, cy: float, r: float, width: float) -> None:
    """Кольцо — круг с «дыркой»: контур радиуса света, ореол, шестерня."""
    pygame.draw.polygon(surf, color, polygon_points(cx, cy, r, CIRCLE_SIDES), int(max(1, width)))


def rounded_rect(surf: pygame.Surface, color, rect: pygame.Rect, radius: float,
                 width: int = 0) -> None:
    pygame.draw.rect(surf, color, rect, width, border_radius=int(max(0, radius)))


def bevel_frame(surf: pygame.Surface, rect: pygame.Rect, body, radius: float,
                outline=theme.OUTLINE, outline_w: int = 0) -> None:
    """Основа постройки: тело, толстый тёмный контур и фаска (plan/09 §2).

    Фаска — два «полуконтура»: верх-лево светлее на 25%, низ-право темнее на
    30%. Именно она даёт объём, которого не даёт плоская заливка.
    """
    w = outline_w or max(2, int(rect.width * 0.09))
    light = theme.lighten(body, 0.25)
    dark = theme.darken(body, 0.30)
    pygame.draw.rect(surf, outline, rect.inflate(w * 2, w * 2),
                     border_radius=int(radius + w))
    pygame.draw.rect(surf, body, rect, border_radius=int(radius))
    # Фаска: светлая полоса сверху и слева, тёмная — снизу и справа.
    pygame.draw.line(surf, light, (rect.left + radius * 0.5, rect.top + w * 0.5),
                     (rect.right - radius * 0.5, rect.top + w * 0.5), max(1, w // 2))
    pygame.draw.line(surf, light, (rect.left + w * 0.5, rect.top + radius * 0.5),
                     (rect.left + w * 0.5, rect.bottom - radius * 0.5), max(1, w // 2))
    pygame.draw.line(surf, dark, (rect.left + radius * 0.5, rect.bottom - w * 0.5),
                     (rect.right - radius * 0.5, rect.bottom - w * 0.5), max(1, w // 2))
    pygame.draw.line(surf, dark, (rect.right - w * 0.5, rect.top + radius * 0.5),
                     (rect.right - w * 0.5, rect.bottom - radius * 0.5), max(1, w // 2))


def rivets(surf: pygame.Surface, rect: pygame.Rect, color, inset: float) -> None:
    """Мелкие заклёпки по углам панели: «тяжёлая» деталь, как в plan/09 §5."""
    r = max(1.0, rect.width * 0.035)
    for x in (rect.left + inset, rect.right - inset):
        for y in (rect.top + inset, rect.bottom - inset):
            circle(surf, color, x, y, r)


def neon_edge(surf: pygame.Surface, rect: pygame.Rect, color, radius: float,
              width: float, alpha: int = 255) -> None:
    """Тонкая неоновая кайма по краю панели — «светящаяся линия» игрока."""
    layer = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(layer, theme.with_alpha(color, alpha), rect,
                     int(max(1, width)), border_radius=int(radius))
    surf.blit(layer, (0, 0))


class SpriteFactory:
    """Кэш спрайтов: `(ключ, размер) → Surface`."""

    def __init__(self, supersample: int = 0) -> None:
        self.ss = int(supersample or config.SPRITE_SUPERSAMPLE)
        self._cache: dict[tuple, pygame.Surface] = {}
        self._halos: dict[tuple, pygame.Surface] = {}
        #: Сколько спрайтов реально нарисовано. В игровом кадре не должно расти.
        self.rendered = 0
        self.halos_made = 0

    # ------------------------------------------------------------- спрайты
    def get(self, key: tuple, size: int, draw) -> pygame.Surface:
        """Спрайт по ключу. `draw(surface, s)` рисует на холсте со стороной `s`."""
        size = max(4, int(size))
        full_key = (key, size)
        surf = self._cache.get(full_key)
        if surf is None:
            big = size * self.ss
            canvas = pygame.Surface((big, big), pygame.SRCALPHA)
            draw(canvas, float(big))
            surf = pygame.transform.smoothscale(canvas, (size, size))
            if pygame.display.get_surface() is not None:
                surf = surf.convert_alpha()
            self._cache[full_key] = surf
            self.rendered += 1
        return surf

    def cached(self, key: tuple, size: int) -> pygame.Surface | None:
        """Уже нарисованный спрайт — без отрисовки (для тестов кэша)."""
        return self._cache.get((key, max(4, int(size))))

    def clear(self) -> None:
        self._cache.clear()
        self._halos.clear()

    # -------------------------------------------------------------- ореолы
    def halo(self, radius: int, color, inner: float = 0.0, peak: float = 1.0,
             soft: float = 1.0) -> pygame.Surface:
        """Радиальный ореол свечения (plan/09 §3.4). Блитится аддитивно.

        Строится один раз на `(радиус, цвет, форма, яркость)` и дальше только
        блитится — свечение не должно стоить ни одной аллокации в кадре.
        """
        radius = max(2, int(radius))
        key = (radius, color, round(inner, 2), round(peak, 2), round(soft, 2))
        surf = self._halos.get(key)
        if surf is None:
            surf = _make_halo(radius, color, inner, peak, soft)
            self._halos[key] = surf
            self.halos_made += 1
        return surf

    @staticmethod
    def blit_add(target: pygame.Surface, surf: pygame.Surface, pos: tuple[float, float]) -> None:
        """Аддитивный блит ореола с центром в `pos`."""
        rect = surf.get_rect(center=(int(pos[0]), int(pos[1])))
        target.blit(surf, rect, special_flags=pygame.BLEND_RGB_ADD)


def _make_halo(radius: int, color, inner: float, peak: float,
               soft: float) -> pygame.Surface:
    """Кольцевой градиент: ровный внутри `inner`, дальше сходит на нет."""
    size = radius * 2 + 1
    surf = pygame.Surface((size, size))
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = float(radius)
    dist = np.hypot(xx - c, yy - c) / max(1.0, c)
    inner = max(0.0, min(0.95, inner))
    k = 1.0 - np.clip((dist - inner) / max(1e-6, soft - inner), 0.0, 1.0)
    k = np.clip(k, 0.0, 1.0) ** 1.35          # чуть резче к краю — свет не «мылит»
    arr = pygame.surfarray.pixels3d(surf)
    for ch in range(3):
        # `peak` — доля 0..1, а цвет уже 0..255: делить на 255 ещё раз нельзя,
        # иначе ореол выходит чёрным (так и было до M4.6 — свечения не было видно).
        arr[..., ch] = np.clip(color[ch] * peak * k, 0, 255).astype(np.uint8).T
    del arr
    return surf
