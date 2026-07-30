"""Hardening v2 dataset generator (ADR-006).

Produces DRAFT / synthetic evaluation cases that stress the matcher's
retrieval / ranking / decision separation:

    development/tr-category-dev.v2.jsonl      (≥ 120)
    validation/tr-category-validation.v2.jsonl (≥ 80, ≥50 with forbidden)

Coverage (across the two files):

* ≥ 50 explicit negation
* ≥ 30 user correction
* ≥ 30 parent-child ambiguity
* ≥ 30 direct alias
* ≥ 30 sibling ambiguity
* ≥ 30 typo / characterless Turkish

Every case:

* ``annotation.status = DRAFT``
* ``privacy.synthetic = true``
* ``semantic_group_id`` prefixed with ``hard.v2.`` — cannot overlap with v1
* fixture keys use v2 catalog (``fixture.computer-devices``,
  ``fixture.portable-computer``, ``fixture.desktop-computer``, plus every
  v1 key).

Run:

    python evaluation/datasets/_generate_hardening_v2.py
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
DEV_PATH = DEV_DIR / "tr-category-dev.v2.jsonl"
VAL_PATH = VAL_DIR / "tr-category-validation.v2.jsonl"


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

# Primary natural-language concept per fixture (NOT category IDs).
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
                "source": "hardening-generator.v2",
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
    """Annotate concept-level constraints for matcher eval (no category IDs)."""
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
                    {
                        "concept": concept,
                        "source": "EXPLICIT_NEGATION",
                        "confidence": 0.99,
                    }
                )

    if "user_correction" in tags or "category_change" in tags:
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
                if not any(n["concept"] == prev for n in negative):
                    negative.append(
                        {
                            "concept": prev,
                            "source": "USER_CORRECTION",
                            "confidence": 0.98,
                        }
                    )

    if not positive and not negative and not corrections:
        return None
    return {
        "positive": positive,
        "negative": negative,
        "corrections": corrections,
    }


def _cid(bucket: str, split: str, index: int) -> str:
    tag = split[:3]
    return f"case-hard-{bucket}-{tag}-{index:03d}"


# ---------------------------------------------------------------------------
# Case builders
# ---------------------------------------------------------------------------


NEG_TEMPLATES: list[tuple[str, str, str, str]] = [
    # (utterance, target_slug, forbidden_slug, tag_suffix)
    ("telefon istemiyorum, bilgisayar arıyorum", "laptop", "mobile", "phone_vs_laptop"),
    ("telefon değil laptop bakıyorum", "laptop", "mobile", "phone_vs_laptop2"),
    ("tablet almam gerekmiyor, telefon arıyorum", "mobile", "tablet", "no_tablet"),
    ("tablet değil telefon lazım", "mobile", "tablet", "tablet_neg"),
    ("televizyon değil bilgisayar bakıyorum", "laptop", "tv", "tv_vs_laptop"),
    ("kamera değil telefon önerin", "mobile", "camera", "camera_neg"),
    ("dizüstü değil masaüstü arıyorum", "desktop", "laptop", "laptop_vs_desktop"),
    ("kulaklık istemiyorum hoparlör alacağım", "audio", "", "headphone_vs_speaker"),
    ("konsol lazım değil bilgisayar olsun", "laptop", "console", "console_neg"),
    ("bulaşık makinesi istemiyorum çamaşır makinesi alacağım", "appliance", "", "appliance_within"),
    ("robot süpürge değil dikey süpürge olsun", "vacuum", "", "vacuum_subtype"),
    ("elektrikli bisiklet değil normal bisiklet", "bicycle", "", "bike_type"),
    ("smart tv değil normal tv", "tv", "", "tv_type"),
    ("bilgisayar istemiyorum tablet alcağım", "tablet", "computer", "no_computer"),
    ("beyaz eşya değil mobilya lazım", "furniture", "appliance", "furniture_over_appliance"),
    ("mobilya değil beyaz eşya", "appliance", "furniture", "appliance_over_furniture"),
    ("klima almak istemiyorum ısıtıcı arıyorum", "appliance", "ac", "no_ac"),
    ("kahve makinesi istemiyorum blender arıyorum", "small_kitchen", "", "kitchen_subtype"),
    ("gopro değil dslr arıyorum", "camera", "", "camera_subtype"),
    ("akıllı saat değil fitness bileklik", "wearable", "", "watch_vs_band"),
    # Extra negation variants — extend explicit_negation coverage to ≥50.
    ("cep telefonu istemiyorum, ipad alacağım", "tablet", "mobile", "no_phone_ipad"),
    ("iphone almak istemiyorum, android da olur laptop olsun", "laptop", "mobile", "no_iphone"),
    ("laptop değil, tablet önerin", "tablet", "laptop", "no_laptop_tablet"),
    ("masaüstü değil taşınabilir bilgisayar", "laptop", "desktop", "desktop_neg"),
    ("dslr değil aksiyon kamerası", "camera", "", "camera_action"),
    ("aksiyon kamerası değil aynasız", "camera", "", "camera_mirrorless"),
    ("hoparlör değil kulaklık", "audio", "", "audio_switch"),
    ("bluetooth kulaklık değil kablolu olsun", "audio", "", "wired_audio"),
    ("smart tv istemiyorum sadece görüntü kalitesi", "tv", "", "no_smart_tv"),
    ("nintendo değil playstation", "console", "", "no_nintendo"),
    ("xbox değil ps5 arıyorum", "console", "", "xbox_neg"),
    ("airfryer değil kahve makinesi", "small_kitchen", "", "no_airfryer"),
    ("blender değil tost makinesi", "small_kitchen", "", "no_blender"),
    ("dikey süpürge değil robot", "vacuum", "", "vertical_neg"),
    ("normal bisiklet değil e-bike", "bicycle", "", "ebike_only"),
    ("apple watch değil bileklik", "wearable", "", "no_apple"),
    ("fitness bileklik değil smartwatch", "wearable", "", "no_band"),
    ("mobilya istemem masaya sandalyeye ihtiyacım yok, tablet yeter", "tablet", "furniture", "furniture_neg_tab"),
    ("çamaşır makinesi değil bulaşık makinesi", "appliance", "", "washer_neg"),
    ("buzdolabı değil bulaşık makinesi", "appliance", "", "fridge_neg"),
    ("klima değil vantilatör önerin", "appliance", "ac", "ac_neg"),
    ("televizyon değil projeksiyon önerin ama tv lazım", "tv", "", "tv_confusion"),
    ("bisiklet değil scooter... aslında bisiklet olsun", "bicycle", "", "bike_flip"),
    ("kanepe değil koltuk arıyorum", "furniture", "", "sofa_switch"),
    ("gardırop değil komodin bakıyorum", "furniture", "", "wardrobe_neg"),
    ("dslr istemiyorum sadece telefon kamerası yeter", "mobile", "camera", "phone_cam_only"),
    ("konsol istemiyorum tv alacağım", "tv", "console", "tv_over_console"),
    ("saat değil telefon", "mobile", "wearable", "watch_neg"),
    ("telefonum var yeni telefon almak istemiyorum tablet lazım", "tablet", "mobile", "no_new_phone"),
    ("bisiklet değil elektrikli motor... yani bisiklet olabilir", "bicycle", "", "bike_final"),
    ("tv istemem sadece hoparlör istiyorum", "audio", "tv", "audio_only"),
    ("blender değil hava fritözü", "small_kitchen", "", "airfryer_switch"),
    ("robot süpürge lazım değil dikey süpürge olur", "vacuum", "", "vacuum_switch"),
    ("iphone değil android telefon olabilir yeter ki telefon olsun", "mobile", "", "phone_any"),
    ("smart tv değil sadece büyük ekran tv", "tv", "", "big_tv"),
    # Additional negation variants explicitly wired with forbidden concepts so
    # validation set carries strong forbidden_fixture_keys support (≥ 50).
    ("tablet değil laptop", "laptop", "tablet", "tab_vs_laptop"),
    ("kamera değil laptop", "laptop", "camera", "cam_vs_laptop"),
    ("laptop değil telefon", "mobile", "laptop", "laptop_vs_mobile"),
    ("bilgisayar değil telefon", "mobile", "computer", "computer_vs_mobile"),
    ("tv değil ses sistemi", "audio", "tv", "tv_vs_audio"),
    ("bisiklet değil tv", "tv", "bicycle", "bike_vs_tv"),
    ("konsol değil tv", "tv", "console", "console_vs_tv"),
    ("beyaz eşya değil süpürge", "vacuum", "appliance", "appliance_vs_vac"),
    ("mobilya değil tv", "tv", "furniture", "furniture_vs_tv"),
    ("saat değil kamera", "camera", "wearable", "watch_vs_cam"),
    ("kamera değil saat", "wearable", "camera", "cam_vs_watch"),
    ("kulaklık değil tv", "tv", "audio", "audio_vs_tv"),
    ("blender değil çamaşır makinesi", "appliance", "small_kitchen", "sk_vs_app"),
    ("çamaşır makinesi değil blender", "small_kitchen", "appliance", "app_vs_sk"),
    ("mobilya değil telefon", "mobile", "furniture", "furniture_vs_mobile"),
    ("laptop değil klima", "ac", "laptop", "laptop_vs_ac"),
    ("klima değil televizyon", "tv", "ac", "ac_vs_tv"),
]


CORRECTION_TEMPLATES: list[tuple[str, str, str, str]] = [
    ("aslında telefon değil laptop demiştim yanlış anladın", "laptop", "mobile", "phone_correction"),
    ("hayır telefon demedim tablet dedim", "tablet", "mobile", "tablet_correction"),
    ("üzgünüm önce laptop dedim ama masaüstü lazım", "desktop", "laptop", "desktop_correction"),
    ("aslında kamera değil telefon", "mobile", "camera", "mobile_correction"),
    ("özür dilerim tablet değil telefon", "mobile", "tablet", "phone_correction_2"),
    ("aslında beyaz eşya değil mobilya diyecektim", "furniture", "appliance", "furniture_correction"),
    ("yanlış söyledim ses sistemi lazım televizyon değil", "audio", "tv", "audio_correction"),
    ("laptop dedim ama aslında dizüstü hayır masaüstü", "desktop", "laptop", "final_desktop"),
    ("hayır bisiklet değil scooter... aslında bisiklet", "bicycle", "", "flip_flop"),
    ("aslında konsol değil bilgisayar", "laptop", "console", "console_to_laptop"),
    ("yok yok telefon dedim demedim tablet lazım", "tablet", "mobile", "tablet_final"),
    # Extra correction variants to reach ≥30 coverage.
    ("hayır ipad dedim iphone demedim", "tablet", "mobile", "ipad_correction"),
    ("özür dilerim kamera dedim ama telefon lazım", "mobile", "camera", "camera_to_phone"),
    ("aslında televizyon dedim ses sistemi değil", "tv", "audio", "audio_to_tv"),
    ("hayır blender dedim mikser değil demek istedim", "small_kitchen", "", "blender_correct"),
    ("aslında robot süpürge değil dikey süpürge diyecektim", "vacuum", "", "vacuum_correct"),
    ("hayır masaüstü değil dizüstü lazım demişim yanlış", "laptop", "desktop", "laptop_recorrect"),
    ("özür dilerim çamaşır değil bulaşık makinesi", "appliance", "", "washer_to_dish"),
    ("hayır e-bike değil normal bisiklet", "bicycle", "", "ebike_correct"),
    ("aslında akıllı saat lazım bileklik dediysem yanlış", "wearable", "", "watch_correct"),
    ("hayır tv değil ekranlı bir cihaz kastettim, laptop olabilir", "laptop", "tv", "tv_to_laptop"),
    ("özür dilerim hoparlör dedim kulaklık kastetmiştim", "audio", "", "hp_correct"),
    ("aslında iphone değil android olacaktı, telefon", "mobile", "", "iphone_to_android"),
    ("hayır oyun konsolu dedim tv kastediyordum", "tv", "console", "console_to_tv"),
    ("özür dilerim laptop demiştim ama tablet lazımmış", "tablet", "laptop", "laptop_to_tab"),
    ("aslında gopro dedim yanlış oldu dslr olacak", "camera", "", "gopro_correct"),
    ("hayır ipad değil galaksi tab olacaktı yani tablet", "tablet", "", "tab_final"),
    ("özür dilerim smart tv dedim ama normal tv yeter", "tv", "", "smart_correct"),
    ("aslında bulaşık değil çamaşır makinesi lazım", "appliance", "", "washer_final"),
    ("hayır fitness bileklik dedim aslında akıllı saat olacak", "wearable", "", "band_to_watch"),
    ("özür dilerim uçak bileti dedim ama tv arıyorum", "tv", "", "travel_to_tv"),
    ("aslında yanılmışım, çamaşır makinesi değil buzdolabı lazım", "appliance", "", "washer_to_fridge"),
    ("özür dilerim hoparlör istedim aslında amaç kulaklık", "audio", "", "audio_swap"),
]


DIRECT_ALIAS_TEMPLATES: list[tuple[str, str, str]] = [
    ("laptop", "laptop", "direct_laptop"),
    ("iphone", "mobile", "direct_iphone"),
    ("macbook", "laptop", "direct_macbook"),
    ("ipad", "tablet", "direct_ipad"),
    ("playstation", "console", "direct_ps"),
    ("xbox", "console", "direct_xbox"),
    ("smart tv", "tv", "direct_smart_tv"),
    ("kahve makinesi", "small_kitchen", "direct_coffee"),
    ("airfryer", "small_kitchen", "direct_airfryer"),
    ("robot süpürge", "vacuum", "direct_robot"),
    ("kablosuz kulaklık", "audio", "direct_wireless_hp"),
    ("bluetooth kulaklık", "audio", "direct_bt_hp"),
    ("blender", "small_kitchen", "direct_blender"),
    ("gopro", "camera", "direct_gopro"),
    ("apple watch", "wearable", "direct_apple_watch"),
    ("inverter klima", "ac", "direct_inverter"),
    ("dizüstü", "laptop", "direct_dizustu"),
    ("notebook", "laptop", "direct_notebook"),
    ("beyaz eşya", "appliance", "direct_beyaz_esya"),
    ("hoparlör", "audio", "direct_speaker"),
    ("kanepe", "furniture", "direct_kanepe"),
    ("gardırop", "furniture", "direct_gardirop"),
    ("bulaşık makinesi", "appliance", "direct_dishwasher"),
    ("çamaşır makinesi", "appliance", "direct_washer"),
    ("dslr", "camera", "direct_dslr"),
    ("smartwatch", "wearable", "direct_smartwatch"),
    ("nintendo", "console", "direct_nintendo"),
    ("tv", "tv", "direct_tv"),
    ("aynasız kamera", "camera", "direct_mirrorless"),
    ("aksiyon kamerası", "camera", "direct_action_cam"),
    ("bluetooth hoparlör", "audio", "direct_bt_speaker"),
    ("dikey süpürge", "vacuum", "direct_vertical"),
    ("hava fritözü", "small_kitchen", "direct_hava"),
    ("cep telefonu", "mobile", "direct_cep"),
]


PARENT_CHILD_TEMPLATES: list[tuple[str, str, str, tuple[str, ...], str]] = [
    (
        "bilgisayar bakıyorum işlerim için dizüstü olabilir",
        "laptop",
        "MATCHED",
        (),
        "parent_child_laptop_preferred",
    ),
    (
        "bilgisayar alacağım masaüstü daha uygun",
        "desktop",
        "MATCHED",
        (),
        "parent_child_desktop_preferred",
    ),
    (
        "yeni bir dizüstü bilgisayar arıyorum",
        "laptop",
        "MATCHED",
        (),
        "parent_child_child_specific_laptop",
    ),
    (
        "masaüstü bilgisayar önerin",
        "desktop",
        "MATCHED",
        (),
        "parent_child_child_specific_desktop",
    ),
    (
        "kod yazacağım için taşınabilir bilgisayar lazım",
        "laptop",
        "MATCHED",
        (),
        "parent_child_indirect_laptop",
    ),
    (
        "evde oyun için masaüstü kasa yenileyeceğim",
        "desktop",
        "MATCHED",
        (),
        "parent_child_indirect_desktop",
    ),
    (
        "bir bilgisayar bakıyorum daha karar veremedim",
        "",  # ambiguous — child specificity missing
        "AMBIGUOUS",
        ("laptop", "desktop"),
        "parent_child_ambiguous_computer",
    ),
    (
        "bilgisayar önerir misiniz",
        "",
        "AMBIGUOUS",
        ("laptop", "desktop"),
        "parent_child_ambiguous_generic",
    ),
    (
        "kompüter bakıyorum türü fark etmez",
        "",
        "AMBIGUOUS",
        ("laptop", "desktop"),
        "parent_child_ambiguous_generic_2",
    ),
    (
        "pc almak istiyorum tavsiye",
        "",
        "AMBIGUOUS",
        ("laptop", "desktop"),
        "parent_child_ambiguous_pc",
    ),
]


SIBLING_AMBIGUITY_TEMPLATES: list[tuple[str, tuple[str, ...], str]] = [
    ("telefonla mı tablet ile mi film izlemeliyim", ("mobile", "tablet"), "phone_vs_tablet"),
    ("tv mi projeksiyon mu bilmiyorum salon için", ("tv",), "tv_vs_projector"),
    ("kulaklık mı hoparlör mü almalıyım", ("audio",), "hp_vs_speaker"),
    ("konsol mu laptop mu daha iyi oyun için", ("console", "laptop"), "console_vs_laptop"),
    ("akıllı saat mı bileklik mi spor için", ("wearable",), "watch_vs_band_amb"),
    ("dizüstü mü masaüstü mü almalıyım", ("laptop", "desktop"), "laptop_vs_desktop"),
    ("robot süpürge mi kablosuz süpürge mi", ("vacuum",), "robot_vs_upright"),
    ("blender mı airfryer mı mutfağa", ("small_kitchen",), "blender_vs_airfryer"),
    ("split klima mı inverter mi", ("ac",), "split_vs_inverter"),
    ("kanepe mi koltuk mu oturma odasına", ("furniture",), "sofa_vs_armchair"),
    ("iphone mu android mı", ("mobile",), "iphone_vs_android"),
    ("playstation mı xbox mu", ("console",), "ps_vs_xbox"),
    ("dslr mi aynasız mı", ("camera",), "dslr_vs_mirrorless"),
    ("smart tv mi projektör mü", ("tv",), "smart_tv_vs_projector"),
    ("tablet mi laptop mı öğrenci için", ("tablet", "laptop"), "student_amb"),
    ("mobilya mı beyaz eşya mı önce", ("furniture", "appliance"), "furniture_vs_appliance"),
]


TYPO_TEMPLATES: list[tuple[str, str, str]] = [
    ("telfon almak istiyorum", "mobile", "typo_phone_1"),
    ("cep telefno bakıyorum", "mobile", "typo_phone_2"),
    ("akıllı telfon lazım", "mobile", "typo_phone_3"),
    ("laptp almak istiyorum", "laptop", "typo_laptop_1"),
    ("dızüstü bakıyorum", "laptop", "typo_laptop_2"),
    ("notbook lazım", "laptop", "typo_laptop_3"),
    ("dızustu bılgısayar arıyorum", "laptop", "typo_laptop_4"),
    ("tablt almak istiyorum", "tablet", "typo_tablet"),
    ("buzdolabi almak istiyorum", "appliance", "typo_fridge_1"),
    ("camasır makınesi arıyorum", "appliance", "typo_washer_1"),
    ("bulaşk makinesi", "appliance", "typo_dish_1"),
    ("kanpe almak istiyorum", "furniture", "typo_kanepe"),
    ("dogal ahsap gardırop", "furniture", "typo_gardirop"),
    ("supurge alacağım", "vacuum", "typo_vacuum_1"),
    ("supurge modellerine bakıyorum", "vacuum", "typo_vacuum_2"),
    ("kliima öneri", "ac", "typo_ac_1"),
    ("iphon aliyorum", "mobile", "typo_iphone"),
    ("kahv makinesi lazım", "small_kitchen", "typo_coffee"),
    ("airfyer önerin", "small_kitchen", "typo_airfryer"),
    ("hopalor lazım", "audio", "typo_speaker"),
    ("kulak lık bakıyorum", "audio", "typo_headphone"),
    ("televzyon almak istiyorum", "tv", "typo_tv"),
    ("smart t v arıyorum", "tv", "typo_smart_tv"),
    ("konsl bakıyorum", "console", "typo_console"),
    ("bısıklet arıyorum", "bicycle", "typo_bike_1"),
    ("bisklet önerin", "bicycle", "typo_bike_2"),
    ("gopr bakıyorum", "camera", "typo_gopro"),
    ("akilli saat almak istiyorum", "wearable", "typo_watch"),
    ("fıtnes bileklik", "wearable", "typo_fitness"),
    ("cam maşır makinesi", "appliance", "typo_washer_2"),
    ("bulaşk maıknesi", "appliance", "typo_dish_2"),
    ("mobilyaa arıyorum", "furniture", "typo_furniture"),
    ("kanepee önerin", "furniture", "typo_kanepe_2"),
    ("kliima nöeri", "ac", "typo_ac_2"),
]


NO_MATCH_TEMPLATES: list[tuple[str, str, tuple[str, ...]]] = [
    ("bugün hava çok güzel", "no_general_1", ()),
    ("hesabımdaki son işlemi görebilir miyim", "no_support_1", ()),
    ("uçak bileti almak istiyorum", "no_travel_1", (FK["travel"],)),
    ("otel rezervasyonu yapmak istiyorum", "no_travel_2", (FK["travel"],)),
    ("bize bir tavsiye ver hafta sonu için", "no_general_2", ()),
    ("kredi kartı taksitlerini nasıl kapatırım", "no_finance_1", ()),
    ("kahve içiyorum sohbet edelim", "no_general_3", ()),
]


def _build_negation_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, forbid, suffix) in enumerate(NEG_TEMPLATES, start=1):
        # Route cases with an explicit forbidden into validation for
        # meaningful forbidden_fixture_keys support (ADR-006 requires ≥ 50).
        if forbid:
            split = "validation"
        else:
            split = "development" if idx % 2 else "validation"
        required = (FK[target],) if target else ()
        forbidden = (FK[forbid],) if forbid else ()
        acceptable = required
        # Ambiguous-only case has no forbid + no required; ensure at least one.
        out.append(
            GenCase(
                case_id=_cid(f"neg-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.neg.{suffix}",
                utterance=utt,
                expected_status="MATCHED" if required else "AMBIGUOUS",
                tags=("explicit_negation", "negation"),
                difficulty="HARD",
                split=split,
                required=required,
                acceptable=acceptable,
                forbidden=forbidden,
            )
        )
    return out


def _build_correction_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, prev, suffix) in enumerate(CORRECTION_TEMPLATES, start=1):
        # Corrections often carry a forbidden concept; route the majority to
        # validation so its forbidden_fixture_keys support reaches ≥ 50.
        if prev:
            split = "development" if idx % 3 == 0 else "validation"
        else:
            split = "validation" if idx % 2 == 0 else "development"
        required = (FK[target],)
        forbidden = (FK[prev],) if prev else ()
        out.append(
            GenCase(
                case_id=_cid(f"corr-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.corr.{suffix}",
                utterance=utt,
                expected_status="MATCHED",
                tags=("user_correction",),
                difficulty="HARD",
                split=split,
                required=required,
                acceptable=required,
                forbidden=forbidden,
            )
        )
    return out


def _build_direct_alias_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, suffix) in enumerate(DIRECT_ALIAS_TEMPLATES, start=1):
        split = "validation" if idx % 4 == 0 else "development"
        out.append(
            GenCase(
                case_id=_cid(f"alias-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.alias.{suffix}",
                utterance=utt,
                expected_status="MATCHED",
                tags=("direct_alias", "direct_match"),
                difficulty="EASY",
                split=split,
                required=(FK[target],),
                acceptable=(FK[target],),
            )
        )
    return out


def _build_parent_child_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, status, extra, suffix) in enumerate(PARENT_CHILD_TEMPLATES, start=1):
        split = "validation" if idx % 2 == 0 else "development"
        required: tuple[str, ...] = ()
        acceptable: tuple[str, ...] = ()
        if target:
            required = (FK[target],)
            acceptable = required
        else:
            acceptable = tuple(FK[t] for t in extra)
        out.append(
            GenCase(
                case_id=_cid(f"pc-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.pc.{suffix}",
                utterance=utt,
                expected_status=status,
                tags=("parent_child_ambiguity",),
                difficulty="HARD",
                split=split,
                required=required,
                acceptable=acceptable,
            )
        )
    # Add extra pairs so count ≥ 30
    extras = [
        ("dizüstü ya da masaüstü fark etmez", "", "AMBIGUOUS", ("laptop", "desktop"), "extra_generic"),
        ("bilgisayar önerin fark etmez", "", "AMBIGUOUS", ("laptop", "desktop"), "extra_generic2"),
        ("laptop veya masaüstü olabilir", "", "AMBIGUOUS", ("laptop", "desktop"), "extra_generic3"),
        ("kod yazmak için taşınabilir bir bilgisayar", "laptop", "MATCHED", (), "extra_child_1"),
        ("işyerine masaüstü bilgisayar", "desktop", "MATCHED", (), "extra_child_2"),
        ("okulda taşımak için ince laptop", "laptop", "MATCHED", (), "extra_child_3"),
        ("evde oyun oynayacağım kasa toplayayım", "desktop", "MATCHED", (), "extra_child_4"),
        ("üniversite için notebook", "laptop", "MATCHED", (), "extra_child_5"),
        ("stüdyo işi için masaüstü", "desktop", "MATCHED", (), "extra_child_6"),
        ("evde küçük bir bilgisayar ne olabilir", "", "AMBIGUOUS", ("laptop", "desktop"), "extra_generic4"),
        ("2 tane bilgisayar almam gerek karar veremedim", "", "AMBIGUOUS", ("laptop", "desktop"), "extra_generic5"),
        ("laptop veya notebook", "laptop", "MATCHED", (), "extra_child_7"),
        ("ultrabook lazım", "laptop", "MATCHED", (), "extra_child_8"),
        ("all in one bilgisayar", "desktop", "MATCHED", (), "extra_child_9"),
        ("masaüstü toplama seti", "desktop", "MATCHED", (), "extra_child_10"),
        ("çift ekran masaüstü", "desktop", "MATCHED", (), "extra_child_11"),
        ("ergonomik dizüstü tavsiye", "laptop", "MATCHED", (), "extra_child_12"),
        ("performanslı masaüstü kasa", "desktop", "MATCHED", (), "extra_child_13"),
        ("dizüstü, çevirme ekran olabilir", "laptop", "MATCHED", (), "extra_child_14"),
        ("iş için bir bilgisayar arıyorum türü önemli değil", "", "AMBIGUOUS", ("laptop", "desktop"), "extra_generic6"),
    ]
    base_len = len(out)
    for j, (utt, target, status, extra, suffix) in enumerate(extras, start=1):
        idx = base_len + j
        split = "development" if j % 2 else "validation"
        required = (FK[target],) if target else ()
        acceptable = required or tuple(FK[t] for t in extra)
        out.append(
            GenCase(
                case_id=_cid(f"pc-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.pc.{suffix}",
                utterance=utt,
                expected_status=status,
                tags=("parent_child_ambiguity",),
                difficulty="HARD",
                split=split,
                required=required,
                acceptable=acceptable,
            )
        )
    return out


def _build_sibling_ambiguity_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, targets, suffix) in enumerate(SIBLING_AMBIGUITY_TEMPLATES, start=1):
        split = "validation" if idx % 3 else "development"
        acceptable = tuple(FK[t] for t in targets)
        out.append(
            GenCase(
                case_id=_cid(f"sib-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.sib.{suffix}",
                utterance=utt,
                expected_status="AMBIGUOUS",
                tags=("sibling_ambiguity", "ambiguous"),
                difficulty="HARD",
                split=split,
                acceptable=acceptable,
            )
        )
    # Add a few extras to comfortably exceed 30
    extras = [
        ("ses sistemi mi kulaklık mı", ("audio",), "audio_amb_extra"),
        ("beyaz eşya mı süpürge mi önce", ("appliance", "vacuum"), "appliance_vs_vacuum"),
        ("mobilya mı airfryer mı önce", ("furniture", "small_kitchen"), "furniture_vs_kitchen"),
        ("dslr mi telefon kamerası mı", ("camera", "mobile"), "camera_vs_phone_cam"),
        ("smartwatch mı akıllı bileklik mi", ("wearable",), "smartwatch_amb"),
        ("konsol mu tablet mi çocuğa", ("console", "tablet"), "console_vs_tablet"),
        ("saatli tv mi projektör mü", ("tv",), "tv_only_amb"),
        ("apple watch mı garmin mi", ("wearable",), "watch_brand_amb"),
        ("hoparlör seti mi soundbar mı", ("audio",), "speaker_soundbar"),
        ("kablosuz mu kablolu kulaklık mı", ("audio",), "wired_wireless"),
        ("blender mı el blender mi", ("small_kitchen",), "blender_amb"),
        ("dikey mi robot süpürge mi", ("vacuum",), "vacuum_types"),
        ("kanepe mi berjer mi", ("furniture",), "sofa_berjer"),
        ("uçak bileti mi otel mi", (), "oos_amb"),
        ("laptop mu ultrabook mu", ("laptop",), "laptop_subtype"),
        ("tablet mi ipad mi", ("tablet",), "tablet_ipad_amb"),
    ]
    base = len(out)
    for j, (utt, targets, suffix) in enumerate(extras, start=1):
        idx = base + j
        split = "development" if j % 2 else "validation"
        acceptable = tuple(FK[t] for t in targets) if targets else ()
        status = "AMBIGUOUS" if targets else "NO_MATCH"
        out.append(
            GenCase(
                case_id=_cid(f"sib-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.sib.{suffix}",
                utterance=utt,
                expected_status=status,
                tags=("sibling_ambiguity", "ambiguous"),
                difficulty="HARD",
                split=split,
                acceptable=acceptable,
            )
        )
    return out


def _build_typo_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, target, suffix) in enumerate(TYPO_TEMPLATES, start=1):
        split = "validation" if idx % 4 == 0 else "development"
        out.append(
            GenCase(
                case_id=_cid(f"typo-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.typo.{suffix}",
                utterance=utt,
                expected_status="MATCHED",
                tags=("characterless_turkish", "typo"),
                difficulty="HARD",
                split=split,
                required=(FK[target],),
                acceptable=(FK[target],),
            )
        )
    return out


def _build_no_match_cases() -> list[GenCase]:
    out: list[GenCase] = []
    for idx, (utt, suffix, forbidden) in enumerate(NO_MATCH_TEMPLATES, start=1):
        split = "validation" if idx % 2 else "development"
        out.append(
            GenCase(
                case_id=_cid(f"nom-{idx:02d}", split, idx),
                semantic_group_id=f"hard.v2.nom.{suffix}",
                utterance=utt,
                expected_status="NO_MATCH",
                tags=("no_match",),
                difficulty="MEDIUM",
                split=split,
                forbidden=forbidden,
            )
        )
    return out


ALL_BUILDERS = [
    _build_negation_cases,
    _build_correction_cases,
    _build_direct_alias_cases,
    _build_parent_child_cases,
    _build_sibling_ambiguity_cases,
    _build_typo_cases,
    _build_no_match_cases,
]


def generate() -> tuple[dict[str, int], dict[str, int]]:
    all_cases: list[GenCase] = []
    for builder in ALL_BUILDERS:
        all_cases.extend(builder())

    # Ensure development ≥ 120 by promoting some validation cases if needed —
    # but never break the validation floor. We first count.
    dev = [c for c in all_cases if c.split == "development"]
    val = [c for c in all_cases if c.split == "validation"]
    if len(dev) < 120:
        needed = 120 - len(dev)
        # promote from typo bucket where we have plenty
        for c in val:
            if needed <= 0:
                break
            if "typo" in c.tags or "direct_alias" in c.tags:
                c.split = "development"
                needed -= 1
        dev = [c for c in all_cases if c.split == "development"]
        val = [c for c in all_cases if c.split == "validation"]
    if len(val) < 80:
        needed = 80 - len(val)
        for c in dev:
            if needed <= 0:
                break
            # Move some direct_alias / typo cases into validation.
            if "direct_alias" in c.tags or "explicit_negation" in c.tags:
                c.split = "validation"
                needed -= 1
        dev = [c for c in all_cases if c.split == "development"]
        val = [c for c in all_cases if c.split == "validation"]

    counts_by_bucket = _bucket_counts(all_cases)
    forbidden_count_val = sum(1 for c in val if c.forbidden)
    forbidden_count_all = sum(1 for c in all_cases if c.forbidden)

    _write_split(DEV_PATH, dev)
    _write_split(VAL_PATH, val)

    return (
        {
            "development": len(dev),
            "validation": len(val),
            "validation_forbidden_support": forbidden_count_val,
            "total_forbidden_support": forbidden_count_all,
        },
        counts_by_bucket,
    )


def _bucket_counts(cases: Iterable[GenCase]) -> dict[str, int]:
    counts: dict[str, int] = {
        "explicit_negation": 0,
        "user_correction": 0,
        "parent_child_ambiguity": 0,
        "direct_alias": 0,
        "sibling_ambiguity": 0,
        "characterless_turkish": 0,
        "no_match": 0,
    }
    for case in cases:
        for tag in case.tags:
            if tag in counts:
                counts[tag] += 1
    return counts


def _write_split(target: Path, cases: Sequence[GenCase]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case.to_payload(), ensure_ascii=False))
            fh.write("\n")


def main() -> int:
    counts, buckets = generate()
    print(json.dumps({"counts": counts, "buckets": buckets}, ensure_ascii=False, indent=2))
    ok = (
        counts["development"] >= 120
        and counts["validation"] >= 80
        and counts["validation_forbidden_support"] >= 50
        and buckets["explicit_negation"] >= 50
        and buckets["user_correction"] >= 30
        and buckets["parent_child_ambiguity"] >= 30
        and buckets["direct_alias"] >= 30
        and buckets["sibling_ambiguity"] >= 30
        and buckets["characterless_turkish"] >= 30
    )
    if not ok:
        print("ERROR: hardening coverage floors not met", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
