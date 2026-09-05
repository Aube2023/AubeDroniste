// Widget téléphone Aube : sélecteur d'indicatif pays + numéro auto-espacé.
// S'applique à tout conteneur `.js-phone` contenant un `input[name="phone"]`
// caché ; construit le sélecteur + le champ numéro, et tient le champ caché à
// jour (« +33 06 12 34 56 78 »). Réutilisable : inscription, profil, réglages.
(function () {
  // [ indicatif, drapeau, nom FR ] — pays du réseau d'abord, puis le monde.
  var INDICATIFS = [
    ['+1','🇨🇦','Canada'], ['+33','🇫🇷','France'], ['+32','🇧🇪','Belgique'],
    ['+41','🇨🇭','Suisse'], ['+352','🇱🇺','Luxembourg'], ['+212','🇲🇦','Maroc'],
    ['+213','🇩🇿','Algérie'], ['+216','🇹🇳','Tunisie'], ['+1','🇺🇸','États-Unis'],
    ['+221','🇸🇳','Sénégal'], ['+225','🇨🇮','Côte d’Ivoire'], ['+237','🇨🇲','Cameroun'],
    ['+241','🇬🇦','Gabon'], ['+243','🇨🇩','Congo (RDC)'], ['+44','🇬🇧','Royaume-Uni'],
    ['+353','🇮🇪','Irlande'], ['+34','🇪🇸','Espagne'], ['+351','🇵🇹','Portugal'],
    ['+39','🇮🇹','Italie'], ['+49','🇩🇪','Allemagne'], ['+31','🇳🇱','Pays-Bas'],
    ['+43','🇦🇹','Autriche'], ['+45','🇩🇰','Danemark'], ['+46','🇸🇪','Suède'],
    ['+47','🇳🇴','Norvège'], ['+358','🇫🇮','Finlande'], ['+48','🇵🇱','Pologne'],
    ['+30','🇬🇷','Grèce'], ['+420','🇨🇿','Tchéquie'], ['+36','🇭🇺','Hongrie'],
    ['+40','🇷🇴','Roumanie'], ['+380','🇺🇦','Ukraine'], ['+7','🇷🇺','Russie'],
    ['+90','🇹🇷','Turquie'], ['+972','🇮🇱','Israël'], ['+971','🇦🇪','Émirats arabes unis'],
    ['+966','🇸🇦','Arabie saoudite'], ['+974','🇶🇦','Qatar'], ['+965','🇰🇼','Koweït'],
    ['+961','🇱🇧','Liban'], ['+20','🇪🇬','Égypte'], ['+27','🇿🇦','Afrique du Sud'],
    ['+234','🇳🇬','Nigéria'], ['+254','🇰🇪','Kenya'], ['+91','🇮🇳','Inde'],
    ['+92','🇵🇰','Pakistan'], ['+86','🇨🇳','Chine'], ['+81','🇯🇵','Japon'],
    ['+82','🇰🇷','Corée du Sud'], ['+84','🇻🇳','Vietnam'], ['+66','🇹🇭','Thaïlande'],
    ['+62','🇮🇩','Indonésie'], ['+63','🇵🇭','Philippines'], ['+60','🇲🇾','Malaisie'],
    ['+65','🇸🇬','Singapour'], ['+61','🇦🇺','Australie'], ['+64','🇳🇿','Nouvelle-Zélande'],
    ['+52','🇲🇽','Mexique'], ['+55','🇧🇷','Brésil'], ['+54','🇦🇷','Argentine'],
    ['+56','🇨🇱','Chili'], ['+57','🇨🇴','Colombie'], ['+51','🇵🇪','Pérou']
  ];
  // Regroupement des chiffres par pays (le reste : paires de 2, norme
  // Europe / Maghreb). Ex. Canada 3-3-4, France 2-2-2-2-2.
  var GROUPES = { '+1': [3, 3, 4], '+44': [4, 3, 3], '+61': [4, 3, 3], '+91': [5, 5] };

  function formater(code, valeur) {
    var chiffres = (valeur || '').replace(/\D/g, '').slice(0, 14);
    var motif = GROUPES[code] || null;
    var out = [], i = 0, g = 0;
    while (i < chiffres.length) {
      var taille = motif ? (motif[g] || motif[motif.length - 1] || 2) : 2;
      out.push(chiffres.substr(i, taille));
      i += taille; g++;
    }
    return out.join(' ');
  }

  // Sépare « +33 06 12 34 56 78 » -> { code:'+33', national:'0612345678' }.
  // Numéro sans indicatif reconnu -> { code:null, national:<chiffres> }.
  function analyser(valeur) {
    valeur = (valeur || '').trim();
    if (!valeur) return { code: null, national: '' };
    var best = null;
    for (var i = 0; i < INDICATIFS.length; i++) {
      var c = INDICATIFS[i][0];
      if (valeur.indexOf(c) === 0 && (!best || c.length > best.length)) best = c;
    }
    if (best) return { code: best, national: valeur.slice(best.length).replace(/\D/g, '') };
    return { code: null, national: valeur.replace(/\D/g, '') };
  }

  function init(wrap) {
    if (!wrap || wrap.__phoneInit) return;
    var hidden = wrap.querySelector('input[name="phone"]');
    if (!hidden) return;
    wrap.__phoneInit = true;

    var selCode = document.createElement('select');
    selCode.className = 'phone-code';
    selCode.setAttribute('aria-label', 'Indicatif du pays');
    var numero = document.createElement('input');
    numero.type = 'tel'; numero.className = 'phone-number';
    numero.setAttribute('inputmode', 'tel');
    numero.setAttribute('autocomplete', 'tel-national');

    var parPays = {};
    INDICATIFS.forEach(function (e, i) {
      var opt = document.createElement('option');
      opt.value = e[0];
      opt.textContent = e[1] + ' ' + e[0] + ' · ' + e[2];
      selCode.appendChild(opt);
      if (parPays[e[2].toLowerCase()] == null) parPays[e[2].toLowerCase()] = i;
    });

    wrap.insertBefore(selCode, hidden);
    wrap.insertBefore(numero, hidden);

    // Valeur existante (formulaire d'édition) : on préremplit sans écraser.
    var initial = (hidden.value || '').trim();
    var parsed = analyser(initial);
    var idx = 0;
    if (parsed.code) {
      for (var j = 0; j < selCode.options.length; j++) {
        if (selCode.options[j].value === parsed.code) { idx = j; break; }
      }
    }
    selCode.selectedIndex = idx;                    // Canada (0) par défaut
    numero.value = formater(selCode.value, parsed.national);

    function combiner() {
      var n = numero.value.trim();
      hidden.value = n ? (selCode.value + ' ' + n) : '';
    }
    function majPlaceholder() { numero.placeholder = formater(selCode.value, '5551234567'); }
    majPlaceholder();

    // Reformate pendant la frappe en gardant le curseur au bon endroit.
    function reformater() {
      var avant = numero.value;
      var pos = numero.selectionStart == null ? avant.length : numero.selectionStart;
      var chiffresAvant = avant.slice(0, pos).replace(/\D/g, '').length;
      var apres = formater(selCode.value, avant);
      numero.value = apres;
      var p = 0, vus = 0;
      while (p < apres.length && vus < chiffresAvant) {
        if (/\d/.test(apres.charAt(p))) vus++;
        p++;
      }
      try { numero.setSelectionRange(p, p); } catch (e) { /* champ masqué */ }
      combiner();
    }
    numero.addEventListener('input', reformater);

    // Un champ déjà rempli est considéré « figé » : on ne resynchronise pas
    // l'indicatif d'après le pays (on respecte ce qui est enregistré).
    var codeManuel = !!initial;
    selCode.addEventListener('change', function () {
      codeManuel = true;
      numero.value = formater(selCode.value, numero.value);
      majPlaceholder();
      combiner();
    });

    // Cale l'indicatif sur le pays saisi (uniquement pour un champ vide, ex.
    // inscription) tant que l'usager n'a pas choisi lui-même l'indicatif.
    var sel = wrap.getAttribute('data-country');
    var champPays = sel ? document.querySelector(sel) : null;
    if (champPays) {
      function synchroniserPays() {
        if (codeManuel) return;
        var p = champPays.value.trim().toLowerCase();
        if (parPays[p] != null) {
          selCode.selectedIndex = parPays[p];
          numero.value = formater(selCode.value, numero.value);
          majPlaceholder();
          combiner();
        }
      }
      champPays.addEventListener('change', synchroniserPays);
      champPays.addEventListener('blur', synchroniserPays);
      synchroniserPays();
    }
  }

  window.AubePhone = { init: init, formater: formater, analyser: analyser };
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.js-phone').forEach(init);
  });
})();
