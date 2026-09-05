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
    '/pilotes?lat=' + zone.lat + '&lng=' + zone.lng + '&radius_km=100');
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
    case 'reload': e.preventDefault(); location.reload(); break;
    case 'open-details': {
      var d = document.getElementById(t.getAttribute('data-target'));
      if (d) d.open = true;
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
