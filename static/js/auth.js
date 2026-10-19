/* Shared behaviour for the public auth pages (sign in / register).
 *
 * Only one thing lives here: the show/hide button that sits on the right edge
 * of a password field. It is pure presentation — the password rules are the
 * server's business.
 */
(function () {
  "use strict";

  function toArray(nodeList) {
    return Array.prototype.slice.call(nodeList || []);
  }

  toArray(document.querySelectorAll("[data-toggle-password]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var input = document.getElementById(button.getAttribute("data-toggle-password"));
      if (!input) return;
      input.type = input.type === "text" ? "password" : "text";
      var visible = input.type === "text";
      var eye = button.querySelector('[data-icon="eye"]');
      var eyeOff = button.querySelector('[data-icon="eye-off"]');
      if (eye) eye.hidden = visible;
      if (eyeOff) eyeOff.hidden = !visible;
      button.setAttribute("aria-pressed", visible ? "true" : "false");
      button.setAttribute("aria-label", visible ? "Hide password" : "Show password");
    });
  });
})();
