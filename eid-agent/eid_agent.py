"""Agent local de lecture de la carte d'identité belge (eID) via un lecteur PC/SC.

Il écoute uniquement sur 127.0.0.1 et ne renvoie que l'identité (nom, prénoms, date et lieu de naissance, sexe,
nationalité) et l'adresse. Le numéro national n'est jamais transmis ni stocké.
"""
import argparse
import json
import base64
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.System import readers

SELECT_ID_FILE = [0x00, 0xA4, 0x08, 0x0C, 0x06, 0x3F, 0x00, 0xDF, 0x01, 0x40, 0x31]
SELECT_ADDRESS_FILE = [0x00, 0xA4, 0x08, 0x0C, 0x06, 0x3F, 0x00, 0xDF, 0x01, 0x40, 0x33]
SELECT_PHOTO_FILE = [0x00, 0xA4, 0x08, 0x0C, 0x06, 0x3F, 0x00, 0xDF, 0x01, 0x40, 0x35]
MONTHS = {
    "JAN": 1, "JANV": 1, "FEB": 2, "FEV": 2, "FEBR": 2, "MAR": 3, "MARS": 3, "MAART": 3, "MRZ": 3,
    "APR": 4, "AVR": 4, "MAI": 5, "MEI": 5, "JUN": 6, "JUIN": 6, "JUNI": 6, "JUL": 7, "JUIL": 7, "JULI": 7,
    "AUG": 8, "AOUT": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "OKT": 10, "NOV": 11, "DEC": 12, "DEZ": 12,
}


class CardError(Exception):
    pass


def read_files() -> tuple[bytes, bytes, bytes]:
    available = readers()
    if not available:
        raise CardError("Aucun lecteur de carte détecté.")
    connection = None
    for reader in available:
        candidate = reader.createConnection()
        try:
            candidate.connect()
            connection = candidate
            break
        except (NoCardException, CardConnectionException):
            continue
    if connection is None:
        raise CardError("Aucune carte dans le lecteur.")

    def read_file(select: list[int]) -> bytes:
        _, sw1, sw2 = connection.transmit(select)
        if (sw1, sw2) != (0x90, 0x00):
            raise CardError("Cette carte ne semble pas être une carte d'identité belge.")
        data = bytearray()
        while len(data) < 16384:
            offset = [(len(data) >> 8) & 0xFF, len(data) & 0xFF]
            chunk, sw1, sw2 = connection.transmit([0x00, 0xB0, *offset, 0xFF])
            if sw1 == 0x6C:
                chunk, sw1, sw2 = connection.transmit([0x00, 0xB0, *offset, sw2])
            if (sw1, sw2) not in ((0x90, 0x00), (0x62, 0x82)) or not chunk:
                break
            data.extend(chunk)
            if len(chunk) < 0xFF:
                break
        return bytes(data)

    try:
        identity = read_file(SELECT_ID_FILE)
        try:
            address = read_file(SELECT_ADDRESS_FILE)
        except CardError:
            address = b""
        try:
            photo = read_file(SELECT_PHOTO_FILE)
        except CardError:
            photo = b""
        return identity, address, photo
    finally:
        connection.disconnect()


def parse_tlv(raw: bytes) -> dict[int, str]:
    fields: dict[int, str] = {}
    pos = 0
    while pos + 2 <= len(raw):
        tag, length = raw[pos], raw[pos + 1]
        pos += 2
        fields[tag] = raw[pos:pos + length].decode("utf-8", errors="replace").strip()
        pos += length
    return fields


def date_from_national_number(number: str) -> str | None:
    digits = re.sub(r"\D", "", number)
    if len(digits) != 11:
        return None
    base, check = int(digits[:9]), int(digits[9:])
    yy, mm, dd = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    for century in (1900, 2000):
        shifted = base + (2_000_000_000 if century == 2000 else 0)
        if 97 - shifted % 97 == check and mm and dd:
            return f"{century + yy:04d}-{mm:02d}-{dd:02d}"
    return None


def national_number(value: str | None) -> str | None:
    """Numéro national (11 chiffres) uniquement s'il passe le contrôle modulo 97."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 11:
        return None
    base, check = int(digits[:9]), int(digits[9:])
    if any(97 - (base + extra) % 97 == check for extra in (0, 2_000_000_000)):
        return digits
    return None


def photo_base64(raw: bytes) -> str | None:
    start = raw.find(b"\xff\xd8")
    end = raw.rfind(b"\xff\xd9")
    if start < 0 or end < start:
        return None
    return base64.b64encode(raw[start:end + 2]).decode("ascii")


def birth_date(fields: dict[int, str]) -> str | None:
    """Date lue sur la carte (ex. « 01 JAN 1980 »), sinon déduite du numéro national (non conservé)."""
    match = re.match(r"^(\d{1,2})\W+([^\W\d]+)\.?\W*(\d{4})$", fields.get(12, ""))
    if match:
        month = MONTHS.get(match.group(2).upper().replace("É", "E").replace("Û", "U"))
        if month:
            return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"
    return date_from_national_number(fields.get(6, ""))


def tidy(value: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    return cleaned or None


def sex_code(value: str | None) -> str | None:
    letter = (value or "").strip().upper()[:1]
    return {"M": "M", "F": "F", "V": "F", "W": "F"}.get(letter)


NATIONALITIES = {
    "BELG": "Belge", "BELGE": "Belge", "BELGIQUE": "Belge", "BELGISCH": "Belge",
    "FRAN": "Française", "FRANCAISE": "Française", "FRANÇAISE": "Française",
}


def nationality(value: str | None) -> str | None:
    text = tidy(value)
    if not text:
        return None
    return NATIONALITIES.get(text.upper(), text.capitalize())


def read_card() -> dict[str, object]:
    identity_raw, address_raw, photo_raw = read_files()
    fields = parse_tlv(identity_raw)
    address = parse_tlv(address_raw)
    if not fields.get(7) or not fields.get(8):
        raise CardError("Lecture de la carte incomplète. Réinsérez la carte et réessayez.")
    given = tidy(" ".join(part for part in (fields.get(8), fields.get(9)) if part))
    return {
        "nom": tidy(fields[7]),
        "prenom": tidy(fields[8]).split(" ")[0],
        "prenoms": given,
        "date_naissance": birth_date(fields),
        "lieu_naissance": tidy(fields.get(11)),
        "sexe": sex_code(fields.get(13)),
        "nationalite": nationality(fields.get(10)),
        "adresse": tidy(address.get(1)),
        "code_postal": tidy(address.get(2)),
        "commune": tidy(address.get(3)),
        "numero_national": national_number(fields.get(6)),
        "photo": photo_base64(photo_raw),
    }


def make_handler(allowed_origins: set[str]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
            origin = self.headers.get("Origin", "")
            self.send_response(status)
            if origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self._send(204)

        def do_GET(self):
            if self.headers.get("Origin", "") not in allowed_origins:
                self._send(403, {"detail": "Origine non autorisée."})
                return
            path = self.path.split("?")[0]
            if path == "/status":
                self._send(200, {"ok": True, "readers": [str(r) for r in readers()]})
            elif path == "/card":
                try:
                    self._send(200, read_card())
                except CardError as error:
                    self._send(422, {"detail": str(error)})
                except Exception as error:
                    self._send(500, {"detail": f"Erreur de lecture : {error}"})
            else:
                self._send(404, {"detail": "Introuvable."})

        def log_message(self, *_):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent local de lecture eID belge")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origin", action="append", default=[],
                        help="Origine web autorisée (répétable). Défaut : https://ocr.ca-ds.be")
    args = parser.parse_args()
    origins = set(args.origin or ["https://ocr.ca-ds.be"])
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(origins))
    print(f"Agent eID prêt sur http://127.0.0.1:{args.port} (origines : {', '.join(sorted(origins))})")
    server.serve_forever()


if __name__ == "__main__":
    main()
