/* Public registration stepper — progressive enhancement for /register/.
 *
 * The server renders every step of the form inside a single <form> and
 * validates the whole thing on POST (accounts/forms.py + accounts/services.py).
 * This script only changes how the form is *presented*: it reveals one step
 * at a time, drives the progress indicator, and points the applicant at
 * obviously missing input before they move on.
 *
 * It never decides what is valid — that stays on the server. Without
 * JavaScript the page still renders every step, still posts every field in one
 * request, and the server still rejects anything invalid.
 */
(function () {
  "use strict";

  function toArray(nodeList) {
    return Array.prototype.slice.call(nodeList || []);
  }

  var form = document.getElementById("registration-stepper");
  if (!form) return;

  var panels = toArray(form.querySelectorAll("[data-step-panel]"));
  if (!panels.length) return;

  var navItems = toArray(form.querySelectorAll("[data-step-nav-item]"));
  var nav = form.querySelector("[data-step-nav]");
  var statusEl = form.querySelector("[data-step-status]");
  var progressFill = form.querySelector("[data-step-progress]");
  var strengthFill = form.querySelector("[data-strength-fill]");
  var strengthLabel = form.querySelector("[data-strength-label]");
  var passwordInput = form.querySelector("#id_password1");

  /* Pre-submission interview sheet preview. The server renders the same
   * document staff see from the bound (unsaved) form in
   * accounts.views.RegisterSheetPreviewView; this script only asks for it and
   * drops the response into an iframe. The button is a real submit with a
   * formaction, so without JavaScript the browser posts to the preview URL and
   * opens it in a new tab. */
  var previewBlock = form.querySelector("[data-sheet-preview-block]");
  var previewButton = form.querySelector("[data-sheet-preview-open]");
  var previewStatus = form.querySelector("[data-sheet-preview-status]");
  var previewFrameWrap = form.querySelector("[data-sheet-preview-frame]");
  var previewFrame = form.querySelector("[data-sheet-preview-iframe]");
  /* Set after an inline preview fails: the next click deliberately falls
   * through to the browser's own post-to-formaction behaviour. */
  var previewFallback = false;

  /* Which fields the SERVER considered required before the stepper started
   * moving things around. The stepper re-computes "required" per role; this
   * map is the source of truth for what the server insists on. */
  var serverRequired = new Map();
  toArray(form.querySelectorAll("input, select, textarea")).forEach(function (el) {
    serverRequired.set(el, el.hasAttribute("required"));
  });

  /* The active step is tracked by key, not by index: the number of steps
   * changes with the role, so an index would point at the wrong step when
   * the applicant switches between Donor and Requester. */
  var currentKey = panels[0].getAttribute("data-step-key");

  /* ------------------------------------------------------------------ *
   * Role + step resolution
   * ------------------------------------------------------------------ */

  function selectedRole() {
    var checked = form.querySelector('input[name="role"]:checked');
    return checked ? checked.value : "";
  }

  /* The female-donor block of the questionnaire is required only when the
   * applicant is female; sex is a select on the donor-details step. */
  function selectedSex() {
    var checked = form.querySelector('input[name="sex"]:checked');
    if (checked) return checked.value;
    var select = form.querySelector('select[name="sex"]');
    return select ? select.value : "";
  }

  /* Panels that belong to the selected role, in document order. A panel
   * without data-step-for belongs to every role. */
  function scopedPanels() {
    var role = selectedRole();
    return panels.filter(function (panel) {
      var scope = (panel.getAttribute("data-step-for") || "all").split(/\s+/);
      if (scope.indexOf("all") !== -1) return true;
      return role !== "" && scope.indexOf(role) !== -1;
    });
  }

  function panelByKey(key) {
    for (var i = 0; i < panels.length; i++) {
      if (panels[i].getAttribute("data-step-key") === key) return panels[i];
    }
    return null;
  }

  function navByKey(key) {
    for (var i = 0; i < navItems.length; i++) {
      if (navItems[i].getAttribute("data-step-key") === key) return navItems[i];
    }
    return null;
  }

  function documentIndex(panel) {
    return panels.indexOf(panel);
  }

  /* A field is required when the server said so, when its role is selected, or
   * when its sex scope ("FEMALE") matches the selected sex. */
  function mustHaveValue(el) {
    if (!el.name || el.type === "radio" || el.type === "hidden") return false;
    var sexScope = el.getAttribute("data-required-for-sex");
    if (sexScope) return sexScope === selectedSex();
    var scope = el.getAttribute("data-required-for");
    if (scope) return scope === selectedRole();
    return serverRequired.get(el) === true;
  }

  /* A radio group is satisfied when any member is checked. */
  function radioGroupInvalid(name) {
    var group = toArray(form.querySelectorAll('input[type="radio"][name="' + name + '"]'));
    if (!group.length) return false;
    var sexScope = group[0].getAttribute("data-required-for-sex");
    var scope = group[0].getAttribute("data-required-for");
    var needed = serverRequired.get(group[0]) === true ||
      (!!sexScope && sexScope === selectedSex()) ||
      (!!scope && scope === selectedRole());
    if (!needed) return false;
    for (var i = 0; i < group.length; i++) {
      if (group[i].checked) return false;
    }
    return true;
  }

  function syncRequirements() {
    var role = selectedRole();
    var sex = selectedSex();
    toArray(form.querySelectorAll("input, select, textarea")).forEach(function (el) {
      if (!el.name) return;
      var sexScope = el.getAttribute("data-required-for-sex");
      var scope = el.getAttribute("data-required-for");
      var required = sexScope ? sexScope === sex
        : scope ? scope === role : serverRequired.get(el) === true;
      if (required) {
        el.setAttribute("required", "required");
      } else {
        el.removeAttribute("required");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Rendering
   * ------------------------------------------------------------------ */

  function panelHasError(panel) {
    return !!panel.querySelector('[role="alert"]');
  }

  function render() {
    var scoped = scopedPanels();
    if (!scoped.length) return;

    var active = panelByKey(currentKey);
    if (!active || scoped.indexOf(active) === -1) {
      /* The step the applicant was on does not exist for the selected role
       * (Donor -> Requester on the donor step). Move to the next step that
       * does exist, otherwise to the last one. */
      var from = active ? documentIndex(active) : -1;
      active = null;
      for (var i = 0; i < scoped.length; i++) {
        if (documentIndex(scoped[i]) > from) { active = scoped[i]; break; }
      }
      if (!active) active = scoped[scoped.length - 1];
      currentKey = active.getAttribute("data-step-key");
    }
    var currentIndex = scoped.indexOf(active);

    panels.forEach(function (panel) {
      var key = panel.getAttribute("data-step-key");
      var item = navByKey(key);
      var inScope = scoped.indexOf(panel) !== -1;
      var isActive = panel === active;
      var position = scoped.indexOf(panel);

      /* The panels are all rendered (so the form still works without
       * JavaScript); the stepper switches between them with .is-active. */
      panel.classList.toggle("is-active", isActive);

      if (item) {
        item.hidden = !inScope;
        item.classList.toggle("is-active", isActive);
        item.classList.toggle("is-done", inScope && position < currentIndex);
        item.classList.toggle("has-error", inScope && panelHasError(panel));
        var num = item.querySelector(".bb-step-num");
        if (num && inScope) num.textContent = String(position + 1);
        if (isActive) {
          item.setAttribute("aria-current", "step");
        } else {
          item.removeAttribute("aria-current");
        }
      }
    });

    /* The step rail and the per-step buttons only make sense with JS. */
    if (nav) nav.hidden = false;
    toArray(form.querySelectorAll("[data-step-footer]")).forEach(function (footer) {
      var panel = footer.closest("[data-step-panel]");
      footer.hidden = !(panel && scoped.indexOf(panel) !== -1);
    });
    toArray(form.querySelectorAll("[data-step-back]")).forEach(function (button) {
      button.hidden = false;
    });

    if (statusEl) {
      /* Before a role is chosen the number of steps is not known yet, so the
       * counter does not promise one. */
      statusEl.textContent = selectedRole()
        ? "Step " + (currentIndex + 1) + " of " + scoped.length
        : "Step 1 · Account type";
    }
    if (progressFill) {
      var pct = scoped.length > 1 ? ((currentIndex + 1) / scoped.length) * 100 : 100;
      progressFill.style.width = pct.toFixed(1) + "%";
    }
    syncRequirements();
    syncReviewScope();
    syncPreviewScope();
    refreshReview();
  }

  function goTo(index, focus) {
    var scoped = scopedPanels();
    if (!scoped.length) return;
    var target = scoped[Math.max(0, Math.min(index, scoped.length - 1))];
    currentKey = target.getAttribute("data-step-key");
    render();
    if (focus !== false) {
      var heading = target.querySelector("[data-step-heading]");
      if (heading && heading.focus) {
        try { heading.focus({ preventScroll: true }); } catch (err) { heading.focus(); }
      }
    }
  }

  function goToKey(key) {
    var scoped = scopedPanels();
    for (var i = 0; i < scoped.length; i++) {
      if (scoped[i].getAttribute("data-step-key") === key) {
        goTo(i);
        return;
      }
    }
  }

  /* ------------------------------------------------------------------ *
   * Validation (mirrors the server; it never replaces it)
   * ------------------------------------------------------------------ */

  function firstInvalidField(panel) {
    var fields = toArray(panel.querySelectorAll("input, select, textarea"));
    for (var i = 0; i < fields.length; i++) {
      var el = fields[i];
      if (!el.name || el.disabled || el.type === "hidden") continue;
      if (el.type === "radio") {
        if (radioGroupInvalid(el.name)) return el;
        continue;
      }
      if (el.type === "checkbox") {
        if (mustHaveValue(el) && !el.checked) return el;
        continue;
      }
      if (mustHaveValue(el) && !String(el.value).trim()) return el;
      if (!el.checkValidity()) return el;
    }
    return null;
  }

  function validatePanel(panel, report) {
    var bad = firstInvalidField(panel);
    if (bad && report) {
      if (bad.reportValidity) bad.reportValidity();
      if (bad.focus) {
        try { bad.focus({ preventScroll: false }); } catch (err) { bad.focus(); }
      }
    }
    return !bad;
  }

  /* ------------------------------------------------------------------ *
   * Review summary
   * ------------------------------------------------------------------ */

  function displayValue(name) {
    var inputs = toArray(form.querySelectorAll('[name="' + name + '"]'));
    if (!inputs.length) return "";
    var first = inputs[0];
    if (first.type === "radio") {
      var checked = form.querySelector('[name="' + name + '"]:checked');
      if (!checked) return "";
      return checked.getAttribute("data-label") || checked.value;
    }
    if (first.tagName === "SELECT") {
      var option = first.options[first.selectedIndex];
      return option ? String(option.text).trim() : "";
    }
    return String(first.value || "").trim();
  }

  function refreshReview() {
    toArray(form.querySelectorAll("[data-review-field]")).forEach(function (cell) {
      var value = displayValue(cell.getAttribute("data-review-field"));
      cell.textContent = value === "" ? "—" : value;
    });
  }

  function syncReviewScope() {
    var role = selectedRole();
    toArray(form.querySelectorAll("[data-review-row]")).forEach(function (row) {
      var scope = (row.getAttribute("data-review-row") || "all").split(/\s+/);
      row.hidden = scope.indexOf("all") === -1 && scope.indexOf(role) === -1;
    });
  }

  /* ------------------------------------------------------------------ *
   * Interview sheet preview
   * ------------------------------------------------------------------ */

  function setPreviewStatus(message, tone) {
    if (!previewStatus) return;
    previewStatus.textContent = message;
    previewStatus.hidden = !message;
    previewStatus.classList.toggle("is-error", tone === "error");
    previewStatus.classList.toggle("is-stale", tone === "stale");
  }

  function clearSheetPreview() {
    if (previewFrameWrap) {
      previewFrameWrap.hidden = true;
      previewFrameWrap.classList.remove("is-stale");
    }
    if (previewFrame) previewFrame.removeAttribute("srcdoc");
    setPreviewStatus("", "");
    previewFallback = false;
  }

  /* A requester has no interview sheet, so the block only exists for the
   * donor role (the server refuses the preview for everyone else too). */
  function syncPreviewScope() {
    if (!previewBlock) return;
    var role = selectedRole();
    var scope = (previewBlock.getAttribute("data-preview-for") || "all").split(/\s+/);
    var visible = scope.indexOf("all") !== -1 || (role !== "" && scope.indexOf(role) !== -1);
    previewBlock.hidden = !visible;
    if (!visible) clearSheetPreview();
  }

  function markPreviewStale() {
    if (!previewFrameWrap || previewFrameWrap.hidden) return;
    previewFrameWrap.classList.add("is-stale");
    setPreviewStatus("Your details changed - preview again to see the updated sheet.", "stale");
  }

  function validateEveryPanel() {
    var scoped = scopedPanels();
    for (var i = 0; i < scoped.length; i++) {
      if (!validatePanel(scoped[i], false)) {
        goTo(i);
        validatePanel(scoped[i], true);
        return false;
      }
    }
    return true;
  }

  function requestSheetPreview() {
    if (!previewButton || !previewFrame || !previewFrameWrap) return;
    if (!validateEveryPanel()) {
      setPreviewStatus("Finish every step first - the preview shows exactly what you entered.", "error");
      return;
    }
    var url = previewButton.getAttribute("formaction");
    if (!url || !window.fetch || !window.FormData) return;
    previewButton.disabled = true;
    previewFrameWrap.hidden = false;
    previewFrameWrap.classList.remove("is-stale");
    setPreviewStatus("Building your interview sheet...", "");
    fetch(url, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      var kind = response.headers.get("X-Register-Preview");
      return response.text().then(function (html) {
        return { html: html, kind: kind };
      });
    }).then(function (result) {
      /* srcdoc paints synchronously enough that the frame's load event is not
       * a reliable place to re-enable the button (some browsers fire it before
       * a listener added in this turn), so settle the UI here. */
      previewButton.disabled = false;
      if (result.kind === "unavailable") {
        /* The document was refused (a detail still needs attention): the
         * page in the frame lists what to fix. */
        setPreviewStatus("Some details still need attention - the list in the preview says what to fix.", "error");
      } else {
        setPreviewStatus("Preview only - nothing has been submitted yet.", "");
      }
      previewFrame.srcdoc = result.html;
      previewFrame.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }).catch(function () {
      previewButton.disabled = false;
      previewFrameWrap.hidden = true;
      previewFallback = true;
      setPreviewStatus("This browser could not show the preview inline. Click again to open it in a new tab.", "error");
    });
  }

  /* ------------------------------------------------------------------ *
   * Password helpers
   * ------------------------------------------------------------------ */

  var STRENGTH_LABELS = ["Too short", "Weak", "Fair", "Good", "Strong"];
  var STRENGTH_COLORS = ["#f43f5e", "#f97316", "#eab308", "#22c55e", "#22c55e"];

  function passwordScore(value) {
    if (!value) return 0;
    if (value.length < 8) return 0;
    var score = 1;
    if (value.length >= 12) score += 1;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
    if (/\d/.test(value)) score += 1;
    if (/[^A-Za-z0-9]/.test(value)) score += 1;
    return Math.min(score, 4);
  }

  function updateStrength() {
    if (!strengthFill || !passwordInput) return;
    var score = passwordScore(passwordInput.value);
    strengthFill.style.width = (score / 4) * 100 + "%";
    strengthFill.style.backgroundColor = STRENGTH_COLORS[score];
    if (strengthLabel) strengthLabel.textContent = STRENGTH_LABELS[score];
  }

  /* ------------------------------------------------------------------ *
   * Wiring
   * ------------------------------------------------------------------ */

  form.addEventListener("change", function (event) {
    if (event.target && (event.target.name === "role" || event.target.name === "sex")) {
      syncRequirements();
      render();
    }
  });

  form.addEventListener("input", function () {
    refreshReview();
    updateStrength();
    markPreviewStale();
  });

  toArray(form.querySelectorAll("[data-step-next]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var scoped = scopedPanels();
      var panel = scoped[scoped.indexOf(panelByKey(currentKey))];
      if (!panel) return;
      if (!validatePanel(panel, true)) return;
      goTo(scoped.indexOf(panel) + 1);
    });
  });

  toArray(form.querySelectorAll("[data-step-back]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var scoped = scopedPanels();
      goTo(scoped.indexOf(panelByKey(currentKey)) - 1);
    });
  });

  toArray(form.querySelectorAll("[data-step-goto]")).forEach(function (button) {
    button.addEventListener("click", function () {
      goToKey(button.getAttribute("data-step-goto"));
    });
  });

  if (previewButton) {
    previewButton.addEventListener("click", function (event) {
      /* Without fetch (or after a failed inline attempt) the button keeps its
       * native submit: the browser posts to formaction and opens a new tab. */
      if (previewFallback || !window.fetch || !window.FormData) return;
      event.preventDefault();
      requestSheetPreview();
    });
  }

  /* Enter moves to the next step instead of posting a half-filled form. */
  form.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.shiftKey || event.altKey) return;
    var target = event.target;
    if (!target || target.tagName === "TEXTAREA" || target.tagName === "BUTTON") return;
    /* Enter is the native "open/confirm" key on a <select>. Do not swallow it
     * and jump to the next step: the dropdown must stay keyboard-operable. */
    if (target.tagName === "SELECT") return;
    if (target.type === "checkbox") return;
    var scoped = scopedPanels();
    if (scoped.indexOf(panelByKey(currentKey)) >= scoped.length - 1) return;
    event.preventDefault();
    var panel = scoped[scoped.indexOf(panelByKey(currentKey))];
    if (validatePanel(panel, true)) goTo(scoped.indexOf(panel) + 1);
  });

  /* Last gate before the POST: every step is checked, and the applicant is
   * taken to the first step that still needs something. */
  form.addEventListener("submit", function (event) {
    var scoped = scopedPanels();
    for (var i = 0; i < scoped.length; i++) {
      if (!validatePanel(scoped[i], false)) {
        event.preventDefault();
        goTo(i);
        validatePanel(scoped[i], true);
        return;
      }
    }
  });

  /* The show/hide button on the password fields lives in static/js/auth.js
   * (it is shared with the sign-in page). */

  /* ---- boot ---- */
  syncRequirements();
  render();
  updateStrength();

  /* A re-rendered form (server-side validation errors) opens on the first
   * step that has a problem. */
  var scopedNow = scopedPanels();
  for (var i = 0; i < scopedNow.length; i++) {
    if (panelHasError(scopedNow[i])) {
      goToKey(scopedNow[i].getAttribute("data-step-key"));
      break;
    }
  }
})();
