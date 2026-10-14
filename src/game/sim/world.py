"""Карта: тайлы, генерация, залежи. Не импортирует pygame."""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.game import config
from src.game.data.resources import ItemTable
from src.game.data.terrain import Terrain, terrain_type


@dataclass
class Deposit:
    """Залежь ресурса на одном тайле."""

    id: int
    resource_id: str
    x: int
    y: int
    amount: int

    @property
    def depleted(self) -> bool:
        return self.amount <= 0


class World:
    """Слои карты в numpy + список залежей.

    Слои: `terrain` (int8), `deposit_id` (int32, -1 = нет залежи).
    Мир про освещённость ничего не знает: слой света живёт в `sim/light.py`,
    заражение появится там же на M8.
    """

    def __init__(
        self,
        seed: int,
        terrain: np.ndarray,
        deposit_id: np.ndarray,
        deposits: list[Deposit],
        start_rect: tuple[int, int, int, int],
        items: ItemTable | None = None,
    ) -> None:
        self.seed = seed
        self.terrain = terrain
        self.deposit_id = deposit_id
        self.deposits = deposits
        self.start_rect = start_rect  # (x0, y0, w, h)
        self.width = int(terrain.shape[1])
        self.height = int(terrain.shape[0])
        # Ресурсы забега нужны карте, чтобы разложить залежи: их состав
        # рандомизируется каждый забег (plan/02).
        if items is None:
            from src.game.sim.run_gen import generate_run
            items = generate_run(seed).items
        self.items = items

    # ------------------------------------------------------------ генерация
    @classmethod
    def generate(cls, seed: int, items: ItemTable | None = None) -> "World":
        """Детерминированная генерация карты по seed.

        `items` — ресурсы забега (генератор M3). Без них берётся набор по тому
        же сиду: карта и ресурсы обязаны сходиться, иначе залежь ссылалась бы
        на несуществующий ресурс.
        """
        if items is None:
            from src.game.sim.run_gen import generate_run
            items = generate_run(seed).items
        rng = random.Random(seed)
        w, h = config.MAP_W, config.MAP_H

        terrain = np.full((h, w), int(Terrain.GROUND), dtype=np.int8)

        # 1. Горные и водные пятна — только вне стартовой зоны.
        cls._stamp_blobs(terrain, rng, config.MOUNTAIN_BLOB_COUNT,
                         config.MOUNTAIN_BLOB_RADIUS, int(Terrain.MOUNTAIN))
        cls._stamp_blobs(terrain, rng, config.WATER_BLOB_COUNT,
                         config.WATER_BLOB_RADIUS, int(Terrain.WATER))

        # 2. Стартовая зона всегда чистая земля.
        sx, sy, sw, sh = cls._start_rect()
        terrain[sy:sy + sh, sx:sx + sw] = int(Terrain.GROUND)

        # 3. Сплошное горное кольцо вокруг стартовой зоны (обвалится в M8).
        ring = cls._ring_mask(sx, sy, sw, sh)
        terrain[ring] = int(Terrain.MOUNTAIN)

        # 4. Соединяем карманы земли снаружи кольца узкими проходами.
        cls._connect_outside(terrain, ring | cls._zone_mask(sx, sy, sw, sh))

        # 5. Залежи: сначала гарантированные в стартовой зоне, потом дикие.
        deposit_id = np.full((h, w), -1, dtype=np.int32)
        deposits: list[Deposit] = []
        cls._place_deposits(deposits, deposit_id, terrain, ring, rng, items.common_ids,
                            config.DEPOSITS_PER_RESOURCE_START, (sx, sy, sw, sh))
        cls._place_deposits(deposits, deposit_id, terrain, ring, rng, items.common_ids,
                            config.DEPOSITS_PER_RESOURCE_WILD, None)
        # 6. Редкий ресурс: ровно одно месторождение на забег (plan/02), и оно в
        #    стартовой зоне — иначе до обвала гор (M8) его было бы не достать,
        #    а «стратегическое решение» превратилось бы в картинку.
        cls._place_rare(deposits, deposit_id, terrain, rng, (sx, sy, sw, sh), items)
        # 7. Гарантия топлива: горючий ресурс — рядом со стартом (см. run_gen).
        cls._bring_fuel_near(deposits, deposit_id, terrain, items, (sx, sy, sw, sh))

        return cls(seed, terrain, deposit_id, deposits, (sx, sy, sw, sh), items)

    @classmethod
    def _bring_fuel_near(cls, deposits: list[Deposit], deposit_id: np.ndarray,
                         terrain: np.ndarray, items: ItemTable,
                         rect: tuple[int, int, int, int]) -> None:
        """Ближайшую залежь горючего ресурса переносит к старту, если она далеко.

        Переносится существующая залежь (а не добавляется новая): число залежей
        каждого ресурса остаётся тем, что задано 🔧 в конфиге. Тайл — свободная
        земля в кольце от `SPAWN_CLEAR_RADIUS + 1` до 🔧 `FUEL_NEAR_SPAWN`,
        выбор детерминирован (ближайший по кругу, затем y, x).
        """
        from src.game.sim.run_gen import fuel_resource_id
        fuel = fuel_resource_id(items)
        if fuel is None:
            return
        sx, sy, sw, sh = rect
        cx, cy = sx + sw // 2, sy + sh // 2
        mine = [d for d in deposits if d.resource_id == fuel]
        if not mine:
            return
        nearest = min(mine, key=lambda d: (d.x - cx) ** 2 + (d.y - cy) ** 2)
        if max(abs(nearest.x - cx), abs(nearest.y - cy)) <= config.FUEL_NEAR_SPAWN:
            return
        near = config.SPAWN_CLEAR_RADIUS + 1
        for radius in range(near, config.FUEL_NEAR_SPAWN + 1):
            ring = [(cx + dx, cy + dy) for dy in range(-radius, radius + 1)
                    for dx in range(-radius, radius + 1)
                    if max(abs(dx), abs(dy)) == radius]
            for x, y in sorted(ring, key=lambda t: (t[1], t[0])):
                if not (0 <= x < terrain.shape[1] and 0 <= y < terrain.shape[0]):
                    continue
                if terrain[y, x] != int(Terrain.GROUND) or deposit_id[y, x] != -1:
                    continue
                deposit_id[nearest.y, nearest.x] = -1
                nearest.x, nearest.y = x, y
                deposit_id[y, x] = nearest.id
                return

    @staticmethod
    def _start_rect() -> tuple[int, int, int, int]:
        sw = sh = config.START_ZONE
        sx = (config.MAP_W - sw) // 2
        sy = (config.MAP_H - sh) // 2
        return sx, sy, sw, sh

    @staticmethod
    def _rect_mask(x0: int, y0: int, w: int, h: int) -> np.ndarray:
        """Маска прямоугольника тайлов (включительно по обеим границам)."""
        xs = np.arange(config.MAP_W, dtype=np.int32)[None, :]
        ys = np.arange(config.MAP_H, dtype=np.int32)[:, None]
        mx = (xs >= x0) & (xs <= x0 + w - 1)
        my = (ys >= y0) & (ys <= y0 + h - 1)
        return np.broadcast_to(mx, (config.MAP_H, config.MAP_W)) & \
            np.broadcast_to(my, (config.MAP_H, config.MAP_W))

    @classmethod
    def _zone_mask(cls, sx: int, sy: int, sw: int, sh: int) -> np.ndarray:
        """Маска самой стартовой зоны."""
        return cls._rect_mask(sx, sy, sw, sh)

    @classmethod
    def _ring_mask(cls, sx: int, sy: int, sw: int, sh: int) -> np.ndarray:
        """Маска горного кольца: тайлы ВНЕ зоны на расстоянии ≤ толщины (L∞).

        Сама стартовая зона в маску не входит — кольцо только вокруг неё.
        """
        e = config.MOUNTAIN_RING_THICKNESS
        outer = cls._rect_mask(sx - e, sy - e, sw + 2 * e, sh + 2 * e)
        return outer & ~cls._zone_mask(sx, sy, sw, sh)

    @classmethod
    def _connect_outside(cls, terrain: np.ndarray, forbidden: np.ndarray) -> int:
        """Прорубает проходы между карманами земли снаружи кольца.

        Зачем: при плотном рельефе проходимая земля вне стартовой зоны
        распадается на изолированные карманы. Часть залежей и будущих ростков
        (M8) оказалась бы в недостижимом кармане, и победа «уничтожить все
        ростки» стала бы невозможной. Поэтому генератор обязан гарантировать
        связность, а не надеяться на случайность.

        Тропа идёт от каждого кармана к самой большой области по кратчайшему
        пути и никогда не заходит в `forbidden` (стартовая зона и кольцо) —
        герметичность старта сохраняется.

        Возвращает число прорубленных тайлов.
        """
        h, w = terrain.shape
        open_mask = (terrain == int(Terrain.GROUND)) & ~forbidden

        # 1. Компоненты связности проходимой земли (4 соседа).
        label = np.full((h, w), -1, dtype=np.int32)
        comps: list[list[tuple[int, int]]] = []
        for sy in range(h):
            for sx in range(w):
                if not open_mask[sy, sx] or label[sy, sx] >= 0:
                    continue
                cid = len(comps)
                cells: list[tuple[int, int]] = []
                queue = deque([(sx, sy)])
                label[sy, sx] = cid
                while queue:
                    cx, cy = queue.popleft()
                    cells.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and open_mask[ny, nx] \
                                and label[ny, nx] < 0:
                            label[ny, nx] = cid
                            queue.append((nx, ny))
                comps.append(cells)

        if len(comps) < 2:
            return 0

        # 2. Волна от самой большой области по всей карте, кроме запретных тайлов.
        main_id = max(range(len(comps)), key=lambda i: len(comps[i]))
        dist = np.full((h, w), -1, dtype=np.int32)
        parent = np.full((h, w, 2), -1, dtype=np.int32)
        queue = deque(comps[main_id])
        for cx, cy in comps[main_id]:
            dist[cy, cx] = 0
        while queue:
            cx, cy = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                if forbidden[ny, nx] or dist[ny, nx] >= 0:
                    continue
                dist[ny, nx] = dist[cy, cx] + 1
                parent[ny, nx] = (cx, cy)
                queue.append((nx, ny))

        # 3. От каждого кармана — кратчайшая тропа к главной области.
        #    Все тропы кончаются в одной и той же области, поэтому одного
        #    прохода достаточно: после прорубки карманы связаны между собой.
        half = config.WILD_CORRIDOR_WIDTH // 2
        carved = 0
        for cid, cells in enumerate(comps):
            if cid == main_id:
                continue
            reachable = [c for c in cells if dist[c[1], c[0]] >= 0]
            if not reachable:
                continue  # отрезано запретной зоной — такого быть не должно
            cx, cy = min(reachable, key=lambda c: dist[c[1], c[0]])
            while dist[cy, cx] > 0:
                for oy in range(cy - half, cy - half + config.WILD_CORRIDOR_WIDTH):
                    for ox in range(cx - half, cx - half + config.WILD_CORRIDOR_WIDTH):
                        if 0 <= ox < w and 0 <= oy < h and not forbidden[oy, ox] \
                                and terrain[oy, ox] != int(Terrain.GROUND):
                            terrain[oy, ox] = int(Terrain.GROUND)
                            carved += 1
                px, py = parent[cy, cx]
                cx, cy = int(px), int(py)
        return carved

    @staticmethod
    def _stamp_disc(layer: np.ndarray, cx: float, cy: float, radius: float, value: int) -> None:
        h, w = layer.shape
        x0 = max(0, int(math.floor(cx - radius)))
        x1 = min(w, int(math.ceil(cx + radius)) + 1)
        y0 = max(0, int(math.floor(cy - radius)))
        y1 = min(h, int(math.ceil(cy + radius)) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        yy = np.arange(y0, y1, dtype=np.float32)[:, None]
        xx = np.arange(x0, x1, dtype=np.float32)[None, :]
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius * radius
        region = layer[y0:y1, x0:x1]
        region[mask] = value

    @classmethod
    def _stamp_blobs(cls, layer: np.ndarray, rng: random.Random, count: int,
                     radius_range: tuple[int, int], value: int) -> None:
        """Разбрасывает «пятна» из 2–3 наложенных дисков, чтобы форма была неровной."""
        sx, sy, sw, sh = cls._start_rect()
        e = config.MOUNTAIN_RING_THICKNESS
        for _ in range(count):
            r = rng.uniform(*radius_range)
            for _ in range(config.TERRAIN_PLACEMENT_TRIES):
                cx = rng.uniform(0, config.MAP_W)
                cy = rng.uniform(0, config.MAP_H)
                # не задеваем стартовую зону и её горное кольцо
                if (sx - e - r <= cx <= sx + sw - 1 + e + r and
                        sy - e - r <= cy <= sy + sh - 1 + e + r):
                    continue
                break
            else:
                continue
            cls._stamp_disc(layer, cx, cy, r, value)
            for _ in range(rng.randint(1, 2)):
                ox = cx + rng.uniform(-r, r)
                oy = cy + rng.uniform(-r, r)
                cls._stamp_disc(layer, ox, oy, r * rng.uniform(0.4, 0.8), value)

    @classmethod
    def _place_deposits(cls, deposits: list[Deposit], deposit_id: np.ndarray,
                        terrain: np.ndarray, ring: np.ndarray, rng: random.Random,
                        resource_ids: tuple[str, ...], per_resource: int,
                        rect: tuple[int, int, int, int] | None) -> None:
        """Раскладывает залежи. При rect — внутри зоны, иначе всюду вне кольца."""
        for resource_id in resource_ids:
            for _ in range(per_resource):
                spot = cls._find_spot(deposits, deposit_id, terrain, ring, rng, rect,
                                      config.DEPOSIT_MIN_SPACING)
                if spot is None:
                    # без учёта расстояния — гарантия, что ресурс доступен
                    spot = cls._find_spot(deposits, deposit_id, terrain, ring, rng, rect, 1)
                if spot is None:
                    continue
                cls._add_deposit(deposits, deposit_id, resource_id, spot,
                                 config.DEPOSIT_AMOUNT)

    @classmethod
    def _place_rare(cls, deposits: list[Deposit], deposit_id: np.ndarray,
                    terrain: np.ndarray, rng: random.Random,
                    rect: tuple[int, int, int, int], items: ItemTable) -> None:
        """Единственное месторождение редкого ресурса (plan/02)."""
        if items.rare_id is None:
            return
        ring = np.zeros_like(deposit_id, dtype=bool)
        for _ in range(config.RARE_DEPOSITS):
            spot = cls._find_spot(deposits, deposit_id, terrain, ring, rng, rect,
                                  config.DEPOSIT_MIN_SPACING)
            if spot is None:
                spot = cls._find_spot(deposits, deposit_id, terrain, ring, rng, rect, 1)
            if spot is None:
                continue
            cls._add_deposit(deposits, deposit_id, items.rare_id, spot,
                             config.RARE_DEPOSIT_AMOUNT)

    @staticmethod
    def _add_deposit(deposits: list[Deposit], deposit_id: np.ndarray,
                     resource_id: str, spot: tuple[int, int], amount: int) -> None:
        d = Deposit(len(deposits), resource_id, spot[0], spot[1], amount)
        deposits.append(d)
        deposit_id[d.y, d.x] = d.id

    @classmethod
    def _find_spot(cls, deposits: list[Deposit], deposit_id: np.ndarray,
                   terrain: np.ndarray, ring: np.ndarray, rng: random.Random,
                   rect: tuple[int, int, int, int] | None, spacing: int) -> tuple[int, int] | None:
        sx, sy, sw, sh = cls._start_rect()
        spawn = (sx + sw // 2, sy + sh // 2)
        for _ in range(config.DEPOSIT_PLACEMENT_TRIES * 8):
            if rect is not None:
                rx, ry, rw, rh = rect
                x = rng.randrange(rx, rx + rw)
                y = rng.randrange(ry, ry + rh)
            else:
                x = rng.randrange(0, config.MAP_W)
                y = rng.randrange(0, config.MAP_H)
                if ring[y, x] or World._in_rect(x, y, (sx, sy, sw, sh)):
                    continue
            if terrain[y, x] != int(Terrain.GROUND):
                continue
            if deposit_id[y, x] != -1:
                continue
            if abs(x - spawn[0]) <= config.SPAWN_CLEAR_RADIUS and \
                    abs(y - spawn[1]) <= config.SPAWN_CLEAR_RADIUS:
                continue
            if any(max(abs(x - d.x), abs(y - d.y)) < spacing for d in deposits):
                continue
            return x, y
        return None

    @staticmethod
    def _in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    # ------------------------------------------------------------- запросы
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def tile(self, x: int, y: int) -> int:
        """Тип тайла. Вне карты — гора (непроходимо)."""
        if not self.in_bounds(x, y):
            return int(Terrain.MOUNTAIN)
        return int(self.terrain[y, x])

    def terrain_at(self, fx: float, fy: float) -> int:
        """Тип тайла по мировым (дробным) координатам."""
        return self.tile(int(math.floor(fx)), int(math.floor(fy)))

    def is_walkable_tile(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        return terrain_type(self.tile(x, y)).walkable

    def deposit_at(self, x: int, y: int) -> Deposit | None:
        if not self.in_bounds(x, y):
            return None
        did = int(self.deposit_id[y, x])
        return self.deposits[did] if did >= 0 else None

    def active_deposits(self) -> list[Deposit]:
        return [d for d in self.deposits if not d.depleted]

    def circle_hits_blocked(self, fx: float, fy: float, radius: float) -> bool:
        """Пересекается ли круг (дрон) с непроходимым тайлом."""
        if radius <= 0:
            return not self.is_walkable_tile(int(math.floor(fx)), int(math.floor(fy)))
        x0 = int(math.floor(fx - radius))
        x1 = int(math.floor(fx + radius))
        y0 = int(math.floor(fy - radius))
        y1 = int(math.floor(fy + radius))
        r2 = radius * radius
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                if not self.in_bounds(tx, ty):
                    return True
                if self.is_walkable_tile(tx, ty):
                    continue
                nx = min(max(fx, tx), tx + 1.0)
                ny = min(max(fy, ty), ty + 1.0)
                if (fx - nx) ** 2 + (fy - ny) ** 2 < r2:
                    return True
        return False

    def spawn_point(self) -> tuple[float, float]:
        """Центр стартовой зоны — здесь появляется дрон."""
        x, y, w, h = self.start_rect
        return x + w / 2.0, y + h / 2.0

    def in_start_zone(self, x: int, y: int) -> bool:
        """Внутри стартовой зоны? До обвала гор (M8) это и есть весь доступный мир."""
        sx, sy, sw, sh = self.start_rect
        return sx <= x < sx + sw and sy <= y < sy + sh

    def start_zone_tiles(self):
        x, y, w, h = self.start_rect
        for ty in range(y, y + h):
            for tx in range(x, x + w):
                yield tx, ty

    def tile_variant(self, x: int, y: int) -> int:
        """Стабильный «шум» для процедурной вариации цвета тайла."""
        h = (x * 73856093) ^ (y * 19349663)
        return h % 4
