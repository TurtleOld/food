// Сканер штрихкодов: полоса камеры, детектор и подстановка кода в поле. Всё прочее (поиск) — обычный htmx.
// Полифилл и wasm тяжёлые и нужны не каждому, поэтому грузятся лениво — при первом включении сканера.
let detectorPromise = null;

function loadDetector(assets) {
  detectorPromise ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = assets.polyfill;
    script.onload = () => {
      // Без этого wasm ищется на CDN, а не рядом с нашей статикой.
      BarcodeDetectionAPI.prepareZXingModule({
        overrides: { locateFile: (path, prefix) => (path.endsWith(".wasm") ? assets.wasm : prefix + path) },
        fireImmediately: true,
      });
      resolve(new BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a"] }));
    };
    script.onerror = () => {
      detectorPromise = null;
      reject(new Error("scanner assets failed to load"));
    };
    document.head.append(script);
  });
  return detectorPromise;
}

document.addEventListener("alpine:init", () => {
  Alpine.data("scanner", ({ auto = false } = {}) => {
    // Поток и таймер вне реактивного прокси Alpine.
    let stream = null;
    let timer = null;

    return {
      cam: "off", // off | asking | live | denied | nocam | insecure
      photoNote: "",
      typed: false,

      init() {
        if (auto) this.$nextTick(() => this.start());
      },

      get strip() {
        return this.$root.querySelector("[data-scanner]");
      },
      get field() {
        return this.$root.querySelector(this.strip.dataset.field);
      },
      get stripOpen() {
        return this.cam === "asking" || this.cam === "live";
      },

      // Только привязка кода: форму целиком не отправляем, шторка остаётся открытой.
      addCode() {
        const form = this.$root.closest("form");
        htmx.ajax("POST", form.getAttribute("hx-post"), {
          source: form,
          target: "#sheet-body",
          swap: "innerHTML",
          values: { add_barcode: "1" },
        });
      },

      toggle() {
        if (this.stripOpen || this.cam !== "off") this.stop();
        else this.start();
      },

      async start() {
        this.stop();
        this.photoNote = "";
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
          this.cam = "insecure";
          return;
        }
        this.cam = "asking";
        loadDetector(this.strip.dataset).catch(() => {});
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: "environment" } },
            audio: false,
          });
        } catch (error) {
          this.cam = ["NotAllowedError", "SecurityError"].includes(error.name) ? "denied" : "nocam";
          return;
        }
        if (this.cam !== "asking") {
          this.stop(); // закрыли, пока висел запрос доступа
          return;
        }
        const video = this.$refs.video;
        video.srcObject = stream;
        await video.play().catch(() => {});
        this.cam = "live";
        let detector;
        try {
          detector = await loadDetector(this.strip.dataset);
        } catch {
          this.stop();
          this.photoNote = "Не удалось загрузить сканер — введите цифры штрихкода";
          return;
        }
        const tick = async () => {
          if (this.cam !== "live") return;
          try {
            const [hit] = await detector.detect(video);
            if (hit) return this.accept(hit.rawValue);
          } catch {
            // кадр не разобрали — пробуем следующий
          }
          timer = setTimeout(tick, 150);
        };
        tick();
      },

      stop() {
        clearTimeout(timer);
        stream?.getTracks().forEach((track) => track.stop());
        stream = null;
        this.cam = "off";
      },

      async photo(event) {
        const file = event.target.files[0];
        event.target.value = "";
        if (!file) return;
        this.photoNote = "Распознаём фото…";
        try {
          const [hit] = await (await loadDetector(this.strip.dataset)).detect(file);
          if (hit) return this.accept(hit.rawValue);
        } catch {
          // не разобрали — сообщаем ниже
        }
        this.photoNote = "На фото не нашли штрихкод — снимите ближе или введите цифры";
      },

      accept(code) {
        navigator.vibrate?.(40);
        this.stop();
        this.photoNote = "";
        const field = this.field;
        field.value = code;
        if (field.name === "q") {
          // Код из Каталога ведёт сразу на шаг количества; иначе остаются строки исходов.
          const jump = () => {
            document.removeEventListener("htmx:afterSettle", jump);
            document.querySelector("#search-results [data-catalog]")?.click();
          };
          document.addEventListener("htmx:afterSettle", jump);
          field.dispatchEvent(new Event("search", { bubbles: true }));
        } else {
          this.typed = true;
          field.dispatchEvent(new Event("input", { bubbles: true }));
        }
      },
    };
  });
});
