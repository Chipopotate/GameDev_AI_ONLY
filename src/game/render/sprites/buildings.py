"""Постройки игрока (plan/09 §5).

Общий язык форм: квадратная основа с толстым тёмным контуром и фаской, внутри
панель, на панели «деталь по роли» цветом здания, по краю неоновая линия.
Размер и силуэт читаются с первого взгляда — это важнее красоты, потому что
игра про автоматизацию: игрок смотрит на поток предметов, а не на картинку.

Спрайт делится на две части:

- **основа** кэшируется (`factory.get`) и рисуется один раз на здание, размер
  и состояние — свет, тьма, зум на неё уже не влияют;
- **живая часть** (шевроны конвейера, ротор бура, стрела сборщика, пульс ядра)
  рисуется каждый кадр: она зависит от `anim_time` и состояния здания.

Состояния (plan/09 §5): `on` — работает, `off` — нет питания (тусклое и с
мигающим значком), `plan` — заготовка при стройке (контур-«голограмма»),
`damaged` — трещины по числу стадий.
"""

from __future__ import annotations

import math

import pygame

from src.game.data.buildings import (ASSEMBLER, CONVEYOR, DRILL, GENERATOR,
                                     LAB, LAMP, STORAGE, TURRET, WALL, WIRE)
from src.game.render import theme
from src.game.render.sprites import factory

STATE_ON = "on"
STATE_OFF = "off"
STATE_PLAN = "plan"
#: Стадий трещин по HP (plan/09 §5): 0 — целое здание.
DAMAGE_STAGES = 3

#: Цвет «детали по роли»: он же цвет здания в данных, но чуть поднятый —
#: деталь должна светиться на панели, а не сливаться с ней.
ROLE_COLOR: dict[str, tuple[int, int, int]] = {
    LAMP: theme.NEON_AMBER,
    LAB: theme.NEON_CYAN,
    DRILL: theme.NEON_AMBER,
    CONVEYOR: theme.STEEL_LIGHT,
    WIRE: theme.NEON_AZURE,
    STORAGE: theme.STEEL_LIGHT,
    ASSEMBLER: theme.hue_shift(theme.NEON_CYAN, 60),
    GENERATOR: theme.NEON_AMBER,
    TURRET: theme.NEON_CYAN,
    WALL: theme.STEEL_LIGHT,
}


def _layer(size: tuple[int, int]) -> pygame.Surface:
    """Прозрачный слой: полупрозрачные элементы рисуются на нём и блитятся.

    `pygame.draw` на SRCALPHA-холсте **заменяет** пиксель вместе с альфой, а не
    смешивает, поэтому «стекло» и «голограмму» рисуем на отдельном слое.
    """
    return pygame.Surface(size, pygame.SRCALPHA)


def _rect(s: float, inset: float) -> pygame.Rect:
    m = s * inset
    side = s - 2 * m
    return pygame.Rect(int(m), int(m), int(side), int(side))


# ------------------------------------------------------------------ спрайты
def draw_building(surf: pygame.Surface, s: float, kind: str, state: str,
                  damage: int = 0) -> None:
    """Основа постройки. Кэшируется по `(kind, state, damage, size)`."""
    body = theme.STEEL_MID
    role = ROLE_COLOR.get(kind, theme.NEON_CYAN)
    if state == STATE_PLAN:
        _draw_blueprint(surf, s, role)
        return
    if state == STATE_OFF:
        body = theme.mix(body, theme.POWER_OFF, 0.45)
        role = theme.mix(role, theme.POWER_OFF, 0.7)
    _DRAW[kind](surf, s, body, role)
    if damage > 0:
        _draw_cracks(surf, s, min(damage, DAMAGE_STAGES))
    # Значка «нет питания» в основе нет: соседняя постройка, лампа или куча
    # предметов, нарисованные позже, закрывали его. Рендер кладёт значок
    # отдельным проходом поверх всего мира (`Renderer._draw_status_marks`).


def _draw_blueprint(surf: pygame.Surface, s: float, role) -> None:
    """Заготовка при стройке: контур-«голограмма» (plan/09 §5)."""
    rect = _rect(s, 0.06)
    glow = _layer(surf.get_size())
    pygame.draw.rect(glow, theme.with_alpha(role, 46), rect, border_radius=int(s * 0.1))
    pygame.draw.rect(glow, theme.with_alpha(role, 190), rect, max(1, int(s * 0.035)),
                     border_radius=int(s * 0.1))
    surf.blit(glow, (0, 0))
    # Уголки: у голограммы видны только «кронштейны», а не сплошная рамка.
    arm = s * 0.16
    for cx, sx_ in ((rect.left, 1), (rect.right, -1)):
        for cy, sy_ in ((rect.top, 1), (rect.bottom, -1)):
            pygame.draw.line(surf, theme.lighten(role, 0.4),
                             (cx, cy), (cx + arm * sx_, cy), max(1, int(s * 0.045)))
            pygame.draw.line(surf, theme.lighten(role, 0.4),
                             (cx, cy), (cx, cy + arm * sy_), max(1, int(s * 0.045)))


def _draw_cracks(surf: pygame.Surface, s: float, stage: int) -> None:
    """Трещины по стадиям HP (plan/09 §5): тёмные ломаные линии."""
    rng_like = ((0.32, 0.18), (0.68, 0.44), (0.46, 0.72))
    for i in range(stage):
        x0, y0 = rng_like[i]
        pts = [(s * x0, s * y0), (s * (x0 + 0.08), s * (y0 + 0.14)),
               (s * (x0 - 0.05), s * (y0 + 0.26))]
        pygame.draw.lines(surf, theme.darken(theme.STEEL_DARK, 0.5), False, pts,
                          max(1, int(s * 0.025)))


def draw_no_power_mark(surf: pygame.Surface, s: float, bright: bool = True) -> None:
    """Значок «нет питания»: тёмный кружок с красной каймой и молнией.

    Отдельный спрайт на весь холст, а не часть основы здания: рендер кладёт
    его поверх всего мира и мигает им, чередуя яркий и тусклый варианты.
    """
    c = s / 2.0
    r = s * 0.44
    edge = theme.POWER_MISSING if bright else theme.darken(theme.POWER_MISSING, 0.45)
    factory.circle(surf, theme.OUTLINE, c, c, r)
    factory.circle(surf, edge, c, c, r * 0.86)
    factory.circle(surf, theme.darken(theme.POWER_MISSING, 0.78), c, c, r * 0.68)
    bolt = [(c - r * 0.10, c - r * 0.58), (c + r * 0.34, c - r * 0.58),
            (c + r * 0.06, c - r * 0.08), (c + r * 0.30, c - r * 0.08),
            (c - r * 0.20, c + r * 0.62), (c - r * 0.02, c + r * 0.06),
            (c - r * 0.26, c + r * 0.06)]
    pygame.draw.polygon(surf, theme.OUTLINE, factory.pad(bolt, c, c, s * 0.03))
    pygame.draw.polygon(surf, theme.lighten(edge, 0.25) if bright else edge, bolt)


# ------------------------------------------------------------ виды построек
def _draw_lamp(surf: pygame.Surface, s: float, body, role) -> None:
    """Лампа: тяжёлое кольцо-основание и янтарное ядро (plan/09 §5)."""
    c = s / 2.0
    factory.circle(surf, theme.OUTLINE, c, c, s * 0.42)
    factory.circle(surf, theme.darken(body, 0.35), c, c, s * 0.38)
    factory.circle(surf, body, c, c, s * 0.32)
    # Рёбра радиатора по кругу — лампа греется.
    for i in range(8):
        a = math.tau * i / 8
        pygame.draw.line(surf, theme.darken(body, 0.45),
                         (c + math.cos(a) * s * 0.30, c + math.sin(a) * s * 0.30),
                         (c + math.cos(a) * s * 0.38, c + math.sin(a) * s * 0.38),
                         max(1, int(s * 0.03)))
    factory.circle(surf, theme.darken(role, 0.55), c, c, s * 0.24)
    factory.circle(surf, role, c, c, s * 0.20)
    factory.circle(surf, theme.lighten(role, 0.7), c - s * 0.012, c - s * 0.012, s * 0.10)


def _draw_lab(surf: pygame.Surface, s: float, body, role) -> None:
    """Лаборатория: панель и кольцо анализатора (линия-сканер — в живом слое)."""
    rect = _rect(s, 0.08)
    factory.bevel_frame(surf, rect, body, s * 0.10)
    panel = rect.inflate(-rect.width * 0.30, -rect.height * 0.30)
    pygame.draw.rect(surf, theme.darken(body, 0.55), panel, border_radius=int(s * 0.06))
    factory.neon_edge(surf, rect.inflate(-s * 0.03, -s * 0.03), role, s * 0.09,
                      max(1, s * 0.022))
    factory.rivets(surf, rect, theme.lighten(body, 0.35), s * 0.075)
    c = s / 2.0
    factory.ring(surf, theme.darken(role, 0.4), c, c, s * 0.20, s * 0.035)
    factory.circle(surf, theme.darken(body, 0.65), c, c, s * 0.14)


def _draw_drill(surf: pygame.Surface, s: float, body, role) -> None:
    """Бур: рама с четырьмя опорами. Ротор вращается в живом слое."""
    rect = _rect(s, 0.09)
    factory.bevel_frame(surf, rect, body, s * 0.12)
    c = s / 2.0
    factory.circle(surf, theme.OUTLINE, c, c, s * 0.34)
    factory.circle(surf, theme.darken(body, 0.62), c, c, s * 0.30)
    # Опоры по углам рамы — бур «упирается» в залежь.
    for sx_, sy_ in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = c + sx_ * s * 0.33
        y = c + sy_ * s * 0.33
        factory.circle(surf, theme.OUTLINE, x, y, s * 0.075)
        factory.circle(surf, theme.lighten(body, 0.18), x, y, s * 0.055)
    factory.neon_edge(surf, rect.inflate(-s * 0.035, -s * 0.035),
                      theme.mix(role, theme.NEON_WHITE, 0.2), s * 0.10,
                      max(1, s * 0.02))


def _draw_conveyor(surf: pygame.Surface, s: float, body, role) -> None:
    """Конвейер: тёмная лента с бортами. Шевроны — в живом слое."""
    del role
    rect = _rect(s, 0.06)
    factory.bevel_frame(surf, rect, theme.darken(body, 0.25), s * 0.14)
    belt = rect.inflate(-s * 0.10, -s * 0.10)
    pygame.draw.rect(surf, theme.CONVEYOR_BELT, belt, border_radius=int(s * 0.06))
    pygame.draw.rect(surf, theme.OUTLINE, belt, max(1, int(s * 0.02)),
                     border_radius=int(s * 0.06))
    # Поперечные ролики: лента читается как лента даже без шевронов.
    for i in range(1, 3):
        x = belt.left + belt.width * i / 3.0
        pygame.draw.line(surf, theme.darken(body, 0.55), (x, belt.top + s * 0.02),
                         (x, belt.bottom - s * 0.02), max(1, int(s * 0.018)))


#: Стороны провода в порядке маски: восток, юг, запад, север (как `DIRECTIONS`).
WIRE_SIDES = ((1, 0), (0, 1), (-1, 0), (0, -1))
#: Толщина кабеля и оболочки, в долях тайла.
WIRE_CORE = 0.075
WIRE_SHEATH = 0.17


def _draw_wire(surf: pygame.Surface, s: float, body, role) -> None:
    """Провод без соседей (галерея, превью): узел и короткий кабель по оси."""
    draw_wire(surf, s, (True, False, True, False), role, body)


def draw_wire(surf: pygame.Surface, s: float, mask: tuple[bool, ...], role,
              body=theme.STEEL_MID) -> None:
    """Провод по маске соседей (plan/09 §5: «тонкая неоновая линия»).

    Кабель идёт от центра **до самого края тайла** в сторону каждого соседа по
    сети, поэтому кабели двух соседних проводов сходятся в одну линию, а кабель
    к зданию упирается в его корпус. Пока линии рисовал рендер по полтайла и
    «один раз на пару», между проводами оставался разрыв, а линии ложились под
    соседние постройки. Маска — 4 стороны, вариантов 16, все в кэше.
    """
    c = s / 2.0
    sheath = s * WIRE_SHEATH
    core = s * WIRE_CORE
    for (dx, dy), on in zip(WIRE_SIDES, mask):
        if not on:
            continue
        end = (c + dx * s, c + dy * s)       # за край: сглаживание не оставит щели
        pygame.draw.line(surf, theme.OUTLINE, (c, c), end, int(sheath))
        pygame.draw.line(surf, theme.darken(body, 0.35), (c, c), end, int(sheath * 0.62))
        pygame.draw.line(surf, theme.darken(role, 0.15), (c, c), end, max(1, int(core)))
    # Узел: маленькая стальная муфта с неоновым ядром — сеть «держится» за неё.
    rect = _rect(s, 0.30)
    factory.bevel_frame(surf, rect, theme.darken(body, 0.25), s * 0.10)
    factory.circle(surf, theme.OUTLINE, c, c, s * 0.12)
    factory.circle(surf, theme.darken(role, 0.45), c, c, s * 0.095)
    factory.circle(surf, theme.lighten(role, 0.35), c, c, s * 0.05)


def _draw_storage(surf: pygame.Surface, s: float, body, role) -> None:
    """Склад: ящик с крышкой и угловыми накладками (заполнение — в живом слое)."""
    rect = _rect(s, 0.08)
    factory.bevel_frame(surf, rect, body, s * 0.07)
    lid = pygame.Rect(rect.left, rect.top + rect.height * 0.26,
                      rect.width, rect.height * 0.16)
    pygame.draw.rect(surf, theme.darken(body, 0.42), lid)
    pygame.draw.rect(surf, theme.lighten(body, 0.22), lid, max(1, int(s * 0.02)))
    for sx_, sy_ in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = rect.centerx + sx_ * rect.width * 0.40
        y = rect.centery + sy_ * rect.height * 0.40
        factory.circle(surf, theme.darken(body, 0.5), x, y, s * 0.045)
    factory.neon_edge(surf, rect.inflate(-s * 0.025, -s * 0.025), role, s * 0.07,
                      max(1, s * 0.018))


def _draw_assembler(surf: pygame.Surface, s: float, body, role) -> None:
    """Сборщик: корпус-шестигранник с гнездом (стрела-манипулятор — в живом слое)."""
    c = s / 2.0
    pts = factory.polygon_points(c, c, s * 0.40, 6, -math.pi / 2)
    pygame.draw.polygon(surf, theme.OUTLINE, pts)
    pygame.draw.polygon(surf, theme.darken(body, 0.30), pts)
    inner = factory.polygon_points(c, c, s * 0.31, 6, -math.pi / 2)
    pygame.draw.polygon(surf, body, inner)
    factory.circle(surf, theme.darken(body, 0.62), c, c, s * 0.17)
    factory.ring(surf, role, c, c, s * 0.24, max(1, s * 0.022))


def _draw_generator(surf: pygame.Surface, s: float, body, role) -> None:
    """Генератор: корпус с рёбрами радиатора и янтарным ядром."""
    rect = _rect(s, 0.08)
    factory.bevel_frame(surf, rect, body, s * 0.08)
    # Рёбра радиатора: вертикальные полосы по бокам панели.
    for i in range(4):
        x = rect.left + rect.width * (0.14 + i * 0.06)
        pygame.draw.line(surf, theme.darken(body, 0.45), (x, rect.top + s * 0.10),
                         (x, rect.bottom - s * 0.10), max(1, int(s * 0.022)))
        x = rect.right - rect.width * (0.14 + i * 0.06)
        pygame.draw.line(surf, theme.darken(body, 0.45), (x, rect.top + s * 0.10),
                         (x, rect.bottom - s * 0.10), max(1, int(s * 0.022)))
    c = s / 2.0
    factory.circle(surf, theme.OUTLINE, c, c, s * 0.24)
    factory.circle(surf, theme.darken(role, 0.6), c, c, s * 0.20)
    factory.circle(surf, role, c, c, s * 0.13)
    factory.neon_edge(surf, rect.inflate(-s * 0.03, -s * 0.03), role, s * 0.08,
                      max(1, s * 0.02))


def _draw_turret(surf: pygame.Surface, s: float, body, role) -> None:
    """Турель: статичная восьмигранная основа (голова со стволом — живой слой)."""
    c = s / 2.0
    pts = factory.polygon_points(c, c, s * 0.44, 8, math.pi / 8)
    pygame.draw.polygon(surf, theme.OUTLINE, pts)
    pygame.draw.polygon(surf, theme.darken(body, 0.25), pts)
    inner = factory.polygon_points(c, c, s * 0.34, 8, math.pi / 8)
    pygame.draw.polygon(surf, body, inner)
    factory.ring(surf, role, c, c, s * 0.30, max(1, s * 0.02))


def _draw_wall(surf: pygame.Surface, s: float, body, role) -> None:
    """Стена: массивный блок с фаской и бороздками (plan/09 §5)."""
    rect = _rect(s, 0.03)
    factory.bevel_frame(surf, rect, theme.lighten(body, 0.08), s * 0.04)
    for k in (0.36, 0.64):
        y = rect.top + rect.height * k
        pygame.draw.line(surf, theme.darken(body, 0.45), (rect.left + s * 0.08, y),
                         (rect.right - s * 0.08, y), max(1, int(s * 0.03)))


_DRAW = {
    LAMP: _draw_lamp,
    LAB: _draw_lab,
    DRILL: _draw_drill,
    CONVEYOR: _draw_conveyor,
    WIRE: _draw_wire,
    STORAGE: _draw_storage,
    ASSEMBLER: _draw_assembler,
    GENERATOR: _draw_generator,
    TURRET: _draw_turret,
    WALL: _draw_wall,
}


# ------------------------------------------------------- вращающиеся части
#: Угловых шагов у вращающихся частей (plan/09 §3.2): 32 хватает, чтобы ротор
#: вращался плавно, а кэш остался маленьким.
ANGLE_BUCKETS = 32


def nearest_bucket(angle: float) -> int:
    """Индекс углового шага, ближайшего к углу в радианах."""
    step = math.tau / ANGLE_BUCKETS
    return int(round((angle % math.tau) / step)) % ANGLE_BUCKETS


def draw_rotor(surf: pygame.Surface, s: float, angle: float, role, active: bool) -> None:
    """Ротор бура: три лопасти с зубцами. Кэшируется по угловому шагу."""
    c = s / 2.0
    color = role if active else theme.mix(role, theme.POWER_OFF, 0.55)
    for i in range(3):
        a = angle + math.tau * i / 3
        tip = (c + math.cos(a) * s * 0.30, c + math.sin(a) * s * 0.30)
        left = (c + math.cos(a + 0.42) * s * 0.12, c + math.sin(a + 0.42) * s * 0.12)
        right = (c + math.cos(a - 0.42) * s * 0.12, c + math.sin(a - 0.42) * s * 0.12)
        pygame.draw.polygon(surf, theme.OUTLINE, (tip, left, right))
        pygame.draw.polygon(surf, theme.darken(color, 0.25),
                            (_shrink(tip, c, 0.72), _shrink(left, c, 0.7),
                             _shrink(right, c, 0.7)))
    factory.circle(surf, theme.OUTLINE, c, c, s * 0.11)
    factory.circle(surf, theme.lighten(color, 0.35), c, c, s * 0.08)
    factory.circle(surf, theme.darken(theme.STEEL_DARK, 0.5), c, c, s * 0.035)


def _shrink(p, c, k):
    return (c + (p[0] - c) * k, c + (p[1] - c) * k)


def draw_scanner(surf: pygame.Surface, cx: float, cy: float, s: float, angle: float,
                 role) -> None:
    """Линия-сканер лаборатории вокруг центра `(cx, cy)` (plan/09 §5).

    Рисуется прямо в кадр, поэтому центр задаётся явно: раньше линия шла от
    `s / 2` — то есть от угла экрана, а не от лаборатории.
    """
    x = cx + math.cos(angle) * s * 0.22
    y = cy + math.sin(angle) * s * 0.22
    pygame.draw.line(surf, theme.lighten(role, 0.5), (cx, cy), (x, y), max(1, int(s * 0.05)))
    factory.circle(surf, theme.NEON_WHITE, x, y, s * 0.045)


def draw_arm(surf: pygame.Surface, cx: float, cy: float, s: float, angle: float,
             role, frac: float) -> None:
    """Стрела-манипулятор сборщика вокруг `(cx, cy)`: тянется к детали и назад."""
    reach = s * (0.12 + 0.12 * frac)
    x = cx + math.cos(angle) * reach
    y = cy + math.sin(angle) * reach
    pygame.draw.line(surf, theme.OUTLINE, (cx, cy), (x, y), max(2, int(s * 0.075)))
    pygame.draw.line(surf, theme.lighten(role, 0.2), (cx, cy), (x, y), max(1, int(s * 0.05)))
    factory.circle(surf, role, x, y, s * 0.055)
    factory.circle(surf, theme.lighten(role, 0.6), x, y, s * 0.025)


# ------------------------------------------------------------- живой слой
def draw_chevron(surf: pygame.Surface, s: float, color, angle: float) -> None:
    """Один шеврон конвейера, смотрящий по ходу движения (plan/09 §5).

    Кэшируется как спрайт, а «бег» делает уже рендер: он блитит три шеврона со
    смещением от `anim_time`. Так лента остаётся сглаженной (спрайт нарисован
    в 4× и сжат) и не создаёт ни одной поверхности в кадре.
    """
    c = s / 2.0
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def rot(px: float, py: float) -> tuple[float, float]:
        return (c + px * cos_a - py * sin_a, c + px * sin_a + py * cos_a)

    half = s * 0.20
    tip = rot(half * 1.4, 0.0)
    left = rot(-half, -half * 1.5)
    right = rot(-half, half * 1.5)
    # Шеврон — тот же треугольник, раздутый от центра: так у него появляется
    # тёмная обводка, и лента читается на любом фоне.
    outline = factory.pad([tip, left, right], c, c, s * 0.05)
    pygame.draw.polygon(surf, theme.CONVEYOR_CHEVRON, outline)
    pygame.draw.polygon(surf, color, (tip, left, right))


def draw_fill_bar(target: pygame.Surface, rect: pygame.Rect, frac: float,
                  color=theme.NEON_CYAN, bg=theme.MINE_BAR_BG) -> None:
    """Полоска заполнения на панели: сколько места занято в контейнере."""
    if rect.width <= 0 or rect.height <= 0:
        return
    pygame.draw.rect(target, bg, rect, border_radius=2)
    filled = int(rect.width * max(0.0, min(1.0, frac)))
    if filled > 0:
        pygame.draw.rect(target, color, (rect.left, rect.top, filled, rect.height),
                         border_radius=2)
    pygame.draw.rect(target, theme.OUTLINE, rect, 1, border_radius=2)
