/* Effets au survol des cartes pilote (2026-11-20) : radar, hologramme,
   trajectoire (le drone est dans card-drone.js). Chaque visiteur choisit le
   sien dans le menu « Effets » du pied de page, ou « Aucun » qui coupe aussi
   les drones du bandeau. Le choix reste dans son navigateur. */
(function () {
  var root = document.documentElement;

  // --- menu « Effets » du pied de page -----------------------------------
  function current() {
    return root.getAttribute("data-fx") === "off" ? "none" : (root.getAttribute("data-card-fx") || "drone");
  }
  function sync() {
    var m = current();
    document.querySelectorAll('[data-action="pick-fx"]').forEach(function (sel) { sel.value = m; });
    document.querySelectorAll('[data-action="pick-fx-btn"]').forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.m === m ? "true" : "false");
    });
  }
  function choose(m) {
    if (m === "none") root.setAttribute("data-fx", "off");
    else { root.removeAttribute("data-fx"); root.setAttribute("data-card-fx", m); }
    try { localStorage.setItem("aube-card-fx", m); localStorage.removeItem("aube-fx"); } catch (err) {}
    sync();
  }
  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest('[data-action="pick-fx"]');
    if (sel) choose(sel.value);
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest('[data-action="pick-fx-btn"]');
    if (b) choose(b.dataset.m);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sync); else sync();

  if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;

  function rel(card, el) {
    var c = card.getBoundingClientRect(), r = el.getBoundingClientRect();
    return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height };
  }
  function layer(card, cls) {
    var old = card.querySelector(".fxd"); if (old) old.remove();
    var l = document.createElement("span");
    l.className = "fxd " + cls; l.setAttribute("aria-hidden", "true");
    card.appendChild(l); return l;
  }

  // --- Radar : ondes + balayage depuis la photo --------------------------
  function radar(card) {
    var av = card.querySelector(".avatar"); if (!av) return;
    var a = rel(card, av), cx = a.x + a.w / 2, cy = a.y + a.h / 2;
    var l = layer(card, "fxd-radar");
    l.style.setProperty("--cx", cx + "px"); l.style.setProperty("--cy", cy + "px");
    l.innerHTML = '<i class="sweep"></i><i class="ring"></i><i class="ring r2"></i><i class="ring r3"></i>' +
      '<i class="blip" style="left:' + (cx + 120) + 'px;top:' + (cy - 30) + 'px"></i>' +
      '<i class="blip b2" style="left:' + (cx + 60) + 'px;top:' + (cy + 70) + 'px"></i>' +
      '<i class="lockon" style="left:' + (a.x - 6) + 'px;top:' + (a.y - 6) + 'px;width:' + (a.w + 12) + 'px;height:' + (a.h + 12) + 'px"></i>';
  }

  // --- Hologramme : carte inclinee en 3D + reflet ------------------------
  function holoMove(card, e) {
    var r = card.getBoundingClientRect();
    var px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    card.style.transform = "perspective(800px) rotateX(" + ((0.5 - py) * 12).toFixed(2) + "deg) rotateY(" + ((px - 0.5) * 14).toFixed(2) + "deg) translateY(-4px) scale(1.02)";
    var l = card.querySelector(".fxd-holo") || layer(card, "fxd-holo");
    l.style.setProperty("--px", (px * 100) + "%"); l.style.setProperty("--py", (py * 100) + "%");
    l.style.setProperty("--ang", (px * 180 + 90) + "deg");
  }

  // --- Trajectoire : du pin a la ville, en pointilles --------------------
  function trajet(card) {
    var pin = card.querySelector(".map-pin") || card.querySelector(".callsign");
    var city = card.querySelector(".loc-city") || card.querySelector(".loc");
    if (!pin || !city) return;
    var p = rel(card, pin), c = rel(card, city);
    var x1 = pin.classList.contains("map-pin") ? p.x + p.w / 2 : p.x + Math.min(p.w, 44), y1 = p.y + p.h / 2, x2 = c.x + Math.min(c.w, 70) / 2, y2 = c.y + c.h / 2;
    var mx = (x1 + x2) / 2 + 90, my = Math.min(y1, y2) - 10 + (y2 - y1) * 0.35;
    var d = "M" + x1 + " " + y1 + " Q" + mx + " " + my + " " + x2 + " " + y2;
    var l = layer(card, "fxd-trajet");
    var W = card.clientWidth, H = card.clientHeight;
    l.innerHTML = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '">' +
      '<path class="trail" d="' + d + '"/><path class="draw" d="' + d + '" pathLength="100"/>' +
      '<circle class="dot-a" cx="' + x1 + '" cy="' + y1 + '" r="4"/>' +
      '<circle class="dot-b" cx="' + x2 + '" cy="' + y2 + '" r="5"/><circle class="ping" cx="' + x2 + '" cy="' + y2 + '" r="5"/>' +
      '<g class="plane"><circle r="3.5"/><animateMotion dur="1.6s" fill="freeze" path="' + d + '" keyPoints="0;1" keyTimes="0;1" calcMode="spline" keySplines=".5 0 .2 1"/></g></svg>';
  }

  document.addEventListener("pointerover", function (e) {
    var card = e.target.closest && e.target.closest(".pilot-card");
    if (!card || card._fxdOn || root.getAttribute("data-fx") === "off") return;
    var m = root.getAttribute("data-card-fx");
    if (m === "drone") return;
    card._fxdOn = true;
    if (m === "radar") radar(card);
    if (m === "holo") { card.classList.add("holo-on"); holoMove(card, e); }
    if (m === "trajet") trajet(card);
  });
  document.addEventListener("pointermove", function (e) {
    var card = e.target.closest && e.target.closest(".pilot-card");
    if (card && card._fxdOn && root.getAttribute("data-card-fx") === "holo") holoMove(card, e);
  }, { passive: true });
  document.addEventListener("pointerout", function (e) {
    var card = e.target.closest && e.target.closest(".pilot-card");
    if (!card || !card._fxdOn || card.contains(e.relatedTarget)) return;
    card._fxdOn = false;
    card.classList.remove("holo-on");
    card.style.transform = "";
    var l = card.querySelector(".fxd"); if (l) { l.classList.add("out"); setTimeout(function () { l.remove(); }, 300); }
  });
})();
