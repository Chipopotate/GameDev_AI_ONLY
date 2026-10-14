"""Время суток: день и ночь по таблице планеты (plan/01). Не импортирует pygame.

Длины фаз фиксированы внутри забега: первый день самый длинный, дальше обычный
день и ночь. От планеты к планете день сжимается, а ночь растёт.
"""

from __future__ import annotations

from src.game import config

# Фазы
DAY = "day"
NIGHT = "night"

# События смены фазы. Что делать при них, решает симуляция:
# закат начисляет очки роста росткам, рассвет обрушивает горы по расписанию
# (M8, `Simulation._on_dusk`/`_on_dawn`) — часы о них не знают.
EVENT_DAWN = "dawn"
EVENT_DUSK = "dusk"


class TimeCycle:
    """Чередование день/ночь. Часы забега."""

    def __init__(self, planet: int = config.PLANET) -> None:
        first_day, day, night = config.TIME_TABLE[planet]
        self.planet = planet
        self.first_day_length = float(first_day)
        self.day_length = float(day)
        self.night_length = float(night)
        self.day_number = 1          # нумерация дней с единицы
        self.phase = DAY
        self.t_in_phase = 0.0        # секунд прошло в текущей фазе

    # ------------------------------------------------------------- свойства
    @property
    def phase_length(self) -> float:
        """Длина текущей фазы. Первый день длиннее остальных."""
        if self.phase == NIGHT:
            return self.night_length
        return self.first_day_length if self.day_number == 1 else self.day_length

    @property
    def t_left(self) -> float:
        """Сколько секунд осталось до смены фазы."""
        return max(0.0, self.phase_length - self.t_in_phase)

    @property
    def is_night(self) -> bool:
        return self.phase == NIGHT

    @property
    def darkness(self) -> float:
        """0 — светло, 1 — полная тьма.

        Днём всегда 0. Ночью 1, но с плавным входом и выходом: без этого экран
        щёлкал бы мгновенно на закате и рассвете.
        """
        if not self.is_night:
            return 0.0
        fade = min(config.DARKNESS_FADE, self.phase_length * 0.5)
        if fade <= 0.0:
            return 1.0
        return max(0.0, min(1.0, min(self.t_in_phase, self.t_left) / fade))

    # ------------------------------------------------------------ обновление
    def update(self, dt: float) -> tuple[str, ...]:
        """Продвигает часы на `dt` секунд. Возвращает случившиеся события фазы."""
        if dt <= 0.0:
            return ()
        events: list[str] = []
        self.t_in_phase += dt
        # Цикл, а не `if`: длинный шаг (или тест с большим dt) может перескочить
        # через несколько фаз сразу. Длину фазы перечитываем — после рассвета
        # она меняется с «первого дня» на обычную.
        while self.t_in_phase >= self.phase_length:
            self.t_in_phase -= self.phase_length
            if self.phase == DAY:
                self.phase = NIGHT
                events.append(EVENT_DUSK)
            else:
                self.phase = DAY
                self.day_number += 1
                events.append(EVENT_DAWN)
        return tuple(events)

    def state(self) -> tuple[int, str, float]:
        """Короткий слепок часов — для тестов и отладочной панели."""
        return self.day_number, self.phase, round(self.t_in_phase, 6)
