# Spécifications fonctionnelles

## 1. Vision

Construire un outil personnel, exécuté sur l'ordinateur de l'utilisateur, qui transforme un CV de référence et des pistes d'emploi en candidatures mieux ciblées et suivies. Le produit aide à gagner du temps sans déformer le parcours professionnel ni envoyer de candidature à la place de l'utilisateur.

## 2. Utilisateur et hypothèses de départ

- Utilisateur unique, en recherche d'un poste de développeur.
- Le profil (poste visé, compétences auto-évaluées, zone de recherche, types de contrat) est saisi
  dans l'application et stocké localement ; aucune donnée personnelle n'est versionnée.
- Hors de la zone renseignée, l'utilisateur peut choisir de ne retenir que les postes entièrement
  en télétravail.
- Les zones interrogées par les sources d'offres, les zones prioritaires et les mots exclus du
  titre se règlent dans la page Veille (fichier local `data/watcher_config.json`).
- Les préférences sont des valeurs initiales modifiables ; le CV de référence reste à fournir par
  l'utilisateur.
- Le service fonctionne sur une seule machine et n'a pas besoin d'être publié ou accessible depuis Internet.
- Les documents peuvent contenir des données personnelles : ils restent locaux par défaut.
- Les premiers connecteurs de recherche seront choisis selon les sources réellement disponibles et leurs conditions d'utilisation.

Les niveaux de compétences sont des auto-évaluations de l'utilisateur. Ils servent à orienter la
recherche et ne doivent pas être présentés comme des évaluations objectives.

## 3. Objectifs

1. Regrouper offres, entreprises, contacts et candidatures dans un espace consultable.
2. Détecter les offres compatibles avec le profil et expliquer les critères de correspondance.
3. Comparer chaque offre au CV de référence, repérer les mots-clés manquants et préparer un CV adapté fidèle aux expériences.
4. Découvrir des entreprises pertinentes même sans annonce publiée afin de préparer une candidature spontanée.
5. Permettre à l'utilisateur de relire, modifier et valider tout contenu avant export ou prise de contact.

## 4. Hors périmètre initial

- Envoi automatique de candidatures, messages ou formulaires.
- Contournement de protections anti-robots, collecte derrière authentification ou scraping contraire aux conditions d'une source.
- Promesse de passer un ATS ou de garantir un entretien.
- Comptes multi-utilisateurs, synchronisation cloud, application mobile ou publication publique.
- Génération de compétences, diplômes, résultats ou expériences non présents dans le profil source.

## 5. Parcours utilisateur cible

1. **Configurer le profil** : importer un CV DOCX ou PDF, saisir/valider les informations structurées, les compétences, les expériences et les préférences de recherche.
2. **Trouver des pistes** : lancer une recherche par intitulé, compétences, secteur, lieu et type de contrat, ou importer manuellement une annonce.
3. **Qualifier une offre** : examiner le résumé, la source, la date et les critères qui rapprochent ou éloignent l'offre du profil ; écarter les doublons.
4. **Préparer une candidature** : comparer l'annonce au CV de référence, sélectionner les expériences pertinentes, proposer des reformulations et signaler les mots-clés à traiter. Plusieurs brouillons peuvent être préparés et enregistrés dans le batch CV.
5. **Relire et exporter** : modifier le document proposé, vérifier les faits et la mise en page, puis télécharger le PDF de l'offre choisie sur sa carte.
6. **Soumettre et suivre** : l'utilisateur ouvre l'annonce et soumet lui-même sa candidature ; il peut ensuite enregistrer son état, la date de relance, les échanges et la prochaine action dans l'outil.
7. **Prospecter** : rechercher des entreprises par secteur et zone, compléter leur fiche avec une source consultable et préparer une candidature spontanée sans prétendre qu'un poste est ouvert.

## 6. Exigences fonctionnelles

### Profil et CV

- Importer un CV PDF ou DOCX et conserver l'original comme référence.
- Extraire le texte pour préremplir les sections ; demander confirmation pour les données incertaines.
- Maintenir un profil structuré éditable : coordonnées, titre, expériences, réalisations, compétences, formations, langues et préférences.
- Maintenir séparément les données structurées du CV de base dans `CV de référence`, avec un éditeur JSON, une prévisualisation PDF et un enregistrement permettant d'ajouter ou de corriger des expériences sans modifier les CV ciblés déjà enregistrés.
- Corriger et vérifier le problème signalé dans l'aperçu PDF de **Vue d'ensemble → CV de référence** ; les symptômes doivent être précisés avant le diagnostic.
- Garder la provenance de chaque élément (document/section ou saisie utilisateur) pour éviter les ajouts non vérifiés.
- Versionner les CV adaptés et pouvoir revenir à l'original.

### Offres d'emploi

- Créer une offre manuellement ou l'importer depuis une source autorisée.
- Conserver URL/source, date de collecte, employeur, intitulé, lieu, type de contrat, texte intégral et état.
- Pour une source connectée, conserver le code pays, l'URL d'origine et la provenance des
  informations ; ne retenir un e-mail que s'il est explicitement publié dans les données
  accessibles et autorisées de l'annonce, sans jamais déduire une adresse.
- Pour les postes à l'étranger, relever les mentions explicites d'aide à la relocalisation avec
  leur extrait justificatif ; une mention reste à confirmer avec l'employeur.
- Normaliser les critères de recherche et détecter les doublons par URL et similarité de contenu.
- Filtrer et trier les offres ; expliquer un score indicatif à partir des critères configurés.
- Marquer une offre comme nouvelle, à examiner, intéressante, écartée ou liée à une candidature.

### Adaptation du CV et analyse ATS

- Comparer exigences et CV sur les mots-clés, compétences, expérience, séniorité et localisation.
- Afficher séparément les éléments présents, absents, ambigus et non applicables ; ne pas assimiler un mot-clé absent du texte à une compétence absente du candidat.
- Proposer des améliorations uniquement à partir de faits du CV validés par l'utilisateur.
- Préserver la lisibilité ATS : structure sobre, titres usuels, texte sélectionnable, ordre logique, sans information essentielle uniquement dans une image ou un tableau.
- Le PDF CV ciblé utilise une mise en page à une colonne, affiche le nom de l'entreprise sans la ville, et n'inclut pas de lien vers l'annonce.
- Le nom de téléchargement du PDF ciblé est descriptif et professionnel (candidat, entreprise et métier), sans suffixe numérique artificiel ; les collisions sont résolues sans écraser les documents.
- Afficher un avertissement : score et suggestions sont des aides heuristiques, pas une simulation fiable d'un ATS particulier.
- Produire un brouillon modifiable et exportable, avec revue explicite avant toute utilisation.

### Entreprises et candidatures spontanées

- Créer une fiche entreprise (nom, secteur, zone, site, page carrière, taille si connue, source, notes).
- Rechercher les entreprises avec des critères de secteur, lieu et adéquation au profil, indépendamment de l'existence d'une offre.
- Indiquer clairement « aucune offre trouvée dans les sources consultées » plutôt que d'affirmer qu'aucun recrutement n'existe.
- Conserver les sources et la date de vérification pour les coordonnées et informations d'entreprise.
- Préparer un brouillon de message/CV de candidature spontanée, à valider manuellement.
- Le batch peut créer et enregistrer plusieurs CV ciblés, mais chaque PDF est téléchargé à la demande depuis la carte de son annonce ; générer un batch ne déclenche pas de téléchargement.

### Suivi

- Tableau des candidatures avec entreprise, rôle, date, statut, version de CV et prochaine action.
- Statuts configurables au minimum : à préparer, prête à envoyer, envoyée, entretien, refusée, retirée.
- Notes et dates de relance ; aucune notification externe obligatoire en V1.
- Export des données de suivi en CSV et sauvegarde/restauration des données locales.
- Une recherche en arrière-plan lancée manuellement n'est pas un planificateur ; aucune candidature n'est soumise automatiquement à un site d'emploi.

## 7. Exigences non fonctionnelles

- **Local-first** : aucune transmission réseau de CV ou données personnelles sans action/consentement explicite.
- **Transparence** : afficher la source, date de collecte et limites des résultats.
- **Sécurité** : ne pas stocker les mots de passe des sites d'emploi ; exclure données personnelles, bases et exports des commits Git.
- **Robustesse** : valider les fichiers et tailles, gérer les formats non pris en charge avec une erreur compréhensible, sauvegarder avant migrations.
- **Portabilité** : démarrage documenté sur Windows, données sous un dossier local configurable.
- **Qualité** : tests unitaires pour le parsing, la déduplication, le scoring et les exports ; tests de bout en bout des parcours principaux.
- **Accessibilité d'usage** : interface claire en français, actions non ambiguës, aperçu avant export.

## 8. Critères d'acceptation du MVP

1. L'application démarre localement et les données persistent après redémarrage.
2. Un CV DOCX ou PDF textuel peut être importé ; son contenu extrait est vérifiable et éditable.
3. Une annonce peut être saisie/collée, enregistrée, retrouvée et reliée à une entreprise.
4. Le comparatif avec le profil distingue mots-clés trouvés, non trouvés et à vérifier, avec les éléments justificatifs visibles.
5. Un brouillon de CV ciblé peut être modifié, exporté, rouvert et comparé au CV de référence.
6. Une entreprise sans offre connue dans les résultats peut être enregistrée comme piste spontanée avec la date et les sources consultées.
7. Une candidature peut être suivie de la préparation à la clôture, avec une prochaine action.
8. Aucune candidature n'est soumise automatiquement à un site d'emploi ; les informations personnelles ne sont pas incluses dans le dépôt.

## 9. Questions produit historiques

- Quelles régions/villes, distance et modalités (télétravail/hybride/présentiel) ?
- Quels langages, spécialités, niveaux de séniorité, types de contrats et secteurs prioritaires ?
- Quelles plateformes/sources sont acceptables et disposent d'une API ou d'un flux autorisé ?
- Le CV source est-il en DOCX, PDF textuel, PDF scanné, ou plusieurs formats ?
- Souhait d'utiliser un modèle de langage local (par exemple via Ollama) pour reformuler, ou de commencer avec des règles déterministes uniquement ?
- Format préféré pour le CV exporté et style de mise en page existant à préserver ?

Les décisions prises depuis (CV ATS une colonne à partir du JSON structuré, batch avec
téléchargement individuel, lettre française avant traduction) sont reportées dans
[docs/roadmap.md](roadmap.md).
