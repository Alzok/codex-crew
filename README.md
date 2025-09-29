# Numerus – Orchestrateur local pour Codex CLI

## Vue d'ensemble
- **Objectif** : lancer depuis un terminal local (Docker ou bare metal) une supervision multi-agents reposant **exclusivement** sur Codex CLI (`codex`, `codex exec`).
- **CLI principale** : `numerus` (alias actuel : module Python `mcp.cli.app`). Exemple : `numerus start` pour initier un nouveau job.
- **Isolation** : chaque tâche Codex est exécutée dans un PTY dédié et un répertoire sandbox `./runs/<taskId>`.
- **Persistance** : état des tâches, verrous et journaux centralisés dans SQLite (`store/tasks.db`) + NDJSON par tâche.

## Cycle de fonctionnement
1. **Prompt utilisateur** via `numerus start`.
2. **Planificateur Codex** : une première instance `codex exec` élabore un plan JSON (tâches, dépendances, intentions sur les fichiers).
3. **Phase d'analyse (Claim)** : pour chaque tâche, l'orchestrateur ouvre un PTY « agent » et lui demande de détailler en JSON les fichiers à lire/modifier et la nature des changements envisagés (`runs/<job>/<task>_claim.json`).
4. **Arbitrage** : l'orchestrateur tient une table de verrous par fichier. Il répond :
   - `GO` si les ressources demandées sont libres (les verrous sont posés, puis l'exécution démarre avec un prompt `APPROVAL: GO`) ;
   - `NO GO` implicite si un autre agent les détient ou si une dépendance n'est pas satisfaite (le claim reste enregistré et l'agent sera relancé plus tard).
5. **Exécution** : sur `GO`, le même agent Codex applique les modifications (prompt enrichi avec le claim validé). À la fin, l'orchestrateur libère les verrous, enregistre les diff/test, et met à jour l'état. `TerminalManager` conserve les métadonnées (claim, ressources) et expose un mode `attach` pour un suivi interactif ultérieur.
6. **Boucle** : en fonction des résultats (succès/échec), l'orchestrateur peut re-planifier, relancer une analyse ou passer à la tâche suivante.

Ce cycle garantit qu'aucun agent n'écrit simultanément sur le même fichier et que les décisions restent pilotées par un superviseur local.

## Arborescence actuelle
```
docs/ARCHITECTURE.md        # Architecture cible et décisions
src/mcp/cli/app.py          # CLI numerus (run/status/logs/kill)
src/mcp/terminal/manager.py # Gestion PTY Codex + logs
src/mcp/orchestrator/worker.py # Worker individuel
src/mcp/orchestrator/job_runner.py # Orchestration plan/claim/exécution
src/mcp/store/sqlite.py     # Persistance SQLite
```

## Utilisation rapide
```bash
# Installation locale (editable)
python3 -m pip install -e .

# Mode interactif
python3 -m numerus start

# Lancer un job simple (plan + exécution démo)
python3 -m numerus run "echo hello"

# Inspecter les tâches
python3 -m numerus status
python3 -m numerus logs <taskId>
python3 -m numerus kill <taskId>
```

> **Artefacts** : chaque `run` crée `runs/<taskId>/plan.json` (plan Codex) puis `stdout.log`, `events.ndjson`, etc. Le binaire Codex CLI doit être disponible sur la machine (ou monté dans le container) et accessible via `$PATH` ou la variable `CODEX_BIN`.

## Prompts & formats JSON
- **Planificateur (`NUMERUS_PLAN V1`)** → JSON attendu :
  ```json
  {
    "objective": "...",
    "tasks": [
      {
        "id": "task-id",
        "summary": "objectif court",
        "description": "détails supplémentaires",
        "dependencies": ["autre-task-id"],
        "resources": {
          "reads": ["src/file.ts"],
          "writes": ["src/file.ts"]
        }
      }
    ]
  }
  ```
- **Analyse/claim (`NUMERUS_CLAIM V1`)** → JSON attendu par tâche :
  ```json
  {
    "task_id": "task-id",
    "resources": {
      "reads": ["src/file.ts"],
      "writes": ["src/file.ts"]
    },
    "execution": {
      "commands": ["npm test"]
    }
  }
  ```
- **Exécution (`NUMERUS_EXECUTE V1`)** : l'orchestrateur renvoie le claim validé dans la clé `RESOURCES` et ajoute `APPROVAL: GO`.

## Journaux & événements
- `runs/<jobId>/<taskId>/events.ndjson` → traces PTY (stdout/stderr, metadata, exit).
- `runs/<jobId>/events.ndjson` → événements d'orchestration :
  - `claim_recorded`, `claim_blocked`, `claim_unblocked`, `claim_approved`, `locks_released`
  - `task_completed`, `task_failed`
  - chaque entrée comporte `ts`, `event`, `task_id`, `payload`.
- `runs/<jobId>/<taskId>_claim.json` → claim validé et archivé.

## Mode Docker
```bash
# Construire l'image
docker build -t numerus .

# Lancer (monte un volume pour les runs + le binaire Codex local)
docker run --rm -it \
  -v $(pwd)/runs:/workspace/runs \
  -v $(pwd)/store:/workspace/store \
  -v /chemin/vers/codex:/usr/local/bin/codex:ro \
  numerus start
```
Le binaire Codex doit être monté dans le conteneur (exemple ci-dessus) et rendu exécutable. Ajustez les volumes selon vos besoins (workspace, caches, npm, etc.).
