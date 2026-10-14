"""Защита архитектуры: правила проекта, которые легко нарушить случайно.

Главное правило — `src/game/sim/` не знает про pygame. Если кто-то однажды
импортирует pygame в симуляции, тесты это поймают сразу.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.game import config
from src.game.data.resources import Prop

ROOT = Path(__file__).resolve().parent.parent
SIM_DIR = ROOT / "src" / "game" / "sim"

# Именно импорт, а не упоминание в комментарии или docstring.
PYGAME_IMPORT = re.compile(r"^\s*(?:import|from)\s+pygame\b", re.MULTILINE)


def test_sim_package_has_no_pygame_in_source():
    offenders = [p.name for p in SIM_DIR.glob("*.py")
                 if PYGAME_IMPORT.search(p.read_text("utf-8"))]
    assert offenders == [], f"pygame импортируется в симуляции: {offenders}"


def test_sim_imports_without_pygame_in_fresh_process():
    """Импорт симуляции не должен тянуть pygame даже транзитивно."""
    code = (
        "import sys;"
        "import src.game.sim.simulation, src.game.sim.world, src.game.sim.units,"
        "src.game.sim.resources, src.game.sim.time_cycle, src.game.sim.light,"
        "src.game.sim.run_gen, src.game.sim.research, src.game.sim.buildings,"
        "src.game.sim.drops, src.game.sim.power,"
        "src.game.data.resources, src.game.data.recipes, src.game.data.relics,"
        "src.game.data.buildings, src.game.config;"
        "assert 'pygame' not in sys.modules, sorted(m for m in sys.modules if 'pygame' in m);"
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_sim_does_not_use_global_random():
    """В симуляции разрешён только random.Random(seed), не глобальный random."""
    for path in SIM_DIR.glob("*.py"):
        source = path.read_text("utf-8")
        assert "random.seed(" not in source, f"{path.name}: глобальный random.seed"
        assert "random.randint(" not in source, f"{path.name}: глобальный random.randint"
        assert "random.random(" not in source, f"{path.name}: глобальный random.random"


def test_config_values_are_sane():
    assert config.TICK > 0
    assert 0 < config.MAX_FRAME_DT <= 0.5
    assert config.MAP_W > config.START_ZONE and config.MAP_H > config.START_ZONE
    assert config.TILE > 0 and config.CHUNK > 0
    assert config.ZOOM_MIN < config.ZOOM_DEFAULT < config.ZOOM_MAX
    assert config.MINE_TIME_PER_UNIT > 0, "иначе цикл добычи зациклится"
    assert config.DRONE_SPEED > 0 and config.DRONE_RADIUS > 0
    assert config.MOUNTAIN_RING_THICKNESS >= 1
    assert config.DEPOSIT_AMOUNT > 0
    assert config.TOAST_MAX > 0 and config.TOAST_TTL > 0
    # M3: ресурсы забега, скан, реликвии, лаборатория
    assert config.RESOURCES_PER_RUN >= 4, "иначе забег не покроет все свойства"
    assert 1 <= config.RESOURCE_PROPS_MAX <= len(list(Prop))
    assert 2 <= config.RARE_PROPS <= len(list(Prop))
    assert config.RARE_DEPOSITS >= 1 and config.RARE_DEPOSIT_AMOUNT > 0
    assert config.HINT_PROPS >= 1
    assert config.SCAN_RANGE >= config.MINE_RANGE, "скан не ближе добычи"
    assert config.SCAN_TIME > 0 and config.SCAN_REVEALS >= 1
    assert config.AMP_RELICS <= config.BASE_RELICS
    assert config.RELIC_STUDY_TIME > 0 and config.RELIC_STUDY_RANGE > 0
    assert config.RELIC_MIN_SPACING >= 2
    assert config.LAB_ANALYZE_TIME > 0 and config.LAB_SLOTS >= 1
    assert config.LAB_SAMPLE_COST >= 1
    # M3.5: инвентарь слоты/стаки и брошенные предметы
    assert config.DRONE_SLOTS >= 1 and config.SLOT_STACK_MAX >= 1
    assert config.DROP_PICKUP_DELAY >= 0
    assert 0 < config.PICKUP_RADIUS < config.DROP_DISTANCE, \
        "иначе дрон подберёт свой же выброшенный стак"
    # Предметы игрока не исчезают: таймер либо выключен, либо длиннее мигания.
    assert config.DROP_DESPAWN_SECONDS is None or \
        config.DROP_DESPAWN_SECONDS > config.DROP_BLINK_TIME
    assert config.DROP_BLINK_HZ > 0
    # Снос здания: доля выпадающего и радиус поиска свободного тайла.
    assert 0.0 <= config.DESTROY_DROP_RATIO <= 1.0
    assert config.DESTROY_SCATTER_RADIUS >= 1
    # M2: время суток и свет
    assert config.PLANET in config.TIME_TABLE
    assert sorted(config.TIME_TABLE) == [1, 2, 3]
    for first_day, day, night in config.TIME_TABLE.values():
        assert first_day >= day > 0, "первый день не короче обычного"
        assert night > 0, "нулевая ночь зациклила бы часы"
    assert config.DARKNESS_FADE > 0
    assert 0 < config.NIGHT_DARKNESS_ALPHA <= 255
    assert config.LAMP_LIGHT_RADIUS > 0 and config.DRONE_LIGHT_RADIUS > 0
    assert config.LAMP_PLACE_RANGE >= 0
    # M4: экономика и энергия
    assert config.BUILD_RANGE >= config.LAMP_PLACE_RANGE, \
        "здание не должно ставиться дальше лампы"
    assert config.CONTAINER_RANGE > 0
    assert config.GENERATOR_FUEL_TIME > 0, "иначе топливо сгорит мгновенно"
    assert config.DRILL_TIME_PER_UNIT > 0 and config.CONVEYOR_MOVE_TIME > 0
    assert config.CONVEYOR_BATCH >= 1 and config.ASSEMBLER_SPEED > 0
    assert config.LAMP_POWER_USE > 0, "лампа должна что-то тратить (M4)"
    assert config.SCAN_ENERGY_COST >= 1


def test_start_zone_fits_in_map():
    assert config.START_ZONE + 2 * config.MOUNTAIN_RING_THICKNESS < min(
        config.MAP_W, config.MAP_H)


def test_expected_modules_exist():
    """Структура из CLAUDE.md: модули появляются по мере этапов."""
    game_dir = ROOT / "src" / "game"
    assert (ROOT / "main.py").is_file()
    assert (game_dir / "config.py").is_file()
    for name in ("data", "sim", "render", "ui"):
        assert (game_dir / name).is_dir(), f"нет пакета {name}"
        assert (game_dir / name / "__init__.py").is_file(), f"{name} без __init__.py"
    for name in ("world.py", "simulation.py", "units.py", "resources.py",
                 "time_cycle.py", "light.py", "run_gen.py", "research.py",
                 "buildings.py", "drops.py", "power.py"):
        assert (SIM_DIR / name).is_file(), f"нет sim/{name}"


@pytest.mark.parametrize("module", [
    "src.game.config",
    "src.game.data.resources",
    "src.game.data.terrain",
    "src.game.data.recipes",
    "src.game.data.relics",
    "src.game.data.buildings",
    "src.game.sim.world",
    "src.game.sim.units",
    "src.game.sim.resources",
    "src.game.sim.simulation",
    "src.game.sim.time_cycle",
    "src.game.sim.light",
    "src.game.sim.run_gen",
    "src.game.sim.research",
    "src.game.sim.buildings",
    "src.game.sim.drops",
    "src.game.sim.power",
])
def test_module_imports_cleanly(module):
    __import__(module)
