"""Приложение: главный цикл, пауза, ввод.

Тесты идут в dummy-режиме SDL (см. conftest), окно не открывается. Ввод
проверяем целиком через `Game.handle_event` и `Game.poll_input`: мышь и
клавиатура подменяются через monkeypatch, а не дёргаются методы симуляции.
"""

from __future__ import annotations

import pygame
import pytest

from src.game import config
from src.game.app import Game
from src.game.data.buildings import CONVEYOR, DRILL, LAB, LAB_SAMPLES, LAMP, WIRE
from src.game.data.resources import Prop
from src.game.sim.buildings import DIRECTIONS
from src.game.sim.commands import SelectHotbar
from src.game.sim.resources import Stack, give
from src.game.sim.simulation import ENERGY_ITEM
from src.game.ui import widgets
from src.game.ui.hud import PROMPT_ABOVE_HOTBAR
from src.game.ui.item_window import MODE_CONTAINER, MODE_DRONE
from tests.conftest import (SEED, give_building, place, power_grid, slot_of,
                            stock)


@pytest.fixture
def game():
    g = Game(seed=SEED)
    yield g
    g.close()


def ticks_in(seconds: float) -> int:
    return int(seconds / config.TICK)


# -------------------------------------------------------------------- пауза
def test_paused_does_not_advance_simulation(game):
    game.paused = True
    before = game.sim.state_signature()
    assert game.advance(1.0) == 0
    assert game.sim.state_signature() == before


def test_unpaused_advances_fixed_steps(game):
    game.sim.set_move_input(1.0, 0.0)
    assert game.advance(1.0) == ticks_in(1.0)
    assert game.sim.time == pytest.approx(1.0, abs=1e-6)


def test_pause_accumulator_does_not_pile_up(game):
    game.paused = True
    game.advance(1.0)
    game.paused = False
    assert game.advance(config.TICK) == 1


def test_partial_frames_accumulate(game):
    half = config.TICK / 2
    assert game.advance(half) == 0
    assert game.advance(half) == 1


def test_space_toggles_pause(game):
    assert not game.paused
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    assert game.paused
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    assert not game.paused


def test_escape_opens_the_pause_menu_and_quit_is_a_button(game):
    """Esc игру не закрывает: открывает меню паузы, выход — кнопкой «Выйти»."""
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
    assert game.running and game.menu.visible
    quit_rect = game.menu.buttons()[-1][2]
    game.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                                         pos=quit_rect.center))
    assert not game.running


def test_quit_event_stops_game(game):
    game.handle_event(pygame.event.Event(pygame.QUIT))
    assert not game.running


def test_wheel_changes_zoom(game):
    before = game.camera.zoom
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1, x=0))
    assert game.camera.zoom > before
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-1, x=0))
    assert game.camera.zoom == pytest.approx(before)


def test_f3_toggles_debug(game):
    assert not game.debug.visible
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3))
    assert game.debug.visible


def test_f2_toggles_light_layer(game):
    assert not game.debug.show_layers
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F2))
    assert game.debug.show_layers
    game.draw_frame(config.TICK)
    assert game.renderer.overlay.draws > 0, "слой света должен быть нарисован"


# ------------------------------------------------------------ хелперы ввода
def key(game, code: int) -> None:
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=code))


def mods(monkeypatch, value: int = 0) -> None:
    """Зажатые модификаторы (Shift/Ctrl) — их приложение читает при клике и колесе."""
    monkeypatch.setattr(pygame.key, "get_mods", lambda: value)


def screen_of(game, tx: int, ty: int) -> tuple[int, int]:
    """Экранная точка центра тайла."""
    sx, sy = game.camera.world_to_screen(tx + 0.5, ty + 0.5)
    return int(sx), int(sy)


def click_screen(game, pos: tuple[int, int], button: int = 1) -> None:
    game.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos))


def click_at(game, tx: int, ty: int, button: int = 1) -> None:
    """Клик мышью по центру тайла (по умолчанию ЛКМ)."""
    click_screen(game, screen_of(game, tx, ty), button)


def hold_mouse(monkeypatch, pos, buttons=(0, 0, 0), keys=None):
    """Мышь в заданной экранной точке (и с зажатыми кнопками)."""
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: pos)
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: buttons)
    monkeypatch.setattr(pygame.key, "get_pressed",
                        lambda: keys if keys is not None else [False] * 512)


def next_to_drone(game) -> tuple[int, int]:
    """Соседний с дроном тайл свободной земли — туда ставят все тесты."""
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    assert game.sim.can_place(LAMP, tx, ty), "у точки старта должна быть земля"
    return tx, ty


def find_tile(game, kind: str = LAMP, near: float = 0.0, far: float = 1e9,
              in_range: bool = True) -> tuple[int, int]:
    """Свободный под `kind` тайл на экране на расстоянии `near`…`far` от дрона.

    `in_range=False` — тайл годится под здание, но дрон до него не достаёт:
    так проверяется именно радиус, а не гора или вода.
    """
    sim = game.sim
    dtx, dty = sim.drone.tile()
    top = game.hud.hotbar.rect.top
    tiles = [(dtx + dx, dty + dy) for dx in range(-14, 15) for dy in range(-9, 10)]
    tiles.sort(key=lambda t: sim.drone.distance_to_tile(*t))
    for x, y in tiles:
        if not near <= sim.drone.distance_to_tile(x, y) <= far:
            continue
        if not sim._tile_ok_for(kind, x, y) or sim.in_build_range(x, y) != in_range:
            continue
        sx, sy = screen_of(game, x, y)
        if 0 <= sx < config.WINDOW_W and 0 <= sy < top:
            return x, y
    raise AssertionError("рядом с дроном нет подходящего тайла")


def drone_next_to_deposit(game):
    """Дрон на свободной земле вплотную к залежи, камера — на нём.

    Оттуда до залежи достают и добыча, и скан, и постановка бура.
    """
    sim = game.sim
    deposits = sorted((d for d in sim.world.deposits if not d.depleted),
                      key=lambda d: sim.drone.distance_to_tile(d.x, d.y))
    for deposit in deposits:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            x, y = deposit.x + dx, deposit.y + dy
            if not sim.world.is_walkable_tile(x, y) or sim.world.deposit_at(x, y):
                continue
            if sim.run.relic_at(x, y) is not None:
                continue
            sim.drone.x, sim.drone.y = x + 0.5, y + 0.5
            game.camera.snap_to(sim.drone.x, sim.drone.y)
            assert sim.in_mine_range(deposit) and sim.in_scan_range(deposit)
            return deposit
    raise AssertionError("на карте нет залежи со свободным тайлом рядом")


# ------------------------------------------------- хотбар: выбор слота
def test_digit_keys_select_hotbar_slot(game):
    """1…9 выбирают слот хотбара: «что строю» — это выбранный слот (plan/10)."""
    stock(game.sim)                      # постройки легли в слоты 0…7 по порядку
    assert game.sim.selected_slot == 0
    assert game.sim.selected_building() == LAMP
    key(game, pygame.K_2)
    assert game.sim.selected_slot == 1
    assert game.sim.selected_building() == LAB
    key(game, pygame.K_9)
    assert game.sim.selected_slot == 8
    assert game.sim.selected_building() is None, "девятый слот пуст"
    key(game, pygame.K_1)
    assert game.sim.selected_slot == 0


def test_click_on_hud_hotbar_selects_slot_and_does_not_mine(game, monkeypatch):
    """ЛКМ по слоту хотбара выбирает его; добыча под панелью при этом не идёт."""
    mods(monkeypatch)
    slot = game.hud.hotbar.slot_rects()[4]
    click_screen(game, slot.center)
    assert game.sim.selected_slot == 4
    hold_mouse(monkeypatch, slot.center, buttons=(1, 0, 1))
    game.poll_input()
    assert game.sim.mine_active is False, "над хотбаром кнопка мыши — не добыча"
    assert game.sim.scan_active is False
    assert game.ghost_tile() is None, "над хотбаром призрака нет"


def test_shift_wheel_scrolls_hotbar_and_plain_wheel_zooms(game, monkeypatch):
    """Колесо остаётся зумом; Shift+колесо листает слоты по кругу."""
    zoom = game.camera.zoom
    mods(monkeypatch, pygame.KMOD_LSHIFT)
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-1, x=0))
    assert game.sim.selected_slot == 1, "колесо вниз — следующий слот"
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1, x=0))
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1, x=0))
    assert game.sim.selected_slot == config.HOTBAR_SLOTS - 1, "по кругу"
    assert game.camera.zoom == pytest.approx(zoom), "Shift+колесо не зумит"

    mods(monkeypatch, 0)
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1, x=0))
    assert game.camera.zoom > zoom
    assert game.sim.selected_slot == config.HOTBAR_SLOTS - 1, "зум не трогает слот"


def test_shift_wheel_also_takes_the_horizontal_scroll(game, monkeypatch):
    """С Shift некоторые системы (macOS, тачпады) шлют прокрутку по `x`, а не по `y`."""
    zoom = game.camera.zoom
    mods(monkeypatch, pygame.KMOD_LSHIFT)
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=-1, y=0))
    assert game.sim.selected_slot in (1, config.HOTBAR_SLOTS - 1), "слот сменился"
    game.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=1, y=0))
    assert game.sim.selected_slot == 0, "обратная прокрутка вернула слот"
    assert game.camera.zoom == pytest.approx(zoom), "Shift+колесо не зумит"


# --------------------------------------------- постановка из хотбара (ПКМ)
def test_right_click_places_lamp_from_selected_slot(game, monkeypatch):
    """ПКМ по земле со слотом-лампой ставит лампу и тратит ровно один предмет."""
    mods(monkeypatch)
    give_building(game.sim, LAMP, 3)
    assert game.sim.selected_building() == LAMP
    tx, ty = next_to_drone(game)
    click_at(game, tx, ty, button=3)
    assert game.sim.light.lamp_at(tx, ty) is not None
    assert game.sim.cargo.count(LAMP) == 2
    click_at(game, tx, ty, button=3)          # тайл уже занят
    assert len(game.sim.light.lamps) == 1
    assert game.sim.cargo.count(LAMP) == 2, "неудачная постановка не тратит предмет"


def test_right_click_places_the_selected_building_only(game, monkeypatch):
    """ПКМ ставит то, что в выбранном слоте, а не «всё подряд»."""
    mods(monkeypatch)
    stock(game.sim)
    labs = game.sim.cargo.count(LAB)
    lamps = game.sim.cargo.count(LAMP)
    tx, ty = next_to_drone(game)
    key(game, pygame.K_2)
    click_at(game, tx, ty, button=3)
    assert game.sim.buildings.at(tx, ty) is not None
    assert game.sim.buildings.at(tx, ty).kind == LAB
    assert game.sim.light.lamp_at(tx, ty) is None, "ПКМ ставит выбранное, а не всё"
    assert game.sim.cargo.count(LAB) == labs - 1
    assert game.sim.cargo.count(LAMP) == lamps


def test_right_click_out_of_build_range_places_nothing(game, monkeypatch):
    """Тайл годится под лампу, но дрон до него не достаёт: ничего не меняется."""
    mods(monkeypatch)
    give_building(game.sim, LAMP, 1)
    tx, ty = find_tile(game, LAMP, in_range=False)
    before = game.sim.state_signature()
    click_at(game, tx, ty, button=3)
    assert game.sim.light.lamps == []
    assert game.sim.state_signature() == before, "предмет не потрачен"


def test_right_click_with_empty_slot_changes_nothing(game, monkeypatch):
    """Пустой слот и ПКМ по земле: ничего (plan/10)."""
    mods(monkeypatch)
    assert game.sim.selected_stack() is None
    tx, ty = next_to_drone(game)
    before = game.sim.state_signature()
    click_at(game, tx, ty, button=3)
    assert game.sim.state_signature() == before
    assert not game.item_window.visible


def test_right_click_with_resource_in_slot_changes_nothing(game, monkeypatch):
    """Ресурс в слоте — не постройка: ПКМ по земле его не тратит и ничего не ставит."""
    mods(monkeypatch)
    item = game.sim.run.items.natural[0].id
    assert game.sim.hotbar.add(item, 3) == 0
    assert game.sim.selected_stack().item_id == item
    assert game.sim.selected_building() is None
    tx, ty = next_to_drone(game)
    before = game.sim.state_signature()
    click_at(game, tx, ty, button=3)
    assert game.sim.state_signature() == before
    assert game.sim.hotbar.count(item) == 3


def test_rmb_on_a_red_ghost_does_nothing_and_says_nothing(game, monkeypatch):
    """Красный призрак — не команда: ни постройки, ни тоста «Здесь не построить».

    Проверка та же, что у призрака (`sim.can_place`): вне радиуса, на здании
    без контейнеров (провод) и на залежи, куда лампу не поставить, ПКМ молчит.
    """
    mods(monkeypatch)
    sim = game.sim
    deposit = drone_next_to_deposit(game)
    wire = find_tile(game, WIRE, near=1.0)
    assert place(sim, WIRE, *wire) is not None
    give_building(sim, LAMP, 2)
    assert sim.apply(SelectHotbar(slot_of(sim.hotbar, LAMP)))
    far = find_tile(game, LAMP, in_range=False)
    tiles = (far, wire, (deposit.x, deposit.y))
    sim.toasts.clear()
    before = sim.state_signature()
    for tile in tiles:
        assert not sim.can_place(LAMP, *tile), f"призрак на {tile} должен быть красным"
        click_at(game, *tile, button=3)
    assert sim.state_signature() == before
    assert sim.cargo.count(LAMP) == 2, "предмет не потрачен"
    assert [t.text for t in sim.toasts] == [], "красный призрак не тостит"
    assert not game.item_window.visible, "у провода контейнеров нет — окна нет"

    empty = next(i for i in range(sim.hotbar.size) if sim.hotbar.slot(i) is None)
    assert sim.apply(SelectHotbar(empty))
    before = sim.state_signature()
    click_at(game, *wire, button=3)
    assert sim.state_signature() == before and not sim.toasts
    assert not game.item_window.visible


def test_rmb_that_placed_the_last_drill_does_not_start_a_scan(game, monkeypatch):
    """Нажатие ПКМ, поставившее бур, до отпускания не становится удержанием.

    Последний бур ушёл из слота — слот пуст, а пустой слот с ПКМ на залежи —
    это скан. Без «потраченного» нажатия скан начинался сам, той же кнопкой.
    """
    mods(monkeypatch)
    sim = game.sim
    deposit = drone_next_to_deposit(game)
    assert sim.knowledge.unknown(deposit.resource_id), "скану есть что открыть"
    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST * 3)
    give_building(sim, DRILL, 1)
    assert sim.apply(SelectHotbar(slot_of(sim.hotbar, DRILL)))
    pos = screen_of(game, deposit.x, deposit.y)

    hold_mouse(monkeypatch, pos, buttons=(0, 0, 1))
    click_screen(game, pos, button=3)
    drill = sim.buildings.at(deposit.x, deposit.y)
    assert drill is not None and drill.kind == DRILL
    assert sim.selected_building() is None, "последний бур потрачен — слот пуст"
    for _ in range(ticks_in(config.SCAN_TIME * 2)):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.scan_active is False
    assert sim.scan_progress == 0.0 and sim.scans_done == 0
    assert sim.cargo.count(ENERGY_ITEM) == config.SCAN_ENERGY_COST * 3

    hold_mouse(monkeypatch, pos)                # отпустил
    game.poll_input()
    hold_mouse(monkeypatch, pos, buttons=(0, 0, 1))
    game.poll_input()
    assert sim.scan_active is True, "после отпускания ПКМ снова держится в мире"


def test_lmb_pressed_on_the_hotbar_does_not_mine_when_dragged_onto_the_map(
        game, monkeypatch):
    """ЛКМ выбрала слот хотбара — дотянутая до залежи, она не копает.

    Новое нажатие уже в мире — обычная добыча.
    """
    mods(monkeypatch)
    sim = game.sim
    deposit = drone_next_to_deposit(game)
    click_screen(game, game.hud.hotbar.slot_rects()[2].center)
    assert sim.selected_slot == 2
    pos = screen_of(game, deposit.x, deposit.y)
    hold_mouse(monkeypatch, pos, buttons=(1, 0, 0))   # кнопку не отпускали
    for _ in range(5):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.mine_active is False and sim.mine_progress == 0.0

    hold_mouse(monkeypatch, pos)
    game.poll_input()
    click_screen(game, pos)
    hold_mouse(monkeypatch, pos, buttons=(1, 0, 0))
    for _ in range(5):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.mine_active is True and sim.mine_progress > 0.0


def test_left_click_in_world_builds_nothing(game, monkeypatch):
    """Стройки ЛКМ-кликом больше нет: ЛКМ в мире — только удержание (poll_input)."""
    mods(monkeypatch)
    give_building(game.sim, LAMP, 1)
    tx, ty = next_to_drone(game)
    before = game.sim.state_signature()
    click_at(game, tx, ty, button=1)
    assert game.sim.state_signature() == before


def test_crafted_lamp_goes_to_hotbar_and_is_placed_by_right_click(game, monkeypatch):
    """Приёмка: скрафтил лампу в окне E, она в хотбаре, ПКМ по земле её ставит."""
    mods(monkeypatch)
    sim = game.sim
    luminous = sim.run.items.with_prop(Prop.LUMINOUS)[0].id
    sim.inventory.add(luminous, 10)
    key(game, pygame.K_e)
    window = game.item_window
    window._layout(sim)                       # раскладка ставит панель крафта
    options = window.craft.options(sim)
    index = next(i for i, o in enumerate(options) if o.recipe_id == LAMP)
    click_screen(game, window.craft.cell_rects(len(options))[index].center)
    assert sim.cargo.count(LAMP) == 1, "клик по рецепту крафтит одну лампу"
    slot = slot_of(sim.hotbar, LAMP)
    assert sim.inventory.count(luminous) < 10, "материалы потрачены"

    key(game, pygame.K_e)
    assert not window.visible
    key(game, pygame.K_1 + slot)
    tx, ty = next_to_drone(game)
    click_at(game, tx, ty, button=3)
    assert sim.light.lamp_at(tx, ty) is not None
    assert sim.cargo.count(LAMP) == 0, "предмет израсходован"


# ---------------------------------------------- разбор удержанием ЛКМ
def test_holding_left_button_dismantles_own_building(game, monkeypatch):
    """ЛКМ (удержание) по своей лампе: разбор, предмет падает на её тайл.

    Отпустил раньше — прогресс сброшен, лампа стоит. Лампа дальше радиуса
    подбора, чтобы упавший предмет было видно на земле.
    """
    mods(monkeypatch)
    tx, ty = find_tile(game, LAMP, near=config.PICKUP_RADIUS + 1.0)
    assert place(game.sim, LAMP, tx, ty) is not None
    pos = screen_of(game, tx, ty)

    hold_mouse(monkeypatch, pos, buttons=(1, 0, 0))
    game.poll_input()
    game.advance(config.DISMANTLE_TIME * 0.5)
    assert game.sim.dismantle_progress > 0.0
    hold_mouse(monkeypatch, pos)              # отпустил
    game.poll_input()
    game.advance(config.TICK)
    assert game.sim.dismantle_progress == 0.0
    assert game.sim.light.lamp_at(tx, ty) is not None, "прерванный разбор ничего не снял"

    hold_mouse(monkeypatch, pos, buttons=(1, 0, 0))
    game.poll_input()
    game.advance(config.DISMANTLE_TIME + config.TICK * 2)
    assert game.sim.light.lamp_at(tx, ty) is None
    assert game.sim.drops.total().get(LAMP, 0) == 1, "лампа лежит на земле предметом"


# ----------------------------------------------------------------- поворот
def test_r_rotates_the_ghost_of_selected_conveyor(game, monkeypatch):
    """R с конвейером в слоте крутит призрак — и конвейер встаёт так же."""
    mods(monkeypatch)
    stock(game.sim)
    key(game, pygame.K_4)
    assert game.sim.selected_building() == CONVEYOR
    tx, ty = next_to_drone(game)
    hold_mouse(monkeypatch, screen_of(game, tx, ty))
    assert game.sim.build_rotation is None
    key(game, pygame.K_r)
    first = game.sim.build_rotation
    assert first is not None
    key(game, pygame.K_r)
    assert game.sim.build_rotation == (first + 1) % len(DIRECTIONS)
    click_at(game, tx, ty, button=3)
    conveyor = game.sim.buildings.at(tx, ty)
    assert conveyor is not None and conveyor.kind == CONVEYOR
    assert conveyor.direction == DIRECTIONS[game.sim.build_rotation]


def test_r_without_ghost_rotates_conveyor_under_cursor(game, monkeypatch):
    """Пустой слот (призрака нет): R крутит уже стоящий конвейер под курсором."""
    tx, ty = next_to_drone(game)
    conveyor = place(game.sim, CONVEYOR, tx, ty)
    assert conveyor is not None
    assert game.sim.selected_building() is None
    hold_mouse(monkeypatch, screen_of(game, tx, ty))
    direction = conveyor.direction
    key(game, pygame.K_r)
    expected = DIRECTIONS[(DIRECTIONS.index(direction) + 1) % len(DIRECTIONS)]
    assert conveyor.direction == expected
    assert game.sim.build_rotation is None
    assert not game.item_window.visible, "R ничего не открывает"


def test_old_keys_do_nothing(game, monkeypatch):
    """T, I, Tab убраны: не крутят, не открывают окно. Книги рецептов нет."""
    stock(game.sim)
    tx, ty = next_to_drone(game)
    conveyor = place(game.sim, CONVEYOR, tx, ty)
    key(game, pygame.K_4)                     # конвейер в слоте: есть призрак
    hold_mouse(monkeypatch, screen_of(game, tx, ty))
    direction = conveyor.direction
    before = game.sim.state_signature()
    for code in (pygame.K_t, pygame.K_i, pygame.K_TAB):
        key(game, code)
        assert not game.item_window.visible, pygame.key.name(code)
    assert conveyor.direction == direction
    assert game.sim.build_rotation is None
    assert game.sim.state_signature() == before
    assert not hasattr(game, "book"), "книга рецептов удалена"


# ------------------------------------------------- окно инвентаря (E)
def test_e_opens_and_closes_the_drone_inventory(game):
    assert not game.item_window.visible
    key(game, pygame.K_e)
    assert game.item_window.mode == MODE_DRONE
    assert game.item_window.has_craft, "крафт живёт в окне инвентаря"
    game.draw_frame(config.TICK)
    key(game, pygame.K_e)
    assert not game.item_window.visible


def test_escape_closes_the_inventory_before_quitting(game):
    key(game, pygame.K_e)
    key(game, pygame.K_ESCAPE)
    assert not game.item_window.visible and game.running
    key(game, pygame.K_ESCAPE)
    assert game.running and game.menu.visible, "второй Esc — меню паузы, не выход"


def test_space_pauses_with_the_window_open(game):
    key(game, pygame.K_e)
    key(game, pygame.K_SPACE)
    assert game.paused and game.item_window.visible


def test_pause_plate_is_drawn_over_the_open_window(game, monkeypatch):
    """«ПАУЗА» — самый верхний слой: окно предметов и хотбар её не закрывают."""
    drawn: list[str] = []
    original = widgets.draw_text

    def record(target, value, *args, **kwargs):
        drawn.append(value)
        return original(target, value, *args, **kwargs)

    monkeypatch.setattr(widgets, "draw_text", record)
    key(game, pygame.K_e)
    key(game, pygame.K_SPACE)
    assert game.paused and game.item_window.visible
    game.render(config.TICK)
    title = game.item_window.title
    assert title in drawn and "ПАУЗА" in drawn
    last_title = max(i for i, value in enumerate(drawn) if value == title)
    assert drawn.index("ПАУЗА") > last_title, "пауза нарисована под окном"
    assert drawn[-2:] == ["ПАУЗА", "Пробел — продолжить"], "после паузы ничего нет"


def test_item_window_swallows_clicks(game, monkeypatch):
    """Пока окно открыто, ПКМ по карте не ставит постройку из слота."""
    mods(monkeypatch)
    stock(game.sim)
    key(game, pygame.K_e)
    tx, ty = next_to_drone(game)
    lamps = game.sim.cargo.count(LAMP)
    click_at(game, tx, ty, button=3)
    click_at(game, tx, ty, button=1)
    assert game.sim.light.lamp_at(tx, ty) is None
    assert game.sim.buildings.buildings == []
    assert game.sim.cargo.count(LAMP) == lamps


def test_item_window_blocks_mining_and_scanning(game, monkeypatch):
    """Иначе крафт в окне и добыча в мире срабатывали бы одной кнопкой."""
    key(game, pygame.K_e)
    hold_mouse(monkeypatch, (10, 10), buttons=(1, 0, 1))
    game.poll_input()
    assert game.sim.mine_active is False
    assert game.sim.scan_active is False
    assert game.ghost_tile() is None, "при открытом окне призрака нет"
    key(game, pygame.K_e)
    game.poll_input()
    assert game.sim.mine_active is True and game.sim.scan_active is True


def test_q_throws_the_stack_away(game, monkeypatch):
    """Выбросить предмет можно клавишей Q по слоту под курсором."""
    item = game.sim.run.items.natural[0].id
    game.sim.inventory.add(item, 3)
    key(game, pygame.K_e)
    panels = game.item_window._layout(game.sim)[1]
    panel = next(p for p in panels if p.container is game.sim.inventory)
    slot = panel.grid.rects(panel.origin, game.sim.inventory.size)[
        slot_of(game.sim.inventory, item)]
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: slot.center)
    key(game, pygame.K_q)
    assert game.sim.inventory.count(item) == 0
    assert game.sim.drops.total().get(item, 0) == 3, "предмет упал на землю"


# ------------------------------------------------ поле поиска и клавиши
def test_search_field_takes_the_keyboard(game, monkeypatch):
    """Пока поиск в фокусе: WASD не двигает, E не закрывает, цифры и R не действуют.

    Esc сначала снимает фокус, второй Esc закрывает окно (plan/10).
    """
    mods(monkeypatch)
    stock(game.sim)
    key(game, pygame.K_4)                     # конвейер: R крутил бы призрак
    key(game, pygame.K_e)
    window = game.item_window
    window._layout(game.sim)
    click_screen(game, window.craft.search.rect.center)
    assert game.ui.text_input_active

    class FakeKeys:
        def __getitem__(self, code):
            return code in (pygame.K_d, pygame.K_w)

    hold_mouse(monkeypatch, (10, 10), keys=FakeKeys())
    game.poll_input()
    assert game.sim.move_input == (0.0, 0.0), "WASD — это буквы, а не движение"
    for code in (pygame.K_e, pygame.K_2, pygame.K_r, pygame.K_SPACE):
        key(game, code)
    assert window.visible and game.sim.selected_slot == 3
    assert game.sim.build_rotation is None and not game.paused
    game.handle_event(pygame.event.Event(pygame.TEXTINPUT, text="Ла"))
    assert window.craft.search.text == "Ла"

    key(game, pygame.K_ESCAPE)
    assert not game.ui.text_input_active and window.visible
    key(game, pygame.K_ESCAPE)
    assert not window.visible and game.running


def test_click_outside_the_search_field_drops_focus(game, monkeypatch):
    mods(monkeypatch)
    key(game, pygame.K_e)
    window = game.item_window
    x, y, _w, _h = window._layout(game.sim)[0]
    click_screen(game, window.craft.search.rect.center)
    assert game.ui.text_input_active
    click_screen(game, (x + 2, y + 2))        # угол окна: мимо поля
    assert not game.ui.text_input_active and window.visible


# ---------------------------------------------- окна зданий (ПКМ)
def place_lab(game) -> tuple[int, int]:
    """Лаборатория на соседнем тайле, поставленная ПКМ из хотбара.

    Без сети: она нужна не каждому тесту.
    """
    stock(game.sim)
    tx, ty = next_to_drone(game)
    key(game, pygame.K_2)
    assert game.sim.selected_building() == LAB
    click_at(game, tx, ty, button=3)
    assert game.sim.buildings.at(tx, ty) is not None, "лаборатория не встала"
    return tx, ty


def test_right_click_on_a_lab_opens_its_slots_whatever_the_slot(game, monkeypatch):
    """ПКМ по зданию с контейнерами — его окно, что бы ни лежало в слоте."""
    mods(monkeypatch)
    tx, ty = place_lab(game)
    for code in (pygame.K_1, pygame.K_2, pygame.K_9):   # лампа, лаборатория, пусто
        key(game, code)
        hotbar = game.sim.hotbar.items()
        click_at(game, tx, ty, button=3)
        assert game.item_window.mode == MODE_CONTAINER
        assert game.item_window.building is game.sim.buildings.at(tx, ty)
        assert not game.item_window.has_craft, "в окне здания крафта нет"
        assert game.sim.hotbar.items() == hotbar, "ничего не потрачено"
        key(game, pygame.K_ESCAPE)
    assert len(game.sim.buildings.buildings) == 1 and game.sim.light.lamps == []


def test_right_click_elsewhere_opens_nothing(game):
    click_at(game, int(game.sim.drone.x) + 1, int(game.sim.drone.y), button=3)
    assert not game.item_window.visible


def test_lab_window_closes_when_the_drone_walks_away(game, monkeypatch):
    tx, ty = place_lab(game)
    click_at(game, tx, ty, button=3)
    assert game.item_window.visible
    game.sim.drone.x, game.sim.drone.y = tx + config.CONTAINER_RANGE + 5, ty + 0.5
    hold_mouse(monkeypatch, (10, 10))
    game.poll_input()
    assert not game.item_window.visible, "окно живёт, пока дрон рядом"


def test_lab_window_closes_when_the_lab_is_dismantled(game):
    """Окно живёт ровно столько, сколько существует его лаборатория."""
    tx, ty = place_lab(game)
    click_at(game, tx, ty, button=3)
    assert game.item_window.visible
    assert game.sim.dismantle_building(tx, ty)   # разбор мимо окна
    assert game.sim.buildings.at(tx, ty) is None
    game.poll_input()
    assert not game.item_window.visible


# ------------------------------------------- лаборатория без окна (M3.5)
def lab_with_sample(game, count: int = 1):
    """Работающая лаборатория рядом с дроном и образец, лежащий в её слоте.

    Сеть обязательна: с M4 без энергии слот показывает «нет энергии» и образец
    ждёт (plan/02). Рядом с лабораторией встаёт заправленный генератор.
    """
    tx, ty = place_lab(game)
    power_grid(game.sim, tx, ty)
    lab = game.sim.buildings.at(tx, ty)
    item = next(i for i in game.sim.run.items.natural
                if game.sim.knowledge.unknown(i.id))
    assert give(Stack(item.id, count), lab.container(LAB_SAMPLES)) is None
    return lab, item


def test_analysis_runs_while_the_window_is_closed_and_the_drone_is_away(game):
    """Приёмка M3.5: положить образец, закрыть окно, уйти — свойства откроются."""
    lab, item = lab_with_sample(game)
    click_at(game, lab.x, lab.y, button=3)
    assert game.item_window.visible, "окно лаборатории открыто"
    game.advance(config.LAB_ANALYZE_TIME * 0.5)
    assert 0.4 < lab.progress < 0.6, "половина анализа прошла при открытом окне"

    key(game, pygame.K_ESCAPE)
    game.sim.drone.x += config.CONTAINER_RANGE + 5   # дрон ушёл по делам
    assert not game.item_window.visible
    game.advance(config.LAB_ANALYZE_TIME * 0.6)
    assert game.sim.knowledge.unknown(item.id) == (), "лаборатория не досчитала"


def test_pause_freezes_the_lab_analysis(game):
    """Пауза — это отсутствие update: за паузой образец не анализируется."""
    lab, item = lab_with_sample(game)
    game.advance(config.LAB_ANALYZE_TIME * 0.5)
    progress = lab.progress
    game.paused = True
    game.advance(config.LAB_ANALYZE_TIME * 5)
    assert lab.progress == progress and lab.processed == 0
    game.paused = False
    game.advance(config.LAB_ANALYZE_TIME * 0.6)
    assert game.sim.knowledge.unknown(item.id) == ()


def test_sample_can_be_put_in_by_the_mouse(game, monkeypatch):
    """Игрок кладёт образец мышью (Shift+ЛКМ): окно лишь посылает приказ в sim."""
    tx, ty = place_lab(game)
    lab = game.sim.buildings.at(tx, ty)
    samples = lab.container(LAB_SAMPLES)
    item = game.sim.run.items.natural[0].id
    game.sim.inventory.add(item, 2)
    click_at(game, tx, ty, button=3)
    window = game.item_window
    panels = window._layout(game.sim)[1]
    drone_panel = next(p for p in panels if p.container is game.sim.inventory)
    lab_panel = next(p for p in panels if p.container is samples)
    # Слот ищем по предмету: запас ролей от `place_lab` занял первые слоты.
    slot = slot_of(game.sim.inventory, item)
    from_drone = drone_panel.grid.rects(drone_panel.origin,
                                        game.sim.inventory.size)[slot].center
    to_lab = lab_panel.grid.rects(lab_panel.origin, samples.size)[0].center

    mods(monkeypatch, pygame.KMOD_LSHIFT)
    click_screen(game, from_drone)
    assert samples.count(item) == 2
    assert game.sim.inventory.count(item) == 0, "переложено, а не скопировано"
    mods(monkeypatch, 0)
    click_screen(game, to_lab)
    assert window.held is not None and window.held.count == 2, "стак взят в руку"
    assert game.sim.buildings.at(tx, ty) is lab, "клик по окну не ушёл в мир"
    key(game, pygame.K_ESCAPE)
    assert samples.count(item) == 2, "предмет вернулся в слот"


# ----------------------------------------------------------- призрак
def test_ghost_tile_follows_cursor_over_the_map(game, monkeypatch):
    """Призрак — тайл под курсором, пока курсор над картой и окна закрыты."""
    tx, ty = next_to_drone(game)
    hold_mouse(monkeypatch, screen_of(game, tx, ty))
    assert game.ghost_tile() == (tx, ty)
    key(game, pygame.K_F4)                    # галерея — режим просмотра
    assert game.ghost_tile() is None


def test_hud_panels_take_the_cursor_after_a_frame(game, monkeypatch):
    """Над панелью HUD мир не виден: ни призрака, ни добычи, ни скана (plan/10).

    Места панелей запоминает кадр, который их нарисовал, — до первого кадра
    их нет. Строка подсказки у курсора — не панель: она мир не закрывает.
    """
    mods(monkeypatch)
    sim = game.sim
    hud = game.hud
    give_building(sim, LAMP, 1)
    deposit = drone_next_to_deposit(game)
    hold_mouse(monkeypatch, screen_of(game, deposit.x, deposit.y))
    game.poll_input()                           # курсор на залежи — есть подсказка
    game.draw_frame(config.TICK)
    m = config.UI_MARGIN
    panels = {
        "статус": (m + 5, m + 5),
        "время": (config.WINDOW_W // 2, m + 5),
        "сводка инвентаря": (m + 5, config.WINDOW_H - m - 5),
    }
    for name, pos in panels.items():
        assert hud.over_ui(pos), name
        hold_mouse(monkeypatch, pos, buttons=(1, 0, 1))
        assert game.ghost_tile() is None, f"призрак под панелью «{name}»"
        game.poll_input()
        assert sim.mine_active is False and sim.scan_active is False, name

    prompt = (config.WINDOW_W // 2, hud.hotbar.rect.top - PROMPT_ABOVE_HOTBAR + 13)
    assert hud._prompt_for(sim)[0] is not None
    assert not hud.over_ui(prompt), "строка подсказки — не панель"
    tile = next_to_drone(game)
    pos = screen_of(game, tile[0], tile[1])
    assert not hud.over_ui(pos)
    hold_mouse(monkeypatch, pos)
    assert game.ghost_tile() == tile, "над картой призрак есть"


# -------------------------------------------------------------- ввод в sim
def test_right_button_starts_scanning(game, monkeypatch):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: [False] * 512)
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: (0, 0, 1))
    game.sim.set_scanning(True)
    game.poll_input()
    assert game.sim.scan_active is True


def test_poll_input_reads_keys(game, monkeypatch):
    class FakeKeys:
        def __getitem__(self, key):
            return key == pygame.K_d

    monkeypatch.setattr(pygame.key, "get_pressed", lambda: FakeKeys())
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: (1, 0, 0))
    game.poll_input()
    assert game.sim.move_input == (1.0, 0.0)
    assert game.sim.mine_active is True


def test_left_button_release_clears_mining(game, monkeypatch):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: [False] * 512)
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: (0, 0, 0))
    game.sim.set_mining(True)
    game.poll_input()
    assert game.sim.mine_active is False


def test_cursor_over_deposit_is_resolved_to_that_deposit(game, monkeypatch):
    """Экранная точка курсора → тайл → залежь. Проверяем весь путь ввода."""
    deposit = game.sim.world.deposits[0]
    sx, sy = game.camera.world_to_screen(deposit.x + 0.5, deposit.y + 0.5)
    hold_mouse(monkeypatch, (int(sx), int(sy)))
    game.poll_input()
    assert game.sim.cursor_deposit is deposit


def test_cursor_over_empty_ground_finds_nothing(game, monkeypatch):
    drone = game.sim.drone
    sx, sy = game.camera.world_to_screen(drone.x, drone.y)
    hold_mouse(monkeypatch, (int(sx), int(sy)))
    game.poll_input()
    assert game.sim.cursor_deposit is None


# ------------------------------------------------------------------ кадры
def test_draw_frame_renders_without_error(game):
    for _ in range(5):
        game.draw_frame(config.TICK)
    assert game.sim.time > 0
    assert game.frame_ms >= 0.0


def test_camera_follows_drone(game):
    """В движении камера отстаёт на постоянную величину, на месте — сходится."""
    game.sim.set_move_input(1.0, 0.0)
    for _ in range(60):
        game.draw_frame(config.TICK)
    lag = game.sim.drone.x - game.camera.x
    assert 0.0 < lag < config.DRONE_SPEED / config.CAMERA_SMOOTH + 0.1
    game.sim.set_move_input(0.0, 0.0)
    for _ in range(180):
        game.draw_frame(config.TICK)
    assert game.camera.x == pytest.approx(game.sim.drone.x, abs=0.01)
    assert game.camera.y == pytest.approx(game.sim.drone.y, abs=0.01)


def test_camera_stays_inside_world(game):
    for _ in range(600):
        game.draw_frame(config.TICK)
    hw, hh = game.camera.half_extent
    assert hw - 1e-6 <= game.camera.x <= game.sim.world.width - hw + 1e-6
    assert hh - 1e-6 <= game.camera.y <= game.sim.world.height - hh + 1e-6


def test_seed_argument_is_used():
    game = Game(seed=777)
    try:
        assert game.seed == 777
        assert game.sim.seed == 777
        assert game.sim.world.seed == 777
    finally:
        game.close()


def test_skip_to_night_makes_it_dark(game):
    """Отладочный прыжок в ночь: иначе первую ночь пришлось бы ждать 6 минут."""
    assert not game.sim.time_cycle.is_night
    game.skip_to_night()
    assert game.sim.time_cycle.is_night
    assert game.sim.darkness == pytest.approx(1.0)
    game.draw_frame(config.TICK)
    assert game.renderer.overlay.draws > 0, "ночью тьма должна рисоваться"


def test_random_seed_when_not_given():
    game = Game()
    try:
        assert 1 <= game.seed < 2 ** 31
    finally:
        game.close()


def test_r_over_a_hud_panel_does_nothing(game, monkeypatch):
    """Над панелью HUD мира не видно: R не крутит конвейер, спрятанный под ней."""
    mods(monkeypatch)
    tx, ty = next_to_drone(game)
    conveyor = place(game.sim, CONVEYOR, tx, ty)
    assert conveyor is not None and game.sim.selected_building() is None
    game.draw_frame(config.TICK)                 # кадр запоминает места панелей
    panel = (config.UI_MARGIN + 5, config.UI_MARGIN + 5)
    assert game.hud.over_ui(panel)
    # Курсор над панелью, а «тайл под курсором» подменён на тайл конвейера: так
    # видно, что R отказала именно из-за панели, а не из-за пустого тайла.
    hold_mouse(monkeypatch, panel)
    monkeypatch.setattr(game, "tile_at_screen", lambda pos=None: (tx, ty))
    direction = conveyor.direction
    key(game, pygame.K_r)
    assert conveyor.direction == direction
