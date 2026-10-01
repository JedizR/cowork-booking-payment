// Progressive enhancement only: every page works without this file and the server decides.
(function () {
  "use strict";

  // --- Hosted checkout: countdown (PMT-R07). Counts the server's data-seconds-left down with a
  // monotonic timer; the browser's own date is never read.
  var countdown = document.getElementById("countdown");
  var payButton = document.getElementById("pay");
  if (countdown) {
    var total = parseInt(countdown.getAttribute("data-seconds-left"), 10);
    var payBy = countdown.getAttribute("data-pay-by");
    var started = performance.now();
    var tick = function () {
      var left = total - Math.floor((performance.now() - started) / 1000);
      if (left <= 0) {
        countdown.textContent = "Time to pay has run out";
        countdown.classList.remove("is-urgent");
        countdown.classList.add("is-over");
        if (payButton) payButton.disabled = true;
        var over = document.getElementById("time-over");
        if (over) over.hidden = false;
        return;
      }
      countdown.textContent = "Pay by " + payBy + " (" +
        (left < 60 ? "less than 1 min left" : Math.ceil(left / 60) + " min left") + ")";
      countdown.classList.toggle("is-urgent", left <= 120);
      setTimeout(tick, 1000 - ((performance.now() - started) % 1000));
    };
    tick();
  }

  // --- Hosted checkout: card fields ----------------------------------------------------------
  var form = document.querySelector(".pay-form");
  if (form) {
    var number = form.querySelector("#card_number");
    var expiry = form.querySelector("#expiry");
    var cvc = form.querySelector("#cvc");
    var error = form.querySelector("#card-error");
    var field = error.parentNode;
    var digits = function (value) { return value.replace(/\D/g, ""); };

    // Card number in groups of 4, keeping the caret after the same digit.
    number.addEventListener("input", function () {
      var caret = digits(number.value.slice(0, number.selectionStart)).length;
      var d = digits(number.value).slice(0, 19);
      number.value = d.replace(/(\d{4})(?=\d)/g, "$1 ");
      var pos = caret + Math.floor(Math.max(caret - 1, 0) / 4);
      if (document.activeElement === number) number.setSelectionRange(pos, pos);
    });
    // Expiry MM/YY: the slash comes by itself; "4" becomes "04/".
    expiry.addEventListener("input", function (event) {
      var d = digits(expiry.value).slice(0, 4);
      if (d.length === 1 && d > "1") d = "0" + d;
      var deleting = event.inputType && event.inputType.indexOf("delete") === 0;
      expiry.value = d.length > 2 || (d.length === 2 && !deleting) ? d.slice(0, 2) + "/" + d.slice(2) : d;
    });
    cvc.addEventListener("input", function () { cvc.value = digits(cvc.value).slice(0, 4); });

    var showError = function (input, text) {
      [number, expiry, cvc].forEach(function (el) { el.removeAttribute("aria-invalid"); });
      field.classList.toggle("has-error", !!text);
      error.textContent = text || "";
      error.hidden = !text;
      if (input) { input.setAttribute("aria-invalid", "true"); input.focus(); }
    };
    [number, expiry, cvc].forEach(function (el) {
      el.addEventListener("input", function () { if (el.getAttribute("aria-invalid")) showError(null, ""); });
    });

    // Shape checks in the server's order (PMT-R08). Whether the month has passed is the
    // server's call: its clock may be the test clock.
    form.noValidate = true;
    form.addEventListener("submit", function (event) {
      var problem =
        !/^\d{13,19}$/.test(digits(number.value)) ? [number, "Card number must be 13 to 19 digits"] :
        !/^\d{3,4}$/.test(cvc.value) ? [cvc, "CVC must be 3 or 4 digits"] :
        !/^(0[1-9]|1[0-2])\/\d{2}$/.test(expiry.value) ? [expiry, "Expiry must be MM/YY"] : null;
      if (problem) {
        event.preventDefault();
        showError(problem[0], problem[1]);
        return;
      }
      payButton.disabled = true;
      payButton.textContent = "Processing...";
    });
    // Coming back through the history cache must not leave the button stuck.
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) window.location.reload();
    });

    // Test cards fill the form.
    document.querySelectorAll(".test-card").forEach(function (button) {
      button.addEventListener("click", function () {
        number.value = button.getAttribute("data-card").replace(/(\d{4})(?=\d)/g, "$1 ");
        if (!expiry.value) expiry.value = "12/30";
        if (!cvc.value) cvc.value = "123";
        showError(null, "");
        payButton.focus();
      });
    });
  }

  // --- Operator page: Sessions / Attempts / Refunds as tabs ------------------------------------
  var records = document.querySelector(".records");
  if (records) {
    var tabs = Array.prototype.slice.call(records.querySelectorAll('[role="tab"]'));
    var select = function (id, focus) {
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("aria-controls") === id;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        document.getElementById(tab.getAttribute("aria-controls")).hidden = !on;
        if (on && focus) tab.focus();
      });
      history.replaceState(null, "", "#" + id);
    };
    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { select(tab.getAttribute("aria-controls")); });
      tab.addEventListener("keydown", function (event) {
        var step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (step) select(tabs[(i + step + tabs.length) % tabs.length].getAttribute("aria-controls"), true);
      });
    });
    document.querySelectorAll("[data-tab-link]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        select(link.getAttribute("data-tab-link"));
        records.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    records.querySelector('[role="tablist"]').hidden = false;
    records.classList.add("has-tabs");
    var wanted = location.hash.slice(1);
    select(tabs.some(function (t) { return t.getAttribute("aria-controls") === wanted; }) ? wanted : "sessions");
  }
})();
