"""Окно предметов: сетки дрона и зданий, перенос, сортировка, крафт (M3.5, plan/02, plan/03)."""

from __future__ import annotations

import pygame
import pytest

from src.game import config
from src.game.data.buildings import LAB, LAB_SAMPLES, LAMP
from src.game.render.icons import IconCache
from src.game.sim.resources import Stack
from src.game.sim.simulation import Simulation
from src.game.ui.item_window import (DOUBLE_CLICK_MS, MODE_CONTAINER, MODE_DRONE,
                                     ItemWindow)
from tests.conftest import SEED, place


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
    """Ставит лабораторию рядом с дроном, как это делает игрок: из предмета-постройки."""
    x, y = int(sim.drone.x) + 1, int(sim.drone.y)
    lab = place(sim, LAB, x, y)
    assert lab is not None and sim.buildings.at(x, y) is lab
    assert sim.cargo.count(LAB) == 0, "предмет-постройка потрачен на здание"
    return lab


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
    """Окно E: сетка инвентаря и под ней отдельный ряд хотбара (plan/03)."""
    w, sim, _screen = window
    w.open_drone(sim)
    panels = w.panels(sim)
    assert len(panels) == 2
    assert panels[0].container is sim.inventory and panels[1].container is sim.hotbar
    for panel in panels:
        container = panel.container
        assert panel.grid.cols * panel.grid.rows(container.size) >= container.size, \
            "все слоты дрона должны быть видны"
    assert panels[1].grid.rows(sim.hotbar.size) == 1, "хотбар — один ряд"
    assert panels[1].active == sim.selected_slot, "выбранный слот подсвечен и в окне"
    placed = w._layout(sim)[1]                   # координаты считает раскладка
    inventory, hotbar = placed[0], placed[1]
    inventory_bottom = inventory.origin[1] + inventory.grid.size(sim.inventory.size)[1]
    assert hotbar.origin[1] > inventory_bottom, "хотбар — под основной сеткой"


def test_drone_window_has_the_craft_panel_and_the_lab_window_does_not(window):
    """Крафт живёт в окне E, справа от сеток; в окне здания его нет (plan/02)."""
    w, sim, _screen = window
    lab = put_lab(sim)
    w.open_drone(sim)
    assert w.has_craft
    (x, y, ww, hh), panels = w._layout(sim)
    window_rect = pygame.Rect(x, y, ww, hh)
    search = w.craft.search.rect
    assert window_rect.contains(search), "панель крафта — внутри окна"
    grids_right = max(p.origin[0] + p.grid.size(p.container.size)[0] for p in panels)
    assert search.left >= grids_right, "панель крафта — справа от сеток"

    w.open_container(sim, lab)
    assert not w.has_craft, "в окне здания панели крафта нет"
    assert [p.container for p in w.panels(sim)] == \
        [samples(lab), sim.inventory, sim.hotbar], "слева здание, справа дрон"


def test_lab_window_shows_lab_and_drone(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    w.open_container(sim, lab)
    panels = w.panels(sim)
    assert len(panels) == 3
    assert panels[0].container is samples(lab)
    assert panels[1].container is sim.inventory and panels[2].container is sim.hotbar
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
    assert panels[1].statuses == () and panels[2].statuses == (), \
        "у сеток дрона состояний нет"


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


def test_shift_click_in_the_drone_window_moves_between_inventory_and_hotbar(window):
    """В окне E Shift+ЛКМ перекидывает стак между инвентарём и хотбаром."""
    w, sim, _screen = window
    item = item_of(sim)
    sim.inventory.add(item, 3)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim, shift=True)     # инвентарь → хотбар
    assert sim.inventory.count(item) == 0 and sim.hotbar.count(item) == 3
    assert w.held is None
    w.click(slot_pos(w, sim, 1, slot_of(sim.hotbar, item)), 1, sim, shift=True)
    assert sim.hotbar.count(item) == 0 and sim.inventory.count(item) == 3, \
        "хотбар → инвентарь"


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


def test_left_click_carries_a_stack_from_the_inventory_to_the_hotbar(window):
    """Перетаскивание между сетками дрона — та же логика ЛКМ, что внутри сетки."""
    w, sim, _screen = window
    sim.inventory.add(LAMP, 3)                   # прямо в инвентарь, мимо правила
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    w.click(slot_pos(w, sim, 1, 4), 1, sim)
    assert w.held is None
    assert sim.hotbar.slot(4).item_id == LAMP and sim.hotbar.slot(4).count == 3
    assert sim.inventory.count(LAMP) == 0


def test_right_click_put_one_respects_the_building_stack_limit(window):
    """ПКМ «положить одну» не переполняет стак постройки: лимит 🔧 `BUILDING_STACK`."""
    w, sim, _screen = window
    limit = config.BUILDING_STACK
    assert sim.hotbar.max_for(LAMP) == limit < sim.hotbar.stack_max
    sim.hotbar.slots[0] = Stack(LAMP, limit - 1)
    sim.inventory.add(LAMP, 3)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)      # три лампы в руку
    w.click(slot_pos(w, sim, 1, 0), 3, sim)      # одна досыпается до лимита
    assert sim.hotbar.slot(0).count == limit and w.held.count == 2
    w.click(slot_pos(w, sim, 1, 0), 3, sim)      # стак полон — ничего
    assert sim.hotbar.slot(0).count == limit and w.held.count == 2
    assert sim.cargo.count(LAMP) + w.held.count == limit + 2, "ничего не потеряно"


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


def test_close_returns_a_held_building_to_the_hotbar(window):
    """Предмет-постройка из руки при закрытии ложится в хотбар — оттуда её ставят."""
    w, sim, _screen = window
    sim.inventory.add(LAMP, 2)                   # лежала в рюкзаке
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    assert w.held.item_id == LAMP and w.held.count == 2
    w.close(sim)
    assert w.held is None and not sim.drops.total().get(LAMP)
    assert sim.hotbar.count(LAMP) == 2 and sim.inventory.count(LAMP) == 0, \
        "постройка вернулась в хотбар по правилу `Cargo`"


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


def test_held_stack_does_not_vanish_with_a_dismantled_building(window):
    """Здание снесли при открытом окне: предмет из руки не уходит в его слоты.

    Снесённое здание исчезло с карты вместе с контейнерами, и стак, «вернувшийся»
    туда, пропал бы — закон сохранения (plan/03). Он идёт дрону.
    """
    w, sim, _screen = window
    lab = put_lab(sim)
    item = item_of(sim)
    samples(lab).add(item, 3)
    w.open_container(sim, lab)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)     # образцы — в руку
    assert w.held is not None and w.held.item_id == item and w.held.count == 3
    assert sim.dismantle_building(lab.x, lab.y)
    assert lab not in sim.buildings.buildings

    w.close(sim)
    assert w.held is None
    assert samples(lab).count(item) == 0, "в снесённое здание ничего не вернулось"
    assert sim.cargo.count(item) + sim.drops.total().get(item, 0) == 3, \
        "стак из руки не пропал"
    assert sim.cargo.count(item) == 3, "место у дрона есть — стак у него"


def test_opening_the_other_window_returns_the_held_stack(window):
    w, sim, _screen = window
    lab = put_lab(sim)
    sim.inventory.add(item_of(sim), 2)
    w.open_drone(sim)
    w.click(slot_pos(w, sim, 0, 0), 1, sim)
    w.open_container(sim, lab)
    assert w.mode == MODE_CONTAINER and w.held is None
    assert sim.inventory.count(item_of(sim)) == 2


# ----------------------------------------- закон сохранения при двойном клике
def test_double_click_gathers_no_more_than_a_stack(window):
    """Рука, как и слот, вмещает не больше лимита стека — остальное ждёт в слотах."""
    w, sim, _screen = window
    sim.hotbar.add(LAMP, config.BUILDING_STACK)
    sim.inventory.add(LAMP, config.BUILDING_STACK)
    w.open_drone(sim)
    pos = slot_pos(w, sim, 1, 0)                 # хотбар, слот с лампами
    w.click(pos, 1, sim, when=1000)
    w.click(pos, 1, sim, when=1000 + DOUBLE_CLICK_MS)
    assert w.held.item_id == LAMP and w.held.count == config.BUILDING_STACK
    assert sim.cargo.count(LAMP) == config.BUILDING_STACK, "второй стак остался"


def test_swapping_a_gathered_stack_loses_nothing(window):
    """Сценарий из ревью: двойной клик по лампам, затем обмен на слот с рудой.

    Раньше рука собирала 32 лампы, обмен клал в слот только 16, а остальные
    16 пропадали. Сумма предметов до и после обязана совпасть.
    """
    w, sim, _screen = window
    ore = item_of(sim)
    sim.hotbar.add(LAMP, config.BUILDING_STACK)
    sim.inventory.add(LAMP, config.BUILDING_STACK)
    sim.inventory.add(ore, 3)
    total = sim.cargo.total_count()
    w.open_drone(sim)
    lamps = slot_pos(w, sim, 1, 0)
    w.click(lamps, 1, sim, when=1000)
    w.click(lamps, 1, sim, when=1000 + DOUBLE_CLICK_MS)
    w.click(slot_pos(w, sim, 0, slot_of(sim.inventory, ore)), 1, sim, when=5000)
    w.close(sim)
    on_ground = sum(sim.drops.total().values())
    assert sim.cargo.total_count() + on_ground == total


def test_oversized_stack_is_not_swapped_into_a_slot(window):
    """Если в руке больше лимита слота, обмена нет: рука остаётся как была."""
    _w, sim, _screen = window
    ore = item_of(sim)
    sim.inventory.slots[0] = Stack(ore, 3)
    held = Stack(LAMP, config.BUILDING_STACK + 5)
    rest = sim.inventory.put_slot(0, held)
    assert rest is held and rest.count == config.BUILDING_STACK + 5
    assert sim.inventory.slot(0).item_id == ore and sim.inventory.slot(0).count == 3
