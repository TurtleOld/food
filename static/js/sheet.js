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
    loading: false,
    error: false,
    retryable: false,
    dirty: false,
    confirming: false,
    opener: null,
    inerted: [],
    _request: 0,
    _pending: null,
    _retry: null,
    _confirmReturn: null,

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
      // Шторка грузит шаги запросами htmx в #sheet-body; состояние ожидания живёт здесь,
      // чтобы любая ссылка внутри страницы получала его без собственного кода.
      document.body.addEventListener("htmx:beforeRequest", (event) => this._startRequest(event));
      document.body.addEventListener("htmx:beforeSwap", (event) => this._dropStaleSwap(event));
      document.body.addEventListener("htmx:afterRequest", (event) => this._finishRequest(event));
      // Ошибку шторки человек читает в самой шторке; общий тост о ней молчит.
      // Событие помечаем на body: слушатель тоста стоит на window и сработает позже.
      const markSheetError = (event) => {
        if (event.detail.target?.id === "sheet-body") event.detail.sheetHandled = true;
      };
      document.body.addEventListener("htmx:response-error", markSheetError);
      document.body.addEventListener("htmx:send-error", markSheetError);
      // Escape закрывает шторку на любой странице, а открытое подтверждение
      // сначала отменяется: иначе один Esc закрыл бы форму мимо вопроса.
      this.onKeydown = (event) => {
        if (event.key !== "Escape" || !this.sheetOpen) return;
        event.preventDefault();
        if (this.confirming) this.cancelClose();
        else this.closeSheet();
      };
      document.addEventListener("keydown", this.onKeydown);
    },

    destroy() {
      document.removeEventListener("htmx:afterSwap", this.onAfterSwap);
      document.removeEventListener("keydown", this.onKeydown);
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
      this.error = false;
      this.retryable = false;
      this.dirty = false;
      this.confirming = false;
      this.setBackgroundInert(true);
      this.$nextTick(() => this.focusInside());
    },

    // Несохранённые правки подтверждаются в самой шторке: нативный confirm
    // выпадает из языка интерфейса и не даёт вернуть фокус в форму.
    askClose() {
      this._confirmReturn = this.sheet?.contains(document.activeElement)
        ? document.activeElement
        : null;
      this.confirming = true;
      this.$nextTick(() => this.$refs.confirmKeep?.focus());
    },

    cancelClose() {
      const back = this._confirmReturn;
      this._confirmReturn = null;
      this.confirming = false;
      this.$nextTick(() => {
        if (back?.isConnected) back.focus();
        else this.focusInside();
      });
    },

    closeSheet(force = false) {
      if (!force && this.dirty) {
        this.askClose();
        return;
      }
      this.sheetOpen = false;
      this.error = false;
      this.retryable = false;
      this.dirty = false;
      this.confirming = false;
      this._confirmReturn = null;
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

    retry() {
      const retry = this._retry;
      if (!retry) return;
      this.error = false;
      this.loading = true;
      // Повторяем ровно тот запрос, что упал: параметры уже включают hx-vals,
      // поля формы и нажатую кнопку, поэтому повтор не подменяет действие.
      window.htmx
        .ajax(retry.verb, retry.path, {
          target: "#sheet-body",
          swap: "innerHTML",
          values: retry.values,
          source: retry.elt,
        })
        .catch(() => {});
    },

    _focusWhenVisible(selector, attempt = 0) {
      // Шторка появляется и меняет тело в микрозадачах Alpine: фокус ставим
      // только когда элемент уже видим, иначе браузер молча игнорирует focus().
      if (!this.sheetOpen) return;
      const element = document.querySelector(selector);
      if (element && element.getClientRects().length > 0) {
        element.focus();
        return;
      }
      if (attempt < 20) {
        requestAnimationFrame(() => this._focusWhenVisible(selector, attempt + 1));
      }
    },

    _isSheetTarget(event) {
      return Boolean(event.detail.target) && event.detail.target.id === "sheet-body";
    },

    _isCurrent(event) {
      return Boolean(event.detail.xhr) && event.detail.xhr.__sheetRequest === this._request;
    },

    _startRequest(event) {
      if (!this._isSheetTarget(event)) return;
      // Новое действие отменяет предыдущее: иначе медленный старый ответ перерисует
      // шторку после того, как человек выбрал другое действие.
      this._request += 1;
      if (this._pending && this._pending !== event.detail.elt && window.htmx) {
        window.htmx.trigger(this._pending, "htmx:abort");
        // Отмена может синхронно запустить отложенный повторный запрос того же
        // элемента; токен выдаём заново, чтобы последним остался этот выбор.
        this._request += 1;
      }
      event.detail.xhr.__sheetRequest = this._request;
      this._pending = event.detail.elt;
      // Повторяем только загрузку формы (GET). Повтор POST после потерянного
      // ответа может создать второй Продукт, снова привязать код или иначе
      // повторить изменение, поэтому для неидемпотентных действий повтора нет.
      const config = event.detail.requestConfig;
      this._retry =
        config.verb === "get"
          ? { verb: config.verb, path: config.path, values: config.parameters, elt: event.detail.elt }
          : null;
      this.sheetOpen = true;
      this.loading = true;
      this.error = false;
      this.retryable = false;
    },

    _dropStaleSwap(event) {
      // Ответ устаревшего запроса не должен подменять последний выбранный шаг.
      if (!this._isSheetTarget(event) || this._isCurrent(event)) return;
      event.detail.shouldSwap = false;
      event.detail.isError = false;
    },

    _finishRequest(event) {
      // Токен, а не target: успешное сохранение Предложения отвечает HX-Retarget
      // в ленту, и target к этому моменту уже другой.
      if (!this._isCurrent(event)) return;
      const failed = Boolean(event.detail.failed || event.detail.error);
      this.loading = false;
      this.error = failed;
      this.retryable = failed && Boolean(this._retry);
      if (failed) {
        // Для POST результат изменения неизвестен: повтор не предлагаем,
        // фокусируем сообщение, чтобы его прочитали, а форма осталась с вводом.
        requestAnimationFrame(() =>
          this._focusWhenVisible(this.retryable ? "#sheet-error button" : "#sheet-error"),
        );
      } else {
        this.dirty = false;
        // Пока шёл запрос, тело было скрыто, и htmx не смог сфокусировать autofocus.
        requestAnimationFrame(() => this._focusWhenVisible("#sheet-body [autofocus]"));
      }
    },
  }));
});
