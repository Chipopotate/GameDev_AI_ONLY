"""Общие фикстуры. Тесты идут без окна: SDL переводится в dummy-режим."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from src.game import config  # noqa: E402
from src.game.data.buildings import GENERATOR, building as building_def  # noqa: E402
from src.game.data.recipes import ROLES  # noqa: E402
from src.game.data.resources import Prop  # noqa: E402
from src.game.data.terrain import Terrain  # noqa: E402
from src.game.sim.buildings import DIRECTIONS  # noqa: E402
from src.game.sim.simulation import Simulation  # noqa: E402
from src.game.sim.world import World  # noqa: E402

SEED = 20251013

#: По стаку каждого предмета роли — столько даёт `stock` (лимит стека = 1 слот).
STOCK_COUNT = 30


def stock(sim: Simulation, count: int = STOCK_COUNT) -> Simulation:
    """Выдаёт дрону по стаку каждого предмета роли — «экономика M4» для тестов.

    С M4 здания стоят предметов ролей, а скан — энергии. Тесты, которые
    проверяют стройку и логистику, а не саму экономику, берут этот запас:
    тогда цена не мешает проверять то, ради чего тест написан.
    """
    for role in ROLES:
        sim.inventory.add(role.id, count)
    return sim


def slot_of(container, item_id: str) -> int:
    """Номер слота с предметом: запас ролей от `stock` занимает первые слоты."""
    for index in range(container.size):
        stack = container.slot(index)
        if stack is not None and stack.item_id == item_id:
            return index
    raise AssertionError(f"{item_id} не найден в контейнере")


def power_grid(sim: Simulation, x: int, y: int, fuel: int = STOCK_COUNT):
    """Ставит заправленный генератор вплотную к тайлу (x, y) и возвращает его.

    Сеть в M4 — это соседние тайлы, поэтому генератор рядом и есть минимальная
    электростанция: лампы и здания на соседних тайлах получают от него энергию.
    Топливо кладётся прямо в топку — как если бы игрок его туда положил.
    Без `sim.update` сеть ещё не пересчитана: питание появляется на первом шаге.
    """
    stock(sim)
    for dx, dy in DIRECTIONS:
        if not sim._tile_ok_for(GENERATOR, x + dx, y + dy):
            continue
        generator = sim.build(GENERATOR, x + dx, y + dy)
        assert generator is not None, "генератор не встал: проверь запас материалов"
        fuel_id = sim.run.items.with_prop(Prop.FLAMMABLE)[0].id
        generator.container(building_def(GENERATOR).accept_id).add(fuel_id, fuel)
        return generator
    raise AssertionError(f"рядом с тайлом {x}, {y} нет места под генератор")


def power_lamp(sim: Simulation, x: int, y: int, fuel: int = STOCK_COUNT):
    """Горящая лампа на тайле (x, y) — для тестов света и отрисовки.

    С M4 лампа горит, только пока её питает сеть, поэтому рядом встаёт
    заправленный генератор, а два шага симуляции зажигают топку и раздают
    энергию (порядок внутри `Simulation._update_power`).
    """
    stock(sim)
    lamp = sim.place_lamp(x, y)
    assert lamp is not None, "лампа не встала: проверь место и запас материалов"
    power_grid(sim, x, y, fuel)
    for _ in range(2):
        sim.update(config.TICK)
    assert lamp.on, "лампа не загорелась от генератора"
    return lamp


def make_world(rows: list[list[int]], deposits: list | None = None) -> World:
    """Собирает мир из готовой карты — удобно для точных тестов движения."""
    terrain = np.array(rows, dtype=np.int8)
    h, w = terrain.shape
    deposit_id = np.full((h, w), -1, dtype=np.int32)
    deposit_list = list(deposits or [])
    for d in deposit_list:
        deposit_id[d.y, d.x] = d.id
    return World(
        seed=0,
        terrain=terrain,
        deposit_id=deposit_id,
        deposits=deposit_list,
        start_rect=(0, 0, w, h),
    )


def flat_world(w: int = 12, h: int = 12) -> World:
    """Ровная земля без препятствий."""
    return make_world([[int(Terrain.GROUND)] * w for _ in range(h)])


@pytest.fixture
def world() -> World:
    return World.generate(SEED)


@pytest.fixture
def sim() -> Simulation:
    return Simulation(SEED)


@pytest.fixture
def empty_world_factory():
    return flat_world


@pytest.fixture
def world_factory():
    return make_world
