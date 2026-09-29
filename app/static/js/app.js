/* Este archivo es parte de "ISO9001 QMS" (GPL-3.0-or-later). */
(function () {
  "use strict";

  // Ask before submitting forms marked with data-confirm (e.g. deletions).
  // The message is read from the attribute, never evaluated as code.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute && form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });
})();
