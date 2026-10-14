"""Экономика M4 (plan/02, plan/03): сеть, постройки-предметы, бур, конвейер, склад, сборщик.

Приёмка этапа: «бур → конвейер → склад работает, лампа питается от генератора,
ресурсы тратятся по рецептам». Каждый пункт проверяется там, где он живёт:

- чистая сеть — `sim/power.py`, узлами и без мира;
- цена постройки — рецепт её предмета («свойство × количество»): крафт тратит
  ресурсы, постановка — ровно один предмет из хотбара (`PlaceBuilding`);
- дальность и место — `Simulation.can_place` / `Simulation.build`;
- работа зданий — через `sim.update`, то есть так, как это увидит игрок;
- данные зданий — на то, что рецепт ссылается на свойства, а предмет-постройка
  есть в забеге и ставит именно своё здание.

Сцена логистики — плоский мир с одной залежью (`make_world`): на настоящей карте
залежь под бур ещё надо найти, а тест должен падать от поломки логики, а не от
неудачного сида.
"""

from __future__ import annotations

import pytest

from src.game import config
from src.game.data.buildings import (ASSEMBLER, BUILD_ORDER, CONVEYOR, DRILL,
                                     GENERATOR, LAB, LAMP, PORT_FUEL, PORT_IN,
                                     PORT_OUT, PORT_STORE, ROLE_AUTOMATION,
                                     ROLE_AMMO, ROLE_ENERGY, ROLE_STRUCTURE,
                                     STORAGE, TURRET, WALL, WIRE, WORK_CRAFT,
                                     building as building_def)
from src.game.data.recipes import ROLES, recipe as recipe_def
from src.game.data.resources import PROP_NAMES, Prop
from src.game.data.terrain import Terrain
from src.game.sim.buildings import DIRECTIONS, _is_fuel
from src.game.sim.commands import DismantleBuilding, PlaceBuilding, SelectHotbar
from src.game.sim.power import PowerNode, PowerSystem
from src.game.sim.research import plan_craft
from src.game.sim.simulation import ENERGY_ITEM, Simulation
from src.game.sim.world import Deposit
from tests.conftest import (SEED, STOCK_COUNT, make_world, place, power_grid,
                            slot_of, stock)

FLAT = 12                      # сторона плоского тестового мира
DEPOSIT_TILE = (4, 5)          # залежь под будущим буром
DEPOSIT_AMOUNT = 30


def seconds(value: float) -> int:
    """Сколько шагов симуляции в этих секундах (с запасом на накопление float)."""
    return int(value / config.TICK) + 2


def line_sim() -> Simulation:
    """Плоский мир с одной залежью и запасом материалов: сцена логистики.

    Дрон появляется в центре (6, 6) — вся линия вплотную к нему, поэтому
    стройка проходит проверку 🔧 `BUILD_RANGE` без перетаскивания дрона.
    """
    x, y = DEPOSIT_TILE
    world = make_world([[int(Terrain.GROUND)] * FLAT for _ in range(FLAT)],
                       [Deposit(0, "res0", x, y, DEPOSIT_AMOUNT)])
    return stock(Simulation(0, world))


def free_tile(sim: Simulation, kind: str, x: int, y: int) -> tuple[int, int]:
    """Свободный тайл рядом с (x, y), куда встанет это здание."""
    for dx, dy in DIRECTIONS:
        if sim._tile_ok_for(kind, x + dx, y + dy):
            return x + dx, y + dy
    raise AssertionError(f"рядом с тайлом {x}, {y} нет места под {kind}")


def fill_generator(generator, sim: Simulation, count: int = 10) -> str:
    """Кладёт топливо в топку генератора. Возвращает id топлива."""
    fuel_id = sim.run.items.with_prop(Prop.FLAMMABLE)[0].id
    container = generator.container(building_def(GENERATOR).accept_id)
    assert container is not None, "у генератора должна быть топка"
    container.add(fuel_id, count)
    return fuel_id


def empty(container) -> None:
    """Опустошает контейнер: так тест «забирает топливо» без обходов."""
    for index in range(container.size):
        container.take_slot(index)


def give_materials(sim: Simulation, recipe) -> None:
    """Кладёт в инвентарь сырьё под рецепт: на каждое свойство — самый узкий ресурс.

    Узкий — чтобы один ресурс не закрыл два свойства сразу и тест проверял
    рецепт, а не удачный подбор; что именно потратится, решает `plan_craft`.
    """
    for prop, count in recipe.requires:
        item = min(sim.run.items.with_prop(prop), key=lambda i: (len(i.props), i.id))
        sim.inventory.add(item.id, count)


def spent_by(sim: Simulation, recipe) -> dict[str, int]:
    """Что крафт потратит прямо сейчас — по тем же правилам, что и сам крафт."""
    plan = plan_craft(recipe, sim.cargo, sim.run.items)
    assert plan is not None, f"{recipe.id}: материалов не хватает"
    return plan


def build_line(sim: Simulation) -> tuple:
    """Бур на залежи → конвейер → склад плюс заправленный генератор под сеть.

    Направление конвейера задаётся явно: `build_direction` считает его от
    дрона, а тест проверяет линию, а не эвристику поворота (она — отдельно).
    """
    dx, dy = DEPOSIT_TILE
    drill = sim.build(DRILL, dx, dy)
    conveyor = sim.build(CONVEYOR, dx + 1, dy, direction=(1, 0))
    storage = sim.build(STORAGE, dx + 2, dy)
    generator = sim.build(GENERATOR, dx + 1, dy + 1)   # сосед конвейера
    assert drill is not None and conveyor is not None
    assert storage is not None and generator is not None
    fill_generator(generator, sim)
    return drill, conveyor, storage, generator


# ------------------------------------------------------------------- сеть
def node(x: int, y: int, supply: float = 0.0, demand: float = 0.0) -> PowerNode:
    return PowerNode(x, y, object(), supply=supply, demand=demand)


def test_adjacent_nodes_share_one_network():
    """Сеть — это связные узлы на соседних тайлах, а не весь список."""
    system = PowerSystem()
    networks = system.update([node(0, 0, supply=6.0), node(1, 0, demand=2.0),
                              node(2, 0, demand=2.0)])
    assert len(networks) == 1
    assert networks[0].supply == 6.0 and networks[0].demand == 4.0
    assert networks[0].powered and system.starved == 0


def test_far_nodes_are_different_networks():
    """Через пустое место энергия не течёт: потребитель вдали остаётся без неё."""
    system = PowerSystem()
    networks = system.update([node(0, 0, supply=6.0), node(5, 0, demand=2.0)])
    assert len(networks) == 2
    assert {net.powered for net in networks} == {False}, \
        "генератор без потребителей тоже «не работает»"
    assert system.starved == 1, "один потребитель без энергии"


def test_network_needs_supply_not_less_than_demand():
    """Правило простое и грубое: хватает или не хватает, запасов нет."""
    system = PowerSystem()
    system.update([node(0, 0, supply=1.9), node(1, 0, demand=2.0)])
    assert not system.networks[0].powered
    system.update([node(0, 0, supply=2.0), node(1, 0, demand=2.0)])
    assert system.networks[0].powered


def test_wants_power_is_about_demand_not_supply():
    """`wants` — «сети нужно топливо», и он не зависит от выработки.

    Иначе вышел бы порочный круг: нет топлива → нет энергии → генератор не жжёт
    топливо → топливо и не нужно.
    """
    system = PowerSystem()
    system.update([node(0, 0), node(1, 0, demand=2.0)])
    consumer = system.networks[0].nodes[1]
    assert consumer.wants and not consumer.powered
    system.update([node(0, 0, supply=1.0)])          # сеть без потребителей
    assert not system.networks[0].nodes[0].wants


def test_summary_sums_supply_and_demand_of_all_networks():
    system = PowerSystem()
    system.update([node(0, 0, supply=6.0), node(1, 0, demand=1.0),
                   node(9, 9, supply=2.0), node(8, 9, demand=1.0)])
    assert system.summary() == (8.0, 2.0)


# ------------------------------------------------------------- данные зданий
def test_every_recipe_and_port_exists_in_the_data():
    """Рецепт постройки — из свойств, её предмет — в забеге, порты — известные роли."""
    sim = Simulation(SEED)
    ports = {PORT_IN, PORT_OUT, PORT_STORE, PORT_FUEL}
    run_recipes = {recipe.id for recipe in sim.run.recipes}
    for kind in BUILD_ORDER:
        definition = building_def(kind)
        recipe = definition.recipe
        assert recipe is not None, f"{kind}: постройку нечем скрафтить"
        assert recipe.id in run_recipes, f"{kind}: рецепта нет в забеге"
        assert recipe.role == kind and recipe.outputs >= 1, \
            f"{kind}: рецепт должен давать предмет этой постройки"
        assert recipe.requires, f"{kind}: рецепт без требований"
        for prop, count in recipe.requires:
            assert isinstance(prop, Prop), f"{kind}: требование {prop} — не свойство"
            assert count >= 1, f"{kind}: свойство {prop} без количества"
        item = sim.run.items.get(kind)
        assert item is not None, f"{kind}: предмета постройки нет в забеге"
        assert item.building == kind, f"{kind}: предмет ставит не то здание"
        assert item.stack_max == config.BUILDING_STACK
        ids = [c.id for c in definition.containers]
        assert len(set(ids)) == len(ids), f"{kind}: повтор id контейнера"
        for container in definition.containers:
            assert container.slots >= 1 and container.cols >= 1
            assert container.role in ports, f"{kind}: неизвестная роль {container.role}"
        if definition.needs_power:
            assert definition.power_use > 0, f"{kind}: питание без потребления"
        if definition.power_out:
            assert definition.fuel, f"{kind}: даёт энергию, но нечем топиться"
            for mark in definition.fuel:
                assert isinstance(mark, Prop) or mark in sim.run.items


def test_buildings_unlock_by_the_base_relic_of_their_role():
    """Лампа и лаборатория известны с начала, остальное открывает реликвия роли.

    Без лампы первая ночь непроходима, без лаборатории не открыть свойства —
    поэтому реликвия для них была бы замкнутым кругом (plan/02, plan/03).
    """
    expected = {
        LAMP: None, LAB: None,
        STORAGE: ROLE_STRUCTURE,
        DRILL: ROLE_AUTOMATION, CONVEYOR: ROLE_AUTOMATION,
        WIRE: ROLE_AUTOMATION, ASSEMBLER: ROLE_AUTOMATION,
        GENERATOR: ROLE_ENERGY,
        # M5: турель открывает реликвия «боеприпас», стену — «конструкции».
        TURRET: ROLE_AMMO, WALL: ROLE_STRUCTURE,
    }
    assert {kind: building_def(kind).unlock_role for kind in BUILD_ORDER} == expected

    sim = Simulation(SEED)
    for kind, role in expected.items():
        known = sim.knowledge.is_known(building_def(kind).recipe.id)
        assert known == (role is None), f"{kind}: неверно известна на старте"
    for role in (ROLE_STRUCTURE, ROLE_AUTOMATION, ROLE_ENERGY, ROLE_AMMO):
        relic = sim.run.relic_of(role)
        assert relic is not None and sim.study_relic(relic), f"реликвия {role}"
        for kind, unlock in expected.items():
            if unlock == role:
                assert sim.knowledge.is_known(building_def(kind).recipe.id), \
                    f"реликвия {role} не открыла {kind}"
    assert all(sim.knowledge.is_known(building_def(kind).recipe.id)
               for kind in BUILD_ORDER)


def test_every_role_has_two_recipes_with_time():
    """Роль без рецепта нечем собрать, а рецепт без времени — нечего тратить."""
    for role in ROLES:
        assert recipe_def(role.base.id).role == role.id
        assert recipe_def(role.base.id).craft_time > 0
        assert recipe_def(role.amplified.id).craft_time > 0


def test_crafting_buildings_have_input_and_output():
    """Сборщику есть откуда брать и куда класть — иначе он просто стоит."""
    definition = building_def(ASSEMBLER)
    assert definition.work == WORK_CRAFT
    assert definition.input_id is not None and definition.output_id is not None


# ------------------------------------------------------- постройки-предметы
def test_building_item_is_crafted_only_after_its_recipe_is_known():
    """Крафт постройки (plan/02): неизученное не крафтится, изученное — в хотбар.

    Склад открывает конструкционная реликвия. До неё рецепта нет даже в
    панели крафта, и материалы лежат нетронутыми; после — крафт тратит ровно
    то, что выбрал `plan_craft`, а предмет-постройка ложится в хотбар.
    """
    sim = Simulation(SEED)
    recipe = building_def(STORAGE).recipe
    give_materials(sim, recipe)
    held = sorted(sim.cargo.items())
    assert recipe.id not in {option.recipe_id for option in sim.craft_options()}, \
        "неизученного рецепта в панели нет"
    assert not sim.craft(recipe.id), "рецепт ещё не открыт"
    assert sorted(sim.cargo.items()) == held, "отказ ничего не тратит"

    assert sim.study_relic(sim.run.relic_of(ROLE_STRUCTURE))
    plan = spent_by(sim, recipe)
    before = {item_id: sim.cargo.count(item_id) for item_id in plan}
    assert sim.craft(recipe.id)
    for item_id, count in plan.items():
        assert sim.cargo.count(item_id) == before[item_id] - count, item_id
    assert sim.hotbar.count(STORAGE) == recipe.outputs, "постройка — в хотбар"
    assert sim.inventory.count(STORAGE) == 0


def test_craft_option_names_what_is_lacking():
    """Игрок должен узнать, чего не хватает: панель называет свойства и количества.

    «Есть M» считается только по открытым свойствам: ресурс со свойством,
    которое игрок ещё не раскрыл, не подсказывает, что он подойдёт.
    """
    sim = Simulation(SEED)
    recipe = building_def(STORAGE).recipe
    sim.knowledge.learn(recipe.id)

    def option():
        return next(o for o in sim.craft_options() if o.recipe_id == recipe.id)

    expected = tuple((PROP_NAMES[prop], count) for prop, count in recipe.requires)
    assert tuple((name, need) for name, need, _have in option().requires) == expected
    assert not option().available, "пустой инвентарь — чего-то да не хватает"
    assert all(have == 0 for _name, _need, have in option().requires)

    give_materials(sim, recipe)
    assert not option().available, "свойства материалов ещё не открыты"
    assert all(have == 0 for _name, _need, have in option().requires)
    for item_id, _count in sim.cargo.items():
        sim.knowledge.reveal_all(item_id)
    assert option().available, "всё есть и всё известно — недостачи нет"
    assert all(have >= need for _name, need, have in option().requires)


def test_placing_spends_exactly_one_building_item():
    """Постановка списывает ровно один предмет-постройку и больше ничего (plan/03).

    Цена здания уже заплачена крафтом его предмета, поэтому ресурсы и предметы
    ролей при постановке не трогаются.
    """
    sim = stock(Simulation(SEED))
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    before = dict(sim.cargo.items())
    assert sim.apply(SelectHotbar(slot_of(sim.hotbar, STORAGE)))
    storage = sim.apply(PlaceBuilding(sim.selected_slot, (tx, ty)))
    assert storage is not None and sim.buildings.at(tx, ty) is storage
    before[STORAGE] -= 1
    assert dict(sim.cargo.items()) == before


def test_without_a_building_item_nothing_is_built():
    """Без предмета-постройки не построить, даже если сырьё на рецепт есть.

    Постановка не крафтит сама: «есть из чего сделать» и «есть что поставить» —
    разное, и второе решает только содержимое хотбара.
    """
    sim = Simulation(SEED)
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    give_materials(sim, building_def(STORAGE).recipe)
    sim.inventory.add("structure", STOCK_COUNT)    # предмет роли — тоже не постройка
    before = sim.state_signature()
    assert sim.build(STORAGE, tx, ty) is None, "без предмета не построить"
    assert sim.apply(PlaceBuilding(sim.selected_slot, (tx, ty))) is None, \
        "пустой слот — не команда"
    assert sim.buildings.at(tx, ty) is None
    assert sim.state_signature() == before, "отказ ничего не меняет"


def test_dismantling_drops_exactly_one_building_item():
    """Разбор роняет на тайл ровно один предмет-постройку (plan/03).

    В руки ничего не возвращается: снятое лежит там, где стояло, и
    подбирается обычным автоподбором — иначе снос стал бы бесплатным
    перемещением здания. Содержимое склада высыпается вокруг, сумма та же.
    """
    sim = Simulation(SEED)
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    storage = place(sim, STORAGE, tx, ty)
    assert storage is not None
    store = storage.container(building_def(STORAGE).accept_id)
    store.add("res0", 7)
    store.add("structure", 3)
    held = sorted(sim.cargo.items())

    assert sim.apply(DismantleBuilding((tx, ty)))
    assert sim.buildings.at(tx, ty) is None
    assert [(d.item_id, d.count) for d in sim.drops.drops_at(tx, ty)] == [(STORAGE, 1)]
    assert sim.drops.total() == {STORAGE: 1, "res0": 7, "structure": 3}, \
        "содержимое выпало целиком"
    assert sorted(sim.cargo.items()) == held, "в руки ничего не вернулось"


def test_lamp_is_an_item_too():
    """Лампа — такой же предмет-постройка (plan/03), а не бесплатный свет.

    Приёмка: скрафтил лампу — она в хотбаре, выбрал слот, ПКМ по земле —
    лампа стоит, предмет израсходован.
    """
    sim = Simulation(SEED)
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    assert sim.build(LAMP, tx, ty) is None, "без предмета лампу не поставить"
    assert sim.light.lamps == []

    recipe = building_def(LAMP).recipe
    give_materials(sim, recipe)
    assert sim.craft(recipe.id), "лампа известна с начала забега"
    assert sim.cargo.count(LAMP) == sim.hotbar.count(LAMP) == recipe.outputs
    assert sim.apply(SelectHotbar(slot_of(sim.hotbar, LAMP)))
    lamp = sim.apply(PlaceBuilding(sim.selected_slot, (tx, ty)))
    assert lamp is not None and sim.light.lamp_at(tx, ty) is lamp
    assert sim.cargo.count(LAMP) == recipe.outputs - 1, "предмет израсходован"


def test_building_out_of_range_is_refused():
    """Дальность проверяется до стройки: «далеко» и «нечего ставить» — разное."""
    sim = stock(Simulation(SEED))
    far_x = int(sim.drone.x) + int(config.BUILD_RANGE) + 3
    ty = int(sim.drone.y)
    held = sim.cargo.count(STORAGE)
    assert not sim.in_build_range(far_x, ty)
    assert not sim.can_place(STORAGE, far_x, ty)
    assert sim.build(STORAGE, far_x, ty) is None, "далеко — не строим"
    assert sim.buildings.of_kind(STORAGE) == ()
    assert sim.cargo.count(STORAGE) == held, "отказ не тратит предмет"


def test_drill_is_the_only_building_that_stands_on_a_deposit():
    """Бур ставится **на** залежь, остальное — только на свободную землю."""
    sim = stock(Simulation(SEED))
    deposit = next(d for d in sim.world.deposits if not d.depleted)
    assert sim._tile_ok_for(DRILL, deposit.x, deposit.y)
    for kind in (STORAGE, ASSEMBLER, WIRE, CONVEYOR, LAB, LAMP):
        assert not sim._tile_ok_for(kind, deposit.x, deposit.y), kind


# ------------------------------------------------------- работа от энергии
def test_drill_waits_without_a_grid():
    """Без энергии бур стоит: место выбрано, материалы потрачены, добычи нет."""
    sim = line_sim()
    dx, dy = DEPOSIT_TILE
    drill = sim.build(DRILL, dx, dy)
    deposit = sim.world.deposit_at(dx, dy)
    for _ in range(seconds(config.DRILL_TIME_PER_UNIT * 5)):
        sim.update(config.TICK)
    assert drill.mined == 0 and drill.output.total_count() == 0
    assert deposit.amount == DEPOSIT_AMOUNT, "залежь не тронута"


def test_drill_mines_the_deposit_under_it():
    """Сеть есть — бур добывает сам, без дрона (plan/03: «потом автоматика»)."""
    sim = line_sim()
    dx, dy = DEPOSIT_TILE
    drill = sim.build(DRILL, dx, dy)
    power_grid(sim, dx, dy)
    deposit = sim.world.deposit_at(dx, dy)
    for _ in range(seconds(config.DRILL_TIME_PER_UNIT * 3)):
        sim.update(config.TICK)
    assert drill.mined >= 2, "бур должен успеть выдать пару единиц"
    assert drill.output.count(deposit.resource_id) == drill.mined
    assert deposit.amount == DEPOSIT_AMOUNT - drill.mined


def test_drill_conveyor_storage_line_works():
    """Приёмка M4: бур → конвейер → склад. Предметы доезжают до склада сами."""
    sim = line_sim()
    drill, conveyor, storage, _generator = build_line(sim)
    deposit = sim.world.deposit_at(*DEPOSIT_TILE)
    belt = conveyor.container(building_def(CONVEYOR).take_id)
    store = storage.container(building_def(STORAGE).accept_id)

    for _ in range(seconds(config.DRILL_TIME_PER_UNIT * 6)):
        sim.update(config.TICK)

    assert drill.mined >= 3, "бур должен добывать"
    assert store.count(deposit.resource_id) > 0, "склад пуст — линия не работает"
    assert store.count(deposit.resource_id) + belt.count(deposit.resource_id) \
        == drill.mined, "добытое либо на лотке, либо на складе"
    assert deposit.amount == DEPOSIT_AMOUNT - drill.mined


def test_conveyor_pushes_forward_before_pulling_behind():
    """Порядок и есть линия: сначала отдать вперёд, потом взять из-за спины.

    Пока впереди место — предметы уезжают; как только впереди всё занято,
    конвейер не тянет из бура, и добыча встаёт сама.
    """
    sim = line_sim()
    dx, dy = DEPOSIT_TILE
    conveyor = sim.build(CONVEYOR, dx + 1, dy, direction=(1, 0))
    storage = sim.build(STORAGE, dx + 2, dy)
    generator = sim.build(GENERATOR, dx + 1, dy + 1)
    assert conveyor is not None and storage is not None and generator is not None
    fill_generator(generator, sim, 1)
    for _ in range(3):
        sim.update(config.TICK)                     # топка загорелась, сеть дала ток

    belt = conveyor.container(building_def(CONVEYOR).take_id)
    store = storage.container(building_def(STORAGE).accept_id)
    belt.add("res0", 1)
    for _ in range(seconds(config.CONVEYOR_MOVE_TIME)):
        sim.update(config.TICK)
    assert store.count("res0") == 1 and belt.is_empty, "лоток отдал вперёд"

    store.add("res0", store.size * store.stack_max)  # впереди всё занято
    belt.add("res0", 1)
    for _ in range(seconds(config.CONVEYOR_MOVE_TIME * 2)):
        sim.update(config.TICK)
    assert belt.count("res0") == 1, "вперёд не влезло — предмет остался на лотке"


def test_conveyor_takes_from_the_building_behind():
    """Второе правило конвейера: пустой лоток сам берёт из того, кто позади."""
    sim = line_sim()
    conveyor = sim.build(CONVEYOR, 6, 5, direction=(-1, 0))
    storage = sim.build(STORAGE, 7, 5)
    generator = sim.build(GENERATOR, 5, 5)
    assert conveyor is not None and storage is not None and generator is not None
    fill_generator(generator, sim, 1)
    for _ in range(3):
        sim.update(config.TICK)

    store = storage.container(building_def(STORAGE).accept_id)
    store.add("res0", 2)
    for _ in range(seconds(config.CONVEYOR_MOVE_TIME)):
        sim.update(config.TICK)
    belt = conveyor.container(building_def(CONVEYOR).take_id)
    assert belt.count("res0") == config.CONVEYOR_BATCH, "лоток взял из-за спины"
    assert store.count("res0") == 2 - config.CONVEYOR_BATCH


def test_rotation_turns_the_conveyor_and_nothing_else():
    sim = line_sim()
    dx, dy = DEPOSIT_TILE
    conveyor = sim.build(CONVEYOR, dx + 1, dy, direction=(1, 0))
    storage = sim.build(STORAGE, dx + 2, dy)
    before = conveyor.direction
    assert sim.rotate_at(conveyor.x, conveyor.y)
    assert conveyor.direction != before
    assert not sim.rotate_at(storage.x, storage.y), "склад не поворачивается"
    assert not sim.rotate_at(0, 0), "поворачивать нечего — и это не ошибка"


def test_conveyor_built_from_the_drone_points_away_from_it():
    """Игрок ставит линию, стоя у бура: «от себя» и есть верное направление."""
    sim = line_sim()
    dx, dy = DEPOSIT_TILE
    conveyor = sim.build(CONVEYOR, dx + 1, dy)
    assert conveyor is not None
    # Дрон стоит в центре мира (6, 6), конвейер — левее: «от дрона» значит влево.
    assert conveyor.direction == (-1, 0), "конвейер должен везти от дрона"


# ------------------------------------------------------- энергия и топливо
def test_lamp_lights_only_from_the_grid():
    """Приёмка M4: лампа питается от генератора. Без сети она гаснет."""
    sim = stock(Simulation(SEED))
    tx, ty = int(sim.drone.x) + 2, int(sim.drone.y)
    lamp = sim.build(LAMP, tx, ty)
    assert lamp is not None and not lamp.on, "лампа ставится выключенной"
    for _ in range(3):
        sim.update(config.TICK)
    assert not lamp.on, "без генератора гореть нечему"

    generator = power_grid(sim, tx, ty)          # генератор заправлен заранее
    for _ in range(3):
        sim.update(config.TICK)
    assert lamp.on, "от сети лампа обязана гореть"
    assert sim.power_summary[0] >= config.LAMP_POWER_USE

    empty(generator.container(PORT_FUEL))
    generator.burn = 0.1                          # топливо на исходе
    for _ in range(seconds(0.2)):
        sim.update(config.TICK)
    assert not lamp.on, "топливо кончилось — света нет"


def test_wire_extends_the_network_through_empty_tiles():
    """Провод — плитка сети: без него тайлы через один не связаны (plan/03)."""
    sim = stock(Simulation(SEED))
    x, y = int(sim.drone.x), int(sim.drone.y)
    lamp = sim.build(LAMP, x - 1, y - 2)
    generator = sim.build(GENERATOR, x - 1, y)
    assert lamp is not None and generator is not None, "рядом с дроном есть место"
    fill_generator(generator, sim)
    for _ in range(3):
        sim.update(config.TICK)
    assert not lamp.on, "между лампой и генератором пустой тайл"

    assert sim.build(WIRE, x - 1, y - 1) is not None
    for _ in range(3):
        sim.update(config.TICK)
    assert lamp.on, "провод обязан соединить соседей в одну сеть"


def test_generator_burns_fuel_only_while_someone_needs_it():
    """Иначе генератор выжигал бы склад впустую (plan/03)."""
    sim = stock(Simulation(SEED))
    gx, gy = int(sim.drone.x) + 1, int(sim.drone.y)
    generator = sim.build(GENERATOR, gx, gy)
    assert generator is not None
    fuel_id = fill_generator(generator, sim, 5)
    for _ in range(seconds(2.0)):
        sim.update(config.TICK)
    assert generator.burn == 0.0, "питать некого — топливо не тратится"
    assert generator.container(PORT_FUEL).count(fuel_id) == 5

    lamp = sim.build(LAMP, *free_tile(sim, LAMP, gx, gy))
    assert lamp is not None
    for _ in range(3):
        sim.update(config.TICK)
    assert lamp.on and generator.burn > 0.0, "появился потребитель — генератор топит"
    assert generator.container(PORT_FUEL).count(fuel_id) == 4, "единица топлива сгорела"


def test_fuel_runs_out_and_the_network_goes_dark():
    """🔧 GENERATOR_FUEL_TIME секунд на единицу топлива — и сеть выключается."""
    sim = stock(Simulation(SEED))
    tx, ty = int(sim.drone.x) + 2, int(sim.drone.y)
    lamp = sim.build(LAMP, tx, ty)
    generator = power_grid(sim, tx, ty, fuel=1)
    for _ in range(3):
        sim.update(config.TICK)
    assert lamp.on and generator.burn > 0.0

    for _ in range(seconds(config.GENERATOR_FUEL_TIME + 0.1)):
        sim.update(config.TICK)
    assert generator.container(PORT_FUEL).total_count() == 0
    assert not lamp.on, "топливо кончилось — лампа погасла"


def test_power_at_shows_where_the_network_goes():
    """Рендер тянет линии по этому признаку: провод и потребитель — точки сети."""
    sim = stock(Simulation(SEED))
    x, y = int(sim.drone.x), int(sim.drone.y)
    assert not sim.power_at(x - 1, y - 2), "пустая земля — не сеть"
    assert sim.build(LAMP, x - 1, y - 2) is not None
    assert sim.power_at(x - 1, y - 2), "лампа — точка сети"
    assert sim.build(WIRE, x - 1, y - 1) is not None
    assert sim.power_at(x - 1, y - 1), "провод — точка сети"
    assert sim.build(STORAGE, x, y - 1) is not None
    assert not sim.power_at(x, y - 1), "склад ничего не тратит и не проводит"


def test_power_state_goes_into_the_simulation_signature():
    """Сеть входит в состояние симуляции: одинаковые сцены не разъезжаются."""
    sim = line_sim()
    before = sim.state_signature()
    build_line(sim)
    for _ in range(seconds(1.0)):
        sim.update(config.TICK)
    assert sim.state_signature() != before
    assert sim.power_summary[1] > 0.0, "потребители сети должны быть видны HUD"


# ---------------------------------------------------------------- сборщик
def test_assembler_crafts_a_role_item_in_craft_time():
    """Сборщик тратит 🔧 `Recipe.craft_time`: руками крафт мгновенный, время — его."""
    sim = stock(Simulation(SEED))
    ax, ay = int(sim.drone.x) + 1, int(sim.drone.y)
    assembler = sim.build(ASSEMBLER, ax, ay)
    assert assembler is not None
    recipe = recipe_def("structure")               # свойство × 3 → предмет роли
    material = sim.run.items.with_prop(recipe.props[0])[0]
    sim.knowledge.learn(recipe.id)
    assert sim.cycle_recipe(assembler) == recipe.id
    source = assembler.container(building_def(ASSEMBLER).input_id)
    source.add(material.id, recipe.requires[0][1])

    power_grid(sim, ax, ay)
    for _ in range(seconds(recipe.craft_time * 2)):
        sim.update(config.TICK)
    assert assembler.made >= 1, "сборщик ничего не собрал"
    target = assembler.container(building_def(ASSEMBLER).output_id)
    assert target.count(recipe.role) > 0
    assert source.count(material.id) == 0, "материалы потрачены"
    assert assembler.mined == 0, "бур и сборщик — разные работы"


def test_assembler_waits_without_power_or_materials():
    """Без энергии и без материалов сборщик стоит и ничего не тратит."""
    sim = stock(Simulation(SEED))
    ax, ay = int(sim.drone.x) + 1, int(sim.drone.y)
    assembler = sim.build(ASSEMBLER, ax, ay)
    assert assembler is not None
    recipe = recipe_def("structure")
    material = sim.run.items.with_prop(recipe.props[0])[0]
    assembler.recipe = recipe.id
    source = assembler.container(building_def(ASSEMBLER).input_id)
    source.add(material.id, 3)
    for _ in range(seconds(recipe.craft_time * 2)):        # сети ещё нет
        sim.update(config.TICK)
    assert assembler.made == 0 and source.count(material.id) == 3

    power_grid(sim, ax, ay)
    for _ in range(seconds(recipe.craft_time * 2)):
        sim.update(config.TICK)
    assert assembler.made >= 1, "с энергией сборщик обязан собрать"


def test_cycle_recipe_walks_through_known_recipes():
    """Рецепт сборщику выбирает игрок — кнопкой в окне здания (plan/03)."""
    sim = stock(Simulation(SEED))
    ax, ay = int(sim.drone.x) + 1, int(sim.drone.y)
    assembler = sim.build(ASSEMBLER, ax, ay)
    storage = sim.build(STORAGE, *free_tile(sim, STORAGE, ax, ay))
    assert sim.cycle_recipe(assembler) is None, "ни один рецепт ещё не открыт"
    assert sim.cycle_recipe(storage) is None, "у склада рецептов нет"

    sim.knowledge.learn("structure")
    assert sim.cycle_recipe(assembler) == "structure"
    sim.knowledge.learn("energy")
    assert sim.cycle_recipe(assembler) == "energy", "следующий по порядку известных"
    assert sim.cycle_recipe(assembler) == "structure", "перебор идёт по кругу"


def test_cycle_recipe_offers_amplifier_recipes_too():
    """Усилитель («energy+») — рецепт той же роли: сборщик обязан его перебирать.

    Фильтр шёл по id рецепта, а у усилителя id с «+», и рецепт, открытый
    реликвией-усилителем, в сборщике было не выбрать. Рецепты построек
    (лампа и лаборатория известны с начала) в перебор по-прежнему не входят.
    """
    sim = stock(Simulation(SEED))
    ax, ay = int(sim.drone.x) + 1, int(sim.drone.y)
    assembler = sim.build(ASSEMBLER, ax, ay)
    assert assembler is not None
    assert sim.knowledge.is_known(LAMP) and sim.knowledge.is_known(LAB)

    sim.knowledge.learn("energy+")
    assert sim.cycle_recipe(assembler) == "energy+", "открыт только усилитель"
    sim.knowledge.learn("structure")
    seen = {sim.cycle_recipe(assembler) for _ in range(4)}
    assert seen == {"energy+", "structure"}, "по кругу — только рецепты ролей"


def test_assembler_crafts_by_an_amplifier_recipe():
    """Выбранный усилитель работает: 🔧 4 горючего → 2 энергии за его время."""
    sim = stock(Simulation(SEED))
    ax, ay = int(sim.drone.x) + 1, int(sim.drone.y)
    assembler = sim.build(ASSEMBLER, ax, ay)
    assert assembler is not None
    recipe = recipe_def("energy+")
    sim.knowledge.learn(recipe.id)
    assert sim.cycle_recipe(assembler) == recipe.id
    material = sim.run.items.with_prop(recipe.props[0])[0]
    source = assembler.container(building_def(ASSEMBLER).input_id)
    source.add(material.id, recipe.requires[0][1])
    output = assembler.container(building_def(ASSEMBLER).output_id)
    before = output.count(recipe.role)

    power_grid(sim, ax, ay)
    for _ in range(seconds(recipe.craft_time * 2)):
        sim.update(config.TICK)
    assert assembler.made >= 1, "по усилителю сборщик ничего не собрал"
    assert output.count(recipe.role) - before == recipe.outputs
    assert source.count(material.id) == 0, "материалы потрачены"


# --------------------------------------------- энергия в руках и в рецептах
def test_scan_costs_energy_items():
    """С M4 скан тратит энергию (флаг M3 «скан бесплатный» снят)."""
    sim = Simulation(SEED)
    deposit = next(d for d in sim.world.deposits if not d.depleted)
    assert sim.knowledge.unknown(deposit.resource_id), "этот ресурс ещё не изучен"
    sim.drone.x, sim.drone.y = deposit.x + 0.5, deposit.y + 0.5
    sim.set_cursor_deposit(deposit)
    sim.set_scanning(True)

    for _ in range(seconds(config.SCAN_TIME * 2)):
        sim.update(config.TICK)
    assert sim.scans_done == 0, "без энергии скан не идёт"

    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST)
    for _ in range(seconds(config.SCAN_TIME * 2)):
        sim.update(config.TICK)
    assert sim.scans_done == 1
    assert sim.inventory.count(ENERGY_ITEM) == 0, "энергия потрачена"


def test_relic_study_cost_is_a_flag_off_by_default(monkeypatch):
    """❓ plan/02: цену изучения автор ещё не выбрал. Пока флаг None — бесплатно."""
    sim = Simulation(SEED)
    relics = list(sim.relics)
    assert len(relics) >= 2, "в стартовой зоне должны быть реликвии"
    assert config.RELIC_STUDY_COST is None
    assert sim.study_relic(relics[0])

    monkeypatch.setattr(config, "RELIC_STUDY_COST", (ENERGY_ITEM, 2))
    assert not sim.study_relic(relics[1]), "нечем платить — реликвия не изучена"
    sim.inventory.add(ENERGY_ITEM, 2)
    assert sim.study_relic(relics[1])
    assert sim.inventory.count(ENERGY_ITEM) == 0, "цена списана"


def test_generator_fuel_accepts_raw_flame_and_crafted_energy():
    """Топливо — свойство предмета или id: сырьё сразу, энергия как концентрат."""
    sim = Simulation(SEED)
    definition = building_def(GENERATOR)
    raw = sim.run.items.with_prop(Prop.FLAMMABLE)[0].id
    assert _is_fuel(raw, definition.fuel, sim.run.items)
    assert _is_fuel(ENERGY_ITEM, definition.fuel, sim.run.items)
    cold = next(i for i in sim.run.items.natural if not i.has(Prop.FLAMMABLE))
    assert not _is_fuel(cold.id, definition.fuel, sim.run.items)


# ------------------------------------------------- запас для тестов (conftest)
def test_stock_tops_up_to_the_norm_and_is_safe_to_repeat():
    """`stock` доливает запас до нормы, а не насыпает ещё по стаку.

    Хелперы зовут друг друга (`power_lamp` → `power_grid` → `stock`), и
    повторный вызов не должен переполнять хранилище дрона: иначе тест падал
    бы на «не влез предмет постройки», а не на том, что он проверяет.
    """
    sim = stock(Simulation(SEED))
    norm = sorted(sim.cargo.items())
    stock(sim)
    assert sorted(sim.cargo.items()) == norm, "повторный вызов ничего не добавил"

    role = ROLES[0].id
    assert sim.cargo.remove(role, 5)
    x, y = free_tile(sim, LAMP, int(sim.drone.x), int(sim.drone.y))
    assert sim.build(LAMP, x, y) is not None
    stock(sim)
    assert sim.cargo.count(role) == STOCK_COUNT, "роль долита до нормы"
    assert sim.cargo.count(LAMP) == config.BUILDING_STACK, "постройка долита до стака"

    generator = power_grid(sim, x, y)            # внутри — ещё один `stock`
    assert generator is not None
    assert sim.cargo.count(GENERATOR) == config.BUILDING_STACK - 1
    expected = [(item, config.BUILDING_STACK - 1 if item == GENERATOR else count)
                for item, count in norm]
    assert sorted(sim.cargo.items()) == expected
