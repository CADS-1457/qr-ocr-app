import os
from pathlib import Path
from threading import Lock

_pipeline = None
_lock = Lock()


def _get_pipeline():
    global _pipeline
    with _lock:
        if _pipeline is None:
            from paddleocr import PaddleOCR

            _pipeline = PaddleOCR(
                lang=os.getenv("OCR_LANG", "fr"),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )
        return _pipeline


def extract_structure(path: Path) -> dict:
    pipeline = _get_pipeline()
    blocks = []

    for page_number, page in enumerate(pipeline.predict(str(path)), start=1):
        texts = page.get("rec_texts", [])
        scores = page.get("rec_scores", [])
        boxes = page.get("rec_boxes", [])

        for index, text in enumerate(texts):
            box = boxes[index] if index < len(boxes) else None
            blocks.append(
                {
                    "page": page_number,
                    "text": text,
                    "confidence": float(scores[index]) if index < len(scores) else None,
                    "box": [int(value) for value in box] if box is not None else None,
                }
            )

    return {
        "engine": "PaddleOCR",
        "source": str(path),
        "status": "ok",
        "blocks": blocks,
        "text": "\n".join(block["text"] for block in blocks),
    }
