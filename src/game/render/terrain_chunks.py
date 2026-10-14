"""Кэш чанков рельефа (plan/09 §4).

Тайлы статичны, поэтому чанк рисуется один раз в Surface размером
`CHUNK × CHUNK` тайлов и дальше только блитится; перерисовывается он лишь
когда тайл в нём изменился (`invalidate_tile`).

Решения, из-за которых рельеф выглядит рельефом, а не обоями:

- **плавный шум для травы.** Оттенок травы берётся из шума с крупной ячейкой
  (несколько тайлов), а не из хэша тайла: соседние тайлы складываются в
  луговые пятна, а не в шахматную доску. Пучки травы и камешки — из хэша
  координат: карта случайная, но воспроизводимая, и генератора случайных чисел
  в рендере нет.
- **гора — низкополигональный массив.** Углы тайлов — вершины общей сетки
  высот: высота растёт от края массива к середине, а положение вершины чуть
  сдвинуто хэшем её координат. Каждый тайл — четыре треугольника вокруг своей
  вершины-центра, и каждый освещён по своей нормали светом сверху-слева.
  Вершины общие для соседей, поэтому грани складываются в сплошной рельеф без
  повторяющегося «узора плитки». На краю массива вершины втянуты внутрь (край
  неровный, под ним видна трава), у южной подошвы — тёмный обрыв в слоях
  породы, на траву под ним падает тень, в глубине массива лежит снег.
- **каждый тайл рисует свои края сам.** Гора не «дорисовывает» тень на соседа
  снизу — сосед сам затемняет свою верхнюю кромку, если над ним гора. Из-за
  этого на стыке чанков не бывает швов: тайл почти не пишет за свои границы,
  а то, что пишет, совпадает с тем, что нарисует сосед (вершины общие).

Вода в чанке статична, а блик по ней ведёт рендер (`Renderer._draw_water_
shimmer`): анимировать кэшированный чанк нельзя, не перерисовывая его каждый
кадр.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from src.game import config
from src.game.data.terrain import Terrain
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.sim.world import World

MOUNTAIN = int(Terrain.MOUNTAIN)
WATER = int(Terrain.WATER)

#: Насколько темнеет кромка тайла под горой-соседом сверху и слева.
SHADOW_STRENGTH = 0.55
SIDE_SHADOW_STRENGTH = 0.30
#: Ширина мягкой тени от горы, в долях тайла.
SHADOW_BAND = 0.40
SIDE_SHADOW_BAND = 0.22
#: Ячейка плавного шума травы в тайлах: пятна размером с несколько тайлов.
GRASS_CELL = 6.0
OLIVE_CELL = 9.0
#: Порог оливковых проплешин: выше него трава переходит в сухую.
OLIVE_FROM = 0.62
GROUND_JITTER = 2.0
#: Сколько пучков травы на тайле (до) и как часто лежит камешек.
GRASS_TUFTS = 3
PEBBLE_CHANCE = 0.22
STONE_CHANCE = 0.05

#: До какой глубины массива растёт высота: дальше — плато с буграми.
ROCK_DEPTHS = 6
#: Высота одной ступени глубины и случайный бугор вершины, в тайлах.
ROCK_STEP_H = 0.55
ROCK_BUMP_H = 0.45
#: Разброс высоты вершины-центра тайла относительно его углов, в тайлах.
ROCK_CENTER_H = 0.30
#: Насколько хэш сдвигает вершину внутри массива, в долях тайла.
ROCK_JITTER = 0.20
#: Насколько втянут край массива: с юга (там обрыв) — сильнее.
ROCK_EDGE = 0.12
ROCK_CLIFF = 0.32
ROCK_EDGE_JITTER = 0.06
#: Свет сверху-слева и чуть сверху (plan/09 §2): нормализуется при расчёте.
ROCK_LIGHT = (-0.55, -0.70, 0.80)
#: Рассеянный свет: доля яркости, которую получает грань, отвёрнутая от света.
ROCK_AMBIENT = 0.18
#: Насколько светлеет камень с высотой: пики ярче подножия.
ROCK_HEIGHT_LIGHT = 0.035
#: С какой высоты (в ступенях) на гранях лежит снег.
SNOW_FROM = 4.2


def _hash01(x: int, y: int, salt: int) -> float:
    """Стабильное «случайное» число 0..1 для тайла — без random.

    Соли берутся взаимно простыми и далеко друг от друга: хэш — это XOR
    произведений, и близкие соли дают похожие последовательности (пятна
    выстраивались в полосы, тянущиеся вдоль всей карты).
    """
    h = (x * 73856093) ^ (y * 19349663) ^ (salt * 83492791)
    h ^= (h >> 13) * 1274126177
    h &= 0x7FFFFFFF
    return h / 0x7FFFFFFF


def _smooth_noise(x: float, y: float, cell: float, salt: int) -> float:
    """Плавный шум 0..1: хэш в узлах решётки и сглаженная интерполяция между ними."""
    gx, gy = x / cell, y / cell
    ix, iy = math.floor(gx), math.floor(gy)
    fx, fy = gx - ix, gy - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = _hash01(ix, iy, salt)
    b = _hash01(ix + 1, iy, salt)
    c = _hash01(ix, iy + 1, salt)
    d = _hash01(ix + 1, iy + 1, salt)
    top = a + (b - a) * fx
    bottom = c + (d - c) * fx
    return top + (bottom - top) * fy


def mountain_depth(terrain: np.ndarray, levels: int = ROCK_DEPTHS) -> np.ndarray:
    """Глубина горного тайла в массиве: 1 — у края, `levels` — в глубине, 0 — не гора.

    Считается один раз на карту (сдвигами numpy): по глубине растёт высота
    вершин, и середина большого массива поднимается в пики. За краем карты —
    тоже гора, иначе массив у границы мира получал бы ложный «край».
    """
    rock = terrain == MOUNTAIN
    depth = np.where(rock, 1, 0).astype(np.int8)
    current = rock.copy()
    h, w = rock.shape
    for level in range(2, levels + 1):
        padded = np.pad(current, 1, constant_values=True)
        inner = current.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                inner &= padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
        depth[inner] = level
        current = inner
    return depth


def _normalized(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


LIGHT = _normalized(ROCK_LIGHT)


class ChunkCache:
    """Пиксельные чанки рельефа с ленивой отрисовкой и кэшем масштаба."""

    def __init__(self, world: World, tile: int = config.TILE,
                 chunk: int = config.CHUNK) -> None:
        self.world = world
        self.tile = int(tile)
        self.chunk = int(chunk)
        self._chunks: dict[tuple[int, int], pygame.Surface] = {}
        self._scaled: dict[tuple[int, int], pygame.Surface] = {}
        self._scale_zoom: float | None = None
        self._depth = mountain_depth(world.terrain)
        self.chunks_drawn = 0      # для отладочного оверлея (за последний кадр)
        self.scaled_drawn = 0

    def reset_frame_stats(self) -> None:
        self.chunks_drawn = 0
        self.scaled_drawn = 0

    # ------------------------------------------------------------- кэш
    def invalidate_tile(self, x: int, y: int) -> None:
        """Сбросить чанк тайла. Масштабированную копию — обязательно тоже.

        Глубина массива пересчитывается: тайл мог стать горой или перестать ею
        быть (обвал гор, M8), и высоты соседей меняются вместе с ним.
        """
        self._depth = mountain_depth(self.world.terrain)
        key = (x // self.chunk, y // self.chunk)
        self._chunks.pop(key, None)
        self._scaled.pop(key, None)

    def invalidate_all(self) -> None:
        self._depth = mountain_depth(self.world.terrain)
        self._chunks.clear()
        self._scaled.clear()

    def surface(self, cx: int, cy: int) -> pygame.Surface:
        """Базовый чанк в исходном масштабе (1 px = 1 px при zoom=1)."""
        key = (cx, cy)
        surf = self._chunks.get(key)
        if surf is None:
            surf = self._render_chunk(cx, cy)
            self._chunks[key] = surf
            self.chunks_drawn += 1
        return surf

    def scaled_surface(self, cx: int, cy: int, zoom: float) -> pygame.Surface:
        """Чанк, отмасштабированный под зум. Кэш сбрасывается при смене зума."""
        if self._scale_zoom != zoom:
            self._scaled.clear()
            self._scale_zoom = zoom
        key = (cx, cy)
        surf = self._scaled.get(key)
        if surf is None:
            base = self.surface(cx, cy)
            side = max(1, int(round(base.get_width() * zoom)))
            surf = pygame.transform.scale(base, (side, side))
            self._scaled[key] = surf
            self.scaled_drawn += 1
        return surf

    # --------------------------------------------------------- отрисовка
    def _render_chunk(self, cx: int, cy: int) -> pygame.Surface:
        """Чанк в два прохода: сначала поверхность всех тайлов, потом горы.

        Грани горы опираются на общие вершины, а те сдвинуты хэшем — поэтому
        грань тайла может заходить на соседний горный тайл. Нарисуй гору сразу
        после её травы, и следующий тайл залил бы этот заход своей травой
        (щели в массиве). Горы второго прохода берутся и с полосы в тайл за
        краем чанка: их заходы внутрь чанка тоже должны лечь на место.
        """
        side = self.chunk * self.tile
        surf = pygame.Surface((side, side))
        x_start = cx * self.chunk
        y_start = cy * self.chunk
        for ty in range(y_start, y_start + self.chunk):
            for tx in range(x_start, x_start + self.chunk):
                self._draw_tile(surf, tx, ty, (tx - x_start) * self.tile,
                                (ty - y_start) * self.tile)
        for ty in range(y_start - 1, y_start + self.chunk + 1):
            for tx in range(x_start - 1, x_start + self.chunk + 1):
                if self._type(tx, ty) == MOUNTAIN:
                    self._draw_mountain(surf, tx, ty, (tx - x_start) * self.tile,
                                        (ty - y_start) * self.tile)
        return surf

    # ------------------------------------------------------------ тайлы
    def _type(self, tx: int, ty: int) -> int | None:
        """Тип тайла или None, если тайл вне карты (тогда рисуется «ничто»)."""
        if not self.world.in_bounds(tx, ty):
            return None
        return self.world.tile(tx, ty)

    def _is_rock(self, tx: int, ty: int) -> bool:
        """Гора ли тайл. За краем карты — гора: массив не обрывается у границы."""
        value = self._type(tx, ty)
        return value is None or value == MOUNTAIN

    def _tile_depth(self, tx: int, ty: int) -> int:
        if not self.world.in_bounds(tx, ty):
            return ROCK_DEPTHS
        return int(self._depth[ty, tx])

    def _draw_tile(self, surf: pygame.Surface, tx: int, ty: int, px: int, py: int) -> None:
        value = self._type(tx, ty)
        if value is None:
            surf.fill(theme.OUTLINE, (px, py, self.tile, self.tile))
            return
        if value == WATER:
            self._draw_water(surf, tx, ty, px, py)
        else:
            # Под горой тоже трава: неровный край массива её открывает.
            # Сама гора ложится вторым проходом (`_render_chunk`).
            self._draw_ground(surf, tx, ty, px, py)
        if value != MOUNTAIN:
            self._draw_neighbour_shadow(surf, tx, ty, px, py)
        if value != MOUNTAIN and value != WATER:
            # Сетка только там, где можно строить: на горе и воде она — шум.
            self._draw_grid(surf, px, py)

    # ------------------------------------------------------------ земля
    def _draw_ground(self, surf: pygame.Surface, tx: int, ty: int,
                     px: int, py: int) -> None:
        """Трава: плавные луговые пятна, пучки травинок, редкие камешки."""
        t = self.tile
        variants = theme.GROUND_VARIANTS
        lush = _smooth_noise(tx, ty, GRASS_CELL, 1)
        base = variants[min(len(variants) - 1, int(lush * len(variants)))]
        dry = _smooth_noise(tx + 31, ty + 17, OLIVE_CELL, 2)
        if dry > OLIVE_FROM:
            base = theme.mix(base, theme.GROUND_OLIVE,
                             min(1.0, (dry - OLIVE_FROM) / (1.0 - OLIVE_FROM)) * 0.8)
        jitter = (_hash01(tx, ty, 3) - 0.5) * GROUND_JITTER
        surf.fill(theme.clamp_color((base[0] + jitter, base[1] + jitter, base[2] + jitter)),
                  (px, py, t, t))
        # Мягкие тёмные разводы: трава неровная, но пятна не спорят с залежами.
        if _hash01(tx, ty, 5) > 0.45:
            sx = px + int(_hash01(tx, ty, 7) * (t - 8))
            sy = py + int(_hash01(tx, ty, 11) * (t - 6))
            surf.fill(theme.darken(base, 0.12), (sx, sy, 6 + int(t * 0.12), 3))
        # Пучки травы: две-три травинки из одной точки, тень у корня.
        blade = theme.mix(base, theme.GRASS_BLADE, 0.75)
        tufts = int(_hash01(tx, ty, 13) * (GRASS_TUFTS + 1))
        for i in range(tufts):
            o = 17 + i * 6
            bx = px + 3 + int(_hash01(tx, ty, o) * (t - 7))
            by = py + 5 + int(_hash01(tx, ty, o + 1) * (t - 8))
            h = 2 + int(_hash01(tx, ty, o + 2) * 3)
            surf.fill(theme.GRASS_BLADE_DARK, (bx - 1, by, 3, 1))
            pygame.draw.line(surf, blade, (bx, by), (bx, by - h))
            pygame.draw.line(surf, blade, (bx, by), (bx - 1, by - h + 1))
            pygame.draw.line(surf, blade, (bx, by), (bx + 1, by - h + 1))
        # Камешки: тёмная точка с подсветкой сверху — они «лежат» на траве.
        if _hash01(tx, ty, 23) < PEBBLE_CHANCE:
            ox = int(_hash01(tx, ty, 29) * (t - 6)) + 2
            oy = int(_hash01(tx, ty, 31) * (t - 6)) + 2
            w = 2 + int(_hash01(tx, ty, 37) * 2)
            surf.fill(theme.GROUND_PEBBLE, (px + ox, py + oy, w, w))
            surf.fill(theme.lighten(base, 0.10), (px + ox, py + oy, w, 1))
        if _hash01(tx, ty, 41) < STONE_CHANCE:
            ox = int(_hash01(tx, ty, 43) * (t - 9)) + 3
            oy = int(_hash01(tx, ty, 47) * (t - 9)) + 3
            surf.fill(theme.GRASS_BLADE_DARK, (px + ox, py + oy + 1, 5, 3))
            surf.fill(theme.GROUND_STONE, (px + ox, py + oy, 4, 3))
            surf.fill(theme.lighten(theme.GROUND_STONE, 0.3), (px + ox, py + oy, 3, 1))

    # ------------------------------------------------------------- горы
    def _vertex(self, vx: int, vy: int) -> tuple[float, float, float]:
        """Вершина сетки высот в углу тайлов: (сдвиг x, сдвиг y, высота).

        Сдвиг — в долях тайла от узла решётки, высота — в тайлах. Вершина
        зависит только от четырёх тайлов вокруг неё, поэтому все соседи
        получают одну и ту же точку и грани стыкуются без щелей.
        """
        nw, ne = self._is_rock(vx - 1, vy - 1), self._is_rock(vx, vy - 1)
        sw, se = self._is_rock(vx - 1, vy), self._is_rock(vx, vy)
        count = nw + ne + sw + se
        if count == 4:
            depth = min(self._tile_depth(vx - 1, vy - 1), self._tile_depth(vx, vy - 1),
                        self._tile_depth(vx - 1, vy), self._tile_depth(vx, vy))
            height = depth * ROCK_STEP_H + _hash01(vx, vy, 71) * ROCK_BUMP_H
            return ((_hash01(vx, vy, 73) - 0.5) * 2 * ROCK_JITTER,
                    (_hash01(vx, vy, 79) - 0.5) * 2 * ROCK_JITTER, height)
        # Край массива: вершина уходит внутрь, к горным тайлам. На прямом краю
        # она скользит вдоль него, на внешнем углу — по диагонали.
        dirx = (ne + se) - (nw + sw)
        diry = (sw + se) - (nw + ne)
        if count == 3 or (count == 2 and dirx == 0 and diry == 0):
            return (0.0, 0.0, 0.0)          # внутренний угол или диагональ
        sx = 1.0 if dirx > 0 else -1.0 if dirx < 0 else 0.0
        sy = 1.0 if diry > 0 else -1.0 if diry < 0 else 0.0
        jitter = (_hash01(vx, vy, 83) - 0.5) * 2 * ROCK_EDGE_JITTER
        pull_x = sx * (ROCK_EDGE + jitter)
        # Гора к северу от вершины — значит, здесь южная подошва и обрыв.
        pull_y = sy * ((ROCK_CLIFF if sy < 0 else ROCK_EDGE) + jitter)
        if count == 2:
            # Вдоль прямого края — небольшой сдвиг по нему, чтобы край не был линейкой.
            along = (_hash01(vx, vy, 89) - 0.5) * 2 * ROCK_JITTER
            if sx == 0.0:
                pull_x = along
            else:
                pull_y = along
        return (pull_x, pull_y, 0.0)

    def _draw_mountain(self, surf: pygame.Surface, tx: int, ty: int,
                       px: int, py: int) -> None:
        """Горный тайл: четыре освещённые грани, обрыв с юга, кромки по краю."""
        t = self.tile
        corners = []
        for vx, vy in ((tx, ty), (tx + 1, ty), (tx + 1, ty + 1), (tx, ty + 1)):
            ox, oy, h = self._vertex(vx, vy)
            corners.append((vx + ox, vy + oy, h))
        avg_h = sum(c[2] for c in corners) / 4.0
        # Центр — почти на высоте углов: заметный бугор в каждом тайле давал
        # ровную сетку «пирамидок», и массив выглядел как шипованная плита.
        center = (tx + 0.5 + (_hash01(tx, ty, 97) - 0.5) * ROCK_JITTER,
                  ty + 0.5 + (_hash01(tx, ty, 101) - 0.5) * ROCK_JITTER,
                  avg_h + (_hash01(tx, ty, 103) - 0.35) * ROCK_CENTER_H)

        def to_px(p) -> tuple[float, float]:
            return (px + (p[0] - tx) * t, py + (p[1] - ty) * t)

        for a, b in zip(corners, corners[1:] + corners[:1]):
            pygame.draw.polygon(surf, _facet_color(center, a, b),
                                (to_px(center), to_px(a), to_px(b)))
        tl, tr, br, bl = (to_px(c) for c in corners)
        north_open = not self._is_rock(tx, ty - 1)
        east_open = not self._is_rock(tx + 1, ty)
        south_open = not self._is_rock(tx, ty + 1)
        west_open = not self._is_rock(tx - 1, ty)
        if south_open:
            self._draw_cliff(surf, tx, ty, px, py, bl, br)
        # Кромки: к свету (север, запад) — светлая, от света — тёмный контур.
        if north_open:
            pygame.draw.line(surf, theme.MOUNTAIN_RIM, tl, tr)
            pygame.draw.line(surf, theme.OUTLINE, (tl[0], tl[1] - 1), (tr[0], tr[1] - 1))
        if west_open:
            pygame.draw.line(surf, theme.mix(theme.MOUNTAIN_RIM, theme.MOUNTAIN_BASE, 0.4),
                             tl, bl)
            pygame.draw.line(surf, theme.OUTLINE, (tl[0] - 1, tl[1]), (bl[0] - 1, bl[1]))
        if east_open:
            pygame.draw.line(surf, theme.OUTLINE, tr, br, 2)

    def _draw_cliff(self, surf: pygame.Surface, tx: int, ty: int, px: int, py: int,
                    bl: tuple[float, float], br: tuple[float, float]) -> None:
        """Обрыв у южной подошвы: тёмная стена в слоях породы до низа тайла."""
        t = self.tile
        bottom = py + t - 2
        face = [bl, br, (br[0], bottom), (bl[0], bottom)]
        pygame.draw.polygon(surf, theme.MOUNTAIN_CLIFF, face)
        # Слои породы — вертикальные трещины: стена, а не тень.
        for i in range(3):
            k = 0.18 + i * 0.30 + (_hash01(tx, ty, 107 + i) - 0.5) * 0.12
            x = bl[0] + (br[0] - bl[0]) * k
            y = bl[1] + (br[1] - bl[1]) * k
            pygame.draw.line(surf, theme.MOUNTAIN_STRATA, (x, y + 2), (x - 1, bottom - 1))
        # Губа обрыва и подошва: светлая кромка сверху, тёмный контур снизу.
        pygame.draw.line(surf, theme.lighten(theme.MOUNTAIN_CLIFF, 0.28), bl, br)
        pygame.draw.line(surf, theme.OUTLINE, (bl[0], bottom + 1), (br[0], bottom + 1), 2)

    # ------------------------------------------------------------- вода
    def _draw_water(self, surf: pygame.Surface, tx: int, ty: int,
                    px: int, py: int) -> None:
        """Вода: тёмная глубина в середине, светлая кромка у берега."""
        t = self.tile
        deep = all(self._type(tx + dx, ty + dy) == WATER
                   for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
        base = theme.WATER_DEEP if deep else theme.WATER_BASE
        jitter = (_smooth_noise(tx, ty, 4.0, 53) - 0.5) * 10.0
        surf.fill(theme.clamp_color((base[0] + jitter, base[1] + jitter, base[2] + jitter)),
                  (px, py, t, t))
        # Статичная рябь: короткие штрихи, второй темнее — глубина читается как вода.
        y = 3 + int(_hash01(tx, ty, 59) * (t - 8))
        x = 2 + int(_hash01(tx, ty, 67) * (t * 0.4))
        surf.fill(theme.mix(base, theme.WATER_GLINT, 0.6), (px + x, py + y, int(t * 0.4), 1))
        y2 = 3 + int(_hash01(tx, ty, 61) * (t - 8))
        x2 = 2 + int(_hash01(tx, ty, 71) * (t * 0.5))
        surf.fill(theme.mix(base, theme.WATER_GLINT, 0.3), (px + x2, py + y2, int(t * 0.3), 1))
        # Светлая кромка у берега: тайл сам рисует свою границу с землёй.
        for dx, dy, rect, inner in (
                (-1, 0, (px, py, 2, t), (px + 2, py, 1, t)),
                (1, 0, (px + t - 2, py, 2, t), (px + t - 3, py, 1, t)),
                (0, -1, (px, py, t, 2), (px, py + 2, t, 1)),
                (0, 1, (px, py + t - 2, t, 2), (px, py + t - 3, t, 1))):
            if self._type(tx + dx, ty + dy) == WATER:
                continue
            surf.fill(theme.WATER_RIM, rect)
            surf.fill(theme.mix(base, theme.WATER_RIM, 0.35), inner)

    # ------------------------------------------------------ края и сетка
    def _draw_neighbour_shadow(self, surf: pygame.Surface, tx: int, ty: int,
                               px: int, py: int) -> None:
        """Мягкая тень от горы сверху и слева: свет падает сверху-слева."""
        t = self.tile
        if self._type(tx, ty - 1) == MOUNTAIN:
            band = max(2, int(t * SHADOW_BAND))
            for i in range(band):
                k = SHADOW_STRENGTH * (1.0 - i / band) ** 1.6
                surf.fill((int(255 * (1 - k)),) * 3, (px, py + i, t, 1),
                          special_flags=pygame.BLEND_RGB_MULT)
        if self._type(tx - 1, ty) == MOUNTAIN:
            band = max(2, int(t * SIDE_SHADOW_BAND))
            for i in range(band):
                k = SIDE_SHADOW_STRENGTH * (1.0 - i / band) ** 1.6
                surf.fill((int(255 * (1 - k)),) * 3, (px + i, py, 1, t),
                          special_flags=pygame.BLEND_RGB_MULT)

    def _draw_grid(self, surf: pygame.Surface, px: int, py: int) -> None:
        """Едва заметная сетка тайлов: помогает ставить здания по клеткам.

        Рисуется умножением, а не заливкой: тёмная линия заданного цвета на
        светлом пятне травы выглядела бы жирной, а на тёмном — пропадала.
        Умножение одинаково слабо темнит любой тайл.
        """
        surf.fill(theme.GRID_TINT, (px, py, self.tile, 1), special_flags=pygame.BLEND_RGB_MULT)
        surf.fill(theme.GRID_TINT, (px, py, 1, self.tile), special_flags=pygame.BLEND_RGB_MULT)

    # ------------------------------------------------------------ рисование
    def draw(self, target: pygame.Surface, camera: Camera) -> None:
        """Блитит видимые чанки. Только то, что попало в кадр."""
        self.reset_frame_stats()
        x0, y0, x1, y1 = camera.visible_tiles(margin=0)
        cx0 = max(0, x0 // self.chunk)
        cy0 = max(0, y0 // self.chunk)
        cx1 = min((self.world.width - 1) // self.chunk, x1 // self.chunk)
        cy1 = min((self.world.height - 1) // self.chunk, y1 // self.chunk)
        zoom = camera.zoom
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                sx, sy = camera.world_to_screen(cx * self.chunk, cy * self.chunk)
                target.blit(self.scaled_surface(cx, cy, zoom), (int(sx), int(sy)))


def _facet_color(c, a, b) -> tuple[int, int, int]:
    """Цвет грани по её нормали: к свету — светлее, от света — темнее.

    Нормаль — векторное произведение двух рёбер треугольника в «тайлах»;
    высоты те же единицы, поэтому склон крутизной в одну ступень на тайл уже
    заметно отворачивается от света. Высокие грани светлее и в снегу.
    """
    ux, uy, uz = a[0] - c[0], a[1] - c[1], a[2] - c[2]
    vx, vy, vz = b[0] - c[0], b[1] - c[1], b[2] - c[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    if nz < 0:
        nx, ny, nz = -nx, -ny, -nz
    # Ось y экрана смотрит вниз, а свет «сверху» — к −y: знак уже учтён в LIGHT.
    nx, ny, nz = _normalized((nx, ny, nz))
    lit = max(0.0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2])
    k = ROCK_AMBIENT + (1.0 - ROCK_AMBIENT) * lit
    flat = LIGHT[2]                         # так освещена ровная площадка
    if k >= flat:
        color = theme.mix(theme.MOUNTAIN_BASE, theme.MOUNTAIN_LIT,
                          (k - flat) / max(1e-6, 1.0 - flat))
    else:
        color = theme.mix(theme.MOUNTAIN_DARK, theme.MOUNTAIN_BASE, k / max(1e-6, flat))
    height = (a[2] + b[2] + c[2]) / 3.0
    color = theme.lighten(color, min(0.25, height * ROCK_HEIGHT_LIGHT))
    if height >= SNOW_FROM * ROCK_STEP_H:
        snow = min(1.0, (height - SNOW_FROM * ROCK_STEP_H) / ROCK_STEP_H + 0.4)
        color = theme.mix(color, theme.scale(theme.MOUNTAIN_SNOW, 0.55 + 0.45 * k), snow)
    return color
