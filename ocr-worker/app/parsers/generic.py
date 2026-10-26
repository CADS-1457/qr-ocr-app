from .amazon_invoice import is_amazon_layout, parse_amazon_invoice
from .invoice import is_invoice, parse_invoice


def normalize_document(raw: dict) -> dict:
    blocks = raw.get("blocks", [])
    scores = [b["confidence"] for b in blocks if b.get("confidence") is not None]

    result = {
        "document_type": "generic",
        "confidence": round(sum(scores) / len(scores), 4) if scores else None,
        "raw_text": raw.get("text", ""),
        "blocks": blocks,
        "engine": raw.get("engine"),
        "engine_status": raw.get("status"),
    }

    if is_invoice(raw):
        parser = parse_amazon_invoice if is_amazon_layout(raw) else parse_invoice
        result.update(parser(raw))

    return result
