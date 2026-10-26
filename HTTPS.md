# HTTPS pour ocr.ca-ds.be

Couche Nginx + Certbot au-dessus du projet. Certificat Let's Encrypt obtenu par **DNS-01 via l'API OVH** : aucun port 80/443 public ni enregistrement A public n'est nécessaire, seul un TXT temporaire `_acme-challenge` est créé dans la zone OVH.

Fichiers concernés :
- `docker-compose.https.yml` : services `nginx-https` (80/443) et `certbot` (image `certbot/dns-ovh`).
- `nginx/ocr.ca-ds.be.conf` : redirection HTTP vers HTTPS, relais `/api/` (préfixe conservé : `proxy_pass http://api:8000/api/;`) et `/`.
- `certbot/ovh.ini` : clés API OVH (**secret, ignoré par git**).
- `certbot/conf/` : certificats et clés privées (ignoré par git).

Toutes les commandes se lancent depuis `C:\IA\qr-ocr-app`. Abréviation utilisée ci-dessous :

```powershell
$c = "-f docker-compose.yml -f docker-compose.https.yml"
```

## 1. Créer les clés API OVH
1. Créer une application sur https://eu.api.ovh.com/createApp/ : on obtient une *Application Key* et un *Application Secret*.
2. Demander une *Consumer Key* avec les droits DNS. Le plus simple : https://eu.api.ovh.com/createToken/ avec les droits suivants (remplacer `*` par `ca-ds.be` pour restreindre à la zone) :
   - `GET /domain/zone` (sans `/*` : liste des zones, **non couvert** par `/domain/zone/*`)
   - `GET /domain/zone/*`
   - `POST /domain/zone/*/record`
   - `DELETE /domain/zone/*/record/*`
   - `POST /domain/zone/*/refresh`
3. Choisir la validité la plus longue (« Unlimited » si possible). Une clé expirée produit une erreur 403 au renouvellement.

Une Consumer Key peut aussi être demandée par l'API (`POST /1.0/auth/credential` avec l'en-tête `X-Ovh-Application`), puis validée via l'URL renvoyée.

## 2. Fichier `certbot/ovh.ini`

```ini
dns_ovh_endpoint = ovh-eu
dns_ovh_application_key = <Application Key>
dns_ovh_application_secret = <Application Secret>
dns_ovh_consumer_key = <Consumer Key>
```

Ce fichier est monté dans le conteneur certbot sur `/etc/ovh.ini` (voir `docker-compose.https.yml`). Il est dans `.gitignore` : ne jamais le commiter. L'avertissement « Unsafe permissions on credentials configuration file » est normal sous Windows.

## 3. Émettre le certificat
Si un certificat auto-signé existe dans `certbot/conf/live/ocr.ca-ds.be/`, le déplacer ou le supprimer d'abord, sinon certbot le traite comme un certificat existant.

```powershell
docker compose $c run --rm certbot certonly --dns-ovh --dns-ovh-credentials /etc/ovh.ini --dns-ovh-propagation-seconds 60 -d ocr.ca-ds.be --email admin@ca-ds.be --agree-tos --no-eff-email --non-interactive
```

Remplacer `admin@ca-ds.be` par l'adresse réelle de contact (obligatoire à la première émission d'un compte).

## 4. Démarrer / recharger HTTPS

```powershell
docker compose $c up -d nginx-https
docker compose $c exec -T nginx-https nginx -s reload
```

Nginx ne démarre pas si `certbot/conf/live/ocr.ca-ds.be/fullchain.pem` est absent. Sans certificat valide, générer temporairement un certificat auto-signé (voir « Dépannage »).

## 5. Tester

```powershell
docker compose $c ps
curl.exe -I https://ocr.ca-ds.be/ocr.html
curl.exe -s -o NUL -w "%{http_code}`n" https://ocr.ca-ds.be/api/ocr/jobs
```

Résultats attendus : `200` pour la page, `401` pour l'API sans token. Le certificat doit être reconnu sans `-k`.

## 6. Renouvellement (tous les ~2 mois)
Le certificat dure 90 jours. Commandes à planifier dans le Planificateur de tâches Windows :

```powershell
cd C:\IA\qr-ocr-app
docker compose -f docker-compose.yml -f docker-compose.https.yml run --rm certbot renew
docker compose -f docker-compose.yml -f docker-compose.https.yml exec -T nginx-https nginx -s reload
```

`renew` réutilise la méthode DNS-OVH enregistrée à l'émission. Aucun renouvellement n'a lieu si le certificat expire dans plus de 30 jours.

## Dépannage
- **`File not found: /etc/ovh.ini`** : le volume `./certbot/ovh.ini:/etc/ovh.ini:ro` manque dans le service `certbot`, ou `certbot/ovh.ini` n'existe pas.
- **`403 Forbidden` sur `/1.0/domain/zone/`** : droits ou Consumer Key invalides/expirés. Régénérer la clé avec les droits de l'étape 1 et la valider.
- **`SSL_ERROR_RX_RECORD_TOO_LONG`** : le navigateur utilise `https://` sur un port HTTP (par ex. `https://localhost:8080`). Utiliser `http://localhost:8080` ou `https://ocr.ca-ds.be`.
- **Nginx ne démarre pas (certificat manquant)** : générer un certificat auto-signé temporaire :

  ```powershell
  docker run --rm -v "${PWD}\certbot\conf\live\ocr.ca-ds.be:/c" alpine/openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=ocr.ca-ds.be" -addext "subjectAltName=DNS:ocr.ca-ds.be" -keyout /c/privkey.pem -out /c/fullchain.pem
  ```

- **Accès interne** : `ocr.ca-ds.be` résout vers l'IP interne du serveur (DNS interne). Le port 8080 reste disponible en HTTP simple (`http://localhost:8080`).
- **Envois de fichiers** : limités à 20 Mo par l'API, quelle que soit la valeur `client_max_body_size` de Nginx.
