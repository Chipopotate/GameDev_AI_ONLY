"""Инварианты данных: свойства, роли, рецепты, реликвии, здания, рельеф.

Свойства и роли постоянны во всех забегах, поэтому и проверяются здесь
напрямую. Всё, что рандомизируется забегом (набор ресурсов, их свойства и
значки), проверяется в `test_run_gen.py` — там же и на 1000 сидов.
"""

from __future__ import annotations

import random

from src.game import config
from src.game.data.buildings import BUILDING_TYPES, LAB, building
from src.game.data.recipes import (
    CRAFTED_ITEMS,
    RECIPES,
    RECIPES_BY_ID,
    ROLES,
    ROLES_BY_ID,
    role,
)
from src.game.data.relics import AMPLIFIER, BASE, RELIC_TYPES, relic_def
from src.game.data.resources import (
    NAME_END,
    NAME_MIDDLE,
    NAME_START,
    PROP_ICONS,
    PROP_NAMES,
    RESOURCE_SHAPES,
    ItemDef,
    ItemTable,
    Prop,
    random_color,
    random_name,
)
from src.game.data.terrain import TERRAIN_TYPES, Terrain, terrain_type
from src.game.render.shapes import SHAPES


def test_six_permanent_props():
    assert len(Prop) == 6
    assert {int(p) for p in Prop} == {0, 1, 2, 3, 4, 5}


def test_every_prop_has_name_and_icon():
    for prop in Prop:
        assert prop in PROP_NAMES and PROP_NAMES[prop]
        shape, color = PROP_ICONS[prop]
        assert shape in SHAPES, f"неизвестная форма иконки свойства: {shape}"
        assert len(color) == 3


def test_four_roles_with_prop_and_icon():
    assert len(ROLES) == 4
    for role_def in ROLES:
        assert ROLES_BY_ID[role_def.id] is role_def
        assert role(role_def.id) is role_def
        assert role_def.name and role_def.short
        assert role_def.shape in SHAPES
        assert role_def.prop in Prop


def test_recipe_requires_prop_and_makes_sense():
    """Рецепт — это «свойство × количество» (plan/02), а не список ресурсов."""
    assert len(RECIPES) == len(ROLES) * 2   # базовый и усилитель на каждую роль
    for recipe in RECIPES:
        assert RECIPES_BY_ID[recipe.id] is recipe
        assert recipe.role in ROLES_BY_ID
        assert recipe.requires and recipe.outputs > 0 and recipe.craft_time > 0
        assert recipe.props[0] == ROLES_BY_ID[recipe.role].prop
        assert recipe.id.endswith("+") == recipe.amplified


def test_amplifier_is_not_worse_than_base():
    """Усилитель должен быть выгоднее: меньше единиц на предмет."""
    for role_def in ROLES:
        base, amp = role_def.base, role_def.amplified
        assert amp.requires[0][1] / amp.outputs < base.requires[0][1] / base.outputs


def test_crafted_items_have_no_props():
    """Крафченый предмет — продукт, а не сырьё: в рецепты он не идёт."""
    assert {i.id for i in CRAFTED_ITEMS} == {r.id for r in ROLES}
    for item in CRAFTED_ITEMS:
        assert item.crafted and not item.props and item.shape in SHAPES


def test_relics_open_base_and_amplified_recipes():
    assert BASE != AMPLIFIER
    for kind in (BASE, AMPLIFIER):
        rdef = RELIC_TYPES[kind]
        assert rdef.shape in SHAPES and rdef.mark
    assert relic_def(False).kind == BASE
    assert relic_def(True).kind == AMPLIFIER


def test_laboratory_is_a_known_building():
    assert LAB in BUILDING_TYPES
    lab = building(LAB)
    assert lab.name and lab.shape in SHAPES


# ------------------------------------------------------------------ генераторы
def test_random_color_stays_in_readable_range():
    rng = random.Random(7)
    for _ in range(50):
        color = random_color(rng)
        assert len(color) == 3
        assert all(0 <= c <= 255 for c in color)
        # Насыщенность и яркость заданы коридором: серых и неоновых не бывает.
        assert max(color) >= 180


def test_random_color_uses_given_hue():
    """Оттенок задаётся снаружи: так палитра забега расходится по кругу."""
    red = random_color(random.Random(1), 0.0)
    blue = random_color(random.Random(1), 0.66)
    assert red[0] > red[2] and blue[2] > blue[0]


def test_random_name_avoids_repeats_inside_a_run():
    rng = random.Random(3)
    used: set[str] = set()
    for _ in range(len(NAME_START) * 2):
        name = random_name(rng, used)
        assert name not in used
        assert name[0].isupper()
        used.add(name)
    assert NAME_MIDDLE and NAME_END


def test_item_table_lookups():
    items = (
        ItemDef("a", "А", frozenset({Prop.TOUGH}), (1, 2, 3), "circle", hint=Prop.TOUGH),
        ItemDef("rare", "Р", frozenset({Prop.CUTTING}), (4, 5, 6), "star", rare=True),
        ItemDef("ammo", "Боеприпас", frozenset(), (7, 8, 9), "star", crafted=True),
    )
    table = ItemTable(items)
    assert len(table) == 3 and "a" in table and table["a"] is items[0]
    assert table.get("нет") is None
    assert table.natural_ids == ("a", "rare") and table.common_ids == ("a",)
    assert table.rare_id == "rare"
    assert table.with_prop(Prop.TOUGH) == (items[0],)
    assert table.with_prop(Prop.TOUGH, only=("rare",)) == ()
    assert table.covering(Prop.CUTTING) is items[1]


def test_item_table_rejects_duplicate_ids():
    item = ItemDef("x", "Икс", frozenset({Prop.TOUGH}), (1, 2, 3), "circle")
    try:
        ItemTable((item, item))
    except ValueError:
        return
    raise AssertionError("дубликаты id должны ломать таблицу предметов")


def test_run_uses_eight_common_resources_per_config():
    assert config.RESOURCES_PER_RUN == 8
    assert config.RESOURCE_PROPS_MAX == 2
    assert config.RARE_PROPS == 3
    assert len(RESOURCE_SHAPES) >= config.RESOURCE_PROPS_MAX


def test_terrain_types_complete():
    assert set(TERRAIN_TYPES) == {int(t) for t in Terrain}
    for t in Terrain:
        info = terrain_type(int(t))
        assert info.name and len(info.color) == 3 and info.variants
    assert terrain_type(int(Terrain.GROUND)).walkable
    assert not terrain_type(int(Terrain.MOUNTAIN)).walkable
    assert not terrain_type(int(Terrain.WATER)).walkable
