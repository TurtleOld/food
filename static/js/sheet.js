// Шторка: открыта/закрыта, идёт загрузка шага, ошибка загрузки и «есть несохранённые правки» —
// эфемерное состояние страницы-хозяина.
document.addEventListener("alpine:init", () => {
  Alpine.data("sheetHost", () => ({
    sheetOpen: false,
    loading: false,
    error: false,
    retryable: false,
    dirty: false,
    _request: 0,
    _pending: null,
    _retry: null,

    init() {
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
    },

    openSheet() {
      this.sheetOpen = true;
      this.error = false;
      this.retryable = false;
      this.dirty = false;
    },

    closeSheet(force = false) {
      if (!force && this.dirty && !confirm("Закрыть без сохранения?")) return;
      this.sheetOpen = false;
      this.error = false;
      this.retryable = false;
      this.dirty = false;
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
