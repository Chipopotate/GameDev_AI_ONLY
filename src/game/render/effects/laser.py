"""Луч дрона: от носа к цели, пока игрок держит действие (plan/09 §10).

Что рисовать, решает **только** `drone.action` из симуляции: вид действия,
точка цели и прогресс. Рендер ничего не выясняет сам — ни «добывает ли дрон»,
ни «в радиусе ли цель»: если действие есть, оно уже проверено симуляцией.

Цвет — по таблице `theme.ACTION_COLORS`:

- **красный** (добыча, разбор) «жжёт»: яркое ядро, широкий ореол, по лучу бегут
  волны яркости, толщина чуть дрожит; в точке попадания — пятно и искры,
  пульс которых растёт вместе с прогрессом;
- **синий** (изучение, скан) «читает»: тоньше и прозрачнее, мягко «дышит», от
  цели к дрону плывут светящиеся точки — поток данных, у цели — кольцо-сканер;
  по яркости видно, сколько осталось.

Луч рисуется **аддитивно поверх тьмы** (после затемнения кадра), поэтому он
виден и ночью. Тело луча — не цепочка ореолов, а отрезки линий в собственном
буфере свечения: цепочка мелких ореолов на обычном зуме распадалась на
«бусины», а линия непрерывна при любом зуме. Буфер один на всё время жизни
(как emissive-буфер рендера) — после прогрева в кадре не создаётся ни одной
`Surface`; в кадре он только чистится в рамке луча и прибавляется к экрану.
На `low` — простая линия без ореола.

Своё время у луча — сумма `dt`, которые ему дали: на паузе рендер даёт ноль, и
картинка замирает (луч виден, но волны стоят), как и требует plan/09.
"""

from __future__ import annotations

import math
import random

import pygame

from src.game import config
from src.game.render import quality, theme
from src.game.render.camera import Camera
from src.game.render.effects import SPARK
from src.game.sim.units import ACTION_DISMANTLE, ACTION_MINE, DroneAction

#: Красные («жгущие») действия. Остальные — синие («читающие»).
HOT_ACTIONS = frozenset({ACTION_MINE, ACTION_DISMANTLE})
#: Длина отрезка луча в долях тайла: по отрезкам бежит волна яркости. Короче —
#: плавнее волна и дороже кадр; длиннее — волна идёт ступенями.
SEGMENT = 0.14
SEGMENT_MIN_PX = 3
#: Красный луч: доля яркости ореола и среднего слоя (ядро — 1.0). Тело луча
#: почти не зависит от прогресса: оно «жжёт» всё время, а растёт пульс пятна.
HOT_HALO = 0.32
HOT_MID = 0.72
HOT_BODY_GAIN = (0.8, 1.0)
#: Синий луч тоньше и прозрачнее красного (plan/09: «альфа около 40–60%»):
#: аддитивная яркость и есть прозрачность — сквозь луч видно землю. Ядро
#: подмешивает белого лишь на долю `COOL_WHITE`: почти белое ядро на светлой
#: земле читалось серым, а не синим, и путалось с красным лучом.
COOL_WIDTH = 0.6
COOL_HALO = 0.5
COOL_CORE = 0.7
COOL_WHITE = 0.4
COOL_RING = 0.9
#: Насколько синий луч «дышит».
BREATH_DEPTH = 0.25
#: Скорость бегущих волн красного луча, тайлов в секунду.
WAVE_SPEED = 6.0
#: Частота дрожания толщины, Гц (две несоизмеримые — без заметного ритма).
JITTER_HZ = (23.0, 37.0)
#: Шаг точек «потока данных» вдоль синего луча, тайлы.
FLOW_SPACING = 0.42
FLOW_RADIUS = 0.07         # радиус точки потока, доля тайла
#: Пятно попадания: радиус в долях тайла и прибавка от прогресса. Пятно меньше
#: цели (залежь — ~0.8 тайла): луч показывает, **куда** бьёт, но не закрывает,
#: **во что** — по камню видно, что именно добывается.
IMPACT_RADIUS = 0.12
IMPACT_GROWTH = 0.06
#: Мягкий ореол пятна из общего кэша свечения: во сколько раз шире пятна и
#: насколько ярок. Он лишь смягчает край — основное пятно рисуется в буфере.
IMPACT_BLOOM = 1.8
IMPACT_BLOOM_GAIN = 0.35
#: Частота пульса пятна: от спокойной к частой по мере прогресса, Гц.
IMPACT_PULSE = (3.0, 9.0)
#: Ступеней яркости ореола пятна: плавная яркость дала бы новую поверхность.
BLOOM_STEPS = 8
#: Кольцо-сканер у цели синего луча: радиус и размах «дыхания» (доли тайла),
#: толщина линии в долях тайла.
RING_RADIUS = 0.42
RING_SWING = 0.10
RING_WIDTH = 0.06
#: Искры попадания красного луча: скорость, время жизни, размер (в тайлах).
#: Мелкие: пятно и так яркое, крупные искры сливались бы с ним в один блин.
SPARK_SPEED = (0.8, 2.2)
SPARK_TTL = (0.18, 0.40)
SPARK_SIZE = 0.3
#: Ширина «простой линии» на `low`, пиксели.
LOW_WIDTH = 2


class LaserBeam:
    """Состояние и отрисовка луча. Один на рендер, живёт между кадрами."""

    def __init__(self, factory, pool=None, seed: int = 0) -> None:
        # Фабрика — общий кэш свечения (`SpriteFactory.halo`): им смягчается
        # край пятна попадания. Тело луча рисуется в собственном буфере.
        self.factory = factory
        self.pool = pool
        self.rng = random.Random(seed ^ 0x1A5E2)
        #: 0..1 — плавное появление и угасание (🔧 `LASER_FADE_TIME`).
        self.intensity = 0.0
        self.kind: str | None = None
        self.target: tuple[float, float] | None = None
        self.progress = 0.0
        self.time = 0.0
        self._spark_debt = 0.0
        #: Буфер свечения размером с экран: чёрный — ноль для `BLEND_RGB_ADD`.
        self._layer: pygame.Surface | None = None
        #: Сколько светящихся элементов (отрезков, пятен, точек) нарисовано в
        #: последнем кадре — для тестов: 0 значит «луча не было».
        self.dots = 0

    # --------------------------------------------------------------- шаг
    @property
    def visible(self) -> bool:
        return self.intensity > 0.0 and self.target is not None

    @property
    def hot(self) -> bool:
        return self.kind in HOT_ACTIONS

    def update(self, action: DroneAction, dt: float) -> None:
        """Следит за действием дрона. `dt == 0` — пауза: всё замирает."""
        dt = max(0.0, dt)
        fade = max(1e-6, config.LASER_FADE_TIME)
        if action.active:
            # Новое действие сразу забирает луч: цвет не «перетекает» от
            # добычи к скану, а цель — от одной залежи к другой.
            self.kind = action.kind
            self.target = action.target_world_pos
            self.progress = max(0.0, min(1.0, action.progress))
            self.intensity = min(1.0, self.intensity + dt / fade)
        else:
            self.intensity = max(0.0, self.intensity - dt / fade)
            if self.intensity <= 0.0:
                self.target = None
        self.time += dt
        if dt > 0.0 and self.visible and self.hot:
            self._spawn_sparks(dt)

    def _spawn_sparks(self, dt: float) -> None:
        """Искры в точке попадания: чем дальше работа, тем их больше."""
        if self.pool is None or self.target is None:
            return
        if not quality.glow_enabled():
            return                     # `low`: без свечения — и без искр луча
        rate = config.LASER_SPARK_HZ * self.intensity * (0.5 + 0.5 * self.progress)
        self._spark_debt += rate * dt
        tx, ty = self.target
        color = theme.laser_core(self.kind)
        while self._spark_debt >= 1.0:
            self._spark_debt -= 1.0
            a = self.rng.uniform(0.0, math.tau)
            v = self.rng.uniform(*SPARK_SPEED)
            self.pool.spawn(SPARK, tx, ty, math.cos(a) * v, math.sin(a) * v,
                            self.rng.uniform(*SPARK_TTL), SPARK_SIZE, color, drag=2.4)

    # ---------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, camera: Camera, drone) -> None:
        """Рисует луч от носа дрона к цели. Ничего не создаёт в кадре."""
        self.dots = 0
        if not self.visible:
            return
        scale = camera.scale
        tx, ty = self.target
        dx, dy = tx - drone.x, ty - drone.y
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        # Нос — в сторону цели: дрон поворачивается по ходу, а луч бьёт туда,
        # куда смотрит курсор, и из-за спины он выглядел бы нелепо.
        muzzle = min(config.LASER_MUZZLE, length * 0.5)
        start = camera.world_to_screen(drone.x + ux * muzzle, drone.y + uy * muzzle)
        end = camera.world_to_screen(tx, ty)
        color = theme.laser_color(self.kind)
        core = theme.laser_core(self.kind)
        if not quality.glow_enabled():
            # `low`: простая линия без ореола и без плавного появления.
            if self.intensity >= 0.5:
                pygame.draw.line(target, color, start, end, LOW_WIDTH)
            return
        pad = int(scale * (RING_RADIUS + RING_SWING + config.LASER_HALO)) + 6
        rect = pygame.Rect(int(min(start[0], end[0])) - pad, int(min(start[1], end[1])) - pad,
                           int(abs(end[0] - start[0])) + pad * 2,
                           int(abs(end[1] - start[1])) + pad * 2).clip(target.get_rect())
        if rect.width <= 0 or rect.height <= 0:
            return
        layer = self._buffer(target.get_size())
        layer.fill((0, 0, 0), rect)
        gain = config.LASER_MIN_GAIN + (config.LASER_MAX_GAIN
                                        - config.LASER_MIN_GAIN) * self.progress
        if self.hot:
            self._draw_hot(layer, start, end, scale, color, core)
        else:
            self._draw_cool(layer, start, end, scale, color, core, gain)
        target.blit(layer, rect.topleft, area=rect, special_flags=pygame.BLEND_RGB_ADD)
        if self.hot:
            # Мягкий край пятна — общим ореолом и уже поверх кадра.
            self._bloom(target, end, scale, color)

    def _buffer(self, size: tuple[int, int]) -> pygame.Surface:
        """Буфер свечения луча: создаётся один раз (и заново — при смене окна)."""
        if self._layer is None or self._layer.get_size() != size:
            self._layer = pygame.Surface(size)
            self._layer.fill((0, 0, 0))
        return self._layer

    def _draw_hot(self, layer, start, end, scale, color, core) -> None:
        """Красный луч: волны яркости, дрожание толщины, пятно и пульс."""
        jitter = 1.0 + config.LASER_JITTER * 0.5 * (
            math.sin(self.time * JITTER_HZ[0]) + math.sin(self.time * JITTER_HZ[1]) * 0.6)
        halo_w = max(3, int(round(config.LASER_HALO * scale * jitter)))
        core_w = max(1, int(round(config.LASER_WIDTH * scale * jitter)))
        mid_w = max(core_w + 1, (halo_w + core_w) // 2)
        lo, hi = HOT_BODY_GAIN
        body = (lo + (hi - lo) * self.progress) * self.intensity
        wave = self.time * WAVE_SPEED
        depth = config.LASER_WAVE_DEPTH

        def halo(b: float):
            return theme.scale(color, body * HOT_HALO * b)

        def mid(b: float):
            return theme.scale(color, body * HOT_MID * b)

        def hot_core(b: float):
            # Ядро «переливается»: на гребне волны оно почти белое, во впадине
            # уходит в красный — так бегущая волна видна и на светлой земле.
            return theme.scale(theme.mix(color, core, b), body * (0.55 + 0.45 * b))

        segments = self._segments(start, end, scale, wave, depth)
        pulse_hz = IMPACT_PULSE[0] + (IMPACT_PULSE[1] - IMPACT_PULSE[0]) * self.progress
        pulse = 0.5 + 0.5 * math.sin(self.time * math.tau * pulse_hz)
        radius = (IMPACT_RADIUS + IMPACT_GROWTH * (self.progress + 0.5 * pulse)) * scale
        level = self.intensity * (0.6 + 0.4 * pulse)
        # Слои — от широкого тусклого к узкому яркому: линия в буфере
        # перезаписывает пиксели, и узкое яркое ядро должно лечь последним.
        self._circle(layer, end, radius, theme.scale(color, 0.45 * level))
        for width, paint in ((halo_w, halo), (mid_w, mid), (core_w, hot_core)):
            for a, b, k in segments:
                pygame.draw.line(layer, paint(k), a, b, width)
                self.dots += 1
        self._circle(layer, end, radius * 0.55, theme.scale(theme.mix(color, core, pulse),
                                                             level))

    def _draw_cool(self, layer, start, end, scale, color, core, gain) -> None:
        """Синий луч: тонкий, полупрозрачный, «дышит»; поток точек к дрону."""
        breath = 1.0 - BREATH_DEPTH * (
            0.5 + 0.5 * math.sin(self.time * math.tau * config.LASER_RING_PULSE))
        level = gain * breath * self.intensity
        halo_w = max(2, int(round(config.LASER_HALO * scale * COOL_WIDTH)))
        core_w = max(1, int(round(config.LASER_WIDTH * scale * COOL_WIDTH)))
        light = theme.mix(color, core, COOL_WHITE)
        pygame.draw.line(layer, theme.scale(color, level * COOL_HALO), start, end, halo_w)
        pygame.draw.line(layer, theme.scale(light, level * COOL_CORE), start, end, core_w)
        self.dots += 2
        # Поток данных: точки плывут от цели к дрону с частотой 🔧 LASER_FLOW_HZ.
        sx, sy = start
        ex, ey = end
        span = math.hypot(ex - sx, ey - sy) / max(1e-6, scale)
        if span > 1e-6:
            phase = (self.time * config.LASER_FLOW_HZ * FLOW_SPACING) % FLOW_SPACING
            dot_r = max(1.5, FLOW_RADIUS * scale)
            along = phase
            while along < span:
                t = along / span                 # 0 — у цели, 1 — у дрона
                px, py = ex + (sx - ex) * t, ey + (sy - ey) * t
                # Точка ярче в середине пути: у концов она «втекает» в луч.
                bright = min(1.0, level * (0.6 + 0.6 * math.sin(math.pi * t)))
                self._circle(layer, (px, py), dot_r, theme.scale(light, bright))
                along += FLOW_SPACING
        # Кольцо-сканер у цели: мягко дышит вокруг неё, не закрывая её саму.
        swing = 0.5 + 0.5 * math.sin(self.time * math.tau * config.LASER_RING_PULSE)
        radius = (RING_RADIUS + RING_SWING * swing) * scale
        ring_w = max(1, int(round(RING_WIDTH * scale)))
        pygame.draw.circle(layer, theme.scale(color, min(1.0, level * COOL_RING)),
                           (int(ex), int(ey)), max(4, int(radius)), ring_w)
        self.dots += 1
        self._circle(layer, end, IMPACT_RADIUS * 0.6 * scale,
                     theme.scale(light, min(1.0, level)))

    # ------------------------------------------------------- примитивы
    def _segments(self, start, end, scale, wave: float, depth: float):
        """Отрезки луча и яркость волны на каждом: [(начало, конец, 0..1)]."""
        sx, sy = start
        ex, ey = end
        span = math.hypot(ex - sx, ey - sy)
        step = max(SEGMENT_MIN_PX, SEGMENT * scale)
        count = max(1, int(math.ceil(span / step)))
        out = []
        prev = (sx, sy)
        for i in range(1, count + 1):
            t = i / count
            point = (sx + (ex - sx) * t, sy + (ey - sy) * t)
            dist_tiles = span * (t - 0.5 / count) / max(1e-6, scale)
            k = 0.5 + 0.5 * math.sin(dist_tiles * config.LASER_WAVE - wave)
            out.append((prev, point, 1.0 - depth + depth * k))
            prev = point
        return out

    def _circle(self, layer, pos, radius: float, color) -> None:
        pygame.draw.circle(layer, color, (int(pos[0]), int(pos[1])),
                           max(1, int(round(radius))))
        self.dots += 1

    def _bloom(self, target, pos, scale: float, color) -> None:
        """Мягкий ореол пятна попадания из общего кэша свечения фабрики."""
        if self.factory is None:
            return
        radius = max(3, int(round(IMPACT_RADIUS * IMPACT_BLOOM * scale)))
        peak = round(IMPACT_BLOOM_GAIN * self.intensity * BLOOM_STEPS) / BLOOM_STEPS
        if peak <= 0.0:
            return
        halo = self.factory.halo(radius, color, 0.0, peak)
        self.factory.blit_add(target, halo, pos)
