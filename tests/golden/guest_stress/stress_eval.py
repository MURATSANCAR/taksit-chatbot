"""Large-scale guest need-extraction stress harness.

Generates thousands of labeled Turkish scenarios across the full matrix:
  intent clear / unclear · budget present / absent · product name present / absent
  · model+spec-number traps · diacritics / ASCII · colloquial phrasing · noise.

Measures category + budget accuracy and prints sample failures. Run:
    python tests/golden/guest_stress/stress_eval.py [N]
"""

from __future__ import annotations

import random
import sys

sys.path.insert(0, "src")
from taksitlio.guest.need_extraction import parse_budget, resolve_category  # noqa: E402


# --- Unambiguous product surface forms per catalog code -----------------------
CATEGORY_SURFACES: dict[str, list[str]] = {
    "1": ["telefon", "cep telefonu", "akıllı telefon", "iphone", "iphone 15",
          "iphone 15 pro max", "samsung galaxy", "galaxy s24 ultra", "xiaomi",
          "redmi note 13", "android telefon", "telefonu"],
    "2": ["airfryer", "air fryer", "fritöz", "blender", "mikser", "tost makinesi",
          "kahve makinesi", "robot süpürge", "süpürge", "ütü", "su ısıtıcı"],
    "3": ["bilgisayar", "laptop", "notebook", "dizüstü", "masaüstü", "macbook",
          "gaming laptop", "monitör"],
    "4": ["tablet", "ipad", "galaxy tab", "lenovo tab"],
    "5": ["televizyon", "tv", "smart tv", "led tv", "oled tv", "soundbar",
          "ses sistemi", "hoparlör"],
    "6": ["çamaşır makinesi", "bulaşık makinesi", "kurutma makinesi", "çamaşır",
          "bulaşık"],
    "7": ["buzdolabı", "buz dolabı", "derin dondurucu", "fırın", "mikrodalga",
          "ankastre ocak"],
    "8": ["tıraş makinesi", "epilasyon aleti", "saç kurutma makinesi",
          "fön makinesi", "saç düzleştirici"],
    "9": ["klima", "split klima", "portatif klima", "kombi", "vantilatör",
          "ısıtıcı"],
    "10": ["kulaklık", "airpods", "bluetooth kulaklık", "powerbank", "şarj aleti",
           "telefon kılıfı", "ekran koruyucu"],
    "11": ["playstation", "playstation 5", "ps5", "xbox", "xbox series x",
           "nintendo switch", "oyun konsolu"],
    "12": ["akıllı saat", "smartwatch", "apple watch", "galaxy watch", "mi band",
           "akıllı bileklik"],
    "13": ["yazıcı", "tarayıcı", "lazer yazıcı", "printer", "scanner"],
    "14": ["fotoğraf makinesi", "kamera", "dslr", "aynasız kamera", "gopro",
           "objektif"],
    "15": ["mobilya", "koltuk", "kanepe", "yemek masası", "gardırop", "kitaplık"],
    "16": ["yatak", "baza", "yatak seti", "ortopedik yatak", "yaylı yatak"],
    "17": ["gözlük", "güneş gözlüğü", "numaralı gözlük", "optik gözlük"],
    "18": ["kol saati", "duvar saati", "cep saati"],
    "20": ["yenilenmiş telefon", "yenilenmiş iphone", "refurbished telefon"],
    "21": ["motosiklet", "scooter", "moped"],
    "22": ["tansiyon aleti", "nebulizatör", "termometre", "ateş ölçer"],
    "23": ["tatil", "seyahat", "uçak bileti", "otel rezervasyonu", "tur paketi"],
    "24": ["dil kursu", "online kurs", "sertifika programı", "eğitim paketi"],
    "25": ["lastik", "akü", "yedek parça", "jant", "fren balata"],
    "27": ["market alışverişi", "süpermarket alışverişi", "market"],
    "28": ["kolye", "yüzük", "bilezik", "küpe", "pırlanta yüzük", "mücevher"],
}

# --- Budget surface forms: (text, value) --------------------------------------
_SPELLED = {3: "üç", 5: "beş", 8: "sekiz", 10: "on", 15: "on beş", 20: "yirmi",
            25: "yirmi beş", 30: "otuz", 35: "otuz beş", 40: "kırk", 50: "elli",
            60: "altmış", 75: "yetmiş beş", 100: "yüz"}
_AMOUNTS = [3, 5, 8, 10, 15, 20, 25, 30, 35, 40, 50, 60, 75, 100]


def budget_forms(a: int) -> list[tuple[str, float]]:
    v = a * 1000.0
    forms = [
        (f"{a} bin", v), (f"{a} bin tl", v), (f"{a}bin", v), (f"{a}.000 tl", v),
        (f"{a}000", v), (f"{a}k", v), (f"bütçem {a} bin", v),
        (f"{a} bin civarı", v), (f"yaklaşık {a} bin", v), (f"en fazla {a} bin", v),
    ]
    if a in _SPELLED:
        forms.append((f"{_SPELLED[a]} bin", v))
    return forms


# --- Templates ----------------------------------------------------------------
PROD_BUDGET = [
    "{p} alacağım, bütçem {b}", "{p} alıcam {b}", "{b} {p} arıyorum",
    "{p} lazım {b}", "eşime {p} alacağım {b} civarı", "{p} bakıyorum {b}",
    "acil {p} lazım bütçe {b}", "{p} almak istiyorum {b} civarı",
    "merhaba {p} için {b} düşünüyorum", "{p} istiyorum {b}",
]
PROD_ONLY = ["{p} almak istiyorum", "{p} lazım", "{p} arıyorum", "{p} bakıyorum",
             "bir {p} alacağım"]
BUDGET_ONLY = ["bütçem {b} ama ne alsam bilmiyorum", "{b} param var",
               "elimde {b} var", "{b} bütçeyle bir şeyler almak istiyorum"]
TRAP = [
    ("iphone 15 pro alıcam {b}", "1"), ("samsung s24 ultra {b}", "1"),
    ("128 gb iphone {b}", "1"), ("playstation 5 {b}", "11"),
    ("55 inç televizyon {b}", "5"), ("galaxy a54 telefon {b}", "1"),
    ("14 pro max {b} bütçe", "1"), ("256 gb macbook {b}", "3"),
]
NOISE = ["merhaba", "teşekkürler", "nasılsın", "bugün hava güzel",
         "taksit nasıl işliyor", "üye olmadan bakabilir miyim", "iyi günler"]


def gen(n: int, seed: int = 7):
    rng = random.Random(seed)
    cases = []
    codes = list(CATEGORY_SURFACES)
    while len(cases) < n:
        r = rng.random()
        if r < 0.55:  # product + budget
            code = rng.choice(codes)
            p = rng.choice(CATEGORY_SURFACES[code])
            bt, bv = rng.choice(budget_forms(rng.choice(_AMOUNTS)))
            t = rng.choice(PROD_BUDGET).format(p=p, b=bt)
            cases.append((t, code, bv))
        elif r < 0.72:  # product only (no budget)
            code = rng.choice(codes)
            p = rng.choice(CATEGORY_SURFACES[code])
            cases.append((rng.choice(PROD_ONLY).format(p=p), code, None))
        elif r < 0.84:  # budget only (no clear product)
            bt, bv = rng.choice(budget_forms(rng.choice(_AMOUNTS)))
            cases.append((rng.choice(BUDGET_ONLY).format(b=bt), None, bv))
        elif r < 0.95:  # trap (model/spec numbers)
            tmpl, code = rng.choice(TRAP)
            bt, bv = rng.choice(budget_forms(rng.choice(_AMOUNTS)))
            cases.append((tmpl.format(b=bt), code, bv))
        else:  # noise
            cases.append((rng.choice(NOISE), None, None))
    return cases


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    cases = gen(n, seed=seed)
    cat_tot = cat_ok = bud_tot = bud_ok = 0
    cat_fail, bud_fail = [], []
    for text, exp_code, exp_bud in cases:
        hit = resolve_category(text)
        got_code = hit.category_code if hit else None
        if exp_code is not None:
            cat_tot += 1
            if got_code == exp_code:
                cat_ok += 1
            elif len(cat_fail) < 25:
                cat_fail.append((text, exp_code, got_code))
        got_bud = parse_budget(text)
        bud_tot += 1
        if got_bud == exp_bud:
            bud_ok += 1
        elif len(bud_fail) < 25:
            bud_fail.append((text, exp_bud, got_bud))

    print(f"N={len(cases)}")
    print(f"CATEGORY  {cat_ok}/{cat_tot} = {cat_ok/cat_tot:.4f}")
    print(f"BUDGET    {bud_ok}/{bud_tot} = {bud_ok/bud_tot:.4f}")
    if cat_fail:
        print("\n-- category failures (sample) --")
        for t, e, g in cat_fail:
            print(f"   exp={e:<3} got={str(g):<5} | {t}")
    if bud_fail:
        print("\n-- budget failures (sample) --")
        for t, e, g in bud_fail:
            print(f"   exp={str(e):<9} got={str(g):<9} | {t}")


if __name__ == "__main__":
    main()
