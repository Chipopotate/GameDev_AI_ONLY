"""Управление (plan/10): одна таблица на документ и игру, старых клавиш нет, ввод текста.

Ввод проверяется целиком через `Game.handle_event` и `Game.poll_input`, как в
`tests/test_app.py`: мышь и клавиатура подменяются через monkeypatch.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pygame
import pytest

from src.game import config
from src.game.app import Game
from src.game.data.buildings import CONVEYOR, LAMP
from src.game.sim.simulation import ENERGY_ITEM
from src.game.ui import hud, widgets
from src.game.ui.controls import CONTROLS, hint_lines
from tests.conftest import SEED, place, stock

DOC = Path(__file__).resolve().parents[1] / "plan" / "10_controls.md"
HEADER = ("Клавиша", "Действие")


@pytest.fixture
def game():
    g = Game(seed=SEED)
    yield g
    g.close()


# ------------------------------------------------------------------ хелперы
def doc_table() -> tuple[tuple[str, str], ...]:
    """Первая markdown-таблица «| Клавиша | Действие |» из plan/10."""
    rows: list[tuple[str, ...]] = []
    inside = False
    for line in DOC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if not inside:
            inside = line.startswith("|") and cells == HEADER
            continue
        if not line.startswith("|"):
            break
        if all(set(cell) <= set("-: ") for cell in cells):
            continue                              # строка-разделитель |---|---|
        rows.append(cells)
    assert inside, "в plan/10 нет таблицы «| Клавиша | Действие |»"
    return tuple(rows)


def key(game, code: int) -> None:
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=code))


def text(game, value: str) -> None:
    game.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=value))


def click_screen(game, pos: tuple[int, int], button: int = 1) -> None:
    game.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos))


def screen_of(game, tx: int, ty: int) -> tuple[int, int]:
    sx, sy = game.camera.world_to_screen(tx + 0.5, ty + 0.5)
    return int(sx), int(sy)


class Keys:
    """Подмена `pygame.key.get_pressed`: зажаты только перечисленные клавиши."""

    def __init__(self, *codes: int) -> None:
        self.codes = set(codes)

    def __getitem__(self, code: int) -> bool:
        return code in self.codes


def hold(monkeypatch, pos, buttons=(0, 0, 0), keys: Keys | None = None,
         mods: int = 0) -> None:
    """Мышь в экранной точке, зажатые кнопки, клавиши и модификаторы."""
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: pos)
    monkeypatch.setattr(pygame.mouse, "get_pressed", lambda: buttons)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys or Keys())
    monkeypatch.setattr(pygame.key, "get_mods", lambda: mods)


def next_to_drone(game) -> tuple[int, int]:
    tx, ty = int(game.sim.drone.x) + 1, int(game.sim.drone.y)
    assert game.sim.can_place(LAMP, tx, ty), "у точки старта должна быть земля"
    return tx, ty


def drone_at_deposit(game):
    """Дрон рядом с ближайшей залежью, камера — на нём. Возвращает залежь.

    Ставим дрон на свободную землю в тайле от залежи: оттуда достаёт и
    добыча, и скан.
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
            assert sim.in_mine_range(deposit) and sim.in_scan_range(deposit)
            game.camera.snap_to(sim.drone.x, sim.drone.y)
            return deposit
    raise AssertionError("на карте нет залежи со свободным тайлом рядом")


def open_and_focus_search(game) -> None:
    """E, затем клик по полю поиска: клавиатура уходит в поле."""
    key(game, pygame.K_e)
    window = game.item_window
    assert window.visible and window.has_craft
    window._layout(game.sim)                     # раскладка ставит поле на место
    click_screen(game, window.craft.search.rect.center)
    assert game.ui.text_input_active


# ------------------------------------------------- таблица = код = подсказка
def test_doc_table_equals_controls_row_by_row():
    """plan/10 — единый источник: таблица в документе совпадает с кодом построчно."""
    table = doc_table()
    assert len(table) == len(CONTROLS)
    for i, (row, expected) in enumerate(zip(table, CONTROLS)):
        assert row == expected, f"строка {i + 1}: документ {row} ≠ код {expected}"


def test_controls_cover_the_new_keys_only():
    """В таблице есть E и R, а старых I, Tab и T нет."""
    keys = [k for k, _action in CONTROLS]
    assert "E" in keys and "R" in keys
    for old in ("I", "Tab", "T", "I / Tab", "1…8"):
        assert old not in keys, old
    assert all(len(row) == 2 and row[0] and row[1] for row in CONTROLS)


def test_hint_lines_are_built_from_the_table():
    assert hint_lines() == tuple(f"{k} — {a}" for k, a in CONTROLS)
    assert hud.CONTROLS == hint_lines(), "HUD берёт подсказку из ui/controls.py"


def test_hud_draws_the_controls_table(game, monkeypatch):
    """HUD рисует ровно строки таблицы (с переносом по словам); с окном — прячет."""
    drawn: list[str] = []
    original = widgets.draw_text

    def record(target, value, *args, **kwargs):
        drawn.append(value)
        return original(target, value, *args, **kwargs)

    monkeypatch.setattr(widgets, "draw_text", record)
    game.hud.draw(game.screen, game.sim, game.camera, 60.0)
    parts = [line for line, _indent in game.hud._control_lines()]
    assert " ".join(parts) == " ".join(hint_lines()), "перенос не теряет слов"
    start = drawn.index(parts[0])
    assert drawn[start:start + len(parts)] == parts, "таблица нарисована целиком"

    drawn.clear()
    game.hud.draw(game.screen, game.sim, game.camera, 60.0, window_open=True)
    assert not set(parts) & set(drawn), "при открытом окне таблица скрыта"


# ------------------------------------------------------- старых клавиш нет
def test_i_tab_t_do_nothing(game, monkeypatch):
    """I, Tab, T не открывают окно, не закрывают его и не крутят конвейер."""
    tx, ty = next_to_drone(game)
    conveyor = place(game.sim, CONVEYOR, tx, ty)
    assert conveyor is not None
    hold(monkeypatch, screen_of(game, tx, ty))
    direction = conveyor.direction
    before = game.sim.state_signature()
    for code in (pygame.K_i, pygame.K_TAB, pygame.K_t):
        key(game, code)
        assert not game.item_window.visible, pygame.key.name(code)
    assert conveyor.direction == direction, "T больше не крутит"
    assert game.sim.state_signature() == before

    key(game, pygame.K_e)
    for code in (pygame.K_i, pygame.K_TAB, pygame.K_t):
        key(game, code)
        assert game.item_window.visible, f"{pygame.key.name(code)} закрыл окно"


def test_r_opens_no_window(game, monkeypatch):
    """Книги рецептов нет: R ничего не открывает, а без цели — ничего не меняет."""
    hold(monkeypatch, (10, 10))
    before = game.sim.state_signature()
    key(game, pygame.K_r)
    assert not game.item_window.visible
    assert game.sim.state_signature() == before
    assert game.sim.build_rotation is None
    assert not hasattr(game, "book"), "окна книги рецептов больше нет"
    assert importlib.util.find_spec("src.game.ui.recipe_book") is None


def test_digits_are_hotbar_slots_not_a_build_mode(game, monkeypatch):
    """1–8 выбирают слот; бесплатной стройки «режимом» больше нет (plan/03)."""
    sim = game.sim
    for name in ("selected_build", "select_build", "click_at", "build_cost",
                 "can_afford", "place_lamp", "remove_building_at"):
        assert not hasattr(sim, name), f"старый API стройки: {name}"
    hold(monkeypatch, (10, 10))
    tx, ty = next_to_drone(game)
    for i in range(8):
        key(game, pygame.K_1 + i)
        assert sim.selected_slot == i
        assert sim.selected_building() is None, "пустой хотбар — строить нечего"
        before = sim.state_signature()
        click_screen(game, screen_of(game, tx, ty), button=3)
        click_screen(game, screen_of(game, tx, ty), button=1)
        assert sim.state_signature() == before, f"клавиша {i + 1} построила даром"
    assert sim.buildings.buildings == [] and sim.light.lamps == []


# --------------------------------------------------------------- ввод текста
def test_search_focus_takes_the_keyboard(game, monkeypatch):
    """Пока поиск в фокусе: WASD, E, цифры, R и Пробел не действуют в игре."""
    sim = game.sim
    stock(sim)
    key(game, pygame.K_4)                        # конвейер: R крутил бы призрак
    assert sim.selected_building() == CONVEYOR
    open_and_focus_search(game)
    window = game.item_window

    hold(monkeypatch, window.craft.search.rect.center, keys=Keys(pygame.K_w))
    before = sim.state_signature()
    key(game, pygame.K_w)
    game.poll_input()
    assert sim.move_input == (0.0, 0.0), "W — буква, а не движение"
    assert sim.state_signature() == before

    key(game, pygame.K_e)
    assert window.visible, "E в поле поиска окно не закрывает"
    key(game, pygame.K_1)
    assert sim.selected_slot == 3, "цифры не выбирают слот"
    key(game, pygame.K_r)
    assert sim.build_rotation is None, "R не крутит"
    key(game, pygame.K_SPACE)
    assert not game.paused, "Пробел — не пауза"
    assert game.ui.text_input_active and game.running


def test_drone_does_not_move_while_typing(game, monkeypatch):
    sim = game.sim
    open_and_focus_search(game)
    hold(monkeypatch, game.item_window.craft.search.rect.center,
         keys=Keys(pygame.K_w, pygame.K_d))
    start = (sim.drone.x, sim.drone.y)
    for _ in range(30):
        game.poll_input()
        game.advance(config.TICK)
    assert (sim.drone.x, sim.drone.y) == start


def test_text_goes_into_the_search(game):
    open_and_focus_search(game)
    search = game.item_window.craft.search
    text(game, "лам")
    assert search.text == "лам"
    assert [o.recipe_id for o in game.item_window.craft.options(game.sim)] == [LAMP]
    key(game, pygame.K_BACKSPACE)
    assert search.text == "ла"


def test_text_without_focus_is_ignored(game):
    key(game, pygame.K_e)
    text(game, "лам")
    assert game.item_window.craft.search.text == ""


def test_escape_blurs_first_then_closes(game, monkeypatch):
    """Esc снимает фокус (окно открыто), второй Esc закрывает окно, третий — выход."""
    open_and_focus_search(game)
    key(game, pygame.K_ESCAPE)
    assert not game.ui.text_input_active
    assert game.item_window.visible and game.running
    # Без фокуса клавиши снова игровые: W двигает дрона.
    hold(monkeypatch, (10, 10), keys=Keys(pygame.K_w))
    game.poll_input()
    assert game.sim.move_input == (0.0, -1.0)
    key(game, pygame.K_ESCAPE)
    assert not game.item_window.visible and game.running
    key(game, pygame.K_ESCAPE)
    assert not game.running


def test_enter_blurs_the_search(game):
    open_and_focus_search(game)
    key(game, pygame.K_RETURN)
    assert not game.ui.text_input_active and game.item_window.visible


def test_click_outside_the_field_blurs(game, monkeypatch):
    hold(monkeypatch, (10, 10))
    open_and_focus_search(game)
    x, y, _w, _h = game.item_window._layout(game.sim)[0]
    click_screen(game, (x + 3, y + 3))
    assert not game.ui.text_input_active and game.item_window.visible


def test_closing_the_window_drops_the_focus(game, monkeypatch):
    """Окно закрылось — фокуса нет: клавиатура не «залипает» в невидимом поле."""
    hold(monkeypatch, (10, 10))
    open_and_focus_search(game)
    key(game, pygame.K_ESCAPE)
    key(game, pygame.K_ESCAPE)
    assert not game.item_window.visible and not game.ui.text_input_active
    key(game, pygame.K_e)
    assert game.item_window.visible, "E снова открывает окно"


# ------------------------------------------------- пауза и окно инвентаря
def test_pause_works_with_the_window_open(game):
    key(game, pygame.K_e)
    key(game, pygame.K_SPACE)
    assert game.paused and game.item_window.visible
    before = game.sim.state_signature()
    assert game.advance(1.0) == 0
    game.draw_frame(config.TICK)
    assert game.sim.state_signature() == before
    key(game, pygame.K_SPACE)
    assert not game.paused and game.item_window.visible


def test_no_mining_while_the_window_is_open(game, monkeypatch):
    """Окно открыто — ЛКМ принадлежит ему: залежь под курсором не добывается."""
    sim = game.sim
    deposit = drone_at_deposit(game)
    hold(monkeypatch, screen_of(game, deposit.x, deposit.y), buttons=(1, 0, 0))
    key(game, pygame.K_e)
    for _ in range(int(config.MINE_TIME_PER_UNIT * 2 / config.TICK)):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.mine_active is False
    assert sim.mine_progress == 0.0 and sim.mined_total == 0
    assert not sim.drone.action.active, "луча нет: дрон ничего не делает"

    key(game, pygame.K_e)                        # закрыли — добыча пошла
    game.poll_input()
    game.advance(config.TICK * 5)
    assert sim.mine_progress > 0.0
    assert sim.drone.action.active


def test_no_scanning_while_the_window_is_open(game, monkeypatch):
    """Скан (ПКМ-удержание по залежи) при открытом окне тоже не идёт."""
    sim = game.sim
    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST * 5)
    deposit = drone_at_deposit(game)
    assert sim.selected_building() is None
    hold(monkeypatch, screen_of(game, deposit.x, deposit.y), buttons=(0, 0, 1))
    key(game, pygame.K_e)
    for _ in range(int(config.SCAN_TIME * 2 / config.TICK)):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.scan_active is False
    assert sim.scan_progress == 0.0 and sim.scans_done == 0

    key(game, pygame.K_e)
    game.poll_input()
    game.advance(config.TICK * 5)
    assert sim.scan_progress > 0.0, "без окна тот же жест сканирует"



# --------------------------------------------------------- подсказки HUD
def record_text(monkeypatch) -> list[str]:
    """Список всех строк, которые HUD и окна рисуют через `widgets.draw_text`."""
    drawn: list[str] = []
    original = widgets.draw_text

    def record(target, value, *args, **kwargs):
        drawn.append(value)
        return original(target, value, *args, **kwargs)

    monkeypatch.setattr(widgets, "draw_text", record)
    return drawn


def drone_two_tiles_from_deposit(game):
    """Дрон в двух тайлах от неизученной залежи: скан достаёт, добыча — нет."""
    sim = game.sim
    deposits = sorted((d for d in sim.world.deposits
                       if not d.depleted and sim.knowledge.unknown(d.resource_id)),
                      key=lambda d: sim.drone.distance_to_tile(d.x, d.y))
    for deposit in deposits:
        for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            x, y = deposit.x + dx, deposit.y + dy
            if not sim.world.is_walkable_tile(x, y) or sim.world.deposit_at(x, y):
                continue
            if sim.run.relic_at(x, y) is not None:
                continue
            sim.drone.x, sim.drone.y = x + 0.5, y + 0.5
            game.camera.snap_to(sim.drone.x, sim.drone.y)
            assert sim.in_scan_range(deposit) and not sim.in_mine_range(deposit)
            return deposit
    raise AssertionError("нет залежи со свободной землёй в двух тайлах")


def test_prompt_says_scanning_while_the_scan_goes_from_afar(game, monkeypatch):
    """Скан достаёт дальше добычи (🔧 `SCAN_RANGE` > `MINE_RANGE`).

    Пока он идёт, подсказка — «Сканирую …», а не «слишком далеко».
    """
    sim = game.sim
    sim.inventory.add(ENERGY_ITEM, config.SCAN_ENERGY_COST * 2)
    deposit = drone_two_tiles_from_deposit(game)
    hold(monkeypatch, screen_of(game, deposit.x, deposit.y), buttons=(0, 0, 1))
    for _ in range(5):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.scan_target is deposit and sim.scan_progress > 0.0
    name = sim.run.items[deposit.resource_id].name
    assert game.hud._prompt_for(sim)[0] == f"Сканирую {name}"


def test_prompt_does_not_say_mining_with_a_full_cargo(game, monkeypatch):
    """Хранилище полно — добычи нет, и подсказка не пишет «Добываю»."""
    sim = game.sim
    deposit = drone_at_deposit(game)
    filler = next(i.id for i in sim.run.items.natural
                  if not i.rare and i.id != deposit.resource_id)
    sim.hotbar.add(filler, config.HOTBAR_SLOTS * config.SLOT_STACK_MAX)
    sim.inventory.add(filler, config.DRONE_SLOTS * config.SLOT_STACK_MAX)
    assert not sim.cargo.can_accept(deposit.resource_id, config.MINE_YIELD)
    hold(monkeypatch, screen_of(game, deposit.x, deposit.y), buttons=(1, 0, 0))
    for _ in range(10):
        game.poll_input()
        game.advance(config.TICK)
    assert sim.mine_target is None and not sim.drone.action.active
    text, _color = game.hud._prompt_for(sim)
    assert text is not None and not text.startswith("Добываю"), text


def test_fit_cuts_a_long_line_with_an_ellipsis(game):
    size = config.UI_FONT_SIZE
    name = "Конструкционный материал"
    width = widgets.text_width(name, size) - 20
    cut = hud._fit(name, width, size)
    assert cut.endswith("…") and widgets.text_width(cut, size) <= width
    assert name.startswith(cut[:-1]), "обрезан хвост, а не середина"
    assert hud._fit("Руда", 200, size) == "Руда", "короткая строка не меняется"


def test_inventory_summary_cuts_long_item_names(game, monkeypatch):
    """Длинное имя в сводке инвентаря обрезается «…» и не наезжает на число."""
    sim = game.sim
    sim.inventory.add("structure", 30)
    name = sim.run.items["structure"].name
    drawn = record_text(monkeypatch)
    game.hud.draw(game.screen, sim, game.camera, 60.0)
    assert name not in drawn, "полное имя не влезает рядом с числом"
    cut = [value for value in drawn if value.endswith("…") and name.startswith(value[:-1])]
    assert cut, f"имени «{name}» в сводке нет"
    assert "30" in drawn


def test_controls_table_is_rendered_once(game, monkeypatch):
    """Таблица клавиш статична: её строки рендерятся в кэш один раз, не каждый кадр."""
    drawn = record_text(monkeypatch)
    game.hud.draw(game.screen, game.sim, game.camera, 60.0)
    parts = {line for line, _indent in game.hud._control_lines()}
    assert parts <= set(drawn), "в первом кадре таблица нарисована"
    drawn.clear()
    for _ in range(3):
        game.hud.draw(game.screen, game.sim, game.camera, 60.0)
    assert not parts & set(drawn), "строки таблицы рендерятся заново каждый кадр"
