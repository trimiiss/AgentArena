(function () {
  var stored = null;
  try {
    stored = window.localStorage.getItem("aa-theme");
  } catch (error) {
    stored = null;
  }
  var system = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = stored === "dark" || stored === "light" ? stored : system;
})();
