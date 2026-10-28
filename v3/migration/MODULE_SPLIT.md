# Séparation des modules

## Règle de dépendance
Un module peut appeler l'interface publique d'un autre module, pas son repository ni ses tables directement depuis le code métier.

## Contrats
- `patients`: recherche d'identité, contacts, foyers, relations.
- `agenda`: rendez-vous et groupes de réservation familiale.
- `encounters`: épisode clinique et feuilles spécialisées.
- `inami`: recherche d'un code et tarif valable à une date.
- `insurability`: contrôle horodaté et snapshot des droits.
- `billing`: services, claims et lignes; aucun accès direct à MyCareNet.
- `integrations`: ports/adaptateurs externes et journal des échanges.

## Événements internes
`AppointmentBooked`, `AppointmentCompleted`, `EncounterClosed`, `ServicePerformed`, `ClaimSubmitted`, `StockConsumed`, `DocumentValidated`.

## Activation
Lire `core.facility_module` au démarrage. Refuser l'activation si une dépendance n'est pas active. Les migrations de base restent appliquées, même si le module est désactivé.
