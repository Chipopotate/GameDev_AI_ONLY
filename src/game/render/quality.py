"""Уровень графики (plan/09 §3.7).

`high` — свечение, размытие emissive, виньетка и цветокоррекция;
`medium` — без размытия emissive (свечение рисуется плоскими ореолами);
`low` — ни свечения, ни виньетки: для слабых машин и для замеров.

Значение по умолчанию — из `config.GRAPHICS_QUALITY` (🔧). Тесты и замеры
могут переопределить его на время (`override`), не трогая конфиг.
"""

from __future__ import annotations

from contextlib import contextmanager

from src.game import config

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
ORDER = (LOW, MEDIUM, HIGH)

_override: str | None = None


def current() -> str:
    """Действующий уровень графики."""
    if _override is not None:
        return _override
    value = getattr(config, "GRAPHICS_QUALITY", HIGH)
    return value if value in ORDER else HIGH


def level() -> int:
    """Уровень числом: 0 — low, 1 — medium, 2 — high."""
    return ORDER.index(current())


def at_least(name: str) -> bool:
    """Включена ли возможность уровня `name` при текущем уровне."""
    return level() >= ORDER.index(name)


@contextmanager
def override(name: str):
    """Временно переключает уровень графики (тесты, галерея, замеры)."""
    global _override
    previous = _override
    _override = name
    try:
        yield name
    finally:
        _override = previous


#: Короткие флаги возможностей — читаются лучше, чем сравнение уровней на месте.
def glow_enabled() -> bool:
    return at_least(MEDIUM)


def blur_enabled() -> bool:
    return at_least(HIGH)


def vignette_enabled() -> bool:
    return at_least(MEDIUM)


def grade_enabled() -> bool:
    return at_least(MEDIUM)
