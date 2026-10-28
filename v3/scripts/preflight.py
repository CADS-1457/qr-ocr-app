import os, sys
required = ['DATABASE_URL','V2_DATABASE_URL','NISS_HMAC_SECRET','FIELD_ENCRYPTION_KEY']
missing=[k for k in required if not os.getenv(k)]
if missing:
    print('Variables manquantes:', ', '.join(missing)); sys.exit(2)
if len(os.environ['NISS_HMAC_SECRET']) < 32:
    print('NISS_HMAC_SECRET doit contenir au moins 32 caractères'); sys.exit(2)
print('Préflight OK: variables présentes. Vérifier ensuite les connexions et sauvegardes.')
