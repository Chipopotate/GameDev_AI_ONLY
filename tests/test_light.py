"""Свет и тьма: круги ламп, пересчёт только при изменении, доля освещённого."""

from __future__ import annotations

import pytest

from src.game import config
from src.game.data.buildings import LAMP
from src.game.data.terrain import Terrain
from src.game.sim.light import LightSystem
from src.game.sim.run_gen import generate_run
from src.game.sim.simulation import Simulation
from tests.conftest import make_world, power_grid, stock

GROUND = int(Terrain.GROUND)
MOUNTAIN = int(Terrain.MOUNTAIN)
WALL_X = 7  # на тестовой карте правее этой колонки — сплошная гора


def flat(w: int = 12, h: int = 12) -> LightSystem:
    """Слой света на ровной карте без препятствий."""
    return LightSystem(make_world([[GROUND] * w for _ in range(h)]))


def walled(w: int = 13, h: int = 13) -> LightSystem:
    """Карта, разрезанная горной стеной: со старта доступна только левая часть."""
    rows = [[GROUND if x < WALL_X else MOUNTAIN for x in range(w)] for _ in range(h)]
    return LightSystem(make_world(rows))


# ------------------------------------------------------------------- круги
def test_dark_without_lamps():
    light = flat()
    assert light.lamps == []
    assert not light.is_lit(5, 5)
    assert light.lit_ratio() == 0.0


def test_lamp_lights_a_circle():
    light = flat()
    light.add_lamp(5, 5, radius=2.0)
    assert light.is_lit(5, 5), "тайл под лампой освещён"
    assert light.is_lit(7, 5), "на границе радиуса — ещё освещён"
    assert not light.is_lit(8, 5), "за радиусом — темно"
    assert not light.is_lit(7, 7), "круг, а не квадрат"


def test_two_lamps_cover_more_than_one():
    light = flat()
    light.add_lamp(2, 2, radius=2.0)
    one = light.lit_ratio()
    light.add_lamp(9, 9, radius=2.0)
    assert light.lit_ratio() > one


def test_lamp_outside_the_map_is_safe():
    light = flat()
    light.add_lamp(0, 0, radius=50.0)
    assert light.is_lit(-1, -1) is False
    assert light.is_lit(12, 12) is False
    assert light.is_lit(0, 0)


def test_disabled_lamp_gives_no_light():
    light = flat()
    lamp = light.add_lamp(5, 5, radius=2.0)
    light.set_lamp_on(lamp, False)
    assert not light.is_lit(5, 5)
    light.set_lamp_on(lamp, True)
    assert light.is_lit(5, 5)


def test_removed_lamp_gives_no_light():
    light = flat()
    lamp = light.add_lamp(5, 5, radius=2.0)
    assert light.remove_lamp(lamp) is True
    assert not light.is_lit(5, 5)
    assert light.remove_lamp(lamp) is False, "повторный снос ничего не делает"


# --------------------------------------------------- пересчёт только при правке
def test_layer_is_rebuilt_once_then_cached():
    light = flat()
    light.add_lamp(5, 5)
    assert light.rebuilds == 0, "до первого чтения слоя пересчёта нет"
    light.is_lit(5, 5)
    assert light.rebuilds == 1
    for _ in range(10):
        light.is_lit(5, 5)
        light.lit_ratio()
    assert light.rebuilds == 1, "чтение не должно пересчитывать свет"


@pytest.mark.parametrize("action", ["add", "remove", "off"])
def test_every_change_triggers_exactly_one_rebuild(action):
    light = flat()
    lamp = light.add_lamp(5, 5)
    light.is_lit(0, 0)
    before = light.rebuilds
    if action == "add":
        light.add_lamp(8, 8)
    elif action == "remove":
        light.remove_lamp(lamp)
    else:
        light.set_lamp_on(lamp, False)
    light.is_lit(0, 0)
    assert light.rebuilds == before + 1
    light.is_lit(0, 0)
    assert light.rebuilds == before + 1


def test_setting_same_state_does_not_rebuild():
    light = flat()
    lamp = light.add_lamp(5, 5)
    light.is_lit(0, 0)
    before = light.rebuilds
    light.set_lamp_on(lamp, True)
    light.is_lit(0, 0)
    assert light.rebuilds == before


# --------------------------------------------------------------- доля света
def test_ratio_grows_with_lamps_and_reaches_one():
    light = flat()
    light.add_lamp(6, 6, radius=30.0)
    assert light.lit_ratio() == pytest.approx(1.0), "лампа накрыла всю карту"


def test_ratio_ignores_unreachable_tiles():
    """Свет за горной стеной не считается: доступные тайлы — только со стартом."""
    light = walled()
    light.add_lamp(10, 10, radius=2.0)  # за стеной, до старта не дойти
    assert light.lit_ratio() == 0.0
    light.add_lamp(2, 2, radius=30.0)   # накрыла и недоступную половину
    assert light.lit_ratio() == pytest.approx(1.0)


def test_ratio_of_walled_off_half_is_relative_to_reachable():
    light = walled()
    light.add_lamp(2, 2, radius=3.0)
    reachable = WALL_X * 13
    lit_reachable = 27  # круг радиуса 3 вокруг (2, 2), без тайлов за краем карты
    assert light.lit_ratio() == pytest.approx(lit_reachable / reachable)


# ------------------------------------------------------------ через симуляцию
def test_simulation_counts_lamps_as_light():
    sim = stock(Simulation(0, world=make_world([[GROUND] * 12 for _ in range(12)])))
    assert sim.tiles_lit_ratio == 0.0
    sim.drone.x, sim.drone.y = 6.0, 6.0
    power_grid(sim, 6, 6)                      # лампа горит только от сети (M4)
    assert sim.build(LAMP, 6, 6) is not None
    # Генератор зажигает топливо на первом шаге, свет появляется на втором:
    # питание пересчитывается до того, как топка разгорится.
    for _ in range(2):
        sim.update(config.TICK)
    assert sim.tiles_lit_ratio > 0.0


def test_lamp_cannot_be_placed_too_far():
    """Дальность постановки — общий 🔧 `BUILD_RANGE`, отдельного радиуса лампы нет."""
    sim = stock(Simulation(0, world=make_world([[GROUND] * 12 for _ in range(12)])))
    sim.drone.x, sim.drone.y = 1.0, 1.0
    assert sim.drone.distance_to_tile(10, 10) > config.BUILD_RANGE
    assert not sim.can_place(LAMP, 10, 10)
    lamps = sim.cargo.count(LAMP)
    assert sim.build(LAMP, 10, 10) is None
    assert sim.light.lamps == []
    assert sim.cargo.count(LAMP) == lamps, "отказ не тратит предмет"
    near = int(config.BUILD_RANGE) - 1          # центр тайла ближе BUILD_RANGE
    assert sim.drone.distance_to_tile(near, 0) <= config.BUILD_RANGE
    assert sim.can_place(LAMP, near, 0)
    assert sim.build(LAMP, near, 0) is not None


def test_lamp_cannot_be_placed_on_mountain_or_deposit():
    from src.game.sim.world import Deposit

    rows = [[GROUND] * 12 for _ in range(12)]
    rows[6][6] = MOUNTAIN
    deposit = Deposit(0, generate_run(0).items.natural_ids[0], 5, 6, 5)
    sim = stock(Simulation(0, world=make_world(rows, [deposit])))
    sim.drone.x, sim.drone.y = 6.5, 5.5
    lamps = sim.cargo.count(LAMP)
    assert sim.build(LAMP, 6, 6) is None, "гора"
    assert sim.build(LAMP, 5, 6) is None, "залежь"
    assert sim.cargo.count(LAMP) == lamps, "отказ не тратит предмет"
    assert sim.build(LAMP, 7, 6) is not None, "свободная земля рядом"
    assert sim.build(LAMP, 7, 6) is None, "место уже занято лампой"
    assert sim.cargo.count(LAMP) == lamps - 1, "ушла ровно одна лампа"
    assert sim.dismantle_building(7, 6) is True
    assert sim.light.lamp_at(7, 6) is None
    assert sim.dismantle_building(7, 6) is False, "снимать уже нечего"


def test_lamp_placement_changes_state_signature():
    """Лампа входит в слепок состояния, а одинаковые действия дают один слепок."""
    plain = [[GROUND] * 12 for _ in range(12)]
    sim = stock(Simulation(0, world=make_world(plain)))
    assert sim.build(LAMP, 6, 6) is not None
    with_lamp = sim.state_signature()
    assert sim.dismantle_building(6, 6)
    assert sim.state_signature() != with_lamp, "лампа в слепке есть"
    # Тот же забег и те же действия — тот же слепок (тот же запас, та же
    # потраченная лампа).
    other = stock(Simulation(0, world=make_world([[GROUND] * 12 for _ in range(12)])))
    assert other.build(LAMP, 6, 6) is not None
    assert other.state_signature() == with_lamp


def test_default_lamp_radius_is_from_config():
    light = flat()
    lamp = light.add_lamp(5, 5)
    assert lamp.radius == config.LAMP_LIGHT_RADIUS
    assert lamp.on is True
