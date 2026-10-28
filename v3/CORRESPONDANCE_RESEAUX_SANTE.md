# Courrier médical et réseaux de santé

## Canal V3 prioritaire
Le module `correspondence` produit et valide le courrier. Le module `integrations` assure la transmission externe. Pour l'échange direct de courrier médical entre acteurs de soins belges, prévoir un adaptateur `eHealthBox`. Ne jamais coder le protocole eHealth dans le module métier.

## Flux
1. Consultation clôturée.
2. Création d'un courrier depuis un modèle versionné.
3. Fusion des variables autorisées.
4. Relecture et validation humaine.
5. Résolution explicite du destinataire professionnel.
6. Création d'une transmission idempotente.
7. Envoi par l'adaptateur de réseau de santé configuré.
8. Enregistrement de l'identifiant externe et des statuts/accusés disponibles.
9. Audit de l'action, sans recopier le contenu clinique dans le journal d'audit.

## Tables
- `correspondence.template`: modèles/version/langue/variables.
- `correspondence.document`: instance du courrier clinique validé.
- `correspondence.recipient`: destinataire professionnel et identifiant réseau.
- `correspondence.attachment`: pièces jointes avec hash d'intégrité.
- `correspondence.transmission`: canal, statut, corrélation, accusés, erreurs et idempotence.
- `integration.external_exchange`: trace technique chiffrée et limitée dans le temps.

## Règles
- Un document validé est figé; une correction crée une nouvelle version/document.
- Le destinataire doit être sélectionné et vérifié, jamais déduit uniquement d'un nom.
- Le module courrier reste fonctionnel même si le réseau externe est indisponible: la transmission passe en état contrôlé (`queued`/`failed`) sans perdre le document.
- Les secrets/certificats restent hors base métier et sont référencés par `credential_secret_ref`.
- Les données médicales ne doivent pas être envoyées par e-mail classique comme substitut automatique au canal de santé configuré.
