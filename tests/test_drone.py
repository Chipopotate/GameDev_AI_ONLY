"""Движение и коллизии дрона."""

from __future__ import annotations

import math

import pytest

from src.game import config
from src.game.data.terrain import Terrain
from src.game.sim.units import Drone

G = int(Terrain.GROUND)
M = int(Terrain.MOUNTAIN)
W = int(Terrain.WATER)


def step(drone: Drone, world, ix: float, iy: float, seconds: float,
         dt: float = config.TICK) -> float:
    """Прогоняет движение ровно `seconds` секунд фиксированными шагами."""
    total = 0.0
    for _ in range(int(round(seconds / dt))):
        total += drone.move(world, ix, iy, dt)
    return total


def test_spawn_at_world_spawn_point(world):
    drone = Drone.spawn(world)
    assert (drone.x, drone.y) == world.spawn_point()
    assert drone.alive


def test_moves_at_configured_speed(empty_world_factory):
    world = empty_world_factory(40, 20)
    drone = Drone(x=5.0, y=5.0)
    step(drone, world, 1.0, 0.0, 1.0)
    assert drone.x == pytest.approx(5.0 + config.DRONE_SPEED, abs=1e-6)
    assert drone.y == pytest.approx(5.0)


def test_diagonal_speed_is_normalized(empty_world_factory):
    world = empty_world_factory(40, 40)
    drone = Drone(x=10.0, y=10.0)
    step(drone, world, 1.0, 1.0, 1.0)
    dist = math.hypot(drone.x - 10.0, drone.y - 10.0)
    assert dist == pytest.approx(config.DRONE_SPEED, rel=1e-6)


def test_no_input_no_movement(empty_world_factory):
    world = empty_world_factory()
    drone = Drone(x=5.0, y=5.0)
    assert step(drone, world, 0.0, 0.0, 1.0) == 0.0
    assert (drone.x, drone.y) == (5.0, 5.0)


def test_mountain_blocks_movement(world_factory):
    # стена из гор в колонке x=6
    rows = [[G] * 12 for _ in range(12)]
    for y in range(12):
        rows[y][6] = M
    world = world_factory(rows)
    drone = Drone(x=4.0, y=5.5)
    step(drone, world, 1.0, 0.0, 2.0)
    assert drone.x < 6.0 - config.DRONE_RADIUS + 1e-6
    assert drone.x > 5.0


def test_water_blocks_movement(world_factory):
    rows = [[G] * 12 for _ in range(12)]
    for y in range(12):
        rows[y][6] = W
    world = world_factory(rows)
    drone = Drone(x=4.0, y=5.5)
    step(drone, world, 1.0, 0.0, 2.0)
    assert drone.x < 6.0


def test_slides_along_wall(world_factory):
    """Диагональ в стену: движение по свободной оси сохраняется."""
    rows = [[G] * 12 for _ in range(12)]
    for x in range(12):
        rows[3][x] = M  # горизонтальная стена сверху
    world = world_factory(rows)
    drone = Drone(x=5.0, y=5.0)
    step(drone, world, 1.0, -1.0, 0.5)
    assert drone.x > 5.0, "дрон должен скользить вправо вдоль стены"
    assert drone.y < 5.0
    assert drone.y > 3.0 + config.DRONE_RADIUS


def test_cannot_leave_map(world_factory):
    world = world_factory([[G] * 8 for _ in range(8)])
    drone = Drone(x=4.0, y=4.0)
    step(drone, world, -1.0, 0.0, 5.0)
    assert drone.x >= config.DRONE_RADIUS - 1e-6


def test_facing_follows_direction(empty_world_factory):
    world = empty_world_factory(20, 20)
    drone = Drone(x=10.0, y=10.0)
    step(drone, world, 0.0, 1.0, 0.1)
    assert drone.facing == pytest.approx(math.pi / 2)
    step(drone, world, -1.0, 0.0, 0.1)
    assert drone.facing == pytest.approx(math.pi)


def test_tile_and_distance_helpers(empty_world_factory):
    world = empty_world_factory(20, 20)
    drone = Drone(x=3.4, y=7.9)
    assert drone.tile() == (3, 7)
    # до центра тайла (3, 8) → (3.5, 8.5): dx=0.1, dy=0.6
    assert drone.distance_to_tile(3, 8) == pytest.approx(math.hypot(0.1, 0.6))


def test_cannot_escape_start_zone_in_generated_world(world):
    """Интеграционная проверка: кольцо гор держит дрон."""
    drone = Drone.spawn(world)
    for ix, iy in ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1)):
        step(drone, world, ix, iy, 6.0)
    zx, zy, zw, zh = world.start_rect
    tx, ty = drone.tile()
    assert zx <= tx < zx + zw
    assert zy <= ty < zy + zh
