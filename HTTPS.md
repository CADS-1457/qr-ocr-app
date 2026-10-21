# HTTPS pour ocr.ca-ds.be

## Mode interne (en place actuellement)
Le DNS public de `ocr.ca-ds.be` n'existe pas : Let's Encrypt ne peut donc pas délivrer de certificat. Un certificat auto-signé (SAN `ocr.ca-ds.be` et `192.168.88.179`, 825 jours) a été placé dans `certbot/conf/live/ocr.ca-ds.be/` (ignoré par git). Pour supprimer l'avertissement du navigateur, importer `certbot/conf/live/ocr.ca-ds.be/fullchain.pem` dans « Autorités de certification racines de confiance » des postes clients.

Le relais `/api/` doit conserver le préfixe (`proxy_pass http://api:8000/api/;`), comme `frontend/nginx.conf`.

Pour régénérer le certificat auto-signé :

```powershell
docker run --rm -v "${PWD}\certbot\conf\live\ocr.ca-ds.be:/c" alpine/openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=ocr.ca-ds.be/O=CADS" -addext "subjectAltName=DNS:ocr.ca-ds.be,IP:192.168.88.179" -keyout /c/privkey.pem -out /c/fullchain.pem
docker compose -f docker-compose.yml -f docker-compose.https.yml exec nginx-https nginx -s reload
```

Les sections suivantes ne concernent que le passage à un vrai certificat Let's Encrypt (DNS public requis).

Ce complément est prévu pour le projet qr-ocr-app existant, avec des services Docker nommés `frontend` et `api`.

## Prérequis
- Le DNS A/AAAA de ocr.ca-ds.be doit pointer vers le serveur.
- Les ports TCP 80 et 443 doivent arriver sur cette machine.
- Le compose principal doit contenir les services `frontend` et `api`.

## Installation
Copier le contenu de ce package à la racine du projet (par exemple C:\IA\qr-ocr-app).

### 1. Démarrer temporairement Nginx en HTTP
La configuration HTTPS référence un certificat qui n'existe pas encore. Pour le premier certificat, remplace temporairement `nginx/ocr.ca-ds.be.conf` par un serveur HTTP contenant uniquement le bloc ACME, ou utilise ton proxy HTTP déjà actif pour exposer `./certbot/www` sous `/.well-known/acme-challenge/`.

### 2. Demander le certificat Let's Encrypt
Une fois le challenge HTTP accessible :

```powershell
docker compose -f docker-compose.yml -f docker-compose.https.yml run --rm certbot certonly --webroot -w /var/www/certbot -d ocr.ca-ds.be --email admin@ca-ds.be --agree-tos --no-eff-email
```

Remplace `admin@ca-ds.be` par ton adresse réelle si nécessaire.

### 3. Démarrer la couche HTTPS

```powershell
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d
```

### 4. Tester

```powershell
docker compose -f docker-compose.yml -f docker-compose.https.yml ps
curl.exe -I https://ocr.ca-ds.be
```

## Renouvellement
Commande manuelle :

```powershell
docker compose -f docker-compose.yml -f docker-compose.https.yml run --rm certbot renew
docker compose -f docker-compose.yml -f docker-compose.https.yml exec nginx-https nginx -s reload
```

À planifier régulièrement sur l'hôte Windows (Task Scheduler), en conservant ces deux commandes.

## Important pour l'application
Utilise `/api/...` côté navigateur plutôt qu'une URL `http://localhost:8000`. Nginx relaie `/api/` vers `api:8000` sur le réseau Docker.

## Attention
Si le compose principal publie déjà le frontend sur le port 80 de l'hôte, retire ce mapping avant de lancer `nginx-https`, sinon le port 80 sera déjà occupé.
