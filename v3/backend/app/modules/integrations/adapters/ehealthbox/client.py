from dataclasses import dataclass

@dataclass
class EHealthBoxMessage:
    recipient_identifier: str
    subject: str
    body: str
    attachment_keys: list[str]

class EHealthBoxClient:
    """Port d'intégration. Implémenter selon la documentation technique et les accès eHealth attribués au logiciel."""
    def send(self, message: EHealthBoxMessage):
        raise NotImplementedError
    def fetch_receipts(self):
        raise NotImplementedError
