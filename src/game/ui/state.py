"""Общее состояние интерфейса: какой элемент сейчас забирает клавиатуру.

Флаг `text_input_active` — единственное место, где решается «клавиши идут в
поле ввода, а не в игру» (plan/10). Приложение спрашивает его один раз — в
начале разбора клавиш и при чтении WASD, — а не обходит поле поиска в каждом
обработчике по отдельности.
"""

from __future__ import annotations


class UiState:
    """Фокус ввода. Поле, получившее фокус, само вызывает `focus`/`blur`."""

    def __init__(self) -> None:
        self.focused = None

    @property
    def text_input_active(self) -> bool:
        """Идёт ли сейчас ввод текста (WASD, цифры, E, R не действуют в игре)."""
        return self.focused is not None

    def focus(self, field) -> None:
        self.focused = field

    def blur(self, field=None) -> None:
        """Снимает фокус (с этого поля или с любого, если поле не указано)."""
        if field is None or self.focused is field:
            self.focused = None
