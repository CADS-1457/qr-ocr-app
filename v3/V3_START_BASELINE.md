# Baseline de démarrage V3

Cette baseline fige les décisions prises pour lancer la construction :

1. Monolithe modulaire FastAPI avec PostgreSQL.
2. Modules : patients, providers, agenda, encounters, INAMI, insurability, billing, correspondence, inventory, notifications, integrations, audit.
3. Patients : contacts partagés, foyers, relations, GSM non unique, réservations familiales.
4. Courrier médical : génération/version/validation, destinataire professionnel, pièces jointes et transmission via adaptateur de réseau de santé.
5. Intégrations eHealth : Credential Manager, référence vers coffre à secrets, certificat séparé de l'identité de l'utilisateur.
6. Facturation : la personne qui encode est distincte du prestataire qui réalise la prestation.
7. Délégation : une secrétaire peut encoder, associer un prestataire, valider ou transmettre uniquement selon les autorisations effectives.
8. Audit obligatoire des opérations sensibles et transmissions.
9. Migration V2 -> V3 par phases avec id_map, rejets, dry-run, réconciliation et rollback.

## Ordre de construction
- Lot A : Core + sécurité + migrations + audit.
- Lot B : Patients + familles + contacts partagés.
- Lot C : Prestataires + Agenda + Consultations + feuilles par spécialité.
- Lot D : Stock + notifications + courrier.
- Lot E : INAMI + nomenclature + tarifs.
- Lot F : Credential Manager + intégrations eHealth/MyCareNet.
- Lot G : Assurabilité + facturation + délégations + transmission.

Toute nouvelle fonction doit se rattacher à un module et passer par le service public du module, pas par les tables d'un autre domaine directement.
