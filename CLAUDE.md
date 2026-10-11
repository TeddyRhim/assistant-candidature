# assistant-candidatures

Application locale (Python, Streamlit, SQLAlchemy et SQLite) d'aide à la recherche d'emploi : elle collecte des offres, les compare à un profil, génère un CV ciblé et une lettre en PDF, et suit les candidatures. Rien n'est envoyé automatiquement : l'utilisateur postule lui-même.

## Commandes
- Installer : `uv sync`.
- Interface : `uv run streamlit run src/app.py`.
- Veille (collecte quotidienne) : `uv run python -m src.cli daily`, ou le bouton de la page Veille. Code retour 1 si le profil est incomplet.
- File des CV à soigner : `uv run python -m src.cli soigner list|show <id>|save <id> <fichier.json>|state <id> <état>`, pilotée par le skill local `soigner-cv` (`.claude/skills/`, non versionné).
- Tests : `uv run pytest`. Lint : `uv run ruff check .` (longueur de ligne 100, règles E, F, I, UP, Python 3.11). La CI GitHub exécute les deux.
- CV autonome : `uv run python render.py [entrée.json sortie.pdf]`.
- Tâche planifiée Windows (facultative, non utilisée par défaut) : `scripts/install_daily_task.ps1`.

## Lancer une veille
1. Vérifier que le profil est renseigné et que les clés d'API sont disponibles (variables d'environnement ou `.streamlit/secrets.toml`, modèle dans `.streamlit/secrets.toml.example`).
2. Lancer la commande en arrière-plan, lire le résumé (nouvelles offres, doublons, titres exclus) et les lignes d'erreur (429 Adzuna, quota Jooble).
3. Prévenir l'utilisateur à la fin : nombre de nouvelles offres, de dossiers prêts, erreurs, quota Jooble restant. Lui rappeler qu'il postule lui-même.
Ne jamais remettre à zéro le compteur de quota Jooble sans raison. Adzuna : une zone à la fois, pauses, reprise par curseur.

## Structure
- `src/app.py` (interface, très long), `src/cli.py`, `src/config.py`, `src/db.py`, `src/models.py`.
- `src/services` : `job_watcher.py` (cycle de veille, filtres, doublons), `daily_run.py`, `matching.py`, `job_titles.py`, `send_queue.py`, `dossier_generator.py`, `tailored_resumes.py`, `offer_keywords.py` (mots de l'annonce, adaptation du CV), `recent_applications.py` (offres déjà visées cette semaine), `cv_curation.py` (état du CV : Automatique, À soigner, Soigné, et contrôle anti-invention), `cv_renderer.py` (Jinja2 et WeasyPrint), `cover_letters.py`, `applications.py`, `email_delivery.py`.
- `src/services/job_sources` : un connecteur par source (Adzuna, France Travail, Jooble, Himalayas et RemoteOK, Greenhouse, Lever, La Bonne Boîte).
- `templates/` (CV, lettre), `docs/` (architecture, sources, feuille de route), `tests/`.

## Variables (noms seulement)
`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `FRANCE_TRAVAIL_CLIENT_ID`, `FRANCE_TRAVAIL_CLIENT_SECRET`, `JOOBLE_API_KEY`, `MAILJET_*`, `ASSISTANT_CANDIDATURES_DATA_DIR`. Lues d'abord dans l'environnement, puis dans `.streamlit/secrets.toml`. Aucun `.env` n'est chargé automatiquement.

## Données privées (jamais dans git)
`data/` (base SQLite et sauvegardes, CV réels, brouillons, journaux, compteurs de quota, configuration de veille), `.env*`, `.streamlit/secrets*.toml`, `output/`, `*.pdf`, `.claude/`, notes internes de `docs/`. Les tests ne doivent jamais lire ni écrire dans `data/` : `conftest.py` l'isole. Tests, captures et exemples utilisent des données fictives (profil d'exemple), jamais le vrai nom, la vraie zone de recherche ni des entreprises réellement contactées.

## Règles métier
- Les exclusions personnelles, la zone prioritaire, le seuil de score et les interrupteurs par source se règlent dans la page Veille ou dans `data/watcher_config.json`, jamais en dur dans le code ni dans les tests.
- Filtres : titres de stage et d'alternance, rôles hors développement, contrats exclus, annonces ni en français ni en anglais, score sous le seuil. Doublons : même URL ou même clé normalisée (titre, entreprise, lieu).
- Une offre dont l'entreprise et le poste correspondent à une candidature envoyée dans les 7 derniers jours est ignorée par la veille et masquée de la file d'envoi.
- Chaque offre a une origine (`origin` : Application ou Manuel). Ce que l'utilisateur saisit (formulaire ou lien) est « Manuel » ; la veille et les recherches sont « Application ». La file d'envoi les sépare en onglets, et la page Offres a un filtre.
- Une offre ajoutée à la main (formulaire ou lien) passe à l'état de CV « À soigner » ; celles de la veille restent « Automatique ». L'application ne gère que l'état ; le skill `soigner-cv` rédige le JSON, `soigner save` le refuse s'il contient un terme technique, un chiffre ou un fait absent du CV de base, puis l'état passe à « Soigné ». Compléter le texte d'une annonce régénère le CV et remet l'état à « À soigner ».
- Le CV adapté ne fait que réordonner, réduire (6 puces par expérience au plus) et mettre en gras ce que le CV contient déjà ; un mot demandé mais absent n'est jamais ajouté, seulement signalé.
- Le nom de l'entreprise n'apparaît jamais dans le nom de fichier du CV ni dans le CV lui-même ; il reste dans la lettre.
- Ne pas pousser les références locales `refs/agents/*` (points de reprise non publiés).
