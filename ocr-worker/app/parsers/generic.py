def normalize_document(raw: dict) -> dict:
    blocks = raw.get("blocks", [])
    scores = [b["confidence"] for b in blocks if b.get("confidence") is not None]

    return {
        "document_type": "generic",
        "confidence": round(sum(scores) / len(scores), 4) if scores else None,
        "raw_text": raw.get("text", ""),
        "blocks": blocks,
        "engine": raw.get("engine"),
        "engine_status": raw.get("status"),
    }
