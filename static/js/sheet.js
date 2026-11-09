// Шторка: открыта/закрыта и «есть несохранённые правки» — эфемерное состояние страницы-хозяина.
// Фокус держится внутри шторки, а фон становится inert: так Tab не уходит на ссылки
// дневника под ней, и после закрытия фокус возвращается туда, откуда шторку открыли.
const SHEET_FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

document.addEventListener("alpine:init", () => {
  Alpine.data("sheetHost", () => ({
    sheetOpen: false,
    dirty: false,
    opener: null,
    inerted: [],

    init() {
      // `$el` в обработчике указывает на кликнутый элемент, поэтому корень
      // страницы-хозяина запоминаем один раз при инициализации.
      this.host = this.$root ?? this.$el;
      this.onAfterSwap = (event) => {
        if (!this.sheetOpen) return;
        const target = event.detail.target;
        if (!target || !this.sheet) return;
        if (!this.sheet.contains(target)) {
          // htmx мог заменить фон (например, ленту после сохранения) — новый узел
          // тоже должен стать inert, иначе под открытой шторкой появится фокус.
          this.setBackgroundInert(true);
          return;
        }
        // Поиск и живой пересчёт КБЖУ меняют только часть шторки — фокус в поле
        // ввода при этом трогать нельзя. Новый шаг или форма приходят в #sheet-body
        // (или заменяют #form), и тогда фокус надо поставить заново.
        if (target.id === "sheet-body" || !this.sheet.contains(document.activeElement)) {
          this.focusInside();
        }
      };
      document.addEventListener("htmx:afterSwap", this.onAfterSwap);
    },

    destroy() {
      document.removeEventListener("htmx:afterSwap", this.onAfterSwap);
      this.setBackgroundInert(false);
    },

    get sheet() {
      return (this.host ?? document).querySelector(".sheet");
    },

    openSheet(event) {
      this.opener =
        event?.currentTarget ??
        (this.host?.contains(document.activeElement) ? document.activeElement : null);
      this.sheetOpen = true;
      this.dirty = false;
      this.setBackgroundInert(true);
      this.$nextTick(() => this.focusInside());
    },

    closeSheet(force = false) {
      const active = document.activeElement;
      if (!force && this.dirty && !confirm("Закрыть без сохранения?")) {
        // Отказ от закрытия не должен выбрасывать фокус из формы.
        if (active?.closest("form") && this.sheet?.contains(active)) active.focus();
        else this.focusInside();
        return;
      }
      this.sheetOpen = false;
      this.dirty = false;
      this.setBackgroundInert(false);
      const opener = this.opener;
      this.opener = null;
      this.$nextTick(() => {
        if (opener?.isConnected) opener.focus();
      });
    },

    focusInside() {
      const sheet = this.sheet;
      if (!sheet) return;
      const preferred = sheet.querySelector("[autofocus]");
      const body = sheet.querySelector("#sheet-body");
      const target =
        (preferred && this.isFocusable(preferred) ? preferred : null) ??
        this.focusable(body)[0] ??
        this.focusable(sheet)[0];
      target?.focus();
    },

    trapFocus(event) {
      if (!this.sheetOpen) return;
      const items = this.focusable(this.sheet);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (!this.sheet.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    },

    focusable(root) {
      if (!root) return [];
      return Array.from(root.querySelectorAll(SHEET_FOCUSABLE)).filter((element) =>
        this.isFocusable(element),
      );
    },

    isFocusable(element) {
      return !element.closest("[inert]") && element.getClientRects().length > 0;
    },

    setBackgroundInert(on) {
      this.inerted.forEach((element) => {
        element.inert = false;
        element.removeAttribute("inert");
      });
      this.inerted = [];
      if (!on) return;
      const nodes = [];
      for (let node = this.sheet; node && node.parentElement; node = node.parentElement) {
        for (const sibling of node.parentElement.children) {
          // Затемнение само закрывает шторку, поэтому его не выключаем.
          if (sibling !== node && !sibling.classList.contains("sheet-backdrop")) nodes.push(sibling);
        }
      }
      nodes.forEach((element) => {
        element.inert = true;
        element.setAttribute("inert", "");
      });
      this.inerted = nodes;
    },
  }));
});
