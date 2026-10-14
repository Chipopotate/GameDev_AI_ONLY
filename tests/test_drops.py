"""Предметы на земле: падение, слияние, подбор (M3.5, plan/03)."""

from __future__ import annotations

from src.game import config
from src.game.sim.drops import DropSystem
from src.game.sim.resources import Inventory


def make_inv(stack_max: int = 10, slots: int = 2) -> Inventory:
    return Inventory(slots=slots, stack_max=stack_max)


def tick(system: DropSystem, inv: Inventory, seconds: float, x: float = 0.0,
         y: float = 0.0) -> list:
    """Прогоняет время фиксированными шагами, как симуляция."""
    picked = []
    steps = int(round(seconds / config.TICK))
    for _ in range(steps):
        picked.extend(system.update(config.TICK, inv, x, y))
    return picked


def test_spawn_puts_an_item_on_the_ground():
    drops = DropSystem()
    drop = drops.spawn("a", 3, 5.0, 6.0)
    assert drop is not None and len(drops) == 1
    assert (drop.item_id, drop.count) == ("a", 3)
    assert drop.tile == (5, 6) and drop.center == (5.5, 6.5)
    assert drops.at(5, 6) is drop
    assert drops.at(0, 0) is None


def test_nothing_is_dropped_for_an_empty_stack():
    drops = DropSystem()
    assert drops.spawn("a", 0, 0.0, 0.0) is None
    assert drops.spawn("a", -2, 0.0, 0.0) is None
    assert len(drops) == 0


# ------------------------------------------------------------------- хранение
def test_items_live_on_tiles():
    """Хранилище — «тайл → стеки»: подбор и отрисовка смотрят только рядом."""
    drops = DropSystem()
    drops.spawn_tile("a", 1, 3, 4)
    drops.spawn_tile("b", 1, 3, 4)
    assert set(drops.tiles) == {(3, 4)}
    assert [d.item_id for d in drops.drops_at(3, 4)] == ["a", "b"]
    assert drops.drops_at(4, 3) == ()


def test_same_resource_merges_into_one_stack():
    """Стеки одного ресурса на тайле сливаются до лимита, а не лежат по одному."""
    drops = DropSystem()
    drops.spawn_tile("a", 3, 1, 1)
    drops.spawn_tile("a", 4, 1, 1)
    assert len(drops) == 1
    assert drops.at(1, 1).count == 7


def test_merging_respects_the_stack_limit():
    drops = DropSystem()
    limit = config.SLOT_STACK_MAX
    drops.spawn_tile("a", limit + 5, 1, 1)
    assert len(drops) == 2
    assert sorted(d.count for d in drops.drops_at(1, 1)) == [5, limit]


def test_total_counts_every_stack():
    drops = DropSystem()
    drops.spawn_tile("a", 3, 1, 1)
    drops.spawn_tile("b", 2, 1, 1)
    drops.spawn_tile("a", 4, 2, 2)
    assert drops.total() == {"a": 7, "b": 2}


def test_empty_tiles_are_forgotten():
    """Пустые тайлы не хранятся: иначе словарь рос бы на каждом шаге подбора."""
    drops = DropSystem()
    inv = make_inv()
    drops.spawn_tile("a", 2, 0, 0)
    tick(drops, inv, config.TICK)
    assert drops.tiles == {} and len(drops) == 0


# --------------------------------------------------------------------- подбор
def test_item_is_not_picked_up_instantly():
    """Иначе стоящий на месте дрон подобрал бы свой же выброшенный стак."""
    drops = DropSystem()
    inv = make_inv()
    drops.spawn("a", 2, 0.5, 0.0)
    assert tick(drops, inv, config.DROP_PICKUP_DELAY * 0.5) == []
    assert inv.count("a") == 0
    assert tick(drops, inv, config.DROP_PICKUP_DELAY + config.TICK)
    assert inv.count("a") == 2 and len(drops) == 0


def test_item_far_away_stays_on_the_ground():
    drops = DropSystem()
    inv = make_inv()
    drops.spawn("a", 2, config.PICKUP_RADIUS * 3, 0.0, delay=0.0)
    assert tick(drops, inv, 1.0, x=0.0, y=0.0) == []
    assert len(drops) == 1 and inv.count("a") == 0
    far = drops.items[0]
    picked = tick(drops, inv, config.TICK, x=far.center[0], y=far.center[1])
    assert [(p.item_id, p.count) for p in picked] == [("a", 2)]
    assert inv.count("a") == 2


def test_partial_pickup_when_the_inventory_is_almost_full():
    """Подбирается столько, сколько влезет; остаток остаётся лежать."""
    drops = DropSystem()
    inv = make_inv(stack_max=4, slots=1)
    drops.spawn("a", 10, 0.0, 0.0, delay=0.0)
    picked = tick(drops, inv, config.TICK)
    assert [(p.item_id, p.count, p.left) for p in picked] == [("a", 4, 6)]
    assert inv.count("a") == 4
    assert len(drops) == 1 and drops.items[0].count == 6, "остаток лежит дальше"


def test_full_inventory_picks_up_nothing():
    drops = DropSystem()
    inv = make_inv(stack_max=2, slots=1)
    inv.add("b", 2)
    drops.spawn("a", 5, 0.0, 0.0, delay=0.0)
    assert tick(drops, inv, config.TICK) == []
    assert inv.count("b") == 2 and len(drops) == 1


def test_pickup_is_reported_once_per_item():
    """За шаг игроку нужно одно сообщение на предмет, а не по одному на кучу."""
    drops = DropSystem()
    inv = make_inv()
    drops.spawn_tile("a", 1, 0, 0)
    drops.spawn_tile("a", 1, 1, 0)
    picked = tick(drops, inv, config.TICK, x=0.5, y=0.5)
    assert len(picked) == 1
    assert (picked[0].item_id, picked[0].count) == ("a", 2)


def test_pickup_order_is_deterministic():
    """Обход тайлов и стеков — по порядку появления: подбор воспроизводим."""
    def picked_items() -> list:
        drops = DropSystem()
        inv = make_inv(stack_max=1, slots=2)
        drops.spawn_tile("a", 1, 0, 0)
        drops.spawn_tile("b", 1, 0, 0)
        return tick(drops, inv, config.TICK)

    assert [p.item_id for p in picked_items()] == ["a", "b"]


# ------------------------------------------------------- время жизни предмета
def test_items_do_not_disappear_by_themselves():
    """🔧 DROP_DESPAWN_SECONDS = None: вещи игрока лежат, пока их не подберут."""
    assert config.DROP_DESPAWN_SECONDS is None, "таймер выключен в config.py"
    drops = DropSystem()
    inv = make_inv()
    drop = drops.spawn("a", 1, 50.0, 50.0, delay=0.0)
    tick(drops, inv, 600.0, x=0.0, y=0.0)
    assert len(drops) == 1 and not drop.expiring
    assert drop.alive_ratio == 1.0


def test_despawn_timer_still_works_when_switched_on(monkeypatch):
    """Таймер остался в коде: на M5 он понадобится мусору от врагов."""
    monkeypatch.setattr(config, "DROP_DESPAWN_SECONDS", 3.0)
    drops = DropSystem()
    inv = make_inv()
    drops.spawn("a", 1, 50.0, 50.0, delay=0.0)
    tick(drops, inv, 3.0 - config.TICK, x=0.0, y=0.0)
    assert len(drops) == 1, "предмет ещё жив"
    tick(drops, inv, config.TICK * 2, x=0.0, y=0.0)
    assert len(drops) == 0, "предмет исчез"


def test_expiring_items_blink_before_vanishing(monkeypatch):
    monkeypatch.setattr(config, "DROP_DESPAWN_SECONDS", 30.0)
    drops = DropSystem()
    drop = drops.spawn("a", 1, 0.0, 0.0)
    assert not drop.expiring
    drop.age = 30.0 - config.DROP_BLINK_TIME * 0.5
    assert drop.expiring, "последние секунды предмет мигает"
    assert 0.0 < drop.alive_ratio < 0.2


# ------------------------------------------------------------------- прочее
def test_update_ignores_zero_dt():
    drops = DropSystem()
    inv = make_inv()
    drops.spawn("a", 1, 0.0, 0.0, delay=0.0)
    assert drops.update(0.0, inv, 0.0, 0.0) == ()
    assert inv.count("a") == 0


def test_pickup_never_loses_a_unit():
    """Сколько лежало — столько и перешло: ничего не создаётся и не пропадает."""
    drops = DropSystem()
    inv = make_inv(stack_max=7, slots=3)
    drops.spawn_tile("a", 30, 0, 0)
    before = drops.total()["a"]
    picked = tick(drops, inv, config.TICK)
    assert inv.count("a") + drops.total().get("a", 0) == before
    assert sum(p.count for p in picked) == inv.count("a")


def test_many_drops_stay_cheap():
    """200 куч на карте: подбор смотрит только на тайлы рядом с дроном."""
    drops = DropSystem()
    inv = make_inv()
    for i in range(200):
        drops.spawn_tile("a", 1, i % 20, i // 20)
    assert len(drops) == 200
    far = tick(drops, inv, config.TICK, x=90.0, y=90.0)
    assert far == [] and len(drops) == 200, "дальние кучи не подбираются"
    near = tick(drops, inv, config.TICK, x=0.5, y=0.5)
    assert len(near) == 1, "подбирается только то, что рядом"


def test_state_snapshot_changes_with_contents():
    drops = DropSystem()
    before = drops.state()
    drops.spawn("a", 1, 1.0, 2.0)
    assert drops.state() != before
