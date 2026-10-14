"""Окно предметов: сетка слотов, перенос, сортировка, снос (M3.5, plan/03)."""

from __future__ import annotations

import pygame
import pytest

from src.game import config
from src.game.data.buildings import LAB, LAB_SAMPLES
from src.game.render.icons import IconCache
from src.game.sim.resources import Stack
from src.game.sim.simulation import Simulation
from src.game.ui.item_window import (DOUBLE_CLICK_MS, MODE_CONTAINER, MODE_DRONE,
                                     ItemWindow)
from tests.conftest import SEED, stock


@pytest.fixture(scope="module")
def display():
    pygame.display.init()
    screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H))
    yield screen
    pygame.display.quit()


@pytest.fixture
def window(display):
    sim = Simulation(SEED)
    return ItemWindow(IconCache(sim.run.items)), sim, display


def slot_pos(window: ItemWindow, sim: Simulation, panel: int, slot: int) -> tuple[int, int]:
    """Центр слота на экране: раскладка детерминирована, считаем её же."""
    panels = window._layout(sim)[1]
    rects = panels[panel].grid.rects(panels[panel].origin,
                                     panels[panel].container.size)
    return rects[slot].center


def button_pos(window: ItemWindow, sim: Simulation, panel: int) -> tuple[int, int]:
    return window._layout(sim)[1][panel].button.center


def put_lab(sim: Simulation):
    """Ставит лабораторию рядом с дроном, как это делает игрок (M4: за плату)."""
    stock(sim)
    sim.select_build(LAB)
    x, y = int(sim.drone.x) + 1, int(sim.drone.y)
    assert sim.click_at(x, y) == LAB
    return sim.buildings.at(x, y)


def samples(lab):
    """Контейнер образцов лаборатории — из данных здания."""
    return lab.container(LAB_SAMPLES)


def item_of(sim: Simulation) -> str:
    return sim.run.items.natural[0].id


def slot_of(container, item_id: str) -> int:
    """Номер слота с предметом: запас ролей занимает первые слоты дрона."""
    for index in range(container.size):
        stack = container.slot(index)
        if stack is not None and stack.item_id == item_id:
            return index
    raise AssertionError(f"{item_id} не найден в контейнере")


# ------------------------------------------------------------------ открытие
def test_window_is_closed_until_asked(window):
    w, sim, _screen = window
    assert not w.visible and w.mode is None
    w.toggle_drone(sim)
    assert w.visible and w.mode == MODE_DRONE
    w.toggle_drone(sim)
    assert not w.visible


def test_drone_window_shows_the_whole_inventory(window):
    w, sim, _screen = window
    w.open_drone(sim)
    panels = w.panels(sim)
    assert len(panels) == 1
    assert panels[0].container is sim.inventory
    assert panels[0].grid.cols * panels[0].grid.rows(sim.inventory.size) >= \
        sim.inventory.size, "все слоты дрона должны быть видны"


def test_lab_window_shows_lab_and_drone(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    w.open_container(sim, lab)
    panels = w.panels(sim)
    assert len(panels) == 2
    assert panels[0].container is samples(lab) and panels[1].container is sim.inventory
    assert panels[0].container.size == config.LAB_SLOTS
    assert panels[0].title == "Образцы", "заголовок берётся из данных здания"


def test_lab_window_shows_slot_states(window):
    """Под сеткой лаборатории видно, что делает каждый слот (plan/02)."""
    w, sim, _screen = window
    lab = put_lab(sim)
    samples(lab).add(item_of(sim), 1)
    w.open_container(sim, lab)
    panels = w.panels(sim)
    assert len(panels[0].statuses) == config.LAB_SLOTS
    assert panels[0].statuses[0][0] == "analyzing"
    assert panels[1].statuses == (), "у инвентаря дрона состояний нет"


def test_window_fits_on_screen(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    for opener in (lambda: w.open_drone(sim), lambda: w.open_container(sim, lab)):
        opener()
        x, y, ww, hh = w._layout(sim)[0]
        assert 0 <= x and 0 <= y
        assert x + ww <= config.WINDOW_W and y + hh <= config.WINDOW_H


def test_closed_window_swallows_nothing(window):
    w, sim, _screen = window
    assert not w.click((10, 10), 1, sim), "закрытое окно кликов не берёт"


def test_draw_does_not_change_simulation(window):
    w, sim, screen = window
    lab = put_lab(sim)
    samples(lab).add(item_of(sim), 2)
    w.open_container(sim, lab)
    before = sim.state_signature()
    screen.fill((0, 0, 0))
    w.draw(screen, sim)
    assert sim.state_signature() == before


# --------------------------------------------------------------- перенос ЛКМ
def test_left_click_takes_a_stack_and_puts_it_back(window):
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 7)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    assert w.held is not None and w.held.count == 7
    assert sim.inventory.count(item) == 0, "стак ушёл в руку"
    w.click(slot_pos(w, sim, 0, 3), 1, sim)
    assert w.held is None
    assert sim.inventory.slot(3).count == 7


def test_left_click_tops_up_a_stack_of_the_same_item(window):
    """ЛКМ по слоту с таким же предметом досыпает до лимита (plan/03)."""
    w, sim, _screen = window
    item = item_of(sim)
    limit = sim.inventory.stack_max
    sim.inventory.slots[0] = Stack(item, limit - 3)
    sim.inventory.slots[1] = Stack(item, 5)      # в руке окажется весь второй стак
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 1), 1, sim)
    assert w.held.count == 5
    w.click(slot_pos(w, sim, 0, 0), 1, sim)      # досыпаем в первый слот
    assert sim.inventory.slot(0).count == limit
    assert (w.held.item_id, w.held.count) == (item, 2), "остаток остался в руке"
    assert sim.inventory.count(item) + w.held.count == limit + 2, "ничего не потеряно"


def test_left_click_swaps_different_items(window):
    w, sim, _screen = window
    first, second = sim.run.items.natural[0].id, sim.run.items.natural[1].id
    sim.inventory.add(first, 2)           # слот 0
    sim.inventory.add(second, 3)          # слот 1
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    w.click(slot_pos(w, sim, 0, 1), 1, sim)
    assert sim.inventory.slot(0) is None, "первый слот опустел"
    assert sim.inventory.slot(1).item_id == first
    assert w.held.item_id == second and w.held.count == 3


# --------------------------------------------------------------- перенос ПКМ
def test_right_click_takes_half_of_the_stack(window):
    """ПКМ пустой рукой берёт половину — большая часть остаётся в слоте."""
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 7)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 3, sim)
    assert (w.held.item_id, w.held.count) == (item, 3)
    assert sim.inventory.slot(0).count == 4
    assert w.held.count + sim.inventory.count(item) == 7, "предмет не потерялся"


def test_right_click_on_a_single_unit_takes_nothing(window):
    """Половину от одной единицы взять нельзя: слот остаётся целым."""
    w, sim, _screen = window
    sim.inventory.add(item_of(sim), 1)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 3, sim)
    assert w.held is None and sim.inventory.slot(0).count == 1


def test_right_click_puts_one_unit(window):
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 5)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    w.click(slot_pos(w, sim, 0, 2), 3, sim)
    assert sim.inventory.slot(2).count == 1
    assert w.held.count == 4
    assert w.held.count + sim.inventory.count(item) == 5, "предмет не потерялся"


def test_right_click_into_a_different_item_does_nothing(window):
    w, sim, _screen = window
    first, second = sim.run.items.natural[0].id, sim.run.items.natural[1].id
    sim.inventory.add(first, 5)
    sim.inventory.slots[1] = None
    sim.inventory.add(second, 2)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    w.click(slot_pos(w, sim, 0, 1), 3, sim)
    assert sim.inventory.slot(1).count == 2, "чужой стак не тронут"
    assert w.held.count == 5


# ------------------------------------------------------------- двойной клик
def test_double_click_collects_every_unit_of_the_item(window):
    """Двойной ЛКМ собирает в руку весь предмет из всех сеток окна."""
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.slots[0] = Stack(item, 5)
    sim.inventory.slots[1] = Stack(item, 4)
    sim.inventory.slots[2] = Stack(item, 3)
    assert sim.inventory.count(item) == 12 and sim.inventory.used_slots == 3
    w.open_drone(sim)
    pos = slot_pos(w, sim, 0, 0)
    w.click(pos, 1, sim, when=1000)
    assert w.held.count == 5
    w.click(pos, 1, sim, when=1000 + DOUBLE_CLICK_MS)
    assert w.held.item_id == item and w.held.count == 12
    assert sim.inventory.count(item) == 0


def test_double_click_from_the_lab_gathers_from_the_drone_too(window):
    """Собирается весь предмет окна: и из лаборатории, и из сетки дрона."""
    w, sim, _screen = window
    lab = put_lab(sim)
    item = item_of(sim)
    sim.inventory.add(item, 2)
    samples(lab).add(item, 3)
    w.open_container(sim, lab)
    pos = slot_pos(w, sim, 0, 0)
    w.click(pos, 1, sim, when=1000)
    w.click(pos, 1, sim, when=1000 + DOUBLE_CLICK_MS)
    assert (w.held.item_id, w.held.count) == (item, 5)
    assert sim.inventory.count(item) == 0 and samples(lab).count(item) == 0


def test_slow_second_click_is_not_a_double_click(window):
    """Медленный второй клик — обычный клик: стак возвращается в слот."""
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 5)
    sim.inventory.add(item, 4)
    w.open_drone(sim)
    pos = slot_pos(w, sim, 0, 0)
    w.click(pos, 1, sim, when=1000)
    w.click(pos, 1, sim, when=1000 + DOUBLE_CLICK_MS + 1)
    assert sim.inventory.count(item) == 9, "ничего не собиралось"
    assert w.held is None


def test_double_click_on_another_slot_does_not_collect(window):
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.slots[0] = Stack(item, 5)
    sim.inventory.slots[3] = Stack(item, 4)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim, when=1000)
    w.click(slot_pos(w, sim, 0, 2), 1, sim, when=1000 + 10)
    assert w.held is None and sim.inventory.slot(2).count == 5
    assert sim.inventory.slot(3).count == 4, "остальное не тронуто"


# ------------------------------------------------------------- сортировка
def test_sort_button_orders_the_drone_grid(window):
    w, sim, _screen = window
    first, second = sim.run.items.natural[0].id, sim.run.items.natural[1].id
    sim.inventory.add(second, 2)
    sim.inventory.add(first, 3)
    w.open_drone(sim)
    assert w.click(button_pos(w, sim, 0), 1, sim), "клик по кнопке поглощён окном"
    assert [(s.item_id, s.count) for s in sim.inventory.slots if s] == \
        [(first, 3), (second, 2)], "сортировка по типу, потом по количеству"


def test_sort_keeps_the_total_count(window):
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 5)
    sim.inventory.add(item, 4)
    before = sim.inventory.total_count()
    w.open_drone(sim)
    w.click(button_pos(w, sim, 0), 1, sim)
    assert sim.inventory.total_count() == before


def test_sort_button_does_not_touch_the_lab_slots(window):
    """Сетку образцов не сортируем: прогресс привязан к номеру слота."""
    w, sim, _screen = window
    lab = put_lab(sim)
    samples(lab).add(item_of(sim), 1)
    w.open_container(sim, lab)
    assert w._layout(sim)[1][0].button is None, "у лаборатории кнопки нет"
    assert w._layout(sim)[1][1].button is not None, "у дрона — есть"


# ------------------------------------------------------- Shift+ЛКМ: перекладывание
def test_shift_click_moves_a_stack_between_lab_and_drone(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    item = item_of(sim)
    sim.inventory.add(item, 6)
    w.open_container(sim, lab)
    drone_slot = slot_of(sim.inventory, item)
    w.click(slot_pos(w, sim, 1, drone_slot), 1, sim, shift=True)   # дрон → лаборатория
    assert samples(lab).count(item) == 6 and sim.inventory.count(item) == 0
    w.click(slot_pos(w, sim, 0, 0), 1, sim, shift=True)   # лаборатория → дрон
    assert samples(lab).total_count() == 0 and sim.inventory.count(item) == 6


def test_shift_click_in_the_drone_window_does_nothing(window):
    w, sim, _screen = window
    sim.inventory.add(item_of(sim), 3)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim, shift=True)
    assert sim.inventory.count(item_of(sim)) == 3
    assert w.held is None


def test_shift_click_reports_when_there_is_no_room(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    item, other = sim.run.items.natural[0].id, sim.run.items.natural[1].id
    samples(lab).add(other, config.LAB_SLOTS * config.SLOT_STACK_MAX)
    sim.inventory.add(item, 1)
    w.open_container(sim, lab)
    w.click(slot_pos(w, sim, 1, 0), 1, sim, shift=True)
    assert sim.inventory.count(item) == 1, "не влезло — осталось у дрона"
    assert any("места" in t.text.lower() for t in sim.toasts)


# ------------------------------------------------------------------ выброс
def test_drop_under_cursor_throws_the_stack(window):
    """Q выбрасывает стак из-под курсора — вещь не застревает в руках."""
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 4)
    w.open_drone(sim)
    before = sim.drops.total()
    assert w.drop_under_cursor(slot_pos(w, sim, 0, 0), sim)
    assert sim.inventory.count(item) == 0
    assert sim.drops.total()[item] == before.get(item, 0) + 4


# ------------------------------------------------------------------- закрытие
def test_close_returns_the_held_stack(window):
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 5)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    assert w.held is not None
    w.close(sim)
    assert w.held is None and not w.visible
    assert sim.inventory.count(item) == 5, "предмет вернулся, а не пропал"


def test_close_drops_what_does_not_fit(window):
    w, sim, _screen = window
    free_slots = sim.inventory.size * sim.inventory.stack_max
    sim.inventory.add(item_of(sim), 1)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    # Пока предмет в руке, забиваем инвентарь другим предметом доверху.
    other = sim.run.items.natural[1].id
    assert sim.inventory.add(other, free_slots) == 0
    w.close(sim)
    assert w.held is None
    assert len(sim.drops) == 1, "не влезло — упало под ноги"
    assert sim.drops.items[0].count == 1


def test_close_returns_the_held_stack_to_the_open_lab(window):
    """Предмет из руки возвращается и в лабораторию, если у дрона места нет."""
    w, sim, _screen = window
    lab = put_lab(sim)
    item = item_of(sim)
    other = sim.run.items.natural[1].id
    samples(lab).add(item, 1)
    sim.inventory.add(other, sim.inventory.size * sim.inventory.stack_max)
    w.open_container(sim, lab)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)     # взяли образец в руку
    assert w.held.item_id == item
    w.close(sim)
    assert sim.inventory.count(item) == 0 and not sim.drops.total().get(item)
    assert samples(lab).count(item) == 1, "вернулся в слот, а не пропал"


def test_opening_the_other_window_returns_the_held_stack(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    sim.inventory.add(item_of(sim), 2)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    w.open_container(sim, lab)
    assert w.mode == MODE_CONTAINER and w.held is None
    assert sim.inventory.count(item_of(sim)) == 2
