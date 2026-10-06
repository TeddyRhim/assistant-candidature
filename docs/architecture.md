# Architecture initiale

## Décisions

- **Python 3.11+** : écosystème adapté au traitement documentaire, à l'extraction de texte et à l'analyse de données.
- **Streamlit** : interface web locale rapide à construire pour un outil mono-utilisateur ; pas de serveur public ni de frontend JavaScript à maintenir.
- **SQLite** via **SQLAlchemy 2** : stockage embarqué, sans service externe, suffisant pour un usage individuel.
- **Pydantic 2** : validation des données de profil, d'offres et de paramètres.
- **python-docx**, **pypdf** et **ReportLab** : lecture des CV DOCX/PDF textuels et création locale de PDF ; l'OCR des PDF scannés est hors MVP.
- **uv** : environnement et dépendances reproductibles.
- **pytest** et **Ruff** : tests et vérifications rapides.

Cette architecture privilégie une installation légère et une faible maintenance plutôt qu'une architecture de service. Si les connecteurs ou les traitements longs imposent un backend séparé, l'architecture pourra évoluer sans déplacer les règles métier.

## Modules prévus

```text
src/
  app.py                 # navigation et pages Streamlit
  config.py              # chemins et configuration locale
  db.py                  # connexion SQLite et initialisation
  models/                # profil, CV, offres, entreprises, candidatures
  repositories/          # persistance et requêtes
  services/
    cv_import.py         # validation et extraction DOCX/PDF
    matching.py          # comparaison explicable profil/offre
    cv_drafting.py       # préparation du brouillon à partir de faits validés
    job_sources/         # connecteurs opt-in vers des sources autorisées
    company_sources/     # recherche de sociétés avec provenance
    export.py            # génération DOCX/PDF et CSV
  pages/                 # écrans métier
tests/
docs/
data/                    # créé à l'exécution, ignoré par Git
```

Le socle implémenté comprend actuellement la configuration du répertoire local (`config.py`),
la base SQLite (`db.py`), le modèle validé du profil et les pages Streamlit de profil et
d'import du CV. Le profil est un enregistrement local unique ; les listes de préférences et
compétences sont stockées en JSON SQLite. Au premier démarrage, un fichier `data/profile_seed.json`
facultatif est importé uniquement si aucun profil n'existe déjà. Les tests de persistance utilisent
leur propre base temporaire.

Le service `services/resume_import.py` contrôle l'extension, la signature du fichier et la taille
(10 Mo maximum), puis extrait les paragraphes/tableaux DOCX ou le texte des pages PDF en mémoire.
Pour un PDF balisé, le texte sémantique des éléments est préféré afin de préserver l'ordre de
lecture ; sinon, les pages sont extraites en mode disposition. Une normalisation Unicode NFC
retire les caractères invisibles, normalise les ligatures typographiques et réassemble les
séquences de lettres espacées. Le texte reste modifiable et doit être relu avant enregistrement
afin de confirmer l'exactitude des informations.
Le contenu DOCX décompressé est également limité à 50 Mo pour prévenir les archives compressées
anormalement volumineuses.
L'utilisateur peut relire et modifier le texte avant validation. Chaque version validée conserve
une copie du fichier sous `data/resumes/` avec un nom généré, et son texte vérifié ainsi que le nom
d'origine et la date sont conservés en SQLite. Les CV scannés et protégés ne sont pas acceptés ;
aucun OCR ni envoi réseau n'est effectué.

Les versions CV ciblées sont enregistrées dans `tailored_resumes`, une par offre, avec une
référence au CV source, le JSON modifiable et les dates de création/mise à jour. La génération
part des données structurées dans `data/cv_base.json`, et utilise l'offre pour afficher le nom de
l'entreprise seulement et prioriser les compétences du profil. La ville et l'URL de l'annonce ne
sont pas intégrées au PDF. Le template suit une mise en page ATS à une colonne avec profil,
expériences, formation et compétences. L'utilisateur peut modifier cette version distincte avant
usage et télécharger le PDF ; le CV importé de référence reste inchangé.

La page **CV de référence** contient aussi l'éditeur des données de `data/cv_base.json`. Il valide
le JSON avant sauvegarde atomique et présente un aperçu PDF du contenu édité. Ces changements
s'appliquent aux prochaines générations uniquement : les brouillons déjà enregistrés sont des
copies indépendantes. Les téléchargements et pièces jointes utilisent un nom descriptif formé à
partir du nom du candidat, du poste et de l'entreprise ; si plusieurs offres ont le même nom
d'entreprise et de poste, le nom proposé reste identique et le navigateur gère les téléchargements
en doublon.

Le batch de CV réutilise le même service : l'utilisateur sélectionne plusieurs offres, puis
génère et enregistre un brouillon JSON pour chacune. Cette action ne déclenche aucun
téléchargement. Chaque carte d'offre comporte son bouton de téléchargement PDF individuel ; le
téléchargement n'a lieu qu'après activation de ce bouton. La recherche asynchrone signifie ici
qu'un travail Adzuna manuel est exécuté en arrière-plan dans le processus Streamlit, pas qu'une
recherche planifiée ou une candidature est automatisée.

Les offres manuelles sont validées par `JobOfferData` et persistées dans `job_offers`. Le service
`services/job_offers.py` fournit les opérations de création, lecture, modification et suppression,
et refuse les URL HTTP(S) dupliquées. La source par défaut est « Saisie manuelle » ; aucun accès à
un site d'emploi ou chargement réseau d'annonce n'a lieu.

Les entreprises sont persistées séparément dans `companies`, via `CompanyData` et
`services/companies.py`. Une fiche doit inclure une source consultable et des éléments vérifiés
justifiant une activité ou équipe de développement. Un contact e-mail est facultatif, mais s'il
est saisi son URL de provenance est obligatoire. Le CRUD et le filtre de lieu sont locaux ;
aucune entreprise n'est présélectionnée ni présentée comme recrutant actuellement.

Les candidatures sont stockées séparément dans `applications`, avec une entreprise obligatoire,
une offre facultative (absence d'offre = démarche spontanée), un statut, les dates d'envoi et
d'action suivante, et des notes. Les références SQLite sont activées ; une entreprise ne peut
pas être supprimée tant qu'une candidature y est liée. Supprimer une offre conserve son suivi
de candidature en la convertissant en démarche spontanée. Le tableau d'accueil expose les
comptes locaux réels d'offres, entreprises et candidatures ; aucun enregistrement fictif n'est
injecté.

Les résultats de l'API publique de recherche d'entreprises sont gardés dans une table séparée
`company_candidates`, à clé SIREN. Ils ne sont pas assimilés à des entreprises vérifiées et
n'exigent pas d'adresse e-mail. La recherche cible les départements 06, 75 et 83. Sans mot-clé, elle
effectue une recherche par chacun des codes NAF/APE logiciels configurés, limite la concurrence
à trois requêtes et dédoublonne par SIREN ; la recherche par mot-clé/code reste disponible.
L'utilisateur décide quels résultats conserver puis doit vérifier leur activité réelle et la
présence d'une équipe tech avant candidature. Une piste locale peut être promue vers `companies`
en un clic ; sa fiche reprend l'activité déclarée comme provenance et indique que l'équipe tech
reste à vérifier. Cette action ne crée jamais d'offre d'emploi. Les pistes enregistrées peuvent
être relues et retirées de l'application.
Les requêtes utilisent le magasin racine de `certifi` plutôt que des autorités implicites ; les
réseaux avec interception TLS peuvent ajouter une CA approuvée via
`ASSISTANT_CANDIDATURES_CA_BUNDLE`. La vérification du nom d'hôte et de la chaîne reste activée.

L'envoi de candidature utilise l'API Mailjet v3.1 via HTTPS et la bibliothèque standard Python.
Les clés API et l'expéditeur validé sont lus des secrets Streamlit ou de l'environnement ; ils ne
sont jamais persistés en base. L'interface exige une confirmation explicite par message,
n'envoie qu'à l'adresse publique sourcée d'une entreprise vérifiée et peut joindre le PDF CV
ciblé enregistré. Le corps n'est pas persisté en base mais reste dans la session Streamlit active ;
la réussite affichée signifie que Mailjet a accepté le message pour traitement.

Le service `services/matching.py` compare chaque compétence saisie du profil au texte de l'annonce
par correspondance de termes entiers et par quelques synonymes connus, sans modèle externe. Il
conserve un extrait de preuve et détecte si une compétence est requise, simplement mentionnée ou
présentée comme un bonus ; ces priorités pondèrent la contribution des compétences au score. Les
autres compétences sont classées comme « non mentionnées — à vérifier », sans déduire qu'elles
sont absentes du candidat. Le calcul de correspondance classe les offres par un score
déterministe et explicable : compétences (60 %), intitulé cible (20 %), contrat (10 %) et
lieu/télétravail (10 %) ; seuls les critères renseignés entrent dans le calcul. Les entreprises
sont analysées séparément à partir des activités déclarées et des textes sourcés, sans leur
attribuer un score de stack technique ou inventer une équipe.

Le score de correspondance est affiché dans chaque offre enregistrée, tandis que l'activité
déclarée et les compétences documentées sont visibles dans les fiches d'entreprises. Le générateur
de CV cible reprend la totalité du texte vérifié du CV source dans le brouillon éditable et ajoute
les informations de ciblage à partir du profil et de l'offre. Il ne supprime ni ne reformule les
éléments du CV source. L'export PDF compact reprend une mise en page à deux colonnes inspirée du
document de référence.

Le contrat commun des connecteurs est décrit dans `services/job_sources/base.py` : requêtes
structurées, annonces normalisées, URL originale, contact public avec provenance et indice de
relocalisation justifié par un extrait. Les connecteurs intégrés comprennent Adzuna (recherche
par mots-clés et pays avec identifiants locaux) ainsi que Greenhouse et Lever (collecte ciblée
en lecture seule sur les tableaux d'employeurs sans clé API). Aucun scraper de portail d'emploi
n'est fourni. Voir `docs/job-sources.md` pour les sources examinées et leurs restrictions connues.

## Flux de données

1. Un document est validé par type et taille, extrait localement, puis présenté à l'utilisateur.
2. Le profil structuré validé est enregistré dans SQLite avec la référence au CV source.
3. Une offre ou entreprise est importée avec URL/source et horodatage ; une saisie manuelle reste possible.
4. Un service de rapprochement déterministe calcule les critères et expose leur justification.
5. Toute reformulation est un brouillon, liée à l'offre et au CV source ; l'utilisateur valide avant export.
6. L'export PDF est préparé localement et n'est téléchargé qu'après action explicite de l'utilisateur.

## Confidentialité et réseau

- L'application doit être utilisable sans réseau après installation.
- Les collectes d'annonces sont des connecteurs distincts, explicitement déclenchés et limités aux interfaces autorisées (API, flux, import fourni par l'utilisateur) ; la veille planifiée (`services/job_watcher.py`) est un déclenchement périodique opt-in, démarré explicitement par l'utilisateur, qui s'exécute dans le processus Streamlit et s'arrête avec lui.
- L'envoi par e-mail est individuel et nécessite une confirmation explicite ; aucune candidature n'est soumise automatiquement à un site d'emploi.
- Un éventuel LLM local sera optionnel et désactivé par défaut ; aucun service distant ne reçoit de document sans consentement explicite.
- Seules les données de recherche nécessaires sont conservées ; l'utilisateur peut exporter ou supprimer ses données.
- Ne pas enregistrer de credentials de plateformes dans SQLite, `.env` commité ou le dépôt.

## Modèle de données conceptuel

- `Profile` 1—N `ResumeVersion`
- `ResumeVersion` 1—N `TailoredResume` (chaque brouillon garde sa source)
- `JobOffer` 1—0..1 `TailoredResume`
- `ResumeVersion` 1—N `ResumeFact` (faits vérifiés, section et provenance)
- `JobOffer` N—1 `Company` (lien facultatif si employeur non identifié)
- `JobOffer` 1—N `MatchAnalysis`
- `JobOffer` 1—N `Application`
- `Application` N—1 `ResumeVersion` (version effectivement préparée)
- `Company` et chaque annonce gardent URL/source et date de dernière vérification.

## Exploitation locale

- Le dossier `data/` contient la base, hors contrôle de version.
- Prévoir sauvegarde/restauration explicite avant les migrations de schéma.
- Les connecteurs d'offres ont des délais, limites et erreurs visibles dans l'interface ; ils ne doivent jamais bloquer l'accès aux fonctions manuelles.
- Le démarrage V1 se fait par `uv run streamlit run src/app.py`.
