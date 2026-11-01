(function () {
  const root = document.getElementById("push-settings");
  if (!root) return;

  const enableButton = document.getElementById("push-enable");
  const disableButton = document.getElementById("push-disable");
  const errorBox = document.getElementById("push-error");
  const subscribeForm = document.getElementById("push-subscribe-form");
  const unsubscribeForm = document.getElementById("push-unsubscribe-form");

  const showError = (text) => {
    errorBox.textContent = text;
    errorBox.hidden = false;
  };

  const base64ToBytes = (value) => {
    const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
    const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (char) => char.charCodeAt(0));
  };

  const registration = async () => {
    await navigator.serviceWorker.ready;
    return navigator.serviceWorker.getRegistration();
  };

  const syncButtons = async () => {
    if (
      !("serviceWorker" in navigator) ||
      !("PushManager" in window) ||
      !("Notification" in window)
    ) {
      enableButton.hidden = true;
      disableButton.hidden = true;
      showError("Этот браузер не поддерживает уведомления.");
      return;
    }
    const current = await registration();
    const subscription = current ? await current.pushManager.getSubscription() : null;
    enableButton.hidden = Boolean(subscription);
    disableButton.hidden = !subscription;
  };

  enableButton.addEventListener("click", async () => {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        showError("Разрешение на уведомления не выдано.");
        return;
      }
      const current = await registration();
      const subscription = await current.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64ToBytes(root.dataset.vapidPublicKey),
      });
      const keys = subscription.toJSON().keys || {};
      subscribeForm.elements.endpoint.value = subscription.endpoint;
      subscribeForm.elements.p256dh.value = keys.p256dh || "";
      subscribeForm.elements.auth.value = keys.auth || "";
      subscribeForm.submit();
    } catch (error) {
      showError("Не удалось включить уведомления. Проверьте разрешение в настройках браузера.");
    }
  });

  disableButton.addEventListener("click", async () => {
    try {
      const current = await registration();
      const subscription = current ? await current.pushManager.getSubscription() : null;
      unsubscribeForm.elements.endpoint.value = subscription ? subscription.endpoint : "";
      if (subscription) await subscription.unsubscribe();
      unsubscribeForm.submit();
    } catch (error) {
      showError("Не удалось выключить уведомления. Попробуйте ещё раз.");
    }
  });

  syncButtons().catch(() => {
    showError("Не удалось проверить состояние уведомлений.");
  });
})();
