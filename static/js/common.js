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
