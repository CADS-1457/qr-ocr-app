def parse_invoice(raw: dict) -> dict:
    return {
        "document_type": "invoice",
        "supplier": None,
        "invoice_number": None,
        "invoice_date": None,
        "currency": None,
        "subtotal": None,
        "vat": None,
        "total": None,
        "lines": [],
        "raw_text": raw.get("text", ""),
    }
