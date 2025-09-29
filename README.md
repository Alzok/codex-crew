# Numerus – orchestrateur local Codex CLI

Numerus est un superviseur multi-agents qui décompose un objectif utilisateur, assigne des rôles (`planner`, `executor`, `reviewer`, `queen`) et exécute chaque sous-tâche via des sessions `codex exec` isolées dans des PTY. Aucune API OpenAI n’est utilisée : tout passe par le binaire `codex`.

## Installation
```bash
# Pré-requis : Python 3.10+, binaire `codex` disponible (ou `CODEX_BIN` défini)
python3 -m pip install -e .
```

## Commandes principales
- `python3 -m numerus start` : mode interactif (demande l’objectif, affiche le plan et suit l’exécution).
- `python3 -m numerus run "<objectif>"` : lance directement un job (planification + exécution).
- `python3 -m numerus status` : affiche la liste des jobs (statut, PID, chemins).
- `python3 -m numerus logs <taskId> [--follow]` : lit ou suit les logs d’une tâche.
- `python3 -m numerus kill <taskId>` : stoppe un job en cours (SIGTERM envoyé au worker).

## Cycle d’un job
1. **Plan** : Numerus invoque `codex exec` pour produire un plan JSON (`runs/<job>/plan.json`).
2. **Rôles** : un second passage `codex exec` assigne les rôles à chaque tâche.
3. **Claim** : pour chaque tâche, un agent propose les fichiers/commandes (JSON `*_claim.json`).
4. **Arbitrage** : Numerus verrouille les fichiers et renvoie `GO` ou `NO GO` (gestion des conflits).
5. **Exécution** : l’agent exécute la tâche ; stdout/stderr sont journalisés (`events.ndjson`, `stdout.log`).
6. **Boucle** : en cas d’échec/time-out, Numerus peut relancer ou re-planifier.

## Artefacts générés
- `runs/<job>/plan.json` : plan détaillé avec rôles.
- `runs/<job>/<task>_claim.json` : analyse avant exécution.
- `runs/<job>/<task>/stdout.log` et `events.ndjson` : logs PTY.
- `store/tasks.db` : état des jobs.
- `store/memory.db` : historique (objectif, plan, events, claims, etc.).

## Bus d’événements interne
`mcp.event_bus.EVENT_BUS` émet les événements temps réel :
- `job.*` (`plan_created`, `roles_assigned`, `claim_blocked`, `task_completed`, ...)
- `terminal.*` (`started`, `stdout`, `exit`, pool stats...)
- `memory.*` (`bank_created`, `entry_added`)

Usage rapide en Python :
```python
from mcp.event_bus import EVENT_BUS

unsubscribe = EVENT_BUS.subscribe('job.task_completed', lambda payload: print(payload))
```

## Docker (optionnel)
```bash
docker build -t numerus .
docker run --rm -it \
  -v $(pwd)/runs:/workspace/runs \
  -v $(pwd)/store:/workspace/store \
  -v /chemin/vers/codex:/usr/local/bin/codex:ro \
  numerus start
```

Todo liste et architecture détaillée : voir `docs/ARCHITECTURE.md`.
