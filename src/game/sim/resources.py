"""Контейнеры и инвентарь (M1 → M3.5). Рецепты и генератор забега — `data/` и `run_gen.py`.

Контейнер — **единственная модель хранения** в игре: сетка слотов со стаками,
как в Minecraft (🔧 `DRONE_SLOTS` слотов по 🔧 `SLOT_STACK_MAX` единиц). Свой
контейнер у дрона, свои — у лаборатории (из `data/buildings.py`), а на M4 ту же
модель получат склад, сборщик, турель с боеприпасами и робот. Отдельной
«логики инвентаря дрона» и «слотов лаборатории» нет и быть не должно.

Два правила, на которых держится вся экономика:

- **Стеки объединяются только по предмету.** Свойства живут на типе ресурса
  (plan/02), индивидуальных свойств у единицы нет, поэтому две единицы одного
  ресурса — всегда один стак, и лимит стека общий для всех контейнеров.
- **Количество сохраняется.** Любой перенос идёт через `transfer`: он ничего не
  создаёт и не теряет, а всё, что не влезло, остаётся у отправителя.

Порядок слотов — часть состояния: он не перестраивается при каждом добавлении,
поэтому сетка в интерфейсе не «прыгает» между кадрами. Переставить её можно
только явно — кнопкой «сортировать».
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.game import config


@dataclass
class Stack:
    """Стак: сколько единиц одного предмета лежит в одном слоте."""

    item_id: str
    count: int

    def copy(self) -> "Stack":
        return Stack(self.item_id, self.count)


class Container:
    """Сетка слотов фиксированного размера со стаками.

    Один и тот же класс — инвентарь дрона, слоты лаборатории, а дальше склад,
    турель и робот. Ничего не знает ни про pygame, ни про рецепты: только
    «положить / забрать / переложить».
    """

    def __init__(self, slots: int, stack_max: int = config.SLOT_STACK_MAX,
                 limits: Mapping[str, int] | None = None) -> None:
        if slots < 0 or stack_max < 1:
            raise ValueError("некорректный размер контейнера")
        self.stack_max = int(stack_max)
        #: Свой лимит для отдельных предметов (у построек он меньше: 🔧
        #: `BUILDING_STACK`). Лимит — свойство предмета, а не места (plan/02),
        #: поэтому контейнер только спрашивает его у таблицы предметов забега.
        self.limits: dict[str, int] = dict(limits or {})
        #: Пустой слот — None: так сразу видно свободные места для интерфейса.
        self.slots: list[Stack | None] = [None] * int(slots)

    def max_for(self, item_id: str) -> int:
        """Лимит стека для этого предмета."""
        return self.limits.get(item_id, self.stack_max)

    # ------------------------------------------------------------ размеры
    @property
    def size(self) -> int:
        return len(self.slots)

    @property
    def used_slots(self) -> int:
        return sum(1 for slot in self.slots if slot is not None)

    @property
    def free_slots(self) -> int:
        return self.size - self.used_slots

    @property
    def is_full(self) -> bool:
        return self.free_slots == 0

    @property
    def is_empty(self) -> bool:
        return self.used_slots == 0

    def total_count(self) -> int:
        """Сколько всего единиц лежит в контейнере (для проверок сохранности)."""
        return sum(slot.count for slot in self.slots if slot is not None)

    # ------------------------------------------------------------- запросы
    def count(self, item_id: str) -> int:
        return sum(s.count for s in self.slots if s is not None and s.item_id == item_id)

    def has(self, item_id: str, amount: int = 1) -> bool:
        return self.count(item_id) >= amount

    def items(self) -> list[tuple[str, int]]:
        """Сводка «id → количество» в порядке слотов (для рецептов и HUD)."""
        totals: dict[str, int] = {}
        for slot in self.slots:
            if slot is not None:
                totals[slot.item_id] = totals.get(slot.item_id, 0) + slot.count
        return list(totals.items())

    def fits(self, item_id: str, amount: int = 1, allow_empty: bool = True) -> int:
        """Сколько единиц ещё влезет (докладывая в существующие стаки и в пустые).

        `allow_empty=False` — считать только досыпание в уже занятые таким же
        предметом слоты. Так панель быстрого доступа принимает ресурс, не
        занимая под него пустой слот (plan/03: «ресурсы идут в инвентарь»).
        """
        if amount <= 0:
            return 0
        limit = self.max_for(item_id)
        free = 0
        for slot in self.slots:
            if slot is None:
                if allow_empty:
                    free += limit
            elif slot.item_id == item_id:
                free += limit - slot.count
            if free >= amount:
                return amount
        return free

    def can_accept(self, item_id: str, amount: int = 1,
                   allow_empty: bool = True) -> bool:
        """Влезет ли столько — не меняя состояния (для проверок до действия)."""
        return self.fits(item_id, amount, allow_empty) >= amount

    # -------------------------------------------------------------- запись
    def add(self, item_id: str, amount: int = 1, allow_empty: bool = True) -> int:
        """Кладёт предмет. Возвращает **остаток**, который не влез.

        `allow_empty=False` — только досыпать в существующие стаки.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        limit = self.max_for(item_id)
        left = int(amount)
        for slot in self.slots:                      # сначала досыпаем в стаки
            if left <= 0:
                return 0
            if slot is not None and slot.item_id == item_id and slot.count < limit:
                moved = min(limit - slot.count, left)
                slot.count += moved
                left -= moved
        if not allow_empty:
            return left
        for i, slot in enumerate(self.slots):        # потом занимаем пустые
            if left <= 0:
                return 0
            if slot is None:
                moved = min(limit, left)
                self.slots[i] = Stack(item_id, moved)
                left -= moved
        return left

    def add_stack(self, stack: Stack | None) -> Stack | None:
        """Кладёт стак целиком. Возвращает остаток стака (None — если влез весь)."""
        if stack is None or stack.count <= 0:
            return None
        rest = self.add(stack.item_id, stack.count)
        return Stack(stack.item_id, rest) if rest > 0 else None

    def remove(self, item_id: str, amount: int = 1) -> bool:
        """Забирает предмет. False — если не хватает (контейнер не меняется).

        Это «всё или ничего»: так крафт тратит материалы только целиком, а не
        половину рецепта. Кто хочет забрать сколько получится — `take`.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        if not self.has(item_id, amount):
            return False
        self.take(item_id, amount)
        return True

    def take(self, item_id: str, amount: int = 1) -> int:
        """Забирает до `amount` единиц. Возвращает, сколько реально снято.

        В отличие от `remove` берёт сколько есть — именно так работает перенос
        между контейнерами: не влезло к получателю, значит осталось здесь.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        left = int(amount)
        # Берём с конца: первые стаки остаются целыми, и сетка не «дышит».
        for i in range(len(self.slots) - 1, -1, -1):
            slot = self.slots[i]
            if left <= 0:
                break
            if slot is None or slot.item_id != item_id:
                continue
            moved = min(slot.count, left)
            slot.count -= moved
            left -= moved
            if slot.count == 0:
                self.slots[i] = None
        return int(amount) - left

    def clear(self) -> None:
        self.slots = [None] * self.size

    # --------------------------------------------------------- работа со слотами
    def slot(self, index: int) -> Stack | None:
        if not 0 <= index < self.size:
            return None
        return self.slots[index]

    def take_slot(self, index: int, amount: int | None = None) -> Stack | None:
        """Забирает из слота весь стак или его часть. None — если брать нечего."""
        slot = self.slot(index)
        if slot is None:
            return None
        take = slot.count if amount is None else min(slot.count, max(0, int(amount)))
        if take <= 0:
            return None
        slot.count -= take
        if slot.count == 0:
            self.slots[index] = None
        return Stack(slot.item_id, take)

    def put_slot(self, index: int, stack: Stack) -> Stack | None:
        """Кладёт стак в слот: досыпает в такой же, иначе меняет местами.

        Возвращает то, что осталось «в руке» (None — если положили всё).
        """
        if stack is None or stack.count <= 0 or not 0 <= index < self.size:
            return stack
        limit = self.max_for(stack.item_id)
        slot = self.slots[index]
        if slot is None:
            moved = min(limit, stack.count)
            self.slots[index] = Stack(stack.item_id, moved)
            rest = stack.count - moved
            return Stack(stack.item_id, rest) if rest > 0 else None
        if slot.item_id == stack.item_id and slot.count < limit:
            moved = min(limit - slot.count, stack.count)
            slot.count += moved
            rest = stack.count - moved
            return Stack(stack.item_id, rest) if rest > 0 else None
        # Разные предметы: обмен, как в Minecraft. Если в руке больше, чем
        # влезает в слот, обмена нет: иначе излишек пропал бы (закон
        # сохранения, plan/03) — рука остаётся как есть.
        if stack.count > limit:
            return stack
        self.slots[index] = Stack(stack.item_id, stack.count)
        return slot

    def move_slot(self, src: int, dst: int) -> bool:
        """Перекладывает слот в слот (обмен или досыпание). False — не вышло."""
        if src == dst:
            return False
        stack = self.slot(src)
        if stack is None:
            return False
        rest = self.put_slot(dst, stack)
        if rest is None:
            self.slots[src] = None
        elif rest.count == stack.count:
            return False                             # ничего не поменялось
        else:
            self.slots[src] = rest
        return True

    def split_slot(self, index: int) -> bool:
        """Делит стак пополам: половина уходит в первый свободный слот.

        False — если делить нечего (1 единица) или нет свободного места.
        """
        slot = self.slot(index)
        if slot is None or slot.count < 2:
            return False
        free = next((i for i, s in enumerate(self.slots) if s is None), None)
        if free is None:
            return False
        half = slot.count // 2
        slot.count -= half
        self.slots[free] = Stack(slot.item_id, half)
        return True

    def sort(self) -> None:
        """Раскладывает содержимое: по типу, потом по количеству (plan/03).

        Стаки одного предмета сначала сливаются (иначе сортировка оставила бы
        по два неполных стака), потом предметы идут по id, а внутри — по
        количеству. Ничего не создаёт и не теряет: слотов нужно не больше, чем
        было занято.
        """
        totals: dict[str, int] = {}
        for slot in self.slots:
            if slot is not None:
                totals[slot.item_id] = totals.get(slot.item_id, 0) + slot.count
        ordered = sorted(totals.items(), key=lambda pair: (pair[0], pair[1]))
        slots: list[Stack | None] = []
        for item_id, count in ordered:
            limit = self.max_for(item_id)
            while count > 0:
                part = min(limit, count)
                slots.append(Stack(item_id, part))
                count -= part
        slots.extend([None] * (self.size - len(slots)))
        self.slots = slots

    # ------------------------------------------------------------- перенос
    def transfer_to(self, other: "Container", index: int) -> int:
        """Перекидывает слот целиком в другой контейнер. Возвращает, сколько перешло."""
        return transfer(self, other, index)

    def transfer_all_to(self, other: "Container") -> int:
        """Перекидывает всё, что влезает. Возвращает, сколько перешло."""
        return transfer(self, other)

    # ---------------------------------------------------------- прочее
    def copy(self) -> "Container":
        clone = Container(self.size, self.stack_max, self.limits)
        clone.slots = [s.copy() if s is not None else None for s in self.slots]
        return clone

    def state(self) -> tuple:
        """Слепок для тестов и для подписи состояния симуляции."""
        return tuple((s.item_id, s.count) if s is not None else None for s in self.slots)

    def __len__(self) -> int:
        """Сколько слотов занято (а не сколько разных предметов)."""
        return self.used_slots

    def __contains__(self, item_id: object) -> bool:
        return self.count(str(item_id)) > 0


def transfer(src: Container, dst: Container, index: int | None = None,
             amount: int | None = None) -> int:
    """Перекладывает предметы между контейнерами. Возвращает, сколько перешло.

    Единственный способ передачи: окно предметов, снос здания, а дальше склад и
    роботы зовут именно его. Правило одно и оно проверяется тестом —
    **сумма предметов до и после равна**: всё, что не влезло в `dst`, остаётся
    у `src`, и ни одна единица не появляется из воздуха.

    `index=None` — переложить всё, что влезает (слот за слотом); иначе один слот
    целиком или `amount` единиц из него.
    """
    if index is None:
        return sum(transfer(src, dst, i) for i in range(src.size))
    stack = src.slot(index)
    if stack is None:
        return 0
    want = stack.count if amount is None else min(stack.count, max(0, int(amount)))
    if want <= 0:
        return 0
    moved = want - dst.add(stack.item_id, want)
    if moved > 0:
        src.take_slot(index, moved)
    return moved


def give(stack: Stack | None, *containers: Container) -> Stack | None:
    """Отдаёт стак в контейнеры по очереди. Возвращает то, что никуда не влезло.

    Тот же закон сохранения, что у `transfer`, только отправитель — не слот, а
    стак «в руке»: окно предметов возвращает так предмет при закрытии, а дальше
    так же будут передавать вещи роботы-курьеры (M7).
    """
    for container in containers:
        if stack is None or stack.count <= 0:
            return None
        rest = container.add(stack.item_id, stack.count)
        stack = Stack(stack.item_id, rest) if rest > 0 else None
    return stack



class Inventory(Container):
    """Инвентарь дрона: контейнер на 🔧 `DRONE_SLOTS` слотов.

    Отдельный класс — потому что размер у дрона свой, а дальше его можно будет
    увеличить модулем грузового отсека (plan/04).
    """

    def __init__(self, slots: int = config.DRONE_SLOTS,
                 stack_max: int = config.SLOT_STACK_MAX,
                 limits: Mapping[str, int] | None = None) -> None:
        super().__init__(slots, stack_max, limits)


class Hotbar(Container):
    """Панель быстрого доступа: 🔧 `HOTBAR_SLOTS` слотов под рукой (plan/03).

    Обычный контейнер — и это важно: предмет-постройка лежит в ней как любой
    другой предмет, перекладывается мышью и не требует отдельного хранилища.
    Отличие одно: в пустые слоты хотбара кладут только постройки (см. `Cargo`).
    """

    def __init__(self, slots: int = config.HOTBAR_SLOTS,
                 stack_max: int = config.SLOT_STACK_MAX,
                 limits: Mapping[str, int] | None = None) -> None:
        super().__init__(slots, stack_max, limits)


class Cargo:
    """Хранилище дрона целиком: хотбар и основной инвентарь вместе (plan/03).

    Правило «куда что кладётся» живёт здесь, а не в вызывающем коде, и оно
    одно на всю игру:

    - **предмет-постройка** идёт сначала в хотбар — оттуда её ставят, и
      оказаться в глубине инвентаря она не должна;
    - **ресурс** идёт в инвентарь и досыпается только в те стаки хотбара,
      которые уже есть: иначе добыча забила бы панель пустыми слотами.

    Запросы (`count`, `has`, `items`) и списание (`take`, `remove`) видят оба
    контейнера как одно хранилище: для крафта и для «сколько у меня этого»
    разницы между рукой и рюкзаком нет.
    """

    def __init__(self, hotbar: Container, inventory: Container,
                 table: "ItemTable") -> None:
        self.hotbar = hotbar
        self.inventory = inventory
        #: Таблица предметов забега. Имя с «table» намеренно: `items()` — метод
        #: сводки, и одноимённое поле перекрыло бы его.
        self.table = table

    # ---------------------------------------------------------- правило
    def is_building(self, item_id: str) -> bool:
        """Ставится ли этот предмет как здание."""
        item = self.table.get(item_id)
        return item is not None and item.building is not None

    # ----------------------------------------------------------- запись
    def fits(self, item_id: str, amount: int = 1) -> int:
        """Сколько единиц влезет с учётом правила (не меняет состояния)."""
        if amount <= 0:
            return 0
        into_hotbar = self.hotbar.fits(item_id, amount,
                                       allow_empty=self.is_building(item_id))
        if into_hotbar >= amount:
            return amount
        return min(amount, into_hotbar
                   + self.inventory.fits(item_id, amount - into_hotbar))

    def can_accept(self, item_id: str, amount: int = 1) -> bool:
        return self.fits(item_id, amount) >= amount

    def add(self, item_id: str, amount: int = 1) -> int:
        """Кладёт предмет по правилу. Возвращает остаток, который не влез."""
        left = self.hotbar.add(item_id, amount,
                               allow_empty=self.is_building(item_id))
        if left > 0:
            left = self.inventory.add(item_id, left)
        return left

    # ---------------------------------------------------------- запросы
    def count(self, item_id: str) -> int:
        return self.hotbar.count(item_id) + self.inventory.count(item_id)

    def has(self, item_id: str, amount: int = 1) -> bool:
        return self.count(item_id) >= amount

    def items(self) -> list[tuple[str, int]]:
        """Сводка «id → количество» в порядке слотов: сначала рука, потом рюкзак."""
        totals: dict[str, int] = {}
        for container in (self.hotbar, self.inventory):
            for item_id, count in container.items():
                totals[item_id] = totals.get(item_id, 0) + count
        return list(totals.items())

    def total_count(self) -> int:
        return self.hotbar.total_count() + self.inventory.total_count()

    def copy(self) -> "Cargo":
        """Копия обоих контейнеров — чтобы проверить «влезет ли» до действия."""
        return Cargo(self.hotbar.copy(), self.inventory.copy(), self.table)

    # ---------------------------------------------------------- списание
    def take(self, item_id: str, amount: int = 1) -> int:
        """Забирает до `amount` единиц. Сначала из инвентаря, потом из хотбара.

        Такой порядок бережёт панель: стак «под рукой» остаётся целым, пока в
        рюкзаке есть чем заплатить.
        """
        got = self.inventory.take(item_id, amount)
        if got < amount:
            got += self.hotbar.take(item_id, amount - got)
        return got

    def remove(self, item_id: str, amount: int = 1) -> bool:
        """Забирает ровно столько. False — если не хватает (ничего не снято)."""
        if amount < 0:
            raise ValueError("amount must be >= 0")
        if not self.has(item_id, amount):
            return False
        self.take(item_id, amount)
        return True
