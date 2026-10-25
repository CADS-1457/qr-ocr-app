# Procédure de recette

## Base
- La migration apparaît dans `schema_migrations`.
- Les tables `appointments`, `patient_flow_history`, `follow_up_tasks`, `stock_movements` et `notifications` existent.
- `appointments.patient_id` et `follow_up_tasks.patient_id` sont des entiers référant aux fiches `patient.id` existantes.
- Les routes Agenda refusent les appels sans jeton Bearer de l’application ; la création de salles, types et ressources exige un rôle admin.

## Agenda
- Créer deux plages pour une même salle le même jour.
- Créer deux rendez-vous non chevauchants.
- Vérifier qu'un chevauchement dans la même salle est refusé.
- Vérifier les vues Présent, En salle, Réveil, Facturation et Terminé.

## Parcours patient
- Marquer le patient Présent.
- Transférer vers salle d'opération.
- Transférer vers réveil avec commentaire.
- Ajouter des instructions de sortie.
- Créer un suivi post-op.
- Vérifier l'historique horodaté et l'auteur.

## Stock
- En tant qu’admin, créer deux emplacements, des articles et des unités/lots depuis **Configurer**.
- Vérifier qu’un article avec suivi de lot ou de série exige les champs correspondants.
- Effectuer une sortie de plusieurs unités depuis la fenêtre de transfert.
- Sortir des articles provenant de deux stocks lors du même transfert.
- Vérifier les quantités et les mouvements OUT.
- Tester une quantité insuffisante : ni le transfert ni les sorties ne doivent être enregistrés.
- Les routes de retour et de libération de réservation ne sont pas encore intégrées.

## SMS et e-mail
- Non activés : le signalétique actuel ne possède ni téléphone, ni e-mail, ni consentements.
- Aucun worker ni fournisseur n’est intégré ; vérifier qu’aucun message n’est envoyé par cette version.
- Avant mise en production de l’envoi, compléter le signalétique, les préférences, l’historisation de `recipient`, l’idempotence et les reprises du worker.

## Sécurité
- Vérifier que les routes utilisent le jeton Bearer existant et que `X-User-Id` n’accorde aucun accès.
- Contrôler les permissions admin et utilisateur.
- Ne jamais inclure d'informations médicales sensibles dans le SMS.
- Journaliser chaque transfert et mouvement de stock.
