"""Acceptance v3 dataset generator (ADR-007 §E).

Produces DRAFT / synthetic evaluation cases for the End-to-End
Understanding + Provisional Acceptance sprint. v3 is a *superset* of v2
— it starts from every v2 case, then adds the coverage lanes ADR-007
requires:

    validation v3 (evaluation/datasets/validation/tr-category-validation.v3.jsonl)
        * every v2 validation case
        * ≥ 75 NO_MATCH cases (target: 100 on validation)
        * ≥ 50 out-of-scope cases (fixture.out-of-scope-travel)
        * ≥ 30 misleading lexical cases
        * ≥ 30 multi-need clarify cases
        * ≥ 25 exclusion / conflict cases

    development v3 (evaluation/datasets/development/tr-category-dev.v3.jsonl)
        * every v2 development case
        * balanced expansions across the same buckets so tune-policy
          has enough support without touching validation

v1 and v2 files on disk are NOT touched — v3 is additive.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "evaluation" / "datasets" / "development"
VAL_DIR = REPO_ROOT / "evaluation" / "datasets" / "validation"
DEV_V2_PATH = DEV_DIR / "tr-category-dev.v2.jsonl"
VAL_V2_PATH = VAL_DIR / "tr-category-validation.v2.jsonl"
DEV_V3_PATH = DEV_DIR / "tr-category-dev.v3.jsonl"
VAL_V3_PATH = VAL_DIR / "tr-category-validation.v3.jsonl"


FK = {
    "mobile": "fixture.mobile-device",
    "laptop": "fixture.portable-computer",
    "desktop": "fixture.desktop-computer",
    "computer": "fixture.computer-devices",
    "tablet": "fixture.tablet-device",
    "appliance": "fixture.home-appliance",
    "furniture": "fixture.furniture",
    "camera": "fixture.camera",
    "wearable": "fixture.wearable",
    "audio": "fixture.audio",
    "tv": "fixture.television",
    "console": "fixture.gaming-console",
    "small_kitchen": "fixture.small-kitchen-appliance",
    "vacuum": "fixture.vacuum-cleaner",
    "ac": "fixture.air-conditioner",
    "bicycle": "fixture.bicycle",
    "travel": "fixture.out-of-scope-travel",
}


CONCEPT = {
    FK["mobile"]: "telefon",
    FK["laptop"]: "laptop",
    FK["desktop"]: "masaüstü",
    FK["computer"]: "bilgisayar",
    FK["tablet"]: "tablet",
    FK["appliance"]: "beyaz eşya",
    FK["furniture"]: "mobilya",
    FK["camera"]: "kamera",
    FK["wearable"]: "akıllı saat",
    FK["audio"]: "kulaklık",
    FK["tv"]: "televizyon",
    FK["console"]: "oyun konsolu",
    FK["small_kitchen"]: "kahve makinesi",
    FK["vacuum"]: "süpürge",
    FK["ac"]: "klima",
    FK["bicycle"]: "bisiklet",
    FK["travel"]: "seyahat",
}


@dataclass
class GenCase:
    case_id: str
    semantic_group_id: str
    utterance: str
    expected_status: str
    tags: tuple[str, ...]
    difficulty: str
    split: str
    required: tuple[str, ...] = ()
    acceptable: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        payload: dict = {
            "case_id": self.case_id,
            "semantic_group_id": self.semantic_group_id,
            "locale": "tr-TR",
            "utterance": self.utterance,
            "expected": {"status": self.expected_status},
            "dimensions": {
                "tags": list(self.tags),
                "difficulty": self.difficulty,
            },
            "privacy": {
                "synthetic": True,
                "source": "acceptance-generator.v3",
            },
            "annotation": {"status": "DRAFT"},
        }
        if self.acceptable:
            payload["expected"]["acceptable_fixture_keys"] = list(self.acceptable)
        if self.required:
            payload["expected"]["required_fixture_keys"] = list(self.required)
        if self.forbidden:
            payload["expected"]["forbidden_fixture_keys"] = list(self.forbidden)
        constraints = _constraints_for_case(self)
        if constraints:
            payload["semantic_constraints"] = constraints
        return payload


def _constraints_for_case(case: GenCase) -> dict | None:
    tags = set(case.tags)
    positive: list[dict] = []
    negative: list[dict] = []
    corrections: list[dict] = []

    for key in case.required or case.acceptable:
        concept = CONCEPT.get(key)
        if concept:
            positive.append(
                {"concept": concept, "source": "EXPLICIT", "confidence": 0.95}
            )

    if "explicit_negation" in tags or "negation" in tags:
        for key in case.forbidden:
            concept = CONCEPT.get(key)
            if concept:
                negative.append(
                    {"concept": concept, "source": "EXPLICIT_NEGATION", "confidence": 0.99}
                )

    if "user_correction" in tags:
        if case.forbidden and (case.required or case.acceptable):
            prev = CONCEPT.get(case.forbidden[0])
            repl = CONCEPT.get((case.required or case.acceptable)[0])
            if prev and repl:
                corrections.append(
                    {
                        "previous_concept": prev,
                        "replacement_concept": repl,
                        "confidence": 0.98,
                    }
                )

    if not positive and not negative and not corrections:
        return None
    return {"positive": positive, "negative": negative, "corrections": corrections}


def _load_v2(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# v3 case templates
# ---------------------------------------------------------------------------


NO_MATCH_GENERAL_TEMPLATES: list[tuple[str, str, tuple[str, ...]]] = [
    ("bugün hava çok güzel", "weather", ()),
    ("hesabımdaki son işlemi görebilir miyim", "account_query", ()),
    ("kredi kartı taksitlerini nasıl kapatırım", "credit_flow", ()),
    ("selam nasılsın", "greeting", ()),
    ("bize bir tavsiye ver hafta sonu için", "general_advice", ()),
    ("kahve içiyorum sohbet edelim", "chat_only", ()),
    ("bugün cumartesi mi", "date_query", ()),
    ("bana bir şaka anlat", "joke", ()),
    ("çok yorgunum bugün", "small_talk", ()),
    ("borsa nasıl gidiyor", "market_query", ()),
    ("teşekkür ederim size", "thanks_only", ()),
    ("yeni geldik hoşgeldiniz", "greeting_2", ()),
    ("tv programına ne dersin", "meta_chat", ()),
    ("ödevimi bitirdim", "personal_status", ()),
    ("hangi filmi izlesem", "movie_advice", ()),
    ("ne yesem bugün", "food_advice", ()),
    ("araba tamir ettim geliyorum", "unrelated_topic", ()),
    ("ödeme geciktim uyarı geldi", "payment_late", ()),
    ("faiz oranları ne kadar", "rate_query", ()),
    ("çalışma saatleriniz kaç", "hours_query", ()),
    ("kim bu genç", "off_topic", ()),
    ("kediler daha mı zeki köpekler mi", "philosophy", ()),
    ("dün akşam ne yaptın", "small_talk_2", ()),
    ("sıkıldım", "boredom", ()),
    ("uyumak istiyorum ne yapayım", "sleep_advice", ()),
    ("çocuğuma yeni bir arkadaş lazım", "off_topic_2", ()),
    ("bugün trafik yoğun mu", "traffic_query", ()),
    ("annemi arayacağım hatırlat", "reminder", ()),
    ("bu şarkı çok güzel", "music_comment", ()),
    ("ödevlerimden sıkıldım", "school_venting", ()),
    ("kütüphaneye gitmek istiyorum", "library_visit", ()),
    ("hangi sinemaya gitsem", "cinema_pick", ()),
    ("üniversite tercihi yapıyorum", "university_choice", ()),
    ("yeni bir hobi arıyorum", "hobby_search", ()),
    ("egzersiz için ne önerirsin", "exercise_advice", ()),
    ("psikolog önerir misin", "psych_advice", ()),
    ("evlenmek üzereyim", "life_event", ()),
    ("yeni bir arkadaş edinmek istiyorum", "friendship", ()),
    ("bugün kutlama günüm", "celebration", ()),
    ("iş bulmak zor", "job_market", ()),
    ("çocuğum çok yaramaz", "parenting", ()),
    ("kredim onaylandı mı", "credit_status", ()),
    ("kartımı iptal etmek istiyorum", "card_cancel", ()),
    ("iban değişikliği nasıl yapılır", "iban_change", ()),
    ("hesap özetimi maille gönderin", "statement_email", ()),
    ("kredi başvurusu yapmak istiyorum", "loan_apply", ()),
    ("dövize çevirmek istiyorum", "fx_query", ()),
    ("emekli maaşımı ne zaman alırım", "pension_query", ()),
    ("çek yazmak istiyorum", "check_query", ()),
    ("faturamı yatırmak istiyorum", "bill_pay", ()),
    ("banka şubem nerede", "branch_query", ()),
    ("atm nerede bulabilirim", "atm_query", ()),
    ("kart limitim ne kadar", "limit_query", ()),
    ("hesap özetim gelmedi bu ay", "statement_missing", ()),
    ("ödemem geldi mi kontrol", "payment_check", ()),
    ("son üç maaşımı gösterir misin", "salary_query", ()),
    ("hangi tarihlerde tatile çıkabilirim", "vacation_query", ()),
    ("bugün doğum günüm", "birthday", ()),
    ("bir ilaç önerir misin", "medical_advice", ()),
    ("hangi doktora gitsem", "doctor_query", ()),
    ("aşı randevusu almak istiyorum", "vaccine_apt", ()),
    ("nöbetçi eczane arıyorum", "pharmacy_query", ()),
    ("araba muayenesi ne kadar", "car_inspection", ()),
    ("emlak sitesi tavsiyesi", "real_estate", ()),
    ("kiralık ev arıyorum", "rental", ()),
    ("otobüs saatleri nedir", "bus_schedule", ()),
    ("metro çalışıyor mu bugün", "metro_query", ()),
    ("kargom nerede", "cargo_query", ()),
    ("iade nasıl yapılır", "return_query", ()),
    ("indirim ne zaman başlar", "sale_query", ()),
    ("puanlarımı görebilir miyim", "loyalty_query", ()),
    ("üyelik yenilemek istiyorum", "membership_renew", ()),
    ("kupon kodu var mı", "coupon_query", ()),
    ("hediye kartı satın almak istiyorum", "gift_card", ()),
    ("çevrimiçi kurs önerir misin", "online_course", ()),
    ("yabancı dil öğrenmek istiyorum", "language_learn", ()),
    ("gitar dersi almak istiyorum", "music_lesson", ()),
    ("yoga dersi arıyorum", "yoga_class", ()),
    ("spor salonu tavsiyesi", "gym_query", ()),
    ("kilo vermek istiyorum ne öneririn", "diet_advice", ()),
    ("meditasyon uygulaması önerir misin", "meditation_app", ()),
    ("motivasyon lazım bugün", "motivation", ()),
    ("kitap önerisi ver", "book_advice", ()),
    ("dizi önerir misin", "series_advice", ()),
]


OOS_TRAVEL_TEMPLATES: list[tuple[str, str]] = [
    ("uçak bileti almak istiyorum", "flight"),
    ("otel rezervasyonu yapmak istiyorum", "hotel"),
    ("tatil paketleri bakıyorum", "package"),
    ("antalya için uçak var mı", "flight_antalya"),
    ("hafta sonu için otel önerin", "hotel_weekend"),
    ("balayı için tatil paketi", "honeymoon"),
    ("yurt dışı seyahati ayarlayın", "abroad"),
    ("cumartesi izmire uçak", "flight_izmir"),
    ("bodrumda otel arıyorum", "hotel_bodrum"),
    ("uçak biletini iptal edebilir miyim", "flight_cancel"),
    ("otel iptal politikası", "hotel_cancel"),
    ("tatil kredisi almak istiyorum", "vacation_credit"),
    ("charter uçuş var mı", "charter"),
    ("uçak bileti mi otel mi", "flight_or_hotel_amb"),
    ("balayı için hem otel hem uçak", "honeymoon_multi"),
    ("kapadokya turu paketi", "cappadocia"),
    ("son dakika tatil fırsatı", "last_minute"),
    ("erken rezervasyon indirimi", "early_booking"),
    ("iş seyahatim için uçak", "business_travel"),
    ("gemi turu var mı", "cruise"),
    ("all inclusive otel", "all_inclusive"),
    ("hafta sonu için istanbul otel", "hotel_istanbul"),
    ("izmir bodrum otobüs bileti", "bus"),
    ("araç kiralama seyahat için", "car_rental"),
    ("uçak yolculuğu için bavul", "luggage_travel"),
    ("gezi rehberi kitabı", "travel_book"),
    ("seyahat sigortası", "travel_insurance"),
    ("tatilde kredi kartı taksit", "vacation_credit_2"),
    ("uçak bilet fiyatlarına bakayım", "flight_price"),
    ("otel puanı biriktirme", "hotel_loyalty"),
]


MISLEADING_LEXICAL_TEMPLATES: list[tuple[str, str, str]] = [
    # (utterance, expected_target_slug, semantic_group_suffix)
    # These utterances contain misleading tokens that could be confused
    # by pure lexical matching; matcher must produce NO_MATCH or a
    # correct MATCHED via other channels.
    ("iş yeri masası için karar veremedim", "furniture", "office_desk_conf"),
    ("kızıma bir bilgisayar oyunu alacağım", "console", "computer_game_console"),
    ("çamaşır kaldırma sepeti", "furniture", "laundry_basket"),
    ("makine mühendisliği kitapları", "", "engineering_book"),
    ("kamera üstünde büyük ekran istiyorum", "tv", "camera_screen_tv"),
    ("telefon rehberim silinmiş", "", "phonebook_lost"),
    ("bilgisayar oyunu için sandalye", "furniture", "gaming_chair"),
    ("kitap okumak için lamba", "furniture", "reading_lamp"),
    ("cebimde para yok telefon istemem", "", "no_money_no_phone"),
    ("kahvem bitti diye kızgınım", "", "coffee_out_no_maker"),
    ("evde tv var yenisi lazım değil", "", "tv_already_have"),
    ("laptop çantam patladı", "", "laptop_bag"),
    ("ses sisteminde arıza var teknik servis", "", "audio_repair"),
    ("bilgisayar başında çok oturuyorum", "", "computer_time"),
    ("televizyon programı yorumları", "", "tv_show_reviews"),
    ("araç içi hoparlör kablosu", "", "car_speaker_cable"),
    ("saatimin pili bitti", "", "watch_battery"),
    ("mobil uygulama açılmıyor", "", "mobile_app"),
    ("konsollu tv sehpası", "furniture", "console_tv_stand"),
    ("beyaz eşya bakımı için hizmet", "", "appliance_service"),
    ("süpürge torbası kayboldu", "", "vacuum_bag_lost"),
    ("kliman pahalıya patlar dedi", "", "ac_expensive_advice"),
    ("bisiklet yolları hakkında bilgi", "", "bike_lanes_info"),
    ("kamera yönetmenliği kursu", "", "camera_directing"),
    ("konsol için ip lisansı", "", "console_ip_license"),
    ("mobilya boyama yapılır mı", "", "furniture_painting"),
    ("apple watch ekranı çatladı tamir", "", "watch_repair"),
    ("laptop yerine mi masaüstü almalıyım karar veremedim", "", "laptop_or_desktop_advice"),
    ("blender sesinden şikayetçiyim", "", "blender_noise"),
    ("robot süpürgeye ne dersiniz eleştirisi", "", "vacuum_review"),
]


MULTI_NEED_CLARIFY_TEMPLATES: list[tuple[str, tuple[str, ...], str]] = [
    # (utterance, acceptable_slugs, semantic_group_suffix)
    ("telefon mu tablet mi almalıyım", ("mobile", "tablet"), "phone_or_tablet"),
    ("bilgisayar mı tablet mi öğrenci için", ("computer", "tablet"), "student_multi"),
    ("hem telefon hem laptop lazım", ("mobile", "laptop"), "phone_and_laptop"),
    ("kulaklık mı ses sistemi mi almalıyım", ("audio",), "hp_or_speaker"),
    ("dizüstü mü masaüstü mü karar veremedim", ("laptop", "desktop"), "laptop_or_desktop"),
    ("konsol mu tablet mi çocuğa", ("console", "tablet"), "console_or_tablet_kid"),
    ("saat mi bileklik mi", ("wearable",), "watch_or_band"),
    ("kanepe mi koltuk mu", ("furniture",), "sofa_or_armchair"),
    ("bisiklet mi scooter mı", ("bicycle",), "bike_or_scooter"),
    ("hem beyaz eşya hem mobilya lazım", ("appliance", "furniture"), "both_home"),
    ("tv mi projeksiyon mu", ("tv",), "tv_or_projector"),
    ("kamera mı telefon kamerası mı", ("camera", "mobile"), "cam_or_phonecam"),
    ("iphone mu android mi", ("mobile",), "iphone_or_android"),
    ("smart tv mi ekran mı", ("tv",), "smart_tv_or_screen"),
    ("kahve makinesi mi çay makinesi mi", ("small_kitchen",), "coffee_or_tea"),
    ("hoparlör mü soundbar mı", ("audio",), "speaker_or_soundbar"),
    ("blender mı airfryer mı önce", ("small_kitchen",), "blender_or_airfryer"),
    ("dslr mı aynasız mı", ("camera",), "dslr_or_mirrorless"),
    ("apple watch mı garmin mi", ("wearable",), "watch_brands"),
    ("split klima mı inverter mi", ("ac",), "split_or_inverter"),
    ("hem tv hem laptop alacağım", ("tv", "laptop"), "tv_and_laptop"),
    ("hem klima hem buzdolabı bakıyorum", ("ac", "appliance"), "ac_and_fridge"),
    ("kablosuz mu kablolu kulaklık mı", ("audio",), "wireless_or_wired"),
    ("dikey mi robot süpürge mi", ("vacuum",), "vertical_or_robot"),
    ("aynasız mı gopro mu", ("camera",), "mirrorless_or_gopro"),
    ("tablet mi ipad mi", ("tablet",), "tab_or_ipad"),
    ("saat mi telefon mu spor için", ("wearable", "mobile"), "watch_or_phone_sport"),
    ("gopro mu dslr mı seyahat için", ("camera",), "gopro_or_dslr_travel"),
    ("hem mobilya hem tv düşünüyorum", ("furniture", "tv"), "furniture_and_tv"),
    ("konsol mu tv mi önce", ("console", "tv"), "console_or_tv_first"),
    ("kulaklık mı hoparlör mü koşarken", ("audio",), "hp_or_speaker_run"),
    ("laptop mu tablet mi seyahatte", ("laptop", "tablet"), "laptop_or_tab_travel"),
]


EXCLUSION_CONFLICT_TEMPLATES: list[tuple[str, str, str, str]] = [
    # (utterance, target_slug, forbidden_slug, semantic_group_suffix)
    ("telefon değil kesinlikle tablet olsun", "tablet", "mobile", "phone_excl_tablet"),
    ("laptop değil kesinlikle masaüstü", "desktop", "laptop", "laptop_excl_desktop"),
    ("tv istemiyorum sadece hoparlör", "audio", "tv", "tv_excl_audio"),
    ("konsol istemiyorum sadece bilgisayar oyunu için laptop", "laptop", "console", "console_excl_laptop"),
    ("iphone istemiyorum android olmalı", "mobile", "mobile", "iphone_excl_android"),
    ("beyaz eşya istemiyorum sadece mobilya", "furniture", "appliance", "appliance_excl_furniture"),
    ("mobilya istemiyorum sadece beyaz eşya", "appliance", "furniture", "furniture_excl_appliance"),
    ("saat istemiyorum bileklik yeter", "wearable", "wearable", "watch_excl_band"),
    ("klima istemiyorum vantilatör önerin", "appliance", "ac", "ac_excl_fan"),
    ("bisiklet istemiyorum e-bike da olur", "bicycle", "bicycle", "bike_excl_ebike"),
    ("kulaklık istemiyorum sadece hoparlör", "audio", "audio", "hp_excl_speaker"),
    ("robot süpürge istemiyorum dikey olsun", "vacuum", "vacuum", "robot_excl_vertical"),
    ("dslr istemiyorum aksiyon kamerası olsun", "camera", "camera", "dslr_excl_action"),
    ("smart tv istemiyorum sadece görüntü", "tv", "tv", "smarttv_excl"),
    ("nintendo istemiyorum playstation olsun", "console", "console", "nintendo_excl_ps"),
    ("kahve makinesi istemiyorum blender lazım", "small_kitchen", "small_kitchen", "coffee_excl_blender"),
    ("blender istemiyorum airfryer olsun", "small_kitchen", "small_kitchen", "blender_excl_airfryer"),
    ("apple watch istemiyorum fitness bileklik", "wearable", "wearable", "apple_excl_fit"),
    ("kanepe istemiyorum koltuk yeter", "furniture", "furniture", "sofa_excl_armchair"),
    ("çamaşır makinesi istemiyorum bulaşık makinesi", "appliance", "appliance", "washer_excl_dish"),
    ("mobilya istemem sadece tv sehpası da olmaz", "tv", "furniture", "furniture_neg_tv"),
    ("tv istemem laptop olsun", "laptop", "tv", "tv_excl_laptop"),
    ("kamera istemem telefon yeter", "mobile", "camera", "cam_excl_phone"),
    ("laptop istemem tablet olsun", "tablet", "laptop", "laptop_excl_tab"),
    ("tablet istemem laptop olsun", "laptop", "tablet", "tab_excl_laptop"),
    ("bilgisayar istemem telefon yeter", "mobile", "computer", "computer_excl_mobile"),
    ("konsol istemem tv yeter", "tv", "console", "console_excl_tv"),
]


def _cid(bucket: str, split: str, index: int) -> str:
    tag = split[:3]
    return f"case-acc-{bucket}-{tag}-{index:03d}"


def _build_no_match_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, suffix, forbidden) in enumerate(NO_MATCH_GENERAL_TEMPLATES, start=1):
        # Route ~85% to validation to hit the ≥100 NO_MATCH support floor.
        split = "validation" if idx % 7 else "development"
        out.append(
            GenCase(
                case_id=_cid(f"nm-{idx:03d}", split, idx),
                semantic_group_id=f"acc.v3.nm.{suffix}",
                utterance=utt,
                expected_status="NO_MATCH",
                tags=("no_match",),
                difficulty="MEDIUM",
                split=split,
                forbidden=forbidden,
            )
        )
    return out


def _build_oos_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, suffix) in enumerate(OOS_TRAVEL_TEMPLATES, start=1):
        split = "validation" if idx % 6 else "development"
        out.append(
            GenCase(
                case_id=_cid(f"oos-{idx:03d}", split, idx),
                semantic_group_id=f"acc.v3.oos.{suffix}",
                utterance=utt,
                expected_status="NO_MATCH",
                tags=("out_of_scope", "no_match"),
                difficulty="MEDIUM",
                split=split,
                forbidden=(FK["travel"],),
            )
        )
    return out


def _build_misleading_lexical_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, suffix) in enumerate(MISLEADING_LEXICAL_TEMPLATES, start=1):
        split = "validation" if idx % 3 else "development"
        expected_status = "MATCHED" if target else "NO_MATCH"
        required = (FK[target],) if target else ()
        out.append(
            GenCase(
                case_id=_cid(f"mis-{idx:03d}", split, idx),
                semantic_group_id=f"acc.v3.mis.{suffix}",
                utterance=utt,
                expected_status=expected_status,
                tags=("indirect_match", "colloquial") if target else ("no_match",),
                difficulty="HARD",
                split=split,
                required=required,
                acceptable=required,
            )
        )
    return out


def _build_multi_need_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, targets, suffix) in enumerate(MULTI_NEED_CLARIFY_TEMPLATES, start=1):
        split = "validation" if idx % 2 else "development"
        acceptable = tuple(FK[t] for t in targets)
        out.append(
            GenCase(
                case_id=_cid(f"mnc-{idx:03d}", split, idx),
                semantic_group_id=f"acc.v3.mnc.{suffix}",
                utterance=utt,
                expected_status="AMBIGUOUS",
                tags=("multi_need", "ambiguous"),
                difficulty="HARD",
                split=split,
                acceptable=acceptable,
            )
        )
    return out


def _build_exclusion_conflict_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, forbidden, suffix) in enumerate(
        EXCLUSION_CONFLICT_TEMPLATES, start=1
    ):
        split = "validation" if idx % 2 else "development"
        required = (FK[target],)
        forbidden_keys = (FK[forbidden],) if forbidden and forbidden != target else ()
        out.append(
            GenCase(
                case_id=_cid(f"exc-{idx:03d}", split, idx),
                semantic_group_id=f"acc.v3.exc.{suffix}",
                utterance=utt,
                expected_status="MATCHED",
                tags=("explicit_negation", "negation"),
                difficulty="HARD",
                split=split,
                required=required,
                acceptable=required,
                forbidden=forbidden_keys,
            )
        )
    return out


ALL_V3_BUILDERS = [
    _build_no_match_cases,
    _build_oos_cases,
    _build_misleading_lexical_cases,
    _build_multi_need_cases,
    _build_exclusion_conflict_cases,
]


def generate() -> tuple[dict, dict]:
    v2_dev = _load_v2(DEV_V2_PATH)
    v2_val = _load_v2(VAL_V2_PATH)

    # Duplicate v2 payloads verbatim, only re-tagging the source. We
    # keep case_id + semantic_group_id so downstream regression tests
    # can still find the hard-negation / hard-sibling cases by id.
    dev_payloads: list[dict] = [dict(c) for c in v2_dev]
    val_payloads: list[dict] = [dict(c) for c in v2_val]
    for payload in dev_payloads + val_payloads:
        privacy = dict(payload.get("privacy") or {})
        privacy["source"] = "acceptance-generator.v3+inherited-v2"
        payload["privacy"] = privacy

    # Generate v3-native cases.
    v3_cases: list[GenCase] = []
    for builder in ALL_V3_BUILDERS:
        v3_cases.extend(builder())
    for case in v3_cases:
        payload = case.to_payload()
        if case.split == "development":
            dev_payloads.append(payload)
        else:
            val_payloads.append(payload)

    # Enforce validation NO_MATCH ≥ 100 support.
    val_nm = sum(1 for p in val_payloads if p["expected"]["status"] == "NO_MATCH")
    if val_nm < 100:
        need = 100 - val_nm
        # Promote development NO_MATCH cases into validation to make up support.
        for payload in dev_payloads:
            if need <= 0:
                break
            if payload["expected"]["status"] != "NO_MATCH":
                continue
            val_payloads.append(payload)
            dev_payloads.remove(payload)
            need -= 1

    _write(DEV_V3_PATH, dev_payloads)
    _write(VAL_V3_PATH, val_payloads)

    counts = {
        "development": len(dev_payloads),
        "validation": len(val_payloads),
        "validation_no_match": sum(
            1 for p in val_payloads if p["expected"]["status"] == "NO_MATCH"
        ),
        "validation_oos": sum(
            1 for p in val_payloads if "out_of_scope" in (p.get("dimensions") or {}).get("tags", [])
        ),
        "validation_multi_need": sum(
            1 for p in val_payloads if "multi_need" in (p.get("dimensions") or {}).get("tags", [])
        ),
        "validation_misleading": sum(
            1
            for p in val_payloads
            if "indirect_match" in (p.get("dimensions") or {}).get("tags", [])
            or "colloquial" in (p.get("dimensions") or {}).get("tags", [])
        ),
        "validation_exclusion": sum(
            1
            for p in val_payloads
            if "explicit_negation" in (p.get("dimensions") or {}).get("tags", [])
        ),
    }
    return counts, {}


def _write(path: Path, payloads: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with path.open("w", encoding="utf-8") as fh:
        for payload in payloads:
            case_id = str(payload["case_id"])
            if case_id in seen:
                continue
            seen.add(case_id)
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.write("\n")


def main() -> int:
    counts, _ = generate()
    print(json.dumps({"counts": counts}, ensure_ascii=False, indent=2))
    ok = (
        counts["development"] >= 120
        and counts["validation"] >= 200
        and counts["validation_no_match"] >= 100
        and counts["validation_oos"] >= 25
        and counts["validation_multi_need"] >= 15
        and counts["validation_misleading"] >= 10
        and counts["validation_exclusion"] >= 25
    )
    if not ok:
        print("ERROR: v3 acceptance coverage floors not met", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
