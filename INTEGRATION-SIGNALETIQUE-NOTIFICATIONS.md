# Intégration signalétique et notifications

## Signalétique patient existant

Le module Agenda ne crée pas de seconde fiche patient. `appointments.patient_id`, `follow_up_tasks.patient_id`, `stock_movements.patient_id`, `patient_communication_preferences.patient_id` et `notifications.patient_id` référencent directement `patient.id` de l’application, dont le type est entier.

L’adaptateur `get_patient_contact()` dans `backend/agenda_module/router.py` lit le nom et le prénom du modèle existant. Il ne recherche que des colonnes autorisées correspondant à `mobile`/`telephone`/`phone`, `email`/`courriel` et `language`/`langue`, sans supposer leur présence. L’API `GET /api/agenda/patients/{patient_id}/contact` retourne les champs disponibles ; les autres sont `null`.

Le modèle patient stocke désormais, en plus du nom, du prénom et de la date de naissance : `mobile`, `email`, `language` (fr, nl, de, en), `sms_consent` et `email_consent` (désactivés par défaut). Ils se gèrent depuis la fiche patient (`patients.html`) et restent dans le signalétique, source de vérité : ne pas les recopier dans le module Agenda. Supprimer un mobile ou un e-mail retire automatiquement le consentement correspondant.

## Notifications

La migration ajoute une file commune SMS/e-mail, des modèles et des préférences par canal. `notifications.recipient` et `recipient_name` sont prévus pour conserver une copie de la destination réellement utilisée, mais l’intégration actuelle ne planifie ni n’envoie de notification. Aucun fournisseur, worker, mécanisme de reprise ni gestion de consentement n’est configuré.

Avant toute activation :

1. faire valider la conformité du recueil des coordonnées et consentements, désormais saisis dans la fiche patient ;
2. définir et faire valider la politique de communication et les modèles ;
3. intégrer un fournisseur derrière un worker séparé, avec verrouillage des tâches, reprises maîtrisées et idempotence ;
4. ne jamais inclure de diagnostic ou d’information médicale dans les SMS ;
5. historiser le destinataire utilisé tout en conservant le signalétique comme source de vérité.

La procédure d’installation et les limites actuelles sont décrites dans [README-INSTALLATION.md](./README-INSTALLATION.md).
