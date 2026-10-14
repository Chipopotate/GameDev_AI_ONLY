"""Окно «Книга рецептов»: раскладка, клики, что видит игрок (plan/02)."""

from __future__ import annotations

import pygame
import pytest

from src.game import config
from src.game.data.buildings import LAB, LAB_SAMPLES
from src.game.data.resources import Prop
from src.game.render.icons import IconCache
from src.game.sim.simulation import Simulation
from src.game.ui.recipe_book import RecipeBook
from tests.conftest import SEED, stock


@pytest.fixture(scope="module")
def display():
    pygame.display.init()
    screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H))
    yield screen
    pygame.display.quit()


@pytest.fixture
def book(display):
    sim = Simulation(SEED)
    return RecipeBook(IconCache(sim.run.items)), sim, display


def draw(book, sim, screen) -> None:
    screen.fill((0, 0, 0))
    book.draw(screen, sim)


def resource_with(sim: Simulation, prop: Prop) -> str:
    """Ресурс забега с нужным свойством: гарантия проходимости его обеспечивает."""
    item = sim.run.items.with_prop(prop)[0]
    return item.id


# ------------------------------------------------------------------ раскладка
def test_window_fits_on_screen(book):
    book, _sim, _screen = book
    x, y, w, h = book.window_rect()
    assert 0 <= x and 0 <= y
    assert x + w <= config.WINDOW_W and y + h <= config.WINDOW_H


def test_nothing_is_drawn_while_closed(book):
    b, sim, screen = book
    screen.fill((0, 0, 0))
    b.draw(screen, sim)
    x, y, w, h = b.window_rect()
    assert screen.get_at((x + w // 2, y + h // 2))[:3] == (0, 0, 0), \
        "закрытая книга не рисуется"


def test_open_book_is_visible(book):
    b, sim, screen = book
    b.toggle()
    draw(b, sim, screen)
    x, y, w, h = b.window_rect()
    assert screen.get_at((x + w // 2, y + h // 2))[:3] != (0, 0, 0)
    assert w > h, "окно должно быть шире, чем выше"


def test_draw_does_not_change_simulation(book):
    b, sim, screen = book
    b.toggle()
    before = sim.state_signature()
    draw(b, sim, screen)
    assert sim.state_signature() == before


def test_columns_have_room_for_all_rows(book):
    """В колонку должны влезть и рецепты (по два на роль), и материалы."""
    from src.game.data.recipes import ROLES

    b, sim, _screen = book
    assert b.rows >= len(ROLES) * 2
    assert b.rows >= len(sim.run.items.natural)


def test_unknown_recipes_are_shown_as_questions(book):
    """Приёмка M3: неизвестная роль — «???» с подсказкой нужного свойства."""
    from src.game.data.recipes import ROLES

    b, sim, _screen = book
    assert sim.knowledge.known_count == 0
    for role_def in ROLES:
        assert role_def.prop in Prop, "подсказка роли — её свойство (plan/02)"
        assert role_def.base.id not in sim.knowledge.known_recipes


def test_known_recipe_gets_a_clickable_craft_button(book):
    b, sim, screen = book
    item_id = resource_with(sim, Prop.FLAMMABLE)
    sim.inventory.add(item_id, 10)
    sim.knowledge.learn("energy")
    b.toggle()
    draw(b, sim, screen)
    assert "energy" in b._craft.values(), "кнопка крафта должна быть активна"


def test_unknown_recipe_has_no_craft_button(book):
    b, sim, screen = book
    item_id = resource_with(sim, Prop.FLAMMABLE)
    sim.inventory.add(item_id, 10)
    b.toggle()
    draw(b, sim, screen)
    assert b._craft == {}, "неизвестный рецепт крафтить нельзя"


def test_not_enough_materials_means_no_button(book):
    b, sim, screen = book
    sim.knowledge.learn("energy")
    b.toggle()
    draw(b, sim, screen)
    assert b._craft == {}, "пустой инвентарь — крафтить нечего"


# -------------------------------------------------------------------- клики
def click_center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def test_click_on_craft_button_crafts(book):
    b, sim, screen = book
    item_id = resource_with(sim, Prop.FLAMMABLE)
    sim.inventory.add(item_id, 10)
    sim.knowledge.learn("energy")
    b.toggle()
    draw(b, sim, screen)
    rect = next(r for r, rid in b._craft.items() if rid == "energy")
    assert b.click(click_center(rect), sim)
    assert sim.inventory.count("energy") == 1
    assert sim.inventory.count(item_id) == 7, "рецепт 🔥×3 → 1"
    assert sim.crafted_total == 1


def test_click_inside_book_leaves_world_alone(book):
    b, sim, screen = book
    b.toggle()
    draw(b, sim, screen)
    x, y, w, h = b.window_rect()
    assert b.click((x + w // 2, y + h - 10), sim), "окно поглощает клик"
    assert sim.light.lamps == [] and sim.buildings.buildings == []


def test_click_on_closed_book_is_ignored(book):
    b, sim, _screen = book
    assert not b.click((10, 10), sim), "закрытая книга кликов не берёт"


def test_materials_column_is_read_only(book):
    """Образцы кладут в слоты лаборатории (ПКМ по ней), а не из книги (M3.5)."""
    b, sim, screen = book
    stock(sim)                       # лаборатория стоит материалов (M4)
    item = next(i for i in sim.run.items.natural if sim.knowledge.unknown(i.id))
    sim.inventory.add(item.id, 2)
    sim.select_build(LAB)
    assert sim.click_at(int(sim.drone.x) + 1, int(sim.drone.y)) == "lab"
    b.toggle()
    draw(b, sim, screen)
    assert not hasattr(b, "_sample"), "кнопки отправки образца больше нет"
    # Клик по колонке материалов ничего не тратит и не отправляет.
    x, y, w, h = b.window_rect()
    b.click((x + w - 40, y + 100), sim)
    assert sim.inventory.count(item.id) == 2
    lab = sim.buildings.of_kind(LAB)[0]
    assert lab.container(LAB_SAMPLES).total_count() == 0
