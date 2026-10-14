"""Ресурсы: огранённые самоцветы (plan/09 §7).

Ресурс — не «одна из шести фигур», а **параметрическая огранка**: число вершин
5–9, разброс радиусов (от звезды до выпуклого камня), вытянутость, наклон и
иногда кластер из 2–3 кристаллов. Параметры берутся из сида, который выведен
из id ресурса, поэтому один и тот же забег всегда рисуется одинаково, а разные
забеги не похожи друг на друга.

Огранка рисуется как набор треугольников от центра к вершинам: каждая грань
темнее или светлее в зависимости от того, куда смотрит её нормаль относительно
света сверху-слева. Именно это, а не заливка одним цветом, даёт «камень».

Крафченые предметы ролей — не самоцветы: это стальные жетоны с неоновой
каймой. По картинке сразу видно, что лежит: найденное в земле или сделанное.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pygame

from src.game.render import theme
from src.game.render.sprites import factory

#: Источник света для граней: сверху-слева (plan/09 §7). Менять не нужно —
#: все объекты в игре освещены одинаково, иначе камни «смотрят» вразнобой.
LIGHT_DIR = (-0.62, -0.78)
#: Порог различимости: пара ресурсов ближе этого по оттенку **и** по силуэту
#: считается неудачной, и генератор перебрасывает форму (plan/09 §7).
#: IoU 0.82, а не 0.86: замер на 200 сидах показал, что самая похожая пара
#: забега даёт 0.83, то есть при 0.86 правило не срабатывало **ни разу** и
#: гарантия держалась на честном слове. При 0.82 перевыбор формы случается, а у
#: гарантии появляется запас.
HUE_MIN_DISTANCE = 0.06
SILHOUETTE_MAX_IOU = 0.82
#: Сколько раз форма перебрасывается, прежде чем принять «похожую»: забег не
#: должен зациклиться на поиске идеала.
REROLL_TRIES = 24

#: Размер маски силуэта для сравнения форм. 32×32 хватает: сравнение идёт по
#: крупным чертам (вытянутость, число лучей), а не по пикселям.
MASK_SIZE = 32
#: Насколько контур камня выходит за его грани, в долях радиуса (`draw_gem`).
#: Маска силуэта раздувается на столько же — иначе она описывает не тот
#: силуэт, который видит игрок.
OUTLINE_PAD = 0.16


@dataclass(frozen=True)
class GemShape:
    """Огранка одного ресурса."""

    #: Относительные радиусы вершин (0.45…1.0) и их углы в радианах.
    radii: tuple[float, ...]
    angles: tuple[float, ...]
    #: Наклон всего камня — камни одного залежа не должны стоять «по стойке».
    tilt: float
    #: Кластер: смещения и масштабы спутников вокруг главного кристалла.
    cluster: tuple[tuple[float, float, float], ...] = ()

    @property
    def vertices(self) -> tuple[int, ...]:
        return tuple(range(len(self.radii)))


def make_gem_shape(rng: random.Random) -> GemShape:
    """Случайная огранка. Число вершин, «звёздность» и вытянутость — отсюда."""
    count = rng.randint(5, 9)
    # «Звёздность»: 0 — выпуклый камень, 1 — колючая звезда. Разброс радиусов
    # вокруг среднего и есть эта шкала.
    spikiness = rng.uniform(0.0, 0.75)
    # Две сферы влияния: у звезды лучи чередуются, у камня — плавно.
    phases = rng.uniform(0.0, math.tau)
    radii: list[float] = []
    angles: list[float] = []
    for i in range(count):
        base = math.tau * i / count + rng.uniform(-0.22, 0.22) * (math.tau / count)
        wave = math.sin(phases + i * (math.pi if spikiness > 0.5 else 0.9))
        radii.append(max(0.42, min(1.0, 0.78 + 0.28 * spikiness * wave
                                   + rng.uniform(-0.10, 0.10))))
        angles.append(base)
    # Вытянутость: камень вытягивается по случайной оси — так силуэты
    # различаются даже при одинаковом числе вершин.
    stretch = rng.uniform(1.0, 1.35)
    axis = rng.uniform(0.0, math.pi)
    cos_a, sin_a = math.cos(axis), math.sin(axis)
    out_r: list[float] = []
    out_a: list[float] = []
    for r, a in zip(radii, angles):
        x, y = math.cos(a) * r, math.sin(a) * r
        x *= stretch
        # обратный поворот: вытянутость вдоль оси `axis`
        rx = x * cos_a - y * sin_a
        ry = x * sin_a + y * cos_a
        out_r.append(min(1.0, math.hypot(rx, ry)))
        out_a.append(math.atan2(ry, rx))

    cluster: tuple[tuple[float, float, float], ...] = ()
    if rng.random() < 0.55:
        satellites = rng.randint(2, 3)
        cluster = tuple(
            (math.cos(math.tau * k / satellites + rng.uniform(-0.3, 0.3)) * rng.uniform(0.42, 0.60),
             math.sin(math.tau * k / satellites + rng.uniform(-0.3, 0.3)) * rng.uniform(0.32, 0.52),
             rng.uniform(0.38, 0.56))
            for k in range(satellites)
        )
    return GemShape(tuple(out_r), tuple(out_a), rng.uniform(0.0, math.tau), cluster)


# ------------------------------------------------------------------ сравнение
def _shape_mask(shape: GemShape, size: int = MASK_SIZE) -> list[bool]:
    """Растр силуэта: нужен, чтобы понять, похожи ли два камня (plan/09 §13).

    Маска обязана повторять **нарисованный** силуэт, иначе правило проверяет
    одно, а игрок видит другое: контур обводки раздувает камень на
    `OUTLINE_PAD` от радиуса, и это раздутие сближает похожие формы (IoU
    подрастает). На замере из-за этого расхождения восемь пар из 200 сидов
    проходили внутреннюю проверку и всё равно выглядели похожими.
    """
    mask = [False] * (size * size)
    c = size / 2.0
    r = c * 0.92
    pts = []
    for radius, angle in zip(shape.radii, shape.angles):
        x, y = math.cos(angle + shape.tilt) * radius * r, math.sin(angle + shape.tilt) * radius * r
        pts.append((c + x, c + y))
    _rasterize(mask, size, pts)
    for dx, dy, scale in shape.cluster:
        sub = []
        for radius, angle in zip(shape.radii, shape.angles):
            x = math.cos(angle + shape.tilt) * radius * r * scale
            y = math.sin(angle + shape.tilt) * radius * r * scale
            sub.append((c + dx * r + x, c + dy * r + y))
        _rasterize(mask, size, sub)
    return _dilate(mask, size, max(1, int(round(OUTLINE_PAD * r))))


def _dilate(mask: list[bool], size: int, k: int) -> list[bool]:
    """Раздувает маску на `k` пикселей — обводка контура (`draw_gem`)."""
    out = list(mask)
    for row in range(size):
        for col in range(size):
            if not mask[row * size + col]:
                continue
            for dy in range(-k, k + 1):
                for dx in range(-k, k + 1):
                    if dx * dx + dy * dy > k * k:
                        continue
                    y, x = row + dy, col + dx
                    if 0 <= y < size and 0 <= x < size:
                        out[y * size + x] = True
    return out


def _rasterize(mask: list[bool], size: int, pts: list[tuple[float, float]]) -> None:
    """Заливка выпуклого/звёздчатого многоугольника в маску (scanline по строкам)."""
    if len(pts) < 3:
        return
    for row in range(size):
        y = row + 0.5
        crossings: list[float] = []
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            if (y0 <= y < y1) or (y1 <= y < y0):
                crossings.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        crossings.sort()
        for k in range(0, len(crossings) - 1, 2):
            left, right = crossings[k], crossings[k + 1]
            for col in range(max(0, int(left)), min(size, int(right) + 1)):
                mask[row * size + col] = True


def mask_iou(a: list[bool], b: list[bool]) -> float:
    """Пересечение силуэтов поверх объединения, 0..1."""
    inter = union = 0
    for x, y in zip(a, b):
        if x or y:
            union += 1
            if x and y:
                inter += 1
    return inter / union if union else 1.0


def hue_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Расстояние по оттенку, 0..0.5 (в единицах цветового круга)."""
    import colorsys
    ha = colorsys.rgb_to_hsv(*(c / 255.0 for c in a))[0]
    hb = colorsys.rgb_to_hsv(*(c / 255.0 for c in b))[0]
    d = abs(ha - hb)
    return min(d, 1.0 - d)


class GemFactory:
    """Огранки ресурсов забега: по одной на ресурс, взаимно различимые.

    Уникальность (plan/09 §7) — здесь, а не в симуляции: форма спрайта это
    решение отрисовки. Ресурс, который оказался слишком похож на уже собранные
    (близкий оттенок **и** похожий силуэт), получает другую форму.
    """

    def __init__(self, items=None) -> None:
        self._shapes: dict[str, GemShape] = {}
        self._masks: dict[str, list[bool]] = {}
        self.rerolls = 0
        if items is not None:
            self._build(items)

    def _build(self, items) -> None:
        """Собирает формы для природных ресурсов забега по порядку таблицы."""
        accepted: list[tuple[GemShape, list[bool], tuple[int, int, int]]] = []
        for item in items.natural:
            shape, mask = self._pick(item, accepted)
            self._shapes[item.id] = shape
            self._masks[item.id] = mask
            accepted.append((shape, mask, item.color))

    def _pick(self, item, accepted) -> tuple[GemShape, list[bool]]:
        rng = random.Random(_seed_of(item.id))
        best: tuple[GemShape, list[bool]] | None = None
        for attempt in range(REROLL_TRIES + 1):
            shape = make_gem_shape(rng)
            mask = _shape_mask(shape)
            too_close = False
            for other_shape, other_mask, other_color in accepted:
                del other_shape
                same_hue = hue_distance(item.color, other_color) < HUE_MIN_DISTANCE
                if same_hue and mask_iou(mask, other_mask) > SILHOUETTE_MAX_IOU:
                    too_close = True
                    break
            if not too_close:
                if attempt:
                    self.rerolls += 1
                return shape, mask
            best = (shape, mask)
        assert best is not None
        return best

    def shape(self, item_id: str) -> GemShape:
        """Огранка предмета. Для крафченого жетона — форма-заглушка."""
        shape = self._shapes.get(item_id)
        if shape is None:
            shape = make_gem_shape(random.Random(_seed_of(item_id)))
            self._shapes[item_id] = shape
            self._masks[item_id] = _shape_mask(shape)
        return shape

    def mask(self, item_id: str) -> list[bool]:
        self.shape(item_id)
        return self._masks[item_id]

    def outlines(self) -> dict[str, GemShape]:
        """Все собранные огранки — для галереи и тестов (plan/09 §12–13)."""
        return dict(self._shapes)


def _seed_of(item_id: str) -> int:
    """Стабильный сид из id предмета: одна и та же картинка во всех процессах.

    `hash()` в Python солится на каждый запуск, поэтому id сворачивается
    вручную — иначе форма камня менялась бы между запусками и скриншотами.
    """
    value = 2166136261
    for ch in item_id:
        value = ((value ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return value


# ------------------------------------------------------------------ рисование
def draw_gem(surf: pygame.Surface, s: float, shape: GemShape, color,
             glow: bool = False) -> None:
    """Рисует огранённый самоцвет. `s` — сторона холста в пикселях."""
    c = s / 2.0
    r = s * 0.40
    _draw_crystal(surf, c, c, r, shape, color, shape.tilt, glow=glow)
    for dx, dy, scale in shape.cluster:
        _draw_crystal(surf, c + dx * r, c + dy * r, r * scale, shape, color,
                      shape.tilt + 0.7, glow=False)


def _draw_crystal(surf: pygame.Surface, cx: float, cy: float, r: float,
                  shape: GemShape, color, tilt: float, glow: bool) -> None:
    """Один кристалл: контур, грани от центра, блик на верхней грани."""
    base = theme.darken(color, 0.25)
    pts = [(cx + math.cos(a + tilt) * rad * r, cy + math.sin(a + tilt) * rad * r)
           for a, rad in zip(shape.angles, shape.radii)]
    # Тёмный контур: без него камень теряется на тёмной земле.
    pygame.draw.polygon(surf, theme.OUTLINE, _grow(pts, cx, cy, 1.0, r * OUTLINE_PAD))
    # Грани: треугольник «центр → вершина → следующая вершина». Яркость грани
    # считается по направлению её нормали к свету сверху-слева.
    n = len(pts)
    brightest = (0.0, 0)
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        mx, my = (p0[0] + p1[0]) / 2.0 - cx, (p0[1] + p1[1]) / 2.0 - cy
        length = math.hypot(mx, my) or 1.0
        lam = (mx / length) * LIGHT_DIR[0] + (my / length) * LIGHT_DIR[1]
        k = 0.52 + 0.48 * (lam + 1.0) / 2.0
        pygame.draw.polygon(surf, theme.scale(base, k), (pts[i], p1, (cx, cy)))
        if lam > brightest[0]:
            brightest = (lam, i)
    # Блик на верхней грани и тёмная кромка снизу-справа — объём.
    if brightest[0] > 0.25:
        i = brightest[1]
        p0, p1 = pts[i], pts[(i + 1) % n]
        pygame.draw.polygon(surf, theme.lighten(color, 0.55),
                            (_mix(p0, (cx, cy), 0.55), _mix(p1, (cx, cy), 0.55),
                             _mix(_mix(p0, p1, 0.5), (cx, cy), 0.35)))
    pygame.draw.polygon(surf, theme.darken(base, 0.45), pts, max(1, int(r * 0.07)))
    if glow:
        # Ресурс с известным свойством ✨ слегка светится (plan/09 §7). Свечение
        # рисуется непрозрачным светлым ядром: холст спрайта не умеет
        # полупрозрачность — pygame.draw на SRCALPHA заменяет пиксель целиком.
        factory.circle(surf, theme.lighten(color, 0.7), cx, cy - r * 0.15, r * 0.20)


def _grow(pts, cx, cy, k, pad_):
    """Раздувает многоугольник от центра: подложка контура (см. `factory.pad`)."""
    return factory.pad(pts, cx, cy, pad_, k)


def _mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def draw_chip(surf: pygame.Surface, s: float, color) -> None:
    """Крафченый предмет роли: стальной жетон с неоновой каймой.

    Свойства у него нет, добывать его нельзя — и выглядеть как самоцвет он не
    должен. Правило plan/09 §1 «порядок против заражения» работает и внутри
    инвентаря: найденное — камень, сделанное — деталь.
    """
    c = s / 2.0
    r = s * 0.36
    pts = factory.polygon_points(c, c, r, 6, -math.pi / 2)
    pygame.draw.polygon(surf, theme.OUTLINE, _grow(pts, c, c, 1.0, s * 0.05))
    pygame.draw.polygon(surf, theme.darken(color, 0.45), pts)
    pygame.draw.polygon(surf, theme.lighten(color, 0.15),
                        [(x * 0.34 + c * 0.66, y * 0.34 + c * 0.66) for x, y in pts])
    inner = factory.polygon_points(c, c, r * 0.52, 6, -math.pi / 2)
    pygame.draw.polygon(surf, theme.scale(theme.lighten(color, 0.5), 0.9), inner)
    pygame.draw.polygon(surf, theme.OUTLINE, pts, max(1, int(s * 0.035)))
    # Неоновая кайма поверх — жетон читается в темноте.
    pygame.draw.polygon(surf, theme.lighten(color, 0.75),
                        factory.polygon_points(c, c, r * 1.02, 6, -math.pi / 2),
                        max(1, int(s * 0.022)))
