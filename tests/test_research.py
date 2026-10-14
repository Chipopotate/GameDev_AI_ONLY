"""Знание игрока и крафт по свойствам (plan/02)."""

from __future__ import annotations

import random

from src.game.data.recipes import ROLES, Recipe, role
from src.game.data.resources import PROP_NAMES, ItemDef, ItemTable, Prop
from src.game.sim.research import Knowledge, can_craft, plan_craft
from src.game.sim.resources import Inventory


def make_table() -> ItemTable:
    """Маленькая таблица: ресурс на каждое свойство, «двойной» и крафченый."""
    items = [
        ItemDef(f"p{int(prop)}", PROP_NAMES[prop], frozenset({prop}),
                (40 * int(prop), 120, 200), "circle", hint=prop)
        for prop in Prop
    ]
    items.append(ItemDef("both", "Оба", frozenset({Prop.FLAMMABLE, Prop.TOUGH}),
                         (220, 90, 60), "star"))
    items.append(ItemDef("ammo", "Боеприпас", frozenset(), (255, 255, 0), "star",
                         crafted=True))
    return ItemTable(tuple(items))


FIRE = f"p{int(Prop.FLAMMABLE)}"


# ------------------------------------------------------------------ знание
def test_first_pickup_reveals_one_prop_and_only_once():
    knowledge = Knowledge(make_table())
    assert knowledge.visible(FIRE) == frozenset()
    assert knowledge.unknown(FIRE) == (Prop.FLAMMABLE,)
    assert knowledge.note_pickup(FIRE) == (Prop.FLAMMABLE,)
    assert knowledge.note_pickup(FIRE) == ()            # второй раз молчит
    assert knowledge.visible(FIRE) == frozenset({Prop.FLAMMABLE})


def test_unknown_props_stay_hidden_until_revealed():
    """Приёмка M3: неизвестное свойство показывается как `?`."""
    knowledge = Knowledge(make_table())
    assert knowledge.unknown("both") == (Prop.FLAMMABLE, Prop.TOUGH)
    knowledge.reveal("both", Prop.TOUGH)
    assert knowledge.visible("both") == frozenset({Prop.TOUGH})
    assert knowledge.unknown("both") == (Prop.FLAMMABLE,)
    assert not knowledge.reveal("both", Prop.TOUGH), "повторно открывать нечего"
    assert not knowledge.reveal("both", Prop.CUTTING), "чужого свойства у него нет"


def test_scan_reveals_one_random_unknown_prop():
    knowledge = Knowledge(make_table())
    rng = random.Random(1)
    prop = knowledge.reveal_one("both", rng)
    assert prop in (Prop.FLAMMABLE, Prop.TOUGH)
    assert len(knowledge.visible("both")) == 1
    knowledge.reveal_one("both", rng)
    assert knowledge.unknown("both") == ()
    assert knowledge.reveal_one("both", rng) is None, "открывать больше нечего"


def test_laboratory_reveals_all_props_at_once():
    knowledge = Knowledge(make_table())
    assert knowledge.reveal_all("both") == (Prop.FLAMMABLE, Prop.TOUGH)
    assert knowledge.unknown("both") == ()
    assert knowledge.reveal_all("both") == (), "повторный анализ ничего не даёт"


def test_recipes_are_learned_from_relics():
    knowledge = Knowledge(make_table())
    assert knowledge.known_count == 0
    assert knowledge.learn("ammo")
    assert not knowledge.learn("ammo")
    assert knowledge.is_known("ammo") and not knowledge.is_known("ammo+")


def test_state_signature_changes_with_knowledge():
    knowledge = Knowledge(make_table())
    before = knowledge.state()
    knowledge.note_pickup(FIRE)
    knowledge.learn("ammo")
    assert knowledge.state() != before


# ------------------------------------------------------------------- крафт
def test_craft_needs_exact_amount_of_prop():
    items = make_table()
    recipe = role("energy").base      # 🔥×3
    inventory = Inventory()
    inventory.add(FIRE, 2)
    assert plan_craft(recipe, inventory, items) is None
    assert not can_craft(recipe, inventory, items)
    inventory.add(FIRE, 1)
    assert plan_craft(recipe, inventory, items) == {FIRE: 3}


def test_one_resource_can_cover_two_slots():
    """Ресурс с двумя нужными свойствами тратится один раз, а не два (plan/02).

    В текущих данных у рецептов по одному свойству, поэтому рецепт здесь
    синтетический: проверяем сам подбор, а не конкретную роль.
    """
    items = make_table()
    recipe = Recipe(id="test", role="energy",
                    requires=((Prop.FLAMMABLE, 2), (Prop.TOUGH, 2)),
                    outputs=1, craft_time=1.0)
    inventory = Inventory()
    inventory.add("both", 2)
    assert plan_craft(recipe, inventory, items) == {"both": 2}


def test_greedy_prefers_narrow_resource_when_covering_the_same():
    """Узкий ресурс не жалко: широкий пригодится в другом рецепте."""
    items = make_table()
    recipe = role("energy").base
    inventory = Inventory()
    inventory.add(FIRE, 5)
    inventory.add("both", 5)
    assert plan_craft(recipe, inventory, items) == {FIRE: 3}


def test_crafted_items_are_not_materials():
    items = make_table()
    inventory = Inventory()
    inventory.add("ammo", 10)         # у крафченого свойств нет
    assert plan_craft(role("energy").base, inventory, items) is None


def test_spending_more_than_stock_is_not_allowed():
    items = make_table()
    inventory = Inventory()
    inventory.add(FIRE, 3)
    assert plan_craft(role("energy").base, inventory, items) == {FIRE: 3}
    # Крафт тратит ресурс: второй раз того же не хватит.
    inventory.remove(FIRE, 3)
    assert plan_craft(role("energy").base, inventory, items) is None


def test_amplified_recipe_gives_more_output_for_more_input():
    """Усилитель выгоднее на единицу — иначе он не был бы усилителем."""
    base, amp = role("energy").base, role("energy").amplified
    assert base.requires[0][1] == 3 and base.outputs == 1
    assert amp.requires[0][1] == 4 and amp.outputs == 2
    assert amp.outputs / amp.requires[0][1] > base.outputs / base.requires[0][1]


def test_every_role_recipe_can_be_planned_from_a_matching_resource():
    items = make_table()
    for role_def in ROLES:
        sources = [i.id for i in items.natural if i.has(role_def.prop)]
        assert sources, f"нет ресурса со свойством {role_def.prop}"
        inventory = Inventory()
        inventory.add(sources[0], 10)
        assert plan_craft(role_def.base, inventory, items) is not None
        assert plan_craft(role_def.amplified, inventory, items) is not None
