import re
from decimal import Decimal, InvalidOperation

AMOUNT_RE = re.compile(
    r"^-?\d{1,3}(?:[\s\u00a0.]\d{3})+,\d{1,2}$|^-?\d+,\d{1,2}$|^-?\d+\.\d{2}$"
)
QUANTITY_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
DATE_RE = re.compile(r"(\d{2})[/.-](\d{2})[/.-](\d{4})")
INVOICE_NUMBER_RE = re.compile(
    r"\bFacture\s+(?:avoir\s+)?(?:n[°o]\s*)?:?\s*([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)",
    re.IGNORECASE,
)
CREDIT_NOTE_RE = re.compile(
    r"\b(?:Facture\s+avoir|Note\s+de\s+cr[ée]dit|Avoir)\s+(?:n[°o]\s*)?:?\s*([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)",
    re.IGNORECASE,
)
CORRECTED_INVOICE_RE = re.compile(
    r"(?:Correction\s+facture|Avoir\s+sur\s+facture)\s*:?\s*([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)",
    re.IGNORECASE,
)
VAT_RE = re.compile(r"\bBE\s?[01]\d{3}[\s.]?\d{3}[\s.]?\d{3}\b")
IBAN_RE = re.compile(
    r"IBAN[ ]*:?[ ]*([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?\d{1,3})?)\b"
)
BIC_RE = re.compile(r"BIC[^:\n]*:\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)")
STRUCTURED_COMMUNICATION_RE = re.compile(r"\+\+\+(\d{3})/(\d{4})/(\d{5})\+\+\+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"T[ée]l\.?\s*:?\s*(\+?[\d][\d\s./]{6,})")

TOTAL_LABELS = (
    ("subtotal", re.compile(r"^Total\s+HT\b", re.IGNORECASE)),
    ("vat", re.compile(r"^Total\s+TVA\b", re.IGNORECASE)),
    ("total", re.compile(r"^Total\s+TTC\b", re.IGNORECASE)),
    ("paid", re.compile(r"^Pay[ée]\b", re.IGNORECASE)),
    ("balance_due", re.compile(r"^Reste\s+[àa]\s+payer", re.IGNORECASE)),
)


def parse_amount(text: str):
    cleaned = text.replace("€", "").replace("EUR", "").strip()
    if not AMOUNT_RE.match(cleaned):
        return None
    if "," in cleaned:
        cleaned = re.sub(r"[\s\u00a0.]", "", cleaned).replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_quantity(text: str):
    cleaned = text.strip()
    if not QUANTITY_RE.match(cleaned):
        return None
    return Decimal(cleaned.replace(",", "."))


def parse_date(text: str):
    match = DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def money(value):
    return None if value is None else float(value)


def is_invoice(raw: dict) -> bool:
    text = raw.get("text", "")
    return bool(
        INVOICE_NUMBER_RE.search(text)
        and re.search(r"Total\s+(?:TTC|HT)", text, re.IGNORECASE)
    )


def build_rows(blocks: list) -> list:
    placed = [b for b in blocks if b.get("box") and b.get("text")]
    placed.sort(key=lambda b: (b["page"], (b["box"][1] + b["box"][3]) / 2))

    rows = []
    current = None

    for block in placed:
        center = (block["box"][1] + block["box"][3]) / 2
        height = block["box"][3] - block["box"][1]

        if (
            current
            and current["page"] == block["page"]
            and abs(center - current["center"]) <= max(6, 0.5 * height)
        ):
            current["blocks"].append(block)
        else:
            current = {"page": block["page"], "center": center, "blocks": [block]}
            rows.append(current)

    for row in rows:
        row["blocks"].sort(key=lambda b: b["box"][0])
        row["text"] = " ".join(b["text"] for b in row["blocks"])

    return rows


def extract_lines(rows: list) -> tuple:
    lines = []
    warnings = []
    section = None

    for row in rows:
        blocks = row["blocks"]
        tail = blocks[-3:]

        if (
            len(blocks) >= 3
            and parse_amount(tail[0]["text"]) is not None
            and parse_quantity(tail[1]["text"]) is not None
            and parse_amount(tail[2]["text"]) is not None
        ):
            unit_price = parse_amount(tail[0]["text"])
            quantity = parse_quantity(tail[1]["text"])
            total = parse_amount(tail[2]["text"])
            description = " ".join(b["text"] for b in blocks[:-3]).strip()

            if quantity == 0 and total == 0:
                section = description.lstrip("_ ").strip() or None
                continue

            line = {
                "section": section,
                "description": description,
                "unit_price": money(unit_price),
                "quantity": float(quantity),
                "total": money(total),
                "note": None,
            }

            if abs(unit_price * quantity - total) > Decimal("0.01"):
                warnings.append(
                    f"Ligne incohérente : {description} "
                    f"({unit_price} x {quantity} ≠ {total})"
                )

            lines.append(line)
            continue

        if lines and len(blocks) == 1 and blocks[0]["text"].startswith(">>"):
            if lines[-1]["note"] is None:
                lines[-1]["note"] = blocks[0]["text"].lstrip("> ").strip()

    return lines, warnings


def extract_totals(rows: list) -> dict:
    totals = {}

    for row in rows:
        for index, label in enumerate(row["blocks"]):
            for key, pattern in TOTAL_LABELS:
                if key in totals or not pattern.match(label["text"]):
                    continue

                for block in reversed(row["blocks"][index + 1:]):
                    amount = parse_amount(block["text"])
                    if amount is not None:
                        totals[key] = amount
                        break

                if key == "vat":
                    rate = re.search(r"(\d+(?:[,.]\d+)?)\s*%", label["text"])
                    if rate:
                        totals["vat_rate"] = float(rate.group(1).replace(",", "."))

    return totals


def extract_payments(rows: list) -> list:
    payments = []
    started = False

    left = 0

    for row in rows:
        title = next(
            (b for b in row["blocks"] if b["text"].startswith("Versements")), None
        )
        if title:
            started = True
            left = title["box"][0] - 10
            continue

        if not started:
            continue

        if any(
            b["text"].startswith(("Factures en retard", "Siège")) for b in row["blocks"]
        ):
            break

        blocks = [b for b in row["blocks"] if b["box"][0] >= left]
        if not blocks:
            continue

        amount = next(
            (parse_amount(b["text"]) for b in blocks[1:] if parse_amount(b["text"]) is not None),
            None,
        )

        if DATE_RE.fullmatch(blocks[0]["text"].strip()) and amount is not None:
            payments.append(
                {
                    "date": parse_date(blocks[0]["text"]),
                    "amount": money(amount),
                    "method": blocks[2]["text"] if len(blocks) > 2 else None,
                }
            )

    return payments


def normalize_vat(value: str) -> str:
    return re.sub(r"[\s.]", "", value)


def extract_parties(blocks: list) -> tuple:
    page_one = [b for b in blocks if b.get("box") and b.get("page") == 1]
    anchor = next((b for b in page_one if b["text"].startswith("Adressé")), None)
    table_header = next(
        (b for b in page_one if b["text"].startswith("Désignation")), None
    )

    if not anchor:
        return {}, {}

    table_top = min(
        b["box"][1]
        for b in page_one
        if b["text"].startswith(("Désignation", "Montants exprim"))
    ) if table_header else float("inf")

    customer_blocks = sorted(
        (
            b
            for b in page_one
            if b["box"][0] >= anchor["box"][0] - 25
            and anchor["box"][3] - 2 < b["box"][1] < table_top
        ),
        key=lambda b: (b["box"][1], b["box"][0]),
    )

    supplier_blocks = sorted(
        (
            b
            for b in page_one
            if b["box"][2] < anchor["box"][0] - 5
            and anchor["box"][1] - 5 < b["box"][1] < table_top
        ),
        key=lambda b: (b["box"][1], b["box"][0]),
    )

    customer = {"name": None, "address": [], "vat": None}
    for block in customer_blocks:
        vat = VAT_RE.search(block["text"])
        if vat:
            customer["vat"] = normalize_vat(vat.group())
        elif customer["name"] is None:
            customer["name"] = block["text"]
        else:
            customer["address"].append(block["text"])

    supplier = {"name": None, "address": []}
    for block in supplier_blocks:
        if block["text"].startswith(("Tél", "Tel", "Email", "Web")):
            break
        if supplier["name"] is None:
            supplier["name"] = block["text"]
        else:
            supplier["address"].append(block["text"])

    return supplier, customer


def structured_communication_is_valid(match) -> bool:
    digits = "".join(match.groups())
    base, check = int(digits[:10]), int(digits[10:])
    remainder = base % 97
    return check == (remainder if remainder else 97)


def parse_invoice(raw: dict) -> dict:
    blocks = raw.get("blocks", [])
    text = raw.get("text", "")
    rows = build_rows(blocks)
    row_text = text or "\n".join(block["text"] for block in blocks)

    supplier, customer = extract_parties(blocks)
    lines, warnings = extract_lines(rows)
    totals = extract_totals(rows)

    number = CREDIT_NOTE_RE.search(row_text)
    is_credit_note = number is not None
    number = number or INVOICE_NUMBER_RE.search(row_text)
    corrected = CORRECTED_INVOICE_RE.search(row_text) if is_credit_note else None
    invoice_date = re.search(r"Date\s+(?:de\s+)?facturation\s*:?\s*(.+)", row_text, re.IGNORECASE)
    due_date = re.search(r"Date\s+[ée]ch[ée]ance\s*:?\s*(.+)", row_text, re.IGNORECASE)
    customer_code = re.search(r"Code\s+client\s*:?\s*(\S+)", row_text, re.IGNORECASE)
    iban = IBAN_RE.search(row_text)
    bic = BIC_RE.search(row_text)
    communication = STRUCTURED_COMMUNICATION_RE.search(row_text)
    email = EMAIL_RE.search(row_text)
    phone = PHONE_RE.search(row_text)

    vat_numbers = [normalize_vat(m.group()) for m in VAT_RE.finditer(row_text)]
    supplier_vat = next((v for v in vat_numbers if v != customer.get("vat")), None)

    if supplier:
        supplier.update(
            {
                "vat": supplier_vat,
                "email": email.group() if email else None,
                "phone": phone.group(1).strip() if phone else None,
            }
        )

    checks = {}
    if lines and "subtotal" in totals:
        checks["lines_match_subtotal"] = (
            sum(Decimal(str(line["total"])) for line in lines) == totals["subtotal"]
        )
    if {"subtotal", "vat", "total"} <= totals.keys():
        checks["total_matches_subtotal_plus_vat"] = (
            totals["subtotal"] + totals["vat"] == totals["total"]
        )
    if communication:
        checks["structured_communication_valid"] = structured_communication_is_valid(
            communication
        )

    return {
        "document_type": "credit_note" if is_credit_note else "invoice",
        "corrected_invoice": corrected.group(1) if corrected else None,
        "invoice_number": number.group(1) if number else None,
        "invoice_date": parse_date(invoice_date.group(1)) if invoice_date else None,
        "due_date": parse_date(due_date.group(1)) if due_date else None,
        "currency": "EUR"
        if re.search(r"euros?|€|\bEUR\b", row_text, re.IGNORECASE)
        else None,
        "supplier": supplier or None,
        "customer": customer or None,
        "customer_code": customer_code.group(1) if customer_code else None,
        "subtotal": money(totals.get("subtotal")),
        "vat": money(totals.get("vat")),
        "vat_rate": totals.get("vat_rate"),
        "total": money(totals.get("total")),
        "paid": money(totals.get("paid")),
        "balance_due": money(totals.get("balance_due")),
        "payment": {
            "iban": re.sub(r"\s", "", iban.group(1)) if iban else None,
            "bic": bic.group(1) if bic else None,
            "structured_communication": communication.group() if communication else None,
        },
        "payments": extract_payments(rows),
        "lines": lines,
        "checks": checks,
        "warnings": warnings,
        "raw_text": text,
    }
