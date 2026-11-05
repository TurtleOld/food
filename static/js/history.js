// Ранние ходы: у ленты своя прокрутка (#53), поэтому край ищем у неё, а не у
// окна. Порция истории приходит заменой всей ленты, и чтобы человек остался на
// том же сообщении, scrollTop сдвигается на высоту вставленного сверху текста.
// Сдвиг делается на afterSettle: общий скрипт ленты восстанавливает позицию на
// afterSwap и иначе затёр бы его.
//
// Опрос активного хода заменяет только порцию `#feed-tail`, поэтому подгруженная
// история не перерисовывается и не растёт вместе с ответом. Для этой замены
// скрипт обновляет состояние ленты (занятость и кнопку отправки) и, если человек
// был у нижнего края, доводит ленту до конца.
const HISTORY_EDGE_PX = 40;
const FOLLOW_GAP_PX = 48;

let earlierAnchor = null;
let tailState = null;

const feedElement = () => document.getElementById("feed");
const atFeedEnd = (feed) =>
  feed.scrollHeight - feed.scrollTop - feed.clientHeight < FOLLOW_GAP_PX;

function syncLiveState() {
  const feed = feedElement();
  const tail = document.getElementById("feed-tail");
  if (!feed || !tail) return;
  const busy = tail.getAttribute("aria-busy") === "true";
  feed.setAttribute("aria-busy", String(busy));
  const button = document.querySelector('#chat-composer button[type="submit"]');
  if (!button) return;
  button.disabled = busy;
  button.setAttribute("aria-disabled", String(busy));
}

function requestEarlier() {
  const feed = feedElement();
  if (!feed || earlierAnchor || feed.scrollTop > HISTORY_EDGE_PX) return;
  const more = feed.querySelector("[data-load-earlier]");
  if (more) more.click();
}

// scroll не всплывает, поэтому слушаем документ в фазе перехвата.
document.addEventListener(
  "scroll",
  (event) => {
    if (event.target === feedElement()) requestEarlier();
  },
  true,
);

document.addEventListener("htmx:beforeRequest", (event) => {
  const trigger = event.detail.elt;
  const feed = feedElement();
  if (!feed) return;
  if (trigger?.matches?.("[data-load-earlier]")) {
    earlierAnchor = {
      top: feed.scrollTop,
      height: feed.scrollHeight,
      focus: document.activeElement === trigger,
    };
  } else if (trigger?.id === "feed-tail") {
    tailState = { top: feed.scrollTop, follow: atFeedEnd(feed) };
  }
});

document.addEventListener("htmx:afterSettle", (event) => {
  const targetId = event.detail.target?.id;
  const feed = feedElement();
  if (!feed) return;
  if (targetId === "feed" && earlierAnchor) {
    const anchor = earlierAnchor;
    earlierAnchor = null;
    feed.scrollTop = anchor.top + (feed.scrollHeight - anchor.height);
    // Кнопку, с которой подгружали, заменил новый узел: без переноса фокуса
    // клавиатура начинает обход страницы заново.
    if (anchor.focus) feed.focus({ preventScroll: true });
  } else if (targetId === "feed-tail" && tailState) {
    const state = tailState;
    tailState = null;
    syncLiveState();
    feed.scrollTop = state.follow ? feed.scrollHeight : state.top;
  }
});

for (const event of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
  document.addEventListener(event, () => {
    earlierAnchor = null;
    tailState = null;
  });
}
