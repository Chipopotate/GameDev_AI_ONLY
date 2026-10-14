"""Экономика M4 (plan/02, plan/03): сеть, цены, бур, конвейер, склад, сборщик.

Приёмка этапа: «бур → конвейер → склад работает, лампа питается от генератора,
ресурсы тратятся по рецептам ролей». Каждый пункт проверяется там, где он живёт:

- чистая сеть — `sim/power.py`, узлами и без мира;
- цены, дальность и место — `Simulation.build` с настоящим инвентарём;
- работа зданий — через `sim.update`, то есть так, как это увидит игрок;
- данные зданий — на то, что цена ссылается на существующие предметы ролей.

Сцена логистики — плоский мир с одной залежью (`make_world`): на настоящей карте
залежь под бур ещё надо найти, а тест должен падать от поломки логики, а не от
неудачного сида.
"""

from __future__ import annotations

import pytest

from src.game import config
from src.game.data.buildings import (ASSEMBLER, BUILD_ORDER, CONVEYOR, DRILL,
                                     GENERATOR, LAB, LAMP, PORT_FUEL, PORT_IN,
                                     PORT_OUT, PORT_STORE, STORAGE, WIRE,
                                     WORK_CRAFT, building as building_def)
from src.game.data.recipes import ROLES, recipe as recipe_def
from src.game.data.resources import Prop
from src.game.data.terrain import Terrain
from src.game.sim.buildings import DIRECTIONS, _is_fuel
from src.game.sim.power import PowerNode, PowerSystem
from src.game.sim.simulation import ENERGY_ITEM, Simulation
from src.game.sim.world import Deposit
from tests.conftest import SEED, make_world, power_grid, stock

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


def build_line(sim: Simulation) -> tuple:
    """Бур на залежи → конвейер → склад плюс заправленный генератор под сеть.

    Направление конвейера задаётся явно: `_build_direction` считает его от
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
def test_every_price_and_port_exists_in_the_data():
    """Цены ссылаются на предметы ролей, порты — на известные роли контейнеров."""
    sim = Simulation(SEED)
    ports = {PORT_IN, PORT_OUT, PORT_STORE, PORT_FUEL}
    roles = {role.id for role in ROLES}
    for kind in BUILD_ORDER:
        definition = building_def(kind)
        for item_id, count in definition.cost:
            assert item_id in roles, f"{kind}: цена {item_id} не из ролей"
            assert item_id in sim.run.items, f"{kind}: цены {item_id} нет в забеге"
            assert count >= 1, f"{kind}: цена {item_id} без количества"
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


# -------------------------------------------------------------------- цены
def test_building_costs_role_items_and_spends_exactly_them():
    """Стройка списывает цену целиком и ровно её (plan/03)."""
    sim = Simulation(SEED)
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    cost = sim.build_cost(STORAGE)
    assert cost, "у склада должна быть цена"
    assert not sim.can_afford(STORAGE)
    assert sim.build(STORAGE, tx, ty) is None, "без материалов не построить"
    assert sim.buildings.at(tx, ty) is None

    for item_id, count in cost:
        sim.inventory.add(item_id, count)
    before = {item_id: sim.inventory.count(item_id) for item_id, _c in cost}
    assert sim.build(STORAGE, tx, ty) is not None
    for item_id, count in cost:
        assert sim.inventory.count(item_id) == before[item_id] - count


def test_missing_cost_names_what_is_lacking():
    """Игрок должен узнать, чего не хватает, а не просто «нельзя»."""
    sim = Simulation(SEED)
    missing = sim.missing_cost(STORAGE)
    assert missing, "пустой инвентарь — чего-то да не хватает"
    for item_id, _count in sim.build_cost(STORAGE):
        assert sim.run.items[item_id].name in missing
    stock(sim)
    assert sim.missing_cost(STORAGE) == "", "всё есть — недостачи нет"


def test_demolition_returns_nothing():
    """Возврата цены нет (plan/03): иначе снос стал бы перемещением здания."""
    sim = Simulation(SEED)
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    sim.inventory.add("structure", sim.build_cost(STORAGE)[0][1])
    assert sim.build(STORAGE, tx, ty) is not None
    spent = sim.inventory.count("structure")
    assert sim.remove_building_at(tx, ty)
    assert sim.inventory.count("structure") == spent, "снос ничего не вернул"


def test_lamp_costs_materials_too():
    """Лампа — такое же здание с ценой (plan/03), а не бесплатный свет."""
    sim = Simulation(SEED)
    tx, ty = int(sim.drone.x) + 1, int(sim.drone.y)
    assert sim.place_lamp(tx, ty) is None
    assert sim.light.lamps == []
    stock(sim)
    assert sim.place_lamp(tx, ty) is not None


def test_building_out_of_range_is_refused():
    """Дальность проверяется до стройки: «далеко» и «нечем платить» — разное."""
    sim = stock(Simulation(SEED))
    far_x = int(sim.drone.x) + int(config.BUILD_RANGE) + 3
    ty = int(sim.drone.y)
    assert not sim.in_build_range(far_x, ty)
    assert sim.build(STORAGE, far_x, ty) is None, "далеко — не строим"
    assert sim.buildings.of_kind(STORAGE) == ()


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
    lamp = sim.place_lamp(tx, ty)
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
    lamp = sim.place_lamp(x - 1, y - 2)
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

    lamp = sim.place_lamp(*free_tile(sim, LAMP, gx, gy))
    assert lamp is not None
    for _ in range(3):
        sim.update(config.TICK)
    assert lamp.on and generator.burn > 0.0, "появился потребитель — генератор топит"
    assert generator.container(PORT_FUEL).count(fuel_id) == 4, "единица топлива сгорела"


def test_fuel_runs_out_and_the_network_goes_dark():
    """🔧 GENERATOR_FUEL_TIME секунд на единицу топлива — и сеть выключается."""
    sim = stock(Simulation(SEED))
    tx, ty = int(sim.drone.x) + 2, int(sim.drone.y)
    lamp = sim.place_lamp(tx, ty)
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
    assert sim.place_lamp(x - 1, y - 2) is not None
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
    """Сборщик тратит 🔧 `Recipe.craft_time` — время, не занятое книгой рецептов."""
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
