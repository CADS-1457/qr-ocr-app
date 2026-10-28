# Méthode de transfert V2 vers V3

## 1. Préparation
1. Créer une branche `v3/modular-migration` à partir de la branche V2 validée.
2. Exporter le schéma V2 et les volumes par table.
3. Faire une sauvegarde complète cohérente et tester sa restauration.
4. Adapter `source_mapping.example.yml` sans modifier la V2.
5. Définir la clé d'organisation et l'établissement par défaut.

## 2. Profilage
- Compter les patients, rendez-vous, documents, mouvements de stock et utilisateurs.
- Repérer les NISS invalides ou en double.
- Normaliser les GSM belges en E.164 pour la comparaison, sans les utiliser comme identité.
- Détecter les GSM partagés et produire des propositions de foyers, sans association automatique.
- Contrôler les rendez-vous orphelins, dates invalides et références manquantes.

## 3. Chargement par phases
Ordre recommandé : core -> patients -> contacts -> foyers confirmés -> prestataires -> agenda -> consultations -> stock -> courrier -> notifications -> INAMI.

Chaque ligne migrée reçoit une correspondance dans `migration.id_map`. Les lignes refusées vont dans `migration.reject`. Les scripts doivent être idempotents : une relance ne crée pas de doublon.

## 4. Contacts et familles
- Un numéro normalisé correspond à une ligne `patient.contact_point`.
- Plusieurs `patient.patient_contact` peuvent référencer le même contact.
- Un GSM partagé déclenche un rapport de rapprochement.
- Le foyer et la relation familiale ne sont créés qu'après validation humaine.
- Les autorisations de prise de rendez-vous restent distinctes des droits d'accès au dossier médical.

## 5. Bascule
1. Exécuter une répétition sur une copie récente.
2. Comparer les volumes et échantillons fonctionnels.
3. Mettre V2 en lecture seule.
4. Exécuter le delta final.
5. Valider les contrôles de réconciliation.
6. Basculer l'API et les workers vers V3.
7. Conserver V2 en lecture seule selon la décision interne.

## 6. Rollback
- Arrêter les écritures V3.
- Conserver les journaux et identifiants de migration.
- Restaurer la sauvegarde prévue ou réactiver temporairement V2.
- Ne jamais fusionner manuellement les modifications V3 dans V2 sans procédure de rapprochement.

## 7. Critères de validation
- Totaux V2/V3 expliqués par les rejets documentés.
- 100 % des rendez-vous actifs avec patient et établissement valides.
- Aucun NISS ni GSM journalisé en clair.
- Les GSM partagés retournent plusieurs patients sans fusion automatique.
- Les mouvements de stock reproduisent le solde attendu.
- Les documents restent accessibles, avec hash ou contrôle d'intégrité.
