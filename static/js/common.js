/* Shared helpers for TradeSlip pages */
window.TradeSlip = window.TradeSlip || {};

TradeSlip.parseErrorDetail = function (payload) {
  const detail = payload && payload.detail;
  if (!detail) return "Request failed.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join(", ");
  }
  if (typeof detail === "object" && detail.message) return String(detail.message);
  return "Request failed.";
};

TradeSlip.apiFetch = async function (url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: "include",
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Your session has expired. Please sign in again.");
  }
  return response;
};

TradeSlip.todayIso = function () {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
};

TradeSlip.escapeHtml = function (value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
};

TradeSlip.logout = async function () {
  try {
    await fetch("/api/logout", { method: "POST", credentials: "include" });
  } catch (error) {
    console.error(error);
  }
  window.location.href = "/login";
};

/**
 * Put a submit button into a clear loading state (disabled, spinner, label).
 * Optionally shows a progress message after slowAfterMs if the request is still running.
 * Returns an end() function that restores the button.
 */
TradeSlip.beginButtonLoading = function (btn, options = {}) {
  if (!btn) return function () {};
  const {
    label = "Saving...",
    progressEl = null,
    slowMessage = "Still working… please wait.",
    slowAfterMs = 1500,
  } = options;

  if (btn.dataset.loading === "1") {
    return function () {};
  }

  const idleHtml = btn.innerHTML;
  btn.dataset.loading = "1";
  btn.dataset.idleHtml = idleHtml;
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.classList.add("opacity-70", "cursor-wait");
  btn.innerHTML =
    '<span class="inline-flex items-center justify-center gap-2">' +
    '<svg class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
    '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>' +
    "</svg>" +
    `<span>${label}</span>` +
    "</span>";

  let slowTimer = null;
  if (progressEl) {
    progressEl.textContent = "";
    progressEl.classList.add("hidden");
    slowTimer = setTimeout(function () {
      if (btn.dataset.loading !== "1") return;
      progressEl.textContent = slowMessage;
      progressEl.classList.remove("hidden");
    }, slowAfterMs);
  }

  return function endButtonLoading() {
    if (slowTimer) clearTimeout(slowTimer);
    if (progressEl) {
      progressEl.textContent = "";
      progressEl.classList.add("hidden");
    }
    if (btn.dataset.loading !== "1") return;
    btn.dataset.loading = "0";
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.classList.remove("opacity-70", "cursor-wait");
    btn.innerHTML = btn.dataset.idleHtml || idleHtml;
  };
};

TradeSlip.isButtonLoading = function (btn) {
  return !!(btn && btn.dataset.loading === "1");
};
