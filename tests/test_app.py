"""Приложение: главный цикл, пауза, ввод.

Тесты идут в dummy-режиме SDL (см. conftest), окно не открывается.
"""

from __future__ import annotations

import pygame
import pytest

from src.game import config
from src.game.app import Game
from src.game.data.buildings import LAB, LAB_SAMPLES
from src.game.sim.resources import Stack, give
from tests.conftest import SEED, power_grid, slot_of, stock


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


def test_escape_stops_game(game):
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
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


# ------------------------------------------------------------------- лампы
def click_at(game, tx: int, ty: int, button: int = 1) -> None:
    """Клик мышью по центру тайла (по умолчанию ЛКМ)."""
    sx, sy = game.camera.world_to_screen(tx + 0.5, ty + 0.5)
    game.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button,
                                         pos=(int(sx), int(sy))))


def test_click_places_lamp_and_click_again_removes_it(game):
    stock(game.sim)                      # с M4 лампа стоит материалов
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    assert game.sim.world.is_walkable_tile(tx, ty)
    click_at(game, tx, ty)
    assert game.sim.light.lamp_at(tx, ty) is not None
    click_at(game, tx, ty)
    assert game.sim.light.lamp_at(tx, ty) is None


def test_click_elsewhere_places_nothing(game):
    drone_tx, drone_ty = game.sim.drone.tile()
    far_x = drone_tx + int(config.LAMP_PLACE_RANGE) + 3
    click_at(game, far_x, drone_ty)
    assert game.sim.light.lamps == []


# ------------------------------------------------------- постройки (M3)
def key(game, code: int) -> None:
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=code))


def test_digit_keys_select_what_to_build(game):
    assert game.sim.selected_build == "lamp", "по умолчанию лампа (поведение M2)"
    key(game, pygame.K_2)
    assert game.sim.selected_build == LAB
    key(game, pygame.K_1)
    assert game.sim.selected_build == "lamp"


def test_click_places_selected_building(game):
    stock(game.sim)                      # лаборатория тоже стоит материалов
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    key(game, pygame.K_2)
    click_at(game, tx, ty)
    assert game.sim.buildings.at(tx, ty) is not None
    assert game.sim.light.lamp_at(tx, ty) is None, "клик ставит выбранное, а не всё"
    click_at(game, tx, ty)
    assert game.sim.buildings.at(tx, ty) is None, "повторный клик сносит"


def test_right_button_starts_scanning(game, monkeypatch):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: [False] * 512)
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: (0, 0, 1))
    game.sim.set_scanning(True)
    game.poll_input()
    assert game.sim.scan_active is True


# -------------------------------------------------------------- ввод в sim
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


def hold_mouse(monkeypatch, pos, buttons=(0, 0, 0)):
    """Мышь в заданной экранной точке (и с зажатыми кнопками)."""
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: pos)
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: buttons)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: [False] * 512)


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


# ------------------------------------------------------- книга рецептов
def test_r_toggles_recipe_book(game):
    assert not game.book.visible
    key(game, pygame.K_r)
    assert game.book.visible
    game.draw_frame(config.TICK)
    key(game, pygame.K_r)
    assert not game.book.visible


def test_escape_closes_book_before_quitting(game):
    key(game, pygame.K_r)
    key(game, pygame.K_ESCAPE)
    assert not game.book.visible and game.running
    key(game, pygame.K_ESCAPE)
    assert not game.running


def test_book_swallows_clicks(game, monkeypatch):
    """Пока книга открыта, клик по ней не должен ничего строить в мире."""
    key(game, pygame.K_r)
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    click_at(game, tx, ty)
    assert game.sim.light.lamp_at(tx, ty) is None
    assert game.sim.buildings.buildings == []


def test_book_blocks_mining_and_scanning(game, monkeypatch):
    """Иначе крафт в окне и добыча в мире срабатывали бы одной кнопкой."""
    key(game, pygame.K_r)
    hold_mouse(monkeypatch, (10, 10), buttons=(1, 0, 1))
    game.poll_input()
    assert game.sim.mine_active is False
    assert game.sim.scan_active is False
    key(game, pygame.K_r)
    game.poll_input()
    assert game.sim.mine_active is True and game.sim.scan_active is True


# ------------------------------------------------- окно слотов (M3.5)
def place_lab(game) -> tuple[int, int]:
    """Лаборатория на соседнем тайле. Без сети: она нужна не каждому тесту."""
    stock(game.sim)
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    key(game, pygame.K_2)
    click_at(game, tx, ty)
    return tx, ty


def test_i_opens_the_drone_inventory(game):
    from src.game.ui.item_window import MODE_DRONE

    assert not game.item_window.visible
    key(game, pygame.K_i)
    assert game.item_window.mode == MODE_DRONE
    game.draw_frame(config.TICK)
    key(game, pygame.K_i)
    assert not game.item_window.visible


def test_escape_closes_the_inventory_before_quitting(game):
    key(game, pygame.K_i)
    key(game, pygame.K_ESCAPE)
    assert not game.item_window.visible and game.running
    key(game, pygame.K_ESCAPE)
    assert not game.running


def test_right_click_on_a_lab_opens_its_slots(game):
    from src.game.ui.item_window import MODE_CONTAINER

    tx, ty = place_lab(game)
    click_at(game, tx, ty, button=3)
    assert game.item_window.mode == MODE_CONTAINER
    assert game.item_window.building is game.sim.buildings.at(tx, ty)


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


def test_lab_window_closes_when_the_lab_is_removed(game):
    """Окно живёт ровно столько, сколько существует его лаборатория."""
    tx, ty = place_lab(game)
    click_at(game, tx, ty, button=3)
    assert game.item_window.visible
    assert game.sim.click_at(tx, ty) == "building_removed"   # снос мимо окна
    assert game.sim.buildings.at(tx, ty) is None
    game.poll_input()
    assert not game.item_window.visible


def test_item_window_swallows_clicks(game):
    key(game, pygame.K_i)
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    click_at(game, tx, ty)
    assert game.sim.light.lamp_at(tx, ty) is None
    assert game.sim.buildings.buildings == []


def test_item_window_blocks_mining_and_scanning(game, monkeypatch):
    key(game, pygame.K_i)
    hold_mouse(monkeypatch, (10, 10), buttons=(1, 0, 1))
    game.poll_input()
    assert game.sim.mine_active is False
    assert game.sim.scan_active is False


def test_books_and_windows_do_not_stay_open_together(game):
    key(game, pygame.K_i)
    key(game, pygame.K_r)
    assert game.book.visible and not game.item_window.visible
    key(game, pygame.K_i)
    assert game.item_window.visible and not game.book.visible


def test_tab_opens_the_inventory_too(game):
    """I и Tab — одно и то же окно: у игрока обе привычки (plan/03)."""
    from src.game.ui.item_window import MODE_DRONE

    key(game, pygame.K_TAB)
    assert game.item_window.visible and game.item_window.mode == MODE_DRONE
    key(game, pygame.K_TAB)
    assert not game.item_window.visible


def test_q_throws_the_stack_away(game, monkeypatch):
    """Выбросить предмет можно клавишей Q по слоту под курсором."""
    item = game.sim.run.items.natural[0].id
    game.sim.inventory.add(item, 3)
    key(game, pygame.K_i)
    panel = game.item_window._layout(game.sim)[1][0]
    slot = panel.grid.rects(panel.origin, game.sim.inventory.size)[0]
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: slot.center)
    key(game, pygame.K_q)
    assert game.sim.inventory.count(item) == 0
    assert game.sim.drops.total().get(item, 0) == 3, "предмет упал на землю"


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


def test_sample_can_be_put_in_by_the_mouse(game):
    """Игрок кладёт образец мышью: окно лишь посылает приказ в sim."""
    tx, ty = place_lab(game)
    lab = game.sim.buildings.at(tx, ty)
    item = game.sim.run.items.natural[0].id
    game.sim.inventory.add(item, 2)
    click_at(game, tx, ty, button=3)
    window = game.item_window
    _rect, panels = window._layout(game.sim)
    # Слот ищем по предмету: запас ролей от `place_lab` занял первые слоты.
    slot = slot_of(game.sim.inventory, item)
    from_drone = panels[1].grid.rects(panels[1].origin, game.sim.inventory.size)[slot].center
    to_lab = panels[0].grid.rects(panels[0].origin, lab.container(LAB_SAMPLES).size)[0].center
    window.click(from_drone, 1, game.sim, shift=True)
    assert lab.container(LAB_SAMPLES).count(item) == 2
    assert game.sim.inventory.count(item) == 0, "переложено, а не скопировано"
    assert window.click(to_lab, 1, game.sim), "клик по слоту окна поглощён окном"
    assert window.held.count == 2, "стак взят в руку"
    window.close(game.sim)
    assert lab.container(LAB_SAMPLES).count(item) == 2, "предмет вернулся в слот"
