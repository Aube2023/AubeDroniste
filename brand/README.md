# Visuels hors site

`linkedin-cover.html` : couverture LinkedIn (fond navy, marque, « Trouvez · Réservez · Volez »).
La marque vient de `static/brand/logo-mark-white.svg` (copier le SVG à côté du HTML avant de rendre).
Deux formats, rendus avec Chrome sans fenêtre, à l'échelle 2 :

    .cover{--w:1584px;--h:396px;--mark:150px;--word:86px;--tag:17px;--sub:22px;--subw:720px;--gap:36px;--tgap:14px;--pad:28px;--url:15px} .content{left:560px;top:50%;transform:translateY(-50%)}   # profil personnel 1584×396
    .cover{--w:1128px;--h:191px;--mark:82px;--word:46px;--tag:10px;--sub:13px;--subw:540px;--gap:22px;--tgap:7px;--pad:14px;--url:9px} .content{left:330px;top:50%;transform:translateY(-50%)}      # page entreprise 1128×191

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
      --force-device-scale-factor=2 --window-size=1584,396 --virtual-time-budget=8000 --screenshot=out.png file://…/linkedin-cover.html

Le texte est placé à droite : LinkedIn pose la photo de profil (ou le logo de la page) en bas à gauche de la bannière.
