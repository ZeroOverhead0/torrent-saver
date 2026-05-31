// Minimal progressive enhancement — no framework, no build step.
(function () {
  "use strict";

  // Confirm destructive actions (forms with data-confirm).
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute && form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
    }
  });

  // Auto-dismiss the flash banner.
  var flash = document.querySelector(".flash");
  if (flash) {
    setTimeout(function () {
      flash.style.opacity = "0";
      setTimeout(function () { flash.remove(); }, 400);
    }, 5000);
  }

  // Live-refresh the dashboard cards every 30s without a full reload.
  if (document.body.dataset.page === "dashboard") {
    setInterval(function () { window.location.reload(); }, 30000);
  }
})();
