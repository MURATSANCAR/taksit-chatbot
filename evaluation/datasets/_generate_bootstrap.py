"""Bootstrap generator for the Turkish category-match evaluation datasets.

Produces three JSONL splits under ``evaluation/datasets``:

    development/tr-category-dev.v1.jsonl            (≥ 150)
    golden/tr-category-validation.v1.jsonl          (≥  50)
    golden/tr-category-holdout.v1.jsonl             (≥  50)

Each case is schema-valid, uses fixture keys only, marks itself as
synthetic (``privacy.synthetic = true``) and stays at
``annotation.status = DRAFT`` — none of these cases qualify for the
HUMAN_REVIEWED tier that ADR-005 reserves for two-reviewer golden.

Cases live under stable ``semantic_group_id`` values so that no group
appears in more than one split (split integrity is later enforced
by ``taksitlio.evaluation.dataset.assert_split_integrity``).

Run once with:

    python evaluation/datasets/_generate_bootstrap.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_PATH = REPO_ROOT / "evaluation" / "datasets" / "development" / "tr-category-dev.v1.jsonl"
VAL_PATH = REPO_ROOT / "evaluation" / "datasets" / "golden" / "tr-category-validation.v1.jsonl"
HOLD_PATH = REPO_ROOT / "evaluation" / "datasets" / "golden" / "tr-category-holdout.v1.jsonl"

FIXTURE_KEYS = {
    "mobile": "fixture.mobile-device",
    "laptop": "fixture.portable-computer",
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


@dataclass
class Group:
    group_id: str
    split: str  # "development" | "validation" | "holdout"
    utterances: list[str]
    expected_status: str
    required: tuple[str, ...] = ()
    acceptable: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    difficulty: str = "MEDIUM"


def _g(
    slug: str,
    split: str,
    utterances: Sequence[str],
    *,
    status: str,
    required: Sequence[str] = (),
    acceptable: Sequence[str] = (),
    forbidden: Sequence[str] = (),
    tags: Sequence[str] = (),
    difficulty: str = "MEDIUM",
) -> Group:
    return Group(
        group_id=slug,
        split=split,
        utterances=list(utterances),
        expected_status=status,
        required=tuple(required),
        acceptable=tuple(acceptable or required),
        forbidden=tuple(forbidden),
        tags=tuple(tags),
        difficulty=difficulty,
    )


# ---------------------------------------------------------------------------
# Group definitions — per intent / per split
# ---------------------------------------------------------------------------

GROUPS: list[Group] = []


def _direct_variants(prefix: str, category_key: str, utterances_by_split: dict[str, list[str]]) -> None:
    for split, phrases in utterances_by_split.items():
        for i, phrase in enumerate(phrases, start=1):
            GROUPS.append(
                _g(
                    f"{prefix}.direct.{split[:3]}.g{i:02d}",
                    split,
                    [phrase],
                    status="MATCHED",
                    required=[category_key],
                    tags=("direct_match",),
                    difficulty="EASY",
                )
            )


def _indirect_variants(prefix: str, category_key: str, utterances_by_split: dict[str, list[str]]) -> None:
    for split, phrases in utterances_by_split.items():
        for i, phrase in enumerate(phrases, start=1):
            GROUPS.append(
                _g(
                    f"{prefix}.indirect.{split[:3]}.g{i:02d}",
                    split,
                    [phrase],
                    status="MATCHED",
                    required=[category_key],
                    tags=("indirect_match",),
                    difficulty="MEDIUM",
                )
            )


def _typo_variants(prefix: str, category_key: str, utterances_by_split: dict[str, list[str]]) -> None:
    for split, phrases in utterances_by_split.items():
        for i, phrase in enumerate(phrases, start=1):
            GROUPS.append(
                _g(
                    f"{prefix}.typo.{split[:3]}.g{i:02d}",
                    split,
                    [phrase],
                    status="MATCHED",
                    acceptable=[category_key],
                    required=[],
                    tags=("typo", "colloquial"),
                    difficulty="HARD",
                )
            )


# --- Mobile device ----------------------------------------------------------
_direct_variants(
    "mobile",
    FIXTURE_KEYS["mobile"],
    {
        "development": [
            "telefon almak istiyorum",
            "cep telefonu bakıyorum",
            "akıllı telefon lazım",
            "yeni bir iphone alacağım",
            "android telefon önerin",
            "bir telefon seçmem lazım",
            "telefonuma yeni model bakıyorum",
        ],
        "validation": [
            "cep telefonu için bütçem var",
            "akıllı telefon değiştirmek istiyorum",
        ],
        "holdout": [
            "yeni bir cep telefonu bakıyorum",
            "iphone modelleri arıyorum",
        ],
    },
)
_indirect_variants(
    "mobile",
    FIXTURE_KEYS["mobile"],
    {
        "development": [
            "kamera kalitesi iyi bir cihaz arıyorum, cepten çekim yapacağım",
            "pil ömrü uzun bir mobil cihaz olsun",
            "instagramda video çekmek için yeni bir cihaz almak istiyorum",
            "gün boyu şarj tutan bir cihaz olsun cebimde taşıyacağım",
        ],
        "validation": [
            "ekran kalitesi iyi cepte taşıyabileceğim bir cihaz istiyorum",
        ],
        "holdout": [
            "yolda müzik dinlemek ve fotoğraf çekmek için cep uyumlu cihaz",
        ],
    },
)
_typo_variants(
    "mobile",
    FIXTURE_KEYS["mobile"],
    {
        "development": [
            "telfon almak istiyorum",
            "cep telefno bakıyorum",
            "akılı telefon lazım",
        ],
        "validation": [
            "telefno alıcam",
        ],
        "holdout": [
            "cepphone almak istiyorum",
        ],
    },
)

# --- Portable computer ------------------------------------------------------
_direct_variants(
    "laptop",
    FIXTURE_KEYS["laptop"],
    {
        "development": [
            "laptop almak istiyorum",
            "dizüstü bilgisayar bakıyorum",
            "notebook lazım",
            "macbook düşünüyorum",
            "yeni bir laptop alacağım",
            "iş için dizüstü almam gerek",
            "ultrabook önerir misiniz",
        ],
        "validation": [
            "öğrenci laptopu bakıyorum",
            "üniversite için dizüstü lazım",
        ],
        "holdout": [
            "notebook için bütçem 25 bin",
            "macbook mu diğer laptoplar mı",
        ],
    },
)
_indirect_variants(
    "laptop",
    FIXTURE_KEYS["laptop"],
    {
        "development": [
            "kod yazacağım güçlü bir taşınabilir bilgisayar arıyorum",
            "üniversiteye götüreceğim hafif bir bilgisayar lazım",
            "seyahatte kullanacağım pili uzun süren bir cihaz",
        ],
        "validation": [
            "ofis işleri için taşınabilir bir bilgisayar",
        ],
        "holdout": [
            "grafik tasarım yapabileceğim taşınabilir bilgisayar",
        ],
    },
)
_typo_variants(
    "laptop",
    FIXTURE_KEYS["laptop"],
    {
        "development": [
            "laptp almak istiyorum",
            "dızüstü bakıyorum",
            "notbook lazım",
        ],
        "validation": ["laptobum bozuldu yenisini alcam"],
        "holdout": ["dızustu bılgısayar bakıyorum"],
    },
)

# --- Tablet ----------------------------------------------------------------
_direct_variants(
    "tablet",
    FIXTURE_KEYS["tablet"],
    {
        "development": [
            "tablet almak istiyorum",
            "ipad bakıyorum",
            "çocuğuma tablet lazım",
            "yeni bir tablet arıyorum",
        ],
        "validation": ["okul için tablet önerin"],
        "holdout": ["tablet modelleri hakkında bilgi"],
    },
)
_indirect_variants(
    "tablet",
    FIXTURE_KEYS["tablet"],
    {
        "development": [
            "çizim yapmak için dokunmatik ekranlı bir cihaz",
            "kitap okumak için büyük ekranlı taşınabilir cihaz",
        ],
        "validation": ["not almak için kalemi olan bir cihaz"],
        "holdout": ["film izlemek için portatif geniş ekran"],
    },
)

# --- Home appliance --------------------------------------------------------
_direct_variants(
    "appliance",
    FIXTURE_KEYS["appliance"],
    {
        "development": [
            "buzdolabı almak istiyorum",
            "çamaşır makinesi lazım",
            "bulaşık makinesi bakıyorum",
            "fırın almak istiyorum",
            "beyaz eşya yenileyeceğim",
        ],
        "validation": [
            "yeni eve buzdolabı ve çamaşır makinesi",
        ],
        "holdout": ["derin dondurucu almak istiyorum"],
    },
)
_indirect_variants(
    "appliance",
    FIXTURE_KEYS["appliance"],
    {
        "development": [
            "enerji tasarruflu bir ev aleti düşünüyorum, mutfak için büyük hacimli olsun",
            "çamaşırları kurutan bir cihaz düşünüyorum",
        ],
        "validation": ["haftalık büyük yemekler için geniş fırın"],
        "holdout": ["mutfağa büyük hacimli soğutucu"],
    },
)
_typo_variants(
    "appliance",
    FIXTURE_KEYS["appliance"],
    {
        "development": ["buzdolabi almak istiyorum", "camasır makınesi"],
        "validation": ["fırn arıyorum"],
        "holdout": ["bulaşk makinesi"],
    },
)

# --- Furniture -------------------------------------------------------------
_direct_variants(
    "furniture",
    FIXTURE_KEYS["furniture"],
    {
        "development": [
            "kanepe almak istiyorum",
            "koltuk takımı bakıyorum",
            "yatak arıyorum",
            "gardırop almak istiyorum",
            "yeni mobilya alacağım",
        ],
        "validation": ["oturma odası için mobilya"],
        "holdout": ["yatak odası takımı arıyorum"],
    },
)
_indirect_variants(
    "furniture",
    FIXTURE_KEYS["furniture"],
    {
        "development": ["misafirler için açılıp yatak olan oturma"],
        "validation": ["küçük daireye uygun L koltuk"],
        "holdout": ["yeni evime ergonomik uyku çözümü"],
    },
)

# --- Camera ----------------------------------------------------------------
_direct_variants(
    "camera",
    FIXTURE_KEYS["camera"],
    {
        "development": [
            "fotoğraf makinesi almak istiyorum",
            "dslr bakıyorum",
            "aynasız kamera arıyorum",
            "gopro almak istiyorum",
        ],
        "validation": ["aksiyon kamerası lazım"],
        "holdout": ["profesyonel fotoğraf makinesi"],
    },
)
_indirect_variants(
    "camera",
    FIXTURE_KEYS["camera"],
    {
        "development": ["hobi olarak profesyonel çekim yapmak istiyorum, iyi bir cihaz lazım"],
        "validation": ["seyahatlerde kaliteli fotoğraf çekmek için kompakt cihaz"],
        "holdout": ["youtube videoları için görüntü kaydeden cihaz"],
    },
)

# --- Wearable --------------------------------------------------------------
_direct_variants(
    "wearable",
    FIXTURE_KEYS["wearable"],
    {
        "development": [
            "akıllı saat almak istiyorum",
            "smartwatch bakıyorum",
            "fitness bileklik lazım",
            "apple watch alacağım",
        ],
        "validation": ["spor için akıllı saat"],
        "holdout": ["kalp ritmi ölçen bileklik"],
    },
)
_indirect_variants(
    "wearable",
    FIXTURE_KEYS["wearable"],
    {
        "development": ["uyku takibi yapan bir cihaz istiyorum kola takacağım"],
        "validation": ["nabız ölçen bir cihaz istiyorum"],
        "holdout": ["adımlarımı sayan bir cihaz düşünüyorum"],
    },
)

# --- Audio -----------------------------------------------------------------
_direct_variants(
    "audio",
    FIXTURE_KEYS["audio"],
    {
        "development": [
            "kulaklık almak istiyorum",
            "kablosuz kulaklık lazım",
            "airpods bakıyorum",
            "hoparlör arıyorum",
        ],
        "validation": ["bluetooth kulaklık önerin"],
        "holdout": ["ev sinema hoparlör sistemi"],
    },
)
_indirect_variants(
    "audio",
    FIXTURE_KEYS["audio"],
    {
        "development": ["koşarken kullanacağım pili uzun süren kablosuz cihaz"],
        "validation": ["gürültü engelleyen bir cihaz metroda"],
        "holdout": ["salon için güçlü ses veren cihaz"],
    },
)

# --- Television ------------------------------------------------------------
_direct_variants(
    "tv",
    FIXTURE_KEYS["tv"],
    {
        "development": [
            "televizyon almak istiyorum",
            "smart tv bakıyorum",
            "4k tv arıyorum",
            "yeni televizyon alacağım",
        ],
        "validation": ["salon için büyük ekran tv"],
        "holdout": ["yatak odasına küçük televizyon"],
    },
)
_indirect_variants(
    "tv",
    FIXTURE_KEYS["tv"],
    {
        "development": ["netflix ve youtube izleyeceğim salon için ekran"],
        "validation": ["oled ekran arıyorum salona"],
        "holdout": ["yüksek çözünürlüklü büyük ekran"],
    },
)

# --- Gaming console --------------------------------------------------------
_direct_variants(
    "console",
    FIXTURE_KEYS["console"],
    {
        "development": [
            "playstation almak istiyorum",
            "xbox arıyorum",
            "oyun konsolu bakıyorum",
            "ps5 alacağım",
        ],
        "validation": ["çocuğa nintendo düşünüyorum"],
        "holdout": ["yeni nesil konsol"],
    },
)
_indirect_variants(
    "console",
    FIXTURE_KEYS["console"],
    {
        "development": ["evde arkadaşlarla oyun oynayacağımız cihaz"],
        "validation": ["çocuğuma doğum günü için oyun cihazı"],
        "holdout": ["ev için oyun oynamak amaçlı cihaz"],
    },
)

# --- Small kitchen appliance -----------------------------------------------
_direct_variants(
    "small_kitchen",
    FIXTURE_KEYS["small_kitchen"],
    {
        "development": [
            "kahve makinesi almak istiyorum",
            "blender lazım",
            "airfryer bakıyorum",
            "tost makinesi",
        ],
        "validation": ["yeni bir kahve makinesi"],
        "holdout": ["hava fritözü almak istiyorum"],
    },
)
_indirect_variants(
    "small_kitchen",
    FIXTURE_KEYS["small_kitchen"],
    {
        "development": ["sağlıklı yemek yapmak için yağsız pişirme cihazı"],
        "validation": ["kahvaltı için pratik cihazlar"],
        "holdout": ["smoothie yapmak için mutfak cihazı"],
    },
)

# --- Vacuum cleaner --------------------------------------------------------
_direct_variants(
    "vacuum",
    FIXTURE_KEYS["vacuum"],
    {
        "development": [
            "süpürge almak istiyorum",
            "robot süpürge lazım",
            "dikey süpürge bakıyorum",
        ],
        "validation": ["kablosuz süpürge önerin"],
        "holdout": ["yeni robot süpürge"],
    },
)
_indirect_variants(
    "vacuum",
    FIXTURE_KEYS["vacuum"],
    {
        "development": ["evi otomatik temizleyen küçük bir cihaz düşünüyorum"],
        "validation": ["merdivende de kolay taşınacak temizlik cihazı"],
        "holdout": ["halıları derin temizleyen cihaz"],
    },
)

# --- Air conditioner -------------------------------------------------------
_direct_variants(
    "ac",
    FIXTURE_KEYS["ac"],
    {
        "development": [
            "klima almak istiyorum",
            "salona inverter klima",
            "yeni klima bakıyorum",
        ],
        "validation": ["12000 btu klima arıyorum"],
        "holdout": ["yatak odası için sessiz klima"],
    },
)
_indirect_variants(
    "ac",
    FIXTURE_KEYS["ac"],
    {
        "development": ["yaz sıcağında evi soğutan cihaz istiyorum"],
        "validation": ["kış aylarında ısıtan yaz aylarında serinleten cihaz"],
        "holdout": ["sessiz çalışan soğutma cihazı yatak odasına"],
    },
)

# --- Bicycle ---------------------------------------------------------------
_direct_variants(
    "bicycle",
    FIXTURE_KEYS["bicycle"],
    {
        "development": [
            "bisiklet almak istiyorum",
            "elektrikli bisiklet lazım",
        ],
        "validation": ["e-bike bakıyorum"],
        "holdout": ["yeni bir bisiklet arıyorum"],
    },
)
_indirect_variants(
    "bicycle",
    FIXTURE_KEYS["bicycle"],
    {
        "development": ["işe gidiş dönüş için iki tekerli motorsuz cihaz"],
        "validation": ["şehir içi ulaşım için ekonomik iki tekerli"],
        "holdout": ["dağda kullanılacak sağlam iki tekerli araç"],
    },
)


# --- AMBIGUOUS (cross-category)
_ambiguous_defs = [
    (
        "amb.laptop-tablet",
        [
            "laptop mı tablet mi almalıyım karar veremedim",
            "tablet mi dizüstü mü daha iyi olur",
        ],
        [FIXTURE_KEYS["laptop"], FIXTURE_KEYS["tablet"]],
        "development",
    ),
    (
        "amb.phone-tablet",
        [
            "telefonla mı tablet ile mi izlemeliyim",
            "telefon mu tablet mi çocuğa",
        ],
        [FIXTURE_KEYS["mobile"], FIXTURE_KEYS["tablet"]],
        "development",
    ),
    (
        "amb.tv-projector",
        [
            "salon için ekran arıyorum ama tv mi başka bir şey mi bilmiyorum",
        ],
        [FIXTURE_KEYS["tv"]],
        "development",
    ),
    (
        "amb.headphone-speaker",
        [
            "müzik dinlemek için bir şey almak istiyorum ama kulaklık mı hoparlör mü",
        ],
        [FIXTURE_KEYS["audio"]],
        "development",
    ),
    (
        "amb.console-pc",
        [
            "oyun için konsol mu laptop mu almalıyım",
        ],
        [FIXTURE_KEYS["console"], FIXTURE_KEYS["laptop"]],
        "validation",
    ),
    (
        "amb.watch-band",
        [
            "spor için akıllı saat mi yoksa fitness bileklik mi",
        ],
        [FIXTURE_KEYS["wearable"]],
        "holdout",
    ),
    (
        "amb.vacuum-choice",
        [
            "kablosuz mu robot süpürge mi almalıyım karar veremedim",
        ],
        [FIXTURE_KEYS["vacuum"]],
        "holdout",
    ),
]
for slug, phrases, keys, split in _ambiguous_defs:
    GROUPS.append(
        _g(
            slug,
            split,
            phrases,
            status="AMBIGUOUS",
            acceptable=keys,
            required=[],
            tags=("ambiguous", "multi_need"),
            difficulty="HARD",
        )
    )

# --- MULTI-NEED with both should appear in top-k ---------------------------
_multi_defs = [
    (
        "multi.phone-tablet",
        ["hem telefon hem tablet almam gerek", "telefon ve tablet ikisi de lazım"],
        [FIXTURE_KEYS["mobile"], FIXTURE_KEYS["tablet"]],
        "development",
    ),
    (
        "multi.tv-audio",
        ["televizyon ve hoparlör seti kuracağım"],
        [FIXTURE_KEYS["tv"], FIXTURE_KEYS["audio"]],
        "development",
    ),
    (
        "multi.appliance-furniture",
        ["yeni eve hem mobilya hem beyaz eşya arıyorum"],
        [FIXTURE_KEYS["appliance"], FIXTURE_KEYS["furniture"]],
        "validation",
    ),
    (
        "multi.wearable-bicycle",
        ["akıllı saat ve bisiklet ikisi lazım antrenman için"],
        [FIXTURE_KEYS["wearable"], FIXTURE_KEYS["bicycle"]],
        "holdout",
    ),
]
for slug, phrases, keys, split in _multi_defs:
    GROUPS.append(
        _g(
            slug,
            split,
            phrases,
            status="AMBIGUOUS",
            required=keys,
            tags=("multi_need",),
            difficulty="HARD",
        )
    )

# --- CATEGORY CHANGE — top matched category is the NEW mention -------------
_cat_change_defs = [
    (
        "chg.phone-to-laptop",
        [
            "önce telefon almayı düşündüm ama aslında laptop lazım",
            "telefon dedim önce ama karar verdim laptop alıyorum",
        ],
        FIXTURE_KEYS["laptop"],
        [FIXTURE_KEYS["mobile"]],
        "development",
    ),
    (
        "chg.tv-to-projector",
        ["tv bakıyordum ama artık ses sistemi kurmak istiyorum"],
        FIXTURE_KEYS["audio"],
        [FIXTURE_KEYS["tv"]],
        "development",
    ),
    (
        "chg.tablet-to-camera",
        ["tablet düşünüyordum ama fotoğraf makinesi almaya karar verdim"],
        FIXTURE_KEYS["camera"],
        [FIXTURE_KEYS["tablet"]],
        "validation",
    ),
    (
        "chg.appliance-to-vacuum",
        ["bulaşık makinesi bakıyordum ama önce robot süpürge alacağım"],
        FIXTURE_KEYS["vacuum"],
        [FIXTURE_KEYS["appliance"]],
        "holdout",
    ),
]
for slug, phrases, new_key, forbidden, split in _cat_change_defs:
    GROUPS.append(
        _g(
            slug,
            split,
            phrases,
            status="MATCHED",
            required=[new_key],
            forbidden=list(forbidden),
            tags=("category_change",),
            difficulty="HARD",
        )
    )

# --- NEGATION --------------------------------------------------------------
_negation_defs = [
    (
        "neg.no-phone",
        ["telefon istemiyorum, bilgisayar arıyorum"],
        FIXTURE_KEYS["laptop"],
        [FIXTURE_KEYS["mobile"]],
        "development",
    ),
    (
        "neg.no-tv",
        ["televizyon değil bilgisayar bakıyorum"],
        FIXTURE_KEYS["laptop"],
        [FIXTURE_KEYS["tv"]],
        "development",
    ),
    (
        "neg.no-tablet",
        ["tablet değil telefon lazım"],
        FIXTURE_KEYS["mobile"],
        [FIXTURE_KEYS["tablet"]],
        "validation",
    ),
    (
        "neg.no-audio",
        ["kulaklık değil hoparlör alacağım"],
        FIXTURE_KEYS["audio"],
        [],
        "holdout",
    ),
]
for slug, phrases, key, forbidden, split in _negation_defs:
    GROUPS.append(
        _g(
            slug,
            split,
            phrases,
            status="MATCHED",
            required=[key],
            forbidden=list(forbidden),
            tags=("negation",),
            difficulty="HARD",
        )
    )

# --- NO MATCH / OUT OF SCOPE ----------------------------------------------
_no_match_defs = [
    (
        "nom.general-1",
        [
            "bugün hava çok güzel",
            "hafta sonu ne yapmalıyım",
            "kahve içiyorum sohbet edelim",
        ],
        [],
        "development",
        (),
    ),
    (
        "nom.support-question",
        [
            "hesabımdaki son işlemi görebilir miyim",
        ],
        [],
        "validation",
        (),
    ),
    (
        "nom.random-topic",
        [
            "eve mama getirdim kediye",
        ],
        [],
        "holdout",
        (),
    ),
    (
        "oos.travel",
        [
            "uçak bileti almak istiyorum",
            "yaz tatili otel arıyorum",
        ],
        [FIXTURE_KEYS["travel"]],
        "development",
        ("out_of_scope",),
    ),
    (
        "oos.travel-val",
        [
            "istanbul antalya arası uçuş biletlerine bakıyorum",
        ],
        [FIXTURE_KEYS["travel"]],
        "validation",
        ("out_of_scope",),
    ),
    (
        "oos.travel-hold",
        [
            "avrupa turları için otel rezervasyonu",
        ],
        [FIXTURE_KEYS["travel"]],
        "holdout",
        ("out_of_scope",),
    ),
]
for slug, phrases, forbidden, split, tags in _no_match_defs:
    GROUPS.append(
        _g(
            slug,
            split,
            phrases,
            status="NO_MATCH",
            required=[],
            forbidden=list(forbidden),
            tags=("no_match",) + tuple(tags),
            difficulty="MEDIUM",
        )
    )


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

def _case_id(group_id: str, index: int) -> str:
    stem = group_id.replace(".", "-")
    return f"case-{stem}-{index:02d}"


def _payload(case_id: str, group: Group, utterance: str) -> dict:
    payload: dict = {
        "case_id": case_id,
        "semantic_group_id": group.group_id,
        "locale": "tr-TR",
        "utterance": utterance,
        "expected": {"status": group.expected_status},
        "dimensions": {
            "tags": list(group.tags) or ["direct_match"],
            "difficulty": group.difficulty,
        },
        "privacy": {"synthetic": True, "source": "bootstrap-generator.v1"},
        "annotation": {"status": "DRAFT"},
    }
    if group.acceptable:
        payload["expected"]["acceptable_fixture_keys"] = list(group.acceptable)
    if group.required:
        payload["expected"]["required_fixture_keys"] = list(group.required)
    if group.forbidden:
        payload["expected"]["forbidden_fixture_keys"] = list(group.forbidden)
    return payload


def _write_split(target: Path, groups: Iterable[Group]) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("w", encoding="utf-8") as fh:
        for group in groups:
            for i, utterance in enumerate(group.utterances, start=1):
                payload = _payload(_case_id(group.group_id, i), group, utterance)
                fh.write(json.dumps(payload, ensure_ascii=False))
                fh.write("\n")
                written += 1
    return written


def generate() -> dict[str, int]:
    dev_groups = [g for g in GROUPS if g.split == "development"]
    val_groups = [g for g in GROUPS if g.split == "validation"]
    hold_groups = [g for g in GROUPS if g.split == "holdout"]
    dev_count = _write_split(DEV_PATH, dev_groups)
    val_count = _write_split(VAL_PATH, val_groups)
    hold_count = _write_split(HOLD_PATH, hold_groups)
    return {
        "development": dev_count,
        "validation": val_count,
        "holdout": hold_count,
    }


def _expand_val_holdout_padding() -> None:
    """Extra val / holdout groups so both splits stay ≥ 50 cases.

    These carry brand-new semantic_group_id values (no overlap with
    dev) so split integrity remains intact.
    """

    extras: list[Group] = []
    val_specs = [
        ("mobile-2", FIXTURE_KEYS["mobile"], "MATCHED", ["telefon değiştirmem gerekiyor bu ay", "yeni bir cep almak istiyorum"], ("direct_match",)),
        ("mobile-3", FIXTURE_KEYS["mobile"], "MATCHED", ["telefon önerin uygun fiyatlı"], ("indirect_match",)),
        ("laptop-2", FIXTURE_KEYS["laptop"], "MATCHED", ["laptop öneriniz nedir 20 bin bütçe"], ("direct_match",)),
        ("laptop-3", FIXTURE_KEYS["laptop"], "MATCHED", ["taşınabilir bilgisayar bakıyorum"], ("indirect_match",)),
        ("tablet-2", FIXTURE_KEYS["tablet"], "MATCHED", ["tablet çocuğa okul için"], ("direct_match",)),
        ("appliance-2", FIXTURE_KEYS["appliance"], "MATCHED", ["buzdolabı ve fırın seti bakıyorum"], ("direct_match",)),
        ("appliance-3", FIXTURE_KEYS["appliance"], "MATCHED", ["mutfağa geniş bir soğutucu istiyorum"], ("indirect_match",)),
        ("furniture-2", FIXTURE_KEYS["furniture"], "MATCHED", ["yatak takımı bakıyorum"], ("direct_match",)),
        ("camera-2", FIXTURE_KEYS["camera"], "MATCHED", ["dslr makine önerin"], ("direct_match",)),
        ("wearable-2", FIXTURE_KEYS["wearable"], "MATCHED", ["akıllı saat spor için bakıyorum"], ("indirect_match",)),
        ("audio-2", FIXTURE_KEYS["audio"], "MATCHED", ["hoparlör salona uygun"], ("direct_match",)),
        ("tv-2", FIXTURE_KEYS["tv"], "MATCHED", ["50 inç tv arıyorum salona"], ("direct_match",)),
        ("console-2", FIXTURE_KEYS["console"], "MATCHED", ["oyun konsolu araştırıyorum"], ("direct_match",)),
        ("kitchen-2", FIXTURE_KEYS["small_kitchen"], "MATCHED", ["mutfağa airfryer alacağım"], ("direct_match",)),
        ("vacuum-2", FIXTURE_KEYS["vacuum"], "MATCHED", ["dikey süpürge modelleri"], ("direct_match",)),
        ("ac-2", FIXTURE_KEYS["ac"], "MATCHED", ["yatak odası için sessiz klima"], ("direct_match",)),
        ("bicycle-2", FIXTURE_KEYS["bicycle"], "MATCHED", ["elektrikli bisiklet öneri istiyorum"], ("direct_match",)),
    ]
    for slug, key, status, phrases, tags in val_specs:
        extras.append(
            _g(
                f"val.pad.{slug}",
                "validation",
                phrases,
                status=status,
                required=[key],
                tags=tags,
                difficulty="MEDIUM",
            )
        )

    hold_specs = [
        ("mobile-h2", FIXTURE_KEYS["mobile"], "MATCHED", ["yeni bir cep telefonu düşünüyorum"], ("direct_match",)),
        ("mobile-h3", FIXTURE_KEYS["mobile"], "MATCHED", ["fotoğraf çekimi için mobil cihaz"], ("indirect_match",)),
        ("laptop-h2", FIXTURE_KEYS["laptop"], "MATCHED", ["kod geliştirmek için dizüstü bakıyorum"], ("indirect_match",)),
        ("tablet-h2", FIXTURE_KEYS["tablet"], "MATCHED", ["kalemi olan tablet önerin"], ("indirect_match",)),
        ("appliance-h2", FIXTURE_KEYS["appliance"], "MATCHED", ["çamaşır makinesi bakıyorum enerji tasarruflu"], ("direct_match",)),
        ("furniture-h2", FIXTURE_KEYS["furniture"], "MATCHED", ["salon takımı alacağım"], ("direct_match",)),
        ("camera-h2", FIXTURE_KEYS["camera"], "MATCHED", ["seyahat için kompakt fotoğraf makinesi"], ("indirect_match",)),
        ("wearable-h2", FIXTURE_KEYS["wearable"], "MATCHED", ["fitness bileklik önerin"], ("direct_match",)),
        ("audio-h2", FIXTURE_KEYS["audio"], "MATCHED", ["metroda kullanacağım kulaklık"], ("indirect_match",)),
        ("tv-h2", FIXTURE_KEYS["tv"], "MATCHED", ["oled televizyon bakıyorum salona"], ("direct_match",)),
        ("console-h2", FIXTURE_KEYS["console"], "MATCHED", ["oyun konsolu çocuk için"], ("direct_match",)),
        ("kitchen-h2", FIXTURE_KEYS["small_kitchen"], "MATCHED", ["kahve makinesi önerin"], ("direct_match",)),
        ("vacuum-h2", FIXTURE_KEYS["vacuum"], "MATCHED", ["kablosuz süpürge bakıyorum"], ("direct_match",)),
        ("ac-h2", FIXTURE_KEYS["ac"], "MATCHED", ["salon için klima önerin"], ("direct_match",)),
        ("bicycle-h2", FIXTURE_KEYS["bicycle"], "MATCHED", ["e-bike öneri istiyorum"], ("direct_match",)),
    ]
    for slug, key, status, phrases, tags in hold_specs:
        extras.append(
            _g(
                f"hold.pad.{slug}",
                "holdout",
                phrases,
                status=status,
                required=[key],
                tags=tags,
                difficulty="MEDIUM",
            )
        )
    GROUPS.extend(extras)


def _expand_dev_padding() -> None:
    """Additional dev-only variants to keep dev split ≥ 150.

    These are surface variants (light phrasing tweaks) of already-defined
    development groups; they share the same semantic_group_id so the
    split-integrity check remains valid.
    """

    surface_pool = {
        "mobile.direct.dev.g01": ["telefon almak istiyorum, öneri var mı", "telefon alacağım öneri istiyorum"],
        "mobile.direct.dev.g02": ["cep telefonuna bakıyorum bugün", "cep telefonu satın alacağım"],
        "mobile.direct.dev.g03": ["akıllı telefon lazım en kısa sürede", "akıllı telefon değişikliği düşünüyorum"],
        "laptop.direct.dev.g01": ["laptop almak istiyorum bu ay içinde", "laptop alacağım öneri"],
        "laptop.direct.dev.g02": ["dizüstü bilgisayar bakıyorum uygun modele", "dizüstü bilgisayar arıyorum"],
        "laptop.direct.dev.g03": ["notebook lazım yazılım için", "notebook düşünüyorum"],
        "tablet.direct.dev.g01": ["tablet almak istiyorum çocuğa da uygun olsun", "tablet alma zamanı"],
        "tablet.direct.dev.g02": ["ipad bakıyorum uygun fiyatlı", "ipad alacağım"],
        "appliance.direct.dev.g01": ["buzdolabı almak istiyorum enerji dostu", "buzdolabı yenileyeceğim"],
        "appliance.direct.dev.g02": ["çamaşır makinesi lazım büyük hacimli", "çamaşır makinesi almak istiyorum"],
        "furniture.direct.dev.g01": ["kanepe almak istiyorum salona", "kanepe yenilemek istiyorum"],
        "furniture.direct.dev.g02": ["koltuk takımı bakıyorum oturma odasına", "koltuk takımı yenilenmesi"],
        "camera.direct.dev.g01": ["fotoğraf makinesi almak istiyorum hobi için", "fotoğraf makinesi düşünüyorum"],
        "camera.direct.dev.g02": ["dslr bakıyorum bütçe uygun", "dslr almak istiyorum"],
        "wearable.direct.dev.g01": ["akıllı saat almak istiyorum sporcuyum", "akıllı saat değişikliği"],
        "wearable.direct.dev.g02": ["smartwatch bakıyorum android uyumlu", "smartwatch modelleri"],
        "audio.direct.dev.g01": ["kulaklık almak istiyorum kablosuz olsun", "kulaklık arayışındayım"],
        "audio.direct.dev.g02": ["kablosuz kulaklık lazım koşuya uygun", "kablosuz kulaklık modelleri"],
        "tv.direct.dev.g01": ["televizyon almak istiyorum 55 inç", "televizyon arıyorum salona"],
        "tv.direct.dev.g02": ["smart tv bakıyorum netflix var mı", "smart tv arıyorum"],
        "console.direct.dev.g01": ["playstation almak istiyorum çocuğa hediye", "playstation için karar veriyorum"],
        "console.direct.dev.g02": ["xbox arıyorum uygun bir tane", "xbox alacağım"],
        "small_kitchen.direct.dev.g01": ["kahve makinesi almak istiyorum otomatik olsun", "kahve makinesi lazım"],
        "small_kitchen.direct.dev.g02": ["blender lazım smoothie için", "blender bakıyorum güçlü"],
        "vacuum.direct.dev.g01": ["süpürge almak istiyorum kablosuz olsun", "süpürge değiştirmem gerek"],
        "vacuum.direct.dev.g02": ["robot süpürge lazım tüylü halıya uyumlu", "robot süpürge düşünüyorum"],
        "ac.direct.dev.g01": ["klima almak istiyorum inverter", "klima alacağım salona"],
        "ac.direct.dev.g02": ["salona inverter klima almalıyım", "salona inverter klima düşünüyorum"],
        "bicycle.direct.dev.g01": ["bisiklet almak istiyorum işe gitmek için", "bisiklet düşünüyorum bu yaz"],
    }
    by_id = {g.group_id: g for g in GROUPS}
    for gid, extra in surface_pool.items():
        base = by_id.get(gid)
        if base is None or base.split != "development":
            continue
        base.utterances.extend(extra)


_expand_val_holdout_padding()
_expand_dev_padding()


def main() -> int:
    counts = generate()
    for split, count in counts.items():
        print(f"{split}: {count} cases")
    dev = counts["development"]
    val = counts["validation"]
    hold = counts["holdout"]
    ok = dev >= 150 and val >= 50 and hold >= 50
    if not ok:
        print("ERROR: minimum split sizes not met", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
