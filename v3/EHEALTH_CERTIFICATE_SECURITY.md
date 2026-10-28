# Certificat eHealth et coffre de secrets

## Principe V3
Le certificat eHealth et son mot de passe associé doivent être configurables une seule fois pour les connexions système-à-système qui les utilisent. La base PostgreSQL ne conserve pas le fichier PKCS#12/P12 ni le mot de passe en clair. Elle conserve uniquement des références vers un coffre de secrets.

## Stockage recommandé
- `integration.credential`: métadonnées, portée, empreinte, validité et références de secrets.
- `certificate_secret_ref`: pointeur vers le certificat dans Docker Secrets/Vault/secret manager.
- `password_secret_ref`: pointeur séparé vers son mot de passe.
- permission de lecture réservée au processus d'intégration.
- aucun secret dans logs, audit, exceptions, fichiers `.env` versionnés ou réponses API.

## Portée
La credential peut être liée à l'organisation, à l'établissement et/ou au prestataire. Le résolveur choisit la credential active correspondant au contexte de l'appel.

## Cycle d'utilisation
1. Un administrateur autorisé installe le certificat dans le secret backend.
2. L'application sauvegarde seulement les références et métadonnées.
3. Lors d'un appel eHealth, l'adaptateur charge la credential en mémoire, l'utilise, puis libère les données sensibles.
4. `last_used_at` et un événement technique sans secret sont enregistrés.
5. Une alerte applicative peut utiliser `valid_until` pour signaler une expiration prochaine.

## Interdictions
- ne jamais demander le mot de passe à chaque courrier si une credential autorisée est configurée ;
- ne jamais stocker le mot de passe en clair en PostgreSQL ;
- ne jamais placer le P12 dans Git/GitHub ;
- ne jamais inclure le secret dans une sauvegarde applicative non chiffrée.
