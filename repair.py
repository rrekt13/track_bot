import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote_plus, urljoin

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

QUALITY_PATTERNS = [
    (r"\bservice\s*pack\b|\bсервисн\w*\s+оригинал", "Service Pack (Оригинал)"),
    (r"\bоригинал\b|\boriginal\b|\borig\b", "Оригинал"),
    (r"\bpremium\b|\bпремиум\b", "Premium"),
    (r"\bjcid\b", "JCID"),
    (r"\bbattery\s*collection\b", "Battery Collection"),
    (r"\bdeji\b", "DEJI"),
    (r"\bnohon\b", "Nohon"),
    (r"\bfoxconn\b", "Foxconn"),
    (r"\bsoft\s*oled\b", "Soft OLED"),
    (r"\bhard\s*oled\b", "Hard OLED"),
    (r"\boled\b", "OLED"),
    (r"\bincell\b|\bin-cell\b", "Incell"),
    (r"\btianma\b", "Tianma"),
    (r"\bgx\b", "GX"),
    (r"\bjk\b", "JK"),
    (r"\bzy\b", "ZY"),
    (r"\bусиленн\w*\b", "Усиленная"),
    (r"\bкопия\b|\bcopy\b|\baaa\b", "Копия"),
]


def normalize_query(raw: str) -> str:
    """Преобразует разговорные запросы ('АКБ айфон 13', 'экран 11 айфон') в формат каталогов."""
    q = " ".join(raw.strip().split())
    replacements = [
        (r"\bакб\b", "Аккумулятор"),
        (r"\bбатаре[яюй]\b", "Аккумулятор"),
        (r"\bэкран\b", "Дисплей"),
        (r"\bмодуль\b", "Дисплей"),
        (r"\bразъем\b|\bразъём\b|\bгнездо\b", "Шлейф зарядки"),
        (r"\bайфон\b|\bайфона\b|\biphone\b", "iPhone"),
        (r"\bсамсунг\b", "Samsung"),
        (r"\bсяоми\b|\bксяоми\b", "Xiaomi"),
        (r"\bредми\b", "Redmi"),
        (r"\bпоко\b", "Poco"),
        (r"\bхонор\b", "Honor"),
        (r"\bхуавей\b", "Huawei"),
    ]
    for pat, rep in replacements:
        q = re.sub(pat, rep, q, flags=re.IGNORECASE)

    # Перестановка "Дисплей 11 iPhone" -> "Дисплей iPhone 11"
    q = re.sub(r"\b(\d{1,2}(?:\s*(?:pro\s*max|pro|max|plus|mini|se))?)\s+(iPhone)\b", r"\2 \1", q, flags=re.IGNORECASE)
    return " ".join(q.split())


def detect_quality(title: str) -> str:
    low = title.lower()
    found = []
    for pat, label in QUALITY_PATTERNS:
        if re.search(pat, low):
            found.append(label)
    return ", ".join(dict.fromkeys(found)) if found else "Стандарт"


def extract_price_num(text: str) -> float:
    if not text:
        return 0.0
    cleaned = text.replace("\xa0", "").replace(" ", "")
    m = re.search(r"(\d{2,7}(?:[.,]\d{1,2})?)", cleaned)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return 0.0


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> tuple[int, str]:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=9)) as resp:
            txt = await resp.text(errors="ignore")
            logger.info(f"Parts fetch {url} -> HTTP {resp.status}, len={len(txt)}")
            return resp.status, txt
    except Exception as e:
        logger.warning(f"Parts fetch error {url}: {e}")
        return 0, ""


def _parse_cards(html: str, base_url: str, source_name: str, limit: int = 6) -> list[dict[str, Any]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # 1. Пробуем стандартные классы карточек
    candidates = soup.select(
        ".catalog-item, .product-item, .item_block, .catalog_item, .catalog-block-item, "
        ".product-card, .goods-item, .search-item, [data-entity='item'], .item-card, .bx_catalog_item"
    )

    # 2. Универсальный fallback: ищем родительские блоки вокруг ссылок /catalog/
    if not candidates:
        blocks = []
        for a in soup.select("a[href*='/catalog/']"):
            href = a.get("href") or ""
            if href.count("/") < 3:
                continue
            parent = a.find_parent(["div", "article", "li"])
            if parent and parent not in blocks:
                blocks.append(parent)
        candidates = blocks

    for card in candidates:
        a = card.select_one("a[href*='/catalog/'], a[href*='/product/'], h3 a, h4 a, a.title, a.name, a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen_urls:
            continue

        title_el = card.select_one(".item-title, .product-title, .name, .title, h3, h4, .bx_catalog_item_title") or a
        title = " ".join(title_el.get_text(" ", strip=True).split())
        if len(title) < 6 or title.lower() in ("подробнее", "в корзину", "купить"):
            continue

        price_el = card.select_one(".price, .product-price, .item-price, .cost, .bx_price, [class*='price']")
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        if not price_text:
            m_p = re.search(r"(\d[\d\s\xa0]{1,7})\s*(?:₽|руб|р\.)", card.get_text(" ", strip=True), re.I)
            price_text = m_p.group(0) if m_p else ""

        price_val = extract_price_num(price_text)
        if price_val <= 0:
            continue

        card_text_low = card.get_text(" ", strip=True).lower()
        in_stock = "нет в наличии" not in card_text_low and "под заказ" not in card_text_low

        seen_urls.add(full_url)
        results.append(
            {
                "source": source_name,
                "title": title[:115],
                "quality": detect_quality(title),
                "price": price_val,
                "in_stock": in_stock,
                "url": full_url,
            }
        )
        if len(results) >= limit:
            break

    return results


async def search_parts_all(raw_query: str) -> dict[str, Any]:
    norm_query = normalize_query(raw_query)
    q_enc = quote_plus(norm_query)
    urls = {
        "master_mobile": f"https://master-mobile.ru/catalog/?q={q_enc}",
        "moba": f"https://moba.ru/catalog/?q={q_enc}",
        "mems": f"https://memstech.ru/search?q={q_enc}",
    }

    async with aiohttp.ClientSession() as session:
        (mm_st, mm_html), (moba_st, moba_html), (mems_st, mems_html) = await asyncio.gather(
            _fetch_html(session, urls["master_mobile"]),
            _fetch_html(session, urls["moba"]),
            _fetch_html(session, urls["mems"]),
        )
        # Если на master-mobile /catalog/?q= пусто — пробуем /search/?q=
        mm_items = _parse_cards(mm_html, "https://master-mobile.ru", "Master Mobile")
        if not mm_items and mm_st == 200:
            alt_st, alt_html = await _fetch_html(session, f"https://master-mobile.ru/search/?q={q_enc}")
            if alt_st == 200:
                mm_items = _parse_cards(alt_html, "https://master-mobile.ru", "Master Mobile")

        moba_items = _parse_cards(moba_html, "https://moba.ru", "Moba")
        if not moba_items and moba_st == 200:
            alt_st, alt_html = await _fetch_html(session, f"https://moba.ru/search/?q={q_enc}")
            if alt_st == 200:
                moba_items = _parse_cards(alt_html, "https://moba.ru", "Moba")

        mems_items = _parse_cards(mems_html, "https://memstech.ru", "MEMS")

    statuses = {
        "Master Mobile": mm_st,
        "Moba": moba_st,
        "MEMS": mems_st,
    }

    return {
        "query": raw_query.strip(),
        "normalized_query": norm_query,
        "search_urls": urls,
        "statuses": statuses,
        "groups": {
            "Master Mobile": mm_items,
            "Moba": moba_items,
            "MEMS": mems_items,
        },
    }


def match_work_price(query: str, price_rows: list[dict[str, Any]]) -> tuple[str, float]:
    if not price_rows:
        return ("Работа (базовая)", 0.0)

    q_words = set(re.findall(r"[a-zа-яё0-9]+", query.lower()))
    best_row = price_rows[0]
    best_score = -1

    for row in price_rows:
        kw_str = f"{row.get('title', '')} {row.get('keywords', '')}".lower()
        kws = [w.strip() for w in re.split(r"[,;\s]+", kw_str) if w.strip()]
        score = sum(1 for w in kws if w in q_words or any(w in qw for qw in q_words))
        if score > best_score:
            best_score = score
            best_row = row

    return (best_row["title"], float(best_row["work_price"] or 0.0))