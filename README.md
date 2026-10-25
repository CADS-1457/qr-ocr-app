# Application QR Code + OCR

## 1. Démarrer l'API sous Windows

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "choisir-un-mot-de-passe-long"
$env:SUPERADMIN_USERNAME = "superadmin"
$env:SUPERADMIN_PASSWORD = "choisir-un-autre-mot-de-passe-long"
$secretBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($secretBytes)
$rng.Dispose()
$env:ADMIN_SECRET_KEY = [Convert]::ToBase64String($secretBytes)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Les variables d'environnement doivent être définies dans le terminal qui lance l'API. Au premier démarrage seulement, `ADMIN_USERNAME` et `ADMIN_PASSWORD` créent le compte administrateur initial si aucun compte n'existe encore. `SUPERADMIN_USERNAME` et `SUPERADMIN_PASSWORD` créent un compte SuperAdmin distinct, caché dans la liste et la gestion des comptes administrateur. Son mot de passe doit faire au moins 12 caractères. Configurez ces deux variables avant le démarrage de l'API qui doit créer le SuperAdmin ; cela fonctionne aussi sur une base existante, tant qu'aucun SuperAdmin n'y a déjà été créé. Une fois créé, le compte est conservé en base et ces variables ne servent plus à se connecter. L'accès au journal protégé se fait depuis la page d'accueil après connexion SuperAdmin, ou directement via `superadmin.html`. Les admins ne peuvent ni lister, modifier ni supprimer ce compte ; les activités du SuperAdmin ne figurent pas dans leur journal des scans. Après cela, ces variables de bootstrap ne sont plus nécessaires pour les connexions : les comptes sont gérés dans la base SQLite. Si `ADMIN_SECRET_KEY` n'est pas définie, l'API génère automatiquement une clé aléatoire et la conserve dans la base SQLite ; ne supprimez pas cette base et conservez-la dans les sauvegardes pour garder les sessions valides. Il est aussi possible de définir `ADMIN_SECRET_KEY` explicitement : dans ce cas, utilisez la même valeur à chaque démarrage. Changer de clé invalide les sessions déjà ouvertes. Au démarrage, l'API ajoute automatiquement les colonnes requises aux bases existantes, notamment `auth_version` pour les comptes et `owner_id` pour les scans ; les enregistrements existants sont conservés. Les nouveaux scans sont associés au compte connecté. Pour les scans anciens sans propriétaire, l'API tente de retrouver le compte créateur à partir de l'événement de création dans le journal, lorsque le compte correspondant existait déjà à cette date. Un utilisateur standard peut consulter et créer des scans, et modifier ses propres scans ; un administrateur peut modifier/supprimer tous les scans et gérer les comptes. Les scans dont le créateur ne peut pas être établi restent modifiables uniquement par un administrateur. Choisissez des mots de passe forts. Ne publiez pas les secrets dans le dépôt. Dans l'historique, connectez-vous avec le compte administrateur initial, puis utilisez « Gestion des utilisateurs » pour créer, modifier, désactiver ou supprimer des comptes. Les mots de passe des comptes sont stockés sous forme dérivée PBKDF2. Le journal d'audit SuperAdmin enregistre les créations, modifications et suppressions de scans, comptes, patients et articles/sous-entités de stock, ainsi que les mouvements de stock et délivrances ; il conserve les données avant/après utiles, jamais les mots de passe ni les images, sans expiration, et n'est accessible qu'au rôle SuperAdmin. Les événements de scan déjà présents dans le journal précédent sont importés une fois au démarrage ; les autres entités sont journalisées à partir de cette version, car leurs anciens changements ne peuvent pas être reconstruits. Le journal historique des scans visible par les admins reste purgé après 365 jours. Les sessions sont valables huit heures. Le mot de passe et le jeton de session ne sont chiffrés en transit qu'en HTTPS ; utilisez HTTPS si l'application est accessible sur un réseau non fiable.

## 2. Démarrer le frontend dans un second terminal

```powershell
cd frontend
python -m http.server 5500 --bind 0.0.0.0
```

## 3. Accès

- Depuis le PC serveur : http://localhost:5500
- Depuis le téléphone : http://ADRESSE-IP-DU-PC:5500
- Documentation API : http://ADRESSE-IP-DU-PC:8000/docs

L'URL de l'API est calculée automatiquement depuis le nom d'hôte de la page. Si la page est ouverte sur 192.168.1.50, elle utilisera automatiquement l'API 192.168.1.50:8000.

Dans `historique-patients.html`, le bouton « Copier URL » de chaque délivrance copie un lien direct vers l'historique filtré du patient (`historique-patients.html?patient_id=...`). Ce lien peut être enregistré comme raccourci dans une autre application. Le bouton « Nouvelle délivrance » transmet aussi le patient sélectionné à `delivrance.html`, où son profil est repris automatiquement. L'accès reste protégé : il faut être connecté à l'application pour consulter les données.

## 4. HTTPS sur le réseau local (Caddy)

Réservez une adresse IP locale pour le PC dans le routeur, puis téléchargez Caddy pour Windows et placez `caddy.exe` à la racine du projet. Créez au même endroit un fichier `Caddyfile` avec la configuration ci-dessous en remplaçant `192.168.1.50` par l'adresse réservée du PC :

```caddyfile
https://192.168.1.50 {
    tls internal

    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }

    handle {
        root * C:\IA\qr-ocr-app\frontend
        file_server
    }
}
```

Démarrez l'API dans un terminal, depuis le dossier `backend`, en l'écoutant uniquement sur le PC :

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Dans un second terminal, démarrez Caddy avec le `Caddyfile` :

```powershell
.\caddy.exe run --config C:\IA\qr-ocr-app\Caddyfile
```

Ouvrez ensuite `https://192.168.1.50` sur le PC et le téléphone. Caddy utilise ici sa propre autorité de certification : pour supprimer l'avertissement du navigateur et autoriser la caméra, installez le certificat racine Caddy du PC sur chaque appareil et faites-lui confiance. Le certificat se trouve habituellement sous `%AppData%\Caddy\pki\authorities\local\root.crt`. Sur Windows, `caddy trust` peut installer cette autorité sur le PC ; sur un téléphone, transférez le certificat et installez-le comme autorité de confiance selon les réglages du système. Ne partagez jamais la clé privée de cette autorité.

Autorisez le port TCP 443 dans le pare-feu Windows pour le réseau privé. Ne publiez pas le port 8000 et ne configurez pas de redirection de ports Internet pour cette configuration locale.

La page `index.html` sert de portail : après connexion, elle donne accès au scanner QR/OCR (`scan.html`), aux historiques, aux fiches patients, aux délivrances et aux pages de stock. Les images sélectionnées dans le formulaire du scanner sont enregistrées dans la base SQLite avec le scan associé (formats PNG, JPEG, GIF, WebP et BMP, jusqu'à 10 Mo). Elles sont ensuite consultables dans les derniers enregistrements et dans le détail de l'historique. Le numéro de lot est également enregistré et apparaît dans l'historique, la recherche et l'export CSV. Les champs d'un enregistrement existant peuvent être modifiés depuis l'historique par un utilisateur qui l'a créé ou par un administrateur. La page `stock.html` fournit un inventaire séparé des scans : la dénomination est le produit parent et la quantité de la fiche en est le total agrégé. Le code-barres produit, facultatif, permet de retrouver une fiche stock à partir d'un code-barres scanné ; les QR des sous-entités restent uniques et sont gérés séparément. Tous les comptes connectés peuvent consulter l'inventaire et les sous-entités QR ; seuls les administrateurs peuvent gérer les fiches et supprimer une sous-entité QR. La page `stock-qr.html` permet à tout utilisateur connecté de sélectionner une dénomination et de créer une sous-entité par QR unique. La sous-entité reçoit le numéro de série QR, une unité de stock et les informations de lot, de date d'entrée et de péremption de la fiche parente au moment du scan. Le même QR ne peut pas être enregistré deux fois. Les sorties de stock réduisent d'abord la quantité historique sans QR, puis les sous-entités QR les plus anciennes ; leur quantité restante est visible dans le tableau dédié. Un utilisateur connecté peut également rechercher un produit et enregistrer une sortie de stock depuis `scan.html` ; l'API contrôle atomiquement la quantité disponible et conserve chaque sortie (article, quantité, compte, date) dans l'historique visible par les administrateurs sur la page du stock.

La page `stock-alertes.html`, également accessible depuis `stock.html`, récapitule les niveaux de stock et affiche les alertes calculées à partir des seuils minimums configurés par produit. Les administrateurs et SuperAdmins définissent le seuil minimum dans cette page ; à quantité égale ou inférieure au seuil, l'indicateur est rouge, et il est orange jusqu'à deux fois le seuil. Au-dessus, il est vert. Un seuil nul signifie « non configuré » et ne déclenche pas d'alerte. La page affiche un compteur d'alertes et une alerte textuelle ; les utilisateurs connectés peuvent la consulter, mais seuls les administrateurs peuvent modifier les seuils. La migration ajoute automatiquement la colonne `min_quantity` aux bases existantes.

La page `delivrance.html` permet à tout utilisateur connecté de scanner un code-barres produit enregistré sur la fiche stock, le numéro de série de la fiche ou le QR d'une sous-entité. Après sélection d'un patient existant, ou saisie du nom, prénom et de la date de naissance d'un nouveau patient, l'utilisateur renseigne également le médecin ayant pratiqué l'acte et la salle ; ces deux informations sont obligatoires pour valider la délivrance. La validation crée la délivrance et réduit le stock dans la même transaction. Une sous-entité QR scannée ne peut délivrer que sa quantité disponible. La page `historique-patients.html` affiche les délivrances, dont le médecin et la salle, et permet la recherche par patient, produit, code/numéro de série, médecin, salle, utilisateur et intervalle de dates ; elle est accessible aux utilisateurs connectés. Depuis la liste des patients dans `patients.html`, l'icône d'historique ouvre cette page filtrée sur le patient sélectionné. La page `patients.html` permet aux utilisateurs connectés de rechercher les fiches et de mettre à jour le nom, le prénom et la date de naissance ; les délivrances déjà enregistrées restent rattachées au patient. Les patients et leur historique de délivrances sont conservés dans SQLite ; l'identité du patient est aussi affichée dans l'historique administrateur des sorties sur `stock.html`. Les migrations de démarrage ajoutent automatiquement le champ code-barres et le lien patient aux bases déjà existantes.

L’agenda et le parcours patient sont accessibles sur `agenda.html` après migration PostgreSQL. Ils réutilisent l’identifiant du patient existant et l’authentification de l’application. Consultez [README-INSTALLATION.md](./README-INSTALLATION.md) pour la migration et les limites actuelles des notifications.

## Important pour la caméra

Les navigateurs mobiles exigent généralement HTTPS pour permettre l'accès direct à la caméra. En HTTP sur une adresse IP locale, utilisez le choix d'un fichier QR dans le scanner, ou placez le frontend derrière HTTPS.

## Pare-feu Windows

En HTTP, autoriser les ports TCP 5500 et 8000 sur le réseau privé. Avec Caddy en HTTPS, autoriser uniquement le port TCP 443 ; l'API reste accessible localement sur le PC.
