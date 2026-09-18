// AubeDroniste — JS minimal cote client.

const ZONE_KEY = 'aube-zone';

function useMyLocation() {
  if (!navigator.geolocation) {
    alert('Geolocalisation indisponible dans ce navigateur.');
    return;
  }
  navigator.geolocation.getCurrentPosition(function (pos) {
    const lat = pos.coords.latitude.toFixed(5);
    const lng = pos.coords.longitude.toFixed(5);
    const f = document.querySelector('input[name="lat"]');
    const g = document.querySelector('input[name="lng"]');
    if (f) f.value = lat;
    if (g) g.value = lng;
    saveZone(lat, lng, null);
  }, function () {
    alert('Impossible d\'obtenir la position.');
  });
}

// "Près de moi" sur la landing : géolocalise puis redirige vers /pilotes
// avec les coordonnées et un rayon par défaut. Mémorise la zone.
function findNearMe(targetUrl) {
  const url = targetUrl || '/pilotes';
  if (!navigator.geolocation) {
    alert('Geolocalisation indisponible. Saisissez manuellement votre ville.');
    return;
  }
  navigator.geolocation.getCurrentPosition(function (pos) {
    const lat = pos.coords.latitude.toFixed(5);
    const lng = pos.coords.longitude.toFixed(5);
    saveZone(lat, lng, null);
    window.location.href = url + '?lat=' + lat + '&lng=' + lng + '&radius_km=100';
  }, function (err) {
    alert('Geolocalisation refusée ou indisponible. ' + (err.message || ''));
  });
}

function toggleTheme() {
  const html = document.documentElement;
  const cur = html.getAttribute('data-theme') || 'light';
  const next = cur === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  try { localStorage.setItem('aube-theme', next); } catch (e) {}
  syncThemeColor(next);
}

// Palette d'accent (Paramètres) : aperçu immédiat, mémorisé sur l'appareil ;
// le serveur l'enregistre sur le compte à la soumission du formulaire.
function pickAccent(name) {
  const html = document.documentElement;
  if (!name || name === 'aube') html.removeAttribute('data-accent');
  else html.setAttribute('data-accent', name);
  try { localStorage.setItem('aube-accent', name || 'aube'); } catch (e) {}
}

// Aligne la couleur de la barre système (Android/TWA, iOS PWA) sur le thème.
function syncThemeColor(theme) {
  const m = document.querySelector('meta[name="theme-color"]');
  if (m) m.setAttribute('content', theme === 'dark' ? '#0a0a0c' : '#f8f9fd');
}

// ----- Mémoire de zone (lat/lng + label optionnel) -----

function saveZone(lat, lng, label) {
  try {
    localStorage.setItem(ZONE_KEY, JSON.stringify({
      lat: parseFloat(lat),
      lng: parseFloat(lng),
      label: label || null,
      ts: Date.now(),
    }));
  } catch (e) {}
  refreshZonePill();
}

function loadZone() {
  try {
    const v = localStorage.getItem(ZONE_KEY);
    if (!v) return null;
    return JSON.parse(v);
  } catch (e) { return null; }
}

function clearZone() {
  try { localStorage.removeItem(ZONE_KEY); } catch (e) {}
  refreshZonePill();
}

function refreshZonePill() {
  const pill = document.getElementById('zone-pill');
  if (!pill) return;
  const zone = loadZone();
  if (!zone) {
    pill.classList.remove('active');
    return;
  }
  // Privacy : on n'affiche JAMAIS les coordonnees brutes dans l'UI
  // ("45.51°, -73.56°" donne la position du domicile a la lecture).
  // On utilise le label (ville) s'il est connu, sinon on garde le
  // libelle generique deja rendu cote serveur via i18n ("Ma zone"
  // / "My zone").
  const labelEl = pill.querySelector('.zone-label');
  if (labelEl && zone.label) {
    labelEl.textContent = zone.label;
  }
  pill.classList.add('active');
  pill.setAttribute('href',
    (document.documentElement.dataset.langPrefix || '') + '/pilotes?lat=' + zone.lat + '&lng=' + zone.lng + '&radius_km=100');
  pill.title = 'Filtrer autour de votre zone. Clic droit pour effacer.';
  pill.oncontextmenu = function (e) { e.preventDefault(); clearZone(); return false; };
}

// Auto-pré-remplit lat/lng des formulaires de recherche depuis la zone enregistrée.
function autofillFromZone() {
  const zone = loadZone();
  if (!zone) return;
  document.querySelectorAll('input[name="lat"]').forEach(function (el) {
    if (!el.value) el.value = zone.lat.toFixed(5);
  });
  document.querySelectorAll('input[name="lng"]').forEach(function (el) {
    if (!el.value) el.value = zone.lng.toFixed(5);
  });
}

// ----- Délégation d'évènements (remplace les gestionnaires inline onclick/
// onsubmit/onchange, incompatibles avec une CSP à nonce sans 'unsafe-inline').

// Confirmation avant action : data-confirm="message".
//  - sur un <form> : intercepte l'envoi.
//  - sur un <a>/<button type=button> : intercepte le clic.
document.addEventListener('submit', function (e) {
  var f = e.target;
  if (f && f.matches && f.matches('form[data-confirm]')) {
    if (!window.confirm(f.getAttribute('data-confirm'))) e.preventDefault();
  }
}, true);

document.addEventListener('click', function (e) {
  var t = e.target.closest ? e.target.closest('[data-confirm], [data-action]') : null;
  if (!t) return;

  // data-confirm sur un <a> ou un <button> (submit inclus) : on demande au
  // clic. preventDefault sur le clic d'un bouton submit stoppe l'envoi. Les
  // <form data-confirm> sont geres par l'ecouteur 'submit' ci-dessus (le clic
  // remonte jusqu'au form mais on l'ignore ici via tagName !== 'FORM').
  var msg = t.getAttribute('data-confirm');
  if (msg !== null && t.tagName !== 'FORM') {
    if (!window.confirm(msg)) { e.preventDefault(); return; }
  }

  var action = t.getAttribute('data-action');
  if (!action) return;
  switch (action) {
    case 'use-location': e.preventDefault(); useMyLocation(); break;
    case 'near-me': e.preventDefault(); findNearMe(t.getAttribute('data-url') || '/pilotes'); break;
    case 'toggle-theme': toggleTheme(); break;
    case 'pick-accent': pickAccent(t.value); break;
    case 'reload': e.preventDefault(); location.reload(); break;
    case 'open-hidden': {
      // Déplie un bloc masqué (hidden) et retire le bouton
      e.preventDefault();
      document.querySelectorAll(t.getAttribute('data-target')).forEach(function (el) { el.hidden = false; });
      t.remove();
      break;
    }
    case 'open-details': {
      var d = document.getElementById(t.getAttribute('data-target'));
      if (d) d.open = true;
      break;
    }
    case 'copy-link': {
      e.preventDefault();
      copyLink(t);
      break;
    }
    case 'native-share': {
      e.preventDefault();
      if (navigator.share) {
        navigator.share({ title: t.getAttribute('data-title') || document.title,
                          url: t.getAttribute('data-url') || location.href })
                 .catch(function () {});
      }
      break;
    }
    case 'show': {
      var el = document.getElementById(t.getAttribute('data-target'));
      if (el) el.style.display = 'block';
      break;
    }
  }
});

// Masque un élément si son chargement échoue : data-hide-on-error (ex. logo).
// 'error' ne bulle pas -> écoute en phase de capture.
document.addEventListener('error', function (e) {
  var t = e.target;
  if (t && t.matches && t.matches('[data-hide-on-error]')) t.style.display = 'none';
}, true);

document.addEventListener('DOMContentLoaded', function () {
  refreshZonePill();
  autofillFromZone();
});

window.useMyLocation = useMyLocation;
window.findNearMe = findNearMe;
window.toggleTheme = toggleTheme;
window.clearZone = clearZone;

/* ---- Partage ------------------------------------------------------------
   Copie du lien : l'API presse-papier n'existe qu'en contexte securise, on
   garde un repli sur l'ancienne methode pour le dev en http. Le libelle du
   bouton confirme sur place, plutot qu'une alerte. */
function copyLink(btn) {
  var url = btn.getAttribute('data-url') || location.href;
  var done = btn.getAttribute('data-done') || 'OK';
  var back = btn.textContent;
  function confirmCopy() {
    btn.textContent = done;
    setTimeout(function () { btn.textContent = back; }, 2000);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(url).then(confirmCopy, function () { fallbackCopy(url, confirmCopy); });
  } else {
    fallbackCopy(url, confirmCopy);
  }
}

function fallbackCopy(text, done) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (err) { window.prompt('', text); }
  document.body.removeChild(ta);
}

// Menu de partage du telephone : seulement la ou il existe.
if (navigator.share) {
  document.querySelectorAll('.js-native-share').forEach(function (b) { b.hidden = false; });
}

// Visionneuse plein écran : clic sur une photo du portfolio ou sur la photo
// de profil (rognée en rond sur la fiche) -> grand format entier, flèches ou
// boutons pour passer à la suivante, Échap ou clic hors image pour fermer.
// Les vidéos gardent leur lecteur en place.
function makeViewer(frames, caption) {
  if (!frames.length) return;
  var box = document.createElement('div');
  box.className = 'lightbox';
  box.hidden = true;
  box.innerHTML = '<button type="button" class="lb-close" aria-label="Fermer">×</button>' +
    '<button type="button" class="lb-prev" aria-label="Précédente">‹</button>' +
    '<figure><img alt=""><figcaption></figcaption></figure>' +
    '<button type="button" class="lb-next" aria-label="Suivante">›</button>';
  document.body.appendChild(box);
  var img = box.querySelector('img'), cap = box.querySelector('figcaption'), cur = -1;
  function show(i) {
    cur = (i + frames.length) % frames.length;
    var src = frames[cur];
    img.src = src.getAttribute('data-full') || src.src;
    cap.textContent = caption(src);
    box.hidden = false;
    document.body.style.overflow = 'hidden';
    box.querySelector('.lb-prev').hidden = box.querySelector('.lb-next').hidden = frames.length < 2;
  }
  function hide() { box.hidden = true; document.body.style.overflow = ''; }
  frames.forEach(function (el, i) {
    el.style.cursor = 'zoom-in';
    el.addEventListener('click', function (e) { e.preventDefault(); show(i); });
    el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(i); } });
  });
  box.querySelector('.lb-close').addEventListener('click', hide);
  box.querySelector('.lb-prev').addEventListener('click', function () { show(cur - 1); });
  box.querySelector('.lb-next').addEventListener('click', function () { show(cur + 1); });
  box.addEventListener('click', function (e) { if (e.target === box) hide(); });
  document.addEventListener('keydown', function (e) {
    if (box.hidden) return;
    if (e.key === 'Escape') hide();
    else if (e.key === 'ArrowLeft') show(cur - 1);
    else if (e.key === 'ArrowRight') show(cur + 1);
  });
}
makeViewer(Array.prototype.slice.call(document.querySelectorAll('.media-figure .media-frame img')), function (src) {
  var fig = src.closest('.media-figure');
  var title = fig && fig.querySelector('.media-title');
  var desc = fig && fig.querySelector('.media-body p');
  return [title && title.textContent, desc && desc.textContent].filter(Boolean).join(' · ');
});
makeViewer(Array.prototype.slice.call(document.querySelectorAll('img[data-zoom]')), function (src) {
  return src.getAttribute('alt') || '';
});


// Champ fichier qui envoie son formulaire dès qu'une image est choisie
// (photo d'un appareil : un clic, pas de bouton « Envoyer » en plus).
document.addEventListener('change', function (e) {
  var input = e.target;
  if (!input || input.getAttribute('data-action') !== 'submit-on-change' || !input.files || !input.files.length) return;
  var form = input.closest('form');
  if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
});
