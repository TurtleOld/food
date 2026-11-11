// Календарь даты: поповер под кнопкой даты. Собственный календарь заменяет
// нативный `<input type="date">`: showPicker() есть не во всех браузерах, а
// скрытый input в старом Safari не открывался вообще. Здесь же можно дать
// календарю понятные подписи, 44-пиксельные цели и клавиатурную сетку.
// Русский формат даты фиксирован здесь, а не берётся из locale браузера:
// подписи дней всегда DD-MM-YYYY; кнопка даты задаёт тот же формат в шаблоне.
const MONTHS = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];
const WEEKDAYS_FULL = [
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
  "воскресенье",
];

const pad = (value) => String(value).padStart(2, "0");
const toIso = (date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const formatDate = (date) =>
  `${pad(date.getDate())}-${pad(date.getMonth() + 1)}-${date.getFullYear()}`;
const fromIso = (value) => {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
};
const addDays = (date, days) => {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
};
// Неделя всегда начинается с понедельника, независимо от локали браузера.
const WEEK_START = 1;
const weekdayIndex = (date) => (date.getDay() - WEEK_START + 7) % 7;

document.addEventListener("alpine:init", () => {
  Alpine.data("dateCalendar", () => ({
    open: false,
    selected: "",
    today: "",
    dayUrl: "",
    viewYear: 0,
    viewMonth: 0,
    focusIso: "",

    init() {
      const data = this.$el.dataset;
      this.selected = data.selected;
      this.today = data.today;
      this.dayUrl = data.dayUrl;
      const selected = fromIso(this.selected);
      this.viewYear = selected.getFullYear();
      this.viewMonth = selected.getMonth();
      this.focusIso = this.selected;

      // Клик вне календаря — самое ожидаемое закрытие после Escape; слушатель
      // на документе нужен потому, что клик может прийти на любой элемент фона.
      this.onPointerDown = (event) => {
        if (this.open && !this.$el.contains(event.target)) this.close(false);
      };
      document.addEventListener("pointerdown", this.onPointerDown);
    },

    destroy() {
      document.removeEventListener("pointerdown", this.onPointerDown);
    },

    get title() {
      return `${MONTHS[this.viewMonth]} ${this.viewYear}`;
    },

    get cells() {
      // Сетка всегда шесть недель: высота календаря не прыгает при смене месяца.
      const first = new Date(this.viewYear, this.viewMonth, 1);
      const start = addDays(first, -weekdayIndex(first));
      const tabbable = this.tabbableIso();
      return Array.from({ length: 42 }, (_, index) => {
        const date = addDays(start, index);
        const value = toIso(date);
        return {
          iso: value,
          day: date.getDate(),
          inMonth:
            date.getFullYear() === this.viewYear && date.getMonth() === this.viewMonth,
          isToday: value === this.today,
          isSelected: value === this.selected,
          tabbable: value === tabbable,
          href: this.dayUrl.replace("0000-00-00", value),
          label: this.dayLabel(date, value),
        };
      });
    },

    dayLabel(date, value) {
      const weekday = WEEKDAYS_FULL[weekdayIndex(date)];
      let label = `${formatDate(date)}, ${weekday}`;
      if (value === this.today) label += ", сегодня";
      if (value === this.selected) label += ", выбранный день";
      return label;
    },

    tabbableIso() {
      if (this.isInView(this.focusIso)) return this.focusIso;
      return toIso(new Date(this.viewYear, this.viewMonth, 1));
    },

    isInView(value) {
      const date = fromIso(value);
      return date.getFullYear() === this.viewYear && date.getMonth() === this.viewMonth;
    },

    toggle() {
      if (this.open) this.close();
      else this.show();
    },

    show() {
      const selected = fromIso(this.selected);
      this.viewYear = selected.getFullYear();
      this.viewMonth = selected.getMonth();
      this.focusIso = this.selected;
      this.open = true;
      this.$nextTick(() => this.focusDate(this.selected));
    },

    close(restoreFocus = true) {
      if (!this.open) return;
      this.open = false;
      if (restoreFocus) this.$nextTick(() => this.$refs.trigger.focus());
    },

    focusDate(value) {
      if (!this.open) return;
      this.$refs.grid?.querySelector(`[data-date="${value}"]`)?.focus({ preventScroll: true });
    },

    // Tab — обычный переход по ссылкам сетки; когда фокус уходит из календаря,
    // поповер закрывается. relatedTarget === null означает, что узел просто
    // исчез при смене месяца: тогда фокус ещё вернёт focusDate, а не закроет
    // календарь. Клик вне календаря закрывает отдельный pointerdown.
    onFocusOut(event) {
      if (!this.open || !event.relatedTarget) return;
      if (this.$refs.calendar.contains(event.relatedTarget)) return;
      this.close(false);
    },

    stepMonth(delta) {
      const next = new Date(this.viewYear, this.viewMonth + delta, 1);
      this.viewYear = next.getFullYear();
      this.viewMonth = next.getMonth();
      this.focusIso = toIso(next);
    },

    moveFocus(date) {
      this.focusIso = toIso(date);
      this.viewYear = date.getFullYear();
      this.viewMonth = date.getMonth();
      this.$nextTick(() => this.focusDate(this.focusIso));
    },

    shiftMonth(date, delta) {
      const target = new Date(date.getFullYear(), date.getMonth() + delta, 1);
      const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
      target.setDate(Math.min(date.getDate(), lastDay));
      return target;
    },

    onKeydown(event) {
      const current = fromIso(this.focusIso);
      let target = null;
      switch (event.key) {
        case "ArrowLeft":
          target = addDays(current, -1);
          break;
        case "ArrowRight":
          target = addDays(current, 1);
          break;
        case "ArrowUp":
          target = addDays(current, -7);
          break;
        case "ArrowDown":
          target = addDays(current, 7);
          break;
        case "Home":
          target = addDays(current, -weekdayIndex(current));
          break;
        case "End":
          target = addDays(current, 6 - weekdayIndex(current));
          break;
        case "PageUp":
          target = this.shiftMonth(current, event.shiftKey ? -12 : -1);
          break;
        case "PageDown":
          target = this.shiftMonth(current, event.shiftKey ? 12 : 1);
          break;
        default:
          return;
      }
      event.preventDefault();
      this.moveFocus(target);
    },
  }));
});
