import base64
import io
import os
from datetime import datetime
from typing import Any

from fpdf import FPDF

ORG_NAME = "QHEAD MOBILE"
ORG_SUB = "Сервисный центр • Ремонт техники"
ORG_ADDRESS = "г. Москва, Чонгарский бульвар, 6 (м. Варшавская)"
ORG_PHONE = "+7 (915) 196-13-94 • Telegram: @QQHead"
ORG_HOURS = "Пн–Пт: 10:00–20:00 • Сб–Вс: 11:00–20:00"

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _find_font(candidates: list[str]) -> str:
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


def _fmt_rub(val: Any) -> str:
    try:
        n = float(val or 0)
    except (TypeError, ValueError):
        return "0 ₽"
    if n == int(n):
        return f"{int(n):,}".replace(",", " ") + " ₽"
    return f"{n:,.2f}".replace(",", " ") + " ₽"


def _load_logo_bytes(default_logo_b64: str = "") -> io.BytesIO | None:
    for fname in ("logo.png", "logo.jpg", "logo.jpeg"):
        if os.path.isfile(fname):
            with open(fname, "rb") as f:
                return io.BytesIO(f.read())
    if default_logo_b64:
        return io.BytesIO(base64.b64decode(default_logo_b64))
    return None


def generate_act_pdf(order: dict[str, Any], act_type: str, logo_b64: str = "") -> bytes:
    """
    Генерирует PDF-акт (act_type='intake' — Акт приёма, 'done' — Акт выполненных работ)
    в фирменном стиле QHEAD Mobile без внешних браузерных зависимостей.
    """
    font_reg = _find_font(_FONT_CANDIDATES)
    font_bold = _find_font(_FONT_BOLD_CANDIDATES) or font_reg

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    if font_reg:
        pdf.add_font("DejaVu", "", font_reg)
        pdf.add_font("DejaVu", "B", font_bold)
        fam = "DejaVu"
    else:
        fam = "Helvetica"

    # Шапка с логотипом и адресом
    logo_io = _load_logo_bytes(logo_b64)
    if logo_io:
        pdf.image(logo_io, x=14, y=12, w=18, h=18)
        text_x = 35
    else:
        text_x = 14

    pdf.set_xy(text_x, 12)
    pdf.set_font(fam, "B", 15)
    pdf.set_text_color(17, 17, 19)
    pdf.cell(65, 7, ORG_NAME, ln=0)

    pdf.set_font(fam, "B", 9.5)
    pdf.set_xy(100, 12)
    pdf.cell(96, 5, ORG_ADDRESS, align="R", ln=1)
    pdf.set_font(fam, "", 9)
    pdf.set_text_color(75, 85, 99)
    pdf.set_x(text_x)
    pdf.cell(65, 5, ORG_SUB, ln=0)
    pdf.set_x(100)
    pdf.cell(96, 5, ORG_PHONE, align="R", ln=1)
    pdf.set_x(100)
    pdf.cell(96, 5, ORG_HOURS, align="R", ln=1)

    # Оранжевая разделительная линия
    pdf.set_draw_color(255, 90, 31)
    pdf.set_line_width(0.8)
    pdf.line(14, 33, 196, 33)

    # Заголовок акта и дата
    order_num = str(order.get("id", 0)).zfill(4)
    date_str = datetime.now().strftime("%d.%m.%Y")
    title = f"АКТ ПРИЁМА В РЕМОНТ № {order_num}" if act_type == "intake" else f"АКТ ВЫПОЛНЕННЫХ РАБОТ № {order_num}"
    date_lbl = f"Дата приёма: {date_str}" if act_type == "intake" else f"Дата выдачи: {date_str}"

    pdf.set_xy(14, 38)
    pdf.set_font(fam, "B", 13.5)
    pdf.set_text_color(17, 17, 19)
    pdf.cell(120, 8, title, ln=0)
    pdf.set_font(fam, "", 9.5)
    pdf.set_text_color(75, 85, 99)
    pdf.cell(62, 8, date_lbl, align="R", ln=1)

    pdf.ln(3)

    def draw_kv_row(label: str, value: str) -> None:
        pdf.set_font(fam, "B", 9.5)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(216, 222, 230)
        pdf.set_text_color(17, 17, 19)
        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_xy(14, y0)
        pdf.multi_cell(54, 7, label, border=1, fill=True)
        h_left = pdf.get_y() - y0
        pdf.set_xy(68, y0)
        pdf.set_font(fam, "", 9.5)
        pdf.multi_cell(128, 7, value or "—", border=1, fill=False)
        h_right = pdf.get_y() - y0
        pdf.set_y(y0 + max(h_left, h_right))

    if act_type == "intake":
        draw_kv_row("Заказчик", str(order.get("client_name") or "—"))
        draw_kv_row("Телефон для связи", str(order.get("client_phone") or "—"))
        draw_kv_row("Устройство / Модель", str(order.get("device_model") or "—"))
        draw_kv_row("Серийный № / IMEI", str(order.get("imei_sn") or "—"))
        draw_kv_row("Неисправность", str(order.get("issue_desc") or "—"))
        draw_kv_row("Внешний вид", str(order.get("appearance") or "Б/у, следы эксплуатации"))
        draw_kv_row("Комплектация", str(order.get("completeness") or "Только устройство"))
        draw_kv_row("Пароль / Примечание", str(order.get("passcode_note") or "—"))

        pdf.ln(5)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(255, 90, 31)
        pdf.set_font(fam, "B", 10.5)
        pdf.set_x(14)
        prepay = _fmt_rub(order.get("prepayment", 0))
        est = _fmt_rub(order.get("total_price", 0))
        pdf.cell(182, 10, f"Предоплата: {prepay}    |    Ориентировочная стоимость: {est}", border=1, fill=True, align="R", ln=1)

        pdf.ln(5)
        pdf.set_font(fam, "B", 9.5)
        pdf.set_text_color(17, 17, 19)
        pdf.cell(0, 6, "Условия приёма и обслуживания:", ln=1)
        pdf.set_font(fam, "", 8.5)
        pdf.set_text_color(75, 85, 99)
        pdf.multi_cell(
            182,
            4.8,
            "1. Исполнитель не несёт ответственности за возможную потерю данных — рекомендуем заранее делать резервную копию.\n"
            "2. При приёме устройства с разбитым экраном или после влаги полная проверка узлов возможна только после разборки.\n"
            "3. Окончательная стоимость и сроки согласовываются после диагностики. Бесплатное хранение готового устройства — 30 дней.",
        )
        left_sign = "Сдал (Заказчик): ______________________"
        right_sign = "Принял (Мастер Qhead Mobile): ______________________"
    else:
        draw_kv_row("Заказчик", f"{order.get('client_name') or '—'} • Тел.: {order.get('client_phone') or '—'}")
        draw_kv_row("Устройство", f"{order.get('device_model') or '—'} • IMEI: {order.get('imei_sn') or '—'}")
        warranty = int(order.get("warranty_days") or 30)
        draw_kv_row("Гарантия", f"{warranty} дней (при отсутствии механических и жидкостных повреждений)")

        pdf.ln(4)
        pdf.set_fill_color(17, 17, 19)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(fam, "B", 9.5)
        pdf.set_x(14)
        pdf.cell(12, 8, "№", border=1, fill=True)
        pdf.cell(126, 8, "Наименование работ и комплектующих", border=1, fill=True)
        pdf.cell(44, 8, "Сумма", border=1, fill=True, align="R", ln=1)

        pdf.set_text_color(17, 17, 19)
        pdf.set_font(fam, "", 9.5)
        service_txt = order.get("service_name") or order.get("issue_desc") or "Ремонт устройства"
        tot = _fmt_rub(order.get("total_price", 0))
        y0 = pdf.get_y()
        pdf.set_x(14)
        pdf.cell(12, 8, "1", border=1)
        pdf.cell(126, 8, str(service_txt)[:68], border=1)
        pdf.cell(44, 8, tot, border=1, align="R", ln=1)

        pdf.ln(4)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_font(fam, "B", 11)
        pdf.set_x(14)
        pdf.cell(182, 10, f"Итого к оплате / Оплачено: {tot}", border=1, fill=True, align="R", ln=1)

        pdf.ln(5)
        pdf.set_font(fam, "B", 9.5)
        pdf.cell(0, 6, "Гарантийные обязательства:", ln=1)
        pdf.set_font(fam, "", 8.5)
        pdf.set_text_color(75, 85, 99)
        pdf.multi_cell(
            182,
            4.8,
            "Устройство проверено Заказчиком при получении (сенсор, изображение, зарядка, звук, связь). "
            "Претензий по внешнему виду, комплектации и качеству выполненных работ Заказчик не имеет. "
            "Гарантия не распространяется на устройства со следами ударов, сколов, попадания влаги и вскрытия третьими лицами.",
        )
        left_sign = "Принял (Заказчик): ______________________"
        right_sign = "Выдал (Мастер Qhead Mobile): ______________________"

    pdf.ln(10)
    pdf.set_font(fam, "B", 9.5)
    pdf.set_text_color(17, 17, 19)
    pdf.set_x(14)
    pdf.cell(91, 7, left_sign, ln=0)
    pdf.cell(91, 7, right_sign, align="R", ln=1)

    return bytes(pdf.output())