"""Постройки-предметы, хотбар, постановка и демонтаж (plan/03, plan/10).

Проверяется уровень симуляции — то, что UI только вызывает:

- крафт постройки по обычному рецепту «свойство × N» и правило изученности
  (лампа и лаборатория — с начала, остальное — реликвия роли);
- правило `Cargo`: постройка — в хотбар, ресурс — в инвентарь и в уже
  существующие стаки хотбара;
- `PlaceBuilding` тратит ровно один предмет, а отказ ничего не меняет;
- призрак и постановка — одна проверка (`Simulation.can_place`);
- демонтаж удержанием ЛКМ роняет здание предметом на его тайл;
- `drone.action` — единственное, что читает лазер;
- поворот R, выбор и прокрутка слотов;
- лампа и лаборатория крафтятся из стартовых ресурсов на 1000 сидов.

Точные сцены — плоский мир 16×16 (`make_world`): на настоящей карте нужную
гору, воду и залежь рядом с дроном пришлось бы искать, а тест должен падать от
поломки правила, а не от неудачного сида.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace

import pytest

from src.game import config
from src.game.data.buildings import (BUILD_ORDER, BUILDING_START, BUILDING_TYPES,
                                     CONVEYOR, DRILL, LAB, LAMP, ROLE_AUTOMATION,
                                     STORAGE, WIRE, building as building_def,
                                     unlocked_by)
from src.game.data.resources import Prop
from src.game.data.terrain import Terrain
from src.game.sim.commands import DismantleBuilding, PlaceBuilding, SelectHotbar
from src.game.sim.research import plan_craft
from src.game.sim.resources import Container, Stack
from src.game.sim.run_gen import generate_run
from src.game.sim.simulation import ENERGY_ITEM, Simulation
from src.game.sim.units import (ACTION_DISMANTLE, ACTION_MINE, ACTION_SCAN,
                                ACTION_STUDY)
from src.game.sim.world import Deposit, World
from tests.conftest import SEED, give_building, make_world, place, slot_of

# ------------------------------------------------------------------ сцена
SIZE = 16
#: Дрон стоит в центре тайла (8, 8): так расстояния до тайлов — целые числа.
DRONE_TILE = (8, 8)
GROUND = (9, 8)        # свободная земля вплотную к дрону (1 тайл)
EAST = (10, 8)         # 2 тайла на восток: «от дрона» конвейер смотрит на восток
NEAR = (11, 8)         # 3 тайла: в BUILD_RANGE, но дальше PICKUP_RADIUS
MOUNTAIN = (8, 6)
WATER = (6, 8)
DEPOSIT = (9, 9)       # 1.41 тайла: достаёт и добыча, и скан
DEPLETED = (7, 9)      # выработанная залежь
# Все тайлы выше лежат ближе 🔧 `RELIC_SPAWN_CLEAR` к точке старта (8, 8):
# реликвий на них нет, и отказ в постановке объясняется ровно одной причиной.

#: Забег плоской сцены: `make_world` даёт миру сид 0, симуляция собирает забег
#: по нему же, поэтому ресурсы известны заранее.
RUN0 = generate_run(0)
#: Ресурс залежи — с двумя свойствами: до подбора оба скрыты, скану есть что открыть.
ORE = next(i.id for i in RUN0.items.natural if not i.rare and len(i.props) == 2)
#: Другой обычный ресурс — чтобы забить им инвентарь.
FILLER = next(i.id for i in RUN0.items.natural if not i.rare and i.id != ORE)

E, S, W, N = (1, 0), (0, 1), (-1, 0), (0, -1)


def scene() -> Simulation:
    """Плоский мир с горой, водой, живой и выработанной залежью рядом с дроном."""
    rows = [[int(Terrain.GROUND)] * SIZE for _ in range(SIZE)]
    rows[MOUNTAIN[1]][MOUNTAIN[0]] = int(Terrain.MOUNTAIN)
    rows[WATER[1]][WATER[0]] = int(Terrain.WATER)
    deposits = [Deposit(0, ORE, *DEPOSIT, 20), Deposit(1, ORE, *DEPLETED, 0)]
    sim = Simulation(0, make_world(rows, deposits))
    sim.drone.x, sim.drone.y = DRONE_TILE[0] + 0.5, DRONE_TILE[1] + 0.5
    return sim


def run(sim: Simulation, seconds: float) -> None:
    for _ in range(int(round(seconds / config.TICK))):
        sim.update(config.TICK)


#: Удержание с запасом в пару шагов: прогресс копится float-ами.
DISMANTLE_HOLD = config.DISMANTLE_TIME + 3 * config.TICK


def hold_lmb(sim: Simulation, tile: tuple[int, int], seconds: float) -> None:
    """Игрок навёл курсор на тайл и держит ЛКМ."""
    sim.set_cursor_tile(*tile)
    sim.set_mining(True)
    run(sim, seconds)


def narrow(sim: Simulation, prop: Prop) -> str:
    """Самый «узкий» ресурс со свойством: он не закроет лишнего слота рецепта."""
    return min(sim.run.items.with_prop(prop), key=lambda i: (len(i.props), i.id)).id


def give_materials(sim: Simulation, recipe, times: int = 1) -> None:
    """Сырьё под рецепт в инвентарь: на каждое свойство — узкий ресурс."""
    for prop, count in recipe.requires:
        sim.inventory.add(narrow(sim, prop), count * times)


def everything(sim: Simulation) -> Counter:
    """Все предметы забега: здания (как предметы), их содержимое, земля и дрон.

    Закон сохранения plan/03: демонтаж ничего не создаёт и не теряет —
    здание становится своим предметом, содержимое просто меняет место.
    """
    total: Counter = Counter()
    for building in sim.buildings.buildings:
        total[building.kind] += 1
        for container in building.containers.values():
            total.update(dict(container.items()))
    total[LAMP] += len(sim.light.lamps)
    total.update(sim.drops.total())
    total.update(dict(sim.cargo.items()))
    return +total


def at(sim: Simulation, tile: tuple[int, int]):
    """Что стоит на тайле: здание или лампа (лампа живёт в `sim/light.py`)."""
    return sim.buildings.at(*tile) or sim.light.lamp_at(*tile)


def nearest_relic(sim: Simulation):
    relics = sim.relics
    assert relics, "в сцене должна быть хоть одна реликвия"
    return min(relics, key=lambda r: sim.drone.distance_to_tile(r.x, r.y))


def select(sim: Simulation, kind: str) -> int:
    """Выбирает слот хотбара с этой постройкой — как клавиша 1…9."""
    slot = slot_of(sim.hotbar, kind)
    assert sim.apply(SelectHotbar(slot))
    return slot


# --------------------------------------------------------- крафт постройки
def assert_spent_by_rules(spent: dict[str, int], recipe, sim: Simulation) -> None:
    """Потрачено по правилу «свойство × N»: каждое свойство закрыто, лишнего нет."""
    for item_id in spent:
        item = sim.run.items[item_id]
        assert not item.crafted and item.props, f"{item_id}: потрачено не сырьё"
    for prop, need in recipe.requires:
        covered = sum(count for item_id, count in spent.items()
                      if prop in sim.run.items[item_id].props)
        assert covered >= need, f"{prop}: закрыто {covered} из {need}"


@pytest.mark.parametrize("kind", BUILDING_START)
def test_start_building_is_crafted_into_the_hotbar(kind):
    """Лампа и лаборатория известны с начала: крафт кладёт предмет в хотбар.

    Тратится ровно то, что выбрал `plan_craft` (логика подбора не менялась), и
    этого хватает на все свойства рецепта.
    """
    sim = scene()
    recipe = building_def(kind).recipe
    assert sim.knowledge.is_known(recipe.id), f"{kind} известна с начала забега"
    give_materials(sim, recipe, times=2)
    plan = plan_craft(recipe, sim.cargo, sim.run.items)
    assert plan is not None
    before = Counter(dict(sim.cargo.items()))

    assert sim.craft(recipe.id)
    after = Counter(dict(sim.cargo.items()))
    spent = dict(before - after)
    assert spent == plan, "потрачено ровно то, что выбрал подбор"
    assert_spent_by_rules(spent, recipe, sim)
    assert sim.hotbar.count(kind) == recipe.outputs, "постройка — в хотбар"
    assert sim.inventory.count(kind) == 0
    assert sim.crafted_total == recipe.outputs


@pytest.mark.parametrize("kind", BUILDING_START)
def test_start_building_costs_exactly_its_props(kind):
    """«Свойство × N» буквально: из узкого ресурса на каждое свойство уходит N."""
    sim = scene()
    recipe = building_def(kind).recipe
    sources = {prop: narrow(sim, prop) for prop, _need in recipe.requires}
    assert all(len(sim.run.items[s].props) == 1 for s in sources.values()), \
        "сцене нужны ресурсы ровно с одним свойством"
    give_materials(sim, recipe)
    for source in sources.values():
        sim.inventory.add(source, 3)
    assert sim.craft(recipe.id)
    for prop, _need in recipe.requires:
        assert sim.cargo.count(sources[prop]) == 3, f"{prop}: потрачено не N"


def test_unknown_building_is_not_crafted_until_its_relic_is_studied():
    """Бур открывает только реликвия автоматики: до неё крафт ничего не меняет.

    После изучения (удержание ЛКМ на реликвии, как у игрока) бур крафтится и
    ложится в хотбар, а постройки чужих ролей по-прежнему закрыты.
    """
    sim = scene()
    recipe = building_def(DRILL).recipe
    give_materials(sim, recipe)
    assert not sim.knowledge.is_known(recipe.id)
    before = sim.state_signature()
    assert not sim.craft(recipe.id), "рецепт бура ещё не открыт"
    assert not sim.craft(recipe.id, None), "и «максимум» тоже"
    assert sim.state_signature() == before, "отказ ничего не тратит"
    assert recipe.id not in {o.recipe_id for o in sim.craft_options()}

    relic = sim.run.relic_of(ROLE_AUTOMATION)
    assert relic is not None and relic.placed, "реликвия автоматики в сцене"
    sim.drone.x, sim.drone.y = relic.x + 0.5, relic.y + 0.5
    hold_lmb(sim, (relic.x, relic.y), config.RELIC_STUDY_TIME * 1.1)
    assert relic.studied
    for kind in BUILD_ORDER:
        known = sim.knowledge.is_known(building_def(kind).recipe.id)
        assert known == (kind in BUILDING_START or kind in unlocked_by(ROLE_AUTOMATION)), kind

    assert sim.craft(recipe.id), "после реликвии бур крафтится"
    assert sim.hotbar.count(DRILL) == 1


# ---------------------------------------------------------- правило Cargo
def test_building_items_fill_the_hotbar_first_then_the_inventory():
    sim = scene()
    room = config.HOTBAR_SLOTS * config.BUILDING_STACK
    assert sim.cargo.add(LAMP, room + 5) == 0
    assert sim.hotbar.count(LAMP) == room, "весь хотбар — лампы по 🔧 BUILDING_STACK"
    assert all(s.count == config.BUILDING_STACK for s in sim.hotbar.slots)
    assert sim.inventory.count(LAMP) == 5, "что не влезло — в инвентарь"


def test_building_item_tops_up_its_hotbar_stack_before_an_empty_slot():
    sim = scene()
    sim.hotbar.put_slot(4, Stack(LAB, 10))
    assert sim.cargo.add(LAB, 4) == 0
    assert sim.hotbar.slot(4).count == 14 and sim.hotbar.used_slots == 1
    assert sim.cargo.add(LAB, 5) == 0
    assert sim.hotbar.slot(4).count == config.BUILDING_STACK
    assert sim.hotbar.slot(0) == Stack(LAB, 5 - (config.BUILDING_STACK - 14)), \
        "остаток — в первый пустой слот хотбара"
    assert sim.inventory.is_empty


def test_building_item_goes_to_the_inventory_when_the_hotbar_is_taken():
    sim = scene()
    sim.hotbar.add(FILLER, config.HOTBAR_SLOTS * config.SLOT_STACK_MAX)
    assert sim.hotbar.is_full
    assert sim.cargo.add(LAB, 2) == 0
    assert sim.inventory.count(LAB) == 2 and sim.hotbar.count(LAB) == 0


def test_resource_never_takes_an_empty_hotbar_slot():
    """Ресурс идёт в инвентарь; пустой хотбар его не берёт даже при полном рюкзаке."""
    sim = scene()
    for item_id in (ORE, ENERGY_ITEM):        # сырьё и предмет роли — не постройки
        assert sim.cargo.add(item_id, 5) == 0
        assert sim.inventory.count(item_id) == 5
    assert sim.hotbar.is_empty

    full = scene()
    full.inventory.add(FILLER, config.DRONE_SLOTS * config.SLOT_STACK_MAX)
    assert full.inventory.is_full
    assert not full.cargo.can_accept(ORE)
    assert full.cargo.add(ORE, 5) == 5, "некуда — остаток возвращается"
    assert full.hotbar.is_empty


def test_resource_tops_up_an_existing_hotbar_stack():
    """Стак ресурса, который игрок сам положил в хотбар, досыпается."""
    sim = scene()
    sim.inventory.add(FILLER, config.DRONE_SLOTS * config.SLOT_STACK_MAX)
    sim.hotbar.put_slot(2, Stack(ORE, 10))
    assert sim.cargo.can_accept(ORE, config.SLOT_STACK_MAX - 10)
    assert sim.cargo.add(ORE, 25) == 25 - (config.SLOT_STACK_MAX - 10)
    assert sim.hotbar.slot(2) == Stack(ORE, config.SLOT_STACK_MAX)
    assert sim.hotbar.used_slots == 1, "пустые слоты хотбара не заняты"


def test_picked_up_building_item_lands_in_the_hotbar():
    """Подбор с земли идёт по тому же правилу: постройка — в хотбар, руда — в рюкзак."""
    sim = scene()
    sim.drops.spawn_tile(LAB, 1, *DRONE_TILE)
    sim.drops.spawn_tile(ORE, 4, *DRONE_TILE)
    run(sim, 3 * config.TICK)
    assert len(sim.drops) == 0, "всё подобрано"
    assert sim.hotbar.count(LAB) == 1 and sim.inventory.count(LAB) == 0
    assert sim.inventory.count(ORE) == 4 and sim.hotbar.count(ORE) == 0


# ------------------------------------------------------- крафт ×5 и максимум
LAMP_RECIPE = building_def(LAMP).recipe


def lamp_stock(sim: Simulation, times: int) -> str:
    """Сырьё ровно на `times` ламп и одна лишняя единица. Возвращает её ресурс.

    Лишняя единица — проверка, что «сколько можно» не забирает остаток,
    которого на ещё одну лампу не хватает.
    """
    give_materials(sim, LAMP_RECIPE, times)
    extra = narrow(sim, LAMP_RECIPE.requires[0][0])
    sim.inventory.add(extra, 1)
    return extra


def lamps(sim: Simulation) -> int:
    return sim.cargo.count(LAMP)


def made_toast(sim: Simulation, made: int) -> list[str]:
    return [f"+{LAMP_RECIPE.outputs * made} {sim.run.items[LAMP].name}"]


def test_craft_five_makes_as_many_as_there_is_material_for():
    """Shift+клик просит пять, а материала на три: сделано три, сообщение одно."""
    sim = scene()
    lamp_stock(sim, 3)
    sim.toasts.clear()
    assert sim.craft(LAMP_RECIPE.id, 5)
    assert lamps(sim) == sim.hotbar.count(LAMP) == 3 * LAMP_RECIPE.outputs
    assert plan_craft(LAMP_RECIPE, sim.cargo, sim.run.items) is None, "материал кончился"
    assert sim.inventory.total_count() == 1, "лишняя единица осталась"
    assert [t.text for t in sim.toasts] == made_toast(sim, 3)


def test_craft_five_stops_at_five():
    sim = scene()
    lamp_stock(sim, 7)
    sim.toasts.clear()
    assert sim.craft(LAMP_RECIPE.id, 5)
    assert lamps(sim) == 5 * LAMP_RECIPE.outputs
    assert [t.text for t in sim.toasts] == made_toast(sim, 5)
    assert sim.craft(LAMP_RECIPE.id, None), "материала осталось ещё на две"
    assert lamps(sim) == 7 * LAMP_RECIPE.outputs


def test_craft_max_uses_up_the_material():
    """Ctrl+клик (`times=None`) — сколько хватит; одно сообщение на всё."""
    sim = scene()
    lamp_stock(sim, 4)
    sim.toasts.clear()
    assert sim.craft(LAMP_RECIPE.id, None)
    assert lamps(sim) == 4 * LAMP_RECIPE.outputs
    assert sim.crafted_total == 4 * LAMP_RECIPE.outputs
    assert sim.inventory.total_count() == 1, "лишняя единица осталась"
    assert [t.text for t in sim.toasts] == made_toast(sim, 4)


def test_craft_without_material_says_why_once_and_changes_nothing():
    sim = scene()
    give_materials(sim, LAMP_RECIPE)
    assert sim.inventory.take(narrow(sim, LAMP_RECIPE.requires[0][0]), 1) == 1
    for times in (1, 5, None):
        before = sim.state_signature()
        sim.toasts.clear()
        assert not sim.craft(LAMP_RECIPE.id, times)
        assert sim.state_signature() == before
        assert len(sim.toasts) == 1, "одна причина отказа"


# ------------------------------------------------------------- постановка
@pytest.mark.parametrize("kind", BUILD_ORDER)
def test_place_command_spends_exactly_one_item(kind):
    """ПКМ с постройкой в слоте: здание на тайле, из слота ушла одна единица.

    Бур ставится на залежь (`placeable_on`), всё остальное — на землю.
    """
    sim = scene()
    give_building(sim, kind, 3)
    slot = select(sim, kind)
    tile = DEPOSIT if kind == DRILL else GROUND
    assert sim.can_place(kind, *tile)
    before = Counter(dict(sim.cargo.items()))

    placed = sim.apply(PlaceBuilding(slot, tile))
    assert placed is not None and at(sim, tile) is placed
    before[kind] -= 1
    assert Counter(dict(sim.cargo.items())) == +before, "потрачен ровно один предмет"
    assert sim.hotbar.slot(slot) == Stack(kind, 2)


def _occupied_by_building(sim):
    assert place(sim, STORAGE, *GROUND) is not None
    return LAB, GROUND


def _occupied_by_lamp(sim):
    assert place(sim, LAMP, *GROUND) is not None
    return STORAGE, GROUND


def _out_of_range(sim):
    # Тайл сам по себе годится — отказ объясняется только дальностью.
    assert sim._tile_ok_for(STORAGE, *NEAR)
    sim.drone.x = 1.5
    assert not sim.in_build_range(*NEAR)
    return STORAGE, NEAR


def _outside_the_map(sim):
    sim.drone.x = 0.5
    return LAMP, (-1, DRONE_TILE[1])


def _relic(sim):
    relic = nearest_relic(sim)
    sim.drone.x, sim.drone.y = relic.x + 0.5, relic.y + 0.5
    return LAMP, (relic.x, relic.y)


INVALID = {
    "building": _occupied_by_building,
    "lamp": _occupied_by_lamp,
    "far": _out_of_range,
    "outside": _outside_the_map,
    "mountain": lambda sim: (LAMP, MOUNTAIN),
    "water": lambda sim: (LAB, WATER),
    "deposit": lambda sim: (STORAGE, DEPOSIT),
    "deposit_lamp": lambda sim: (LAMP, DEPOSIT),
    "drill_on_ground": lambda sim: (DRILL, GROUND),
    "drill_on_depleted": lambda sim: (DRILL, DEPLETED),
    "relic": _relic,
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_placement_changes_nothing(case):
    """Занято, далеко, гора, вода, залежь не под бур, реликвия: отказ без следа."""
    sim = scene()
    kind, tile = INVALID[case](sim)
    give_building(sim, kind, 2)
    slot = select(sim, kind)
    assert not sim.can_place(kind, *tile), "призрак красный"
    before = sim.state_signature()
    assert sim.apply(PlaceBuilding(slot, tile)) is None
    assert sim.state_signature() == before, "ничего не изменилось"
    assert sim.hotbar.count(kind) == 2, "предмет не потрачен"


def test_items_on_the_ground_stay_under_a_new_building():
    """Предметы на земле не мешают стройке и остаются лежать под зданием."""
    sim = scene()
    sim.drops.spawn_tile(ORE, 3, *NEAR)
    give_building(sim, STORAGE, 1)
    slot = select(sim, STORAGE)
    assert sim.can_place(STORAGE, *NEAR)
    storage = sim.apply(PlaceBuilding(slot, NEAR))
    assert storage is not None
    run(sim, 10 * config.TICK)
    assert sim.buildings.at(*NEAR) is storage
    assert [(d.item_id, d.count) for d in sim.drops.drops_at(*NEAR)] == [(ORE, 3)]


def test_empty_or_resource_slot_places_nothing():
    """ПКМ по земле, слот пуст или с ресурсом: не команда — ни здания, ни тоста."""
    sim = scene()
    assert sim.selected_stack() is None
    before = sim.state_signature()
    assert sim.apply(PlaceBuilding(sim.selected_slot, GROUND)) is None
    assert sim.state_signature() == before and len(sim.toasts) == 0

    sim.hotbar.put_slot(sim.selected_slot, Stack(ORE, 5))
    assert sim.selected_building() is None, "ресурс — не постройка"
    before = sim.state_signature()
    assert sim.apply(PlaceBuilding(sim.selected_slot, GROUND)) is None
    assert sim.state_signature() == before and len(sim.toasts) == 0
    assert at(sim, GROUND) is None


def test_holding_rmb_on_the_ground_with_an_empty_slot_does_nothing():
    sim = scene()
    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST)
    sim.set_cursor_tile(*GROUND)
    sim.set_scanning(True)
    run(sim, config.SCAN_TIME * 2)
    assert sim.scans_done == 0 and sim.scan_progress == 0.0
    assert not sim.drone.action.active
    assert sim.inventory.count(ENERGY_ITEM) == config.SCAN_ENERGY_COST


# ------------------------------------------------------------------- скан
def scan_ready(slot_item: tuple[str, int] | None) -> Simulation:
    """Сцена со скан-энергией и курсором на залежи; в выбранном слоте — `slot_item`."""
    sim = scene()
    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST)
    if slot_item is not None:
        sim.hotbar.put_slot(sim.selected_slot, Stack(*slot_item))
    sim.set_cursor_tile(*DEPOSIT)
    assert sim.knowledge.unknown(ORE), "скану есть что открыть"
    return sim


@pytest.mark.parametrize("slot_item", [None, (FILLER, 5)], ids=["empty", "resource"])
def test_rmb_hold_on_a_deposit_scans_when_the_slot_holds_no_building(slot_item):
    sim = scan_ready(slot_item)
    unknown = len(sim.knowledge.unknown(ORE))
    sim.set_scanning(True)
    run(sim, config.SCAN_TIME * 1.2)
    assert sim.scans_done == 1
    assert len(sim.knowledge.unknown(ORE)) == unknown - config.SCAN_REVEALS
    assert sim.cargo.count(ENERGY_ITEM) == 0, "скан потратил энергию"


@pytest.mark.parametrize("kind", [LAMP, DRILL])
def test_building_in_the_slot_blocks_the_scan(kind):
    """С постройкой в руке ПКМ — постановка, а не скан: даже бур над залежью.

    Иначе игрок, прицеливаясь буром, случайно тратил бы энергию на скан.
    """
    sim = scan_ready((kind, 1))
    assert sim.selected_building() == kind
    knowledge = sim.knowledge.state()
    sim.set_scanning(True)
    for _ in range(int(config.SCAN_TIME * 2 / config.TICK)):
        sim.update(config.TICK)
        assert sim.drone.action.kind != ACTION_SCAN
    assert sim.scans_done == 0 and sim.scan_progress == 0.0
    assert sim.knowledge.state() == knowledge
    assert sim.cargo.count(ENERGY_ITEM) == config.SCAN_ENERGY_COST


# -------------------------------------------- призрак = постановка (одна проверка)
MESSY = 20


def messy_sim(rng: random.Random) -> Simulation:
    """Пёстрая карта: горы, вода, залежи (часть выработана), здания, лампы, вещи."""
    natural = RUN0.items.natural_ids
    rows: list[list[int]] = []
    for _y in range(MESSY):
        row = []
        for _x in range(MESSY):
            roll = rng.random()
            row.append(int(Terrain.MOUNTAIN) if roll < 0.12
                       else int(Terrain.WATER) if roll < 0.2 else int(Terrain.GROUND))
        rows.append(row)
    center = MESSY // 2
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            rows[center + dy][center + dx] = int(Terrain.GROUND)
    deposits: list[Deposit] = []
    for y in range(MESSY):
        for x in range(MESSY):
            if rows[y][x] == int(Terrain.GROUND) and (x, y) != (center, center) \
                    and rng.random() < 0.12:
                amount = 0 if rng.random() < 0.3 else 10
                deposits.append(Deposit(len(deposits), rng.choice(natural), x, y, amount))
    sim = Simulation(0, make_world(rows, deposits))
    sim.drone.x, sim.drone.y = center + 0.5, center + 0.5
    populate(sim, rng)
    return sim


def populate(sim: Simulation, rng: random.Random, buildings: int = 20,
             drops: int = 8) -> None:
    """Расставляет постройки и роняет предметы рядом с дроном."""
    tx, ty = sim.drone.tile()
    reach = int(config.BUILD_RANGE)
    for _ in range(buildings * 10):
        if buildings <= 0:
            break
        kind = rng.choice(BUILD_ORDER)
        x, y = tx + rng.randint(-reach, reach), ty + rng.randint(-reach, reach)
        if sim.can_place(kind, x, y):
            assert place(sim, kind, x, y) is not None
            buildings -= 1
    for _ in range(drops):
        x, y = tx + rng.randint(-reach, reach), ty + rng.randint(-reach, reach)
        if sim.world.is_walkable_tile(x, y):
            sim.drops.spawn_tile(ORE, 1 + rng.randrange(5), x, y)


def real_sim(rng: random.Random) -> Simulation:
    sim = Simulation(SEED)
    populate(sim, rng)
    return sim


def refusal_reason(sim: Simulation, kind: str, x: int, y: int) -> str:
    """Почему тайл красный — только для учёта покрытия выборки, не как эталон."""
    if not sim.in_build_range(x, y):
        return "far"
    if not sim.world.is_walkable_tile(x, y):
        return "terrain"
    if at(sim, (x, y)) is not None:
        return "taken"
    if sim.run.relic_at(x, y) is not None:
        return "relic"
    if (sim.world.deposit_at(x, y) is None) == (kind == DRILL):
        return "deposit"
    return "other"


@pytest.mark.parametrize("factory, needed", [
    (messy_sim, {"ok", "far", "terrain", "taken", "deposit"}),
    (real_sim, {"ok", "far", "taken", "deposit"}),
], ids=["messy", "real"])
def test_ghost_agrees_with_place_building_on_random_tiles(factory, needed):
    """Для случайных тайлов «призрак зелёный» ⇔ «PlaceBuilding поставил».

    Призрак спрашивает `sim.can_place(sim.selected_building(), x, y)`
    (`render/ghost.py`), постановка идёт командой из выбранного слота. Половину
    поставленного тут же разбираем — тогда на тайле остаётся лежать предмет, и
    проверяется ещё и «вещи на земле не мешают».
    """
    rng = random.Random(SEED)
    sim = factory(rng)
    # Чуть дальше радиуса: примерно половина тайлов в BUILD_RANGE, половина — за ним.
    reach = int(config.BUILD_RANGE) + 1
    seen: Counter = Counter()
    for step in range(200):
        if step % 40 == 20:
            # Дрон отходит: та же клетка становится то близкой, то далёкой.
            tx, ty = sim.drone.tile()
            for _ in range(50):
                nx, ny = tx + rng.randint(-3, 3), ty + rng.randint(-3, 3)
                if sim.world.is_walkable_tile(nx, ny):
                    sim.drone.x, sim.drone.y = nx + 0.5, ny + 0.5
                    break
        kind = rng.choice(BUILD_ORDER)
        tx, ty = sim.drone.tile()
        x, y = tx + rng.randint(-reach, reach), ty + rng.randint(-reach, reach)
        if sim.hotbar.count(kind) == 0:
            give_building(sim, kind, 1)
        select(sim, kind)
        ghost_ok = sim.can_place(sim.selected_building(), x, y)
        reason = "ok" if ghost_ok else refusal_reason(sim, kind, x, y)
        held = sim.hotbar.count(kind)
        before = sim.state_signature()

        placed = sim.apply(PlaceBuilding(sim.selected_slot, (x, y), rng.randrange(4)))
        assert (placed is not None) == ghost_ok, \
            f"шаг {step}: {kind} на {(x, y)} — призрак {ghost_ok}, стройка {placed}"
        seen[reason] += 1
        if placed is None:
            assert sim.state_signature() == before, f"шаг {step}: отказ изменил состояние"
            continue
        assert at(sim, (x, y)) is placed
        assert sim.hotbar.count(kind) == held - 1
        if rng.random() < 0.5:
            assert sim.apply(DismantleBuilding((x, y)))
    missing = needed - set(seen)
    assert not missing, f"выборка не покрыла случаи {missing}: {dict(seen)}"


# --------------------------------------------------------------- демонтаж
def stocked_storage(sim: Simulation, tile=NEAR):
    """Склад с содержимым: два стака руды и предмет роли."""
    storage = place(sim, STORAGE, *tile)
    assert storage is not None
    store = storage.container(building_def(STORAGE).accept_id)
    store.add(ORE, config.SLOT_STACK_MAX + 7)
    store.add(ENERGY_ITEM, 3)
    return storage


def test_holding_lmb_turns_a_building_into_one_item_on_its_tile():
    """За 🔧 `DISMANTLE_TIME` здание становится предметом на своём тайле.

    Содержимое высыпается вокруг, в руки ничего не кладётся, а сумма всех
    предметов (здания + земля + дрон) не меняется.
    """
    sim = scene()
    stocked_storage(sim)
    total = everything(sim)
    cargo = sorted(sim.cargo.items())

    hold_lmb(sim, NEAR, config.DISMANTLE_TIME * 0.9)
    assert sim.buildings.at(*NEAR) is not None, "до конца кольца здание стоит"
    run(sim, DISMANTLE_HOLD - config.DISMANTLE_TIME * 0.9)
    assert sim.buildings.at(*NEAR) is None, "разобрано"

    assert [(d.item_id, d.count) for d in sim.drops.drops_at(*NEAR)] == [(STORAGE, 1)]
    ground = sim.drops.total()
    assert ground[STORAGE] == 1, "ровно одна постройка"
    assert ground[ORE] == config.SLOT_STACK_MAX + 7 and ground[ENERGY_ITEM] == 3, \
        "содержимое выпало целиком"
    assert sorted(sim.cargo.items()) == cargo, "прямо в хотбар не кладётся"
    assert everything(sim) == total, "сумма предметов сохранилась"
    assert sim.dismantle_target is None and sim.dismantle_progress == 0.0


def test_lamp_is_dismantled_the_same_way():
    sim = scene()
    assert place(sim, LAMP, *NEAR) is not None
    total = everything(sim)
    hold_lmb(sim, NEAR, DISMANTLE_HOLD)
    assert sim.light.lamp_at(*NEAR) is None
    assert [(d.item_id, d.count) for d in sim.drops.drops_at(*NEAR)] == [(LAMP, 1)]
    assert everything(sim) == total


def world_items(sim: Simulation) -> tuple:
    """Слепок вещей и построек без часов: время в паузе между шагами идёт."""
    return (sim.buildings.state(), tuple((l.x, l.y) for l in sim.light.lamps),
            sim.drops.state(), sim.hotbar.state(), sim.inventory.state())


def test_released_lmb_cancels_the_dismantle_and_resets_progress():
    """Отпустил на половине: здание стоит, прогресс 0 и не копится между попытками."""
    sim = scene()
    stocked_storage(sim)
    run(sim, config.TICK)                       # сеть и прочее устоялись
    before = world_items(sim)

    hold_lmb(sim, NEAR, config.DISMANTLE_TIME * 0.6)
    assert 0.0 < sim.dismantle_progress < 1.0
    sim.set_mining(False)
    run(sim, config.TICK)
    assert sim.dismantle_progress == 0.0 and sim.dismantle_target is None
    assert world_items(sim) == before, "ничего не изменилось"

    # Вторая попытка начинается с нуля: 0.6 + 0.6 > 1, но здание всё ещё стоит.
    hold_lmb(sim, NEAR, config.DISMANTLE_TIME * 0.6)
    assert sim.buildings.at(*NEAR) is not None
    sim.set_mining(False)
    run(sim, config.TICK)
    assert world_items(sim) == before


def test_building_out_of_build_range_is_not_dismantled():
    sim = scene()
    storage = stocked_storage(sim)
    sim.drone.x = NEAR[0] + 0.5 - config.BUILD_RANGE - 1.0
    assert not sim.in_build_range(*NEAR)
    run(sim, config.TICK)
    before = world_items(sim)
    hold_lmb(sim, NEAR, config.DISMANTLE_TIME * 2)
    assert sim.buildings.at(*NEAR) is storage
    assert sim.dismantle_progress == 0.0 and sim.dismantle_candidate() is None
    assert not sim.drone.action.active
    assert not sim.apply(DismantleBuilding(NEAR)), "и командой тоже далеко"
    assert world_items(sim) == before


def test_undismantlable_building_stays(monkeypatch):
    """Флаг данных `dismantlable = False` (ядро базы и т.п.) запрещает разбор."""
    monkeypatch.setitem(BUILDING_TYPES, STORAGE,
                        replace(BUILDING_TYPES[STORAGE], dismantlable=False))
    sim = scene()
    storage = stocked_storage(sim)
    run(sim, config.TICK)
    before = world_items(sim)
    hold_lmb(sim, NEAR, config.DISMANTLE_TIME * 2)
    assert sim.buildings.at(*NEAR) is storage and sim.dismantle_progress == 0.0
    assert not sim.apply(DismantleBuilding(NEAR))
    assert world_items(sim) == before


def test_dismantled_item_waits_on_the_ground_when_the_drone_is_full():
    """Некуда подобрать — постройка лежит на земле и не пропадает."""
    sim = scene()
    assert place(sim, STORAGE, *GROUND) is not None
    sim.hotbar.add(FILLER, config.HOTBAR_SLOTS * config.SLOT_STACK_MAX)
    sim.inventory.add(FILLER, config.DRONE_SLOTS * config.SLOT_STACK_MAX)
    assert not sim.cargo.can_accept(STORAGE)
    total = everything(sim)

    hold_lmb(sim, GROUND, DISMANTLE_HOLD)
    sim.set_mining(False)
    run(sim, 1.0)                               # дрон стоит рядом, в радиусе подбора
    assert sim.buildings.at(*GROUND) is None
    assert [(d.item_id, d.count) for d in sim.drops.drops_at(*GROUND)] == [(STORAGE, 1)]
    assert everything(sim) == total


# ------------------------------------------------------- действие дрона
def test_drone_action_mine_then_none_after_release():
    sim = scene()
    hold_lmb(sim, DEPOSIT, config.MINE_TIME_PER_UNIT * 0.25)
    first = sim.drone.action
    assert first.kind == ACTION_MINE and first.active
    assert first.target_world_pos == (DEPOSIT[0] + 0.5, DEPOSIT[1] + 0.5), "центр залежи"
    run(sim, config.MINE_TIME_PER_UNIT * 0.4)
    second = sim.drone.action
    assert 0.0 < first.progress < second.progress < 1.0
    assert second.progress == sim.mine_progress

    sim.set_mining(False)
    sim.update(config.TICK)
    assert sim.drone.action.kind is None and not sim.drone.action.active


def test_drone_action_dismantle_aims_at_the_building():
    sim = scene()
    stocked_storage(sim)
    hold_lmb(sim, NEAR, config.DISMANTLE_TIME * 0.3)
    first = sim.drone.action
    assert first.kind == ACTION_DISMANTLE and first.active
    assert first.target_world_pos == (NEAR[0] + 0.5, NEAR[1] + 0.5)
    run(sim, config.DISMANTLE_TIME * 0.3)
    second = sim.drone.action
    assert 0.0 < first.progress < second.progress < 1.0
    assert second.progress == sim.dismantle_progress


def test_drone_action_study_on_a_relic_ends_when_it_is_studied():
    sim = scene()
    relic = nearest_relic(sim)
    sim.drone.x, sim.drone.y = relic.x + 0.5, relic.y + 0.5
    hold_lmb(sim, (relic.x, relic.y), config.RELIC_STUDY_TIME * 0.2)
    first = sim.drone.action
    assert first.kind == ACTION_STUDY and first.active
    assert first.target_world_pos == (relic.x + 0.5, relic.y + 0.5)
    run(sim, config.RELIC_STUDY_TIME * 0.3)
    second = sim.drone.action
    assert 0.0 < first.progress < second.progress < 1.0
    run(sim, config.RELIC_STUDY_TIME * 0.6)     # кнопка всё ещё зажата
    assert relic.studied
    assert not sim.drone.action.active, "изучено — луч гаснет"


def test_drone_action_scan_on_a_deposit():
    sim = scan_ready(None)
    sim.set_scanning(True)
    run(sim, config.SCAN_TIME * 0.25)
    first = sim.drone.action
    assert first.kind == ACTION_SCAN and first.active
    assert first.target_world_pos == (DEPOSIT[0] + 0.5, DEPOSIT[1] + 0.5)
    run(sim, config.SCAN_TIME * 0.4)
    second = sim.drone.action
    assert 0.0 < first.progress < second.progress < 1.0
    sim.set_scanning(False)
    sim.update(config.TICK)
    assert not sim.drone.action.active


def test_no_action_when_the_target_is_out_of_reach():
    """Луч только при цели в радиусе: далёкая залежь — ни добычи, ни скана."""
    sim = scan_ready(None)
    sim.drone.x = DEPOSIT[0] + 0.5 - config.SCAN_RANGE - 1.0
    sim.set_mining(True)
    run(sim, 0.3)
    assert not sim.drone.action.active
    sim.set_mining(False)
    sim.set_scanning(True)
    run(sim, 0.3)
    assert not sim.drone.action.active


# -------------------------------------------------------------- поворот R
def test_r_turns_the_conveyor_ghost_clockwise():
    """С конвейером в слоте R крутит призрак: восток → юг → запад → север.

    Ось y экрана смотрит вниз, поэтому порядок `DIRECTIONS` и есть «по часовой».
    Поставленный конвейер смотрит туда же, куда призрак, а стоящий под
    курсором конвейер не крутится — призрак важнее.
    """
    sim = scene()
    standing = place(sim, CONVEYOR, *NEAR, direction=E)
    give_building(sim, CONVEYOR, 2)
    assert sim.selected_building() == CONVEYOR
    assert sim.ghost_direction(*EAST) == E, "без поворота — «от дрона»"
    turns = []
    for _ in range(4):
        assert sim.rotate_preview(*EAST)
        turns.append(sim.ghost_direction(*EAST))
    assert turns == [S, W, N, E]

    assert sim.rotate_preview(*NEAR)
    assert standing.direction == E, "призрак важнее стоящего конвейера"
    assert sim.ghost_direction(*EAST) == S
    conveyor = sim.apply(PlaceBuilding(sim.selected_slot, EAST))
    assert conveyor is not None and conveyor.direction == S


def test_r_without_a_ghost_turns_the_conveyor_under_the_cursor():
    sim = scene()
    conveyor = place(sim, CONVEYOR, *EAST, direction=E)
    assert sim.selected_building() is None, "предмет потрачен, слот пуст"
    for expected in (S, W, N, E):
        assert sim.rotate_preview(*EAST)
        assert conveyor.direction == expected
    assert sim.build_rotation is None


def test_r_with_a_lamp_and_no_conveyor_does_nothing():
    sim = scene()
    give_building(sim, LAMP, 1)
    assert place(sim, STORAGE, *NEAR) is not None   # не конвейер — не крутится
    before = sim.state_signature()
    for tile in (GROUND, NEAR):
        assert not sim.rotate_preview(*tile)
    assert sim.build_rotation is None
    assert sim.ghost_direction(*GROUND) is None, "у лампы нет направления"
    assert sim.state_signature() == before


# ------------------------------------------------------- выбор слота
def test_select_hotbar_and_scroll_wrap_around():
    sim = scene()
    last = config.HOTBAR_SLOTS - 1
    assert sim.apply(SelectHotbar(4)) and sim.selected_slot == 4
    assert sim.scroll_hotbar(1) == 5
    assert sim.apply(SelectHotbar(last))
    assert sim.scroll_hotbar(1) == 0, "за последним — первый"
    assert sim.scroll_hotbar(-1) == last, "перед первым — последний"
    assert sim.scroll_hotbar(0) == last
    assert sim.scroll_hotbar(config.HOTBAR_SLOTS) == last, "полный круг"
    assert sim.apply(SelectHotbar(config.HOTBAR_SLOTS)) is False
    assert sim.selected_slot == last


def test_selected_building_follows_the_slot():
    sim = scene()
    give_building(sim, LAMP, 1)                    # слот 0
    sim.hotbar.put_slot(1, Stack(ORE, 5))
    expected = {0: LAMP, 1: None, 2: None}
    for slot, kind in expected.items():
        assert sim.apply(SelectHotbar(slot))
        assert sim.selected_building() == kind


def test_changing_the_slot_resets_the_ghost_rotation():
    sim = scene()
    give_building(sim, CONVEYOR, 1)                # слот 0
    give_building(sim, WIRE, 1)                    # слот 1
    assert sim.rotate_preview(*EAST) and sim.build_rotation is not None
    assert sim.apply(SelectHotbar(1))
    assert sim.build_rotation is None
    assert sim.apply(SelectHotbar(0))
    assert sim.rotate_preview(*EAST) and sim.build_rotation is not None
    sim.scroll_hotbar(1)
    assert sim.build_rotation is None
    sim.scroll_hotbar(-1)
    assert sim.ghost_direction(*EAST) == E, "призрак снова «от дрона»"


# ------------------------------------------- стартовые постройки на любом сиде
def assert_first_night_craftable(stock: Container, run_plan, where: str) -> None:
    """Из этого запаса крафтятся и лампа, и лаборатория — одна за другой."""
    for kind in (LAMP, LAB):
        recipe = run_plan.recipe(building_def(kind).recipe.id)
        plan = plan_craft(recipe, stock, run_plan.items)
        assert plan is not None, f"{where}: не скрафтить {kind}"
        for item_id, count in plan.items():
            assert stock.remove(item_id, count)


def test_start_buildings_are_lamp_and_lab():
    assert set(BUILDING_START) == {LAMP, LAB}


def test_start_zone_deposits_feed_the_first_night():
    """На настоящих картах залежей стартовой зоны хватает на лампу и лабораторию."""
    for seed in range(20):
        world = World.generate(seed)
        run_plan = generate_run(world.seed)
        stock = Container(len(world.deposits) * 3)
        for deposit in world.deposits:
            if world.in_start_zone(deposit.x, deposit.y):
                stock.add(deposit.resource_id, deposit.amount)
        assert_first_night_craftable(stock, run_plan, f"seed {seed}")


@pytest.mark.slow
def test_start_buildings_are_craftable_on_1000_seeds():
    """Приёмка plan/02 с постройками: лампа и лаборатория — на каждом сиде.

    Запас — то, что дают залежи стартовой зоны: 🔧
    `DEPOSITS_PER_RESOURCE_START` × `DEPOSIT_AMOUNT` каждого обычного ресурса
    (редкий в критичную цепочку не входит).
    """
    per_resource = config.DEPOSITS_PER_RESOURCE_START * config.DEPOSIT_AMOUNT
    for seed in range(1000):
        run_plan = generate_run(seed)
        assert run_plan.craftable_at_start(), f"seed {seed}: стартовые постройки"
        stock = Container(len(run_plan.items.common_ids) * 5)
        for item_id in run_plan.items.common_ids:
            assert stock.add(item_id, per_resource) == 0
        assert_first_night_craftable(stock, run_plan, f"seed {seed}")
