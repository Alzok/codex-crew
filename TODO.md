# TODO Numerus

- [x] Renommer officiellement la CLI en `numerus` (script d'entrée + messages) et ajouter `numerus start` pour le mode interactif.
- [x] Implémenter le Planner Codex : `codex exec` qui produit un plan JSON (tâches + dépendances + intentions sur les fichiers).
- [x] Créer la phase d'analyse des agents (claim) avec retour JSON des fichiers ciblés et type de modifications.
- [x] Ajouter au scheduler un système de verrous de ressources (fichiers/répertoires) et les statuts `analysis_pending`, `awaiting_go`, `executing`.
- [x] Implémenter la décision `GO/NO GO` + relance automatique des agents refusés.
- [x] Étendre `TerminalManager` pour supporter l'exécution confirmée (réutilisation du rapport validé) et, plus tard, le mode interactif `attach`.
- [x] Journaliser dans `events.ndjson` les claims, arbitrages et diff finaux.
- [x] Ajouter un mode Docker (Dockerfile + docs) pour exécuter Numerus dans un container.
- [x] Étendre la documentation (prompts types, format JSON attendu, exemples d'utilisation).
