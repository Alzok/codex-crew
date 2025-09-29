# Cartographie des référentiels `exemple/`

Cette synthèse passe en revue les fonctionnalités majeures des quatre dépôts de référence et précise si Numerus les
reprend (✅), les reprend partiellement (🟡) ou les écarte (❌), avec une justification concise.

## 1. `exemple/autogen-codex/`

| Domaine | Fonctionnalité clé | État | Commentaire |
|---------|--------------------|------|-------------|
| Orchestration | Supervisor / équipes & rôles dynamiques | ✅ | Cycle analyse→décomposition→assignation repris pour Numerus (plan/claim/exec). |
| Événementiel | Bus CloudEvents + handlers | ✅ | EVENT_BUS de Numerus fournit pub/sub et métriques internes, sans CloudEvents mais même principe. |
| Mémoire | Stores agents (vector, fichiers, etc.) | ✅ | MEMORY_MANAGER SQLite + cache par job ; pas encore vectoriel. |
| Re-planification | Analyse heuristique multi-stratégies | 🟡 | Numerus déclenche replan via rôles/locks mais sans heuristique avancée. |
| Outils externes | Large panel d’outils (Git, Web, API) | ❌ | Numerus s’en tient volontairement à Codex CLI. |
| Collaboration | Templates “Team of Agents” + prompts sophistiqués | ✅ | RolePlanner s’inspire des prompts Autogen pour répartir les rôles. |
| Intégrations | Hébergeurs cloud, Slack, etc. | ❌ | Hors scope local. |

## 2. `exemple/ccswarm-codex/`

| Domaine | Fonctionnalité clé | État | Commentaire |
|---------|--------------------|------|-------------|
| PTY & Sessions | `portable-pty`, pool de sessions, attach | ✅ | TerminalSession/Pool + attach/send implémentés dans Numerus. |
| Circuit breaker | Gestion des erreurs spawn/IO | ✅ | CircuitBreaker + retry sur spawn Codex & SQLite. |
| UI/TUI | Interface curses + dashboards | ❌ | Numerus reste CLI headless. |
| Quality Judge | LLM juge structurel (analyse diff/tests) | ❌ | À envisager plus tard (non repris). |
| Memory & persistence | SQLite state, artefacts, snapshots | ✅ | TaskStore SQLite + MEMORY_MANAGER. |
| Approvals | Workflow approvals multi-niveaux | 🟡 | Numerus applique GO/NO GO + claims verrouillés mais pas d’UI approbations. |
| Plugins/extensibilité | Crates modulaires (extensions) | ❌ | Non repris (design Python simple). |
| Observabilité | Health checks, metrics, tracing | 🟡 | EVENT_BUS fournit stats, pas de dashboard complet. |

## 3. `exemple/claude-flow/`

| Domaine | Fonctionnalité clé | État | Commentaire |
|---------|--------------------|------|-------------|
| Hive Mind | Queen + topologies + consensus | 🟡 | Numerus introduit rôles (queen/planner/executor/reviewer) mais sans topologies ni consensus complet. |
| Flow Nexus Cloud | Sandboxes E2B, marketplace, challenges | ❌ | Hors scope (exécution purement locale). |
| Hooks & automation | Système avancé de hooks pré/post actions | ❌ | Non repris—workflow Numerus reste séquentiel. |
| Monitoring | Swarm metrics dashboards, web UI | ❌ | Pas de monitoring visuel. |
| MCP tools | 80+ outils, wrappers Anthropic | ❌ | Nous limitons à Codex CLI par consigne. |
| Memory banks | SQLite + TTL, indexation | ✅ | MEMORY_MANAGER dérivé du modèle Claude-Flow (banques par job, cache). |
| Role templates | Patterns queen/planner/executor/reviewer | ✅ | RolePlanner reprend ces rôles et notes stratégiques. |
| Automation scripts | Scripts `bin/*` (swarm, hooks, UI) | ❌ | Aucun script Node repris. |
| Benchmarking | Suite bench swarms/perf | ❌ | Hors périmètre Numerus. |

## 4. `exemple/codex/` (CLI officielle)

| Domaine | Fonctionnalité clé | État | Commentaire |
|---------|--------------------|------|-------------|
| CLI `codex`/`codex exec` | Mode interactif & exec | ✅ | Numerus invoque exclusivement `codex exec` et prépare attach/send. |
| Résumé/Resume | `codex exec resume` | 🟡 | Reprise non intégrée ; Numerus repart de zéro par tâche. |
| MCP Server | `codex-rs` server/tooling | ❌ | Pas de serveur MCP ; Numerus reste orchestrateur local. |
| TUI | `codex tui` | ❌ | Non intégré. |
| Approvals CLI | Approvals interactifs | 🟡 | Approvals gérés côté superviseur (GO/NO GO) sans UI dédiée. |
| Scripts packaging | Build/bundles multiplateformes | ❌ | Non pertinent pour Numerus (python). |

## Synthèse adoption Numerus

- **Adopté en priorité** : PTY pool, EventBus, mémoire persistante, roles (queen/planner/executor/reviewer), circuit breakers.
- **Partiel** : Re-planification heuristique, approvals avancés, observabilité, reprise de tâches.
- **Non retenu** : UI/TUI, quality judge, Flow Nexus/cloud, automation hooks massifs, marketplace/outils externes.

Ces choix alignent Numerus sur un orchestrateur local, robuste aux erreurs, avec journalisation riche, sans dériver vers une
plateforme complète multi-cloud.

