from pathlib import Path

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"}


def validate_input(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"Format non pris en charge: {path.suffix}")
    return path
