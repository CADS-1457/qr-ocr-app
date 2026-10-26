def parse_purchase_order(raw: dict) -> dict:
    return {
        "document_type": "purchase_order",
        "supplier": None,
        "order_number": None,
        "order_date": None,
        "lines": [],
        "raw_text": raw.get("text", ""),
    }
