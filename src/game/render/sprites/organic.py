"""Заражение и враги: био-органика (plan/09 §8).

Грамматика стиля: контур — замкнутая кривая из 12–24 точек со **шумовым
смещением вершин**, которое медленно меняется во времени; заливка — тёмное тело
с градиентом к ядру; тонкая тёмная мембрана по краю; жилки и клеточные ядра
внутри. Светятся только глаза, ядра и жилки (plan/09 §1: «тела приглушённо-
тёмные, светятся только глаза/ядра»), поэтому в темноте врага выдаёт пара
светящихся точек, а не силуэт.

Форма-язык бестиария (`plan/05`) сохраняется и после органической обработки:
круг — масса, треугольник — атака, ромб — быстрый, шестиугольник — тяжёлый,
крест — поддержка.

Врагов в симуляции пока нет (M5 — турели и первые враги, M8 — ростки и
инкубаторы). Модуль — заготовка отрисовки: он нужен галерее (plan/09 §12) и
тесту различимости силуэтов (§13), а подключать его к кадру будет M5.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pygame

from src.game.render import theme
from src.game.render.sprites import factory

#: Бестиарий. Ключ — id врага, значение — силуэт (см. `Body`).
CRAWLER = "crawler"      # Ходок: острый клин, два глаза-точки (M5)
SHADOW = "shadow"        # Тень: полупрозрачное пятно с искрами (M5)
MOSQUITO = "mosquito"    # Москит: тонкое тело, мерцающие крылья (M6)
ARTILLERY = "artillery"  # Артиллерия: тяжёлое тело с «пузырём»-орудием (M6)
SPROUT = "sprout"        # Росток: сердце с пульсом и щупальцами (M8)
INCUBATOR = "incubator"  # Инкубатор: капсула с мембраной (M8)
SPIKE = "spike"          # Защитный нарост заражения (M8)


@dataclass(frozen=True)
class Body:
    """Силуэт врага: форма бестиария и её органическая обработка (plan/09 §8).

    Раньше здесь было `(форма, вершин, дрожание)`, и **все** враги выходили
    одинаковым блобом: контур уходил от круга всего на ±11 % радиуса, а порог
    различимости силуэтов (plan/09 §13, IoU 0.75) был превышен вдвое плотнее —
    самые похожие пары давали 0.84. Поэтому у силуэта появились три ручки,
    которыми форма-язык и читается:

    - `amp` — насколько глубоко прорезана базовая форма: у «тяжёлого»
      шестиугольника грань видна, у «массы» её нет вовсе;
    - `stretch` — вытянутость по осям: «быстрый» ромб тонкий, «масса» широкая;
    - `notch` — выемка сверху: сердце ростка.

    Радиус у всех один, так что формы различаются **формой**, а не размером:
    иначе самый крупный силуэт читался бы как самый опасный. Площадь при этом
    разная и обязана быть разной — «масса» шире креста из четырёх шипов.
    """

    base: str                             # форма-язык: круг, треугольник, ромб…
    vertices: int                         # вершин контура, 12–24 (plan/09 §8)
    wobble: float                         # амплитуда дрожания вершин
    amp: float = 0.28                     # глубина прорезки базовой формы
    stretch: tuple[float, float] = (1.0, 1.0)
    notch: float = 0.0                    # выемка сверху, доля радиуса


#: Насколько широко разнесены силуэты — проверяется `tests/test_visual.py`:
#: самая похожая пара врагов должна давать IoU ниже 🔧 0.75 (plan/09 §13).
SHAPES: dict[str, Body] = {
    # Атака: клин остриём вверх, чуть выше, чем шире.
    CRAWLER: Body("triangle", 16, 0.10, amp=0.34, stretch=(0.84, 1.16)),
    # Масса: неровное пятно, самое широкое и самое «дрожащее» — ни грани,
    # ни оси у него нет.
    SHADOW: Body("circle", 14, 0.30, amp=0.0, stretch=(1.20, 0.84)),
    # Быстрый: тонкое тело вдоль хода, ромб с острыми концами.
    MOSQUITO: Body("diamond", 14, 0.12, amp=0.30, stretch=(1.34, 0.56)),
    # Тяжёлый: широкий шестиугольник с крупной гранью.
    ARTILLERY: Body("hexagon", 18, 0.09, amp=0.26, stretch=(1.12, 0.94)),
    # Поддержка: сердце — круг с выемкой сверху.
    SPROUT: Body("circle", 22, 0.14, amp=0.0, notch=0.40),
    # Капсула: вытянута вверх, грани сглажены — «сосуд», а не «броня».
    INCUBATOR: Body("hexagon", 12, 0.09, amp=0.18, stretch=(0.86, 1.20)),
    # Нарост: крест из четырёх шипов. Вершин 16, а не 12: при 12 контур
    # проскакивает впадины креста между шипами, и он выходит просто квадратом.
    SPIKE: Body("cross", 16, 0.12, amp=0.38, stretch=(1.02, 0.98)),
}

#: Цвет свечения по типу: ядовитая половина палитры (plan/09 §2).
GLOW: dict[str, tuple[int, int, int]] = {
    CRAWLER: theme.FOE_TOXIC,
    SHADOW: theme.FOE_MAGENTA,
    MOSQUITO: theme.FOE_TOXIC,
    ARTILLERY: theme.FOE_BILIOUS,
    SPROUT: theme.FOE_MAGENTA,
    INCUBATOR: theme.FOE_BILIOUS,
    SPIKE: theme.FOE_BLOOD,
}


# ------------------------------------------------------------------- контур
def contour_points(kind: str, phase: float, s: float,
                   wobble_scale: float = 1.0) -> list[tuple[float, float]]:
    """Замкнутый органический контур вокруг центра холста (plan/09 §8).

    Один и тот же вызов даёт ту же форму при том же `phase`: дрожание медленно
    «дышит» во времени, но не прыгает — иначе врага нельзя было бы разглядеть.
    """
    body = SHAPES[kind]
    count = body.vertices
    c = s / 2.0
    r = s * 0.34
    rng = random.Random(_seed_of(kind))
    jitter = [rng.uniform(-1.0, 1.0) for _ in range(count)]
    pts: list[tuple[float, float]] = []
    for i in range(count):
        a = math.tau * i / count
        # Базовая форма бестиария: у круга радиус ровный, у треугольника —
        # максимум в трёх направлениях, и так далее. `shape_k` нормирован в
        # 0..1, поэтому `amp` значит одно и то же для всех форм.
        shape_k = _shape_radius(body.base, a)
        # Шумовое смещение: медленная синусоида на каждой вершине со своей фазой.
        noise = math.sin(phase * 1.6 + jitter[i] * 3.1) * body.wobble * wobble_scale
        radius = r * (1.0 + (shape_k - 0.5) * 2.0 * body.amp + noise)
        if body.notch:
            # Выемка сверху (сердце ростка): сужаем радиус в окне вокруг «верха».
            radius *= 1.0 - body.notch * math.exp(-(_wrap(a - math.pi / 2) / 0.55) ** 2)
        pts.append((c + math.cos(a) * radius * body.stretch[0],
                    c + math.sin(a) * radius * body.stretch[1]))
    return pts


def _wrap(angle: float) -> float:
    """Угол в -π..π: без него выемка «сердца» рвалась бы на стыке круга."""
    return (angle + math.pi) % math.tau - math.pi


def _shape_radius(base_shape: str, angle: float) -> float:
    """Отклонение радиуса от круглого для формы бестиария, 0..1."""
    if base_shape == "triangle":
        return math.cos(angle * 3.0 - math.pi / 2) * 0.5 + 0.5
    if base_shape == "diamond":
        return abs(math.cos(angle * 2.0))
    if base_shape == "hexagon":
        return abs(math.cos(angle * 6.0)) * 0.5 + 0.5
    if base_shape == "cross":
        return abs(math.cos(angle * 4.0))
    return 0.5


def _seed_of(kind: str) -> int:
    """Стабильный сид: форма врага не должна меняться между запусками."""
    value = 2166136261
    for ch in kind:
        value = ((value ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return value


# ------------------------------------------------------------------ рисование
def draw_creature(surf: pygame.Surface, s: float, kind: str, phase: float,
                  hurt: float = 0.0) -> None:
    """Враг целиком: тело, мембрана, ядра, жилки, светящиеся глаза."""
    pts = contour_points(kind, phase, s)
    glow = GLOW[kind]
    pulse = 1.0 + 0.05 * math.sin(phase * 2.4)       # ±5% масштаба (plan/09 §8)
    c = s / 2.0
    pts = [(c + (x - c) * pulse, c + (y - c) * pulse) for x, y in pts]

    if kind == SHADOW:
        _draw_shadow_body(surf, s, pts, glow, hurt)
        return

    body = theme.mix(theme.FOE_FLESH, theme.darken(glow, 0.55), 0.35)
    if hurt > 0.0:
        body = theme.mix(body, theme.NEON_WHITE, hurt * 0.85)
    # Мембрана: тонкий тёмный контур вокруг тела.
    pygame.draw.polygon(surf, theme.OUTLINE, _grow(pts, c, 1.0, s * 0.035))
    pygame.draw.polygon(surf, body, pts)
    # Градиент к ядру: тёмный ободок и более светлый центр.
    inner = [(c + (x - c) * 0.56, c + (y - c) * 0.56) for x, y in pts]
    pygame.draw.polygon(surf, theme.lighten(body, 0.22), inner)
    _draw_vitals(surf, s, kind, pts, glow, phase, hurt)
    pygame.draw.polygon(surf, theme.darken(glow, 0.25), pts, max(1, int(s * 0.02)))


def _draw_shadow_body(surf, s, pts, glow, hurt) -> None:
    """Тень: полупрозрачное пятно, редкие искры, тонкий ободок."""
    layer = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    c = s / 2.0
    pygame.draw.polygon(layer, theme.with_alpha(theme.darken(glow, 0.75), 170), pts)
    pygame.draw.polygon(layer, theme.with_alpha(glow, 70),
                        [(c + (x - c) * 0.5, c + (y - c) * 0.5) for x, y in pts])
    surf.blit(layer, (0, 0))
    pygame.draw.polygon(surf, theme.mix(theme.OUTLINE, glow, 0.25 + hurt * 0.5), pts,
                        max(1, int(s * 0.022)))
    rng = random.Random(7)
    for _ in range(6):                              # редкие искры внутри
        a = rng.uniform(0, math.tau)
        r = rng.uniform(0.1, 0.42) * s
        factory.circle(surf, theme.lighten(glow, 0.3),
                       c + math.cos(a) * r, c + math.sin(a) * r, s * 0.018)


def _draw_vitals(surf, s, kind, pts, glow, phase, hurt) -> None:
    """Ядра, жилки и глаза — то, что светится (plan/09 §8)."""
    c = s / 2.0
    r = s * 0.34
    eye = theme.mix(glow, theme.NEON_WHITE, 0.25 + hurt * 0.6)
    if kind == CRAWLER:
        # Клин смотрит «вверх» своей острой вершиной: два глаза-точки впереди.
        for side in (-1, 1):
            factory.circle(surf, eye, c + side * r * 0.26, c - r * 0.22, r * 0.13)
            factory.circle(surf, theme.NEON_WHITE, c + side * r * 0.26,
                           c - r * 0.22, r * 0.055)
    elif kind == MOSQUITO:
        # Тонкое тело: ядро-нить вдоль и мерцающие крылья-линии.
        pygame.draw.line(surf, eye, (c, c - r * 0.62), (c, c + r * 0.62),
                         max(1, int(r * 0.18)))
        factory.circle(surf, eye, c, c, r * 0.16)
        flash = 0.5 + 0.5 * math.sin(phase * 22.0)
        for side in (-1, 1):
            pygame.draw.line(surf, theme.mix(theme.FOE_FLESH, eye, 0.35 + flash * 0.5),
                             (c, c - r * 0.2), (c + side * r * 1.05, c - r * 0.55),
                             max(1, int(r * 0.09)))
    elif kind == ARTILLERY:
        # Пузырь-орудие: темнеет и раздувается при развороте (phase).
        bubble = 1.0 + 0.25 * math.sin(phase * 1.2)
        factory.circle(surf, theme.darken(theme.FOE_FLESH, 0.2), c, c + r * 0.30,
                       r * 0.42 * bubble)
        factory.circle(surf, eye, c, c + r * 0.30, r * 0.22 * bubble)
        for i in range(3):
            a = math.tau * i / 3 + phase * 0.3
            factory.circle(surf, theme.darken(glow, 0.35),
                           c + math.cos(a) * r * 0.42, c + math.sin(a) * r * 0.42, r * 0.10)
    elif kind == SPROUT:
        # Сердце с видимым пульсом: яркость пульса = здоровье (plan/09 §8).
        beat = 0.5 + 0.5 * math.sin(phase * 3.2)
        factory.circle(surf, theme.darken(glow, 0.5), c, c, r * 0.62)
        factory.circle(surf, theme.mix(glow, theme.NEON_WHITE, beat * 0.5), c, c,
                       r * 0.40 + r * 0.06 * beat)
        for i in range(3):
            a = math.tau * i / 3 + 0.4
            factory.circle(surf, theme.darken(glow, 0.3),
                           c + math.cos(a) * r * 0.40, c + math.sin(a) * r * 0.40, r * 0.09)
    elif kind == INCUBATOR:
        # Капсула: пузыри внутри, мембрана по краю.
        rng = random.Random(11)
        for _ in range(5):
            a = rng.uniform(0, math.tau)
            d = rng.uniform(0.1, 0.5) * r
            factory.circle(surf, theme.mix(glow, theme.NEON_WHITE, 0.35),
                           c + math.cos(a) * d, c + math.sin(a) * d, r * 0.10)
        _veins(surf, c, r, glow, 5)
    elif kind == SPIKE:
        for i in range(4):
            a = math.tau * i / 4 + math.pi / 4
            tip = (c + math.cos(a) * r * 1.05, c + math.sin(a) * r * 1.05)
            pygame.draw.line(surf, theme.OUTLINE, (c, c), tip, max(2, int(r * 0.16)))
            pygame.draw.line(surf, glow, (c, c), tip, max(1, int(r * 0.08)))
        factory.circle(surf, eye, c, c, r * 0.18)


def _veins(surf, c: float, r: float, glow, count: int) -> None:
    """Жилки линиями от центра: тонкие, с низкой яркостью (plan/09 §4)."""
    for i in range(count):
        a = math.tau * i / count + 0.3
        mid = (c + math.cos(a) * r * 0.45, c + math.sin(a) * r * 0.45)
        end = (c + math.cos(a + 0.3) * r * 0.95, c + math.sin(a + 0.3) * r * 0.95)
        pygame.draw.line(surf, theme.mix(theme.FOE_FLESH, glow, 0.55), (c, c), mid,
                         max(1, int(r * 0.07)))
        pygame.draw.line(surf, theme.mix(theme.FOE_FLESH, glow, 0.35), mid, end,
                         max(1, int(r * 0.05)))


def draw_infection_patch(surf: pygame.Surface, s: float, seed: int, density: float,
                         phase: float) -> None:
    """Пятно заражения на земле (plan/09 §4): неровный край, жилки, пульсация.

    Рисуется в кэш чанка, поэтому форма зависит только от `seed` тайла, а
    пульсация — от `phase` (её передаёт тот, кто рисует слой заражения).
    """
    rng = random.Random(seed)
    c = s / 2.0
    count = 14
    pts = []
    for i in range(count):
        a = math.tau * i / count
        radius = s * (0.30 + 0.22 * density) * (0.72 + rng.random() * 0.5)
        radius *= 1.0 + 0.04 * math.sin(phase + i)
        pts.append((c + math.cos(a) * radius, c + math.sin(a) * radius))
    layer = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    pygame.draw.polygon(layer, theme.with_alpha(theme.INFECT_DARK, 210), pts)
    pygame.draw.polygon(layer, theme.with_alpha(theme.INFECT_MID,
                                                int(120 * (0.4 + density))),
                        [(c + (x - c) * 0.55, c + (y - c) * 0.55) for x, y in pts])
    surf.blit(layer, (0, 0))
    pygame.draw.polygon(surf, theme.mix(theme.INFECT_DARK, theme.INFECT_VEIN, 0.4),
                        pts, max(1, int(s * 0.02)))
    _veins(surf, c, s * 0.30 * (0.7 + density), theme.INFECT_VEIN, 4)


def _grow(pts, c: float, k: float, pad: float):
    out = []
    for x, y in pts:
        dx, dy = x - c, y - c
        length = math.hypot(dx, dy) or 1.0
        out.append((c + dx * k + dx / length * pad, c + dy * k + dy / length * pad))
    return out
