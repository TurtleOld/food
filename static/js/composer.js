// Композер Ассистента: Enter отправляет, Shift+Enter переносит строку, поле растёт
// до десяти строк. Слушатели висят на документе: htmx заменяет форму после каждого
// ответа, и переинициализация на новом элементе иначе терялась бы.
const COMPOSER_MAX_LINES = 10;
const COMPOSER_COMPOSITION_GUARD_MS = 50;

const isComposerField = (node) => node instanceof Element && node.matches(".chat-composer-input");

function growComposer(field) {
  const style = getComputedStyle(field);
  const line = parseFloat(style.lineHeight) || 20;
  const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  field.style.height = "auto";
  const borders = field.offsetHeight - field.clientHeight;
  const max = line * COMPOSER_MAX_LINES + padding + borders;
  field.style.height = `${Math.min(field.scrollHeight + borders, max)}px`;
  field.style.overflowY = field.scrollHeight + borders > max ? "auto" : "hidden";
}

document.addEventListener("input", (event) => {
  if (isComposerField(event.target)) growComposer(event.target);
});

document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target?.id !== "chat") return;
  document.querySelectorAll(".chat-composer-input").forEach(growComposer);
});

document.querySelectorAll(".chat-composer-input").forEach(growComposer);

// Safari завершает IME-композицию раньше keydown, поэтому одного isComposing мало:
// Enter, которым человек подтвердил выбор иероглифа, не должен отправлять сообщение.
let compositionEndedAt = 0;
document.addEventListener("compositionend", (event) => {
  if (isComposerField(event.target)) compositionEndedAt = Date.now();
});

document.addEventListener("keydown", (event) => {
  const field = event.target;
  if (!isComposerField(field) || event.key !== "Enter" || event.shiftKey) return;
  if (event.isComposing || event.keyCode === 229) return;
  if (Date.now() - compositionEndedAt < COMPOSER_COMPOSITION_GUARD_MS) return;
  // Пока ход выполняется, кнопка заблокирована: Enter не отправляет и не копит переносы.
  event.preventDefault();
  const form = field.form;
  const button = form?.querySelector("button[type='submit']");
  if (!form || !button || button.disabled) return;
  form.requestSubmit(button);
});
