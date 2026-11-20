/* Survol d'une carte pilote (2026-11-19) : un mini drone (le meme rendu
   Blender que le bandeau d'accueil) entre dans la carte, suit la souris avec
   un peu d'inertie et eclaire de son faisceau l'endroit vise ; un viseur de
   camera se verrouille sur la photo du pilote. Decor seulement : souris
   uniquement (pas au toucher), lumiere seule si animations reduites.
   Choix de l'effet et arret : menu « Effets » du pied de page (card-fx.js). */
(function () {
  var root = document.documentElement;
  if (!window.matchMedia || !matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  var calme = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function equip(card) {
    if (card._drone) return card._drone;
    var fx = document.createElement("span");
    fx.className = "cd-fx";
    fx.setAttribute("aria-hidden", "true");
    fx.innerHTML = '<i class="cd-pool"></i><i class="cd-beam"></i><i class="cd-drone"></i>' +
      '<i class="cd-lock"><b></b><b></b><b></b><b></b></i>';
    card.appendChild(fx);
    var st = { fx: fx, beam: fx.querySelector(".cd-beam"), drone: fx.querySelector(".cd-drone"),
               lock: fx.querySelector(".cd-lock"), x: 0, y: 0, tx: 0, ty: 0, raf: 0, on: false };
    card._drone = st;
    return st;
  }

  function placeLock(card, st) {
    var av = card.querySelector(".avatar");
    if (!av) { st.lock.style.display = "none"; return; }
    var c = card.getBoundingClientRect(), a = av.getBoundingClientRect(), pad = 7;
    st.lock.style.cssText = "left:" + (a.left - c.left - pad) + "px;top:" + (a.top - c.top - pad) +
      "px;width:" + (a.width + pad * 2) + "px;height:" + (a.height + pad * 2) + "px";
  }

  function frame(card, st) {
    // le drone vole a 34 px au-dessus du point vise, rattrape la souris en douceur
    st.x += (st.tx - st.x) * 0.12;
    st.y += (st.ty - st.y) * 0.12;
    var w = card.clientWidth;
    var dx = Math.max(34, Math.min(w - 34, st.x));
    var dy = Math.max(30, Math.min(st.y - 70, 70));
    var tilt = Math.max(-14, Math.min(14, (st.tx - st.x) * 0.25));
    st.drone.style.transform = "translate(" + (dx - 42) + "px," + (dy - 20) + "px) rotate(" + tilt + "deg)";
    // faisceau : de la nacelle du drone au point vise
    var bx = dx + 8, by = dy + 13, ex = st.tx - bx, ey = Math.max(8, st.ty - by);
    var len = Math.sqrt(ex * ex + ey * ey), ang = Math.atan2(ex, ey) * 180 / Math.PI;
    st.beam.style.transform = "translate(" + bx + "px," + by + "px) rotate(" + (-ang) + "deg)";
    st.beam.style.height = len + "px";
    st.fx.style.setProperty("--mx", st.tx + "px");
    st.fx.style.setProperty("--my", st.ty + "px");
    if (st.on && (Math.abs(st.tx - st.x) > 0.3 || Math.abs(st.ty - st.y) > 0.3)) {
      st.raf = requestAnimationFrame(function () { frame(card, st); });
    } else st.raf = 0;
  }

  function point(card, st, e) {
    var r = card.getBoundingClientRect();
    st.tx = e.clientX - r.left;
    st.ty = e.clientY - r.top;
    if (calme) { st.x = st.tx; st.y = st.ty; }
    if (!st.raf) st.raf = requestAnimationFrame(function () { frame(card, st); });
  }

  document.addEventListener("pointerover", function (e) {
    var card = e.target.closest && e.target.closest(".pilot-card");
    if (!card || card._drone && card._drone.on || root.getAttribute("data-fx") === "off") return;
    if ((root.getAttribute("data-card-fx") || "drone") !== "drone") return;
    var st = equip(card);
    var r = card.getBoundingClientRect();
    // entree par le coin le plus proche du haut
    st.x = (e.clientX - r.left) < r.width / 2 ? -40 : r.width + 40;
    st.y = 0;
    st.on = true;
    placeLock(card, st);
    point(card, st, e);
    card.classList.add("cd-on");
  });
  document.addEventListener("pointermove", function (e) {
    var card = e.target.closest && e.target.closest(".pilot-card");
    if (card && card._drone && card._drone.on) point(card, card._drone, e);
  }, { passive: true });
  document.addEventListener("pointerout", function (e) {
    var card = e.target.closest && e.target.closest(".pilot-card");
    if (!card || !card._drone || card.contains(e.relatedTarget)) return;
    card._drone.on = false;
    card.classList.remove("cd-on");
  });
})();
