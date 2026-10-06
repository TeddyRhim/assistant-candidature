# Feuille de route

## Phase 0 — Cadrage et données d'entrée

- Valider les préférences de recherche : technologies, niveau, secteurs, lieux, contrats et mobilité.
- Examiner le CV de référence et décider des formats d'import/export à prioriser.
- Choisir une première source d'offres compatible avec ses conditions d'utilisation ; garder l'ajout manuel comme voie de secours.
- Définir les faits qui peuvent être reformulés et le niveau de contrôle souhaité.

**Livrable :** profil cible et source V1 documentés.

## Phase 1 — Socle local

- Environnement Python reproductible, démarrage Streamlit et configuration du dossier de données.
- Schéma SQLite/SQLAlchemy et sauvegarde/restauration.
- Navigation et écrans vides : profil, offres, entreprises, candidatures.
- Tests et vérification de style automatisables localement.

**Terminé lorsque :** l'application démarre, persiste un enregistrement de test et ses données sont ignorées par Git.

## Phase 2 — Profil et CV de référence

- Import PDF textuel et DOCX, contrôle de type/taille et extraction.
- Écran de revue/correction des sections et faits ; stockage du fichier original en local.
- Modèle de profil structuré et gestion de versions.
- Tests sur documents représentatifs et erreurs de fichier.

**Terminé lorsque :** l'utilisateur peut corriger les données extraites et retrouver le CV après redémarrage.

## Phase 3 — Offres et recherche

- Saisie manuelle/coller d'annonce, normalisation, filtres et déduplication.
- Connecteur initial validé par les conditions de la source, avec provenance, dates et limites.
- Scoring de pertinence explicable selon les préférences configurées.

**Terminé lorsque :** les annonces importées et manuelles sont comparables, consultables et supprimables.

## Phase 4 — Adaptation et export du CV

- [x] Comparatif offre/profil : critères présents, absents et à vérifier, avec justificatifs.
- [x] Version CV distincte, liée à l'offre et au CV de référence ; modification et sauvegarde.
- [x] Export PDF téléchargeable de cette version avec le modèle ATS à une colonne et la base JSON fournie par l'utilisateur.
- [ ] Édition structurée, aperçu de mise en page, export DOCX et vérification de lisibilité ATS.
- Vérifier que les éléments clés restent du texte sélectionnable.

**Terminé lorsque :** un brouillon modifié peut être exporté et sa provenance est visible.

## Phase 5 — Entreprises et candidatures spontanées

- Recherche d'entreprises selon secteur et zone via une source acceptable ou une liste importée.
- Fiches entreprise avec URL et date de vérification ; dédoublonnage avec les offres.
- Brouillon de candidature spontanée et suivi de relance, toujours validé manuellement.

**Terminé lorsque :** on peut enregistrer une entreprise sans offre connue et suivre une démarche spontanée.

## Phase 6 — Finition et amélioration

- Export CSV du suivi, sauvegarde/restauration vérifiée et gestion complète des erreurs.
- Ergonomie et accessibilité de l'interface.
- Évaluer l'intérêt d'une aide à la reformulation par LLM local, opt-in, après validation des flux déterministes.
- Tests bout en bout, documentation Windows et procédure de mise à jour.

## État actuel du batch et de l'automatisation

- [x] Sélectionner plusieurs offres, y compris avec **Tout sélectionner**, dans **CV par offre → Batch de CV**.
- [x] Générer et enregistrer les brouillons sans lancer leur téléchargement.
- [x] Télécharger le PDF individuellement depuis le bouton de la carte de l'offre ; il n'y a pas de téléchargement ZIP groupé.
- [x] Lancer une recherche Adzuna à la demande et la laisser s'exécuter en arrière-plan pendant la navigation.
- [x] Veille planifiée des recherches (`services/job_watcher.py`) et pré-génération des dossiers (`services/dossier_generator.py`). Elle vit dans le processus Streamlit ; une gestion durable (service indépendant) reste optionnelle.
- [x] Connecteur France Travail (`services/job_sources/france_travail.py`) : recherche manuelle, import en lot et intégration à la veille. À valider avec de vrais identifiants.
- [x] La Bonne Boîte (`services/job_sources/bonne_boite.py`) : entreprises classées par potentiel d'embauche pour les candidatures spontanées, validé avec de vrais identifiants.
- [x] Score par technologies demandées (au lieu de la couverture des 5 meilleures compétences) et recherche par langages acceptés (niveau 5 ou plus) ; seuil par défaut de la veille à 50 %.
- [x] File d'envoi des candidatures spontanées : lettre, PDF, liens de recherche du contact, suivi et relance à J+7 pour les entreprises à prospecter.
- [ ] Obtenir l'abonnement à l'API « Offres d'emploi » de France Travail pour valider la recherche d'offres.
- [x] File d'envoi (`services/send_queue.py`) : offres classées avec dossier prêt, téléchargement des PDF, enregistrement de l'envoi manuel, relance à J+7 et écartement rapide.
- [ ] Automatiser la soumission des candidatures : non disponible et à ne pas confondre avec la génération de documents. Les API officielles Greenhouse/Lever de dépôt de candidature exigent une clé API de l'employeur et ne sont pas utilisables par un candidat ; cette piste est abandonnée.

L'automatisation actuellement disponible prépare des éléments, mais ne postule pas : la recherche
doit être lancée par l'utilisateur, les CV/lettres doivent être relus et les candidatures doivent
être soumises manuellement sur le site de l'employeur. Une automatisation future pourrait
préparer et organiser les dossiers et les liens, tout en laissant à l'utilisateur le contrôle
final. La soumission intégralement automatique nécessiterait une analyse spécifique des parcours
et conditions de chaque plateforme et n'est pas promise par la feuille de route.

## Améliorations demandées — édition du CV de base et noms de fichiers

1. [x] Donner aux PDF ciblés un nom de fichier lisible construit à partir du candidat, de
   l'entreprise et du type de poste, sans suffixe numérique artificiel ni mention de génération
   automatique. En cas d'offres de même entreprise et intitulé, conserver un nom descriptif
   stable ; le navigateur gère d'éventuels téléchargements portant le même nom.
2. [x] Ajouter dans **CV de référence** une prévisualisation du CV de base structuré et une
   édition indépendante de `data/cv_base.json`, afin de maintenir profil, expériences,
   formations et compétences à jour sans modifier les brouillons déjà personnalisés.
3. [x] Ajouter des tests de stabilité/format pour les noms, de validation/enregistrement du CV
   de base avec une nouvelle expérience et de rendu PDF. Exécution à confirmer localement.

## Point bloquant traité — aperçu du CV

- [x] Diagnostiquer et corriger le problème d'aperçu PDF dans **Vue d'ensemble → CV de référence**.
  - Intégration d'un onglet d'aperçu direct HTML (rendu fidèle immédiat sans dépendance de plugin).
  - Sécurisation de l'aperçu PDF intégré avec `<object>`/`<embed>` et lien de repli.
  - Bouton de téléchargement direct du PDF compilé par WeasyPrint.
- Les autres travaux restants et les questions à renseigner sont regroupés dans
  [docs/suivi-projet-et-questions.md](suivi-projet-et-questions.md).

## Historique : ordre conseillé pour la première session de développement

1. Répondre aux questions de cadrage de `specifications.md`.
2. Mettre en place le profil et l'import de CV avant d'ajouter des connecteurs.
3. Tester le flux manuel annonce → comparaison → brouillon → export.
4. N'ajouter la collecte automatisée qu'après validation de la source et de ses conditions.

## Plan de démarrage détaillé — historique du socle initial

Ce plan décrit les premières étapes du projet et n'est pas un état complet des fonctionnalités
actuelles. Depuis sa rédaction, le dépôt a notamment acquis une recherche Adzuna déclenchée
manuellement, l'import de liens d'offres, les lettres et le batch de CV. Les sections ci-dessus
indiquent l'état courant de l'automatisation et du batch.

### Prochaine itération : socle local

1. [x] Ajouter une configuration locale des chemins, avec `data/` par défaut et
   `ASSISTANT_CANDIDATURES_DATA_DIR` comme surcharge.
2. [x] Initialiser SQLite/SQLAlchemy au démarrage ; isoler les tests dans des bases temporaires.
3. [x] Définir le profil persistant avec ses compétences, préférences de contrat et de zone.
4. [x] Remplacer la page de squelette par une navigation avec accueil et édition du profil.
5. [x] Ajouter les tests de configuration, validation et persistance, puis documenter les
   commandes de vérification.
6. [x] Ajouter le modèle des versions de CV avec le texte relu et les métadonnées de provenance.
   Le modèle détaillé de faits extraits sera ajouté avec l'édition structurée du profil CV.

**Critère de sortie :** l'application démarre sur une installation propre, crée ses données
uniquement dans le dossier local prévu et passe les tests avec une base isolée.

### Parcours utilisateur suivant : importer et valider un CV

1. [x] Prendre en charge les PDF textuels et DOCX, vérifier le type et la taille (10 Mo maximum)
   avant extraction.
2. [x] Présenter le texte extrait et permettre sa correction avant enregistrement ; signaler
   clairement les PDF sans texte exploitable et les documents protégés.
3. [x] Enregistrer le document source sous un nom local généré et le texte vérifié en SQLite.
4. [x] Ajouter des tests pour DOCX valides, documents mal formés, formats refusés, limites de
   taille et persistance.
5. [ ] Vérifier le parcours import → correction → redémarrage avec l'interface locale.

**Critère de sortie :** l'utilisateur peut importer, corriger, enregistrer et retrouver un CV
sans transmettre le document à un service distant.

### Registre local des entreprises

1. [x] Persister les entreprises séparément des annonces, avec lieu, source de vérification,
   preuve d'activité/équipe de développement, notes et date de vérification.
2. [x] Permettre le filtrage géographique et le CRUD, avec une provenance obligatoire pour les
   e-mails publics.
3. [x] Ajouter le suivi des candidatures, y compris spontanées, avec statut et prochaine action.
4. [x] Exposer sur l'accueil les comptes réels en base ; aucun volume d'offres ou d'entreprises
   n'est simulé ou préchargé.
5. [x] Ajouter une recherche déclenchée par l'utilisateur dans l'API publique du registre et
   persister séparément les résultats à vérifier pour les départements 06 et 83.
6. [ ] Relier les offres aux fiches entreprises via une migration additive et réversible, sans
   perte des annonces existantes.
7. [ ] Constituer une première liste vérifiée d'employeurs à partir de ces candidats et de
   sources d'entreprise publiques, sans préremplir de contacts non sourcés.

### Offres manuelles et comparaison du profil

1. [x] Créer un modèle persistant d'offre avec titre, entreprise facultative, lieu, contrat, URL,
   source, date de collecte et texte d'annonce.
2. [x] Ajouter une interface de création, consultation, modification et suppression, sans
   connecteur externe.
3. [x] Ajouter des tests de validation, persistance et détection des doublons par URL.
4. [x] Construire une première comparaison déterministe entre les compétences du profil et le
   texte de l'annonce, avec extraits justificatifs et revue des termes non mentionnés.
5. [ ] Étendre ensuite le comparatif à la revue des critères d'expérience, localisation et
   contrat ; garder les résultats explicables et ne jamais traiter un mot-clé manquant comme une
   compétence absente du candidat.

### Décisions à recueillir avant les fonctions métier

- Profil déjà communiqué : développeur backend / full-stack à dominante backend, environ 5 à
  7 ans d'expérience, compétences détaillées et auto-évaluées dans `specifications.md` ; Nice,
  Cannes et le département du Var, puis uniquement les postes entièrement en télétravail hors
  zone ; CDI privilégié. À préciser : secteurs et CV de référence. Les intitulés backend PHP,
  Symfony et full-stack à dominante backend sont des pistes, pas des filtres obligatoires.
- Format et état du CV de référence (DOCX, PDF textuel ou PDF scanné) et format d'export souhaité.
- Emplacement local souhaité pour les données, si différent de `data/`.
- Sources d'offres réellement souhaitées ; aucune collecte automatisée avant vérification de
  l'existence d'une API ou d'un flux autorisé.
- Vérification initiale des sources et contrat technique commun consignés dans
  `docs/job-sources.md` et `src/services/job_sources/base.py`. Recherche Adzuna ponctuelle
  implémentée ; vérifier couverture, coût/quotas et conditions dans le compte avant usage.
  Connecteurs ciblés Greenhouse et Lever intégrés (`services/job_sources/greenhouse.py`,
  `services/job_sources/lever.py`) pour la collecte directe d'offres en lecture seule.
- Zone retenue pour le premier répertoire d'entreprises : Nice, Cannes et tout le Var. Définir
  une méthode de vérification d'équipe tech, distincte de la recherche candidate par activité
  déclarée via le registre public.
- Commencer avec des règles déterministes ; réévaluer l'intérêt d'un LLM local uniquement après
  validation du flux de bout en bout.
