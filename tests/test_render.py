"""Отрисовка без окна: дымовые тесты и проверка кэшей."""

from __future__ import annotations

import pygame
import pytest

from src.game import config
from src.game.data.buildings import LAB, WIRE
from src.game.render import quality
from src.game.render.camera import Camera
from src.game.render.icons import IconCache
from src.game.render.renderer import NO_POWER_OFFSET, NO_POWER_SIZE, Renderer
from src.game.render.shapes import SHAPES, draw_shape
from src.game.sim.simulation import Simulation
from tests.conftest import SEED, flat_world, power_lamp, stock


@pytest.fixture
def surface() -> pygame.Surface:
    """Прозрачная поверхность: удобно считать «сколько пикселей нарисовано»."""
    return pygame.Surface((320, 240), pygame.SRCALPHA)


@pytest.fixture(scope="module")
def display():
    """Инициализация дисплея в dummy-режиме для тестов рендера."""
    pygame.display.init()
    screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H))
    yield screen
    pygame.display.quit()


# ------------------------------------------------------------------ фигуры
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_shape_draws_pixels(surface, name):
    draw_shape(surface, name, (255, 0, 0), (160.0, 120.0), 20.0)
    bounds = surface.get_bounding_rect()
    assert bounds.width > 0 and bounds.height > 0, f"форма {name} ничего не нарисовала"
    # форма должна быть нарисована там, где просили, а не в углу
    assert bounds.collidepoint(160, 120), f"форма {name} нарисована не в центре"


def test_unknown_shape_falls_back_to_circle():
    opaque = pygame.Surface((320, 240))
    draw_shape(opaque, "не-форма", (0, 255, 0), (160.0, 120.0), 20.0)
    assert opaque.get_at((160, 120))[:3] == (0, 255, 0)


# ------------------------------------------------------------------ значки
def test_icon_cache_reuses_surfaces(display):
    items = Simulation(SEED).run.items
    cache = IconCache(items)
    item_id = items.natural_ids[0]
    first = cache.item_icon(item_id)
    second = cache.item_icon(item_id)
    assert first is second
    assert first.get_size() == (24, 24)


def test_all_item_icons_render(display):
    """Значки рисуются для всех предметов забега: и ресурсов, и продуктов крафта."""
    items = Simulation(SEED).run.items
    cache = IconCache(items)
    for item in items.items:
        icon = cache.item_icon(item.id)
        assert icon.get_bounding_rect().width > 0, f"{item.id}: пустой значок"


def test_unknown_prop_icon_is_a_question_mark(display):
    """Приёмка M3: неизвестное свойство показывается как знак вопроса."""
    cache = IconCache(Simulation(SEED).run.items)
    icon = cache.unknown_icon()
    assert icon is cache.unknown_icon(), "значок вопроса должен кэшироваться"
    assert icon.get_bounding_rect().width > 0


def test_relic_mark_icon_is_cached(display):
    from src.game.data.relics import relic_def

    cache = IconCache(Simulation(SEED).run.items)
    rdef = relic_def(True)
    first = cache.mark_icon(rdef.mark, rdef.color)
    assert first is cache.mark_icon(rdef.mark, rdef.color)
    assert first.get_width() > 0


def test_prop_icons_render(display):
    from src.game.data.resources import Prop

    cache = IconCache()
    for prop in Prop:
        assert cache.prop_icon(prop).get_bounding_rect().width > 0


# ------------------------------------------------------------- кэш чанков
def test_chunks_are_cached_between_frames(display):
    world = Simulation(SEED).world
    camera = Camera(*world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(world)
    screen = display

    renderer.chunks.draw(screen, camera)
    assert renderer.chunks.chunks_drawn > 0
    renderer.chunks.draw(screen, camera)
    assert renderer.chunks.chunks_drawn == 0, "чанки перерисованы повторно"
    assert renderer.chunks.scaled_drawn == 0, "чанки перемасштабированы повторно"


def test_zoom_change_rescales_but_keeps_base_chunks(display):
    world = Simulation(SEED).world
    camera = Camera(*world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(world)
    renderer.chunks.draw(display, camera)
    camera.set_zoom(1.5)
    renderer.chunks.draw(display, camera)
    assert renderer.chunks.scaled_drawn > 0
    assert renderer.chunks.chunks_drawn == 0, "базовые чанки не должны перерисовываться"


def test_invalidate_tile_forces_redraw(display):
    world = Simulation(SEED).world
    camera = Camera(*world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(world)
    renderer.chunks.draw(display, camera)
    spawn_x, spawn_y = (int(v) for v in world.spawn_point())
    renderer.chunks.invalidate_tile(spawn_x, spawn_y)
    renderer.chunks.draw(display, camera)
    assert renderer.chunks.chunks_drawn == 1, "должен перерисоваться ровно один чанк"


# ------------------------------------------------------------- кадр целиком
def test_full_frame_renders_and_is_not_uniform(display):
    sim = Simulation(SEED)
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(sim.world)
    screen = display
    screen.fill((0, 0, 0))
    renderer.draw_world(screen, sim, camera)

    colors = {screen.get_at((x, y))[:3]
              for x in range(0, config.WINDOW_W, 37)
              for y in range(0, config.WINDOW_H, 29)}
    assert len(colors) > 5, "кадр выглядит однотонным"


def test_render_does_not_change_simulation(display):
    sim = Simulation(SEED)
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(sim.world)
    before = sim.state_signature()
    for _ in range(3):
        renderer.draw_world(display, sim, camera)
    assert sim.state_signature() == before


# ------------------------------------------------------------------- тьма
def brightness(screen: pygame.Surface) -> float:
    """Средняя яркость кадра — для сравнения «день vs ночь»."""
    total = 0
    samples = 0
    for x in range(0, config.WINDOW_W, 37):
        for y in range(0, config.WINDOW_H, 29):
            r, g, b = screen.get_at((x, y))[:3]
            total += r + g + b
            samples += 1
    return total / (3.0 * samples)


def make_frame(sim: Simulation) -> tuple[Camera, Renderer]:
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    return camera, Renderer(sim.world)


def test_day_frame_has_no_darkness_overlay(display):
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    renderer.draw_world(display, sim, camera)
    assert sim.darkness == 0.0
    assert renderer.overlay.draws == 0, "днём тьму рисовать нечем"


def test_night_darkens_the_frame(display):
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera)
    day = brightness(display)

    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)
    assert sim.darkness == pytest.approx(1.0)
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera)
    night = brightness(display)
    assert night < day * 0.6, f"ночью экран должен темнеть: {day:.1f} → {night:.1f}"


def test_lamp_light_stays_visible_at_night(display):
    """Освещённый лампой тайл под тьмой не темнеет — иначе свет бесполезен."""
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    tx, ty = int(sim.drone.x) + 2, int(sim.drone.y)
    power_lamp(sim, tx, ty)              # горит: сеть даёт ей энергию (M4)
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)

    sx, sy = camera.world_to_screen(tx + 0.5, ty + 0.5)
    sim.drone.x, sim.drone.y = 0.0, 0.0  # уводим дрон, чтобы его свет не мешал
    display.fill((0, 40, 0))
    renderer.draw_world(display, sim, camera)
    lamp_pixel = display.get_at((int(sx), int(sy)))[:3]
    dark_pixel = display.get_at((int(sx), int(sy + 120)))[:3]
    assert sum(lamp_pixel) > sum(dark_pixel), "под лампой должно быть светлее"
    assert sum(lamp_pixel) > 120, "тайл под лампой почти не затемняется"


def test_lit_layer_shows_what_simulation_counts(display):
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera, show_lit_layer=False)
    plain = brightness(display)
    before = renderer.overlay.draws
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera, show_lit_layer=True)
    assert renderer.overlay.draws == before + 1
    assert brightness(display) != plain, "слой F2 должен быть виден"


def pixel_at(display: pygame.Surface, camera: Camera, wx: float, wy: float):
    """Цвет пикселя в мировых координатах."""
    sx, sy = camera.world_to_screen(wx, wy)
    return display.get_at((int(sx), int(sy)))[:3]


def test_drone_light_is_cool_and_lamp_light_is_warm(display):
    """Ночью источники различаются цветом: лампа тёплая, дрон холодный."""
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    tx, ty = int(sim.drone.x) + 4, int(sim.drone.y)
    assert power_lamp(sim, tx, ty) is not None
    dx, dy = sim.drone.x, sim.drone.y
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)

    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera)
    drone_px = pixel_at(display, camera, dx, dy - 2.0)
    lamp_px = pixel_at(display, camera, tx + 0.5, ty - 2.0)
    assert drone_px[2] > drone_px[0], f"свет дрона должен быть холодным: {drone_px}"
    assert lamp_px[0] > lamp_px[2], f"свет лампы должен быть тёплым: {lamp_px}"


def test_glow_only_in_the_dark_and_surfaces_are_cached(display):
    """Свет распространяется только в темноте, а пятна не создаются каждый кадр."""
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    renderer.draw_world(display, sim, camera)
    assert renderer.glows_drawn == 0, "днём подсвечивать нечего"

    power_lamp(sim, int(sim.drone.x) + 4, int(sim.drone.y))
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)
    renderer.draw_world(display, sim, camera)
    assert renderer.glows_drawn == 2, "лампа и дрон — два пятна"
    cached = len(renderer._glow_cache)
    assert cached == 2
    for _ in range(5):
        renderer.draw_world(display, sim, camera)
    assert len(renderer._glow_cache) == cached, "пятна пересоздаются каждый кадр"


def test_drops_are_drawn_only_inside_the_frame(display):
    """Обходим тайлы кадра, а не список всех предметов карты (plan/07)."""
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    item = sim.run.items.natural_ids[0]
    x0, y0, x1, y1 = camera.visible_tiles(margin=1)
    sim.drops.spawn_tile(item, 1, x0 + 2, y0 + 2)          # в кадре
    sim.drops.spawn_tile(item, 1, x1 + 50, y1 + 50)        # за кадром
    renderer.draw_world(display, sim, camera)
    assert renderer.drops_drawn == 1, "нарисован только видимый предмет"


def _frame_ms(renderer, display, sim, camera) -> float:
    """Время одного кадра целиком, в миллисекундах."""
    import time

    start = time.perf_counter()
    renderer.draw_world(display, sim, camera)
    return (time.perf_counter() - start) * 1000.0


def test_two_hundred_drops_fit_into_a_frame(display):
    """Приёмка M3.5: 200 куч на карте не роняют кадр (🔧 60 FPS — это 16 мс)."""
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    item = sim.run.items.natural_ids[0]
    x0, y0, _x1, _y1 = camera.visible_tiles(margin=1)
    for i in range(200):                        # все двести — прямо в кадре
        sim.drops.spawn_tile(item, i % 7 + 1, x0 + i % 20, y0 + i // 20)
    renderer.draw_world(display, sim, camera)
    assert renderer.drops_drawn == 200

    cached = len(renderer.icons._scaled)
    best = min(_frame_ms(renderer, display, sim, camera) for _ in range(5))
    for _ in range(5):
        renderer.draw_world(display, sim, camera)
    assert len(renderer.icons._scaled) == cached, "значки пересоздаются каждый кадр"
    assert best < 10.0, f"кадр с 200 предметами занял {best:.1f} мс — это уже не 60 FPS"


def tile_region(display, camera, tx: int, ty: int) -> bytes:
    """Пиксели квадрата вокруг центра тайла — по ним видно, что нарисовано."""
    sx, sy = camera.world_to_screen(tx + 0.5, ty + 0.5)
    rect = pygame.Rect(0, 0, 44, 44)
    rect.center = (int(sx), int(sy))
    return pygame.image.tobytes(display.subsurface(rect.clip(display.get_rect())),
                                "RGB")


def changed_pixels(before: bytes, after: bytes) -> int:
    """Сколько пикселей изменилось между двумя снимками области."""
    return sum(1 for i in range(0, len(before), 3)
               if before[i:i + 3] != after[i:i + 3])


def test_two_piles_on_one_tile_do_not_hide_each_other(display):
    """Разные предметы на одном тайле видны оба, а не один под другим."""
    sim = Simulation(SEED)
    camera, renderer = make_frame(sim)
    first, second = sim.run.items.natural_ids[:2]
    x0, y0, _x1, _y1 = camera.visible_tiles(margin=1)
    tx, ty = x0 + 3, y0 + 3

    renderer.draw_world(display, sim, camera)
    empty = tile_region(display, camera, tx, ty)

    sim.drops.spawn_tile(first, 1, tx, ty)
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera)
    one = changed_pixels(empty, tile_region(display, camera, tx, ty))

    sim.drops.spawn_tile(second, 1, tx, ty)
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera)
    both = changed_pixels(empty, tile_region(display, camera, tx, ty))
    assert renderer.drops_drawn == 2
    assert both > one * 1.4, f"вторая куча спряталась под первой: {one} → {both}"


# ----------------------------------------------- провода, значки и свет (M4.5)
def flat_sim() -> Simulation:
    """Ровная земля без залежей: постройки встают ровно туда, куда просили."""
    return stock(Simulation(0, world=flat_world(30, 30)))


def test_lamp_opens_the_darkness_without_glow(display):
    """Лампа открывает тьму сама, без пятна света поверх неё.

    Регрессия: слой тьмы обнулял свет внутри карты, и всю ночную картинку
    держало яркое пятно поверх тьмы, которое заливало постройки «туманом».
    На `low` пятен нет — значит, светлее здесь только из-за самой тьмы.
    """
    # Без `flat_sim`: запас построек и топлива выдаёт сам `power_lamp`, а
    # второй такой же запас в хранилище дрона уже не влезает.
    sim = Simulation(0, world=flat_world(30, 30))
    camera, renderer = make_frame(sim)
    tx, ty = int(sim.drone.x) + 3, int(sim.drone.y)
    power_lamp(sim, tx, ty)
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)
    sim.drone.x, sim.drone.y = 1.0, 1.0           # свет дрона не должен мешать
    with quality.override(quality.LOW):
        display.fill((0, 0, 0))
        renderer.draw_world(display, sim, camera)
    near = sum(pixel_at(display, camera, tx + 0.5, ty - 1.5))
    far = sum(pixel_at(display, camera, tx + 0.5, ty + 9.5))
    assert near > far * 2, f"тьма у лампы не открылась: {near} против {far}"


def test_adjacent_wires_join_into_one_cable(display):
    """Кабель двух соседних проводов сходится на общем краю тайлов, без щели."""
    sim = flat_sim()
    camera, renderer = make_frame(sim)
    x, y = int(sim.drone.x) + 2, int(sim.drone.y)
    assert sim.build(WIRE, x, y) is not None
    assert sim.build(WIRE, x + 1, y) is not None
    renderer.draw_world(display, sim, camera)
    sx, sy = camera.world_to_screen(x + 1.0, y + 0.5)    # общий край тайлов
    for dx in (-2, -1, 0, 1):
        r, g, b = display.get_at((int(sx) + dx, int(sy)))[:3]
        assert max(r, g, b) < 90 or b > r, f"на стыке проводов щель: {(r, g, b)}"
    # Сбоку от кабеля — трава: кабель идёт только к соседу, а не во все стороны.
    above = display.get_at((int(sx), int(sy - camera.scale * 0.4)))[:3]
    assert above[1] > above[2], f"над кабелем должна быть трава: {above}"


def test_no_power_mark_lies_on_top_of_everything(display):
    """Значок «нет питания» виден поверх куч предметов и ночной тьмы."""
    sim = flat_sim()
    camera, renderer = make_frame(sim)
    x, y = int(sim.drone.x) + 2, int(sim.drone.y) - 2
    lab = sim.build(LAB, x, y)
    assert lab is not None
    sim.update(config.TICK)
    assert not lab.powered, "без генератора лаборатория должна стоять"
    item = sim.run.items.natural_ids[0]
    for _ in range(4):
        sim.drops.spawn_tile(item, 3, x, y)
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)
    sim.drone.x, sim.drone.y = 1.0, 1.0
    display.fill((0, 0, 0))
    renderer.draw_world(display, sim, camera)
    ox, oy = NO_POWER_OFFSET
    sx, sy = camera.world_to_screen(x + 0.5 + ox, y + 0.5 + oy)
    ring = int(camera.scale * NO_POWER_SIZE * 0.44 * 0.77)  # середина красной каймы
    # Кайма тоньше двух пикселей, а размер значка и его центр округляются до
    # целых: середина каймы попадает в пиксель ±1 от расчётной точки. Берём
    # самый красный из трёх — закрытый или погасший значок красным не будет нигде.
    r, g, b = max((display.get_at((int(sx) - ring + d, int(sy)))[:3] for d in (-1, 0, 1)),
                  key=lambda c: c[0] - c[1])
    assert r > 150 and r > g + 60, f"значок закрыт или погас: {(r, g, b)}"


def test_deposits_drawn_counter(display):
    sim = Simulation(SEED)
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(sim.world)
    renderer.draw_world(display, sim, camera)
    assert 0 < renderer.deposits_drawn <= len(sim.world.deposits)
