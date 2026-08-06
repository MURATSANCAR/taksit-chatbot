"""Deterministic guest need extraction — intent / category / budget.

Loginsiz guest turn'ünde LLM sunucusu (CPU/RAM, yavaş) istek anında
çağrılmaz. Bu modül serbest Türkçe metinden **kategori** ve **bütçe**yi
sub-ms, ağsız ve deterministik çıkarır. Kalite; zayıf regex yerine
28 kategorilik katalog + morfoloji-duyarlı alias sözlüğü + model/spec
sayılarını eleyen sağlam bütçe parser'ından gelir.

Emitted `category_code` katalog kodudur ("1".."28"), böylece
`CampaignRepository.list_by_category_codes` ile birebir hizalıdır.
`family` yalnızca telefon↔beyaz eşya güvenlik guard'ı içindir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Turkish normalization
# ---------------------------------------------------------------------------

_TR_LOWER = str.maketrans({"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})


def normalize(text: str) -> str:
    """Lowercase Turkish-correctly and collapse whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.translate(_TR_LOWER).lower()).strip()


_ASCII_FOLD = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u"})


def ascii_fold(text: str) -> str:
    """Fold Turkish diacritics so 'buzdolabı' == 'buzdolabi' (users often omit them)."""
    return normalize(text).translate(_ASCII_FOLD)


_LETTER = "a-zçğıöşü"


# ---------------------------------------------------------------------------
# Category catalog (28 categories from the Kategoriler sheet)
# ---------------------------------------------------------------------------
# family: coarse group used ONLY for the phone-vs-non-phone safety guard and
#         for legacy alias-code expansion in the ranker. Non-phone families
#         never cross-pollinate into phone campaigns.


@dataclass(frozen=True)
class CategoryHit:
    category_code: str          # catalog code "1".."28"
    display_name: str
    family: str
    confidence: float = 0.95
    # duck-typed alias so existing pipeline (_extract_need) keeps working
    @property
    def category_hint(self) -> str:
        return self.category_code


@dataclass(frozen=True)
class _CatDef:
    code: str
    name: str
    family: str
    aliases: tuple[str, ...]


# NOTE: order within aliases does not matter; specificity is computed globally
# (multi-word + longer aliases win) so "akıllı saat" (12) beats "saat" (18).
_CATALOG: tuple[_CatDef, ...] = (
    _CatDef("1", "Cep Telefonu", "MOBILE_PHONE", (
        "cep telefonu", "akıllı telefon", "akilli telefon", "telefon", "telefonu",
        "smartphone", "iphone", "samsung galaxy", "galaxy s", "galaxy a", "galaxy z",
        "galaxy note", "xiaomi", "redmi", "redmi note", "poco", "oppo", "realme",
        "vivo", "huawei", "tecno", "reeder", "android telefon", "gsm",
        # common phone model tokens without the brand word ("s24 ultra", "a54")
        "s24", "s23", "s22", "s21", "a54", "a34", "a24", "a14", "note 13",
        "14 pro max", "15 pro max", "16 pro max", "13 pro", "14 pro", "15 pro",
        "16 pro", "pro max",
    )),
    _CatDef("2", "Elektrikli Küçük Ev Aletleri", "SMALL_APPLIANCE", (
        "küçük ev aleti", "kucuk ev aleti", "airfryer", "air fryer", "fritöz", "fritoz",
        "blender", "mikser", "rondo", "mutfak robotu", "doğrayıcı", "tost makinesi",
        "su ısıtıcı", "kettle", "kahve makinesi", "espresso", "çay makinesi",
        "ütü", "utu", "robot süpürge", "robot supurge", "süpürge", "supurge",
        "elektrikli süpürge", "ekmek kızartma", "waffle", "ızgara", "airfryer",
    )),
    _CatDef("3", "Bilgisayar", "COMPUTER", (
        "bilgisayar", "laptop", "notebook", "dizüstü", "dizustu", "masaüstü", "masaustu",
        "macbook", "imac", "ultrabook", "oyuncu bilgisayarı", "gaming laptop",
        "monitör", "monitor", "ekran kartı", "anakart",
    )),
    _CatDef("4", "Tablet", "TABLET", (
        "tablet", "ipad", "galaxy tab", "tab s", "lenovo tab",
    )),
    _CatDef("5", "Televizyon, Sinema ve Ses Sistemleri", "TV_AUDIO", (
        "televizyon", "tv", "smart tv", "led tv", "oled", "qled", "soundbar",
        "ses sistemi", "hoparlör", "hoparlor", "ev sinema", "receiver", "amplifikatör",
        "bluetooth hoparlör",
    )),
    _CatDef("6", "Çamaşır, Bulaşık, Kurutma Makinası", "WHITE_GOODS", (
        "çamaşır makinesi", "camasir makinesi", "çamaşır", "camasir",
        "bulaşık makinesi", "bulasik makinesi", "bulaşık", "bulasik",
        "kurutma makinesi", "kurutmalı",
    )),
    _CatDef("7", "Diğer Beyaz Eşya (Buzdolabı vb.)", "WHITE_GOODS", (
        "buzdolabı", "buzdolabi", "buz dolabı", "beyaz eşya", "beyaz esya",
        "derin dondurucu", "dondurucu", "buzluk", "fırın", "firin", "ankastre",
        "ocak", "set üstü ocak", "davlumbaz", "mikrodalga", "aspiratör",
    )),
    _CatDef("8", "Kişisel Bakım Ürünleri", "PERSONAL_CARE", (
        "kişisel bakım", "kisisel bakim", "tıraş makinesi", "tiras makinesi",
        "epilasyon aleti", "epilasyon", "epilatör", "saç kurutma makinesi",
        "saç kurutma", "fön makinesi", "fon makinesi", "saç düzleştirici",
        "düzleştirici", "saç maşası", "diş fırçası",
    )),
    _CatDef("9", "Klima, Isıtma/Soğutma Ekipmanları", "AIR_CONDITIONER", (
        "klima", "split klima", "portatif klima", "ısıtıcı", "isitici", "soğutucu",
        "vantilatör", "vantilator", "kombi", "radyatör", "şofben", "termosifon",
    )),
    _CatDef("10", "Mobil Aksesuar", "ACCESSORY", (
        "mobil aksesuar", "telefon kılıfı", "telefon kilifi", "kılıf", "kilif",
        "ekran koruyucu", "şarj cihazı", "şarj aleti", "sarj aleti", "powerbank",
        "power bank", "kulaklık", "kulaklik", "airpods", "earbuds",
        "bluetooth kulaklık", "kablo", "adaptör", "telefon tutucu",
    )),
    _CatDef("11", "Oyun Konsolları", "CONSOLE", (
        "oyun konsolu", "konsol", "playstation", "ps5", "ps4", "ps 5", "ps 4",
        "xbox", "series x", "series s", "nintendo", "nintendo switch", "switch",
    )),
    _CatDef("12", "Giyilebilir Teknoloji (Akıllı Saat, Bileklik)", "WEARABLE", (
        "akıllı saat", "akilli saat", "akıllı bileklik", "akıllı bilezik",
        "smartwatch", "apple watch", "galaxy watch", "mi band", "amazfit",
        "fitbit", "giyilebilir teknoloji", "akıllı bant",
    )),
    _CatDef("13", "Yazıcı, Tarayıcı", "PRINTER", (
        "yazıcı", "yazici", "tarayıcı", "tarayici", "printer", "scanner",
        "lazer yazıcı", "mürekkep püskürtmeli", "çok fonksiyonlu yazıcı", "toner", "kartuş",
    )),
    _CatDef("14", "Fotoğraf Makinesi ve Kamera", "CAMERA", (
        "fotoğraf makinesi", "fotograf makinesi", "kamera", "dslr", "aynasız",
        "mirrorless", "gopro", "aksiyon kamera", "objektif", "lens", "kompakt kamera",
    )),
    _CatDef("15", "Mobilya", "FURNITURE", (
        "mobilya", "koltuk", "kanepe", "yemek masası", "masa", "sandalye", "gardırop",
        "dolap", "tv ünitesi", "komodin", "kitaplık", "vestiyer", "berjer", "puf",
    )),
    _CatDef("16", "Yatak", "BED", (
        "yatak", "baza", "yatak seti", "ortopedik yatak", "visco", "yaylı yatak",
        "şilte", "silte", "yatak başlığı",
    )),
    _CatDef("17", "Gözlük", "GLASSES", (
        "gözlük", "gozluk", "güneş gözlüğü", "gunes gozlugu", "numaralı gözlük",
        "optik", "gözlük çerçevesi", "çerçeve",
    )),
    _CatDef("18", "Saat", "WATCH", (
        "kol saati", "duvar saati", "saat", "cep saati",
    )),
    _CatDef("20", "Cep Telefonu (Yenilenmiş)", "MOBILE_PHONE", (
        "yenilenmiş telefon", "yenilenmis telefon", "yenilenmiş iphone",
        "yenilenmiş cep telefonu", "refurbished telefon", "yenilenmiş",
    )),
    _CatDef("21", "Motosiklet 600 CC Altı", "MOTORCYCLE", (
        "motosiklet", "motor", "scooter", "moped", "maxi scooter",
    )),
    _CatDef("22", "Sağlık", "HEALTH", (
        "sağlık", "saglik", "tansiyon aleti", "şeker ölçüm", "nebulizatör",
        "termometre", "ateş ölçer", "medikal", "oksijen konsantratörü",
    )),
    _CatDef("23", "Turizm/Seyahat", "TRAVEL", (
        "tatil", "seyahat", "tur paketi", "uçak bileti", "otel", "konaklama",
        "turizm", "vize",
    )),
    _CatDef("24", "Eğitim", "EDUCATION", (
        "eğitim", "egitim", "kurs", "online kurs", "dil kursu", "sertifika programı",
        "üniversite", "okul taksidi", "ders",
    )),
    _CatDef("25", "Otomotiv Yedek Parça", "AUTOPARTS", (
        "yedek parça", "yedek parca", "lastik", "akü", "aku", "jant", "fren balata",
        "motor yağı", "oto aksesuar", "otomotiv",
    )),
    _CatDef("27", "Süpermarket", "SUPERMARKET", (
        "süpermarket", "supermarket", "market alışverişi", "market", "gıda", "bakkal",
    )),
    _CatDef("28", "Mücevherat", "JEWELRY", (
        "mücevher", "mucevher", "kolye", "yüzük", "yuzuk", "bilezik", "küpe", "kupe",
        "pırlanta", "pirlanta", "gümüş takı", "takı seti", "alyans",
    )),
)


# Public code -> (display_name, family) index — used by the LLM fallback to
# validate/hydrate a model-returned category code against the real catalog.
CATALOG_BY_CODE: dict[str, tuple[str, str]] = {
    c.code: (c.name, c.family) for c in _CATALOG
}


def _compile_alias(alias: str, *, fold: bool = False) -> re.Pattern[str]:
    a = ascii_fold(alias) if fold else normalize(alias)
    letters = "a-z" if fold else _LETTER
    escaped = re.escape(a).replace(r"\ ", r"\s+")
    # left boundary: not preceded by a letter/digit; allow trailing suffix
    # letters so "telefon" matches "telefonu", "telefonlar", "telefonumu".
    return re.compile(rf"(?<![{letters}0-9])" + escaped + rf"[{letters}]*")


# Pre-compile, ranked most-specific first (more words, then longer alias).
# Each entry carries a Turkish pattern and an ASCII-folded pattern so inputs
# without diacritics ("buzdolabi", "gozluk") still resolve.
_RANKED: list[tuple[re.Pattern[str], re.Pattern[str], _CatDef, int]] = sorted(
    (
        (
            _compile_alias(alias),
            _compile_alias(alias, fold=True),
            cat,
            len(alias.split()) * 100 + len(alias),
        )
        for cat in _CATALOG
        for alias in cat.aliases
    ),
    key=lambda t: t[3],
    reverse=True,
)


_NEGATION_AFTER = re.compile(r"\s*(değil|degil|istemiyorum|olmas[ıi]n|yok\b|de[ğg]il)")


def resolve_categories(text: str, *, limit: int = 4) -> list[CategoryHit]:
    """Return all distinct catalog categories mentioned, most-specific first.

    Deduplicated by category_code; the first (highest-specificity) match is the
    primary need, the rest are secondary needs in a multi-product prompt.
    """
    norm = normalize(text)
    if not norm:
        return []
    norm_ascii = norm.translate(_ASCII_FOLD)
    hits: list[CategoryHit] = []
    seen: set[str] = set()
    for pat, pat_ascii, cat, _rank in _RANKED:
        if cat.code in seen:
            continue
        m = pat.search(norm)
        src = norm
        if m is None:
            m = pat_ascii.search(norm_ascii)
            src = norm_ascii
        if m is not None:
            # Negation guard: "telefon değil tablet" must not resolve to phone.
            if _NEGATION_AFTER.match(src[m.end():m.end() + 14]):
                continue
            seen.add(cat.code)
            hits.append(CategoryHit(
                category_code=cat.code,
                display_name=cat.name,
                family=cat.family,
                confidence=0.92 if " " in cat.aliases[0] else 0.85,
            ))
            if len(hits) >= limit:
                break
    return hits


def resolve_category(text: str) -> Optional[CategoryHit]:
    """Return the single best catalog CategoryHit for free-text, or None.

    Specific/multi-word aliases win over generic ones, so "akıllı saat"
    resolves to Giyilebilir Teknoloji (12), not Saat (18).
    """
    hits = resolve_categories(text, limit=1)
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Budget parsing
# ---------------------------------------------------------------------------
# Fixes the model/spec-number traps: never treat "iphone 15", "s24", "128 gb",
# "playstation 5" as money. A number is budget only when it carries a money
# cue (bin / k / tl / lira / ₺ / bütçe context / milyon) or is the sole
# standalone amount ≥ 1000.

_SPEC_UNIT = r"(?:gb|tb|mb|mp|mah|inç|inc|inch|cc|kw|w|watt|kg|gr|gram|ml|lt|litre|hz|dpi|piksel|mpx|\")"

# spelled-out Turkish magnitudes
_ONES = {
    "bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5,
    "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
}
_TENS = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "kirk": 40, "elli": 50,
    "altmış": 60, "altmis": 60, "yetmiş": 70, "yetmis": 70, "seksen": 80, "doksan": 90,
}


def _spelled_out_thousands(norm: str) -> Optional[float]:
    """Parse "kırk bin", "otuz beş bin", "yüz bin", "iki yüz bin" (spelled thousands).

    Millions are handled separately in parse_budget (compound-aware).
    """
    # <...> bin  (hundreds + tens + ones). Leading lookbehind prevents matching
    # "on" inside words like "telefon"; trailing suffix allows "bine"/"binlik".
    m = re.search(
        r"(?<![a-zçğıöşü0-9])"
        r"((?:(?:bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz)\s+)?yüz\s+)?"
        r"(on|yirmi|otuz|kırk|kirk|elli|altmış|altmis|yetmiş|yetmis|seksen|doksan)?\s*"
        r"(bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz)?\s*bin[a-zçğıöşü]*",
        norm,
    )
    if not m or not m.group(0).strip():
        return None
    hundreds = 0
    if m.group(1):
        hm = re.match(r"(bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz)?\s*yüz", m.group(1))
        mult = _ONES.get(hm.group(1), 1) if hm and hm.group(1) else 1
        hundreds = mult * 100
    tens = _TENS.get(m.group(2), 0) if m.group(2) else 0
    ones = _ONES.get(m.group(3), 0) if m.group(3) else 0
    total = hundreds + tens + ones
    if total == 0:
        # bare "bin" with a leading digit handled elsewhere; ignore standalone "bin"
        return None
    return float(total * 1000)


def _to_number(raw: str) -> Optional[float]:
    """"40.000" -> 40000, "40,5" -> 40.5, "40000" -> 40000."""
    s = raw.strip()
    # thousands separators: dot or comma followed by exactly 3 digits (repeated)
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        return float(re.sub(r"[.,]", "", s))
    # decimal comma (e.g. 40,5)
    if re.fullmatch(r"\d+,\d{1,2}", s):
        return float(s.replace(",", "."))
    s = s.replace(",", "").replace(".", "")
    return float(s) if s.isdigit() else None


def parse_budget(text: str) -> Optional[float]:
    """Extract the intended budget (upper bound) in TL, or None.

    Robust against model/spec numbers. Returns the largest cued candidate,
    so "30-40 bin" -> 40000 (ceiling) and "iphone 15 ... 40 bin" -> 40000.
    """
    norm = normalize(text)
    if not norm:
        return None

    candidates: list[float] = []

    # 1) spelled-out ("kırk bin", "yüz bin", "bir milyon")
    spelled = _spelled_out_thousands(norm)
    if spelled:
        candidates.append(spelled)

    # 2a) digit + "bin" (+ suffix: "40 bin", "40bin", "30 bine", "40 binlik")
    for m in re.finditer(r"(\d[\d.,]*)\s*bin[a-zçğıöşü]*", norm):
        val = _to_number(m.group(1))
        if val is not None:
            candidates.append(val * 1000)
    # 2b) digit + "k" (strict: "40k" yes, "40 kadar" no)
    for m in re.finditer(r"(\d[\d.,]*)\s*k(?![a-zçğıöşü])", norm):
        val = _to_number(m.group(1))
        if val is not None:
            candidates.append(val * 1000)

    # 3) milyon — compound aware ("2 milyon 500 bin" = 2.5M, "yarım milyon" = 0.5M)
    for m in re.finditer(
        r"(?<![a-zçğıöşü0-9])"
        r"(\d+(?:[.,]\d+)?|yar[ıi]m|bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz|on)"
        r"\s*milyon(?:\s*(\d[\d.,]*)\s*bin[a-zçğıöşü]*)?",
        norm,
    ):
        tok = m.group(1)
        base = {"yarım": 0.5, "yarim": 0.5}.get(tok)
        if base is None:
            base = _ONES.get(tok)
        if base is None:
            base = _to_number(tok) or 0
        total = base * 1_000_000
        if m.group(2):
            sub = _to_number(m.group(2))
            if sub is not None:
                total += sub * 1000
        candidates.append(total)

    # 4) digit + currency ("40000 tl", "3.500 lira", "₺25000")
    for m in re.finditer(r"(\d[\d.,]*)\s*(?:tl|lira|try|₺)\b", norm):
        val = _to_number(m.group(1))
        if val is not None:
            candidates.append(val)
    for m in re.finditer(r"₺\s*(\d[\d.,]*)", norm):
        val = _to_number(m.group(1))
        if val is not None:
            candidates.append(val)

    # 5) number in explicit budget context ("bütçem 40000", "bütçe 25 bin kadar")
    for m in re.finditer(
        r"b[üu]t[çc]e\w*\s*(?:yakla[şs][ıi]k\s*)?(\d[\d.,]*)\s*(bin|k)?", norm
    ):
        val = _to_number(m.group(1))
        if val is None:
            continue
        candidates.append(val * 1000 if m.group(2) else val)

    if candidates:
        return max(candidates)

    # 6) Fallback: a lone standalone amount >= 1000, not a spec/model number.
    standalone: list[float] = []
    for m in re.finditer(rf"(?<![{_LETTER}0-9])(\d[\d.,]*)(?![{_LETTER}0-9])", norm):
        tail = norm[m.end():m.end() + 6].lstrip()
        if re.match(_SPEC_UNIT, tail):  # "128 gb", "6 inç"
            continue
        val = _to_number(m.group(1))
        if val is not None and val >= 1000:
            standalone.append(val)
    if standalone:
        return max(standalone)
    return None


# ---------------------------------------------------------------------------
# Combined need extraction
# ---------------------------------------------------------------------------


@dataclass
class NeedExtraction:
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    family: Optional[str] = None
    budget_value: Optional[float] = None
    has_product_noun: bool = False
    diagnostics: dict = field(default_factory=dict)


def extract_need(text: str) -> NeedExtraction:
    hit = resolve_category(text)
    budget = parse_budget(text)
    return NeedExtraction(
        category_code=hit.category_code if hit else None,
        category_name=hit.display_name if hit else None,
        family=hit.family if hit else None,
        budget_value=budget,
        has_product_noun=hit is not None,
        diagnostics={
            "resolver": "deterministic_lexicon_v1",
            "matched_family": hit.family if hit else None,
        },
    )


# ---------------------------------------------------------------------------
# Rich profile for long / multi-constraint prompts
# ---------------------------------------------------------------------------
# A long free-text prompt can carry many constraints at once, e.g.
#   "eşime doğum günü için telefon alıcam bütçem 40 bin civarı ama peşinatı
#    düşük olsun, uzun vade istiyorum, albaraka olmasın, yeni müşteriyim"
# We extract ALL of them deterministically so the fast path can honor
# preferences on the very first turn — no LLM round-trip.

_BANKS = {
    "albaraka": "albaraka",
    "kuveyt": "kuveyt",
    "kuveyt türk": "kuveyt",
    "getirfinans": "getirfinans",
    "getir finans": "getirfinans",
}

_PREF_CHEAPER = re.compile(
    r"\b(en\s+ucuz|daha\s+ucuz|ucuz|en\s+uygun|daha\s+uygun|"
    r"d[üu][şs][üu]k\s+(?:faiz|oran|kar|maliyet)|faizsiz|%\s*0)\b"
)
_PREF_LONGER = re.compile(r"\b(en\s+uzun\s+vade|uzun\s+vade|vadeyi\s+uzat|uzun\s+taksit)\b")
_PREF_SHORTER = re.compile(r"\b(k[ıi]sa\s+vade|az\s+taksit|tek\s+[çc]ekim|pe[şs]in)\b")
_PESINAT_LOW = re.compile(
    r"pe[şs]inats[ıi]z|"
    r"pe[şs]inat\w*\s+(?:yok|olmas[ıi]n|d[üu][şs][üu]k|az)|"
    r"(?:d[üu][şs][üu]k|az)\s+pe[şs]inat"
)
_NEW_CUSTOMER = re.compile(r"yeni\s+m[üu][şs]teri|ilk\s+defa|hi[çc]\s+kullanmad")


def _extract_tenure_months(norm: str) -> Optional[int]:
    """Highest explicit '<n> ay' / '<n> taksit' up to 60 months."""
    vals = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*(?:ay|taksit)\b", norm)]
    vals = [v for v in vals if 1 <= v <= 60]
    return max(vals) if vals else None


def _extract_banks(norm: str) -> tuple[list[str], list[str]]:
    """Return (include, exclude) bank slugs from phrases like 'albaraka olsun',
    'kuveyt olmasın', 'getirfinans hariç', 'sadece albaraka'."""
    include: list[str] = []
    exclude: list[str] = []
    for phrase, slug in _BANKS.items():
        for m in re.finditer(re.escape(phrase), norm):
            window = norm[m.end():m.end() + 18]
            if re.search(r"\b(olmas[ıi]n|hari[çc]|istemiyorum|d[ıi][şs][ıi]nda)\b", window):
                exclude.append(slug)
            elif re.search(r"\b(olsun|istiyorum|tercih|sadece|sadece\s+)\b", window) or \
                    re.search(r"\bsadece\s+" + re.escape(phrase), norm):
                include.append(slug)
    return list(dict.fromkeys(include)), list(dict.fromkeys(exclude))


@dataclass
class NeedProfile:
    primary_code: Optional[str] = None
    primary_name: Optional[str] = None
    primary_family: Optional[str] = None
    secondary: list[CategoryHit] = field(default_factory=list)
    budget_value: Optional[float] = None
    budget_type: str = "NONE"          # NONE | APPROXIMATE | MAXIMUM | RANGE
    tenure_months: Optional[int] = None
    prefer_cheaper: bool = False
    prefer_longer: bool = False
    prefer_shorter: bool = False
    low_downpayment: bool = False
    new_customer: bool = False
    include_banks: list[str] = field(default_factory=list)
    exclude_banks: list[str] = field(default_factory=list)
    is_complex: bool = False
    diagnostics: dict = field(default_factory=dict)


def _budget_type(norm: str, value: Optional[float]) -> str:
    if value is None:
        return "NONE"
    if re.search(r"[-–—]\s*\d|\barası\b", norm):
        return "RANGE"
    if re.search(r"\b(en\s+fazla|maks|maksimum|ge[çc]me|a[şs]mas[ıi]n|kadar|alt[ıi]nda)\b", norm):
        return "MAXIMUM"
    if re.search(r"\b(civar[ıi]|yakla[şs][ıi]k|falan|filan|gibi|dolaylar[ıi])\b", norm):
        return "APPROXIMATE"
    return "APPROXIMATE"


def extract_profile(text: str) -> NeedProfile:
    """Full deterministic extraction for long / multi-constraint prompts."""
    norm = normalize(text)
    cats = resolve_categories(text, limit=4)
    budget = parse_budget(text)
    include, exclude = _extract_banks(norm)
    primary = cats[0] if cats else None
    constraints = [
        bool(_PREF_CHEAPER.search(norm)),
        bool(_PREF_LONGER.search(norm)),
        bool(_extract_tenure_months(norm)),
        bool(include or exclude),
        bool(_PESINAT_LOW.search(norm)),
        len(cats) > 1,
    ]
    profile = NeedProfile(
        primary_code=primary.category_code if primary else None,
        primary_name=primary.display_name if primary else None,
        primary_family=primary.family if primary else None,
        secondary=cats[1:],
        budget_value=budget,
        budget_type=_budget_type(norm, budget),
        tenure_months=_extract_tenure_months(norm),
        prefer_cheaper=bool(_PREF_CHEAPER.search(norm)),
        prefer_longer=bool(_PREF_LONGER.search(norm)),
        prefer_shorter=bool(_PREF_SHORTER.search(norm)),
        low_downpayment=bool(_PESINAT_LOW.search(norm)),
        new_customer=bool(_NEW_CUSTOMER.search(norm)),
        include_banks=include,
        exclude_banks=exclude,
        is_complex=sum(constraints) >= 2 or len(norm) > 120,
        diagnostics={"resolver": "deterministic_profile_v1", "n_categories": len(cats)},
    )
    return profile
