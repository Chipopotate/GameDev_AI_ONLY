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
from src.game.sim.commands import PlaceBuilding, SelectHotbar
from src.game.sim.simulation import Simulation
from src.game.ui import fonts, widgets
from src.game.ui.debug import DebugOverlay
from src.game.ui.hud import Hud
from src.game.ui.item_window import MODE_CONTAINER, ItemWindow
from src.game.ui.state import UiState

# Допуск при сравнении накопленного времени с шагом симуляции.
TICK_EPS = 1e-9

#: Клавиши слотов хотбара: 1…9 (plan/10). Режима «что строить» больше нет —
#: постройка ставится из выбранного слота.
HOTBAR_KEYS = {pygame.K_1 + i: i for i in range(config.HOTBAR_SLOTS)}

#: Клавиши, которые в поле поиска что-то значат. Остальные — просто текст
#: (он приходит событием TEXTINPUT) и в игру не уходят.
TEXT_SUBMIT_KEYS = (pygame.K_RETURN, pygame.K_KP_ENTER)


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
        # Кто сейчас забирает клавиатуру (поле поиска): один флаг на всё
        # приложение — `ui.text_input_active` (plan/10).
        self.ui = UiState()
        self.hud = Hud(self.renderer.icons)
        self.item_window = ItemWindow(self.renderer.icons, self.ui)
        self.debug = DebugOverlay()
        # Галерея спрайтов (plan/09 §12): F4 — сравнить все формы на одном экране.
        self.gallery = Gallery(self.renderer.sprites, self.renderer.icons,
                               self.sim.run.items, seed=self.seed)
        self.gallery_visible = False

        #: Нажатие кнопки мыши, уже потраченное на интерфейс или постановку
        #: (ЛКМ, СКМ, ПКМ): до отпускания оно не превращается в «удержание» в
        #: мире. Иначе ПКМ, поставившая последний бур, тут же начинала бы скан
        #: залежи под ним, а ЛКМ, выбравшая слот, — копать (plan/10).
        self._consumed = [False, False, False]

        self._defeat_shown = False
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
        """Непрерывный ввод (движение, добыча, скан, разбор) — каждый кадр.

        Пока открыто окно, кнопки мыши принадлежат ему: иначе игрок крафтил бы
        и добывал одним движением руки. Пока идёт ввод текста (поиск), WASD —
        это буквы, а не движение.
        """
        keys = pygame.key.get_pressed()
        if self.ui.text_input_active:
            move_x = move_y = 0.0
        else:
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
        for i in range(min(len(buttons), len(self._consumed))):
            if not buttons[i]:
                self._consumed[i] = False
        # Галерея — режим просмотра: под ней по карте не строят и не добывают;
        # над интерфейсом кнопка мыши выбирает слот, а не копает.
        held = (not self.item_window.visible and not self.gallery_visible
                and not self.hud.over_ui(pygame.mouse.get_pos()))
        self.sim.set_mining(held and bool(buttons[0]) and not self._consumed[0])
        self.sim.set_scanning(held and bool(buttons[2]) and not self._consumed[2])

    def tile_at_screen(self, pos: tuple[int, int] | None) -> tuple[int, int]:
        """Тайл под экранной точкой (по умолчанию — под курсором)."""
        mx, my = pygame.mouse.get_pos() if pos is None else pos
        wx, wy = self.camera.screen_to_world(mx, my)
        return int(math.floor(wx)), int(math.floor(wy))

    def ghost_tile(self) -> tuple[int, int] | None:
        """Тайл призрака постройки: только над картой и при закрытых окнах."""
        if self.item_window.visible or self.gallery_visible:
            return None
        mouse = pygame.mouse.get_pos()
        if self.hud.over_ui(mouse):
            return None
        return self.tile_at_screen(mouse)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif self.sim.defeated:
            # Экран поражения: игра стоит, Esc — выход.
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.running = False
        elif event.type == pygame.TEXTINPUT:
            if self.ui.text_input_active:
                self.item_window.craft.search.insert(event.text)
        elif event.type == pygame.KEYDOWN:
            self._handle_keydown(event.key)
        elif event.type == pygame.MOUSEWHEEL:
            self._handle_wheel(event)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
            self._handle_click(event)

    def _handle_wheel(self, event: pygame.event.Event) -> None:
        """Колесо — зум, Shift+колесо листает хотбар (вниз — следующий слот).

        На некоторых системах (macOS, тачпады) Shift превращает прокрутку в
        горизонтальную: тогда шаг берётся из `x`.
        """
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            step = int(event.y) or int(getattr(event, "x", 0))
            self.sim.scroll_hotbar(-step)
        else:
            self.camera.zoom_by(event.y)

    def _handle_click(self, event: pygame.event.Event) -> None:
        """Клик мышью: хотбар, потом окно, потом мир.

        В мире по клику работает только ПКМ: окно здания или постановка
        постройки из выбранного слота. Добыча, изучение и разбор держат ЛКМ —
        их ведёт `poll_input`, а не событие клика.
        """
        pos = getattr(event, "pos", None)
        if pos is None:
            pos = pygame.mouse.get_pos()
        if self.gallery_visible:
            return
        mods = pygame.key.get_mods()
        shift = bool(mods & pygame.KMOD_SHIFT)
        ctrl = bool(mods & pygame.KMOD_CTRL)
        slot = self.hud.hotbar.hit(pos)
        if slot is not None and event.button == 1 and not self.item_window.contains(
                pos, self.sim):
            self.ui.blur()
            self.sim.apply(SelectHotbar(slot))
            self._consume(event.button)
            return
        if self.item_window.visible:
            self.item_window.click(pos, event.button, self.sim, shift=shift, ctrl=ctrl)
            self._consume(event.button)
            return
        if self.hud.over_ui(pos):
            self._consume(event.button)
            return
        if event.button == 3 and self.right_click_world(pos):
            self._consume(event.button)

    def _consume(self, button: int) -> None:
        """Это нажатие уже что-то сделало: удержанием в мире оно не станет."""
        index = button - 1
        if 0 <= index < len(self._consumed):
            self._consumed[index] = True

    def right_click_world(self, pos: tuple[int, int]) -> bool:
        """ПКМ по карте (plan/10). True — если клик что-то сделал.

        Здание с контейнерами — открыть его окно, что бы ни было в слоте. Иначе
        постройка в выбранном слоте ставится на тайл (тратится один предмет),
        если призрак зелёный: проверка та же, что у призрака (`sim.can_place`).
        Красный призрак, пустой слот или ресурс в слоте — ничего: скан идёт по
        удержанию ПКМ на залежи, и его ведёт симуляция.
        """
        tile = self.tile_at_screen(pos)
        building = self.sim.buildings.at(*tile)
        if building is not None and building.containers:
            self.open_container(building)
            return True
        kind = self.sim.selected_building()
        if kind is None or not self.sim.can_place(kind, *tile):
            return False
        return self.sim.apply(PlaceBuilding(self.sim.selected_slot, tile)) is not None

    def open_container(self, building) -> bool:
        """ПКМ по зданию: открыть его контейнеры (склад, сборщик, бур, лаборатория)."""
        if not self.sim.building_reachable(building):
            self.sim.toast("Слишком далеко до здания")
            return False
        self.item_window.open_container(self.sim, building)
        return True

    def _handle_keydown(self, key: int) -> None:
        if self.ui.text_input_active:
            self._handle_text_key(key)
            return
        if key == pygame.K_ESCAPE:
            # Esc закрывает окно, а не игру: окно поверх мира — первый шаг назад.
            if self.gallery_visible:
                self.gallery_visible = False
            elif self.item_window.visible:
                self.item_window.close(self.sim)
            else:
                self.running = False
        elif key == pygame.K_SPACE:
            self.paused = not self.paused
        elif key == pygame.K_e:
            # E — инвентарь с крафтом. Из окна здания E тоже закрывает: одна
            # клавиша «окно предметов», как в Minecraft.
            if self.item_window.visible:
                self.item_window.close(self.sim)
            elif not self.gallery_visible:
                self.item_window.open_drone(self.sim)
        elif key == pygame.K_q:
            # Q — выбросить стак из-под курсора, как в Minecraft: иначе вещь
            # из забитого инвентаря некуда деть, кроме как в землю.
            if self.item_window.visible:
                self.item_window.drop_under_cursor(pygame.mouse.get_pos(), self.sim)
        elif key == pygame.K_r:
            # R — поворот на 90° по часовой: призрак, а без него — конвейер под
            # курсором. Иначе ничего (plan/10).
            # Над панелями HUD мира не видно — и крутить там нечего (plan/10).
            mouse = pygame.mouse.get_pos()
            if not self.item_window.visible and not self.gallery_visible \
                    and not self.hud.over_ui(mouse):
                self.sim.rotate_preview(*self.tile_at_screen(mouse))
        elif key in HOTBAR_KEYS:
            self.sim.apply(SelectHotbar(HOTBAR_KEYS[key]))
        elif key == pygame.K_F1:
            self.debug.toggle("f1")
        elif key == pygame.K_F2:
            self.debug.toggle("f2")
        elif key == pygame.K_F3:
            self.debug.toggle("f3")
        elif key == pygame.K_F4:
            self.gallery_visible = not self.gallery_visible

    def _handle_text_key(self, key: int) -> None:
        """Клавиши в поле поиска: Esc снимает фокус, второй Esc закроет окно."""
        search = self.item_window.craft.search
        if key == pygame.K_ESCAPE or key in TEXT_SUBMIT_KEYS:
            search.blur()
        elif key == pygame.K_BACKSPACE:
            search.backspace()

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
        if self.sim.defeated:
            return                      # камера стоит на точке прорыва
        drone = self.sim.drone
        self.camera.follow(drone.x, drone.y, dt)
        self.camera.clamp_to(self.sim.world.width, self.sim.world.height)

    # --------------------------------------------------------- отрисовка
    def render(self, dt: float = config.TICK) -> None:
        # dt идёт и в рендер: от него зависят анимации (а не от частоты кадров).
        # На паузе анимации стоят: картинка замирает вместе с симуляцией
        # (лента, луч дрона, частицы), а не продолжает жить своей жизнью.
        anim_dt = 0.0 if self.paused else dt
        if self.sim.defeated and self.sim.breach_point is not None \
                and not self._defeat_shown:
            # Экран поражения показывает точку прорыва (M5): камера — туда.
            self._defeat_shown = True
            bx, by = self.sim.breach_point
            self.camera.snap_to(bx + 0.5, by + 0.5)
        self.renderer.draw_world(self.screen, self.sim, self.camera,
                                 show_lit_layer=self.debug.show_layers, dt=anim_dt,
                                 ghost_tile=None if self.sim.defeated else self.ghost_tile(),
                                 show_flow=self.debug.show_path)
        self.hud.draw(self.screen, self.sim, self.camera, self.fps,
                      window_open=self.item_window.visible)
        self.item_window.draw(self.screen, self.sim)
        self.hud.draw_hotbar(self.screen, self.sim,
                             tooltip=self.item_window.held is None)
        if self.paused:
            # Поверх окна: пауза работает и с открытым инвентарём, и её видно.
            self.hud.draw_pause(self.screen)
        if self.sim.defeated:
            self.hud.draw_defeat(self.screen, self.sim)
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
