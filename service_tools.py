import json
import logging
import os
import re
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

SICKW_API_KEY = os.getenv("SICKW_API_KEY", "").strip()
IFREE_API_KEY = os.getenv("IFREE_API_KEY", "").strip()

APPLE_PRODUCT_MAP = {
    "iPhone8,1": "iPhone 6s",
    "iPhone8,2": "iPhone 6s Plus",
    "iPhone8,4": "iPhone SE (1-го пок.)",
    "iPhone9,1": "iPhone 7",
    "iPhone9,3": "iPhone 7",
    "iPhone9,2": "iPhone 7 Plus",
    "iPhone9,4": "iPhone 7 Plus",
    "iPhone10,1": "iPhone 8",
    "iPhone10,4": "iPhone 8",
    "iPhone10,2": "iPhone 8 Plus",
    "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X",
    "iPhone10,6": "iPhone X",
    "iPhone11,2": "iPhone XS",
    "iPhone11,4": "iPhone XS Max",
    "iPhone11,6": "iPhone XS Max",
    "iPhone11,8": "iPhone XR",
    "iPhone12,1": "iPhone 11",
    "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2020)",
    "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone14,6": "iPhone SE (2022)",
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,5": "iPhone 16e",
}

PANIC_RULES = [
    (
        r"missing\s+sensor\(s\)\s*:\s*[^\n]*\bprs0\b",
        "Датчик давления PRS0 (барометр)",
        "Система не видит датчик давления PRS0 и перезагружает iPhone каждые ~3 минуты.",
        "Замена нижнего шлейфа разъёма зарядки (ставить оригинал / снятый; проверить обвязку коннектора на плате).",
    ),
    (
        r"missing\s+sensor\(s\)\s*:\s*[^\n]*\bmic1\b",
        "Нижний микрофон Mic1",
        "Отсутствует отклик от термодатчика/микрофона Mic1 — типовой перезапуск каждые 3 минуты.",
        "Замена нижнего шлейфа зарядки (на китайских копиях часто нет терморезистора — нужен оригинал/снятый).",
    ),
    (
        r"missing\s+sensor\(s\)\s*:\s*[^\n]*\bmic2\b",
        "Верхний микрофон Mic2",
        "Отсутствует отклик от верхнего/тыльного микрофона Mic2.",
        "Замена верхнего шлейфа кнопки включения / вспышки (проверить элементы вокруг коннектора на наличие сколов).",
    ),
    (
        r"missing\s+sensor\(s\)\s*:\s*[^\n]*(?:tg0b|tg0v|battery|swi)",
        "Линия данных / термодатчик АКБ (SWI / TG0B)",
        "Плата не получает данные о температуре или идентификаторе аккумулятора.",
        "Подкинуть заведомо исправный оригинальный АКБ; проверить коннектор АКБ и линию PMU_TO_BATTERY_SWI на плате.",
    ),
    (
        r"thermalmonitord",
        "Сбой термомониторинга (thermalmonitord)",
        "Служба контроля температуры не получила отклик от одного из датчиков (перезапуск ~раз в 3 минуты).",
        "Поочерёдно отключить и проверить: 1) нижний шлейф зарядки, 2) верхний шлейф кнопки питания/вспышки, 3) верхний шлейф слухового динамика.",
    ),
    (
        r"aop\s+(?:panic|data\s+abort)|appleaop",
        "Сбой на шине AOP (Always-On Processor)",
        "Критическая ошибка сопроцессора датчиков (часто из-за влаги или КЗ на навесных шлейфах).",
        "Проверить и поочерёдно отключить верхний шлейф динамика/датчиков, нижний шлейф зарядки и шлейф кнопок.",
    ),
    (
        r"\bi2c[0-4]\b|s5l89\w*i2c",
        "Ошибка шины I2C",
        "Просадка или отсутствие ответа на шине I2C (датчики, контроллер заряда, камеры, аудио, NFC).",
        "Отключать по очереди камеры, верхний шлейф и шлейф зарядки. Если на голой плате ошибка остаётся — замер падений напряжения на линиях I2C.",
    ),
    (
        r"\bans2\b|ememory|\bnand\b|\bnvme\b",
        "Память NAND / контроллер ANS2",
        "Ошибка обмена данными с флеш-памятью NAND или её цепями питания.",
        "Требуется диагностика питания NAND, реболл или замена микросхемы NAND с переносом данных программатором.",
    ),
    (
        r"smc\s+panic|smc\s+assert",
        "Контроллер питания SMC / PMU",
        "Аппаратное срабатывание защиты или сбой контроллера управления питанием SMC.",
        "Проверить АКБ и шлейф зарядки; при повторении — диагностика обвязки КП (PMU) и линий питания на материнской плате.",
    ),
    (
        r"baseband|applebaseband|mav\d+|ice\d+|modem",
        "Модемная часть (Baseband)",
        "Сбой модема сотовой связи или его цепей питания.",
        "Проверить наличие IMEI и прошивки модема в настройках. Возможен отвал модема/трансивера или пробой питания RF (BGA-ремонт).",
    ),
    (
        r"tristar|hydra|sn2501|sn2600",
        "Контроллер зарядки / USB (Tristar / Hydra)",
        "Сбой микросхемы управления зарядкой и распознаванием кабеля.",
        "Проверить потребление тока от ЛБП, подкинуть шлейф зарядки; при подтверждении — замена контроллера Tristar/Hydra на плате.",
    ),
    (
        r"sep\s+rom|applesep|sep\s+panic",
        "Secure Enclave (SEP)",
        "Ошибка модуля безопасности (связан с Touch ID / Face ID и шифрованием).",
        "Проверить шлейф кнопки Home / Face ID на повреждения и влагу; при проблеме на плате — диагностика питания SEP.",
    ),
    (
        r"applebcmwlan|\bwlan\b|\bwifi\b",
        "Модуль Wi-Fi / Bluetooth",
        "Сбой драйвера или отказ микросхемы Wi-Fi/BT.",
        "Проверить питание модуля Wi-Fi/BT; при отвале или КЗ — реболл/замена чипа Wi-Fi с отвязкой/прошивкой.",
    ),
    (
        r"applecamin|\bisp\b|cameralock",
        "Камера / линии ISP",
        "Сбой инициализации основной или фронтальной камеры.",
        "Поочерёдно отключить основную и фронтальную камеры; заменить неисправный модуль камеры или проверить питание камер.",
    ),
    (
        r"audio|codec|cs42l",
        "Аудиокодек / цепь звука",
        "Ошибка звукового контроллера (особенно частая болячка на iPhone 7 / 7 Plus).",
        "Проверить нижний и верхний шлейфы; при проблеме с аудиокодеком — реболл/замена микросхемы аудиокодека и восстановление пятаков.",
    ),
    (
        r"userspace\s+watchdog\s+timeout",
        "Таймаут системной службы (Watchdog)",
        "iOS принудительно перезагрузила устройство из-за зависания системного процесса.",
        "Проверить, какие датчики или шлейфы не отвечают (смотреть строки Missing sensor / service выше); протестировать без навесных шлейфов.",
    ),
    (
        r"data\s+abort|synchronous\s+exception|kernel\s+data\s+abort",
        "Критическое исключение ядра (Data Abort)",
        "Аппаратный сбой чтения/записи памяти или периферийной шины.",
        "Проверить устройство без навесных шлейфов (дисплей/шлейф зарядки/верхний шлейф). Если падает на голой плате — диагностика CPU/RAM/NAND.",
    ),
]


def luhn_ok(imei: str) -> bool:
    if not re.fullmatch(r"\d{15}", imei):
        return False
    total = 0
    for idx, ch in enumerate(imei):
        d = int(ch)
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


async def check_imei_info(raw_code: str) -> dict[str, Any]:
    code = re.sub(r"[\s\-]+", "", raw_code.strip().upper())
    is_imei = bool(re.fullmatch(r"\d{15}", code))
    is_sn = bool(re.fullmatch(r"[A-Z0-9]{8,14}", code)) and not is_imei
    luhn_valid = luhn_ok(code) if is_imei else None

    result: dict[str, Any] = {
        "code": code,
        "kind": "IMEI" if is_imei else ("Серийный № (SN)" if is_sn else "Неизвестный формат"),
        "valid_format": is_imei or is_sn,
        "luhn_valid": luhn_valid,
        "api_data": None,
        "api_source": "",
        "links": [
            {"name": "iFreeiCloud (FMI / Модель)", "url": "https://test.ifreeicloud.co.uk/free-check"},
            {"name": "Sickw (iCloud / Blacklist)", "url": "https://sickw.com/iphone-imei-check"},
            {"name": "iUnlocker (Find My ON/OFF)", "url": "https://iunlocker.com/check_icloud.php"},
        ],
    }

    if not (is_imei or is_sn):
        result["highlights"] = extract_imei_highlights(result["api_data"]) if result["api_data"] else {}
    return result

    # Если подключён ключ API (SICKW_API_KEY или IFREE_API_KEY) — запрашиваем автоматически
    if SICKW_API_KEY:
        try:
            svc = os.getenv("SICKW_SERVICE_ID", "demo").strip() or "demo"
            url = f"https://sickw.com/api.php?format=json&key={SICKW_API_KEY}&imei={code}&service={svc}"
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        result["api_data"] = data
                        result["api_source"] = "Sickw API"
        except Exception as e:
            logger.warning(f"Sickw API error: {e}")
    elif IFREE_API_KEY:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    "https://api.ifreeicloud.co.uk",
                    data={"key": IFREE_API_KEY, "imei": code, "service": "0"},
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        result["api_data"] = data
                        result["api_source"] = "iFreeiCloud API"
        except Exception as e:
            logger.warning(f"iFreeiCloud API error: {e}")

    return result


def analyze_panic_log(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    product_id = ""
    ios_build = ""
    panic_str = ""

    # Пробуем разобрать шапку JSON (в файлах .ips первая строка — JSON метаданных, дальше JSON тела)
    for line in text.splitlines()[:5]:
        line_s = line.strip()
        if line_s.startswith("{") and line_s.endswith("}"):
            try:
                obj = json.loads(line_s)
                product_id = obj.get("product") or obj.get("HardwareModel") or product_id
                ios_build = obj.get("os_version") or obj.get("build") or ios_build
                panic_str = obj.get("panicString") or panic_str
            except Exception:
                pass

    if not panic_str:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                product_id = obj.get("product") or product_id
                ios_build = obj.get("os_version") or obj.get("build") or ios_build
                panic_str = obj.get("panicString") or ""
        except Exception:
            pass

    if not product_id:
        m_prod = re.search(r'"product"\s*:\s*"([^"]+)"|Hardware\s+Model\s*:\s*([^\s\n]+)', text, re.I)
        if m_prod:
            product_id = m_prod.group(1) or m_prod.group(2) or ""

    if not ios_build:
        m_ios = re.search(r'"os_version"\s*:\s*"([^"]+)"|OS\s+Version\s*:\s*([^\n]+)', text, re.I)
        if m_ios:
            ios_build = (m_ios.group(1) or m_ios.group(2) or "").strip()

    if not panic_str:
        m_pan = re.search(r'"panicString"\s*:\s*"([^"]{10,1500})"', text)
        if m_pan:
            panic_str = m_pan.group(1).replace("\\n", "\n")
        else:
            m_pan2 = re.search(r"panic\([^)]+\)\s*:[^\n]+(?:\n[^\n]+){0,3}", text, re.I)
            panic_str = m_pan2.group(0) if m_pan2 else text[:450]

    model_name = APPLE_PRODUCT_MAP.get(product_id, product_id or "Не определена")
    search_blob = f"{panic_str}\n{text[:4000]}".lower()

    findings: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for pat, title, cause, fix in PANIC_RULES:
        if re.search(pat, search_blob, re.I):
            if title not in seen_titles:
                seen_titles.add(title)
                findings.append({"title": title, "cause": cause, "fix": fix})

    if not findings:
        findings.append({
            "title": "Нестандартная ошибка ядра",
            "cause": "Типовые сигнатуры датчиков (Prs0, Mic1/2, I2C, NAND, Baseband) в начале лога не найдены.",
            "fix": "Проверьте первые 5–10 строк panicString: отключите навесные шлейфы и проверьте запуск платы отдельно.",
        })

    return {
        "device_model": model_name,
        "product_id": product_id,
        "ios_build": ios_build or "—",
        "panic_snippet": panic_str.strip()[:380],
        "findings": findings[:4],
    }


def extract_imei_highlights(api_data: Any) -> dict[str, str]:
    """Достаёт самые важные поля (iCloud FMI, Регион, Модель, Sim-Lock, Blacklist) из ответа API."""
    out: dict[str, str] = {}
    if not isinstance(api_data, dict):
        return out
    flat: dict[str, str] = {}
    for k, v in api_data.items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                flat[sk.lower()] = str(sv)
        else:
            flat[k.lower()] = str(v)

    def _pick(keys: list[str]) -> str:
        for kw in keys:
            for fk, fv in flat.items():
                if kw in fk and fv and fv.lower() not in ("none", "null", ""):
                    return fv
        return ""

    fmi = _pick(["find my", "fmi", "icloud"])
    region = _pick(["country", "region", "purchase", "sold", "регион"])
    model = _pick(["model", "device", "product"])
    simlock = _pick(["sim-lock", "simlock", "carrier", "locked"])
    blacklist = _pick(["blacklist", "lost", "gsma"])

    if fmi:
        out["iCloud (Find My)"] = fmi
    if region:
        out["Регион / Страна"] = region
    if model:
        out["Модель"] = model
    if simlock:
        out["Sim-Lock / Оператор"] = simlock
    if blacklist:
        out["Blacklist / Lost"] = blacklist
    return out