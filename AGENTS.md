# Agent Instructions

## Commit Policy

**NE PAS faire de commit automatiquement.**

Les commits ne doivent être faits que sur demande explicite de l'utilisateur.

## TODO / Améliorations futures

### Base de données
- [ ] Optimiser les connexions SQLite: utiliser une connexion par requête HTTP avec FastAPI `Depends(get_db)` au lieu d'ouvrir/fermer une connexion par opération DB. Réduirait l'overhead et éviterait les problèmes de "database locked".
