// Ранние ходы: у ленты своя прокрутка (#53), поэтому край ищем у неё, а не у
// окна. Порция приходит заменой всей ленты, и чтобы человек остался на том же
// сообщении, scrollTop сдвигается на высоту вставленного сверху текста. Сдвиг
// делается на afterSettle: общий скрипт ленты восстанавливает позицию на
// afterSwap и иначе затёр бы его.
const HISTORY_EDGE_PX = 40;

let loadingEarlier = false;
let earlierAnchor = null;
let earlierHadFocus = false;

const feedElement = () => document.getElementById("feed");

function requestEarlier() {
  const feed = feedElement();
  if (!feed || loadingEarlier || feed.scrollTop > HISTORY_EDGE_PX) return;
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
  if (!trigger?.matches?.("[data-load-earlier]")) return;
  const feed = feedElement();
  if (!feed) return;
  loadingEarlier = true;
  earlierHadFocus = document.activeElement === trigger;
  earlierAnchor = { top: feed.scrollTop, height: feed.scrollHeight };
});

document.addEventListener("htmx:afterSettle", () => {
  if (!loadingEarlier || !earlierAnchor) return;
  loadingEarlier = false;
  const feed = feedElement();
  if (feed) feed.scrollTop = earlierAnchor.top + (feed.scrollHeight - earlierAnchor.height);
  // Кнопку, с которой подгружали, заменил новый узел: без переноса фокуса
  // клавиатура начинает обход страницы заново.
  if (feed && earlierHadFocus) feed.focus({ preventScroll: true });
  earlierHadFocus = false;
  earlierAnchor = null;
});

for (const event of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
  document.addEventListener(event, () => {
    loadingEarlier = false;
    earlierAnchor = null;
  });
}
