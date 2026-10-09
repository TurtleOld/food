// Шторка: открыта/закрыта и «есть несохранённые правки» — эфемерное состояние страницы-хозяина.
document.addEventListener("alpine:init", () => {
  Alpine.data("sheetHost", () => ({
    sheetOpen: false,
    dirty: false,
    openSheet() {
      this.sheetOpen = true;
      this.dirty = false;
    },
    closeSheet(force = false) {
      if (!force && this.dirty && !confirm("Закрыть без сохранения?")) return;
      this.sheetOpen = false;
      this.dirty = false;
    },
  }));
});
