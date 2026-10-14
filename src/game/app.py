"""Приложение: окно, главный цикл, ввод.

Здесь единственное место, где симуляция и pygame встречаются. Логика шага
симуляции отделена от отрисовки: фиксированный TICK, отдельный рендер.
"""

from __future__ import annotations

import math
import random
import time

import pygame

from src.game import config
from src.game.render.camera import Camera
from src.game.render.gallery import Gallery
from src.game.render.renderer import Renderer
from src.game.sim.simulation import BUILDINGS, Simulation
from src.game.ui import fonts, widgets
from src.game.ui.debug import DebugOverlay
from src.game.ui.hud import Hud
from src.game.ui.item_window import MODE_CONTAINER, ItemWindow
from src.game.ui.recipe_book import RecipeBook

# Допуск при сравнении накопленного времени с шагом симуляции.
TICK_EPS = 1e-9

#: Клавиши выбора постройки: 1, 2, … по порядку `sim.BUILDINGS`.
BUILD_KEYS = {pygame.K_1 + i: kind for i, kind in enumerate(BUILDINGS)}


class Game:
    """Игра целиком: состояние + окно + ввод."""

    def __init__(self, seed: int | None = None, fullscreen: bool = False) -> None:
        # Сид выбирает приложение, а не симуляция: в sim/ глобальный random запрещён.
        self.seed = seed if seed is not None else random.randrange(1, 2 ** 31 - 1)

        pygame.display.init()
        pygame.font.init()
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H), flags)
        pygame.display.set_caption(config.WINDOW_TITLE)
        self.clock = pygame.time.Clock()

        self.sim = Simulation(self.seed)
        view_w, view_h = self.screen.get_size()
        self.camera = Camera(*self.sim.world.spawn_point(), viewport_w=view_w,
                             viewport_h=view_h)
        self.camera.clamp_to(self.sim.world.width, self.sim.world.height)
        self.renderer = Renderer(self.sim.world)
        self.hud = Hud(self.renderer.icons)
        self.book = RecipeBook(self.renderer.icons)
        self.item_window = ItemWindow(self.renderer.icons)
        self.debug = DebugOverlay()
        # Галерея спрайтов (plan/09 §12): F4 — сравнить все формы на одном экране.
        self.gallery = Gallery(self.renderer.sprites, self.renderer.icons,
                               self.sim.run.items, seed=self.seed)
        self.gallery_visible = False

        self.running = True
        self.paused = False
        self.fps = 0.0
        self.frame_ms = 0.0
        self._acc = 0.0

    def skip_to_night(self) -> None:
        """Отладка: промотать часы в середину первой ночи.

        Первый день длится 🔧 360 с — ждать его ради проверки тьмы и света
        слишком долго. Симуляция при этом не идёт: часы просто перескакивают.
        """
        cycle = self.sim.time_cycle
        cycle.update(cycle.first_day_length + cycle.night_length * 0.5)

    # ------------------------------------------------------------- ввод
    def poll_input(self) -> None:
        """Непрерывный ввод (движение, добыча, скан) — читается каждый кадр.

        Пока открыто окно (книга рецептов или слоты), кнопки мыши принадлежат
        ему: иначе игрок крафтил бы и добывал одним движением руки.
        """
        keys = pygame.key.get_pressed()
        move_x = float(keys[pygame.K_d]) - float(keys[pygame.K_a])
        move_y = float(keys[pygame.K_s]) - float(keys[pygame.K_w])
        self.sim.set_move_input(move_x, move_y)
        self.sim.set_cursor_tile(*self.tile_at_screen(None))
        # Окно здания живёт, пока дрон рядом: отошёл — оно закрылось
        # (предмет из руки окно вернёт само, см. ItemWindow.close).
        if self.item_window.mode == MODE_CONTAINER \
                and not self.sim.building_reachable(self.item_window.building):
            self.item_window.close(self.sim)
        buttons = pygame.mouse.get_pressed()
        # Галерея — режим просмотра: под ней по карте не строят и не добывают.
        held = (not self.book.visible and not self.item_window.visible
                and not self.gallery_visible)
        self.sim.set_mining(held and bool(buttons[0]))
        self.sim.set_scanning(held and bool(buttons[2]))

    def tile_at_screen(self, pos: tuple[int, int] | None) -> tuple[int, int]:
        """Тайл под экранной точкой (по умолчанию — под курсором)."""
        mx, my = pygame.mouse.get_pos() if pos is None else pos
        wx, wy = self.camera.screen_to_world(mx, my)
        return int(math.floor(wx)), int(math.floor(wy))

    def click_world(self, pos: tuple[int, int] | None = None) -> None:
        """ЛКМ по миру: снести постройку или поставить выбранную (клавиши 1…8).

        Цену постройки списывает симуляция: приложение только сообщает тайл.
        """
        self.sim.click_at(*self.tile_at_screen(pos))

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.KEYDOWN:
            self._handle_keydown(event.key)
        elif event.type == pygame.MOUSEWHEEL:
            self.camera.zoom_by(event.y)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
            self._handle_click(event)

    def _handle_click(self, event: pygame.event.Event) -> None:
        """Клик мышью: сначала окна, потом мир.

        ЛКМ по миру — постройка/снос (добыча держит кнопку), ПКМ по лаборатории
        рядом — её слоты (M3.5). Скан тоже на ПКМ, но он держит кнопку и на
        здание не смотрит, так что эти два действия не мешают друг другу.
        """
        pos = getattr(event, "pos", None)
        if pos is None:
            pos = pygame.mouse.get_pos()
        if self.gallery_visible:
            return
        if self.item_window.visible:
            shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            self.item_window.click(pos, event.button, self.sim, shift=shift)
        elif event.button == 1:
            if not self.book.click(pos, self.sim):
                self.click_world(pos)
        else:
            self.open_container_at(pos)

    def open_container_at(self, pos: tuple[int, int]) -> bool:
        """ПКМ по зданию: открыть его контейнеры (склад, сборщик, бур, лаборатория)."""
        building = self.sim.buildings.at(*self.tile_at_screen(pos))
        if building is None or not building.containers:
            return False
        if not self.sim.building_reachable(building):
            self.sim.toast("Слишком далеко до здания")
            return False
        self.book.close()
        self.item_window.open_container(self.sim, building)
        return True

    def _handle_keydown(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            # Esc закрывает окно, а не игру: окно поверх мира — первый шаг назад.
            if self.gallery_visible:
                self.gallery_visible = False
            elif self.item_window.visible:
                self.item_window.close(self.sim)
            elif self.book.visible:
                self.book.close()
            else:
                self.running = False
        elif key == pygame.K_SPACE:
            self.paused = not self.paused
        elif key in (pygame.K_i, pygame.K_TAB):
            self.book.close()
            self.item_window.toggle_drone(self.sim)
        elif key == pygame.K_r:
            self.item_window.close(self.sim)
            self.book.toggle()
        elif key == pygame.K_q:
            # Q — выбросить стак из-под курсора, как в Minecraft: иначе вещь
            # из забитого инвентаря некуда деть, кроме как в землю.
            if self.item_window.visible:
                self.item_window.drop_under_cursor(pygame.mouse.get_pos(), self.sim)
        elif key == pygame.K_t:
            # T — повернуть конвейер под курсором (и только его: у остальных
            # зданий стороны нет). Направление при постройке берётся «от дрона»,
            # а T исправляет промах, не требуя сносить и ставить заново.
            if not self.sim.rotate_at(*self.tile_at_screen(None)):
                self.sim.toast("Поворачивать нечего")
        elif key in BUILD_KEYS:
            self.sim.select_build(BUILD_KEYS[key])
        elif key == pygame.K_F1:
            self.debug.toggle("f1")
        elif key == pygame.K_F2:
            self.debug.toggle("f2")
        elif key == pygame.K_F3:
            self.debug.toggle("f3")
        elif key == pygame.K_F4:
            self.gallery_visible = not self.gallery_visible

    # -------------------------------------------------------- симуляция
    def advance(self, dt: float) -> int:
        """Прогоняет симуляцию фиксированными шагами. Возвращает число шагов.

        В паузе не делает ничего — это и есть вся реализация паузы.
        """
        if self.paused:
            return 0
        self._acc += dt
        steps = 0
        # TICK_EPS гасит накопление ошибки float: без него теряется ~1 тик в секунду.
        while self._acc >= config.TICK - TICK_EPS:
            self.sim.update(config.TICK)
            self._acc -= config.TICK
            steps += 1
        return steps

    def update_camera(self, dt: float) -> None:
        drone = self.sim.drone
        self.camera.follow(drone.x, drone.y, dt)
        self.camera.clamp_to(self.sim.world.width, self.sim.world.height)

    # --------------------------------------------------------- отрисовка
    def render(self, dt: float = config.TICK) -> None:
        # dt идёт и в рендер: от него зависят анимации (а не от частоты кадров).
        self.renderer.draw_world(self.screen, self.sim, self.camera,
                                 show_lit_layer=self.debug.show_layers, dt=dt)
        self.hud.draw(self.screen, self.sim, self.camera, self.fps, self.paused)
        self.book.draw(self.screen, self.sim)
        self.item_window.draw(self.screen, self.sim)
        if self.gallery_visible:
            self.gallery.draw(self.screen, hint="F4 или Esc — закрыть")
        self.debug.draw(self.screen, self.sim, self.camera, self.renderer,
                        self.fps, self.frame_ms)

    def draw_frame(self, dt: float = config.TICK) -> None:
        """Один полный кадр без обработки событий — для тестов и скриншотов."""
        t0 = time.perf_counter()
        self.advance(dt)
        self.update_camera(dt)
        self.render(dt)
        self.frame_ms = (time.perf_counter() - t0) * 1000.0

    # ------------------------------------------------------------- цикл
    def run(self) -> None:
        while self.running:
            dt = min(self.clock.tick(config.TARGET_FPS) / 1000.0, config.MAX_FRAME_DT)
            for event in pygame.event.get():
                self.handle_event(event)
            self.poll_input()
            self.draw_frame(dt)
            self.fps = self.clock.get_fps()
            pygame.display.flip()
        self.close()

    def close(self) -> None:
        # Кэши шрифтов и панелей привязаны к дисплею — сбрасываем вместе с ним.
        fonts.clear_cache()
        widgets.clear_cache()
        pygame.display.quit()
        pygame.font.quit()


def save_screenshot(path: str, seed: int | None = None, frames: int = 30,
                    night: bool = False) -> str:
    """Рендерит несколько кадров и сохраняет PNG. Для быстрой проверки вида."""
    game = Game(seed=seed)
    if night:
        game.skip_to_night()
    for _ in range(max(1, frames)):
        game.draw_frame(config.TICK)
    pygame.image.save(game.screen, path)
    game.close()
    return path


def run_bench(frames: int = 300, seed: int | None = None, night: bool = False) -> dict:
    """Замер производительности: среднее время кадра и FPS.

    Ночью кадр дороже (тьма-оверлей), поэтому `night=True` даёт честный
    худший случай, а не средний по дню.
    """
    game = Game(seed=seed)
    if night:
        game.skip_to_night()
    game.camera.snap_to(*game.sim.world.spawn_point())
    for _ in range(10):  # прогрев кэшей
        game.draw_frame(config.TICK)
    t0 = time.perf_counter()
    for _ in range(frames):
        game.draw_frame(config.TICK)
    elapsed = time.perf_counter() - t0
    game.close()
    ms = elapsed / frames * 1000.0
    return {
        "frames": frames,
        "total_s": elapsed,
        "ms_per_frame": ms,
        "fps": 1000.0 / ms if ms > 0 else float("inf"),
    }
