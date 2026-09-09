"""SEO : métadonnées + données structurées (JSON-LD) façon marketplace.

On vise le même niveau qu'Upwork / Fiverr : titres et descriptions riches en
mots-clés, canonical, Open Graph, et surtout des données structurées
schema.org qui déclenchent les "rich results" Google :
  - Organization + WebSite (avec SearchAction -> sitelinks search box)
  - Person / ProfilePage pour les pilotes (note, localisation, métier)
  - JobPosting pour les missions (l'équivalent exact des annonces Upwork)
  - FAQPage sur l'accueil

Chaque page existe dans chaque langue de `i18n.SUPPORTED` sous sa propre URL
(voir i18n.url_prefix) ; les titres et descriptions viennent de la table `_S`
ci-dessous, une entree par langue, repli sur le francais. Les fonctions
renvoient des dicts ; les templates les sérialisent via le filtre Jinja
`|tojson` (échappement sûr).
"""

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import unicodedata
import urllib.request

import content
import i18n
from config import (
    CONTACT_EMAIL, CURRENT_SERVICE_SLUG, DATA_DIR, ROOT_DOMAIN, SITE_URL,
    SOCIAL_LINKS,
)

log = logging.getLogger("aubepilot.seo")

# Base canonique derivee du domaine central (pas de domaine code en dur).
CANONICAL_BASE = f"https://{CURRENT_SERVICE_SLUG}.{ROOT_DOMAIN}"

# ---------------------------------------------------------------------------
# Titres et descriptions par langue. Meme forme que i18n._T : {cle: {lang: txt}}.
# {place} = ville ou pays, {specialty} = libelle de specialite, {name} = pilote,
# {title} = titre de mission. Les variantes *_place portent la preposition.
# ---------------------------------------------------------------------------
_S = {
    "home.title": {"fr": "Louez un pilote de drone certifié — Devis gratuits | AubePilot", "en": "Hire a Certified Drone Pilot — Free Quotes | AubePilot", "es": "Contrate un piloto de drones certificado — Presupuesto gratis | AubePilot", "ru": "Нанять пилота дрона с сертификатом — бесплатный расчёт | AubePilot", "hi": "प्रमाणित ड्रोन पायलट हायर करें — मुफ़्त कोटेशन | AubePilot", "uk": "Найняти сертифікованого пілота дрона — безкоштовні кошториси | AubePilot", "tr": "Sertifikalı Drone Pilotu Kiralayın — Ücretsiz Teklif | AubePilot", "ur": "سرٹیفائیڈ ڈرون پائلٹ ہائر کریں — مفت کوٹیشن | AubePilot", "bn": "সার্টিফাইড ড্রোন পাইলট ভাড়া নিন — বিনামূল্যে কোটেশন | AubePilot"},
    "home.description": {"fr": "Trouvez et réservez un pilote de drone certifié près de chez vous par code postal ou adresse : prises de vue aériennes, inspections, mariages, immobilier. Devis gratuits, paiement sécurisé sous séquestre.", "en": "Find and book a certified drone pilot near you by postal code or address: aerial photography, inspections, weddings and real estate. Free quotes, secure escrow payments.", "es": "Reserve un piloto de drones certificado por código postal o dirección: tomas aéreas, inspecciones, bodas, inmobiliaria. Presupuestos gratis, pago en custodia.", "ru": "Найдите пилота дрона с сертификатом рядом с вами по индексу или адресу: аэросъёмка, инспекции, свадьбы, недвижимость. Бесплатный расчёт, оплата через эскроу.", "hi": "पोस्टल कोड या पते से नज़दीकी प्रमाणित ड्रोन पायलट खोजें और बुक करें: हवाई फ़ोटोग्राफ़ी, निरीक्षण, शादी, रियल एस्टेट। मुफ़्त कोटेशन, सुरक्षित एस्क्रो भुगतान।", "uk": "Знайдіть сертифікованого пілота дрона поруч за індексом чи адресою: аерозйомка, інспекції, весілля, нерухомість. Безкоштовні кошториси, безпечна ескроу-оплата.", "tr": "Posta kodu veya adresle yakınınızda sertifikalı drone pilotu bulup ücretsiz teklif alın: havadan çekim, denetim, düğün, emlak. Emanet hesapla güvenli ödeme.", "ur": "پوسٹل کوڈ یا پتے سے اپنے قریب سرٹیفائیڈ ڈرون پائلٹ تلاش کریں اور بک کریں: فضائی فوٹوگرافی، انسپیکشن، شادیاں، رئیل اسٹیٹ۔ مفت کوٹیشن، محفوظ ایسکرو ادائیگی۔", "bn": "পোস্ট কোড বা ঠিকানা দিয়ে কাছের সার্টিফাইড ড্রোন পাইলট খুঁজে বুক করুন: এরিয়াল ফটোগ্রাফি, ইন্সপেকশন, বিয়ে, রিয়েল এস্টেট। বিনামূল্যে কোটেশন, নিরাপদ এসক্রো পেমেন্ট।"},
    "pilots_list.title": {"fr": "Pilotes de drone certifiés à louer | AubePilot", "en": "Certified Drone Pilots for Hire | AubePilot", "es": "Pilotos de drones certificados para contratar | AubePilot", "ru": "Нанять сертифицированного пилота дрона | AubePilot", "hi": "प्रमाणित ड्रोन पायलट हायर करें | AubePilot", "uk": "Сертифіковані пілоти дронів для найму | AubePilot", "tr": "Sertifikalı Drone Pilotları Kiralayın | AubePilot", "ur": "سرٹیفائیڈ ڈرون پائلٹس ہائر کریں | AubePilot", "bn": "ভাড়ার জন্য সার্টিফাইড ড্রোন পাইলট | AubePilot"},
    "pilots_list.title_place": {"fr": "Pilotes de drone certifiés à louer {in_place} | AubePilot", "en": "Certified Drone Pilots for Hire in {place} | AubePilot", "es": "Pilotos de drones certificados para contratar en {place} | AubePilot", "ru": "Нанять сертифицированного пилота дрона: {place} | AubePilot", "hi": "{place} में प्रमाणित ड्रोन पायलट हायर करें | AubePilot", "uk": "Сертифіковані пілоти дронів для найму: {place} | AubePilot", "tr": "{place} Bölgesinde Sertifikalı Drone Pilotu Kiralayın | AubePilot", "ur": "{place} میں سرٹیفائیڈ ڈرون پائلٹس ہائر کریں | AubePilot", "bn": "{place}-এ ভাড়ার জন্য সার্টিফাইড ড্রোন পাইলট | AubePilot"},
    "pilots_list.description": {"fr": "Comparez les pilotes de drone certifiés : avis, tarifs, brevets et matériel. Demandez des devis gratuits pour vos prises de vue, inspections et événements.", "en": "Compare certified drone pilots: reviews, rates, licences and gear. Request free quotes for aerial photography, inspections and events.", "es": "Compare pilotos de drones certificados: reseñas, tarifas, licencias y equipo. Solicite presupuestos gratis para tomas aéreas, inspecciones y eventos.", "ru": "Сравните сертифицированных пилотов дронов: отзывы, тарифы, лицензии, оборудование. Запросите бесплатный расчёт стоимости аэросъёмки, инспекций и мероприятий.", "hi": "प्रमाणित ड्रोन पायलटों की तुलना करें: समीक्षाएँ, दरें, लाइसेंस और उपकरण। हवाई फ़ोटोग्राफ़ी, निरीक्षण और इवेंट के लिए मुफ़्त कोटेशन माँगें।", "uk": "Порівняйте сертифікованих пілотів дронів: відгуки, тарифи, ліцензії та обладнання. Отримайте безкоштовні кошториси на аерозйомку, інспекції та події.", "tr": "Sertifikalı drone pilotlarını karşılaştırın: yorum, fiyat, lisans ve ekipman. Havadan çekim, denetim ve etkinlikleriniz için ücretsiz teklif alın.", "ur": "سرٹیفائیڈ ڈرون پائلٹس کا موازنہ کریں: ریویوز، ریٹس، لائسنس اور آلات۔ فضائی فوٹوگرافی، انسپیکشن اور ایونٹس کے لیے مفت کوٹیشن طلب کریں۔", "bn": "সার্টিফাইড ড্রোন পাইলটদের তুলনা করুন: রিভিউ, রেট, লাইসেন্স ও সরঞ্জাম। এরিয়াল ফটোগ্রাফি, ইন্সপেকশন ও ইভেন্টের জন্য বিনামূল্যে কোটেশন চান।"},
    "pilots_list.description_place": {"fr": "Comparez les pilotes de drone certifiés {in_place} : avis, tarifs, brevets et matériel. Demandez des devis gratuits pour vos prises de vue, inspections et événements.", "en": "Compare certified drone pilots in {place}: reviews, rates, licences and gear. Request free quotes for aerial photography, inspections and events.", "es": "Compare pilotos de drones certificados en {place}: reseñas, tarifas, licencias y equipo. Solicite presupuestos gratis para tomas aéreas, inspecciones y eventos.", "ru": "Сертифицированные пилоты дронов, {place}: сравните отзывы, тарифы, лицензии и оборудование. Бесплатный расчёт стоимости аэросъёмки, инспекций и мероприятий.", "hi": "{place} में प्रमाणित ड्रोन पायलटों की तुलना करें: समीक्षाएँ, दरें, लाइसेंस और उपकरण। हवाई फ़ोटोग्राफ़ी, निरीक्षण और इवेंट के लिए मुफ़्त कोटेशन माँगें।", "uk": "Сертифіковані пілоти дронів, {place}: порівняйте відгуки, тарифи, ліцензії та обладнання. Безкоштовні кошториси на аерозйомку, інспекції та події.", "tr": "{place} bölgesinde sertifikalı drone pilotlarını karşılaştırın: yorum, fiyat, lisans ve ekipman. Havadan çekim, denetim ve etkinlik için ücretsiz teklif alın.", "ur": "{place} میں سرٹیفائیڈ ڈرون پائلٹس کا موازنہ کریں: ریویوز، ریٹس، لائسنس اور آلات۔ فضائی فوٹوگرافی، انسپیکشن اور ایونٹس کے لیے مفت کوٹیشن طلب کریں۔", "bn": "{place}-এ সার্টিফাইড ড্রোন পাইলটদের তুলনা করুন: রিভিউ, রেট, লাইসেন্স ও সরঞ্জাম। এরিয়াল ফটোগ্রাফি, ইন্সপেকশন ও ইভেন্টের জন্য বিনামূল্যে কোটেশন চান।"},
    "missions_list.title": {"fr": "Missions & contrats de drone à pourvoir | AubePilot", "en": "Open Drone Jobs & Contracts | AubePilot", "es": "Misiones y contratos con drones disponibles | AubePilot", "ru": "Открытые заказы и контракты для пилотов дронов | AubePilot", "hi": "उपलब्ध ड्रोन मिशन और कॉन्ट्रैक्ट | AubePilot", "uk": "Відкриті завдання та контракти для пілотів дронів | AubePilot", "tr": "Açık Drone Görevleri ve İş İlanları | AubePilot", "ur": "دستیاب ڈرون مشنز اور کنٹریکٹس | AubePilot", "bn": "উন্মুক্ত ড্রোন মিশন ও কন্ট্রাক্ট | AubePilot"},
    "missions_list.description": {"fr": "Trouvez des missions de pilotage de drone près de chez vous et envoyez vos devis : inspections, captations aériennes, événements, immobilier. Paiement sécurisé sous séquestre.", "en": "Find drone piloting jobs near you and send your quotes: inspections, aerial filming, events, real estate. Secure escrow payments.", "es": "Encuentre misiones de pilotaje de drones cerca de usted y envíe su presupuesto: inspecciones, filmación aérea, eventos, inmobiliaria. Pago seguro en custodia.", "ru": "Находите заказы для пилотов дронов рядом с вами и отправляйте предложения: инспекции, аэросъёмка, мероприятия, недвижимость. Безопасная оплата через эскроу.", "hi": "अपने पास के ड्रोन पायलटिंग मिशन खोजें और अपने कोटेशन भेजें: निरीक्षण, हवाई फ़िल्मांकन, इवेंट, रियल एस्टेट। सुरक्षित एस्क्रो भुगतान।", "uk": "Знаходьте завдання для пілота дрона поруч і надсилайте свої кошториси: інспекції, аерозйомка, події, нерухомість. Безпечна оплата через ескроу.", "tr": "Yakınınızdaki drone görevlerini bulun ve teklifinizi gönderin: denetim, havadan çekim, etkinlik, emlak. Emanet hesapla güvenli ödeme.", "ur": "اپنے قریب ڈرون پائلٹنگ کے مشنز تلاش کریں اور اپنی کوٹیشن بھیجیں: انسپیکشن، فضائی فلم بندی، ایونٹس، رئیل اسٹیٹ۔ ایسکرو کے ذریعے محفوظ ادائیگی۔", "bn": "আপনার কাছাকাছি ড্রোন পাইলটিংয়ের কাজ খুঁজুন ও কোটেশন পাঠান: ইন্সপেকশন, এরিয়াল ভিডিওগ্রাফি, ইভেন্ট, রিয়েল এস্টেট। নিরাপদ এসক্রো পেমেন্ট।"},
    "faq_page.title": {"fr": "Questions fréquentes — pilotes de drone, devis, paiement | AubePilot", "en": "Frequently Asked Questions — drone pilots, quotes, payment | AubePilot", "es": "Preguntas frecuentes — pilotos de drones, presupuestos, pago | AubePilot", "ru": "Частые вопросы — пилоты дронов, расчёт, оплата | AubePilot", "hi": "अक्सर पूछे जाने वाले प्रश्न — ड्रोन पायलट, कोटेशन, भुगतान | AubePilot", "uk": "Поширені запитання — пілоти дронів, кошториси, оплата | AubePilot", "tr": "Sık Sorulan Sorular — drone pilotları, teklif, ödeme | AubePilot", "ur": "اکثر پوچھے گئے سوالات — ڈرون پائلٹس، کوٹیشن، ادائیگی | AubePilot", "bn": "সচরাচর জিজ্ঞাসা — ড্রোন পাইলট, কোটেশন, পেমেন্ট | AubePilot"},
    "faq_page.description": {"fr": "Tout savoir sur AubePilot : trouver un pilote de drone par code postal, coût, profils vérifiés, paiement sous séquestre, certifications acceptées, versements aux pilotes.", "en": "Everything about AubePilot: finding a drone pilot by postal code, pricing, verified profiles, escrow payment, accepted licences, pilot payouts.", "es": "Todo sobre AubePilot: encontrar un piloto de drones por código postal, precios, perfiles verificados, pago en custodia, licencias aceptadas, pagos a pilotos.", "ru": "Всё об AubePilot: поиск пилота дрона по почтовому индексу, стоимость, проверенные профили, оплата через эскроу, принимаемые лицензии, выплаты пилотам.", "hi": "AubePilot के बारे में सब कुछ: पोस्टल कोड से ड्रोन पायलट खोजना, लागत, सत्यापित प्रोफ़ाइल, एस्क्रो भुगतान, मान्य लाइसेंस, पायलटों को भुगतान।", "uk": "Усе про AubePilot: пошук пілота дрона за поштовим індексом, вартість, перевірені профілі, оплата через ескроу, прийнятні ліцензії, виплати пілотам.", "tr": "AubePilot hakkında her şey: posta koduyla drone pilotu bulma, fiyatlar, doğrulanmış profiller, emanet hesapla ödeme, kabul edilen lisanslar, pilot ödemeleri.", "ur": "AubePilot کے بارے میں سب کچھ: پوسٹل کوڈ سے ڈرون پائلٹ کی تلاش، قیمتیں، تصدیق شدہ پروفائلز، ایسکرو ادائیگی، قابلِ قبول لائسنس، پائلٹس کو ادائیگیاں۔", "bn": "AubePilot সম্পর্কে সবকিছু: পোস্ট কোড দিয়ে ড্রোন পাইলট খোঁজা, খরচ, যাচাইকৃত প্রোফাইল, এসক্রো পেমেন্ট, গ্রহণযোগ্য লাইসেন্স, পাইলটদের পেআউট।"},
    "schools_page.title": {"fr": "Écoles et organismes de formation drone | AubePilot", "en": "Drone Training Schools & Organisations | AubePilot", "es": "Escuelas y centros de formación en drones | AubePilot", "ru": "Школы и центры обучения пилотов дронов | AubePilot", "hi": "ड्रोन प्रशिक्षण स्कूल और संस्थान | AubePilot", "uk": "Школи та центри навчання пілотів дронів | AubePilot", "tr": "Drone Eğitim Okulları ve Kurumları | AubePilot", "ur": "ڈرون ٹریننگ اسکول اور ادارے | AubePilot", "bn": "ড্রোন প্রশিক্ষণ স্কুল ও প্রতিষ্ঠান | AubePilot"},
    "schools_page.description": {"fr": "Inscrivez votre école ou organisme de formation drone : fiche publique avec vos formations (opérations de base, avancées, A2, STS…), visibilité sur la carte du réseau, demandes des futurs pilotes. Gratuit.", "en": "List your drone training school or organisation: public page with your programmes (basic, advanced, A2, STS…), visibility on the network map, requests from future pilots. Free.", "es": "Registre gratis su escuela o centro de formación en drones: ficha con sus cursos (básico, avanzado, A2, STS…), mapa de la red y solicitudes de futuros pilotos.", "ru": "Добавьте школу или учебный центр по дронам: страница с программами (базовые, продвинутые, A2, STS…), место на карте сети, заявки будущих пилотов. Бесплатно.", "hi": "अपना ड्रोन प्रशिक्षण स्कूल या संस्थान जोड़ें: कोर्स (बेसिक, एडवांस्ड, A2, STS…) के साथ सार्वजनिक पेज, नेटवर्क मैप पर दृश्यता, भावी पायलटों के अनुरोध। मुफ़्त।", "uk": "Додайте свою школу пілотів дронів: публічна сторінка з курсами (базові, розширені операції, A2, STS…), місце на мапі мережі, запити майбутніх пілотів. Безкоштовно.", "tr": "Drone okulunuzu veya eğitim kurumunuzu ücretsiz kaydedin: eğitimlerinizi (temel, ileri, A2, STS…) tanıtan sayfa, haritamızda görünürlük, aday pilot talepleri.", "ur": "اپنا ڈرون ٹریننگ اسکول یا ادارہ رجسٹر کریں: کورسز (بنیادی، ایڈوانسڈ، A2، STS…) والا عوامی صفحہ، نیٹ ورک کے نقشے پر نمایاں، نئے پائلٹس کی درخواستیں۔ مفت۔", "bn": "ড্রোন প্রশিক্ষণ স্কুল বা প্রতিষ্ঠান নিবন্ধন করুন: কোর্সসহ পাবলিক পেজ (বেসিক, অ্যাডভান্সড, A2, STS…), নেটওয়ার্ক ম্যাপে দৃশ্যমানতা, ভবিষ্যৎ পাইলটদের অনুরোধ। বিনামূল্যে।"},
    "contact_page.title": {"fr": "Nous joindre — AubePilot", "en": "Contact us — AubePilot", "es": "Contáctenos — AubePilot", "ru": "Связаться с нами — AubePilot", "hi": "हमसे संपर्क करें — AubePilot", "uk": "Зв’язатися з нами — AubePilot", "tr": "Bize Ulaşın — AubePilot", "ur": "ہم سے رابطہ کریں — AubePilot", "bn": "যোগাযোগ করুন — AubePilot"},
    "contact_page.description": {"fr": "Une question sur une mission, un devis, votre profil pilote ou un partenariat ? Écrivez-nous, on répond vite.", "en": "A question about a mission, a quote, your pilot profile or a partnership? Write to us, we answer fast.", "es": "¿Tiene una pregunta sobre una misión, un presupuesto, su perfil de piloto o una colaboración? Escríbanos, respondemos rápido.", "ru": "Вопрос о заказе, расчёте стоимости, профиле пилота или партнёрстве? Напишите нам, мы отвечаем быстро.", "hi": "किसी मिशन, कोटेशन, अपनी पायलट प्रोफ़ाइल या साझेदारी के बारे में सवाल है? हमें लिखें, हम जल्दी जवाब देते हैं।", "uk": "Маєте запитання про завдання, кошторис, профіль пілота чи партнерство? Напишіть нам, ми відповідаємо швидко.", "tr": "Bir görev, teklif, pilot profiliniz veya iş birliği hakkında sorunuz mu var? Bize yazın, hızlı yanıt veriyoruz.", "ur": "مشن، کوٹیشن، اپنے پائلٹ پروفائل یا پارٹنرشپ کے بارے میں کوئی سوال ہے؟ ہمیں لکھیں، ہم جلد جواب دیتے ہیں۔", "bn": "মিশন, কোটেশন, আপনার পাইলট প্রোফাইল বা পার্টনারশিপ নিয়ে প্রশ্ন আছে? আমাদের লিখুন, আমরা দ্রুত উত্তর দিই।"},
    "pilot_profile.title": {"fr": "{name} — Pilote de drone", "en": "{name} — Drone Pilot", "es": "{name} — Piloto de drones", "ru": "{name} — пилот дрона", "hi": "{name} — ड्रोन पायलट", "uk": "{name} — пілот дрона", "tr": "{name} — Drone Pilotu", "ur": "{name} — ڈرون پائلٹ", "bn": "{name} — ড্রোন পাইলট"},
    "pilot_profile.title_place": {"fr": "{name} — Pilote de drone {in_place}", "en": "{name} — Drone Pilot in {place}", "es": "{name} — Piloto de drones en {place}", "ru": "{name} — пилот дрона, {place}", "hi": "{name} — {place} में ड्रोन पायलट", "uk": "{name} — пілот дрона, {place}", "tr": "{name} — {place} Drone Pilotu", "ur": "{name} — {place} میں ڈرون پائلٹ", "bn": "{name} — {place}-এর ড্রোন পাইলট"},
    "pilot_profile.default_desc": {"fr": "Pilote de drone certifié sur AubePilot. Consultez avis, brevets, matériel et demandez un devis gratuit.", "en": "Certified drone pilot on AubePilot. See reviews, licences, gear and request a free quote.", "es": "Piloto de drones certificado en AubePilot. Consulte reseñas, licencias y equipo, y solicite un presupuesto gratis.", "ru": "Сертифицированный пилот дрона на AubePilot. Посмотрите отзывы, лицензии, оборудование и запросите бесплатный расчёт.", "hi": "AubePilot पर प्रमाणित ड्रोन पायलट। समीक्षाएँ, लाइसेंस और उपकरण देखें, और मुफ़्त कोटेशन माँगें।", "uk": "Сертифікований пілот дрона на AubePilot. Перегляньте відгуки, ліцензії, обладнання та запросіть безкоштовний кошторис.", "tr": "AubePilot platformunda sertifikalı drone pilotu. Yorumları, lisansları ve ekipmanı inceleyin, ücretsiz teklif isteyin.", "ur": "AubePilot پر سرٹیفائیڈ ڈرون پائلٹ۔ ریویوز، لائسنس اور آلات دیکھیں اور مفت کوٹیشن طلب کریں۔", "bn": "AubePilot-এ সার্টিফাইড ড্রোন পাইলট। রিভিউ, লাইসেন্স ও সরঞ্জাম দেখুন এবং বিনামূল্যে কোটেশন চান।"},
    "pilot_profile.default_desc_place": {"fr": "Pilote de drone certifié {in_place} sur AubePilot. Consultez avis, brevets, matériel et demandez un devis gratuit.", "en": "Certified drone pilot in {place} on AubePilot. See reviews, licences, gear and request a free quote.", "es": "Piloto de drones certificado en {place}, registrado en AubePilot. Consulte reseñas, licencias y equipo, y solicite un presupuesto gratis.", "ru": "Сертифицированный пилот дрона ({place}) на AubePilot. Посмотрите отзывы, лицензии, оборудование и запросите бесплатный расчёт.", "hi": "AubePilot पर {place} में प्रमाणित ड्रोन पायलट। समीक्षाएँ, लाइसेंस और उपकरण देखें, और मुफ़्त कोटेशन माँगें।", "uk": "Сертифікований пілот дрона ({place}) на AubePilot. Перегляньте відгуки, ліцензії, обладнання та запросіть безкоштовний кошторис.", "tr": "{place} bölgesinde, AubePilot platformunda sertifikalı drone pilotu. Yorumları, lisansları ve ekipmanı inceleyin, ücretsiz teklif isteyin.", "ur": "AubePilot پر {place} میں سرٹیفائیڈ ڈرون پائلٹ۔ ریویوز، لائسنس اور آلات دیکھیں اور مفت کوٹیشن طلب کریں۔", "bn": "AubePilot-এ {place}-এর সার্টিফাইড ড্রোন পাইলট। রিভিউ, লাইসেন্স ও সরঞ্জাম দেখুন এবং বিনামূল্যে কোটেশন চান।"},
    "pilot_profile.job_title": {"fr": "Pilote de drone", "en": "Drone Pilot", "es": "Piloto de drones", "ru": "Пилот дрона", "hi": "ड्रोन पायलट", "uk": "Пілот дрона", "tr": "Drone Pilotu", "ur": "ڈرون پائلٹ", "bn": "ড্রোন পাইলট"},
    "mission_posting.default_title": {"fr": "Mission drone", "en": "Drone job", "es": "Misión con dron", "ru": "Заказ для пилота дрона", "hi": "ड्रोन मिशन", "uk": "Завдання для пілота дрона", "tr": "Drone görevi", "ur": "ڈرون مشن", "bn": "ড্রোন মিশন"},
    "mission_posting.title": {"fr": "{title} — Mission drone", "en": "{title} — Drone job", "es": "{title} — Misión con dron", "ru": "{title} — заказ для пилота дрона", "hi": "{title} — ड्रोन मिशन", "uk": "{title} — завдання для пілота дрона", "tr": "{title} — Drone görevi", "ur": "{title} — ڈرون مشن", "bn": "{title} — ড্রোন মিশন"},
    "mission_posting.title_place": {"fr": "{title} — Mission drone {in_place}", "en": "{title} — Drone job in {place}", "es": "{title} — Misión con dron en {place}", "ru": "{title} — заказ для пилота дрона, {place}", "hi": "{title} — {place} में ड्रोन मिशन", "uk": "{title} — завдання для пілота дрона, {place}", "tr": "{title} — {place} bölgesinde drone görevi", "ur": "{title} — {place} میں ڈرون مشن", "bn": "{title} — {place}-এ ড্রোন মিশন"},
    "mission_posting.industry": {"fr": "Services drone", "en": "Drone services", "es": "Servicios de drones", "ru": "Услуги пилотов дронов", "hi": "ड्रोन सेवाएँ", "uk": "Послуги дронів", "tr": "Drone hizmetleri", "ur": "ڈرون سروسز", "bn": "ড্রোন সেবা"},
    "organization.description": {"fr": "AubePilot met en relation pilotes de drone certifiés et clients partout dans le monde : prises de vue aériennes, inspections, événements.", "en": "AubePilot connects certified drone pilots with clients worldwide for aerial photography, inspections and events.", "es": "AubePilot conecta a pilotos de drones certificados con clientes de todo el mundo: fotografía aérea, inspecciones y eventos.", "ru": "AubePilot связывает сертифицированных пилотов дронов с клиентами по всему миру: аэросъёмка, инспекции, мероприятия.", "hi": "AubePilot दुनिया भर में प्रमाणित ड्रोन पायलटों को ग्राहकों से जोड़ता है: हवाई फ़ोटोग्राफ़ी, निरीक्षण, इवेंट।", "uk": "AubePilot з’єднує сертифікованих пілотів дронів із клієнтами по всьому світу: аерозйомка, інспекції, події.", "tr": "AubePilot, sertifikalı drone pilotlarını dünyanın her yerindeki müşterilerle buluşturur: havadan çekim, denetim, etkinlik.", "ur": "AubePilot دنیا بھر میں سرٹیفائیڈ ڈرون پائلٹس کو کلائنٹس سے جوڑتا ہے: فضائی فوٹوگرافی، انسپیکشن اور ایونٹس۔", "bn": "AubePilot সারা বিশ্বে সার্টিফাইড ড্রোন পাইলট ও ক্লায়েন্টদের সংযুক্ত করে: এরিয়াল ফটোগ্রাফি, ইন্সপেকশন ও ইভেন্ট।"},
    "landing_specialty.title": {"fr": "Pilotes de drone — {specialty} | AubePilot", "en": "Drone pilots — {specialty} | AubePilot", "es": "Pilotos de drones — {specialty} | AubePilot", "ru": "Пилоты дронов — {specialty} | AubePilot", "hi": "ड्रोन पायलट — {specialty} | AubePilot", "uk": "Пілоти дронів — {specialty} | AubePilot", "tr": "Drone Pilotları — {specialty} | AubePilot", "ur": "ڈرون پائلٹس — {specialty} | AubePilot", "bn": "ড্রোন পাইলট — {specialty} | AubePilot"},
    "landing_specialty.title_place": {"fr": "Pilotes de drone — {specialty} {in_place} | AubePilot", "en": "Drone pilots — {specialty} in {place} | AubePilot", "es": "Pilotos de drones — {specialty} en {place} | AubePilot", "ru": "Пилоты дронов — {specialty}, {place} | AubePilot", "hi": "ड्रोन पायलट — {place} में {specialty} | AubePilot", "uk": "Пілоти дронів — {specialty}, {place} | AubePilot", "tr": "{place} Drone Pilotları — {specialty} | AubePilot", "ur": "ڈرون پائلٹس — {place} میں {specialty} | AubePilot", "bn": "ড্রোন পাইলট — {place}-এ {specialty} | AubePilot"},
    "landing_specialty.description": {"fr": "Comparez les pilotes de drone certifiés pour {specialty} : brevets, matériel, avis et tarifs. Devis gratuits, paiement sécurisé sous séquestre.", "en": "Compare certified drone pilots for {specialty}: licences, gear, reviews and rates. Free quotes, secure escrow payment.", "es": "Compare pilotos de drones certificados para {specialty}: licencias, equipo, reseñas y tarifas. Presupuestos gratis, pago seguro en custodia.", "ru": "Сравните сертифицированных пилотов дронов по направлению «{specialty}»: лицензии, оборудование, отзывы и тарифы. Бесплатный расчёт, оплата через эскроу.", "hi": "{specialty} के लिए प्रमाणित ड्रोन पायलटों की तुलना करें: लाइसेंस, उपकरण, समीक्षाएँ और दरें। मुफ़्त कोटेशन, सुरक्षित एस्क्रो भुगतान।", "uk": "Порівняйте сертифікованих пілотів дронів у категорії «{specialty}»: ліцензії, обладнання, відгуки й тарифи. Безкоштовні кошториси, безпечна ескроу-оплата.", "tr": "{specialty} için sertifikalı drone pilotlarını karşılaştırın: lisans, ekipman, yorum ve fiyat. Ücretsiz teklif, emanet hesapla güvenli ödeme.", "ur": "{specialty} کے لیے سرٹیفائیڈ ڈرون پائلٹس کا موازنہ کریں: لائسنس، آلات، ریویوز اور ریٹس۔ مفت کوٹیشن، ایسکرو کے ذریعے محفوظ ادائیگی۔", "bn": "{specialty} কাজের জন্য সার্টিফাইড ড্রোন পাইলটদের তুলনা করুন: লাইসেন্স, সরঞ্জাম, রিভিউ ও রেট। বিনামূল্যে কোটেশন, নিরাপদ এসক্রো পেমেন্ট।"},
    "landing_specialty.description_place": {"fr": "Comparez les pilotes de drone certifiés pour {specialty} {in_place} : brevets, matériel, avis et tarifs. Devis gratuits, paiement sécurisé sous séquestre.", "en": "Compare certified drone pilots for {specialty} in {place}: licences, gear, reviews and rates. Free quotes, secure escrow payment.", "es": "Compare pilotos de drones certificados para {specialty} en {place}: licencias, equipo, reseñas y tarifas. Presupuestos gratis, pago seguro en custodia.", "ru": "Сертифицированные пилоты дронов по направлению «{specialty}», {place}: лицензии, оборудование, отзывы и тарифы. Бесплатный расчёт, оплата через эскроу.", "hi": "{place} में {specialty} के लिए प्रमाणित ड्रोन पायलटों की तुलना करें: लाइसेंस, उपकरण, समीक्षाएँ और दरें। मुफ़्त कोटेशन, सुरक्षित एस्क्रो भुगतान।", "uk": "Сертифіковані пілоти дронів, {place}, категорія «{specialty}»: порівняйте ліцензії, обладнання, відгуки й тарифи. Безкоштовні кошториси, безпечна ескроу-оплата.", "tr": "{place} bölgesinde {specialty} için sertifikalı drone pilotlarını karşılaştırın: lisans, ekipman, yorum, fiyat. Ücretsiz teklif, emanet hesapla güvenli ödeme.", "ur": "{place} میں {specialty} کے لیے سرٹیفائیڈ ڈرون پائلٹس کا موازنہ کریں: لائسنس، آلات، ریویوز اور ریٹس۔ مفت کوٹیشن، ایسکرو کے ذریعے محفوظ ادائیگی۔", "bn": "{place}-এ {specialty} কাজের জন্য সার্টিফাইড ড্রোন পাইলটদের তুলনা করুন: লাইসেন্স, সরঞ্জাম, রিভিউ ও রেট। বিনামূল্যে কোটেশন, নিরাপদ এসক্রো পেমেন্ট।"},
}


def _s(lang, key, *, city="", country="", **kw):
    """`city`/`country` alimentent {place} (affichage) et {in_place} (francais,
    avec la bonne preposition) ; les autres variables passent telles quelles."""
    entry = _S[key]
    txt = entry.get(lang) or entry[i18n.DEFAULT]
    if city or country:
        shown_country = i18n.country_name(country, lang) if country else ""
        kw.setdefault("place", ", ".join(x for x in (city, shown_country) if x))
        kw.setdefault("in_place", i18n.fr_place(city, country))
    return txt.format(**kw) if kw else txt


def slugify(text: str) -> str:
    """Segment d'URL stable et neutre : 'Trois-Rivières' -> 'trois-rivieres'.
    Une valeur sans equivalent ASCII (ex. « Москва ») recoit un court hachage
    plutot qu'un slug vide qui ferait collision."""
    raw = " ".join((text or "").split())
    ascii_ = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    if not slug and raw:
        slug = "x" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return slug


def lang_url(path: str, lang: str) -> str:
    """URL absolue d'une page publique dans une langue donnee."""
    return CANONICAL_BASE + i18n.localized_path(path, lang)
ORG_NAME = "AubePilot"
ORG_LOGO = CANONICAL_BASE + "/static/brand/og-image.png"

# Pages publiques exposées au sitemap (chemin, priorité, fréquence)
PUBLIC_ROUTES = [
    ("/", "1.0", "daily"),
    ("/pilotes", "0.9", "daily"),
    ("/missions", "0.9", "daily"),
    ("/ecoles", "0.7", "weekly"),
    ("/faq", "0.6", "monthly"),
    ("/contact", "0.5", "monthly"),
    ("/mentions-legales", "0.3", "yearly"),
    ("/confidentialite", "0.3", "yearly"),
    ("/cgu", "0.3", "yearly"),
]

# Répertoires interdits aux robots (espace privé, paiement, admin, API)
ROBOTS_DISALLOW = [
    "/espace", "/reservations/", "/admin/", "/stripe/",
    "/api/", "/lang/", "/media/",
]


def _truncate(text, length=158):
    text = " ".join((text or "").split())
    if len(text) <= length:
        return text
    return text[:length - 1].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------- #
# JSON-LD globaux (sur toutes les pages)
# --------------------------------------------------------------------------- #

def organization_ld(lang="fr"):
    # Slogan = l'accroche de l'accueil, deja traduite dans i18n._T
    slogan = " ".join(i18n.t(k, lang) for k in ("home.h1_a", "home.h1_b", "home.h1_b_em"))
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": ORG_NAME,
        "url": CANONICAL_BASE,
        "logo": ORG_LOGO,
        "image": ORG_LOGO,
        "description": _s(lang, "organization.description"),
        "slogan": slogan,
        # Point de contact + reseaux : signaux de confiance (Knowledge Panel)
        "contactPoint": {
            "@type": "ContactPoint",
            "contactType": "customer support",
            "email": CONTACT_EMAIL,
            "url": lang_url("/contact", lang),
            # L'equipe repond en francais et en anglais ; l'interface, elle,
            # existe dans toutes les langues de SUPPORTED.
            "availableLanguage": ["fr", "en"],
        },
        **({"sameAs": [url for _name, url in SOCIAL_LINKS]} if SOCIAL_LINKS else {}),
    }


def website_ld(lang="fr"):
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": ORG_NAME,
        "url": lang_url("/", lang),
        "inLanguage": lang,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": lang_url("/pilotes", lang) + "?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def global_ld(lang="fr"):
    return [organization_ld(lang), website_ld(lang)]


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

def _faq_ld(entries):
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": e["question"],
             "acceptedAnswer": {"@type": "Answer", "text": e["answer"]}}
            for e in entries
        ],
    }


# La FAQ (content.py) n'existe qu'en francais et en anglais : ailleurs, pas
# de FAQPage en donnees structurees, ce serait du francais annonce comme du
# russe. La page /faq elle-meme n'a d'URL que dans ces deux langues.
FAQ_LANGS = ("fr", "en")


def home(lang="fr"):
    # Les questions visibles sur l'accueil (aperçu) = celles des rich results.
    out = {"title": _s(lang, "home.title"), "description": _s(lang, "home.description")}
    if lang in FAQ_LANGS:
        out["jsonld"] = [_faq_ld(content.faq(lang, featured_only=True))]
    return out


def simple_page(lang="fr", *, title_key, description_key=None, robots=None):
    """Pages sans metadonnees dediees (connexion, inscription, legal) : un
    titre propre a la page plutot que celui de l'accueil en double."""
    out = {"title": i18n.t(title_key, lang) + " | AubePilot"}
    if description_key:
        out["description"] = _truncate(i18n.t(description_key, lang))
    if robots:
        out["robots"] = robots
    return out


def faq_page(lang="fr"):
    return {"title": _s(lang, "faq_page.title"), "description": _s(lang, "faq_page.description"),
            "jsonld": [_faq_ld(content.faq(lang))]}


def schools_page(lang="fr"):
    return {"title": _s(lang, "schools_page.title"),
            "description": _s(lang, "schools_page.description")}


def contact_page(lang="fr"):
    title = _s(lang, "contact_page.title")
    desc = _s(lang, "contact_page.description")
    ld = {
        "@context": "https://schema.org",
        "@type": "ContactPage",
        "name": title,
        "url": lang_url("/contact", lang),
        "mainEntity": {"@type": "Organization", "name": ORG_NAME,
                       "email": CONTACT_EMAIL, "url": CANONICAL_BASE},
    }
    return {"title": title, "description": desc, "jsonld": [ld]}


def pilots_list(lang="fr", params=None):
    params = params or {}
    city, country = params.get("city") or "", params.get("country") or ""
    if city or country:
        loc = {"city": city} if city else {"country": country}
        return {"title": _s(lang, "pilots_list.title_place", **loc),
                "description": _s(lang, "pilots_list.description_place", **loc)}
    return {"title": _s(lang, "pilots_list.title"),
            "description": _s(lang, "pilots_list.description")}


def missions_list(lang="fr"):
    return {"title": _s(lang, "missions_list.title"),
            "description": _s(lang, "missions_list.description")}


def landing_page(lang="fr", *, path, specialty="", city="", country="", count=0, pilot_ids=()):
    """Page d'atterrissage (pays, ville ou specialite). Sans pilote, la page
    reste servie mais passe en noindex : une liste vide indexee vaut une
    « soft 404 » chez Google et fait du tort a tout le site."""
    loc = {"city": city} if city else {"country": country}
    if specialty and (city or country):
        title = _s(lang, "landing_specialty.title_place", specialty=specialty, **loc)
        desc = _s(lang, "landing_specialty.description_place", specialty=specialty, **loc)
    elif specialty:
        title = _s(lang, "landing_specialty.title", specialty=specialty)
        desc = _s(lang, "landing_specialty.description", specialty=specialty)
    else:
        title = _s(lang, "pilots_list.title_place", **loc)
        desc = _s(lang, "pilots_list.description_place", **loc)
    out = {"title": title, "description": _truncate(desc)}
    if not count:
        out["robots"] = "noindex, follow"
        return out
    out["jsonld"] = [{
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "url": lang_url(path, lang),
        "inLanguage": lang,
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": count,
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "url": lang_url(f"/pilotes/{pid}", lang)}
                for i, pid in enumerate(pilot_ids)
            ],
        },
    }]
    return out


def pilot_profile(lang="fr", *, name, city="", country="", headline="",
                  bio="", rating=None, image=None, user_id=None):
    place = ", ".join(x for x in [city, country] if x)
    stars = ""
    if rating and rating.get("count"):
        stars = f" · {rating['avg']}★ ({rating['count']})"
    if place:
        title = _s(lang, "pilot_profile.title_place", name=name, city=city, country=country)
        default_desc = _s(lang, "pilot_profile.default_desc_place", city=city, country=country)
    else:
        title = _s(lang, "pilot_profile.title", name=name)
        default_desc = _s(lang, "pilot_profile.default_desc")
    # ~60 caracteres visibles chez Google : la note saute si elle fait deborder
    if len(title + stars) > 60:
        stars = ""
    title += stars + " | AubePilot"
    desc = _truncate(headline or bio or default_desc)

    person = {
        "@type": "Person",
        "name": name,
        "jobTitle": _s(lang, "pilot_profile.job_title"),
        "worksFor": {"@type": "Organization", "name": ORG_NAME, "url": CANONICAL_BASE},
    }
    if headline:
        person["description"] = _truncate(headline, 250)
    if place:
        person["address"] = {
            "@type": "PostalAddress",
            **({"addressLocality": city} if city else {}),
            **({"addressCountry": country} if country else {}),
        }
    if image:
        person["image"] = image
    if user_id:
        person["url"] = lang_url(f"/pilotes/{user_id}", lang)
    if rating and rating.get("count"):
        person["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": rating["avg"],
            "reviewCount": rating["count"],
            "bestRating": 5, "worstRating": 1,
        }
    profile_ld = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "mainEntity": person,
    }
    return {"title": title, "description": desc, "og_image": image,
            "og_type": "profile", "jsonld": [profile_ld]}


def mission_posting(lang="fr", *, mission, mission_type_label="", url=None):
    title_txt = mission.get("title") or _s(lang, "mission_posting.default_title")
    place = ", ".join(x for x in [mission.get("city"), mission.get("country")] if x)
    if place:
        title = _s(lang, "mission_posting.title_place", title=title_txt,
                   city=mission.get("city") or "", country=mission.get("country") or "")
    else:
        title = _s(lang, "mission_posting.title", title=title_txt)
    title += " | AubePilot"
    desc = _truncate(mission.get("description") or title_txt)

    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title_txt,
        "description": mission.get("description") or title_txt,
        "datePosted": (mission.get("created_at") or "")[:10],
        "employmentType": "CONTRACTOR",
        "industry": mission_type_label or _s(lang, "mission_posting.industry"),
        "hiringOrganization": {
            "@type": "Organization", "name": ORG_NAME, "url": CANONICAL_BASE,
            "logo": ORG_LOGO,
        },
        "directApply": True,
    }
    if url:
        posting["url"] = url
    if mission.get("country"):
        posting["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                **({"addressLocality": mission["city"]} if mission.get("city") else {}),
                **({"addressRegion": mission["region"]} if mission.get("region") else {}),
                "addressCountry": mission["country"],
            },
        }
    if mission.get("end_date"):
        posting["validThrough"] = str(mission["end_date"])[:10]
    bmin, bmax = mission.get("budget_min"), mission.get("budget_max")
    if bmin or bmax:
        val = {"@type": "QuantitativeValue", "unitText": "PROJECT"}
        if bmin and bmax:
            val["minValue"], val["maxValue"] = bmin, bmax
        else:
            val["value"] = bmin or bmax
        posting["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": mission.get("currency") or "EUR",
            "value": val,
        }
    return {"title": title, "description": desc, "jsonld": [posting]}


# --------------------------------------------------------------------------- #
# Sitemap : chaque page dans chaque langue, avec ses alternates hreflang
# --------------------------------------------------------------------------- #

def render_sitemap_index() -> str:
    """/sitemap.xml renvoie un index : un fichier par langue, chacun sous la
    limite du protocole (50 000 URL) meme avec des milliers de fiches."""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for code in i18n.SUPPORTED:
        xml.append(f"  <sitemap><loc>{CANONICAL_BASE}/sitemap-{code}.xml</loc></sitemap>")
    xml.append("</sitemapindex>")
    return "\n".join(xml)


def render_sitemap(entries, lang) -> str:
    """`entries` = [(chemin nu, lastmod, priorite, frequence, langues)]. Sort les
    URL de `lang`, chacune listant ses versions dans les autres langues
    (format hreflang des sitemaps, celui que Google recommande)."""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
           '        xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for path, lastmod, prio, freq, langs in entries:
        if lang not in langs:
            continue
        xml.append("  <url>")
        xml.append(f"    <loc>{lang_url(path, lang)}</loc>")
        if lastmod:
            xml.append(f"    <lastmod>{lastmod}</lastmod>")
        xml.append(f"    <changefreq>{freq}</changefreq>")
        xml.append(f"    <priority>{prio}</priority>")
        if len(langs) > 1:
            for code in langs:
                xml.append(f'    <xhtml:link rel="alternate" hreflang="{code}" href="{lang_url(path, code)}"/>')
            xml.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{lang_url(path, i18n.DEFAULT)}"/>')
        xml.append("  </url>")
    xml.append("</urlset>")
    return "\n".join(xml)


# --------------------------------------------------------------------------- #
# IndexNow : prevenir Bing, Yandex, Seznam, Naver des qu'une page change.
# Google ne l'ecoute pas (il suit le sitemap et ses <lastmod>), les autres si.
# La cle derive du secret de l'app : rien a configurer, rien a stocker.
# --------------------------------------------------------------------------- #

def _load_indexnow_key() -> str:
    """Cle publique par construction (servie sur /<cle>.txt) : donc jamais
    derivee du secret de l'app, qu'elle exposerait a une recherche hors
    ligne. Un aleas cree une fois et garde dans DATA_DIR."""
    path = os.path.join(DATA_DIR, "indexnow.key")
    try:
        with open(path, encoding="ascii") as f:
            key = f.read().strip()
        if re.fullmatch(r"[0-9a-f]{32}", key):
            return key
    except OSError:
        pass
    key = secrets.token_hex(16)
    try:
        with open(path, "w", encoding="ascii") as f:
            f.write(key)
    except OSError as exc:              # disque en lecture seule : cle ephemere
        log.warning("IndexNow : cle non persistee (%s)", exc)
    return key


INDEXNOW_KEY = _load_indexnow_key()
INDEXNOW_ENDPOINT = "https://api.indexnow.org/IndexNow"
# Actif seulement quand le site tourne pour de vrai en HTTPS (pas en dev, pas
# sous pytest) : on ne signale pas des URL que personne ne peut visiter.
INDEXNOW_ENABLED = SITE_URL.startswith("https://")


def indexnow_ping(paths, *, enabled=None) -> int:
    """Signale les versions linguistiques de `paths`. Retourne le nombre d'URL
    envoyees (0 si desactive). Reseau en arriere-plan : jamais bloquant."""
    if not (INDEXNOW_ENABLED if enabled is None else enabled):
        return 0
    urls = sorted({lang_url(p, code) for p in paths for code in i18n.SUPPORTED})
    if not urls:
        return 0
    body = json.dumps({
        "host": CANONICAL_BASE.split("://", 1)[1],
        "key": INDEXNOW_KEY,
        "keyLocation": f"{CANONICAL_BASE}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }).encode("utf-8")

    def _send():
        req = urllib.request.Request(
            INDEXNOW_ENDPOINT, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                log.info("IndexNow : %d URL, reponse %s", len(urls), resp.status)
        except Exception as exc:  # reseau, 4xx : on note, on ne casse rien
            log.warning("IndexNow injoignable : %s", exc)

    threading.Thread(target=_send, name="indexnow", daemon=True).start()
    return len(urls)

