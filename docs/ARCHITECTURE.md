# Orchestrateur MCP – Architecture

## Contexte
- Objectif : orchestrer des sous-tâches Codex CLI isolées (PTY) pour exécuter `mcp run`, `mcp status`, `mcp attach`, `mcp logs`, `mcp kill`.
- Domaine : automatiser la décomposition/replanification d'objectifs utilisateurs via Codex CLI uniquement, aucune API OpenAI.
- Références locales :
  - `exemple/ccswarm-codex/` (Rust) — gestion avancée de sessions et PTY.
  - `exemple/autogen-codex/` (AutoGen) — patterns de superviseur/équipe et replanification.
  - `exemple/codex/` (Codex CLI) — modes interactif vs `codex exec`, approvals, usage CLI.

## Contraintes clés
- Un PTY dédié par sous-tâche avec dossier de travail isolé (`./runs/<taskId>`).
- Autoriser uniquement Codex CLI (`codex`, `codex exec`), plus une allow-list métier configurable.
- Gestion des approvals localement (pas d'API externes) et respect des modes Codex.
- Journalisation horodatée (stdout/stderr/événements) + persistance d'état redémarrage.
- Linux/WSL cible. Tests E2E requis (succès, erreurs, parallélisme, chemins WSL).

## Analyse rapide des bases possibles

| Critère | Reprendre `ccswarm-codex` (Rust) | Nouvelle base Python |
| --- | --- | --- |
| Gestion PTY | Implémentation portable (`portable-pty`) + pooling, mais couplée à l'écosystème ccswarm (heavy) | `os.openpty` + `subprocess` suffisent pour un PTY UNIX léger |
| Supervision/Replan | Orchestrateur existant mais très lié à l'architecture ccswarm (agents, bus, UIs) | Facile de re-modéliser superviseur Async Python inspiré AutoGen |
| Vitesse de mise en œuvre | Nécessite forte adaptation + renommer modules, comprendre macro orchestrator | Construire modules ciblés rapidement, écosystème batteries-included |
| Extensibilité future | Performant, typé, mais plus coûteux pour modifications rapides | Python dynamique, facile à étendre pour heuristiques et tests rapides |
| Empreinte & dépendances | Workspace Rust multi-crates, compilation lourde | Librairie standard + SQLite builtin, packaging simple |

## Décision
Nous partons sur **une base Python asynchrone** : elle permet d'itérer vite sur le superviseur/replanification, d'intégrer facilement les patterns AutoGen (publisher/supervisor), tout en s'appuyant sur la librairie standard (`os.openpty`, `subprocess`) pour gérer les PTY Codex. Les briques `ccswarm` serviront d'inspiration ponctuelle (interface TerminalManager, séparation des stores) mais sans fork direct.

## Architecture cible (itération 1)

```
+----------------------+         +----------------------+         +----------------------+
|        CLI           |  mcp *  |   Orchestrator       |  tasks  |     TerminalManager  |
|     (argparse)       +--------->  Planner + Scheduler  +-------->  PTY per task        |
|  run/status/logs/    |         |  State Machine        |         |  codex / codex exec  |
|  attach/kill         |         |                      |         |  logs + allow-list   |
+----------+-----------+         +-----+-----------------+         +----------+-----------+
           |                           |                                         |
           | status/metrics            | state mutations                         | stdout/stderr
           v                           v                                         v
+----------------------+         +----------------------+         +----------------------+
|       Store          | <------ |   Event Bus /        | <------ |    Log Persister     |
|  SQLite + artefacts  | states  |   Supervisor events  | events  |  NDJSON per task     |
+----------------------+         +----------------------+         +----------------------+
```

### Modules
- `src/cli/` — commandes `mcp` (`run`, `status`, `logs`, `attach`, `kill`).
- `src/orchestrator/` — superviseur, planner, scheduler, stratégie de retry.
- `src/terminal/` — `TerminalManager` (spawn PTY, exec, logs, kill, attach future).
- `src/agents/` — wrappers rôles Codex (à étendre plus tard pour spécialistes).
- `src/store/` — persistance (SQLite ou JSON), index des tâches, lecture des logs.

### Stockage & logs
- Répertoire `./runs/<taskId>/` pour chaque tâche : copie sandbox, logs (`stdout.log`, `stderr.log`, `events.ndjson`).
- Métadonnées de tâches conservées dans `store/tasks.db` (SQLite) + snapshot JSON pour inspection rapide.
- `TerminalManager` broadcast les événements (`started`, `stdout`, `stderr`, `exit`, `error`) au superviseur + persistance.

### Approvals & sécurité
- Allow-list de commandes par défaut : `git`, `npm`, `pnpm`, `pytest`, `cargo`, etc. Custom via config.
- Timeouts (commande & tâche) gérés par superviseur et PTY wrapper (`pexpect` timeouts).
- Opérations sensibles (écriture hors sandbox, `push`, `rm -rf`) nécessitent confirmation manuelle via CLI (future `mcp approve`).

### Étapes suivantes
1. Implémentation initiale (`TerminalManager`, CLI basique, store minimal JSON).
2. Planner/scheduler heuristiques (décomposition, dépendances, retry/replan).
3. Support `attach` interactif, surveillance en continu (`logs --follow`).
4. Tests E2E supplémentaires (parallélisme, WSL paths, erreurs, approvals).
