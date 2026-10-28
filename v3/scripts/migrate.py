import argparse, os, uuid, json
PHASES=['core','patients','contacts','households','providers','agenda','encounters','inventory','documents']

def run_phase(name, dry_run):
    # Point d'extension: connecter les repositories V2/V3 et traiter par lots.
    # Ne jamais écrire de NISS/GSM en clair dans les logs.
    print(json.dumps({'phase':name,'dry_run':dry_run,'status':'planned'}))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--phase', default='all', choices=['all']+PHASES)
    p.add_argument('--dry-run', action='store_true')
    args=p.parse_args()
    for phase in (PHASES if args.phase=='all' else [args.phase]): run_phase(phase,args.dry_run)
if __name__=='__main__': main()
