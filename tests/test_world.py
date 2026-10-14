"""Генерация карты, герметичность старта, залежи."""

from __future__ import annotations

from collections import deque

import numpy as np
import pytest

from src.game import config
from src.game.data.terrain import Terrain
from src.game.sim.run_gen import generate_run
from src.game.sim.world import World
from tests.conftest import SEED


def reachable_tiles(world: World) -> set[tuple[int, int]]:
    """Волновой обход проходимых тайлов от точки старта."""
    start = tuple(int(v) for v in world.spawn_point())
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not world.is_walkable_tile(nx, ny):
                continue
            seen.add((nx, ny))
            queue.append((nx, ny))
    return seen


def in_start_zone(world: World, x: int, y: int) -> bool:
    zx, zy, zw, zh = world.start_rect
    return zx <= x < zx + zw and zy <= y < zy + zh


def outside_tiles(world: World) -> set[tuple[int, int]]:
    """Проходимые тайлы за пределами стартовой зоны."""
    return {(x, y) for y in range(world.height) for x in range(world.width)
            if not in_start_zone(world, x, y) and world.is_walkable_tile(x, y)}


def connected_components(tiles: set[tuple[int, int]]) -> list[int]:
    """Размеры связных областей (4 соседа) в множестве тайлов."""
    seen: set[tuple[int, int]] = set()
    sizes: list[int] = []
    for start in tiles:
        if start in seen:
            continue
        seen.add(start)
        queue = deque([start])
        size = 0
        while queue:
            x, y = queue.popleft()
            size += 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p = (x + dx, y + dy)
                if p in tiles and p not in seen:
                    seen.add(p)
                    queue.append(p)
        sizes.append(size)
    return sorted(sizes, reverse=True)


# --------------------------------------------------------------- генерация
def test_generation_is_deterministic(world):
    same = World.generate(SEED)
    assert np.array_equal(world.terrain, same.terrain)
    assert np.array_equal(world.deposit_id, same.deposit_id)
    assert [(d.resource_id, d.x, d.y, d.amount) for d in world.deposits] == \
           [(d.resource_id, d.x, d.y, d.amount) for d in same.deposits]


def test_different_seeds_differ(world):
    other = World.generate(SEED + 1)
    assert not np.array_equal(world.terrain, other.terrain)


def test_map_size_matches_config(world):
    assert world.width == config.MAP_W
    assert world.height == config.MAP_H
    assert world.terrain.shape == (config.MAP_H, config.MAP_W)


def test_map_has_all_three_terrain_types(world):
    kinds = set(np.unique(world.terrain).tolist())
    assert kinds == {int(Terrain.GROUND), int(Terrain.MOUNTAIN), int(Terrain.WATER)}


def test_start_zone_is_clear_ground(world):
    zx, zy, zw, zh = world.start_rect
    zone = world.terrain[zy:zy + zh, zx:zx + zw]
    assert (zone == int(Terrain.GROUND)).all()
    assert zw == config.START_ZONE and zh == config.START_ZONE


def test_start_is_surrounded_by_mountains(world):
    """Дрон не может выйти за стартовую зону: кольцо гор замкнуто."""
    outside = [t for t in reachable_tiles(world) if not in_start_zone(world, *t)]
    assert outside == []


def test_reachable_area_is_the_start_zone(world):
    reachable = reachable_tiles(world)
    zx, zy, zw, zh = world.start_rect
    assert len(reachable) == zw * zh


def test_walkable_land_outside_ring_is_one_region(world):
    """Гарантия генератора: снаружи кольца земля связна.

    Иначе часть залежей и будущих ростков (M8) попала бы в недостижимый
    карман, и победа «уничтожить все ростки» стала бы невозможной.
    """
    outside = outside_tiles(world)
    assert outside, "снаружи кольца вообще нет проходимой земли"
    assert connected_components(outside) == [len(outside)]


def test_corridors_do_not_breach_the_start_ring(world):
    """Проходы не имеют права задеть кольцо: старт остаётся запечатанным."""
    ring = World._ring_mask(*world.start_rect)
    zone = World._zone_mask(*world.start_rect)
    assert (world.terrain[ring] == int(Terrain.MOUNTAIN)).all()
    assert (world.terrain[zone] == int(Terrain.GROUND)).all()


def test_spawn_point_is_walkable(world):
    x, y = world.spawn_point()
    assert world.is_walkable_tile(int(x), int(y))
    assert not world.circle_hits_blocked(x, y, config.DRONE_RADIUS)


# ------------------------------------------------------------------ залежи
def test_world_uses_run_resources(world):
    """Карта и ресурсы забега обязаны сходиться: залежь ссылается на ресурс."""
    assert world.items.natural_ids == generate_run(SEED).items.natural_ids
    for d in world.deposits:
        assert d.resource_id in world.items


def test_deposit_count(world):
    per = config.DEPOSITS_PER_RESOURCE_START + config.DEPOSITS_PER_RESOURCE_WILD
    assert len(world.deposits) == per * len(world.items.common_ids) + \
        config.RARE_DEPOSITS


def test_deposits_are_on_ground_inside_map(world):
    for d in world.deposits:
        assert world.in_bounds(d.x, d.y)
        assert world.tile(d.x, d.y) == int(Terrain.GROUND), \
            f"залежь {d.resource_id} стоит на непроходимом тайле {d.x},{d.y}"
        assert d.amount > 0


def test_deposits_occupy_unique_tiles(world):
    tiles = [(d.x, d.y) for d in world.deposits]
    assert len(set(tiles)) == len(tiles)


def test_deposit_ids_match_layer(world):
    occupied = {(d.x, d.y) for d in world.deposits}
    for d in world.deposits:
        assert int(world.deposit_id[d.y, d.x]) == d.id
        assert world.deposit_at(d.x, d.y) is d
    empty = next(t for t in world.start_zone_tiles() if t not in occupied)
    assert world.deposit_at(*empty) is None


def test_every_resource_has_deposits_in_start_zone(world):
    """Гарантия доступности: каждый ресурс можно добыть до первой ночи.

    До обвала гор (M8) стартовая зона — весь доступный мир, поэтому редкий
    ресурс тоже лежит в ней: иначе он был бы недостижим (plan/02).
    """
    found = {d.resource_id for d in world.deposits if in_start_zone(world, d.x, d.y)}
    assert found == set(world.items.natural_ids)


def test_rare_resource_has_exactly_one_deposit(world):
    """Ровно одно месторождение редкого ресурса на забег (plan/02)."""
    rare_id = world.items.rare_id
    rare = [d for d in world.deposits if d.resource_id == rare_id]
    assert len(rare) == config.RARE_DEPOSITS
    assert all(in_start_zone(world, d.x, d.y) for d in rare)
    assert rare[0].amount == config.RARE_DEPOSIT_AMOUNT


def test_in_start_zone_matches_start_rect(world):
    zx, zy, zw, zh = world.start_rect
    assert world.in_start_zone(zx, zy) and world.in_start_zone(zx + zw - 1, zy + zh - 1)
    assert not world.in_start_zone(zx - 1, zy) and not world.in_start_zone(zx + zw, zy)


@pytest.mark.slow
def test_guarantees_hold_for_many_seeds():
    """Те же гарантии на сорока сидах — как требует docs/02 для генератора."""
    for seed in range(40):
        w = World.generate(seed)
        assert reachable_tiles(w) == set(w.start_zone_tiles()), f"seed {seed}: старт не замкнут"
        outside = outside_tiles(w)
        assert connected_components(outside) == [len(outside)], \
            f"seed {seed}: земля снаружи кольца распалась на карманы"
        in_zone = {d.resource_id for d in w.deposits if in_start_zone(w, d.x, d.y)}
        assert in_zone == set(w.items.natural_ids), \
            f"seed {seed}: не все ресурсы доступны"
        for d in w.deposits:
            assert w.tile(d.x, d.y) == int(Terrain.GROUND), \
                f"seed {seed}: залежь {d.resource_id} на непроходимом тайле"
        tiles = [(d.x, d.y) for d in w.deposits]
        assert len(set(tiles)) == len(tiles), f"seed {seed}: залежи наложились"


# ------------------------------------------------------------------ запросы
def test_tile_out_of_bounds_is_mountain(world):
    assert world.tile(-1, 0) == int(Terrain.MOUNTAIN)
    assert world.tile(0, -1) == int(Terrain.MOUNTAIN)
    assert world.tile(world.width, 0) == int(Terrain.MOUNTAIN)
    assert not world.in_bounds(-1, -1)


def test_circle_hits_blocked_outside_map(world):
    assert world.circle_hits_blocked(-1.0, 5.0, 0.3)


def test_circle_hits_blocked_in_mountain(world):
    zx, zy, zw, zh = world.start_rect
    mx, my = zx - 1, zy + zh // 2  # тайл сразу за кольцом
    assert world.tile(mx, my) == int(Terrain.MOUNTAIN)
    assert world.circle_hits_blocked(mx + 0.5, my + 0.5, config.DRONE_RADIUS)


def test_tile_variant_is_stable_and_in_range(world):
    for x, y in ((0, 0), (5, 7), (95, 95)):
        v = world.tile_variant(x, y)
        assert 0 <= v < 4
        assert v == world.tile_variant(x, y)
