import re
import unicodedata
from decimal import Decimal

from .invoice import build_rows, money, parse_amount, parse_date

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}

VAT_ID_RE = re.compile(r"^TVA\s*:?\s*([A-Z]{2}[0-9A-Z][0-9A-Z .]{7,13})$")
RATE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*%")
LONG_DATE_RE = re.compile(r"(\d{1,2})\s+([^\W\d_]+)\.?\s+(\d{4})")

INVOICE_NUMBER_LABEL = r"Num[ée]ro de la facture"
INVOICE_DATE_LABEL = r"Date de la facture(?:\s*/\s*Date de la livraison)?"


def strip_accents(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def parse_long_date(text: str):
    match = LONG_DATE_RE.search(text or "")

    if match:
        day, month_name, year = match.groups()
        month = MONTHS.get(strip_accents(month_name).lower())

        if month:
            return f"{year}-{month:02d}-{int(day):02d}"

    return parse_date(text or "")


def is_amazon_layout(raw: dict) -> bool:
    text = raw.get("text", "")

    return bool(
        re.search(INVOICE_NUMBER_LABEL, text, re.IGNORECASE)
        and re.search(r"D[ée]tails de la facture", text, re.IGNORECASE)
    )


def labeled_value(rows: list, label: str):
    pattern = re.compile(label, re.IGNORECASE)

    for row in rows:
        blocks = row["blocks"]

        for index, block in enumerate(blocks):
            match = pattern.match(block["text"].strip())

            if not match:
                continue

            remainder = block["text"].strip()[match.end():].strip(" :")

            if remainder:
                return remainder

            if index + 1 < len(blocks):
                return blocks[index + 1]["text"].strip()

    return None


def address_columns(rows: list) -> dict:
    start = next(
        (
            index
            for index, row in enumerate(rows)
            if row["blocks"][0]["text"].startswith("Adresse commerciale")
        ),
        None,
    )

    if start is None:
        return {}

    header = rows[start]["blocks"]
    titles = [block["text"].strip() for block in header]
    positions = [block["box"][0] for block in header]
    columns = [[] for _ in header]

    for row in rows[start + 1:]:
        if row["blocks"][0]["text"].startswith("Informations de la commande"):
            break

        for block in row["blocks"]:
            column = max(
                (i for i, x in enumerate(positions) if block["box"][0] >= x - 25),
                default=0,
            )
            columns[column].append(block["text"].strip())

    return dict(zip(titles, columns))


def build_party(lines: list):
    if not lines:
        return None

    party = {"name": None, "address": [], "vat": None}

    for text in lines:
        vat = VAT_ID_RE.match(text)

        if vat:
            party["vat"] = re.sub(r"[\s.]", "", vat.group(1))
        elif party["name"] is None:
            party["name"] = text
        else:
            party["address"].append(text)

    return party


def parse_line_row(blocks: list):
    texts = [block["text"].strip() for block in blocks]
    index = len(texts)
    rate = None
    quantity = 1.0

    if not index or parse_amount(texts[index - 1]) is None:
        return None

    total = parse_amount(texts[index - 1])
    index -= 1

    if index and RATE_RE.match(texts[index - 1]):
        rate = float(RATE_RE.match(texts[index - 1]).group(1).replace(",", "."))
        index -= 1

    if not index or parse_amount(texts[index - 1]) is None:
        return None

    unit_price = parse_amount(texts[index - 1])
    index -= 1

    if index > 1 and re.fullmatch(r"\d+", texts[index - 1]):
        quantity = float(texts[index - 1])
        index -= 1

    description = " ".join(texts[:index]).strip()

    if not description:
        return None

    return {
        "section": None,
        "description": description,
        "unit_price": money(unit_price),
        "quantity": quantity,
        "vat_rate": rate,
        "total": money(total),
        "note": None,
        "asin": None,
    }


def extract_lines(rows: list) -> tuple:
    lines = []
    warnings = []
    basis = "ttc"
    in_table = False

    for row in rows:
        first = row["blocks"][0]["text"].strip()
        texts = [block["text"].strip() for block in row["blocks"]]

        if first.startswith("Description") and any(t.startswith("Qt") for t in texts):
            in_table = True
            continue

        if not in_table:
            continue

        if first.startswith("Facture Total"):
            break

        if set(texts) <= {"HT", "TTC"}:
            basis = "ttc" if "TTC" in texts else "ht"
            continue

        line = parse_line_row(row["blocks"])

        if line:
            lines.append(line)
            continue

        if not lines or len(texts) != 1:
            continue

        if first.startswith("ASIN"):
            lines[-1]["asin"] = first.split(":", 1)[-1].strip()
        elif not first.startswith("Information indisponible"):
            lines[-1]["description"] += f" {first}"

    for line in lines:
        expected = Decimal(str(line["unit_price"])) * Decimal(str(line["quantity"]))

        if basis == "ttc" and line["vat_rate"]:
            expected *= 1 + Decimal(str(line["vat_rate"])) / 100

        if abs(expected - Decimal(str(line["total"]))) > Decimal("0.02"):
            warnings.append(f"Ligne incohérente : {line['description'][:60]}")

    return lines, basis, warnings


def extract_totals(rows: list) -> dict:
    totals = {"breakdown": []}
    in_breakdown = False

    for row in rows:
        blocks = row["blocks"]
        texts = [block["text"].strip() for block in blocks]
        first = texts[0]

        if first.startswith("Facture Total") and len(texts) > 1:
            totals["total"] = parse_amount(texts[-1])
        elif first == "Taux TVA":
            in_breakdown = True
        elif in_breakdown and first == "Total" and len(texts) >= 3:
            totals["subtotal"] = parse_amount(texts[1])
            totals["vat"] = parse_amount(texts[2])
            in_breakdown = False
        elif in_breakdown and RATE_RE.match(first) and len(texts) >= 3:
            totals["breakdown"].append(
                {
                    "rate": float(RATE_RE.match(first).group(1).replace(",", ".")),
                    "base": money(parse_amount(texts[1])),
                    "vat": money(parse_amount(texts[2])),
                }
            )

    return totals


def parse_amazon_invoice(raw: dict) -> dict:
    blocks = raw.get("blocks", [])
    text = raw.get("text", "")
    rows = build_rows(blocks)

    columns = address_columns(rows)
    customer = build_party(columns.get("Adresse commerciale"))
    delivery = build_party(columns.get("Adresse de livraison"))
    supplier = build_party(columns.get("Vendu par"))

    lines, basis, warnings = extract_lines(rows)
    totals = extract_totals(rows)
    breakdown = totals["breakdown"]

    total = totals.get("total")
    subtotal = totals.get("subtotal")
    vat = totals.get("vat")
    paid = any(
        re.fullmatch(r"Pay[ée]", block["text"].strip(), re.IGNORECASE)
        for block in blocks
    )

    exemption = next(
        (
            block["text"].strip()
            for block in blocks
            if re.match(r"^\(\d\)\s*Exon[ée]ration", block["text"].strip())
        ),
        None,
    )

    checks = {}
    if lines and total is not None:
        reference = total if basis == "ttc" else subtotal

        if reference is not None:
            checks["lines_match_total"] = (
                sum(Decimal(str(line["total"])) for line in lines) == reference
            )
    if None not in (subtotal, vat, total):
        checks["total_matches_subtotal_plus_vat"] = subtotal + vat == total

    return {
        "document_type": "invoice",
        "layout": "amazon_business",
        "invoice_number": labeled_value(rows, INVOICE_NUMBER_LABEL),
        "invoice_date": parse_long_date(labeled_value(rows, INVOICE_DATE_LABEL)),
        "due_date": None,
        "currency": "EUR" if "€" in text or re.search(r"\bEUR\b", text) else None,
        "supplier": supplier,
        "customer": customer,
        "delivery_address": delivery,
        "customer_code": None,
        "order_number": labeled_value(rows, r"Num[ée]ro de la commande"),
        "order_date": parse_long_date(labeled_value(rows, r"Date de la commande")),
        "ordered_by": labeled_value(rows, r"Command[ée] par"),
        "subtotal": money(subtotal),
        "vat": money(vat),
        "vat_rate": breakdown[0]["rate"] if len(breakdown) == 1 else None,
        "vat_breakdown": breakdown,
        "vat_exemption": exemption,
        "total": money(total),
        "paid": money(total) if paid else None,
        "balance_due": 0.0 if paid else None,
        "line_total_basis": basis,
        "payment": {
            "iban": None,
            "bic": None,
            "structured_communication": None,
            "reference": labeled_value(rows, r"R[ée]f[ée]rence de paiement"),
        },
        "payments": [],
        "lines": lines,
        "checks": checks,
        "warnings": warnings,
        "raw_text": text,
    }
