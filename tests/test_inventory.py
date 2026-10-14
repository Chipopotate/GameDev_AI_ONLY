"""Инвентарь и контейнеры: слоты, стаки, перекладывание (M3.5)."""

from __future__ import annotations

import pytest

from src.game import config
from src.game.sim.resources import Container, Inventory, Stack, give


def test_add_and_count():
    """`add` возвращает остаток, который не влез (а не новое количество)."""
    inv = Inventory()
    assert inv.count("a") == 0
    assert inv.add("a", 3) == 0
    assert inv.add("a", 2) == 0
    assert inv.count("a") == 5
    assert inv.total_count() == 5
    assert inv.used_slots == 1, "пять единиц лежат одним стаком"


def test_items_keep_insertion_order():
    inv = Inventory()
    for rid in ("c", "a", "b"):
        inv.add(rid)
    assert [rid for rid, _ in inv.items()] == ["c", "a", "b"]


def test_remove_success_and_failure():
    inv = Inventory()
    inv.add("a", 2)
    assert inv.remove("a", 2) is True
    assert inv.count("a") == 0
    assert "a" not in inv
    assert inv.remove("a") is False


def test_remove_more_than_have_changes_nothing():
    inv = Inventory()
    inv.add("a", 2)
    assert inv.remove("a", 5) is False
    assert inv.count("a") == 2


def test_has_and_len():
    inv = Inventory()
    inv.add("a", 4)
    inv.add("b", 1)
    assert inv.has("a", 4) and not inv.has("a", 5)
    assert len(inv) == 2
    assert inv.used_slots == 2 and inv.free_slots == inv.size - 2


def test_negative_amount_rejected():
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.add("a", -1)
    with pytest.raises(ValueError):
        inv.remove("a", -1)


def test_copy_is_independent():
    inv = Inventory()
    inv.add("a", 1)
    clone = inv.copy()
    clone.add("a", 1)
    assert inv.count("a") == 1
    assert clone.count("a") == 2


# --------------------------------------------------------------- стаки и лимит
def test_stack_max_splits_the_item_across_slots():
    inv = Inventory(slots=4, stack_max=10)
    assert inv.add("a", 25) == 0
    assert inv.count("a") == 25
    assert inv.used_slots == 3, "25 = 10 + 10 + 5"
    assert inv.slots[2].count == 5


def test_add_stops_when_there_is_no_room():
    inv = Inventory(slots=1, stack_max=10)
    assert inv.add("a", 12) == 2, "лишнее возвращается хозяину"
    assert inv.count("a") == 10
    assert inv.is_full


def test_fits_and_can_accept():
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 10)
    assert inv.fits("a", 10) == 10, "докладываем в тот же стак"
    assert inv.fits("b", 10) == 10, "пустой слот"
    assert inv.fits("b", 11) == 10, "больше, чем влезает"
    assert inv.can_accept("b", 10) and not inv.can_accept("b", 11)
    assert inv.fits("a", 0) == 0


def test_can_accept_does_not_change_the_container():
    """Проверка места — только чтение: иначе добыча тратила бы ресурс впустую."""
    inv = Inventory(slots=1, stack_max=5)
    inv.add("a", 3)
    before = inv.state()
    assert inv.can_accept("a", 2) and not inv.can_accept("a", 3)
    assert inv.state() == before


def test_take_returns_what_it_actually_took():
    """`take` берёт сколько есть — на нём стоит перенос между контейнерами."""
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 4)
    assert inv.take("a", 3) == 3
    assert inv.count("a") == 1
    assert inv.take("a", 5) == 1, "больше, чем есть"
    assert inv.take("a", 1) == 0
    assert inv.is_empty


def test_remove_takes_from_the_last_stacks_first():
    """Первые стаки остаются целыми — сетка в интерфейсе не «дышит»."""
    inv = Inventory(slots=4, stack_max=10)
    inv.add("a", 25)
    inv.remove("a", 3)
    assert [s.count if s else 0 for s in inv.slots] == [10, 10, 2, 0]


def test_remove_empty_container_is_a_no_op():
    inv = Inventory(slots=2)
    assert inv.remove("a", 0) is True, "ноль единиц всегда можно забрать"
    assert inv.remove("a", 1) is False


# ------------------------------------------------------------- работа со слотами
def test_take_slot_whole_and_partial():
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 7)
    part = inv.take_slot(0, 3)
    assert (part.item_id, part.count) == ("a", 3)
    assert inv.slot(0).count == 4
    assert inv.take_slot(0).count == 4
    assert inv.slot(0) is None
    assert inv.take_slot(0) is None


def test_put_slot_merges_and_swaps():
    inv = Inventory(slots=2, stack_max=10)
    inv.slots[0] = Stack("a", 8)
    rest = inv.put_slot(0, Stack("a", 5))
    assert inv.slots[0].count == 10 and rest.count == 3, "досыпали до максимума"
    inv.slots[1] = Stack("b", 4)
    rest = inv.put_slot(1, Stack("c", 2))
    assert inv.slots[1].item_id == "c" and rest.item_id == "b", "обмен как в Minecraft"


def test_put_slot_into_empty_old_stack_returns_leftover():
    inv = Inventory(slots=1, stack_max=10)
    rest = inv.put_slot(0, Stack("a", 14))
    assert inv.slots[0].count == 10 and rest.count == 4


def test_move_slot_between_slots():
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 4)
    assert inv.move_slot(0, 1)
    assert inv.slot(0) is None and inv.slot(1).item_id == "a"
    assert not inv.move_slot(0, 1), "пустой слот двигать некуда"


def test_split_slot_halves_the_stack():
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 7)
    assert inv.split_slot(0)
    assert [s.count if s else 0 for s in inv.slots] == [4, 3]
    assert not inv.split_slot(0), "нет свободного слота"


def test_split_slot_needs_two_units():
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 1)
    assert not inv.split_slot(0)


def test_transfer_to_other_container():
    src = Inventory(slots=2, stack_max=10)
    dst = Container(slots=1, stack_max=10)
    src.add("a", 6)
    assert src.transfer_to(dst, 0) == 6
    assert src.count("a") == 0 and dst.count("a") == 6
    assert src.transfer_to(dst, 0) == 0, "пустой слот"


def test_transfer_leaves_what_does_not_fit():
    src = Inventory(slots=1, stack_max=10)
    dst = Container(slots=1, stack_max=10)
    src.add("a", 10)
    dst.add("a", 4)
    assert src.transfer_to(dst, 0) == 6
    assert src.count("a") == 4 and dst.count("a") == 10


def test_transfer_all_moves_everything():
    src = Inventory(slots=3, stack_max=10)
    dst = Container(slots=3, stack_max=10)
    src.add("a", 3)
    src.add("b", 4)
    assert src.transfer_all_to(dst) == 7
    assert src.total_count() == 0 and dst.total_count() == 7


# -------------------------------------------------------- закон сохранения
def _total(*containers) -> int:
    return sum(c.total_count() for c in containers)


@pytest.mark.parametrize("dst_slots,dst_free", [(3, 0), (1, 0), (1, 5), (2, 7)])
def test_transfer_never_creates_or_loses_items(dst_slots, dst_free):
    """Единственный перенос в игре: сумма до равна сумме после."""
    src = Inventory(slots=3, stack_max=10)
    dst = Container(slots=dst_slots, stack_max=10)
    src.add("a", 17)
    src.add("b", 4)                # 21 единица, три слота — как раз
    if dst_free:
        dst.add("c", dst_free)
    before = _total(src, dst)
    for index in range(src.size):
        src.transfer_to(dst, index)
    assert _total(src, dst) == before
    assert dst.count("a") + src.count("a") == 17
    assert dst.count("b") + src.count("b") == 4


def test_transfer_all_never_creates_or_loses_items():
    """То же для переноса «всё, что влезает»: остаток остаётся у хозяина."""
    src = Inventory(slots=4, stack_max=10)
    dst = Container(slots=1, stack_max=10)
    src.add("a", 25)
    before = _total(src, dst)
    moved = src.transfer_all_to(dst)
    assert moved == 10
    assert _total(src, dst) == before
    assert dst.count("a") == 10 and src.count("a") == 15


def test_give_returns_the_leftover():
    """`give` отдаёт стак в контейнеры по очереди и возвращает остаток."""
    first = Container(slots=2, stack_max=10)
    second = Container(slots=2, stack_max=10)
    first.add("x", 10)                       # у первого свободен ровно один слот
    assert give(Stack("a", 15), first, second) is None
    assert first.count("a") == 10 and second.count("a") == 5
    # У второго остался второй слот — в него влезет 10, остальное вернётся.
    rest = give(Stack("b", 12), first, second)
    assert (rest.item_id, rest.count) == ("b", 2), "не влезло — вернулось хозяину"
    assert _total(first, second) + rest.count == 37, "ничего не потерялось"


def test_give_of_nothing_is_nothing():
    assert give(None, Container(slots=1)) is None
    assert give(Stack("a", 0), Container(slots=1)) is None


# ------------------------------------------------------------------ сортировка
def test_sort_orders_by_item_then_count():
    """Сортировка — по типу, потом по количеству (plan/03)."""
    inv = Inventory(slots=6, stack_max=10)
    inv.add("b", 2)
    inv.add("a", 5)
    inv.add("a", 3)
    inv.add("c", 1)
    inv.sort()
    assert [(s.item_id, s.count) for s in inv.slots if s] == \
        [("a", 8), ("b", 2), ("c", 1)]


def test_sort_merges_partial_stacks():
    """Два неполных стака одного предмета сливаются, а не остаются рядом."""
    inv = Inventory(slots=4, stack_max=10)
    inv.slots[0] = Stack("a", 4)
    inv.slots[2] = Stack("a", 2)
    inv.slots[3] = Stack("b", 6)
    inv.sort()
    assert [(s.item_id, s.count) for s in inv.slots if s] == [("a", 6), ("b", 6)]


def test_sort_keeps_the_total_count():
    inv = Inventory(slots=8, stack_max=10)
    inv.add("a", 25)
    inv.add("b", 3)
    before = inv.total_count()
    inv.sort()
    assert inv.total_count() == before
    assert inv.used_slots == 4, "25 = 10 + 10 + 5 и один стак b"


def test_sort_of_an_empty_container_is_safe():
    inv = Inventory(slots=3)
    inv.sort()
    assert inv.is_empty and inv.state() == (None, None, None)


def test_container_rejects_bad_sizes():
    with pytest.raises(ValueError):
        Container(slots=-1)
    with pytest.raises(ValueError):
        Container(slots=2, stack_max=0)


def test_state_snapshot():
    inv = Inventory(slots=2, stack_max=10)
    inv.add("a", 3)
    assert inv.state() == (("a", 3), None)


def test_drone_inventory_uses_config_numbers():
    inv = Inventory()
    assert inv.size == config.DRONE_SLOTS
    assert inv.stack_max == config.SLOT_STACK_MAX
