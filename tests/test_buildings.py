"""Лаборатория: слоты образцов и анализ в каждом слоте (plan/02, M3 → M3.5)."""

from __future__ import annotations

import pytest

from src.game import config
from src.game.data.buildings import LAB, LAB_SAMPLES
from src.game.sim.buildings import (SLOT_ANALYZING, SLOT_EMPTY, SLOT_POWER,
                                    SLOT_STUDIED, BuildingSystem, slot_status)
from src.game.sim.research import Knowledge
from src.game.sim.resources import Stack, give
from src.game.sim.run_gen import generate_run
from tests.conftest import SEED, power_grid, stock

#: Точное число тиков на один образец: ещё один сверху — на накопление float
#: (600 тиков дают 0.9999…, и образец закрылся бы только на 601-м).
SAMPLE_TICKS = int(config.LAB_ANALYZE_TIME / config.TICK) + 1


@pytest.fixture
def lab_system():
    """Система зданий, знание и ресурс, у которого есть скрытое свойство."""
    run = generate_run(SEED)
    knowledge = Knowledge(run.items)
    system = BuildingSystem()
    item = next(i for i in run.items.natural if len(i.props) > 1)
    return system, knowledge, item, run


def samples(lab):
    """Контейнер образцов лаборатории — он объявлен в данных здания."""
    return lab.container(LAB_SAMPLES)


def tick(system: BuildingSystem, knowledge: Knowledge, count: int) -> list:
    """Прогоняет анализ заданным числом фиксированных шагов, как симуляция."""
    results = []
    for _ in range(count):
        results.extend(system.update(config.TICK, knowledge))
    return results


def test_building_lives_on_a_tile(lab_system):
    system, _knowledge, _item, _run = lab_system
    lab = system.add(LAB, 3, 4)
    assert system.at(3, 4) is lab
    assert system.at(4, 3) is None
    assert system.of_kind(LAB) == (lab,)
    assert system.remove(lab) and system.at(3, 4) is None
    assert not system.remove(lab), "повторно сносить нечего"


def test_nearest_respects_kind_and_range(lab_system):
    system, _knowledge, _item, _run = lab_system
    system.add(LAB, 10, 10)
    near = system.add(LAB, 2, 2)
    assert system.nearest(2.5, 2.5, LAB, max_dist=1.0) is near
    assert system.nearest(50.5, 50.5, LAB, max_dist=1.0) is None
    assert system.nearest(10.5, 10.5, "нет такого") is None


def test_containers_come_from_the_data(lab_system):
    """Контейнеры здания — из данных: M4 добавит склад строкой в таблицу."""
    system, _knowledge, _item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    assert samples(lab).size == config.LAB_SLOTS
    assert lab.analyzed is samples(lab), "лаборатория обрабатывает свои образцы"
    assert lab.container("нет такого") is None


def test_sample_is_analyzed_and_reveals_all_props(lab_system):
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    assert set(knowledge.unknown(item.id)) == set(item.props)
    samples(lab).add(item.id, config.LAB_SAMPLE_COST)
    results = tick(system, knowledge, SAMPLE_TICKS)
    assert len(results) == 1
    assert results[0].building is lab and results[0].item_id == item.id
    assert knowledge.unknown(item.id) == ()
    assert lab.processed == 1 and not lab.busy and lab.current is None
    assert samples(lab).total_count() == 0, "образец потрачен"


def test_analysis_takes_the_configured_time(lab_system):
    """Анализ заканчивается ровно через 🔧 LAB_ANALYZE_TIME, не раньше."""
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    samples(lab).add(item.id, 1)
    assert tick(system, knowledge, SAMPLE_TICKS - 2) == [], "рано"
    assert lab.current == item.id and 0.0 < lab.progress < 1.0
    assert set(knowledge.unknown(item.id)) == set(item.props), "анализ ещё не закончен"
    assert tick(system, knowledge, 2) != [], "ровно к LAB_ANALYZE_TIME"


def test_every_slot_has_its_own_progress(lab_system):
    """Слоты — самостоятельные анализаторы, а не очередь (M3.5).

    Два образца в двух слотах доходят до конца одновременно: игрок платит
    временем, а не ждёт вторую линию после первой.
    """
    system, knowledge, item, run = lab_system
    lab = system.add(LAB, 0, 0)
    other = next(i for i in run.items.natural if i is not item)
    samples(lab).add(item.id, 1)
    samples(lab).add(other.id, 1)
    results = tick(system, knowledge, SAMPLE_TICKS)
    assert sorted(r.item_id for r in results) == sorted([item.id, other.id])
    assert lab.processed == 2 and not lab.busy
    assert knowledge.unknown(item.id) == () and knowledge.unknown(other.id) == ()


def test_slots_progress_independently(lab_system):
    """Образец, положенный позже, отстаёт ровно на столько, на сколько позже."""
    system, knowledge, item, run = lab_system
    lab = system.add(LAB, 0, 0)
    other = next(i for i in run.items.natural if i is not item)
    samples(lab).add(item.id, 1)
    tick(system, knowledge, SAMPLE_TICKS // 2)
    samples(lab).add(other.id, 1)
    assert lab.progress_of(0) > lab.progress_of(1)
    assert lab.progress_of(0) > 0.4 and lab.progress_of(1) < 0.1


def test_swapping_the_sample_restarts_the_progress(lab_system):
    """Иначе новая руда дособрала бы чужой прогресс."""
    system, knowledge, item, run = lab_system
    lab = system.add(LAB, 0, 0)
    other = next(i for i in run.items.natural if i is not item)
    container = samples(lab)
    container.add(item.id, 1)
    tick(system, knowledge, SAMPLE_TICKS // 2)
    assert lab.progress_of(0) > 0.4
    container.slots[0] = Stack(other.id, 1)      # игрок подменил образец
    tick(system, knowledge, 1)
    assert lab.progress_of(0) < 0.1, "прогресс начался заново"


def test_lab_analyzes_one_unit_and_keeps_the_rest(lab_system):
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    samples(lab).add(item.id, 5)
    tick(system, knowledge, SAMPLE_TICKS)
    assert samples(lab).count(item.id) == 5 - config.LAB_SAMPLE_COST
    assert knowledge.unknown(item.id) == ()


def test_lab_slots_have_a_limit(lab_system):
    """Больше 🔧 LAB_SLOTS образцов не влезает: это буфер, а не склад."""
    system, _knowledge, item, run = lab_system
    lab = system.add(LAB, 0, 0)
    other = next(i for i in run.items.natural if i is not item)
    samples(lab).add(item.id, 1)
    samples(lab).add(other.id, 1)
    assert lab.full and samples(lab).size == config.LAB_SLOTS


def test_lab_does_not_touch_known_resources(lab_system):
    """Открывать нечего — образец не тратится, игрок заберёт его сам."""
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    knowledge.reveal_all(item.id)
    samples(lab).add(item.id, 2)
    assert tick(system, knowledge, SAMPLE_TICKS * 2) == []
    assert lab.processed == 0 and samples(lab).count(item.id) == 2


def test_studied_slot_says_so(lab_system):
    """Слот с изученным ресурсом показывает «уже изучено» (plan/02)."""
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    assert slot_status(lab, 0, knowledge) == SLOT_EMPTY
    samples(lab).add(item.id, 1)
    assert slot_status(lab, 0, knowledge) == SLOT_ANALYZING
    knowledge.reveal_all(item.id)
    assert slot_status(lab, 0, knowledge) == SLOT_STUDIED


def test_statuses_describe_every_slot(lab_system):
    """Состояние слотов для окна: у каждого слота своя строка и свой прогресс."""
    system, knowledge, item, run = lab_system
    lab = system.add(LAB, 0, 0)
    other = next(i for i in run.items.natural if i is not item)
    samples(lab).add(item.id, 1)
    samples(lab).add(other.id, 1)
    tick(system, knowledge, SAMPLE_TICKS // 4)
    statuses = system.statuses(lab, knowledge)
    assert len(statuses) == config.LAB_SLOTS
    assert all(status == SLOT_ANALYZING for status, _p in statuses)
    assert all(0.0 < progress < 1.0 for _s, progress in statuses)


def test_lab_without_power_stops(lab_system):
    """С M4 лаборатория работает от сети: без энергии образец ждёт в слоте."""
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    lab.powered = False                   # так его выключает `sim/power.py`
    samples(lab).add(item.id, 1)
    assert slot_status(lab, 0, knowledge) == SLOT_POWER
    assert tick(system, knowledge, SAMPLE_TICKS * 2) == []
    assert samples(lab).count(item.id) == 1, "образец ждёт энергию"


def test_removing_the_sample_stops_the_analysis(lab_system):
    system, knowledge, item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    samples(lab).add(item.id, 1)
    tick(system, knowledge, SAMPLE_TICKS // 2)
    assert lab.busy
    samples(lab).take_slot(0)
    tick(system, knowledge, 1)
    assert not lab.busy and lab.progress == 0.0 and lab.processed == 0


def test_empty_lab_does_nothing(lab_system):
    system, knowledge, _item, _run = lab_system
    lab = system.add(LAB, 0, 0)
    assert not lab.busy
    assert tick(system, knowledge, SAMPLE_TICKS * 2) == []
    assert lab.processed == 0


def test_state_signature_covers_buildings(lab_system):
    system, _knowledge, item, _run = lab_system
    before = system.state()
    lab = system.add(LAB, 1, 2)
    assert system.state() != before
    samples(lab).add(item.id, 1)
    system.update(config.TICK, Knowledge(_run.items))
    assert system.state() != before


def test_lab_placed_in_simulation_works_end_to_end():
    """Полный путь: поставить лабораторию, положить образец, дождаться анализа."""
    from src.game.sim.simulation import Simulation

    sim = stock(Simulation(SEED))
    drone = sim.drone
    x, y = int(drone.x) + 1, int(drone.y)
    assert sim.select_build(LAB)
    assert sim.click_at(x, y) == LAB
    power_grid(sim, x, y)                 # лаборатория работает только от сети
    lab = sim.lab_in_range()
    assert lab is not None and sim.building_reachable(lab)
    item = next(i for i in sim.run.items.natural if len(i.props) > 1)
    assert give(Stack(item.id, 1), samples(lab)) is None, "образец в лаборатории"
    for _ in range(SAMPLE_TICKS):
        sim.update(config.TICK)
    assert sim.knowledge.unknown(item.id) == (), "лаборатория не открыла свойства"
    assert not sim.inventory.has(item.id, 1), "образец должен быть потрачен"
