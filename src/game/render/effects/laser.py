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
виден и ночью. Каждая светящаяся точка — ореол из общего кэша фабрики
(`SpriteFactory.halo`), яркости и радиусы квантуются: после прогрева в кадре не
создаётся ни одной `Surface`. На `low` — простая линия без ореола.

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
#: Ступеней яркости ореола: плавная яркость дала бы новую поверхность на кадр.
GAIN_STEPS = 8
#: Шаг между светящимися точками луча в долях радиуса ореола: меньше — плотнее
#: и дороже, больше — луч распадается на бусины.
HALO_STEP = 0.55
CORE_STEP = 0.9
#: Синий луч тоньше и прозрачнее красного (plan/09: «альфа около 40–60%»).
COOL_WIDTH = 0.6
COOL_GAIN = 0.5
#: Насколько синий луч «дышит» и как быстро.
BREATH_DEPTH = 0.25
#: Скорость бегущих волн красного луча, тайлов в секунду.
WAVE_SPEED = 6.0
#: Частота дрожания толщины, Гц (две несоизмеримые — без заметного ритма).
JITTER_HZ = (23.0, 37.0)
#: Шаг точек «потока данных» вдоль синего луча, тайлы.
FLOW_SPACING = 0.42
FLOW_RADIUS = 0.10         # радиус точки потока, доля тайла
#: Пятно попадания: радиус в долях тайла и прибавка от прогресса.
IMPACT_RADIUS = 0.24
IMPACT_GROWTH = 0.10
#: Частота пульса пятна: от спокойной к частой по мере прогресса, Гц.
IMPACT_PULSE = (3.0, 9.0)
#: Кольцо-сканер у цели синего луча: радиус, размах «дыхания» и ступени.
RING_RADIUS = 0.42
RING_SWING = 0.10
RING_STEPS = 8
#: Искры попадания красного луча: скорость, время жизни, размер (в тайлах).
SPARK_SPEED = (0.8, 2.2)
SPARK_TTL = (0.18, 0.40)
SPARK_SIZE = 1.4
#: Ширина «простой линии» на `low`, пиксели.
LOW_WIDTH = 2


class LaserBeam:
    """Состояние и отрисовка луча. Один на рендер, живёт между кадрами."""

    def __init__(self, factory, pool=None, seed: int = 0) -> None:
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
        #: Сколько светящихся точек нарисовано в последнем кадре (для тестов).
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
        gain = config.LASER_MIN_GAIN + (config.LASER_MAX_GAIN
                                        - config.LASER_MIN_GAIN) * self.progress
        if self.hot:
            self._draw_hot(target, start, end, scale, color, core, gain)
        else:
            self._draw_cool(target, start, end, scale, color, core, gain)

    def _draw_hot(self, target, start, end, scale, color, core, gain) -> None:
        """Красный луч: волны яркости, дрожание толщины, пятно и пульс."""
        jitter = 1.0 + config.LASER_JITTER * 0.5 * (
            math.sin(self.time * JITTER_HZ[0]) + math.sin(self.time * JITTER_HZ[1]) * 0.6)
        halo_r = max(2, int(round(config.LASER_HALO * scale * 0.5 * jitter)))
        core_r = max(1, int(round(config.LASER_WIDTH * scale * jitter)))
        wave = self.time * WAVE_SPEED

        def brightness(dist_tiles: float) -> float:
            k = 0.5 + 0.5 * math.sin(dist_tiles * config.LASER_WAVE - wave)
            return 1.0 - config.LASER_WAVE_DEPTH + config.LASER_WAVE_DEPTH * k

        self._dots(target, start, end, scale, halo_r, HALO_STEP, color,
                   gain * 0.55, brightness)
        self._dots(target, start, end, scale, core_r, CORE_STEP, core,
                   gain * 0.9, brightness)
        pulse_hz = IMPACT_PULSE[0] + (IMPACT_PULSE[1] - IMPACT_PULSE[0]) * self.progress
        pulse = 0.5 + 0.5 * math.sin(self.time * math.tau * pulse_hz)
        radius = (IMPACT_RADIUS + IMPACT_GROWTH * (self.progress + 0.5 * pulse)) * scale
        self._spot(target, end, radius, color, gain * (0.6 + 0.4 * pulse))
        self._spot(target, end, radius * 0.45, core, gain)

    def _draw_cool(self, target, start, end, scale, color, core, gain) -> None:
        """Синий луч: тонкий, полупрозрачный, «дышит»; поток точек к дрону."""
        breath = 1.0 - BREATH_DEPTH * (
            0.5 + 0.5 * math.sin(self.time * math.tau * config.LASER_RING_PULSE))
        gain *= COOL_GAIN * breath
        halo_r = max(2, int(round(config.LASER_HALO * scale * 0.5 * COOL_WIDTH)))
        core_r = max(1, int(round(config.LASER_WIDTH * scale * COOL_WIDTH)))
        self._dots(target, start, end, scale, halo_r, HALO_STEP, color, gain * 0.6,
                   lambda _d: 1.0)
        self._dots(target, start, end, scale, core_r, CORE_STEP, core, gain * 0.7,
                   lambda _d: 1.0)
        # Поток данных: точки плывут от цели к дрону с частотой 🔧 LASER_FLOW_HZ.
        sx, sy = start
        ex, ey = end
        span_px = math.hypot(ex - sx, ey - sy)
        span = span_px / max(1e-6, scale)
        if span > 1e-6:
            phase = (self.time * config.LASER_FLOW_HZ) % 1.0
            dot_r = max(2, int(round(FLOW_RADIUS * scale)))
            k = 0
            while True:
                along = (k + phase) * FLOW_SPACING
                if along >= span:
                    break
                t = along / span                 # 0 — у цели, 1 — у дрона
                px, py = ex + (sx - ex) * t, ey + (sy - ey) * t
                # Точка ярче в середине пути: у концов она «втекает» в луч.
                level = gain * (0.6 + 0.8 * math.sin(math.pi * t))
                self._blit(target, (px, py), dot_r, core, level)
                k += 1
        # Кольцо-сканер у цели: мягко дышит, ступенями — ради кэша.
        swing = 0.5 + 0.5 * math.sin(self.time * math.tau * config.LASER_RING_PULSE)
        step = min(RING_STEPS - 1, int(swing * RING_STEPS))
        radius = (RING_RADIUS + RING_SWING * step / RING_STEPS) * scale
        ring = self._ring(max(4, int(radius)), color, _quantize(gain * 1.4, GAIN_STEPS))
        target.blit(ring, ring.get_rect(center=(int(ex), int(ey))),
                    special_flags=pygame.BLEND_RGB_ADD)
        self.dots += 1
        self._spot(target, end, IMPACT_RADIUS * 0.6 * scale, color, gain * 0.8)

    # ------------------------------------------------------- примитивы
    def _dots(self, target, start, end, scale, radius: int, step_k: float, color,
              gain: float, brightness) -> None:
        """Цепочка аддитивных ореолов от начала луча к концу."""
        sx, sy = start
        ex, ey = end
        span = math.hypot(ex - sx, ey - sy)
        step = max(1.5, radius * step_k * 2.0)
        count = max(1, int(span / step))
        for i in range(count + 1):
            t = i / count
            px, py = sx + (ex - sx) * t, sy + (ey - sy) * t
            dist_tiles = span * t / max(1e-6, scale)
            self._blit(target, (px, py), radius, color, gain * brightness(dist_tiles))

    def _spot(self, target, pos, radius: float, color, gain: float) -> None:
        self._blit(target, pos, max(2, int(round(radius))), color, gain)

    def _blit(self, target, pos, radius: int, color, gain: float) -> None:
        level = _quantize(gain * self.intensity, GAIN_STEPS)
        if level <= 0.0:
            return
        halo = self.factory.halo(radius, color, 0.0, level)
        self.factory.blit_add(target, halo, pos)
        self.dots += 1

    def _ring(self, radius: int, color, level: float) -> pygame.Surface:
        """Кольцо-сканер: тонкая окружность, кэш фабрики по радиусу и яркости."""
        tint = theme.scale(color, _quantize(level * self.intensity, GAIN_STEPS))
        size = radius * 2 + 4

        def draw(surf: pygame.Surface, s: float) -> None:
            c = s / 2.0
            pygame.draw.circle(surf, tint, (int(c), int(c)),
                               int(c * radius / (radius + 2)), max(1, int(s * 0.03)))

        sprite = self.factory.get(("laser_ring", tuple(tint)), size, draw)
        return sprite


def _quantize(value: float, steps: int) -> float:
    """0…1 ступенями: яркость ореола — часть ключа кэша."""
    return round(max(0.0, min(1.0, value)) * steps) / steps
