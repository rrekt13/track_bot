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
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

QUALITY_PATTERNS = [
    (r"\bservice\s*pack\b", "Service Pack (Оригинал)"),
    (r"\bоригинал\b|\boriginal\b|\borig\b", "Оригинал"),
    (r"\bsoft\s*oled\b", "Soft OLED"),
    (r"\bhard\s*oled\b", "Hard OLED"),
    (r"\boled\b", "OLED"),
    (r"\bincell\b|\bin-cell\b", "Incell"),
    (r"\btianma\b", "Tianma"),
    (r"\bgx\b", "GX"),
    (r"\bjk\b", "JK"),
    (r"\bzy\b", "ZY"),
    (r"\bкопия\b|\bcopy\b|\baaa\b", "Копия"),
]


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
    cleaned = text.replace("\xa0", " ").replace(" ", "")
    m = re.search(r"(\d+(?:[.,]\d{1,2})?)", cleaned)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return 0.0


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                return await resp.text(errors="ignore")
            logger.warning(f"Search {url} returned HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Search error for {url}: {e}")
    return ""


def _parse_bitrix_cards(html: str, base_url: str, source_name: str, limit: int = 5) -> list[dict[str, Any]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # Универсальный поиск товарных карточек (Bitrix / интернет-магазины)
    candidates = soup.select(
        ".catalog-item, .product-item, .item_block, .catalog_item, "
        ".product-card, .goods-item, .search-item, article, [data-entity='item']"
    )
    if not candidates:
        candidates = soup.select("div.item, li.item")

    for card in candidates:
        a = card.select_one("a[href*='/catalog/'], a[href*='/product/'], a.title, a.name, h3 a, h4 a, a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen_urls:
            continue

        title_el = card.select_one(".item-title, .product-title, .name, .title, h3, h4") or a
        title = " ".join(title_el.get_text(" ", strip=True).split())
        if len(title) < 4:
            continue

        # Ищем цену внутри карточки
        price_el = card.select_one(
            ".price, .product-price, .item-price, .cost, [class*='price']"
        )
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        if not price_text:
            m_p = re.search(r"(\d[\d\s]{1,7})\s*(?:₽|руб|р\.)", card.get_text(" ", strip=True), re.I)
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
                "title": title[:110],
                "quality": detect_quality(title),
                "price": price_val,
                "in_stock": in_stock,
                "url": full_url,
            }
        )
        if len(results) >= limit:
            break

    return results


async def search_parts_all(query: str) -> dict[str, Any]:
    q_enc = quote_plus(query.strip())
    urls = {
        "master_mobile": f"https://master-mobile.ru/catalog/?q={q_enc}",
        "moba": f"https://moba.ru/catalog/?q={q_enc}",
        "mems": f"https://memstech.ru/search?q={q_enc}",
    }

    async with aiohttp.ClientSession() as session:
        mm_html, moba_html, mems_html = await asyncio.gather(
            _fetch_html(session, urls["master_mobile"]),
            _fetch_html(session, urls["moba"]),
            _fetch_html(session, urls["mems"]),
        )
        if not mems_html or "product" not in mems_html.lower():
            alt_mems = await _fetch_html(session, f"https://memstech.ru/catalog?q={q_enc}")
            if alt_mems:
                mems_html = alt_mems

    mm_items = _parse_bitrix_cards(mm_html, "https://master-mobile.ru", "Master Mobile")
    moba_items = _parse_bitrix_cards(moba_html, "https://moba.ru", "Moba")
    mems_items = _parse_bitrix_cards(mems_html, "https://memstech.ru", "MEMS")

    return {
        "query": query.strip(),
        "search_urls": urls,
        "groups": {
            "Master Mobile": mm_items,
            "Moba": moba_items,
            "MEMS": mems_items,
        },
    }


def match_work_price(query: str, price_rows: list[dict[str, Any]]) -> tuple[str, float]:
    """Подбирает наиболее подходящую стоимость работы по ключевым словам запроса."""
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