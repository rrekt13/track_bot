import asyncio
import json
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
    cleaned = str(text).replace("\xa0", "").replace(" ", "")
    m = re.search(r"(\d{2,7}(?:[.,]\d{1,2})?)", cleaned)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return 0.0


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> tuple[int, str]:
    try:
        timeout = aiohttp.ClientTimeout(total=5.5, connect=2.5, sock_read=3.5)
        async with session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True) as resp:
            txt = await resp.text(errors="ignore")
            logger.info(f"Parts fetch {url} -> HTTP {resp.status}, len={len(txt)}")
            return resp.status, txt
    except Exception as e:
        logger.warning(f"Parts fetch error {url}: {e}")
        return 0, ""


def _walk_json_products(obj: Any, base_url: str, source_name: str, out: list[dict[str, Any]], seen: set[str], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(obj, list):
        for item in obj:
            _walk_json_products(item, base_url, source_name, out, seen, limit)
            if len(out) >= limit:
                return
    elif isinstance(obj, dict):
        title = obj.get("name") or obj.get("title") or obj.get("NAME")
        price_raw = obj.get("price") or obj.get("PRICE") or obj.get("minPrice") or obj.get("cost")
        if isinstance(price_raw, dict):
            price_raw = price_raw.get("value") or price_raw.get("price") or price_raw.get("amount")
        slug = obj.get("url") or obj.get("DETAIL_PAGE_URL") or obj.get("slug") or obj.get("link") or obj.get("href")
        if title and price_raw is not None and slug:
            p_val = extract_price_num(str(price_raw))
            t_str = " ".join(str(title).split())
            if p_val > 0 and len(t_str) >= 6:
                href = str(slug).strip()
                if not href.startswith("http") and not href.startswith("/"):
                    href = f"/catalog/{href}"
                full_url = urljoin(base_url, href)
                if full_url not in seen:
                    seen.add(full_url)
                    avail = obj.get("available")
                    qty = obj.get("quantity")
                    in_stock = True
                    if avail is False or qty == 0:
                        in_stock = False
                    out.append({
                        "source": source_name,
                        "title": t_str[:115],
                        "quality": detect_quality(t_str),
                        "price": p_val,
                        "in_stock": in_stock,
                        "url": full_url,
                    })
                    return
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk_json_products(v, base_url, source_name, out, seen, limit)
                if len(out) >= limit:
                    return


def _parse_cards(html: str, base_url: str, source_name: str, limit: int = 6) -> list[dict[str, Any]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # 1. Пробуем стандартные и Bitrix/Aspro классы карточек
    candidates = soup.select(
        ".catalog_item_wrapp, .catalog-block-item, .item_block, .catalog_item, .catalog-item, "
        ".product-item, .product-item-container, .product-card, .goods-item, .search-item, "
        "[data-entity='item'], .item-card, .bx_catalog_item, .list_item_wrapp, .table-view__item, "
        "[class*='ProductCard'], [class*='product-card']"
    )

    # 2. Универсальный fallback: ищем родительские блоки вокруг ссылок /catalog/ или /product/
    if not candidates:
        blocks = []
        for a in soup.select("a[href*='/catalog/'], a[href*='/product/']"):
            href = a.get("href") or ""
            if href.count("/") < 3:
                continue
            parent = a.find_parent(["div", "article", "li"])
            if parent and parent not in blocks:
                blocks.append(parent)
        candidates = blocks

    for card in candidates:
        a = card.select_one(
            "a[href*='/catalog/'], a[href*='/product/'], .item-title a, .product-title a, "
            "h3 a, h4 a, a.title, a.name, a[href]"
        )
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen_urls:
            continue

        title_el = card.select_one(
            ".item-title, .product-title, .name, .title, h3, h4, .bx_catalog_item_title, [class*='title']"
        ) or a
        title = " ".join(title_el.get_text(" ", strip=True).split())
        if len(title) < 6 or title.lower() in ("подробнее", "в корзину", "купить", "быстрый просмотр", "сравнить"):
            continue

        # Ищем цену среди всех возможных элементов и атрибутов (без залипания на пустом первом блоке)
        price_val = 0.0
        for attr in ("data-price", "data-product-price"):
            if card.get(attr):
                price_val = extract_price_num(str(card.get(attr)))
                if price_val > 0:
                    break
        if price_val <= 0:
            meta_p = card.select_one("meta[itemprop='price']")
            if meta_p and meta_p.get("content"):
                price_val = extract_price_num(str(meta_p.get("content")))
        if price_val <= 0:
            for pel in card.select(".price_value, .price, .product-price, .item-price, .cost, .bx_price, [class*='price'], [class*='Price']"):
                cand = extract_price_num(pel.get_text(" ", strip=True))
                if cand > 0:
                    price_val = cand
                    break
        if price_val <= 0:
            m_p = re.search(r"(\d[\d\s\xa0]{1,7})\s*(?:₽|руб|р\.)", card.get_text(" ", strip=True), re.I)
            if m_p:
                price_val = extract_price_num(m_p.group(1))
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

    # 3. Если HTML-карточки не нашлись (SPA / Next.js / JSON-LD) — парсим встроенный JSON
    if not results:
        for sc in soup.select("script#__NEXT_DATA__, script[type='application/ld+json']"):
            raw_json = (sc.string or sc.get_text() or "").strip()
            if not raw_json:
                continue
            try:
                data = json.loads(raw_json)
                _walk_json_products(data, base_url, source_name, results, seen_urls, limit)
            except Exception:
                continue
            if len(results) >= limit:
                break

    return results


async def search_parts_all(raw_query: str) -> dict[str, Any]:
    norm_query = normalize_query(raw_query)
    q_enc = quote_plus(norm_query)
    urls = {
        "moba": f"https://moba.ru/catalog/?q={q_enc}",
    }

    connector = aiohttp.TCPConnector(force_close=True, limit=4)
    async with aiohttp.ClientSession(connector=connector) as session:
        fetches = await asyncio.gather(
            _fetch_html(session, urls["moba"]),
            _fetch_html(session, f"https://moba.ru/search/?q={q_enc}"),
            return_exceptions=True,
        )

    def _safe_res(idx: int) -> tuple[int, str]:
        val = fetches[idx]
        if isinstance(val, tuple) and len(val) == 2:
            return val
        return (0, "")

    moba_st1, moba_h1 = _safe_res(0)
    moba_st2, moba_h2 = _safe_res(1)
    moba_items = _parse_cards(moba_h1, "https://moba.ru", "Moba") or _parse_cards(moba_h2, "https://moba.ru", "Moba")

    return {
        "query": raw_query.strip(),
        "normalized_query": norm_query,
        "search_urls": urls,
        "statuses": {"Moba": moba_st1 or moba_st2},
        "groups": {"Moba": moba_items},
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