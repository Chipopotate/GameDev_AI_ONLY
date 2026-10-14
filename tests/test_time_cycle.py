"""Время суток: таблица фаз, события рассвета и заката, плавная тьма."""

from __future__ import annotations

import pytest

from src.game import config
from src.game.sim.time_cycle import DAY, EVENT_DAWN, EVENT_DUSK, NIGHT, TimeCycle


def advance(cycle: TimeCycle, seconds: float, step: float = 1.0) -> list[str]:
    """Продвигает часы мелкими шагами и собирает события."""
    events: list[str] = []
    left = seconds
    while left > 1e-9:
        dt = min(step, left)
        events.extend(cycle.update(dt))
        left -= dt
    return events


# ------------------------------------------------------------------ таблица
@pytest.mark.parametrize("planet", sorted(config.TIME_TABLE))
def test_table_matches_planet_settings(planet):
    first_day, day, night = config.TIME_TABLE[planet]
    cycle = TimeCycle(planet)
    assert cycle.phase == DAY
    assert cycle.day_number == 1
    assert cycle.t_in_phase == 0.0
    assert cycle.phase_length == first_day, "первый день самый длинный"
    assert cycle.night_length == night


def test_every_planet_shrinks_day_and_grows_night():
    """plan/00: каждый следующий забег — ночь длиннее, день короче."""
    planets = sorted(config.TIME_TABLE)
    first_days = [config.TIME_TABLE[p][0] for p in planets]
    days = [config.TIME_TABLE[p][1] for p in planets]
    nights = [config.TIME_TABLE[p][2] for p in planets]
    assert first_days == sorted(first_days, reverse=True)
    assert days == sorted(days, reverse=True)
    assert nights == sorted(nights)


def test_first_day_is_longer_than_usual_day():
    cycle = TimeCycle(1)
    assert cycle.first_day_length > cycle.day_length


# ------------------------------------------------------------------- смена фаз
def test_dusk_comes_after_the_first_day():
    cycle = TimeCycle(1)
    events = advance(cycle, cycle.first_day_length - 0.5)
    assert events == []
    assert cycle.phase == DAY
    events = advance(cycle, 1.0)
    assert events == [EVENT_DUSK]
    assert cycle.phase == NIGHT
    assert cycle.day_number == 1, "ночь принадлежит тому же дню"


def test_dawn_starts_next_day_and_uses_usual_length():
    cycle = TimeCycle(1)
    advance(cycle, cycle.first_day_length + cycle.night_length + 0.5)
    assert cycle.phase == DAY
    assert cycle.day_number == 2
    assert cycle.phase_length == cycle.day_length, "второй день уже обычной длины"


def test_full_cycle_lengths():
    """День 1 → ночь → день 2 → ночь: длины строго по таблице."""
    cycle = TimeCycle(1)
    first, day, night = config.TIME_TABLE[1]
    assert advance(cycle, first - 1.0) == []
    assert advance(cycle, 1.0) == [EVENT_DUSK]          # конец первого дня
    assert advance(cycle, night - 0.5) == []
    assert advance(cycle, 0.5) == [EVENT_DAWN]          # конец ночи
    assert advance(cycle, day - 0.5) == []
    assert advance(cycle, 0.5) == [EVENT_DUSK]          # конец второго дня
    assert cycle.day_number == 2, "ночь второго дня ещё день 2"
    assert advance(cycle, night - 0.5) == []
    assert advance(cycle, 0.5) == [EVENT_DAWN]
    assert cycle.day_number == 3


def test_big_step_matches_many_small_steps():
    """Событие не теряется, даже если шаг перескочил сразу несколько фаз."""
    fine, coarse = TimeCycle(3), TimeCycle(3)
    assert advance(fine, 900.0, step=0.25) == list(coarse.update(900.0))
    assert fine.state() == coarse.state()


def test_phase_events_alternate():
    cycle = TimeCycle(1)
    events = advance(cycle, 4000.0)
    assert events[0] == EVENT_DUSK
    for i, event in enumerate(events):
        assert event == (EVENT_DUSK if i % 2 == 0 else EVENT_DAWN)


def test_non_positive_dt_is_ignored():
    cycle = TimeCycle(1)
    before = cycle.state()
    assert cycle.update(0.0) == ()
    assert cycle.update(-5.0) == ()
    assert cycle.state() == before


# --------------------------------------------------------------------- тьма
def test_darkness_is_zero_during_the_day():
    cycle = TimeCycle(1)
    assert cycle.darkness == 0.0
    advance(cycle, cycle.first_day_length * 0.5)
    assert cycle.phase == DAY
    assert cycle.darkness == 0.0


def test_darkness_ramps_in_and_out_of_the_night():
    cycle = TimeCycle(1)
    advance(cycle, cycle.first_day_length + 0.1)
    assert cycle.phase == NIGHT
    assert cycle.darkness < 0.2, "ночь входит плавно, а не щелчком"
    advance(cycle, config.DARKNESS_FADE)
    assert cycle.darkness == pytest.approx(1.0)
    advance(cycle, cycle.night_length * 0.5)
    assert cycle.darkness == pytest.approx(1.0), "середина ночи — полная тьма"
    # ...и так же плавно выходит перед рассветом
    advance(cycle, cycle.night_length * 0.5 - config.DARKNESS_FADE - 0.2)
    assert cycle.phase == NIGHT
    assert cycle.darkness < 0.2


def test_darkness_never_exceeds_one():
    cycle = TimeCycle(2)
    for _ in range(2000):
        cycle.update(1.0)
        assert 0.0 <= cycle.darkness <= 1.0


def test_t_left_counts_down_to_the_phase_change():
    cycle = TimeCycle(1)
    assert cycle.t_left == pytest.approx(cycle.first_day_length)
    advance(cycle, 10.0)
    assert cycle.t_left == pytest.approx(cycle.first_day_length - 10.0, abs=1e-6)
