"""Предметы на земле: выкинуть, подобрать, разложить после сноса (M3.5, plan/03).

Предмет лежит **на тайле**, поэтому хранилище — словарь «тайл → список стеков»:
подбор и отрисовка смотрят только на тайлы рядом с дроном и в кадре, а не на
всю карту. Стеки одного ресурса на одном тайле сливаются, так что рюкзак,
высыпанный из снесённой лаборатории, лежит аккуратными кучками.

Три правила, без которых это работало бы неправильно:

- **предмет не исчезает сам.** 🔧 `DROP_DESPAWN_SECONDS = None`: подобранное
  игроком должно лежать, пока за ним не вернутся. Таймер остался в коде — на
  M5 он понадобится мусору от врагов;
- **падает дальше радиуса подбора** (`DROP_DISTANCE` > `PICKUP_RADIUS`) плюс
  задержка 🔧 `DROP_PICKUP_DELAY` — иначе стоящий на месте дрон подберёт свой же
  выброшенный стак;
- **подбирается столько, сколько влезет.** Остаток остаётся лежать: инвентарь
  не бездонный, но и предметы не пропадают.

Враги предметы не трогают (plan/03) — трогать их будут только правила игры,
поэтому никакого «мира взаимодействий» здесь нет.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.game import config
from src.game.sim.resources import Container

#: Тайл, на котором лежат предметы. Ключ словаря `DropSystem.tiles`.
Tile = tuple[int, int]


@dataclass
class ItemDrop:
    """Стак предметов, лежащий на одном тайле карты."""

    id: int
    item_id: str
    count: int
    x: int                        # тайл, а не пиксели: предмет лежит на клетке
    y: int
    age: float = 0.0
    delay: float = 0.0            # сколько ещё нельзя подобрать, с

    @property
    def tile(self) -> Tile:
        return (self.x, self.y)

    @property
    def center(self) -> tuple[float, float]:
        """Мировая точка предмета — центр тайла (для расстояний и отрисовки)."""
        return (self.x + 0.5, self.y + 0.5)

    @property
    def expiring(self) -> bool:
        """Мигает ли предмет перед исчезновением. Без таймера — никогда."""
        ttl = config.DROP_DESPAWN_SECONDS
        if ttl is None:
            return False
        return self.age >= ttl - config.DROP_BLINK_TIME

    @property
    def alive_ratio(self) -> float:
        """1 — предмет только упал, 0 — вот-вот исчезнет (без таймера — всегда 1)."""
        ttl = config.DROP_DESPAWN_SECONDS
        if ttl is None or ttl <= 0.0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - self.age / ttl))

    def distance_to(self, x: float, y: float) -> float:
        """Расстояние от мировой точки до предмета, тайлы."""
        cx, cy = self.center
        return math.hypot(cx - x, cy - y)


@dataclass(frozen=True)
class PickupResult:
    """Что дрон подобрал за шаг: симуляция сообщает об этом игроку."""

    item_id: str
    count: int
    left: int = 0                 # сколько осталось лежать (места не хватило)


class DropSystem:
    """Предметы на земле. Обновляется симуляцией, рисуется рендером."""

    def __init__(self) -> None:
        #: Тайл → стеки на нём. Порядок ключей — порядок появления тайлов,
        #: поэтому обход детерминирован (это важно для подбора и для тестов).
        self.tiles: dict[Tile, list[ItemDrop]] = {}
        self._next_id = 0

    # ------------------------------------------------------------ запросы
    def __len__(self) -> int:
        return sum(len(stack) for stack in self.tiles.values())

    @property
    def items(self) -> list[ItemDrop]:
        """Все стеки плоским списком (для состояния и подсказок)."""
        return [drop for stack in self.tiles.values() for drop in stack]

    def drops_at(self, x: int, y: int) -> tuple[ItemDrop, ...]:
        return tuple(self.tiles.get((int(x), int(y)), ()))

    def at(self, x: int, y: int) -> ItemDrop | None:
        """Первый стек на тайле (для подсказки в HUD)."""
        stack = self.tiles.get((int(x), int(y)))
        return stack[0] if stack else None

    def total(self) -> dict[str, int]:
        """Сколько чего лежит на земле: сводка «предмет → количество»."""
        totals: dict[str, int] = {}
        for drop in self.items:
            totals[drop.item_id] = totals.get(drop.item_id, 0) + drop.count
        return totals

    # -------------------------------------------------------------- запись
    def spawn(self, item_id: str, count: int, x: float, y: float,
              delay: float = config.DROP_PICKUP_DELAY) -> ItemDrop | None:
        """Кладёт стак на тайл под мировой точкой (x, y). None — если класть нечего."""
        return self.spawn_tile(item_id, count, int(math.floor(x)), int(math.floor(y)),
                               delay)

    def spawn_tile(self, item_id: str, count: int, x: int, y: int,
                   delay: float = 0.0) -> ItemDrop | None:
        """Кладёт стак на конкретный тайл, досыпая в такие же стеки.

        Стеки одного ресурса сливаются до 🔧 `SLOT_STACK_MAX`: высыпанные 100
        единиц — это четыре кучи, а не сто. Первый созданный стек возвращается
        вызывающему (остальным он не нужен).
        """
        count = int(count)
        if count <= 0:
            return None
        tile = (int(x), int(y))
        stack_here = self.tiles.setdefault(tile, [])
        first: ItemDrop | None = None
        for drop in stack_here:                       # досыпаем в неполные стеки
            if count <= 0:
                break
            if drop.item_id != item_id:
                continue
            room = config.SLOT_STACK_MAX - drop.count
            if room <= 0:
                continue
            moved = min(room, count)
            drop.count += moved
            count -= moved
            first = first or drop
        while count > 0:                              # остаток — новыми стеками
            part = min(config.SLOT_STACK_MAX, count)
            drop = ItemDrop(self._next_id, item_id, part, tile[0], tile[1],
                            delay=delay)
            self._next_id += 1
            stack_here.append(drop)
            count -= part
            first = first or drop
        return first

    # ----------------------------------------------------------- обновление
    def update(self, dt: float, container: Container, x: float,
               y: float) -> tuple[PickupResult, ...]:
        """Стареет предметы и подбирает те, что рядом с дроном.

        `container` — контейнер дрона: сюда уходит подобранное. Возвращает
        подобранное за шаг, чтобы симуляция сказала об этом игроку.
        """
        if dt <= 0.0:
            return ()
        self._age(dt)
        picked: list[PickupResult] = []
        for drop in self._near(x, y):
            if drop.delay > 0.0:
                continue
            rest = container.add(drop.item_id, drop.count)
            moved = drop.count - rest
            if moved <= 0:
                continue
            drop.count = rest
            picked.append(PickupResult(drop.item_id, moved, rest))
        self._drop_empty_tiles()
        return self._merge(picked)

    def _age(self, dt: float) -> None:
        """Считает возраст и убирает предметы, у которых вышел срок."""
        ttl = config.DROP_DESPAWN_SECONDS
        for tile in list(self.tiles):
            for drop in self.tiles[tile]:
                drop.age += dt
                if drop.delay > 0.0:
                    drop.delay = max(0.0, drop.delay - dt)
            if ttl is None:
                continue
            alive = [d for d in self.tiles[tile]
                     if not (d.count <= 0 or d.age >= ttl)]
            if alive:
                self.tiles[tile] = alive
            else:
                del self.tiles[tile]

    def _near(self, x: float, y: float) -> list[ItemDrop]:
        """Стеки на тайлах, которые попадают в радиус подбора вокруг точки."""
        radius = config.PICKUP_RADIUS
        tx0 = int(math.floor(x - radius))
        tx1 = int(math.floor(x + radius))
        ty0 = int(math.floor(y - radius))
        ty1 = int(math.floor(y + radius))
        near: list[ItemDrop] = []
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                for drop in self.tiles.get((tx, ty), ()):
                    if drop.distance_to(x, y) <= radius:
                        near.append(drop)
        return near

    def _drop_empty_tiles(self) -> None:
        """Убирает тайлы, с которых всё подобрали (пустые списки не храним)."""
        for tile in [t for t, stack in self.tiles.items()
                     if not stack or all(d.count <= 0 for d in stack)]:
            del self.tiles[tile]

    @staticmethod
    def _merge(picked: list[PickupResult]) -> tuple[PickupResult, ...]:
        """Сводит подбор по предметам: за шаг игроку нужно одно сообщение."""
        merged: dict[str, PickupResult] = {}
        for result in picked:
            old = merged.get(result.item_id)
            if old is None:
                merged[result.item_id] = result
            else:
                merged[result.item_id] = PickupResult(
                    result.item_id, old.count + result.count, result.left)
        return tuple(merged.values())

    # ------------------------------------------------------------- для тестов
    def state(self) -> tuple:
        return tuple((tile, tuple((d.id, d.item_id, d.count) for d in stack))
                     for tile, stack in sorted(self.tiles.items()))
