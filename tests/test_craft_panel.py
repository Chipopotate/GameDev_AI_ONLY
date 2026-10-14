"""Панель крафта в окне E: только изученное, поиск, сортировка, клики (plan/02, plan/10).

Главное требование — панель не выдаёт неизвестного: ни «???», ни счётчиков,
ни подсказок, какие из своих ресурсов подойдут. Поэтому проверяется не только
картинка, но и сам объект, который UI получает от симуляции (`CraftOption`):
если скрытых данных нет в нём, нарисовать их панели просто нечем.
"""

from __future__ import annotations

import dataclasses

import pygame
import pytest

from src.game import config
from src.game.data.buildings import (BUILD_ORDER, GENERATOR, LAB, LAMP,
                                     ROLE_ENERGY, building as building_def)
from src.game.data.resources import PROP_NAMES, Prop
from src.game.render.icons import IconCache
from src.game.sim.simulation import START_RECIPES, CraftOption, Simulation
from src.game.ui import craft_panel, widgets
from src.game.ui.craft_panel import (EMPTY_TEXT, FLASH_MS, HELP_TEXT,
                                     NOT_FOUND_TEXT, PLACEHOLDER, SEARCH_MAX,
                                     SHIFT_TIMES, CraftPanel)
from src.game.ui.state import UiState
from tests.conftest import SEED, stock

#: Куда ставится панель в тестах: раскладку обычно задаёт окно инвентаря.
ORIGIN = (120, 80)


@pytest.fixture(scope="module")
def display():
    pygame.display.init()
    screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H))
    yield screen
    pygame.display.quit()


@pytest.fixture
def setup(display):
    """Симуляция, панель на своём месте, общий флаг ввода и экран."""
    sim = Simulation(SEED)
    ui = UiState()
    panel = CraftPanel(IconCache(sim.run.items), ui)
    panel.place(ORIGIN, sim)
    return sim, panel, ui, display


# ------------------------------------------------------------------ хелперы
def ids(options) -> list[str]:
    return [option.recipe_id for option in options]


def luminous_item(sim: Simulation) -> str:
    """Природный ресурс со свойством «светящийся» — сырьё лампы."""
    return sim.run.items.with_prop(Prop.LUMINOUS)[0].id


def cell_of(panel: CraftPanel, sim: Simulation, recipe_id: str) -> pygame.Rect:
    """Ячейка рецепта на экране. Порядок меняется после крафта — считаем заново."""
    options = panel.options(sim)
    index = ids(options).index(recipe_id)
    return panel.cell_rects(len(options))[index]


def option_of(sim: Simulation, recipe_id: str) -> CraftOption:
    return next(o for o in sim.craft_options() if o.recipe_id == recipe_id)


def study(sim: Simulation, role: str, amplified: bool = False) -> None:
    relic = sim.run.relic_of(role, amplified)
    assert relic is not None and sim.study_relic(relic), f"реликвия {role} не изучена"


def flatten(value):
    """Все «листья» значения: строки и числа внутри вложенных кортежей."""
    if isinstance(value, (tuple, list)):
        for part in value:
            yield from flatten(part)
    else:
        yield value


@pytest.fixture
def texts(monkeypatch):
    """Перехват всех строк, которые рисует интерфейс (рисование при этом идёт)."""
    drawn: list[str] = []
    original = widgets.draw_text

    def record(target, text, *args, **kwargs):
        drawn.append(text)
        return original(target, text, *args, **kwargs)

    monkeypatch.setattr(widgets, "draw_text", record)
    return drawn


# ------------------------------------------------- только изученные рецепты
def test_at_start_only_the_lamp_and_the_lab_are_listed(setup):
    """В начале забега игрок знает ровно лампу и лабораторию (plan/02)."""
    sim, panel, _ui, _screen = setup
    assert set(START_RECIPES) == {LAMP, LAB}
    assert set(ids(sim.craft_options())) == {LAMP, LAB}
    assert len(panel.options(sim)) == 2


def test_materials_do_not_reveal_unknown_recipes(setup):
    """Полный рюкзак не добавляет в список ничего, кроме изученного."""
    sim, _panel, _ui, _screen = setup
    stock(sim)
    for item in sim.run.items.natural:
        sim.inventory.add(item.id, 5)
        sim.knowledge.reveal_all(item.id)
    assert set(ids(sim.craft_options())) == {LAMP, LAB}


def test_a_studied_relic_adds_its_recipes(setup):
    """Реликвия роли открывает её рецепт и постройки роли — они появляются в списке."""
    sim, _panel, _ui, _screen = setup
    study(sim, ROLE_ENERGY)
    assert set(ids(sim.craft_options())) == {LAMP, LAB, ROLE_ENERGY, GENERATOR}
    # Усилители достаются не всем ролям: берём любой, какой есть в забеге.
    amplifier = next(r for r in sim.run.relics if r.amplified)
    assert sim.study_relic(amplifier)
    assert set(ids(sim.craft_options())) == \
        {LAMP, LAB, ROLE_ENERGY, GENERATOR, amplifier.recipe_id}


def test_panel_size_does_not_depend_on_what_is_known(setup):
    """Высота сетки одна и та же: по раскладке не сосчитать неоткрытые рецепты."""
    sim, panel, _ui, _screen = setup
    before = panel.size(sim)
    study(sim, ROLE_ENERGY)
    assert panel.size(sim) == before


def test_nothing_unknown_is_ever_drawn(setup, texts):
    """Ни «???», ни имён неизученного, ни счётчиков «не открыто» — даже в тултипе."""
    sim, panel, _ui, screen = setup
    stock(sim)
    sim.inventory.add(luminous_item(sim), 3)       # свойство ещё не открыто
    panel.draw(screen, sim, (-1, -1), now=0)
    # Без наведения панель пишет только поле поиска и строку помощи.
    assert set(texts) == {PLACEHOLDER, HELP_TEXT}
    options = panel.options(sim)
    for rect in panel.cell_rects(len(options)):
        panel.draw(screen, sim, rect.center, now=0)
    assert all("?" not in text for text in texts), texts
    hidden = [building_def(kind).name for kind in BUILD_ORDER
              if building_def(kind).recipe.id not in START_RECIPES]
    hidden += [item.name for item in sim.run.items.natural]
    for text in texts:
        for name in hidden:
            assert name not in text, f"панель выдала «{name}»: {text}"


# ------------------------------------------------------------------- поиск
@pytest.mark.parametrize("query", ["лам", "ЛАМ", "Лампа", "  лАм  "])
def test_search_filters_by_name_ignoring_case(setup, query):
    sim, panel, _ui, _screen = setup
    assert ids(sim.craft_options(query)) == [LAMP]
    panel.search.text = query
    assert ids(panel.options(sim)) == [LAMP]


def test_empty_search_shows_everything_known(setup):
    sim, _panel, _ui, _screen = setup
    assert set(ids(sim.craft_options(""))) == {LAMP, LAB}
    assert set(ids(sim.craft_options("   "))) == {LAMP, LAB}
    assert ids(sim.craft_options("лаб")) == [LAB]


def test_search_does_not_find_unknown_recipes(setup):
    """Поиск по имени неизученной постройки ничего не находит."""
    sim, _panel, _ui, _screen = setup
    assert sim.craft_options(building_def(GENERATOR).name) == ()
    study(sim, ROLE_ENERGY)
    assert ids(sim.craft_options(building_def(GENERATOR).name)) == [GENERATOR]


def test_nothing_found_draws_a_neutral_line(setup, texts):
    """Пустой результат поиска: панель рисуется без ошибок, с нейтральной надписью."""
    sim, panel, _ui, screen = setup
    panel.search.text = "zzz"
    assert panel.options(sim) == ()
    assert panel.cell_rects(0) == []
    panel.draw(screen, sim, panel.search.rect.center, now=0)
    assert NOT_FOUND_TEXT in texts
    assert panel.hit(panel.search.rect.center, sim) is None


def test_no_known_recipes_says_nothing_to_craft(setup, texts):
    """Изученных нет: «Пока нечего крафтить» — без счётчиков неоткрытого."""
    sim, panel, _ui, screen = setup
    sim.knowledge.known_recipes.clear()
    assert sim.craft_options() == ()
    panel.draw(screen, sim, (-1, -1), now=0)
    assert EMPTY_TEXT in texts and NOT_FOUND_TEXT not in texts


def test_search_field_typing(setup):
    """Ввод — только с фокусом; Backspace, очистка и предел длины."""
    _sim, panel, ui, _screen = setup
    field = panel.search
    field.insert("лам")
    assert field.text == "", "без фокуса поле ничего не принимает"
    field.focus()
    assert field.focused and ui.text_input_active
    field.insert("ла")
    field.insert("м")
    assert field.text == "лам"
    field.insert("\n\t")
    assert field.text == "лам", "непечатные символы отбрасываются"
    field.backspace()
    assert field.text == "ла"
    field.clear()
    assert field.text == ""
    field.backspace()
    assert field.text == ""
    field.insert("я" * (SEARCH_MAX + 10))
    assert len(field.text) == SEARCH_MAX
    field.blur()
    assert not field.focused and not ui.text_input_active


def test_clear_button_empties_the_search(setup):
    """Кнопка «×» внутри поля очищает поиск и оставляет фокус."""
    sim, panel, ui, _screen = setup
    field = panel.search
    assert field.rect.contains(field.clear_rect)
    field.focus()
    field.insert("лам")
    assert ids(panel.options(sim)) == [LAMP]
    assert panel.click(field.clear_rect.center, sim)
    assert field.text == "" and field.focused
    assert set(ids(panel.options(sim))) == {LAMP, LAB}


def test_click_on_the_field_focuses_and_elsewhere_blurs(setup):
    sim, panel, ui, _screen = setup
    assert panel.click(panel.search.rect.center, sim)
    assert ui.text_input_active
    away = (panel.search.rect.left - 30, panel.search.rect.top - 30)
    assert not panel.click(away, sim)
    assert not ui.text_input_active


# --------------------------------------------------------------- сортировка
def test_without_materials_recipes_go_by_name(setup):
    sim, _panel, _ui, _screen = setup
    assert ids(sim.craft_options()) == [LAB, LAMP], "«Лаборатория» раньше «Лампы»"


def test_available_recipes_go_first(setup):
    """Лампу можно скрафтить, лабораторию нет: лампа первая, хотя по имени вторая."""
    sim, _panel, _ui, _screen = setup
    item = luminous_item(sim)
    sim.inventory.add(item, 4)
    sim.knowledge.reveal(item, Prop.LUMINOUS)     # «прочный» игрок не знает
    options = sim.craft_options()
    assert ids(options) == [LAMP, LAB]
    assert [o.available for o in options] == [True, False]


def test_sort_order_is_available_then_name(setup):
    """Общее правило на длинном списке: доступные, потом по имени, усилитель — за базой."""
    sim, _panel, _ui, _screen = setup
    for relic in sim.run.relics:
        assert sim.study_relic(relic)
    item = luminous_item(sim)
    sim.inventory.add(item, 4)
    sim.knowledge.reveal(item, Prop.LUMINOUS)
    options = list(sim.craft_options())
    assert options == sorted(options, key=lambda o: (not o.available, o.name, o.amplified))
    flags = [o.available for o in options]
    assert True in flags and False in flags
    assert flags == sorted(flags, reverse=True), "доступные — одним блоком в начале"
    order = ids(options)
    for relic in sim.run.relics:
        if relic.amplified:
            base = sim.run.recipe(relic.recipe_id).role
            assert order.index(relic.recipe_id) == order.index(base) + 1, base


# ------------------------------------------------- нет скрытых данных в объекте
def test_craft_option_has_only_what_the_player_may_see(setup):
    sim, _panel, _ui, _screen = setup
    names = {field.name for field in dataclasses.fields(CraftOption)}
    assert names == {"recipe_id", "name", "role", "outputs", "amplified",
                     "requires", "available"}


def test_craft_option_never_names_a_natural_resource(setup):
    """Ни одно поле ни одной строки не содержит id природного ресурса."""
    sim, _panel, _ui, _screen = setup
    for role in (ROLE_ENERGY, "automation", "structure", "ammo"):
        study(sim, role)
    for item in sim.run.items.natural:
        sim.inventory.add(item.id, 3)
    natural = set(sim.run.items.natural_ids)
    item_names = {item.name for item in sim.run.items.natural}
    prop_names = set(PROP_NAMES.values())
    for option in sim.craft_options():
        for leaf in flatten(dataclasses.astuple(option)):
            assert leaf not in natural, f"{option.recipe_id}: id ресурса {leaf}"
            assert leaf not in item_names, f"{option.recipe_id}: имя ресурса {leaf}"
        for entry in option.requires:
            assert len(entry) == 3
            prop_name, need, have = entry
            assert prop_name in prop_names, "в требованиях — свойство, а не ресурс"
            assert isinstance(need, int) and isinstance(have, int)


def test_have_counts_only_revealed_properties(setup):
    """«Есть M» — по открытым свойствам: неоткрытое ✨ не считается и не подсвечивает."""
    sim, _panel, _ui, _screen = setup
    item = luminous_item(sim)
    sim.inventory.add(item, 5)
    lamp = option_of(sim, LAMP)
    luminous = PROP_NAMES[Prop.LUMINOUS]
    assert lamp.requires == ((luminous, 2, 0),), "свойство не открыто — «есть 0»"
    assert not lamp.available
    assert sim.knowledge.reveal(item, Prop.LUMINOUS)
    lamp = option_of(sim, LAMP)
    assert lamp.requires == ((luminous, 2, 5),)
    assert lamp.available


def test_tooltip_does_not_count_unrevealed_resources(setup, texts):
    """В тултипе — «свойство ×N (есть 0)», пока свойство не открыто."""
    sim, panel, _ui, screen = setup
    sim.inventory.add(luminous_item(sim), 5)
    option = option_of(sim, LAMP)
    panel.draw_tooltip(screen, option, (300, 300))
    assert f"{PROP_NAMES[Prop.LUMINOUS]} ×2 (есть 0)" in texts


# -------------------------------------------------------------------- клики
def test_click_crafts_one_shift_five_ctrl_the_rest(setup):
    """Клик — 1, Shift — ×5 (или сколько хватит), Ctrl — максимум (plan/02)."""
    sim, panel, _ui, _screen = setup
    item = luminous_item(sim)
    sim.inventory.add(item, 30)                  # 15 ламп по ✨×2
    assert panel.click(cell_of(panel, sim, LAMP).center, sim, when=0)
    assert sim.cargo.count(LAMP) == 1
    assert sim.inventory.count(item) == 28
    assert panel.click(cell_of(panel, sim, LAMP).center, sim, shift=True, when=0)
    assert sim.cargo.count(LAMP) == 1 + SHIFT_TIMES
    assert panel.click(cell_of(panel, sim, LAMP).center, sim, ctrl=True, when=0)
    assert sim.cargo.count(LAMP) == 15, "Ctrl — всё, на что хватает"
    assert sim.inventory.count(item) == 0
    assert sim.hotbar.count(LAMP) == 15, "постройки ложатся в хотбар"
    assert not panel.flashing(LAMP, 0), "удачный крафт не мигает"


def test_shift_crafts_as_many_as_there_is(setup):
    sim, panel, _ui, _screen = setup
    item = luminous_item(sim)
    sim.inventory.add(item, 6)                   # на три лампы, а не на пять
    assert panel.click(cell_of(panel, sim, LAMP).center, sim, shift=True, when=0)
    assert sim.cargo.count(LAMP) == 3
    assert sim.inventory.count(item) == 0


def test_click_crafts_by_real_properties_as_before(setup):
    """Прежнее поведение крафта: ресурс подходит, даже если свойство не открыто."""
    sim, panel, _ui, _screen = setup
    item = luminous_item(sim)
    sim.inventory.add(item, 2)
    assert not option_of(sim, LAMP).available, "рецепт приглушён — свойство не открыто"
    assert panel.click(cell_of(panel, sim, LAMP).center, sim, when=0)
    assert sim.cargo.count(LAMP) == 1


def test_failed_click_flashes_and_changes_nothing(setup):
    sim, panel, _ui, _screen = setup
    before = sim.state_signature()
    when = 5000
    assert panel.click(cell_of(panel, sim, LAB).center, sim, when=when)
    assert sim.state_signature() == before, "не хватило — ничего не потрачено"
    assert panel.flashing(LAB, when)
    assert panel.flashing(LAB, when + FLASH_MS)
    assert not panel.flashing(LAB, when + FLASH_MS + 1), "вспышка короткая"
    assert not panel.flashing(LAMP, when), "мигает только то, по чему кликнули"
    for flags in ({"shift": True}, {"ctrl": True}):
        assert panel.click(cell_of(panel, sim, LAB).center, sim, when=when, **flags)
        assert sim.state_signature() == before


def test_click_past_the_recipes_does_nothing(setup):
    sim, panel, _ui, _screen = setup
    sim.inventory.add(luminous_item(sim), 10)
    before = sim.state_signature()
    rects = panel.cell_rects(len(panel.options(sim)))
    gap = (rects[0].right + craft_panel.GAP // 2, rects[0].centery)
    assert panel.hit(gap, sim) is None
    assert not panel.click(gap, sim, when=0)
    assert sim.state_signature() == before


# ---------------------------------------------------------------- отрисовка
def test_draw_does_not_touch_the_simulation(setup):
    sim, panel, _ui, screen = setup
    stock(sim)
    sim.inventory.add(luminous_item(sim), 10)
    before = sim.state_signature()
    toasts = list(sim.toasts)
    for rect in panel.cell_rects(len(panel.options(sim))):
        panel.draw(screen, sim, rect.center, now=0)
    panel.draw(screen, sim, (-1, -1), now=0)
    assert sim.state_signature() == before
    assert list(sim.toasts) == toasts


def test_tooltip_is_drawn_on_hover(setup, texts):
    """Наведение на рецепт: название, результат и требования «свойство ×N (есть M)»."""
    sim, panel, _ui, screen = setup
    item = luminous_item(sim)
    sim.inventory.add(item, 3)
    sim.knowledge.reveal(item, Prop.LUMINOUS)
    rect = cell_of(panel, sim, LAMP)
    mouse = rect.center
    screen.fill((0, 0, 0))
    panel.draw(screen, sim, (-1, -1), now=0)
    plain = screen.copy()
    texts.clear()
    panel.draw(screen, sim, mouse, now=0)
    name = building_def(LAMP).name
    assert name in texts
    assert any(text.startswith("Результат:") and name in text for text in texts)
    assert f"{PROP_NAMES[Prop.LUMINOUS]} ×2 (есть 3)" in texts
    # Тултип — справа-снизу от курсора: там картинка должна поменяться.
    probe = pygame.Rect(mouse[0] + 16, mouse[1] + 18, 60, 30)
    changed = any(screen.get_at((x, y)) != plain.get_at((x, y))
                  for x in range(probe.left, probe.right, 3)
                  for y in range(probe.top, probe.bottom, 3))
    assert changed, "тултип не нарисован"


def test_flash_is_drawn_without_errors(setup):
    """Мигающая ячейка рисуется (красная рамка), ничего не ломая."""
    sim, panel, _ui, screen = setup
    panel.click(cell_of(panel, sim, LAMP).center, sim, when=100)
    assert panel.flashing(LAMP, 150)
    panel.draw(screen, sim, (-1, -1), now=150)
    panel.draw(screen, sim, (-1, -1), now=100 + FLASH_MS + 50)
