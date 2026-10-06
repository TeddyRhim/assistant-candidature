# Assistant candidatures

Application locale pour organiser une recherche d'emploi de développeur, analyser des offres et préparer des candidatures adaptées à partir d'un CV de référence.

## Principes

- Les données et documents restent sur la machine ; la base est un fichier SQLite local.
- Le CV adapté doit rester fidèle au parcours réel : l'outil aide à reformuler et à prioriser, il ne doit ni inventer ni exagérer d'expérience.
- L'analyse ATS fournit des indications et une liste de vérifications, jamais une promesse de classement ou d'acceptation.
- Les annonces et coordonnées d'entreprises sont collectées via des sources autorisées ; aucune candidature n'est envoyée automatiquement.

## Démarrer

Prérequis : Python 3.11 ou supérieur et [`uv`](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run streamlit run src/app.py
```

L'interface s'ouvre dans le navigateur et reste accessible localement. Les choix de conception et le périmètre sont décrits dans [docs/specifications.md](docs/specifications.md) et [docs/architecture.md](docs/architecture.md). Les étapes de réalisation sont dans [docs/roadmap.md](docs/roadmap.md).
Le relevé complet de l'état actuel, des points restant à faire et des questions à compléter est
dans [docs/suivi-projet-et-questions.md](docs/suivi-projet-et-questions.md).

## État

Le socle local, l'édition du profil, l'import du CV et la gestion manuelle des offres sont en
place. Le profil est validé puis persisté dans SQLite. Au premier lancement, si
`data/profile_seed.json` existe, il est importé une seule fois ; sinon le profil peut être saisi
depuis la rubrique **Profil**.

Depuis **CV de référence**, sélectionnez un PDF textuel ou un DOCX (maximum 10 Mo), extrayez et
relisez le texte, puis enregistrez la version vérifiée. Pour les PDF, l'extraction récupère d'abord
le texte sémantique balisé quand il existe, sinon tient compte de la disposition ; elle normalise
les accents, ligatures, caractères invisibles et séquences de lettres espacées. Vérifie toujours
le texte extrait avant de le valider. Le fichier original et le texte corrigé restent dans le
répertoire local de données, sous `resumes/` et SQLite respectivement. Les PDF
scannés, les fichiers protégés par mot de passe et l'OCR ne sont pas pris en charge. Dans
**Offres**, tu peux saisir, consulter, modifier et supprimer des annonces ; coller un lien HTTP(S)
permet d'en lire les métadonnées d'offre disponibles, de vérifier/corriger les champs extraits,
puis d'enregistrer l'offre. Certains sites bloquent cette lecture ou ne publient qu'un extrait ;
vérifie toujours le texte avant usage. Les URL sont validées et les doublons sont refusés. Un comparatif déterministe indique quelles compétences du profil
sont citées dans l'annonce, avec les extraits correspondants, et lesquelles sont à vérifier.
Un mot-clé non mentionné ne signifie pas que la compétence est absente du profil. Aucun
connecteur d'offres tiers n'est activé automatiquement.

Un générateur de CV autonome est également disponible : modifiez `data/cv_base.json`, puis lancez
`uv run python render.py`. Le PDF est créé dans `output/cv.pdf`. Pour utiliser un autre fichier
JSON et choisir le chemin de sortie, lancez `uv run python render.py data/mon_cv.json
output/mon_cv.pdf`. Le rendu repose sur le template Jinja2 `templates/cv.html` et WeasyPrint.
Sous Windows, WeasyPrint nécessite aussi les bibliothèques natives GTK3/Pango. Le rendu cherche
automatiquement `libgobject-2.0-0.dll` dans le `PATH`, les dossiers MSYS2 usuels et
`C:\Program Files\GTK3-Runtime Win64\bin`. Si GTK est installé ailleurs, définis
`WEASYPRINT_DLL_DIRECTORIES` vers son dossier `bin` avant de lancer le générateur ; le script
enregistre ce répertoire pour le chargement des DLL par Python.
Le **CV par offre** est préparé et sauvegardé comme objet JSON modifiable, initialisé depuis
`data/cv_base.json` et complété avec le nom de l'entreprise seulement (sans ville ni URL d'annonce).
Le PDF utilise une mise en page ATS à une colonne, avec profil, expériences, formation et
compétences ; le CV importé de référence reste inchangé. Le PDF téléchargé et la pièce jointe
e-mail utilisent le même template Jinja2. `uv run pytest tests/test_cv_render.py`
génère aussi un PDF de contrôle avec les
données de base et du texte Lorem ipsum ajouté dans tous les champs, afin de vérifier le rendu
complet du template.

Dans **CV de référence**, l'éditeur JSON du CV structuré permet de modifier le profil, les contacts,
les expériences (ajouter un objet dans `experience`), la formation et les compétences ; l'aperçu
PDF suit les modifications avant enregistrement. Le bouton d'enregistrement met à jour
`data/cv_base.json` pour les prochaines générations seulement : les brouillons déjà sauvegardés
restent indépendants. Les PDF ciblés et pièces jointes ont un nom descriptif fondé sur le nom du
candidat, le poste et l'entreprise, sans compteur artificiel.

**Découvrir des entreprises** propose deux sources. **La Bonne Boîte** (France Travail) classe les
entreprises des départements 06, 75 et 83 par potentiel d'embauche pour un métier (M1805
développeur par défaut), y compris sans offre publiée : c'est la meilleure piste pour les
candidatures spontanées. Elle utilise les mêmes identifiants que France Travail, avec l'API
« La Bonne Boîte » ajoutée à ton application. Le score est une estimation relative, et l'API ne
donne jamais d'e-mail (seulement s'il en existe un) : le contact est à trouver sur le site de
l'entreprise. Le **registre public** liste les sociétés par activité déclarée.

Dans **Découvrir des entreprises**, ajoute les pistes vérifiées à **Entreprises à prospecter** ;
elles sont ensuite proposées dans **Candidature spontanée**.

Dans **CV par offre**, tu peux aussi préparer une lettre en français associée à l'offre
sélectionnée, la modifier, l'enregistrer localement et télécharger son PDF. Le texte s'appuie
sur les informations du CV de base et développe les missions déjà décrites dans l'expérience ;
relis-le avant utilisation et complète le CV avant de générer la lettre si nécessaire. La page
**Candidature spontanée** prépare la même lettre sans offre, pour l'entreprise indiquée. La
traduction n'est pas encore prise en charge. L'envoi d'un e-mail reste une action séparée,
individuelle et confirmée dans **Envoyer un e-mail**.

La rubrique **Recherche en ligne** propose deux modes de collecte :
- **Adzuna (recherche par mots-clés)** : lance manuellement une recherche après configuration locale des identifiants (`ADZUNA_APP_ID` et `ADZUNA_APP_KEY`). Elle lance une requête distincte pour chacune des huit compétences prioritaires du profil, plus une requête pour le poste visé. En local, elle couvre la zone PACA élargie (Nice, Cannes, Var, Marseille) et Paris (classé après le local) ; à l'international, les pays sont choisis individuellement avec filtrage de langue. Les résultats sont dédoublonnés et classés selon la correspondance avec le profil.
- **France Travail (API officielle)** : renvoie le texte complet des annonces, ce qui améliore le score et les lettres. Crée un compte sur [francetravail.io](https://francetravail.io/inscription), déclare une application abonnée à l'API « Offres d'emploi », puis ajoute `FRANCE_TRAVAIL_CLIENT_ID` et `FRANCE_TRAVAIL_CLIENT_SECRET` dans `.streamlit/secrets.toml` (ignoré par Git). La recherche couvre les départements choisis (06, 83, 13 et 75 par défaut), en CDI uniquement par défaut, avec import unitaire ou en lot des offres au-dessus du seuil. La veille automatique l'interroge aussi.
- **Greenhouse & Lever (collecte ciblée par employeur)** : interroge directement les API publiques officielles en lecture seule des tableaux de recrutement d'entreprises cibles, sans clé API ni scraping. L'identifiant du tableau peut être saisi sous forme de slug ou d'URL complète (ex. `ateliertech` ou `https://boards.greenhouse.io/ateliertech`, `exampleco` ou `https://jobs.lever.co/exampleco` avec support des instances européennes `api.eu.lever.co`). Le connecteur nettoie le texte, détecte les contrats, les mentions de relocalisation et les e-mails de contact sourcés. Les offres peuvent être filtrées par mot-clé et importées individuellement ou en lot avec liaison automatique aux fiches entreprises locales.

La page **File d'envoi** (rubrique Candidatures) regroupe les offres à traiter, classées par score
global, avec l'état de leur CV et de leur lettre. Pour chaque offre : ouvre l'annonce, prépare puis
télécharge les PDF (le CV ciblé et la lettre enregistrés), relis la lettre, postule toi-même sur le
site de l'employeur, puis clique sur **J'ai envoyé ma candidature**. Cela crée la candidature
« Envoyée » avec une relance à 7 jours et retire l'offre de la file. Si l'entreprise n'a pas de
fiche, une fiche minimale est créée à partir de l'annonce (sans contact). Les offres peuvent être
marquées « Intéressante » ou écartées. Les relances échues apparaissent en tête de page. Rien
n'est envoyé automatiquement.

Les extraits Adzuna sont coupés à 500 caractères : la carte l'indique, et le volet
**Compléter l'annonce** permet de coller le texte complet copié depuis l'annonce d'origine. Le
score est recalculé avec les technologies réellement demandées et, si la case est cochée, le CV
ciblé et la lettre sont régénérés (ce qui écrase leurs modifications manuelles). Le texte collé doit
être plus complet que l'extrait enregistré.

L'onglet **Candidatures spontanées** de la même page reprend ce parcours pour les entreprises de
**Entreprises à prospecter** (par exemple issues de La Bonne Boîte) qui n'ont encore aucune
candidature : prépare la lettre (modifiable), télécharge le CV de base et la lettre en PDF, trouve
le contact via les liens de recherche proposés (site de l'entreprise, LinkedIn), envoie toi-même,
puis clique sur **J'ai envoyé ma candidature** : une candidature spontanée « Envoyée » est créée
avec une relance à 7 jours, et l'entreprise quitte la file. Aucun contact n'est deviné ni collecté.

Les annonces importées restent modifiables, supprimables et utilisables pour générer un CV et une lettre adaptés. Aucune offre n'est enregistrée sans action explicite de l'utilisateur.

Dans **CV par offre → Batch de CV**, les offres enregistrées sont affichées en cartes sur quatre
colonnes de hauteur uniforme. **Tout sélectionner** coche ou décoche tout le lot. La génération
enregistre les brouillons sans lancer de téléchargement. Chaque carte propose ensuite son propre
bouton **Télécharger le CV PDF** ; aucun téléchargement en lot n'est proposé. La carte affiche
**PDF non généré** tant qu'aucun brouillon n'a été créé. Le PDF ne contient ni le lieu ni le lien
de l'annonce.

La recherche Adzuna est lancée manuellement et tourne en arrière-plan pendant que l'interface
reste navigable. Une **veille automatique planifiée** (Adzuna, Greenhouse, Lever) peut aussi être
démarrée depuis la page de recherche : elle tourne dans le processus Streamlit (elle s'arrête avec
lui), relit sa configuration à chaque cycle, importe les offres au-dessus du seuil de pertinence et
pré-génère CV et lettre. Aucune candidature n'est envoyée automatiquement.
Le batch prépare et enregistre des CV, mais ne postule pas : il faut relire le CV et la lettre,
ouvrir l'annonce d'origine, puis soumettre soi-même le dossier sur le site de l'employeur. La
page **Envoyer un e-mail** permet un envoi individuel après confirmation explicite ; elle ne
soumet pas de candidature à un formulaire d'emploi.
Ne colle jamais de clé API dans le code, Git ou la conversation ; si une clé a été partagée,
révoque-la et génère-en une nouvelle. La méthode conseillée est de créer
`.streamlit/secrets.toml` à la racine du projet (ce fichier est ignoré par Git) avec :

```toml
ADZUNA_APP_ID = "ton-nouvel-identifiant"
ADZUNA_APP_KEY = "ta-nouvelle-cle"
```

Redémarre ensuite Streamlit. L'application accepte aussi les variables dans le terminal
PowerShell courant :

```powershell
$env:ADZUNA_APP_ID = "ton-nouvel-identifiant"
$env:ADZUNA_APP_KEY = "ta-nouvelle-cle"
uv run streamlit run src/app.py
```

La rubrique **Entreprises à prospecter** permet d'enregistrer et filtrer par lieu des entreprises
vérifiées, avec le lien source justifiant leur activité/équipe de développement. Les coordonnées
publiques exigent leur lien source ; aucune entreprise n'est préchargée et une fiche ne signifie
pas qu'un poste est ouvert. Le périmètre initial est Nice, Cannes et tout le Var. La recherche
**Découvrir des entreprises** interroge le registre public pour proposer des pistes actives dans
les départements 06, 75 (Paris) et 83. Sans mot-clé, elle parcourt les cinq codes NAF/APE informatiques
configurés, avec au plus trois appels simultanés, puis fusionne les doublons par SIREN. Un
mot-clé ou un code particulier permet de restreindre la recherche. Les résultats restent des
pistes à vérifier : un code d'activité ne prouve ni équipe de développement ni recrutement. Les
pistes locales enregistrées peuvent être ajoutées à **Entreprises à prospecter** en un clic.
L'entrée créée cite l'activité du registre et garde une note explicite pour vérifier l'équipe
tech ; elle ne crée jamais d'offre d'emploi. Aucun contact n'est deviné. La
rubrique **Candidatures** permet de suivre une démarche liée à une offre ou spontanée, avec statut
et prochaine action. Le tableau d'accueil affiche les nombres
réellement présents dans la base. Aucune offre ni entreprise n'est préchargée automatiquement ;
les listes restent locales.

Les appels HTTPS à l'API utilisent le magasin public de certificats certifi mis à jour. Si un
proxy réseau ou antivirus intercepte HTTPS avec son propre certificat, configure
`ASSISTANT_CANDIDATURES_CA_BUNDLE` vers un fichier PEM contenant les CA approuvées pour ce
réseau. Ne désactive pas la vérification TLS. Une erreur de certificat expiré persistante doit
être vérifiée côté date/heure Windows ou proxy d'entreprise ; elle ne se corrige pas en
contournant HTTPS.

La rubrique **CV par offre** crée une copie JSON modifiable à partir de `data/cv_base.json` et
des données de l'offre. Elle affiche le nom de l'entreprise et réordonne les groupes de
compétences selon les compétences repérées dans l'annonce. Le CV importé sert de référence
associée au suivi, mais son texte n'est pas automatiquement converti dans le JSON. Le PDF utilise
le template ATS à une colonne ; il n'affiche ni la ville ni le lien de l'annonce. Relis le contenu
avant utilisation. Le CV de base reste inchangé, et ses modifications ultérieures ne changent pas
les brouillons déjà enregistrés.
Pour une offre à Paris ou à l'étranger, une question modifiable sur les
modalités de présence, le télétravail et un éventuel accompagnement du déplacement/installation
est proposée à copier dans le mail ou la lettre. Il n'est pas nécessaire de renvoyer le CV de
référence s'il apparaît déjà dans **CV de référence**.

La page **Envoyer un e-mail** envoie un seul message à la fois via l'API transactionnelle
Mailjet ; elle n'envoie jamais automatiquement et ne propose que les contacts publics sourcés
des entreprises vérifiées. Crée un compte Mailjet, valide l'adresse expéditrice dans leur
interface, puis configure localement les clés API et l'expéditeur dans
`.streamlit/secrets.toml` avec les noms affichés par la page. Ne mets jamais ces valeurs dans
Git ni dans la conversation. La page utilise le [Send API v3.1 de Mailjet](https://dev.mailjet.com/docs/email-api/send-api-v31/send-basic-email).
L'envoi est déclenché uniquement après relecture et confirmation ;
Mailjet accepte le message pour traitement, ce qui ne garantit pas sa livraison en boîte de
réception. Un PDF CV ciblé enregistré peut être joint. Les corps ne sont pas persistés en base ;
ils peuvent rester dans la session Streamlit active.

La correspondance avec le profil est affichée directement dans chaque offre enregistrée :
**ta maîtrise moyenne des technologies que l'annonce demande** (langages, frameworks, bases de
données, outils), pondérée par leur importance dans l'annonce (requise, mentionnée ou bonus,
double poids si la technologie est dans le titre), proximité de l'intitulé cible, contrat et
zone/télétravail. Une technologie absente du profil compte pour 0 ; un framework voisin d'un
langage que tu maîtrises (Laravel pour PHP, Django pour Python) compte pour 60 % du niveau de ce
langage. Le détail « Technologies demandées et ton niveau » est affiché sous le score. Les
langages de ton profil au niveau 5 ou plus (aujourd'hui PHP, Python, JavaScript) sont cherchés en
premier par les recherches Adzuna et France Travail : changer leur niveau dans **Profil** les
ajoute ou les retire de la recherche. Les critères
non renseignés sont exclus du score indicatif et les compétences absentes du texte sont marquées
« à vérifier », pas comme manquantes chez le candidat. Les synonymes courants sont reconnus et
chaque mention conserve un extrait de preuve ; vérifie les résultats, car la détection reste
déterministe et dépend du texte disponible. Dans **Entreprises à prospecter**, l'activité
déclarée et les compétences explicitement présentes dans les notes sont affichées sans score global
artificiel : le registre ne décrit pas la stack ni les équipes des entreprises.

La génération du CV ciblé conserve tout le texte vérifié du CV source et ajoute le rôle visé ainsi
que les compétences du profil repérées dans l'annonce. Elle ne sélectionne ni ne supprime des
expériences du CV source. Le PDF reprend une présentation compacte à deux colonnes inspirée du
CV de référence et vise une page ; le contenu source n'est pas coupé.

Le menu latéral est regroupé par usage : vue d'ensemble, recherche et candidatures.

Pour placer les données ailleurs, définir `ASSISTANT_CANDIDATURES_DATA_DIR` avant le démarrage.
Les tests utilisent des bases et documents temporaires et ne modifient pas les données
personnelles locales :

```powershell
uv run pytest
uv run ruff check .
```
