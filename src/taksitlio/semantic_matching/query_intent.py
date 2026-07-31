"""Utterance-level intent / safety signals for the decision policy.

Content-blind heuristics: no category ID / fixture key / catalog code.
These cues only decide whether auto-select is *safe* — they never pick a
category. ADR-007 §9: weak lexical or bare alias must not force MATCHED
when the utterance is non-purchase, out-of-scope, or a choice question.
"""

from __future__ import annotations

from enum import Enum
import re

from taksitlio.semantic_matching.turkish_normalize import turkish_lower


class QueryIntentKind(str, Enum):
    PRODUCT_PURCHASE = "PRODUCT_PURCHASE"
    NON_PURCHASE = "NON_PURCHASE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    CHOICE = "CHOICE"
    UNKNOWN = "UNKNOWN"


# Active shopping / replacement verbs (must be checked before bare "lazım").
_ACTIVE_PURCHASE_CUES = (
    "almak istiyorum",
    "almak isterim",
    "alıyorum",
    "alacağım",
    "alacagim",
    "arıyorum",
    "ariyorum",
    "bakıyorum",
    "bakiyorum",
    "öner",
    "oner",
    "tavsiye",
    "satın al",
    "satin al",
    "fiyat",
    "taksit",
    "bütçe",
    "butce",
    # Replacement after negation ("konsol lazım değil bilgisayar olsun").
    " olsun",
    " yeter",
)

# Weaker need cues — still purchase when not negated.
_WEAK_PURCHASE_CUES = (
    "almak",
    "lazım",
    "lazim",
    "gerek",
    "ihtiyac",
    "ihtiyaç",
    "kaç para",
    "kac para",
    "ne kadar",
)

# Info / complaint / service — non-purchase when no active shopping verb.
_NON_PURCHASE_CUES = (
    "hakkında bilgi",
    "hakkinda bilgi",
    "ne dersin",
    "şikayet",
    "sikayet",
    "şikayetçiyim",
    "sikayetciyim",
    "arıza",
    "ariza",
    "teknik servis",
    "bakımı",
    "bakimi",
    "boyama",
    "oturuyorum",
    "lisansı",
    "lisansi",
    "lisans",
    "programına",
    "programina",
    "yolları",
    "yollari",
    "yapılır mı",
    "yapilir mi",
    "yapılır mi",
    "nasıl yapılır",
    "nasil yapilir",
    "kablosu",
    "tamir",
    "pahalıya patlar",
    "pahaliya patlar",
    "patlar dedi",
)

# Full-utterance purchase refusal (no replacement product).
_PURCHASE_REFUSAL_CUES = (
    "lazım değil",
    "lazim degil",
    "lazım degil",
    "lazim değil",
    "gerek yok",
    "yenisi lazım değil",
    "yenisi lazim degil",
    "almak istemiyorum",
)

_OUT_OF_SCOPE_CUES = (
    "uçak bileti",
    "ucak bileti",
    "otel rezervasyon",
    "otel rezervasyonu",
    "seyahat",
    "tatil paketi",
    "tur paketi",
    "turu paketi",
    "kapadokya",
    "otel mi",
)

# Chitchat / open-world asks — never enter product assist or LLM answer prose.
_GENERAL_CHAT_CUES = (
    "merhaba",
    "selam",
    "nasılsın",
    "nasilsin",
    "iyi misin",
    "günaydın",
    "gunaydin",
    "iyi akşamlar",
    "iyi aksamlar",
    "ne haber",
    "naber",
    "sohbet edelim",
    "konuşalım",
    "konusalim",
    "genel sohbet",
    "kimsin",
    "sen kimsin",
    "hava durumu",
    "hava nasıl",
    "hava nasil",
    "fıkra",
    "fikra",
    "şaka yap",
    "saka yap",
    "şiir yaz",
    "siir yaz",
    "hikaye anlat",
    "ödev yap",
    "odev yap",
    "ödevimi",
    "odevimi",
    "çeviri yap",
    "ceviri yap",
    "ingilizceye çevir",
    "ingilizceye cevir",
    "şarkı sözü",
    "sarki sozu",
    "futbol maç",
    "futbol mac",
    "siyaset",
    "borsa tavsiye",
    "kripto",
    "bitcoin",
    "chatgpt",
    "chat gpt",
)

# Lexical product-domain anchors when verbs are missing ("iphone 15").
_PRODUCT_DOMAIN_NOUNS = (
    "telefon",
    "laptop",
    "bilgisayar",
    "notebook",
    "tablet",
    "televizyon",
    " tv",
    "buzdolab",
    "çamaşır",
    "camasir",
    "bulaşık",
    "bulasik",
    "klima",
    "kulaklık",
    "kulaklik",
    "airpods",
    "iphone",
    "samsung",
    "macbook",
    "ipad",
    "konsol",
    "playstation",
    "xbox",
    "ürün",
    "urun",
    "taksit",
    "kampanya",
    "fiyat",
    "bütçe",
    "butce",
    "aylık",
    "aylik",
)

_CHOICE_RE = re.compile(
    r"\b\w+\s+m[ıiuü]\b.+\bm[ıiuü]\b|\byoksa\b",
    re.IGNORECASE,
)

OUT_OF_SCOPE_ASSIST_MESSAGE = (
    "Bu konuda yardımcı olamıyorum. Yalnızca Taksitlio katalogundaki ürün ve "
    "taksit ihtiyaçlarınız için buradayım; sistemde olmayan bilgi veremem ve "
    "genel sohbet yapmam."
)


def _fold(text: str) -> str:
    return " " + turkish_lower(text or "") + " "


def _has_any(folded: str, cues: tuple[str, ...]) -> bool:
    return any(cue in folded for cue in cues)


def classify_query_intent(text: str) -> QueryIntentKind:
    """Classify whether auto-select is safe for this utterance."""

    folded = _fold(text)
    if not folded.strip():
        return QueryIntentKind.UNKNOWN

    if _has_any(folded, _OUT_OF_SCOPE_CUES):
        return QueryIntentKind.OUT_OF_SCOPE

    active = _has_any(folded, _ACTIVE_PURCHASE_CUES)
    weak = _has_any(folded, _WEAK_PURCHASE_CUES)

    # General chat without an explicit shopping ask → out of scope.
    if _has_any(folded, _GENERAL_CHAT_CUES) and not active and not weak:
        return QueryIntentKind.OUT_OF_SCOPE

    if _CHOICE_RE.search(folded) or (
        folded.count(" mi ") + folded.count(" mı ") + folded.count(" mu ")
        + folded.count(" mü ")
        >= 2
    ):
        return QueryIntentKind.CHOICE

    non_purchase = _has_any(folded, _NON_PURCHASE_CUES)
    refusal = _has_any(folded, _PURCHASE_REFUSAL_CUES)

    # "X istemiyorum Y arıyorum" is still a purchase (negation + replacement).
    # Only refuse when there is no active replacement shopping verb.
    if refusal and not active:
        return QueryIntentKind.NON_PURCHASE

    if non_purchase and not active and not weak:
        return QueryIntentKind.NON_PURCHASE

    if active or weak:
        return QueryIntentKind.PRODUCT_PURCHASE

    return QueryIntentKind.UNKNOWN


def is_off_domain_for_assist(text: str) -> bool:
    """True when the assistant must refuse (no catalog facts, no general chat)."""

    kind = classify_query_intent(text)
    if kind is QueryIntentKind.OUT_OF_SCOPE:
        return True
    if kind is QueryIntentKind.NON_PURCHASE:
        return True
    if kind in {QueryIntentKind.PRODUCT_PURCHASE, QueryIntentKind.CHOICE}:
        return False
    # UNKNOWN: allow only clear product-domain nouns (e.g. bare model names).
    return not _has_any(_fold(text), _PRODUCT_DOMAIN_NOUNS)


def is_choice_question(text: str) -> bool:
    return classify_query_intent(text) is QueryIntentKind.CHOICE


def blocks_auto_select(text: str) -> bool:
    """True when MATCHED auto-select is unsafe for this utterance."""

    kind = classify_query_intent(text)
    return kind in {
        QueryIntentKind.NON_PURCHASE,
        QueryIntentKind.OUT_OF_SCOPE,
        QueryIntentKind.CHOICE,
    }


__all__ = [
    "OUT_OF_SCOPE_ASSIST_MESSAGE",
    "QueryIntentKind",
    "blocks_auto_select",
    "classify_query_intent",
    "is_choice_question",
    "is_off_domain_for_assist",
]
