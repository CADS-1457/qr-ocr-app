# Installation de l’agenda et du parcours patient

Le module s’intègre à l’API existante : il utilise la même base PostgreSQL, le même jeton Bearer et les mêmes patients que l’application QR/OCR. `patient_id` reste un entier et référence `patient.id` ; aucun second signalétique n’est créé.

## Prérequis

- Docker Compose et le fichier `.env` déjà requis par le projet ;
- le service PostgreSQL `db` défini dans `docker-compose.yml` ;
- une sauvegarde PostgreSQL récente avant d’appliquer la migration.

Le module Agenda nécessite PostgreSQL. Le mode SQLite local existant reste disponible pour le cœur QR/OCR, mais la migration Agenda PostgreSQL ne doit pas être appliquée à SQLite.

## Déploiement

Depuis la racine du dépôt, démarrer d’abord l’API afin qu’elle crée les tables principales `patient` et `useraccount` :

```powershell
docker compose up -d --build api
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > .\pre_agenda.dump
```

Vérifier que le fichier `pre_agenda.dump` a été créé et n’est pas vide. Appliquer ensuite la migration :

```powershell
Get-Content .\database\010_agenda_patient_flow_stock.sql -Raw |
  docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Appliquer ensuite la migration `011_stock_legacy_link.sql`, qui relie le stock historique (`stockitem`, `stockqrunit`) au stock de l’agenda (`stock_items`, `stock_units`) :

```powershell
Get-Content .\database\011_stock_legacy_link.sql -Raw -Encoding UTF8 |
  docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Elle importe les articles et les unités existants (emplacement par défaut « Réserve 1 ») et installe des déclencheurs : toute création, modification ou suppression dans le stock historique est répercutée dans l’agenda, et toute sortie de stock faite depuis l’agenda décrémente aussi le stock historique. Le stock historique reste la source de vérité.

Puis appliquer `012_stock_location_hierarchy.sql` : un emplacement peut contenir des sous-emplacements (Réserve 1 › Armoire A › Tiroir 2) et être associé à plusieurs salles (table `stock_location_rooms`). Une salle accède aux emplacements qui lui sont associés et à leurs sous-emplacements (`GET /api/agenda/stock-units?room_id=...`). L’ancienne colonne `stock_locations.room_id` n’est plus utilisée ; ses valeurs ont été reprises dans la nouvelle table.

```powershell
Get-Content .\database\012_stock_location_hierarchy.sql -Raw -Encoding UTF8 |
  docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Puis appliquer `013_scheduling_blocks.sql` : plages de rendez-vous (table `scheduling_blocks`) par salle, jour de semaine et éventuellement agenda de médecin, avec un pas de programmation en minutes et une période de validité. Une salle sans plage active reste librement réservable ; dès qu’elle en possède, `POST /api/agenda/appointments` n’accepte que les créneaux alignés sur le pas et inclus dans une plage (fuseau Europe/Brussels). Les administrateurs gèrent les plages via le bouton **Plages de rendez-vous** du calendrier (`/api/agenda/scheduling-blocks`) ; `GET /api/agenda/availability` alimente l’affichage.

```powershell
Get-Content .\database\013_scheduling_blocks.sql -Raw -Encoding UTF8 |
  docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Puis reconstruire l’application et le frontend :

```powershell
docker compose up -d --build api frontend
```

La migration peut être vérifiée ainsi :

```powershell
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version FROM schema_migrations ORDER BY version;"'
```

Ouvrir `http://localhost:8080/agenda.html`, se connecter avec un compte existant, puis utiliser **Configurer** (compte administrateur) pour ajouter les salles, les types de rendez-vous, les ressources et le stock multi-emplacements. La page `agenda-calendrier.html` offre une vue calendrier (jour, semaine, mois, façon Outlook) alimentée par `GET /api/agenda/range-view`. L’API expose aussi les opérations du module sous `/api/agenda`.

## Signalétique et coordonnées de contact

Les rendez-vous et les suivis référencent directement les patients de l’application. L’adaptateur `/api/agenda/patients/{id}/contact` lit `prenom` et `nom`, ainsi que le mobile, l’e-mail, la langue et les consentements SMS/e-mail saisis dans la fiche patient (`patients.html`). Les consentements sont désactivés par défaut et ne peuvent être activés que si la coordonnée correspondante est renseignée. Les colonnes sont ajoutées automatiquement au démarrage de l’API sur les bases existantes. Aucun message n’est toutefois envoyé par cette intégration.

La migration crée la structure de préférences, de modèles et de file de notifications, mais aucun fournisseur ni worker d’envoi n’est configuré. Avant d’activer des communications, il faudra ajouter et gérer les coordonnées/consentements du signalétique, puis intégrer un fournisseur et un worker conforme aux exigences de confidentialité.

## Fonctionnement local

L’API existante peut toujours démarrer avec SQLite comme décrit dans [README.md](./README.md). Dans ce mode, l’import du module Agenda ne bloque pas le démarrage, mais les fonctionnalités Agenda et la migration restent réservées au déploiement PostgreSQL Docker.
