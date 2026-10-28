import re

def normalize_be_phone(value: str) -> str:
    digits=re.sub(r'\D','',value or '')
    if digits.startswith('0032'): digits=digits[4:]
    elif digits.startswith('32'): digits=digits[2:]
    elif digits.startswith('0'): digits=digits[1:]
    if not digits or not (8 <= len(digits) <= 10):
        raise ValueError('Numéro belge invalide ou non pris en charge')
    return '+32'+digits
