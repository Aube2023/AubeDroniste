/* Fil des pilotes (/fil) : j'aime sans recharger, heures relatives dans la
   langue de la page, apercu de la photo/video avant publication, copie du
   lien. Tout marche aussi sans JavaScript (formulaires classiques). */
(function () {
  var lang = document.documentElement.lang || "fr";

  // --- « il y a 5 min » dans la langue de la page -------------------------
  function relTimes() {
    if (!window.Intl || !Intl.RelativeTimeFormat) return;
    var rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    var now = Date.now();
    document.querySelectorAll(".fpost time, .fcom time").forEach(function (el) {
      var d = new Date(el.getAttribute("datetime"));
      if (isNaN(d)) return;
      var s = Math.round((d - now) / 1000), a = Math.abs(s), txt;
      if (a < 45) txt = rtf.format(0, "second");
      else if (a < 3600) txt = rtf.format(Math.round(s / 60), "minute");
      else if (a < 86400) txt = rtf.format(Math.round(s / 3600), "hour");
      else if (a < 86400 * 7) txt = rtf.format(Math.round(s / 86400), "day");
      else txt = d.toLocaleDateString(lang, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
      el.textContent = txt;
      el.title = d.toLocaleString(lang);
    });
  }
  relTimes();
  setInterval(relTimes, 60000);

  // --- J'aime sans recharger la page ---------------------------------------
  document.addEventListener("submit", function (e) {
    var form = e.target.closest && e.target.closest(".js-like-form");
    if (!form || !window.fetch) return;
    e.preventDefault();
    var btn = form.querySelector("button");
    btn.disabled = true;
    fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin",
                         headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { if (!r.ok) throw r; return r.json(); })
      .then(function (data) {
        btn.classList.toggle("on", data.liked);
        btn.setAttribute("aria-pressed", data.liked ? "true" : "false");
        if (data.liked) { btn.classList.remove("pop"); void btn.offsetWidth; btn.classList.add("pop"); }
        var post = form.closest(".fpost"), span = post && post.querySelector(".js-likes");
        if (span) {
          span.hidden = !data.likes;
          span.textContent = "👍 " + span.dataset.tpl.replace("__N__", data.likes);
        }
      })
      .catch(function () { form.submit(); })
      .finally(function () { btn.disabled = false; });
  });

  // --- Bouton « Commenter » : place le curseur dans le champ ----------------
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest('[data-action="feed-comment"]');
    if (b) {
      var input = document.getElementById(b.dataset.target);
      if (input) { input.scrollIntoView({ block: "center", behavior: "smooth" }); input.focus(); }
      return;
    }
    var s = e.target.closest && e.target.closest('[data-action="feed-share"]');
    if (s) {
      var done = function () {
        var i18n = document.getElementById("feed-i18n");
        s.textContent = "✓ " + (i18n ? i18n.dataset.copied : "OK");
      };
      if (navigator.share && matchMedia("(pointer: coarse)").matches) {
        navigator.share({ url: s.dataset.url }).catch(function () {});
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(s.dataset.url).then(done, function () {});
      }
    }
  });

  // --- Apercu de la photo ou de la video avant de publier -----------------
  var form = document.querySelector(".fcompose");
  if (form) {
    var input = form.querySelector('input[type="file"]'), prev = form.querySelector(".fcompose-preview");
    var url = null;
    input.addEventListener("change", function () {
      if (url) URL.revokeObjectURL(url);
      prev.innerHTML = "";
      var f = input.files && input.files[0];
      if (!f) { prev.hidden = true; return; }
      url = URL.createObjectURL(f);
      var el;
      if (/^video\//.test(f.type)) { el = document.createElement("video"); el.controls = true; el.muted = true; }
      else if (/^image\/(jpeg|png|webp)/.test(f.type)) { el = document.createElement("img"); el.alt = ""; }
      else { el = document.createElement("span"); el.textContent = f.name; }
      if (el.tagName !== "SPAN") el.src = url;
      var x = document.createElement("button");
      x.type = "button"; x.className = "fcompose-x"; x.textContent = "✕"; x.setAttribute("aria-label", "✕");
      x.addEventListener("click", function () { input.value = ""; prev.innerHTML = ""; prev.hidden = true; });
      prev.appendChild(el); prev.appendChild(x); prev.hidden = false;
    });
    form.addEventListener("submit", function () {
      var b = form.querySelector('button[type="submit"]');
      b.disabled = true; b.classList.add("busy");
    });
  }
})();
