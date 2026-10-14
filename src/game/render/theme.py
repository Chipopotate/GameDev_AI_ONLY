"""Единственное место цветов (plan/09 §2).

Здесь лежит вся палитра: земля, сталь построек, неон игрока, цвета заражения
и интерфейса. Модуль **не импортирует pygame** — на него смотрит и `data/`
(цвета тайлов), поэтому он должен оставаться чистыми данными плюс четыре
функции над цветом. «Магических» цветов в остальном коде быть не должно:
если понадобился новый оттенок — он появляется здесь.

Правило языка форм (plan/09 §1): игрок — холодный неон и строгая геометрия,
заражение — ядовитые цвета и неровные контуры. Поэтому `NEON_*` и `FOE_*`
разведены по разным половинам палитры, и путать их нельзя.
"""

from __future__ import annotations

import colorsys

Color = tuple[int, int, int]


def rgb(value: str) -> Color:
    """`"#38e8ff"` → `(56, 232, 255)`. Палитра в документах задана hex-ом."""
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# ------------------------------------------------------------------ земля
# Тёмная трава с зеленцой (решение автора, 2026-10-14): почти чёрная сине-серая
# земля читалась как «пустота», и горы на ней терялись. Трава остаётся тёмной —
# на ней по-прежнему видны неон, тёплый свет ламп и ядовитые пятна заражения.
# Варианты идут от тёмного к светлому: рендер выбирает их по плавному шуму, и
# соседние тайлы складываются в луговые пятна, а не в шахматную доску.
GROUND_VARIANTS: tuple[Color, ...] = (
    rgb("#17251b"), rgb("#1a2a1e"), rgb("#1d2e20"), rgb("#203223"),
)
GROUND_OLIVE = rgb("#262d1b")       # оливковые проплешины поверх травы
GRASS_BLADE = rgb("#35553a")        # травинки-пучки
GRASS_BLADE_DARK = rgb("#0f1912")   # тень у корня пучка
GROUND_PEBBLE = rgb("#0c140f")      # редкие «камешки» тёмными точками (plan/09 §4)
GROUND_STONE = rgb("#4a5052")       # редкий светлый камень на траве
# Сетка тайлов рисуется умножением: этот серый темнит тайл на ~10% и
# остаётся «едва заметным», чего заливка не даёт.
GRID_TINT = (228, 228, 228)

# ------------------------------------------------------------------- горы
# Гора — светлый каменный массив над тёмной травой: грани «пирамидок» освещены
# сверху-слева, у подошвы — тёмный обрыв, на траву под ним падает тень. Пока
# масса горы была одного тона с землёй, гору было не отличить от равнины.
MOUNTAIN_BASE = rgb("#434853")      # камень, средний тон
MOUNTAIN_LIT = rgb("#636a7b")       # грани к свету (верх-лево)
MOUNTAIN_DARK = rgb("#252931")      # грани от света (низ-право)
MOUNTAIN_CLIFF = rgb("#1c1f26")     # обрыв у подошвы — он и даёт высоту
MOUNTAIN_STRATA = rgb("#121419")    # слои породы на обрыве
MOUNTAIN_RIM = rgb("#8790a4")       # освещённая кромка массива
MOUNTAIN_SNOW = rgb("#c3cede")      # снег на пиках в глубине массива

# ------------------------------------------------------------------ вода
WATER_DEEP = rgb("#0b2136")
WATER_BASE = rgb("#123150")
WATER_GLINT = rgb("#3a7bb0")        # синусоидальный блик
WATER_RIM = rgb("#6cb8e6")          # светлая кромка у берега

# -------------------------------------------------------------- заражение
INFECT_DARK = rgb("#2a0f3a")
INFECT_MID = rgb("#5b1670")
INFECT_VEIN = rgb("#d23cff")
INFECT_SPORE = rgb("#f2b0ff")

# ----------------------------------------------------------------- сталь
STEEL_DARK = rgb("#2a3140")
STEEL_MID = rgb("#363f52")
STEEL_LIGHT = rgb("#454f63")
OUTLINE = rgb("#0a0d12")            # тёмный контур всех построек

# ------------------------------------------------------------ неон игрока
NEON_CYAN = rgb("#38e8ff")          # базовый неон
NEON_AZURE = rgb("#4aa8ff")         # логистика, провода
NEON_AMBER = rgb("#ffb23d")         # свет и энергия
NEON_WHITE = rgb("#eaf6ff")         # дрон, ядро
NEON_ICE = rgb("#9fe7ff")           # блики

# ---------------------------------------------------------------- враги
FOE_MAGENTA = rgb("#d23cff")
FOE_TOXIC = rgb("#9dff3b")
FOE_BILIOUS = rgb("#ff7a1a")
FOE_BLOOD = rgb("#8a1230")
FOE_FLESH = rgb("#3a1030")          # тело врага: тёмное, светятся только ядра

# ------------------------------------------------------------ интерфейс
UI_BG = (16, 18, 26)
UI_BG_ALPHA = 216
UI_PANEL = (26, 30, 42)
UI_PANEL_LIT = (38, 44, 60)         # фаска панели: верх-лево светлее
UI_BORDER = (66, 74, 94)
UI_TEXT = rgb("#e2e8f4")
UI_TEXT_DIM = rgb("#8e98ac")
UI_TEXT_WARN = rgb("#f0b060")

# Смысловые цвета интерфейса
PLAYER = NEON_CYAN
PLAYER_DARK = rgb("#2c8494")
PLAYER_CORE = NEON_WHITE
ACCENT = NEON_AZURE
DANGER = rgb("#e8545c")
WARN = NEON_AMBER
OK = rgb("#80d680")

# Мир: подсветка выбора и полосы прогресса
DEPOSIT_DEPLETED = rgb("#565860")
SELECT_RING = rgb("#fff0aa")
HOVER_RING = rgb("#969eb2")
MINE_BAR_BG = rgb("#14161e")
MINE_BAR_FG = rgb("#ffd66e")
RELIC_RING = rgb("#aad6ff")
RELIC_STUDIED = rgb("#606878")
SCAN_BAR_FG = rgb("#8cc8ff")
STUDY_BAR_FG = rgb("#c4a8ff")

# Слоты и окна предметов
SLOT_BG = (32, 36, 50)
SLOT_BORDER = (62, 70, 90)
SLOT_ACTIVE = (240, 200, 120)
SLOT_HOVER = (110, 124, 156)
MENU_BG = (22, 25, 36)
MENU_HOVER = (44, 52, 72)
DROP_RING = rgb("#fff0aa")

# Панель быстрого доступа (plan/03): подложка всегда на экране, под ней мир,
# поэтому она темнее окон и почти не просвечивает.
HOTBAR_BG = (16, 19, 28)
HOTBAR_SLOT = (30, 34, 46)
HOTBAR_SELECT = rgb("#ffd66e")
HOTBAR_KEY = rgb("#9aa4b8")         # цифра-подсказка 1…9 в углу слота

# ---------------------------------------------------------- постройка и луч
# Призрак постройки: зелёный — ставить можно, красный — нельзя (plan/09 §12).
# Цвет подмешивается к спрайту здания умножением, поэтому берётся яркий и
# насыщенный: тусклый оттенок на тёмном спрайте не читался бы.
GHOST_OK = rgb("#5ee88a")
GHOST_BAD = rgb("#ff6a5a")
GHOST_OUTLINE = rgb("#eaf6ff")      # тонкая рамка зоны постройки
# Кольцо прогресса демонтажа над зданием: тот же «жгущий» красный, что и луч.
DISMANTLE_RING = rgb("#ff8a6a")
DISMANTLE_RING_BG = rgb("#2a1410")

# Луч дрона (plan/09 §10). Смысл цвета — «что дрон делает»: красный жжёт
# (добыча, разбор), синий читает (изучение, скан). Поэтому таблица ниже, а не
# пара констант по месту: перепутать их нельзя.
LASER_RED = rgb("#ff5a3c")
LASER_RED_CORE = rgb("#ffd9c4")
LASER_BLUE = rgb("#5cc8ff")
LASER_BLUE_CORE = rgb("#dff2ff")
LASER_GREEN = rgb("#5cf0a0")        # ремонт (M6): «строит», а не жжёт и не читает
LASER_GREEN_CORE = rgb("#e2ffef")
ARTILLERY_RING = rgb("#ff7a1a")

#: Вид действия дрона (`sim/units.py`) → цвет луча и его ядра. Единственное
#: место этого соответствия: и рендер, и тесты берут цвета отсюда.
ACTION_COLORS: dict[str, Color] = {
    "mine": LASER_RED,
    "dismantle": LASER_RED,
    "study": LASER_BLUE,
    "scan": LASER_BLUE,
    "repair": LASER_GREEN,
}

#: Ядро луча — по тому же виду действия.
ACTION_CORES: dict[str, Color] = {
    "mine": LASER_RED_CORE,
    "dismantle": LASER_RED_CORE,
    "study": LASER_BLUE_CORE,
    "scan": LASER_BLUE_CORE,
    "repair": LASER_GREEN_CORE,
}


def laser_color(kind: str | None) -> Color:
    """Цвет луча для вида действия. Неизвестный вид — синий «чтения»."""
    return ACTION_COLORS.get(kind or "", LASER_BLUE)


def laser_core(kind: str | None) -> Color:
    return ACTION_CORES.get(kind or "", LASER_BLUE_CORE)

# Бой (M5): следы выстрелов и полосы прочности.
SHOT_TURRET = NEON_AMBER
SHOT_DRONE = NEON_ICE
SHOT_ENEMY = FOE_TOXIC
HP_BAR_FG = rgb("#e8545c")
ENEMY_EYE = FOE_TOXIC
BREACH_RING = rgb("#ff3b5c")
TURRET_BARREL = rgb("#1a1f29")

# Экономика и энергия
POWER_OFF = rgb("#464a58")          # куда уходит цвет здания без энергии
WIRE_LINE = NEON_AZURE
WIRE_FLOW = NEON_WHITE              # бегущая по проводу точка
CONVEYOR_CHEVRON = rgb("#0a0d12")   # шевроны на ленте
CONVEYOR_BELT = rgb("#1a1f29")
POWER_OK = NEON_AMBER
POWER_MISSING = rgb("#ff6a5a")      # мигающий значок «нет питания»

# Свет и тьма
LAMP_BODY = rgb("#3a4152")
LAMP_GLOW = rgb("#ffd98a")
LAMP_LIGHT = rgb("#ffdc8c")         # цвет пятна света лампы (тёплый, plan/09 §9)
LAMP_OFF = rgb("#5a5f6c")
# Контуры радиусов — тусклые: пять ламп рядом дают пять колец, и яркие кольца
# превращали полосу базы в мишень (визуальный мусор, plan/09 §12.6).
LAMP_RADIUS = rgb("#7a7046")
DRONE_LIGHT = rgb("#a8d2ff")        # холодное пятно света дрона
DRONE_RADIUS = rgb("#5c7d9c")
DARKNESS = rgb("#03050c")
LIGHT_ACCENT = rgb("#ffec96")
DARK_TILE = rgb("#304ca8")

# Атмосфера (plan/09 §9)
DAY_TINT = rgb("#b8ccff")           # день «чуть холоднее и светлее»
# Ночь «тёмная и синяя»: оттенок обязан быть темнее самой тьмы, иначе он не
# красит ночь, а подсвечивает её — экран переставал темнеть к ночи, а тёплый
# свет лампы синел. Прежний #1c2a55 был светлее ночной земли.
NIGHT_TINT = rgb("#0c1438")
VIGNETTE = rgb("#000000")

# Отладка
DEBUG_BG = (8, 10, 14)
DEBUG_TEXT = rgb("#96f0aa")


# ------------------------------------------------------------ функции цвета
def clamp_color(color) -> Color:
    return tuple(max(0, min(255, int(round(c)))) for c in color[:3])  # type: ignore[return-value]


def mix(a: Color, b: Color, t: float) -> Color:
    """Смешивает два цвета: t = 0 — первый, t = 1 — второй."""
    t = max(0.0, min(1.0, t))
    return clamp_color(tuple(x + (y - x) * t for x, y in zip(a, b)))


def lighten(color: Color, k: float) -> Color:
    """Фаска «верх-лево»: тот же оттенок, но ближе к белому (plan/09 §2)."""
    return mix(color, (255, 255, 255), k)


def darken(color: Color, k: float) -> Color:
    """Фаска «низ-право» и тени."""
    return mix(color, (0, 0, 0), k)


def scale(color: Color, k: float) -> Color:
    """Умножает яркость, сохраняя оттенок."""
    return clamp_color(tuple(c * k for c in color))


def with_alpha(color: Color, alpha: int) -> tuple[int, int, int, int]:
    """Цвет с альфой 0..255 — для SRCALPHA-поверхностей."""
    return (color[0], color[1], color[2], max(0, min(255, int(alpha))))


def hue_shift(color: Color, deg: float) -> Color:
    """Сдвиг оттенка в градусах: соседние ресурсы не должны сливаться (§7)."""
    r, g, b = (c / 255.0 for c in color)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h = (h + deg / 360.0) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return clamp_color((r * 255, g * 255, b * 255))


def brighten_towards(color: Color, target: Color, k: float) -> Color:
    """Подмешивает целевой оттенок — так тьма и свет окрашивают сцену."""
    return mix(color, target, k)
