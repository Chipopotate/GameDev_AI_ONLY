"""Симуляция: добыча, время, детерминизм."""

from __future__ import annotations

import pytest

from src.game import config
from src.game.data.buildings import LAB, LAB_SAMPLES
from src.game.sim.run_gen import generate_run
from src.game.sim.simulation import BUILD_LAMP, ENERGY_ITEM, Simulation
from src.game.sim.world import Deposit
from tests.conftest import SEED, make_world, power_grid, stock

#: Ресурсы забега с сидом 0 — на крошечных тестовых картах симуляция берёт их же.
IDS = generate_run(0).items.natural_ids


def run(sim: Simulation, seconds: float) -> None:
    for _ in range(int(round(seconds / config.TICK))):
        sim.update(config.TICK)


def sim_with_deposit(offset_x: int = 1, amount: int = 5, drone_xy=(5.0, 5.0)) -> Simulation:
    """Симуляция на ровной карте с одной залежью рядом с дроном."""
    from src.game.data.terrain import Terrain

    deposit = Deposit(0, IDS[0], int(drone_xy[0]) + offset_x,
                      int(drone_xy[1]), amount)
    world = make_world([[int(Terrain.GROUND)] * 12 for _ in range(12)], [deposit])
    sim = Simulation(0, world=world)
    sim.drone.x, sim.drone.y = drone_xy
    return sim


def sim_with_two_deposits(drone_xy=(5.5, 5.5)) -> Simulation:
    """Ближняя залежь (1.0 тайла) и дальняя (1.4) — обе в радиусе добычи."""
    from src.game.data.terrain import Terrain

    deposits = [
        Deposit(0, IDS[0], 6, 5, 5),   # ближе к дрону
        Deposit(1, IDS[1], 6, 6, 5),   # дальше, но тоже достаёт
    ]
    world = make_world([[int(Terrain.GROUND)] * 12 for _ in range(12)], deposits)
    sim = Simulation(0, world=world)
    sim.drone.x, sim.drone.y = drone_xy
    return sim


def aim_and_hold(sim: Simulation, deposit_id: int) -> None:
    """Игрок навёл курсор на залежь и зажал ЛКМ."""
    sim.set_cursor_deposit(sim.world.deposits[deposit_id])
    sim.set_mining(True)


# ------------------------------------------------------------------- добыча
def test_deposit_in_range_is_reachable():
    sim = sim_with_deposit()
    assert sim.in_mine_range(sim.world.deposits[0])


def test_deposit_out_of_range_is_not_reachable():
    sim = sim_with_deposit(offset_x=6)
    assert not sim.in_mine_range(sim.world.deposits[0])


def test_mining_yields_resource_after_time():
    sim = sim_with_deposit(amount=5)
    aim_and_hold(sim, 0)
    run(sim, config.MINE_TIME_PER_UNIT * 0.5)
    assert sim.inventory.total_count() == 0, "рано: ещё не прошло время добычи"
    run(sim, config.MINE_TIME_PER_UNIT * 0.6)
    assert sim.inventory.count(IDS[0]) == 1
    assert sim.world.deposits[0].amount == 4
    assert sim.mined_total == 1


def test_mining_stops_when_button_released():
    sim = sim_with_deposit()
    aim_and_hold(sim, 0)
    run(sim, config.MINE_TIME_PER_UNIT * 2)
    mined = sim.inventory.total_count()
    sim.set_mining(False)
    run(sim, config.MINE_TIME_PER_UNIT * 3)
    assert sim.inventory.total_count() == mined
    assert sim.mine_target is None
    assert sim.mine_progress == 0.0


def test_mining_target_is_the_one_under_cursor():
    """В отличие от «ближайшей залежи»: добывается именно та, на которую смотрим."""
    sim = sim_with_two_deposits()
    assert sim.drone.distance_to_tile(6, 5) < sim.drone.distance_to_tile(6, 6)
    aim_and_hold(sim, 1)  # курсор на дальней
    run(sim, config.MINE_TIME_PER_UNIT * 1.1)
    assert sim.inventory.count(IDS[1]) == 1
    assert sim.inventory.count(IDS[0]) == 0
    assert sim.world.deposits[0].amount == 5, "ближнюю залежь трогать не должны"


def test_mining_stops_when_cursor_leaves_deposit():
    sim = sim_with_deposit()
    aim_and_hold(sim, 0)
    run(sim, config.MINE_TIME_PER_UNIT * 0.5)
    sim.set_cursor_deposit(None)  # увели курсор, кнопку держим
    run(sim, config.MINE_TIME_PER_UNIT * 3)
    assert sim.inventory.total_count() == 0
    assert sim.mine_progress == 0.0


def test_hovering_without_pressing_mines_nothing():
    """Курсор на залежи сам по себе ничего не добывает — нужна зажатая ЛКМ."""
    sim = sim_with_deposit()
    sim.set_cursor_deposit(sim.world.deposits[0])
    sim.set_mining(False)
    run(sim, config.MINE_TIME_PER_UNIT * 3)
    assert sim.inventory.total_count() == 0
    assert sim.cursor_deposit is sim.world.deposits[0]


def test_mining_empties_deposit():
    sim = sim_with_deposit(amount=3)
    aim_and_hold(sim, 0)
    run(sim, config.MINE_TIME_PER_UNIT * 5)
    assert sim.world.deposits[0].amount == 0
    assert sim.world.deposits[0].depleted
    assert sim.inventory.total_count() == 3
    assert sim.mine_target is None


def test_mining_out_of_range_does_nothing():
    sim = sim_with_deposit(offset_x=6)
    aim_and_hold(sim, 0)
    run(sim, 2.0)
    assert sim.inventory.total_count() == 0
    assert sim.mine_progress == 0.0
    assert sim.cursor_deposit is sim.world.deposits[0], "курсор остался на залежи"


def test_toast_appears_and_expires():
    sim = sim_with_deposit()
    aim_and_hold(sim, 0)
    run(sim, config.MINE_TIME_PER_UNIT * 1.1)
    # M3: первый подбор ресурса показывает ещё и одно его свойство (plan/02).
    texts = [t.text for t in sim.toasts]
    assert len(texts) == 2
    assert texts[0].startswith("+1") and "видно" in texts[1]
    sim.set_mining(False)  # иначе новые сообщения появятся во время ожидания
    run(sim, config.TOAST_TTL + 0.1)
    assert len(sim.toasts) == 0


def test_toast_list_is_bounded():
    sim = sim_with_deposit()
    for i in range(config.TOAST_MAX + 5):
        sim.toast(f"msg {i}")
    assert len(sim.toasts) == config.TOAST_MAX


# ------------------------------------------------------------------- время
def test_update_advances_time():
    sim = sim_with_deposit()
    run(sim, 1.0)
    assert sim.time == pytest.approx(1.0, abs=config.TICK)


def test_non_positive_dt_is_ignored():
    sim = sim_with_deposit()
    before = sim.state_signature()
    sim.update(0.0)
    sim.update(-1.0)
    assert sim.state_signature() == before


def test_movement_moves_drone():
    sim = sim_with_deposit(drone_xy=(5.0, 5.0))
    sim.set_move_input(1.0, 0.0)
    run(sim, 0.5)
    assert sim.drone.x == pytest.approx(5.0 + config.DRONE_SPEED * 0.5, abs=1e-6)


def test_tiles_lit_ratio_starts_at_zero(sim):
    """Ламп нет — освещать нечего."""
    assert sim.tiles_lit_ratio == 0.0


def test_darkness_is_zero_in_the_day(sim):
    assert sim.darkness == 0.0


# --------------------------------------------------------------- сутки в игре
def test_simulation_switches_to_night(sim):
    first_day = config.TIME_TABLE[config.PLANET][0]
    run(sim, first_day + 0.5)
    assert sim.time_cycle.is_night
    assert sim.darkness > 0.0


def test_dusk_and_dawn_are_reported_to_the_player(sim):
    """Смена фазы видна игроком: иначе ночь наступила бы незаметно."""
    first_day, _day, night = config.TIME_TABLE[config.PLANET]
    run(sim, first_day + 0.1)
    assert [t.text for t in sim.toasts] == ["Ночь 1"]
    run(sim, night)  # до рассвета (сообщение о закате к этому моменту уже истекло)
    assert sim.time_cycle.day_number == 2
    assert [t.text for t in sim.toasts] == ["День 2"]


# ------------------------------------------------------------- детерминизм
def test_same_seed_same_state():
    a, b = Simulation(SEED), Simulation(SEED)
    for s in (a, b):
        s.set_move_input(1.0, 0.3)
        run(s, 2.0)
    assert a.state_signature() == b.state_signature()


def test_different_seed_different_state():
    a, b = Simulation(SEED), Simulation(SEED + 7)
    assert a.state_signature() != b.state_signature()


def test_state_signature_reacts_to_movement():
    sim = Simulation(SEED)
    before = sim.state_signature()
    sim.set_move_input(1.0, 0.0)
    run(sim, 0.5)
    assert sim.state_signature() != before


# -------------------------------------------------- скан, реликвии, крафт (M3)
def test_scan_reveals_one_prop_without_mining():
    sim = sim_with_deposit(amount=5)
    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST)   # с M4 скан платный
    item_id = IDS[0]
    before = sim.knowledge.unknown(item_id)
    assert before, "у ресурса должно быть скрытое свойство"
    sim.set_cursor_deposit(sim.world.deposits[0])
    sim.set_scanning(True)
    run(sim, config.SCAN_TIME * 1.1)
    assert sim.scans_done == 1
    assert len(sim.knowledge.unknown(item_id)) == len(before) - config.SCAN_REVEALS
    assert sim.inventory.count(ENERGY_ITEM) == 0, "скан тратит энергию (M4)"
    assert sim.inventory.total_count() == 0, "скан не добывает ресурс"
    assert sim.world.deposits[0].amount == 5, "залежь не тратится"


def test_scan_out_of_range_does_nothing():
    sim = sim_with_deposit(offset_x=6)
    sim.set_cursor_deposit(sim.world.deposits[0])
    sim.set_scanning(True)
    run(sim, config.SCAN_TIME * 3)
    assert sim.scans_done == 0
    assert sim.scan_progress == 0.0


def test_released_right_button_stops_scanning():
    sim = sim_with_deposit()
    sim.set_cursor_deposit(sim.world.deposits[0])
    sim.set_scanning(True)
    run(sim, config.SCAN_TIME * 0.5)
    sim.set_scanning(False)
    run(sim, config.SCAN_TIME * 3)
    assert sim.scan_target is None and sim.scan_progress == 0.0
    assert sim.scans_done == 0


def relic_sim(amplified: bool = False):
    """Симуляция с дроном, стоящим на реликвии, и курсором на ней."""
    sim = Simulation(SEED)
    relic = next(r for r in sim.relics if r.amplified == amplified)
    sim.drone.x, sim.drone.y = relic.x + 0.5, relic.y + 0.5
    sim.set_cursor_tile(relic.x, relic.y)
    return sim, relic


def test_studying_a_relic_opens_its_recipe():
    sim, relic = relic_sim()
    assert not sim.knowledge.is_known(relic.recipe_id)
    sim.set_mining(True)
    run(sim, config.RELIC_STUDY_TIME * 1.1)
    assert relic.studied
    assert sim.knowledge.is_known(relic.recipe_id)
    assert any("открыт" in t.text for t in sim.toasts)


def test_amplifier_relic_opens_amplified_recipe():
    sim, relic = relic_sim(amplified=True)
    assert relic.recipe_id.endswith("+")
    sim.set_mining(True)
    run(sim, config.RELIC_STUDY_TIME * 1.1)
    assert sim.knowledge.is_known(relic.recipe_id)


def test_relic_too_far_is_not_studied():
    sim, relic = relic_sim()
    sim.drone.x, sim.drone.y = relic.x + 20.0, relic.y
    sim.set_mining(True)
    run(sim, config.RELIC_STUDY_TIME * 2)
    assert not relic.studied
    assert sim.study_progress == 0.0


def test_releasing_the_button_resets_study_progress():
    sim, relic = relic_sim()
    sim.set_mining(True)
    run(sim, config.RELIC_STUDY_TIME * 0.5)
    assert 0.0 < sim.study_progress < 1.0
    sim.set_mining(False)
    run(sim, config.TICK)
    assert sim.study_target is None and sim.study_progress == 0.0
    assert not relic.studied


def test_unknown_relic_does_not_stand_on_a_deposit():
    """Реликвия важнее добычи — правило работает, только если тайлы не совпадают."""
    sim = Simulation(SEED)
    for relic in sim.relics:
        assert sim.world.deposit_at(relic.x, relic.y) is None


def test_craft_consumes_materials_and_produces_role_item():
    from src.game.data.resources import Prop

    sim = Simulation(SEED)
    assert not sim.craft("energy"), "рецепт ещё не открыт"
    sim.knowledge.learn("energy")
    proto = sim.run.items.with_prop(Prop.FLAMMABLE)[0]
    sim.inventory.add(proto.id, 10)
    assert sim.craft("energy")
    assert sim.inventory.count("energy") == 1
    assert sim.inventory.count(proto.id) == 7, "рецепт 🔥×3 → 1"
    assert sim.crafted_total == 1
    assert not sim.craft("energy+"), "усилитель ещё не открыт"


def test_lab_window_opens_only_next_to_the_lab():
    """ПКМ по зданию открывает его контейнеры, только если дрон рядом."""
    sim = stock(Simulation(SEED))
    sim.select_build(LAB)
    x, y = int(sim.drone.x) + 1, int(sim.drone.y)
    assert sim.click_at(x, y) == LAB
    lab = sim.buildings.at(x, y)
    assert sim.building_reachable(lab)
    assert not sim.building_reachable(None), "окно без здания не открыть"
    # Отошли далеко — контейнеры здания больше не открыть.
    sim.drone.x, sim.drone.y = x + config.CONTAINER_RANGE + 3, y + 0.5
    assert not sim.building_reachable(lab)
    # Снесли здание — окно закрывать нечему.
    sim.drone.x, sim.drone.y = x + 0.5, y + 0.5
    assert sim.click_at(x, y) == "building_removed"
    assert not sim.building_reachable(lab)


def test_thrown_item_lands_ahead_and_can_be_picked_up():
    sim = Simulation(SEED)
    item = sim.run.items.natural[0]
    sim.inventory.add(item.id, 4)
    assert sim.throw_item(0)
    assert sim.inventory.count(item.id) == 0
    assert len(sim.drops) == 1
    # Падает дальше, чем подбирается: стоящий дрон не подбирает свой же стак.
    drop = sim.drops.items[0]
    assert drop.distance_to(sim.drone.x, sim.drone.y) > config.PICKUP_RADIUS
    for _ in range(int((config.DROP_PICKUP_DELAY + config.TICK) / config.TICK)):
        sim.update(config.TICK)
    assert len(sim.drops) == 1 and sim.inventory.count(item.id) == 0, "дрон стоит"
    # Подошли к предмету — он сам уходит в инвентарь.
    sim.drone.x, sim.drone.y = drop.x, drop.y
    sim.update(config.TICK)
    assert sim.inventory.count(item.id) == 4 and len(sim.drops) == 0


def test_throwing_a_part_of_the_stack_and_an_empty_slot():
    sim = Simulation(SEED)
    item = sim.run.items.natural[0]
    sim.inventory.add(item.id, 5)
    assert not sim.throw_item(1), "слот пуст"
    assert sim.throw_item(0, 2)
    assert sim.inventory.count(item.id) == 3
    assert sim.drops.items[0].count == 2
    assert sim.throw_item(0, 0) is False, "ноль единиц — не выброс"


def test_mining_stops_when_the_inventory_is_full():
    """Бездонного инвентаря больше нет: полный дрон добычу останавливает."""
    from src.game import config as cfg

    sim = Simulation(SEED)
    deposit = sim.world.deposits[0]
    sim.drone.x, sim.drone.y = deposit.x + 0.5, deposit.y + 0.5
    sim.set_cursor_tile(deposit.x, deposit.y)
    sim.set_mining(True)
    # Забиваем инвентарь под завязку тем же ресурсом.
    room = sim.inventory.size * sim.inventory.stack_max
    sim.inventory.clear()
    sim.inventory.add(deposit.resource_id, room)
    assert sim.inventory.can_accept(deposit.resource_id) is False
    run(sim, cfg.MINE_TIME_PER_UNIT * 2)
    assert sim.mined_total == 0, "места нет — добыча стоит"
    assert any("полон" in t.text for t in sim.toasts)
    # Освободили слот — добыча пошла.
    sim.inventory.take_slot(0)
    run(sim, cfg.MINE_TIME_PER_UNIT + cfg.TICK)
    assert sim.mined_total == cfg.MINE_YIELD


def test_craft_needs_room_for_the_result():
    from src.game.data.resources import Prop

    sim = Simulation(SEED)
    sim.knowledge.learn("energy")
    proto = sim.run.items.with_prop(Prop.FLAMMABLE)[0]
    filler = next(i.id for i in sim.run.items.natural if i.id != proto.id)
    full = sim.inventory.size * sim.inventory.stack_max

    def fill(proto_units: int) -> None:
        """Материал в первом слоте, остальное забито чужим предметом."""
        sim.inventory.clear()
        sim.inventory.add(proto.id, proto_units)
        sim.inventory.add(filler, full - proto_units)

    fill(10)
    assert sim.inventory.is_full
    assert not sim.craft("energy"), "результат некуда положить"
    assert any("места" in t.text for t in sim.toasts)
    assert sim.crafted_total == 0
    assert sim.inventory.count(proto.id) == 10, "материалы не тронуты"

    # Материалы освобождают слот целиком: тогда результат влезает.
    fill(3)
    assert sim.craft("energy")
    assert sim.inventory.count("energy") == 1


def test_lab_cannot_be_placed_far_or_on_a_deposit():
    sim = stock(Simulation(SEED))
    deposit = sim.world.deposits[0]
    sim.drone.x, sim.drone.y = deposit.x + 0.5, deposit.y + 0.5
    assert sim.build(LAB, deposit.x, deposit.y) is None, "залежь — только под бур"
    far = int(sim.drone.x) + int(config.BUILD_RANGE) + 3
    assert sim.build(LAB, far, deposit.y) is None, "далеко"
    assert sim.buildings.at(deposit.x, deposit.y) is None


def test_click_removes_a_building():
    sim = stock(Simulation(SEED))
    sim.select_build(LAB)
    x, y = int(sim.drone.x) + 1, int(sim.drone.y)
    assert sim.click_at(x, y) == LAB
    assert sim.buildings.at(x, y) is not None
    assert sim.click_at(x, y) == "building_removed"
    assert sim.buildings.at(x, y) is None


def test_select_build_rejects_unknown_kind():
    sim = Simulation(SEED)
    assert sim.select_build("нет такого") is False
    assert sim.selected_build == BUILD_LAMP


# ------------------------------------------------------------- снос зданий
def placed_lab(sim: Simulation):
    """Ставит лабораторию рядом с дроном и возвращает её (цена уже оплачена)."""
    stock(sim)
    sim.select_build(LAB)
    x, y = int(sim.drone.x) + 1, int(sim.drone.y)
    assert sim.click_at(x, y) == LAB
    return sim.buildings.at(x, y)


def building_total(sim: Simulation) -> int:
    """Сколько всего лежит в контейнерах всех зданий."""
    return sum(c.total_count() for b in sim.buildings.buildings
               for c in b.containers.values())


def test_demolition_drops_everything_on_the_ground():
    """Закон сохранения: сколько было в лаборатории, столько лежит на земле."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    samples = lab.container(LAB_SAMPLES)
    first, second = sim.run.items.natural[0].id, sim.run.items.natural[1].id
    samples.add(first, 5)
    samples.add(second, 3)
    before = samples.total_count()
    assert before == 8

    assert sim.demolish(lab)
    assert sim.buildings.at(lab.x, lab.y) is None, "здание снесено"
    assert building_total(sim) == 0, "контейнеры опустели"
    dropped = sim.drops.total()
    assert dropped.get(first, 0) + dropped.get(second, 0) == before
    assert dropped.get(first, 0) == 5 and dropped.get(second, 0) == 3


def test_demolition_lays_items_on_the_free_tiles_around():
    """Предметы раскладываются по кольцу вокруг здания, а не кучей на нём."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    samples = lab.container(LAB_SAMPLES)
    for item in sim.run.items.natural[:2]:
        samples.add(item.id, 1)
    assert sim.demolish(lab)
    tiles = set(sim.drops.tiles)
    assert len(tiles) == 2, "два стака — два тайла"
    assert (lab.x, lab.y) not in tiles, "тайл здания оставлен на крайний случай"
    for x, y in tiles:
        assert max(abs(x - lab.x), abs(y - lab.y)) == 1, "ближайшее кольцо"


def test_demolition_uses_the_nearest_free_tile_first():
    """Порядок детерминирован: кольцо, затем y, затем x."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    tiles = sim.scatter_tiles(lab.x, lab.y)
    assert tiles[0] == (lab.x - 1, lab.y - 1), "первым идёт верхний левый тайл кольца"
    assert (lab.x, lab.y) not in tiles, "свой тайл в список не входит"
    assert len(tiles) == len(set(tiles)), "тайлы не повторяются"
    outer = [max(abs(tx - lab.x), abs(ty - lab.y)) for tx, ty in tiles]
    assert outer == sorted(outer), "тайлы идут по расширяющимся кольцам"
    ring = [(tx, ty) for tx, ty in tiles
            if max(abs(tx - lab.x), abs(ty - lab.y)) == 1]
    assert ring == sorted(ring, key=lambda t: (t[1], t[0])), \
        "внутри кольца порядок по y, потом по x"


def test_scatter_skips_water_and_other_buildings():
    """Свободный тайл — земля без здания: в чужую постройку не кладём."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    other = sim.buildings.add(LAB, lab.x - 1, lab.y - 1)
    free = set(sim.scatter_tiles(lab.x, lab.y))
    assert (other.x, other.y) not in free
    assert all(sim.world.is_walkable_tile(x, y) for x, y in free)


def test_demolition_falls_back_to_the_building_tile(monkeypatch):
    """Свободных тайлов нет — вещи ложатся на тайл здания: важнее сохранить."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    item = sim.run.items.natural[0].id
    lab.container(LAB_SAMPLES).add(item, 4)
    # Занято всё вокруг: и земля, и вода, и постройки — свободных тайлов нет.
    monkeypatch.setattr(sim, "_drop_tile_is_free", lambda x, y: False)
    assert sim.scatter_tiles(lab.x, lab.y) == []
    assert sim.demolish(lab)
    assert sim.drops.total().get(item, 0) == 4, "ничего не потерялось"
    assert (lab.x, lab.y) in sim.drops.tiles, "лежит на тайле здания"


def test_demolition_drops_stacks_as_separate_piles():
    """Стак больше лимита делится на кучи, но сумма остаётся прежней."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    item = sim.run.items.natural[0].id
    limit = config.SLOT_STACK_MAX
    lab.container(LAB_SAMPLES).add(item, limit + 7)
    assert sim.demolish(lab)
    assert sim.drops.total()[item] == limit + 7
    assert len(sim.drops) == 2, "две кучи по слотам, каждая в пределах лимита"
    assert sorted(d.count for d in sim.drops.items) == [7, limit]


def test_demolition_keeps_a_half_analyzed_sample():
    """Снос возвращает образец целиком: анализ просто не состоялся."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    power_grid(sim, lab.x, lab.y)          # без сети анализ не идёт (M4)
    item = next(i for i in sim.run.items.natural if sim.knowledge.unknown(i.id))
    lab.container(LAB_SAMPLES).add(item.id, 1)
    run(sim, config.LAB_ANALYZE_TIME * 0.5 + 2 * config.TICK)
    assert lab.progress > 0.0
    assert sim.demolish(lab)
    assert sim.drops.total().get(item.id, 0) == 1, "образец не потрачен"
    assert sim.knowledge.unknown(item.id), "свойств лаборатория не открыла"


def test_demolish_by_hand_and_by_click_are_the_same_thing():
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    item = sim.run.items.natural[0].id
    lab.container(LAB_SAMPLES).add(item, 2)
    assert sim.remove_building_at(lab.x, lab.y)
    assert sim.drops.total().get(item, 0) == 2
    assert not sim.remove_building_at(lab.x, lab.y), "сносить больше нечего"


def test_demolished_items_can_be_picked_up_again():
    """Критерий приёмки: снести лабораторию с образцами и подобрать их обратно."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    item = sim.run.items.natural[0].id
    lab.container(LAB_SAMPLES).add(item, 3)
    assert sim.demolish(lab)
    drop = sim.drops.items[0]
    sim.drone.x, sim.drone.y = drop.center      # дрон подошёл к куче
    run(sim, config.TICK)
    assert sim.inventory.count(item) == 3, "дрон подобрал свой образец"
    assert len(sim.drops) == 0


def test_demolition_does_not_refund_the_build_cost():
    """Здания до M4 бесплатны: возвращать нечего (решение за экономикой, M4)."""
    sim = Simulation(SEED)
    lab = placed_lab(sim)
    before = sim.inventory.total_count()
    assert sim.demolish(lab)
    assert sim.inventory.total_count() == before
    assert sim.drops.total() == {}, "пустое здание ничего не роняет"


def test_prop_slots_show_unknown_as_none():
    """Интерфейс рисует по слотам `?` — столько же, сколько свойств у ресурса."""
    sim = Simulation(SEED)
    item = next(i for i in sim.run.items.natural if len(i.props) > 1)
    slots = sim.prop_slots(item.id)
    assert len(slots) == len(item.props)
    assert all(s is None for s in slots), "пока ничего не открыто"
    sim.knowledge.reveal_all(item.id)
    assert len(sim.prop_slots(item.id)) == len(item.props)
    assert set(sim.prop_slots(item.id)) == set(item.props)
    assert sim.props_known[0] >= len(item.props)
    assert sim.props_known[1] == sum(len(i.props) for i in sim.run.items.natural)


def test_all_relics_are_placed_inside_or_outside_the_start_zone():
    """Базовые рецепты открываются внутри стартовой зоны, усилители — снаружи."""
    sim = Simulation(SEED)
    base = [r for r in sim.relics if not r.amplified]
    amps = [r for r in sim.relics if r.amplified]
    assert base, "базовые реликвии обязаны быть размещены"
    assert all(sim.world.in_start_zone(r.x, r.y) for r in base)
    assert all(not sim.world.in_start_zone(r.x, r.y) for r in amps)
