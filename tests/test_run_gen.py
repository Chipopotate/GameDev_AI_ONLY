"""Генератор забега (M3): рандомизация и гарантия проходимости.

Главный тест здесь — приёмочный: на 1000 сидов генератор обязан вернуть
проходимый забег (plan/02, «Тест (обязателен)»). Он гоняется на дешёвом
`generate_run`, а не на полной генерации карты: карта для гарантии не нужна,
а 1000 полных миров стоили бы десятки секунд.
"""

from __future__ import annotations

import pytest

from src.game import config
from src.game.data.recipes import ROLES
from src.game.data.resources import RESOURCE_SHAPES, ItemTable, Prop
from src.game.sim.run_gen import UNPLACED, generate_run

SEEDS_1000 = range(1000)


def test_generates_eight_common_and_one_rare():
    run = generate_run(1)
    items = run.items
    assert len(items.natural) == config.RESOURCES_PER_RUN + 1
    assert len(items.common_ids) == config.RESOURCES_PER_RUN
    assert items.rare_id is not None
    rare = items[items.rare_id]
    assert rare.rare and len(rare.props) == config.RARE_PROPS
    assert rare.id not in items.common_ids


def test_every_resource_has_one_or_two_props():
    for seed in range(50):
        for item in generate_run(seed).items.natural:
            if item.rare:
                continue
            assert 1 <= len(item.props) <= config.RESOURCE_PROPS_MAX, \
                f"seed {seed}, {item.id}: свойств {len(item.props)}"


def test_names_icons_and_props_differ_between_seeds():
    """Смысл всей системы: забеги должны быть непохожи."""
    first, second = generate_run(1).items, generate_run(2).items
    assert [i.name for i in first.natural] != [i.name for i in second.natural]
    assert [i.color for i in first.natural] != [i.color for i in second.natural]
    assert [i.props for i in first.natural] != [i.props for i in second.natural]


def test_names_and_ids_are_unique_inside_a_run():
    for seed in range(50):
        items = generate_run(seed).items
        names = [i.name for i in items.natural]
        assert len(set(names)) == len(names), f"seed {seed}: имена повторяются"
        assert len(set(items.natural_ids)) == len(items.natural_ids)


def test_resource_shapes_are_distinct_where_possible():
    items = generate_run(3).items
    shapes = [i.shape for i in items.natural]
    for shape in shapes:
        assert shape in RESOURCE_SHAPES
    # Значки не должны сливаться: различных форм не меньше, чем есть в наборе.
    assert len(set(shapes)) == min(len(shapes), len(RESOURCE_SHAPES))


def test_same_seed_gives_same_run():
    """Детерминизм обязателен: карта и залежи ссылаются на ресурсы забега."""
    a, b = generate_run(42), generate_run(42)
    assert a.items.items == b.items.items
    assert [(r.id, r.role, r.amplified) for r in a.relics] == \
           [(r.id, r.role, r.amplified) for r in b.relics]
    assert generate_run(42).items.items != generate_run(43).items.items


def test_every_prop_has_a_source_in_every_run():
    for seed in range(200):
        run = generate_run(seed)
        for prop in Prop:
            assert run.items.with_prop(prop), f"seed {seed}: нет источника свойства"


def test_base_relic_for_each_role_and_some_amplifiers():
    for seed in range(50):
        relics = generate_run(seed).relics
        base = [r for r in relics if not r.amplified]
        amp = [r for r in relics if r.amplified]
        assert {r.role for r in base} == {role.id for role in ROLES}
        assert len(amp) == min(config.AMP_RELICS, len(ROLES))
        assert len({r.id for r in relics}) == len(relics)
        for relic in relics:
            assert relic.recipe_id == (f"{relic.role}+" if relic.amplified else relic.role)


def test_nothing_is_placed_before_place_relics():
    run = generate_run(5)
    assert all(r.x == UNPLACED and r.y == UNPLACED for r in run.relics)
    assert run.relics and run.relic_at(0, 0) is None


@pytest.mark.slow
def test_passability_guarantee_on_1000_seeds():
    """Приёмка M3: 1000 сидов — каждый забег проходим (plan/02)."""
    for seed in SEEDS_1000:
        run = generate_run(seed)
        assert run.is_passable(), f"seed {seed}: забег непроходим"
        assert run.missing_props() == (), f"seed {seed}: нет источников свойств"
        assert isinstance(run.items, ItemTable)
