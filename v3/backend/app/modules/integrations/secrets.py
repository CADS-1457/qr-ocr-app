from pathlib import Path

class SecretResolver:
    def read(self, secret_ref: str) -> bytes:
        # Exemple Docker Secrets: secret_ref peut désigner un fichier monté sous /run/secrets.
        # En production, remplacer/étendre ce backend par le gestionnaire de secrets retenu.
        path = Path(secret_ref)
        if not path.is_file():
            raise FileNotFoundError("Secret introuvable")
        return path.read_bytes()
