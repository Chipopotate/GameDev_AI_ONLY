"""Эффекты и «сок»: один пул частиц фиксированного размера (plan/09 §10).

Пул заводится один раз (🔧 2000 частиц) и переиспользуется: частица — это
запись в списках, а не объект. В кадре не создаётся ни одной `Surface`:
искры, дым и кольца берутся из кэшей фабрики, капли и осколки рисуются
примитивами.

Виды частиц (plan/09 §10): искра, дым, капля, осколок, кольцо-вспышка. Комки
дыма и кольца кэшируются по радиусу — растягивать спрайт каждый кадр было бы
дороже, чем нарисовать.

Здесь же живёт **наблюдатель** (`EffectsDirector`): он смотрит на состояние
симуляции и превращает события в частицы. Симуляция о частицах не знает —
рендер только читает её состояние.
"""

from __future__ import annotations

import math
import random

import pygame

from src.game import config
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.render.sprites import factory

SPARK = "spark"
SMOKE = "smoke"
DROP = "drop"
SHARD = "shard"
RING = "ring"

#: Сколько ступеней радиуса кэшируется у дыма и кольца. Кольцо растёт
#: дискретно — по ступеням, иначе спрайт пришлось бы масштабировать в кадре.
SMOKE_STEPS = 6
RING_STEPS = 10
SPARK_STEPS = 4
#: Множители размера частиц в разлёте: конечный набор вместо плавного.
SIZE_GRADES = (0.7, 0.85, 1.0, 1.15, 1.3)
#: Ореол искры в долях её размера: `size` задан в тайлах, и искра в 2 тайла
#: без этого множителя давала ореол радиусом в два тайла — пятно, а не искру.
SPARK_SCALE = 0.25
#: Осколок разрушения, в тайлах: 2.4 давало треугольники радиусом ~77 px,
#: которые закрывали полэкрана вокруг здания.
SHARD_SIZE = 0.3
#: Искры добычи — мелкие «крошки» в цвете ресурса: у луча дрона свои искры
#: (`effects/laser.py`), и крупные искры здесь превращали залежь в белый диск.
MINE_SPARK_SIZE = 0.6
MINE_SPARK_HZ = 8.0


class Particle:
    """Одна частица. Обычный класс: пул небольшой, экономить не на чем."""

    __slots__ = ("kind", "x", "y", "vx", "vy", "life", "ttl", "size", "color",
                 "spin", "angle", "drag", "active")

    def __init__(self) -> None:
        self.active = False

    def start(self, kind: str, x: float, y: float, vx: float, vy: float,
              ttl: float, size: float, color, drag: float = 0.0,
              spin: float = 0.0) -> None:
        self.kind = kind
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life, self.ttl = 0.0, max(0.01, ttl)
        self.size = size
        self.color = color
        self.drag = drag
        self.spin = spin
        self.angle = 0.0
        self.active = True

    @property
    def ratio(self) -> float:
        """Доля прожитой жизни, 0..1."""
        return min(1.0, self.life / self.ttl)


class ParticlePool:
    """Пул частиц: обновление и отрисовка. Ничего не создаёт в кадре."""

    def __init__(self, size: int = 0, factory_: factory.SpriteFactory | None = None) -> None:
        self.capacity = int(size or config.PARTICLE_POOL)
        self.particles = [Particle() for _ in range(self.capacity)]
        self._next = 0
        self.factory = factory_ or factory.SpriteFactory()
        self.drawn = 0
        self.spawned = 0

    # ----------------------------------------------------------- спавн
    def spawn(self, kind: str, x: float, y: float, vx: float, vy: float,
              ttl: float, size: float, color, drag: float = 0.0,
              spin: float = 0.0) -> None:
        """Берёт частицу из пула, вытесняя самую старую при переполнении."""
        particle = self.particles[self._next]
        self._next = (self._next + 1) % self.capacity
        particle.start(kind, x, y, vx, vy, ttl, size, color, drag, spin)
        self.spawned += 1

    def burst(self, kind: str, x: float, y: float, count: int, speed: float,
              ttl: float, size: float, color, rng: random.Random,
              drag: float = 0.0) -> None:
        """Разлёт частиц по кругу — попадание, смерть, разрушение здания."""
        for _ in range(count):
            a = rng.uniform(0.0, math.tau)
            v = speed * rng.uniform(0.35, 1.0)
            # Размер — ступенями, а не плавно: спрайт и ореол частицы берутся
            # из кэша по размеру, и плавный разброс давал новую поверхность
            # почти на каждую частицу.
            grade = SIZE_GRADES[rng.randrange(len(SIZE_GRADES))]
            self.spawn(kind, x, y, math.cos(a) * v, math.sin(a) * v,
                       ttl * rng.uniform(0.7, 1.3), size * grade, color, drag)

    # --------------------------------------------------------- обновление
    def update(self, dt: float) -> None:
        for p in self.particles:
            if not p.active:
                continue
            p.life += dt
            if p.life >= p.ttl:
                p.active = False
                continue
            if p.drag:
                k = max(0.0, 1.0 - p.drag * dt)
                p.vx *= k
                p.vy *= k
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.angle += p.spin * dt

    def active_count(self) -> int:
        return sum(1 for p in self.particles if p.active)

    def clear(self) -> None:
        for p in self.particles:
            p.active = False

    # ---------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, camera: Camera) -> None:
        """Рисует активные частицы. Мировые координаты → экранные."""
        self.drawn = 0
        scale = camera.scale
        for p in self.particles:
            if not p.active:
                continue
            self.drawn += 1
            sx, sy = camera.world_to_screen(p.x, p.y)
            ratio = p.ratio
            if p.kind == SPARK:
                # Искра гаснет ступенями — по той же причине, что и дым.
                step = min(SPARK_STEPS - 1, int(ratio * SPARK_STEPS))
                radius = max(2, int(round(p.size * scale * SPARK_SCALE
                                          * (1.0 - step / SPARK_STEPS * 0.6))))
                glow = self.factory.halo(radius, p.color, 0.0, 1.0)
                self.factory.blit_add(target, glow, (sx, sy))
            elif p.kind == SMOKE:
                step = min(SMOKE_STEPS - 1, int(ratio * SMOKE_STEPS))
                # p.size — в тайлах, scale — уже пикселей на тайл.
                size = max(6, int(p.size * scale * (0.5 + step * 0.35)))
                sprite = self.factory.get(("smoke", step), size, _draw_smoke)
                target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))
            elif p.kind == DROP:
                pygame.draw.circle(target, p.color, (int(sx), int(sy)),
                                   max(1, int(p.size * scale * (1.0 - ratio * 0.4))))
            elif p.kind == SHARD:
                self._draw_shard(target, sx, sy, p, scale)
            elif p.kind == RING:
                step = min(RING_STEPS - 1, int(ratio * RING_STEPS))
                size = max(10, int(p.size * scale * (0.5 + step * 0.55)))
                sprite = self.factory.get(("ring", step, tuple(p.color)), size,
                                          _draw_ring_for(p.color))
                target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))

    @staticmethod
    def _draw_shard(target: pygame.Surface, sx: float, sy: float, p: Particle,
                    scale: float) -> None:
        """Осколок: вращающийся треугольник, уменьшается к концу жизни."""
        r = max(1.0, p.size * scale * (1.0 - p.ratio * 0.5))
        pts = factory.polygon_points(sx, sy, r, 3, p.angle)
        pygame.draw.polygon(target, theme.OUTLINE, pts)
        pygame.draw.polygon(target, p.color,
                            factory.polygon_points(sx, sy, r * 0.7, 3, p.angle))


# ------------------------------------------------------------------ события
class EffectsDirector:
    """Превращает события симуляции в частицы.

    Симуляция не рассылает событий (и не должна: частицы — это отрисовка),
    поэтому наблюдатель сравнивает состояние с прошлым кадром. Так эффекты не
    могут разойтись с логикой: они и есть её отражение.
    """

    def __init__(self, pool: ParticlePool, seed: int = 0) -> None:
        self.pool = pool
        self.rng = random.Random(seed ^ 0x5EEDF00D)
        self._inventory_total = 0
        self._building_ids: dict[int, tuple[int, int, str, str]] = {}
        self._mined_total = 0
        self._dismantling: int | None = None
        self._shake = 0.0
        self.events = 0

    # ------------------------------------------------------------- встряска
    @property
    def shake(self) -> float:
        """Текущая амплитуда тряски экрана в пикселях (plan/09 §10)."""
        return self._shake

    def add_shake(self, amount: float) -> None:
        self._shake = min(config.SCREEN_SHAKE_MAX, self._shake + amount)

    # --------------------------------------------------------------- кадр
    def update(self, sim, dt: float) -> None:
        """Один шаг эффектов: сравнить состояние и насыпать частиц."""
        self._shake = max(0.0, self._shake - dt * config.SCREEN_SHAKE_MAX * 2.0)
        self.pool.update(dt)
        self._watch_buildings(sim)
        self._watch_mining(sim, dt)
        self._watch_inventory(sim)
        self._watch_mined(sim)

    def _watch_buildings(self, sim) -> None:
        """Постройка и снос здания: кольцо-вспышка и осколки."""
        seen: dict[int, tuple[int, int, str, str]] = {}
        for b in sim.buildings.buildings:
            seen[b.id] = (b.x, b.y, b.kind, _state(sim, b))
            if b.id not in self._building_ids:
                self.events += 1
                cx, cy = b.x + 0.5, b.y + 0.5
                self.pool.spawn(RING, cx, cy, 0.0, 0.0, 0.35, 1.0, theme.NEON_CYAN)
                self.pool.burst(SMOKE, cx, cy, 6, 1.2, 0.5, 1.0, (0, 0, 0), self.rng, 2.0)
        for bid, (x, y, _kind, _state_) in self._building_ids.items():
            if bid in seen:
                continue
            self.events += 1
            cx, cy = x + 0.5, y + 0.5
            if bid == self._dismantling:
                # Разбор игроком — спокойная работа, а не катастрофа: короткая
                # вспышка и немного дыма, без осколков и тряски (plan/09 §10:
                # «стройка/снос» и «разрушение» — разные эффекты).
                self.pool.spawn(RING, cx, cy, 0.0, 0.0, 0.30, 0.9, theme.DISMANTLE_RING)
                self.pool.burst(SMOKE, cx, cy, 4, 0.9, 0.5, 0.8, (0, 0, 0), self.rng, 2.4)
                continue
            # Разрушение (на M5 — врагами): осколки, дым и встряска экрана.
            self.pool.burst(SHARD, cx, cy, 8, 2.6, 0.7, SHARD_SIZE, theme.STEEL_LIGHT,
                            self.rng, 1.4)
            self.pool.burst(SMOKE, cx, cy, 8, 1.4, 0.9, 1.2, (0, 0, 0), self.rng, 2.4)
            self.add_shake(config.SCREEN_SHAKE_MAX * 0.35)
        self._building_ids = seen
        # Что разбирается сейчас: здание исчезнет на следующем шаге, и по этому
        # id эффект узнает, что это разбор, а не разрушение.
        target = sim.dismantle_target
        self._dismantling = getattr(target, "id", None) \
            if target in sim.buildings.buildings else None

    def _watch_mining(self, sim, dt: float) -> None:
        """Добыча: искры из залежи, пока идёт цикл."""
        target = sim.mine_target
        if target is None or sim.mine_progress <= 0.0:
            return
        rate = MINE_SPARK_HZ * dt
        while rate > 0.0:
            if rate < 1.0 and self.rng.random() > rate:
                break
            rate -= 1.0
            a = self.rng.uniform(0.0, math.tau)
            speed = self.rng.uniform(0.6, 1.8)
            color = _item_color(sim, target.resource_id)
            self.pool.spawn(SPARK, target.x + 0.5, target.y + 0.5,
                            math.cos(a) * speed, math.sin(a) * speed,
                            self.rng.uniform(0.25, 0.5), MINE_SPARK_SIZE, color,
                            drag=1.6)

    def _watch_inventory(self, sim) -> None:
        """Подбор предмета: светлячки летят к дрону.

        Считается всё хранилище дрона (`Cargo`): иначе подбор постройки в хотбар
        не светился бы, а перекладывание хотбар → инвентарь в окне выглядело бы
        как подбор.
        """
        total = sim.cargo.total_count()
        if total > self._inventory_total:
            gained = min(6, total - self._inventory_total)
            drone = sim.drone
            for _ in range(gained):
                a = self.rng.uniform(0.0, math.tau)
                d = self.rng.uniform(0.6, 1.2)
                self.pool.spawn(SPARK, drone.x + math.cos(a) * d, drone.y + math.sin(a) * d,
                                -math.cos(a) * d, -math.sin(a) * d,
                                0.35, 1.6, theme.NEON_ICE, drag=1.0)
        self._inventory_total = total

    def _watch_mined(self, sim) -> None:
        """Крупная веха добычи — маленькая тряска: мир «отзывается» на труд."""
        if sim.mined_total // 25 > self._mined_total // 25:
            self.add_shake(config.SCREEN_SHAKE_MAX * 0.15)
        self._mined_total = sim.mined_total


def _draw_smoke(surf: pygame.Surface, s: float) -> None:
    """Мягкое дымное пятно: шесть кругов с убывающей альфой.

    Рисуется на отдельном слое и блитится: `pygame.draw` на SRCALPHA-холсте
    заменяет альфу, а не смешивает её, и без слоя дым был бы плоским кругом.
    """
    c = s / 2.0
    layer = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    for k in range(6):
        radius = c * (1.0 - k / 6.5)
        pygame.draw.circle(layer, (24, 26, 34, int(58 * (1.0 - k / 6.0))),
                           (int(c), int(c)), int(radius))
    surf.blit(layer, (0, 0))


def _draw_ring_for(color):
    """Рисование кольца-вспышки заданного цвета (замыкание для кэша)."""
    def draw(surf: pygame.Surface, s: float) -> None:
        c = s / 2.0
        pygame.draw.circle(surf, color, (int(c), int(c)), int(c * 0.86),
                           max(1, int(s * 0.10)))
    return draw


def _item_color(sim, item_id: str):
    item = sim.run.items.get(item_id)
    return item.color if item is not None else theme.NEON_ICE


def _state(sim, building) -> str:
    """Строка состояния здания — сравнение состояний тоже «событие»."""
    if not building.powered and _needs_power(building.kind):
        return "off"
    if building.busy:
        return "work"
    return "idle"


def _needs_power(kind: str) -> bool:
    from src.game.data.buildings import building as building_def
    return building_def(kind).needs_power
