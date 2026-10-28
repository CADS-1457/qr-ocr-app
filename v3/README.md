# QR-OCR-App V3 - kit de séparation et migration

Ce kit prépare le passage de la V2 vers une V3 modulaire FastAPI/PostgreSQL.

## Modules
`core`, `patients`, `providers`, `agenda`, `encounters`, `inami`, `insurability`, `billing`, `correspondence`, `inventory`, `notifications`, `integrations`, `audit`.

## Stratégie
- monolithe modulaire au départ ;
- un schéma PostgreSQL par domaine ;
- contrats de service explicites entre modules ;
- migration par étapes avec tables de correspondance V2 -> V3 ;
- double lecture temporaire, puis bascule des écritures ;
- rollback par restauration et retour du routage vers V2.

## Démarrage
```bash
cp .env.example .env
docker compose up -d postgres
psql "$DATABASE_URL" -f migrations/001_foundation.sql
psql "$DATABASE_URL" -f migrations/002_patient_household.sql
psql "$DATABASE_URL" -f migrations/003_clinical_business.sql
psql "$DATABASE_URL" -f migrations/004_health_correspondence.sql
psql "$DATABASE_URL" -f migrations/005_billing_delegation.sql
psql "$DATABASE_URL" -f migrations/005_ehealth_certificate_vault.sql
python scripts/preflight.py
python scripts/migrate.py --phase all --dry-run
```

Ne lancez pas la migration réelle avant d'avoir adapté `migration/source_mapping.example.yml` aux tables de la V2 et testé une restauration de sauvegarde.

## Courrier médical
Voir `CORRESPONDANCE_RESEAUX_SANTE.md`. Le canal externe est isolé dans `integrations/adapters/ehealthbox`.

## Certificat eHealth
Voir `EHEALTH_CERTIFICATE_SECURITY.md`. Les secrets ne sont pas stockés en clair dans PostgreSQL ou Git.
