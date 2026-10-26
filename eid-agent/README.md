# Agent de lecture de la carte d'identité belge (eID)

Petit service à lancer **sur le poste de l'accueil équipé du lecteur USB** (pas sur le serveur).
Le navigateur l'interroge sur `http://127.0.0.1:8765` quand on clique sur « Lire la carte eID » dans la fenêtre « Nouveau patient ».

## Installation (Windows)
1. Brancher un lecteur de cartes compatible PC/SC (le service « Carte à puce » de Windows doit être actif).
2. Installer Python 3.10+ puis : `pip install -r requirements.txt`
3. Lancer : `python eid_agent.py`
   - Autre adresse du site : `python eid_agent.py --origin https://mon-site`
   - Autre port : `--port 8800` (à reporter dans le navigateur : `localStorage.eidAgentUrl = "http://127.0.0.1:8800"`).
4. Pour un démarrage automatique : créer une tâche planifiée « À l'ouverture de session » exécutant la commande ci-dessus.

## Sécurité et vie privée
- Écoute uniquement sur 127.0.0.1 ; seules les origines autorisées peuvent lire la carte.
- Renvoie l'identité (nom, prénoms, date et lieu de naissance, sexe, nationalité), l'adresse, la photo (JPEG) et le numéro national (validé par sa clé modulo 97). Ces données restent sur le poste jusqu'à ce que l'utilisateur enregistre la fiche patient.
- Le numéro national et la photo sont enregistrés dans la fiche patient (accès réservé aux utilisateurs connectés) pour les échanges registre national / assurabilité.
- Aucun code PIN n'est nécessaire : seul le fichier d'identité public est lu.
