import re

def _expand() -> dict[str, list[str]]:
    b: dict[str, list[str]] = {}

    # 1. Apple (2010 - 2026: iPhone 4..17, iPad, MacBook, Watch)
    apple = [
        "Apple iPhone 4", "Apple iPhone 4s", "Apple iPhone 5", "Apple iPhone 5c", "Apple iPhone 5s",
        "Apple iPhone 6", "Apple iPhone 6 Plus", "Apple iPhone 6s", "Apple iPhone 6s Plus",
        "Apple iPhone SE (2016)", "Apple iPhone 7", "Apple iPhone 7 Plus",
        "Apple iPhone 8", "Apple iPhone 8 Plus", "Apple iPhone X", "Apple iPhone XR", "Apple iPhone XS", "Apple iPhone XS Max",
        "Apple iPhone 11", "Apple iPhone 11 Pro", "Apple iPhone 11 Pro Max", "Apple iPhone SE (2020)",
        "Apple iPhone 12 mini", "Apple iPhone 12", "Apple iPhone 12 Pro", "Apple iPhone 12 Pro Max",
        "Apple iPhone 13 mini", "Apple iPhone 13", "Apple iPhone 13 Pro", "Apple iPhone 13 Pro Max", "Apple iPhone SE (2022)",
        "Apple iPhone 14", "Apple iPhone 14 Plus", "Apple iPhone 14 Pro", "Apple iPhone 14 Pro Max",
        "Apple iPhone 15", "Apple iPhone 15 Plus", "Apple iPhone 15 Pro", "Apple iPhone 15 Pro Max",
        "Apple iPhone 16", "Apple iPhone 16 Plus", "Apple iPhone 16 Pro", "Apple iPhone 16 Pro Max", "Apple iPhone 16e",
        "Apple iPhone 17", "Apple iPhone 17 Air", "Apple iPhone 17 Pro", "Apple iPhone 17 Pro Max",
    ]
    for n in range(2, 12):
        apple.append(f"Apple iPad {n}")
    for gen in ["1", "2", "3", "4", "5", "M1", "M2"]:
        apple.append(f"Apple iPad Air {gen}")
    for gen in range(1, 8):
        apple.append(f"Apple iPad mini {gen}")
    for sz in ["9.7", "10.5", "11", "12.9", "13 (M4)"]:
        apple.append(f"Apple iPad Pro {sz}")
    for mb in ["Air 11", "Air 13", "Air M1", "Air M2", "Air M3", "Pro 13", "Pro 14", "Pro 15", "Pro 16", "Pro M1", "Pro M2", "Pro M3", "Pro M4"]:
        apple.append(f"Apple MacBook {mb}")
    for s in range(1, 11):
        apple.append(f"Apple Watch Series {s}")
    apple += ["Apple Watch SE", "Apple Watch SE 2", "Apple Watch Ultra", "Apple Watch Ultra 2"]
    b["Apple"] = apple

    # 2. Samsung (2010 - 2026: S, Note, A, J, M, F, Z Fold/Flip, C, E, Tab)
    sam = [
        "Samsung Galaxy S", "Samsung Galaxy S2", "Samsung Galaxy S3", "Samsung Galaxy S3 mini",
        "Samsung Galaxy S4", "Samsung Galaxy S4 mini", "Samsung Galaxy S5", "Samsung Galaxy S5 mini",
        "Samsung Galaxy S6", "Samsung Galaxy S6 Edge", "Samsung Galaxy S6 Edge+",
        "Samsung Galaxy S7", "Samsung Galaxy S7 Edge", "Samsung Galaxy S8", "Samsung Galaxy S8+",
        "Samsung Galaxy S9", "Samsung Galaxy S9+", "Samsung Galaxy S10e", "Samsung Galaxy S10", "Samsung Galaxy S10+", "Samsung Galaxy S10 Lite",
    ]
    for n in range(20, 26):
        sam += [f"Samsung Galaxy S{n}", f"Samsung Galaxy S{n}+", f"Samsung Galaxy S{n} Ultra", f"Samsung Galaxy S{n} FE"]
    for n in [1, 2, 3, 4, 5, 7, 8, 9, 10, 20]:
        sam.append(f"Samsung Galaxy Note {n}")
        if n >= 10:
            sam.append(f"Samsung Galaxy Note {n}+" if n == 10 else f"Samsung Galaxy Note {n} Ultra")
        if n in (3, 4, 10):
            sam.append(f"Samsung Galaxy Note {n} Lite" if n == 10 else f"Samsung Galaxy Note {n} Neo")
    for n in [3, 5, 7, 8, 9]:
        for yr in ["(2015)", "(2016)", "(2017)", "(2018)"]:
            if not (n >= 8 and yr in ("(2015)", "(2016)")):
                sam.append(f"Samsung Galaxy A{n} {yr}")
    for n in ["01", "02", "02s", "03", "03s", "04", "04e", "04s", "05", "05s", "06",
              "10", "10s", "11", "12", "13", "14", "15", "16",
              "20", "20s", "21", "21s", "22", "23", "24", "25", "26",
              "30", "30s", "31", "32", "33", "34", "35", "36",
              "40", "41", "42", "50", "50s", "51", "52", "52s", "53", "54", "55", "56",
              "60", "70", "71", "72", "73", "80", "90"]:
        sam.append(f"Samsung Galaxy A{n}")
    for j in ["J1", "J1 Mini", "J2", "J2 Prime", "J2 Core", "J3 (2016)", "J3 (2017)", "J4", "J4+", "J5 (2015)", "J5 (2016)", "J5 (2017)", "J6", "J6+", "J7 (2015)", "J7 (2016)", "J7 (2017)", "J8"]:
        sam.append(f"Samsung Galaxy {j}")
    for m in [10, 11, 12, 13, 14, 15, 20, 21, 22, 23, 30, 31, 32, 33, 34, 35, 51, 52, 53, 54, 55, 62]:
        sam.append(f"Samsung Galaxy M{m}")
    for z in range(1, 7):
        sam += [f"Samsung Galaxy Z Fold {z}", f"Samsung Galaxy Z Flip {z}"]
    for t in ["Tab S6", "Tab S6 Lite", "Tab S7", "Tab S7 FE", "Tab S8", "Tab S8+", "Tab S8 Ultra", "Tab S9", "Tab S9 FE", "Tab S9 Ultra", "Tab S10 Ultra", "Tab A7", "Tab A7 Lite", "Tab A8", "Tab A9", "Tab A9+"]:
        sam.append(f"Samsung Galaxy {t}")
    b["Samsung"] = sam

    # 3. Xiaomi / Redmi / POCO (2011 - 2026)
    xia = [
        "Xiaomi Mi 1", "Xiaomi Mi 2", "Xiaomi Mi 3", "Xiaomi Mi 4", "Xiaomi Mi 4i", "Xiaomi Mi 4c",
        "Xiaomi Mi 5", "Xiaomi Mi 5s", "Xiaomi Mi 5X", "Xiaomi Mi 6", "Xiaomi Mi 8", "Xiaomi Mi 8 Lite", "Xiaomi Mi 8 SE",
        "Xiaomi Mi 9", "Xiaomi Mi 9 SE", "Xiaomi Mi 9 Lite", "Xiaomi Mi 9T", "Xiaomi Mi 9T Pro",
        "Xiaomi Mi 10", "Xiaomi Mi 10 Pro", "Xiaomi Mi 10 Lite", "Xiaomi Mi 10T", "Xiaomi Mi 10T Pro", "Xiaomi Mi Note 10", "Xiaomi Mi Note 10 Lite",
        "Xiaomi Mi 11", "Xiaomi Mi 11 Pro", "Xiaomi Mi 11 Ultra", "Xiaomi Mi 11 Lite", "Xiaomi 11T", "Xiaomi 11T Pro",
        "Xiaomi Mi A1", "Xiaomi Mi A2", "Xiaomi Mi A2 Lite", "Xiaomi Mi A3",
        "Xiaomi Mi Max", "Xiaomi Mi Max 2", "Xiaomi Mi Max 3", "Xiaomi Mi Mix 2", "Xiaomi Mi Mix 2S", "Xiaomi Mi Mix 3", "Xiaomi Mi Mix 4",
    ]
    for n in range(12, 16):
        xia += [f"Xiaomi {n}", f"Xiaomi {n} Pro", f"Xiaomi {n} Ultra", f"Xiaomi {n} Lite", f"Xiaomi {n}T", f"Xiaomi {n}T Pro"]
    for rn in ["1S", "2", "3", "3S", "3 Pro", "4", "4A", "4X", "5", "5A", "5 Plus", "6", "6A", "7", "7A", "8", "8A", "9", "9A", "9C", "9T", "10", "10A", "10C", "12", "12C", "13", "13C", "14C", "A1", "A2", "A3"]:
        xia.append(f"Xiaomi Redmi {rn}")
    for n in range(2, 15):
        xia.append(f"Xiaomi Redmi Note {n}")
        if n >= 4:
            xia.append(f"Xiaomi Redmi Note {n} Pro")
        if n >= 8:
            xia.append(f"Xiaomi Redmi Note {n}S")
        if n >= 11:
            xia.append(f"Xiaomi Redmi Note {n} Pro+")
        if n in (4, 7, 8, 9):
            xia.append(f"Xiaomi Redmi Note {n}T" if n == 8 else f"Xiaomi Redmi Note {n}X" if n == 4 else f"Xiaomi Redmi Note {n}s")
    for p in ["F1", "F2 Pro", "F3", "F3 GT", "F4", "F4 GT", "F5", "F5 Pro", "F6", "F6 Pro", "F7", "F7 Pro",
              "X2", "X3", "X3 NFC", "X3 Pro", "X3 GT", "X4 Pro", "X4 GT", "X5", "X5 Pro", "X6", "X6 Pro", "X7", "X7 Pro",
              "M2", "M3", "M3 Pro", "M4 Pro", "M5", "M5s", "M6", "M6 Pro", "M7 Pro", "C3", "C31", "C40", "C51", "C55", "C65", "C75"]:
        xia.append(f"POCO {p}")
    b["Xiaomi / Redmi / POCO"] = xia

    # 4. Huawei & Honor (2012 - 2026)
    hua = []
    for p in [6, 7, 8, 9, 10, 20, 30, 40, 50, 60]:
        hua += [f"Huawei P{p}", f"Huawei P{p} Lite", f"Huawei P{p} Pro"]
    for p in ["P Smart", "P Smart 2019", "P Smart Z", "P Smart 2021", "Pura 70", "Pura 70 Pro", "Pura 70 Ultra", "Pura 80", "Pura 80 Pro"]:
        hua.append(f"Huawei {p}")
    for m in [7, 8, 9, 10, 20, 30, 40, 50, 60, 70]:
        hua += [f"Huawei Mate {m}", f"Huawei Mate {m} Lite", f"Huawei Mate {m} Pro"]
    for nv in ["2", "2i", "3", "3i", "4", "5T", "7i", "8", "8i", "9", "9 SE", "10", "10 Pro", "11", "11 Pro", "11i", "12", "12s", "12 Pro", "13"]:
        hua.append(f"Huawei Nova {nv}")
    for y in ["Y3 2017", "Y5 2018", "Y5 2019", "Y5p", "Y6 2018", "Y6 2019", "Y6p", "Y6s", "Y7 2019", "Y7p", "Y8p", "Y9 2019", "Y9 Prime 2019", "Y9s"]:
        hua.append(f"Huawei {y}")
    for h in ["4C", "4X", "5A", "5C", "5X", "6", "6A", "6X", "7", "7A", "7C", "7X", "8", "8A", "8C", "8S", "8X", "8X Max", "9", "9A", "9C", "9S", "9X", "9X Pro",
              "10", "10i", "10 Lite", "20", "20 Lite", "20 Pro", "20S", "30", "30i", "30 Pro", "30 Pro+", "50", "50 Lite", "70", "90", "90 Lite", "200", "200 Lite", "200 Pro", "300", "300 Pro",
              "X5", "X6", "X6a", "X7", "X7a", "X7b", "X8", "X8a", "X8b", "X9a", "X9b", "X9c", "Magic 4 Pro", "Magic 5 Pro", "Magic 6 Pro", "Magic 7 Pro", "Play"]:
        hua.append(f"Honor {h}")
    b["Huawei / Honor"] = hua

    # 5. Realme / OPPO / OnePlus / Vivo
    bbk = []
    for r in ["3", "3 Pro", "5", "5 Pro", "6", "6i", "6 Pro", "7", "7i", "7 Pro", "8", "8i", "8 Pro", "9", "9i", "9 Pro", "9 Pro+",
              "10", "10 Pro", "10 Pro+", "11", "11 Pro", "11 Pro+", "12", "12+", "12 Pro", "12 Pro+", "13", "13+", "13 Pro", "13 Pro+",
              "C11", "C15", "C21", "C21Y", "C25", "C30", "C31", "C33", "C35", "C51", "C53", "C55", "C63", "C65", "C67",
              "GT", "GT Master Edition", "GT Neo 2", "GT Neo 3", "GT 3", "GT 5", "GT 6", "GT 6T", "Narzo 30", "Narzo 50", "Narzo 60"]:
        bbk.append(f"Realme {r}")
    for o in ["A12", "A15", "A16", "A17", "A18", "A31", "A38", "A52", "A53", "A54", "A57", "A58", "A72", "A74", "A77", "A78", "A91", "A96", "A98",
              "F5", "F7", "F9", "F11", "F11 Pro", "Reno 2", "Reno 4", "Reno 5", "Reno 6", "Reno 7", "Reno 8", "Reno 8T", "Reno 10", "Reno 10 Pro", "Reno 11", "Reno 11 F", "Reno 12", "Reno 12 Pro",
              "Find X2 Pro", "Find X3 Pro", "Find X5 Pro", "Find X6 Pro", "Find X7 Ultra", "Find X8 Pro"]:
        bbk.append(f"OPPO {o}")
    for op in ["One", "2", "X", "3", "3T", "5", "5T", "6", "6T", "7", "7 Pro", "7T", "7T Pro", "8", "8 Pro", "8T", "9", "9 Pro", "9R", "10 Pro", "10T", "11", "11R", "12", "12R", "13", "13R",
               "Nord", "Nord N10", "Nord N100", "Nord 2", "Nord 2T", "Nord 3", "Nord 4", "Nord CE", "Nord CE 2", "Nord CE 3", "Nord CE 4", "Open"]:
        bbk.append(f"OnePlus {op}")
    for v in ["V15", "V17", "V19", "V20", "V21", "V23", "V25", "V27", "V29", "V30", "V40",
              "X50 Pro", "X60 Pro", "X70 Pro", "X80 Pro", "X90 Pro", "X100 Pro", "X200 Pro",
              "Y11", "Y12", "Y15", "Y17", "Y19", "Y20", "Y21", "Y22", "Y27", "Y31", "Y33s", "Y35", "Y36", "Y53s", "Y76", "Y100"]:
        bbk.append(f"Vivo {v}")
    b["Realme / OPPO / OnePlus / Vivo"] = bbk

    # 6. Google / Sony / LG / HTC / Motorola / Nokia / ASUS / Meizu / Lenovo
    classic = [
        "Google Nexus S", "Google Galaxy Nexus", "Google Nexus 4", "Google Nexus 5", "Google Nexus 6", "Google Nexus 5X", "Google Nexus 6P",
        "Google Pixel", "Google Pixel XL", "Google Pixel 2", "Google Pixel 2 XL", "Google Pixel 3", "Google Pixel 3 XL", "Google Pixel 3a",
        "Google Pixel 4", "Google Pixel 4 XL", "Google Pixel 4a", "Google Pixel 5", "Google Pixel 5a",
        "Google Pixel 6", "Google Pixel 6 Pro", "Google Pixel 6a", "Google Pixel 7", "Google Pixel 7 Pro", "Google Pixel 7a",
        "Google Pixel 8", "Google Pixel 8 Pro", "Google Pixel 8a", "Google Pixel 9", "Google Pixel 9 Pro", "Google Pixel 9 Pro XL", "Google Pixel 9 Pro Fold",
    ]
    for sx in ["Z", "Z1", "Z1 Compact", "Z2", "Z3", "Z3 Compact", "Z5", "Z5 Compact", "Z5 Premium",
               "X", "XA", "XA1", "XA1 Ultra", "XA2", "XZ", "XZs", "XZ1", "XZ1 Compact", "XZ2", "XZ3",
               "1", "1 II", "1 III", "1 IV", "1 V", "1 VI", "5", "5 II", "5 III", "5 IV", "5 V", "10", "10 II", "10 III", "10 IV", "10 V", "10 VI"]:
        classic.append(f"Sony Xperia {sx}")
    for lg in ["G2", "G3", "G4", "G5", "G6", "G7 ThinQ", "G8 ThinQ", "V10", "V20", "V30", "V40 ThinQ", "V50 ThinQ", "V60 ThinQ", "Velvet", "Wing", "Q6", "Q7", "K10", "K40", "K50"]:
        classic.append(f"LG {lg}")
    for htc in ["Desire HD", "Sensation", "One X", "One M7", "One M8", "One M9", "One A9", "10", "U11", "U11+", "U12+", "Desire 626", "Desire 820", "Desire 20 Pro"]:
        classic.append(f"HTC {htc}")
    for mot in ["Moto G", "Moto G4", "Moto G5", "Moto G6", "Moto G7", "Moto G8", "Moto G9", "Moto G10", "Moto G22", "Moto G32", "Moto G54", "Moto G73", "Moto G84",
                "Moto E6", "Moto E7", "Moto Z", "Moto X", "Edge 20", "Edge 30", "Edge 30 Ultra", "Edge 40", "Edge 40 Pro", "Edge 50 Pro", "Edge 50 Ultra", "Razr 40 Ultra", "Razr 50 Ultra"]:
        classic.append(f"Motorola {mot}")
    for nok in ["Lumia 520", "Lumia 630", "Lumia 730", "Lumia 830", "Lumia 930", "Lumia 950",
                "3", "3.1", "5", "5.1", "5.3", "5.4", "6", "6.1", "7 Plus", "7.2", "8", "8.1", "8.3", "G10", "G20", "G21", "G22", "G42", "G60", "X10", "X20", "X30"]:
        classic.append(f"Nokia {nok}")
    for asu in ["ZenFone 2", "ZenFone 3", "ZenFone 4", "ZenFone 5", "ZenFone 6", "ZenFone 7", "ZenFone 8", "ZenFone 9", "ZenFone 10", "ZenFone 11 Ultra",
                "ROG Phone", "ROG Phone 2", "ROG Phone 3", "ROG Phone 5", "ROG Phone 6", "ROG Phone 7", "ROG Phone 8", "ROG Phone 9"]:
        classic.append(f"ASUS {asu}")
    for mz in ["MX3", "MX4", "MX5", "MX6", "M2 Note", "M3 Note", "M3s", "M5", "M5s", "M5 Note", "M6", "M6 Note", "M6T", "Pro 5", "Pro 6", "Pro 7", "15", "16th", "16s", "17", "18", "20", "21"]:
        classic.append(f"Meizu {mz}")
    for ln in ["P780", "S660", "S850", "A5000", "A6000", "A7000", "K5", "K5 Note", "K6", "K6 Note", "Vibe P1", "Vibe P1m", "P2", "Z5", "Z6 Pro", "Legion Duel", "Legion Y700"]:
        classic.append(f"Lenovo {ln}")
    b["Google / Sony / LG / HTC / Motorola / Nokia / ASUS / Meizu / Lenovo"] = classic

    # 7. Tecno / Infinix / ZTE / Nothing / Rugged / Other Android
    other = []
    for tc in ["Pop 5", "Pop 6", "Pop 7", "Pop 8", "Spark 6", "Spark 7", "Spark 8C", "Spark 9", "Spark 10", "Spark 10 Pro", "Spark 20", "Spark 20 Pro", "Spark 20 Pro+", "Spark 30", "Spark 30 Pro",
               "Camon 16", "Camon 17", "Camon 18", "Camon 19", "Camon 20", "Camon 20 Pro", "Camon 30", "Camon 30 Pro",
               "Pova 2", "Pova 3", "Pova 4", "Pova 5", "Pova 5 Pro", "Pova 6", "Pova 6 Pro", "Phantom X2 Pro", "Phantom V Fold", "Phantom V Fold 2"]:
        other.append(f"Tecno {tc}")
    for inf in ["Smart 5", "Smart 6", "Smart 7", "Smart 8", "Hot 10", "Hot 11", "Hot 12", "Hot 20", "Hot 30", "Hot 40", "Hot 40 Pro", "Hot 50 Pro+",
                "Note 10", "Note 11", "Note 12", "Note 30", "Note 30 Pro", "Note 40", "Note 40 Pro", "Note 40 Pro+", "Zero 20", "Zero 30", "Zero 40", "GT 10 Pro", "GT 20 Pro"]:
        other.append(f"Infinix {inf}")
    for zte in ["Blade A3", "Blade A5", "Blade A7", "Blade A31", "Blade A51", "Blade A53", "Blade A71", "Blade A73", "Blade V30", "Blade V40", "Blade V50",
                "Axon 30", "Axon 40 Ultra", "Nubia RedMagic 7", "Nubia RedMagic 8 Pro", "Nubia RedMagic 9 Pro", "Nubia Z50", "Nubia Z60 Ultra"]:
        other.append(f"ZTE {zte}")
    other += [
        "Nothing Phone (1)", "Nothing Phone (2)", "Nothing Phone (2a)", "Nothing Phone (2a) Plus", "CMF Phone 1",
        "Blackview A55", "Blackview A80", "Blackview A95", "Blackview BV6600", "Blackview BV8800", "Blackview BV8900", "Blackview BV9200", "Blackview BV9300",
        "Doogee S88 Pro", "Doogee S96 Pro", "Doogee S98", "Doogee S100", "Doogee S110", "Doogee V20", "Doogee V30",
        "Ulefone Armor 11", "Ulefone Armor 14", "Ulefone Armor 17 Pro", "Ulefone Armor 18T", "Ulefone Armor 22", "Ulefone Armor 24",
        "Oukitel WP19", "Oukitel WP21", "Oukitel WP33 Pro", "Cubot KingKong 9", "Cubot KingKong Power",
    ]
    b["Tecno / Infinix / ZTE / Nothing / Защищённые"] = other

    # 8. Игровые консоли (QHEAD)
    b["Консоли (QHEAD)"] = [
        "Nintendo Switch 2", "Nintendo Switch OLED", "Nintendo Switch V2", "Nintendo Switch V1", "Nintendo Switch Lite",
        "Nintendo New 3DS XL", "Nintendo New 3DS", "Nintendo 3DS XL", "Nintendo 3DS", "Nintendo New 2DS XL", "Nintendo 2DS",
        "Nintendo DSi XL", "Nintendo DSi", "Nintendo DS Lite",
        "Sony PlayStation 5 Pro", "Sony PlayStation 5 Slim", "Sony PlayStation 5",
        "Sony PlayStation 4 Pro", "Sony PlayStation 4 Slim", "Sony PlayStation 4",
        "Sony PlayStation 3 Super Slim", "Sony PlayStation 3 Slim",
        "Sony PlayStation Vita 2000", "Sony PlayStation Vita 1000", "Sony PSP E1000", "Sony PSP 3000", "Sony PSP 2000", "Sony PSP 1000",
        "Valve Steam Deck OLED", "Valve Steam Deck LCD",
        "ASUS ROG Ally X", "ASUS ROG Ally", "Lenovo Legion Go", "MSI Claw",
        "Microsoft Xbox Series X", "Microsoft Xbox Series S", "Microsoft Xbox One X", "Microsoft Xbox One S", "Microsoft Xbox 360",
        "DualSense Edge (PS5)", "DualSense (Геймпад PS5)", "DualShock 4 (Геймпад PS4)", "Nintendo Joy-Con", "Nintendo Switch Pro Controller",
    ]
    return b


DEVICES_BY_BRAND: dict[str, list[str]] = _expand()
ALL_DEVICES: list[str] = [dev for devs in DEVICES_BY_BRAND.values() for dev in devs]

_RU_EN_MAP = {
    "айфон": "iphone",
    "айпад": "ipad",
    "макбук": "macbook",
    "самсунг": "samsung",
    "галакси": "galaxy",
    "сяоми": "xiaomi",
    "ксяоми": "xiaomi",
    "редми": "redmi",
    "поко": "poco",
    "хонор": "honor",
    "хуавей": "huawei",
    "риалми": "realme",
    "реалми": "realme",
    "оппо": "oppo",
    "виво": "vivo",
    "ванплас": "oneplus",
    "пиксель": "pixel",
    "нексус": "nexus",
    "сони": "sony",
    "иксперия": "xperia",
    "лджи": "lg",
    "моторола": "motorola",
    "нокиа": "nokia",
    "асус": "asus",
    "мейзу": "meizu",
    "леново": "lenovo",
    "текно": "tecno",
    "инфиникс": "infinix",
    "свитч": "switch",
    "нинтендо": "nintendo",
    "плойка": "playstation",
    "пс5": "ps5",
    "пс4": "ps4",
    "стимдек": "steam deck",
    "про": "pro",
    "макс": "max",
    "мини": "mini",
    "плюс": "plus",
    "ультра": "ultra",
    "лайт": "lite",
    "олед": "oled",
    "ноут": "note",
}


def search_devices(query: str, limit: int = 12) -> list[str]:
    raw = query.strip().lower()
    if not raw:
        return []
    for ru, en in _RU_EN_MAP.items():
        raw = re.sub(rf"\b{ru}\b", en, raw)
    tokens = [t for t in re.split(r"[\s/]+", raw) if t]
    if not tokens:
        return []

    scored: list[tuple[int, str]] = []
    for dev in ALL_DEVICES:
        low = dev.lower()
        if all(tok in low for tok in tokens):
            score = 100 - len(dev) + (20 if low.startswith(tokens[0]) else 0)
            scored.append((score, dev))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [d for _, d in scored[:limit]]