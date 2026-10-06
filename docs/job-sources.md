# Sources d'offres et stratégie de collecte

## Principes

- Ne pas lancer de scraping par défaut. Une page accessible publiquement ou autorisée par
  `robots.txt` ne suffit pas à établir qu'une collecte automatisée et la conservation des
  données sont permises.
- Préférer une API officielle ou un flux documenté. Vérifier les conditions actuelles, quotas,
  attribution, conservation et réutilisation avant d'activer chaque connecteur.
- Ne collecter que des offres publiées et conserver l'URL originale et la source. L'utilisateur
  ouvre ensuite l'annonce sur le site d'origine.
- N'enregistrer une adresse e-mail que si elle est explicitement publiée comme contact de
  l'annonce ou de l'agence. Ne pas deviner d'adresses, contourner une connexion ni parcourir
  d'autres pages pour chercher des coordonnées privées.
- Traiter la relocalisation comme un indice de texte à vérifier avec l'employeur ; aucune des
  interfaces examinées ne fournit un champ normalisé et fiable de prise en charge du déplacement.

## Sources examinées

Vérification documentaire initiale le 5 octobre 2026 ; ce relevé n'est pas une autorisation
juridique et doit être revu avant activation.

| Source | Couverture utile | Accès documenté | Limites et décision |
| --- | --- | --- | --- |
| [Adzuna API](https://developer.adzuna.com/overview) | Recherche agrégée par pays, lieu, mots-clés et type de contrat ; utilisée pour une recherche en France et certains pays européens. | API de recherche avec identifiants `app_id` et `app_key`. | La disponibilité des pays doit être confirmée pour le compte. Les résultats exposent un extrait et une URL de redirection, pas un e-mail fiable ni nécessairement le texte intégral. Connecteur intégré, recherche déclenchée manuellement. |
| [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) | Annonces publiées d'employeurs sélectionnés qui utilisent Greenhouse, y compris dans plusieurs pays. | API officielle par tableau employeur ; lecture des offres publiées sans authentification. | Connecteur intégré (`services/job_sources/greenhouse.py`). Collecte ciblée par tableau d'employeur via identifiant ou URL. Détection des contrats, mentions de relocalisation et contact e-mail public. Import sélectif ou en lot. |
| [Lever Postings API](https://github.com/Lever/postings-api) | Annonces publiées d'employeurs sélectionnés ; instances API globales et européennes, lieu et modalité de travail exposés. | API officielle de publications, en lecture seule. | Connecteur intégré (`services/job_sources/lever.py`). Collecte ciblée par site d'employeur via identifiant ou URL (avec support de l'endpoint européen). Détection des contrats, mentions de relocalisation et contact e-mail public. Import sélectif ou en lot. |
| [France Travail - offres d'emploi](https://francetravail.io/data/api/offres-emploi) | Source à examiner pour Nice, Cannes, le Var et le marché français. | L'accès documenté et ses conditions n'ont pas pu être confirmés lors de la vérification initiale. | Ne pas implémenter avant de vérifier les modalités d'authentification, quotas et règles d'usage courantes. |
| [EURES](https://eures.europa.eu/index_en) | Portail européen utile pour explorer les postes transfrontaliers et à l'étranger. | Aucune API ou permission de collecte automatisée n'a été confirmée. | À utiliser manuellement tant qu'un accès officiel n'est pas documenté ; ne pas scraper son portail. |

## Contact d'agence et relocalisation

Les API Greenhouse et Lever renvoient parfois le texte complet, mais ne définissent pas de champ
standard pour l'adresse d'un recruteur. Adzuna documente principalement des extraits et une URL de
redirection. La collecte d'e-mails sera donc opportuniste : uniquement une adresse explicitement
présente dans les données autorisées de l'annonce, accompagnée de sa provenance. L'application
devra sinon proposer le lien original pour vérification manuelle.

Le modèle source distingue `not_mentioned` et `mentioned` pour la relocalisation et garde l'extrait
de preuve. Une mention n'est pas une prise en charge confirmée : il faut vérifier le pays de
destination, les frais couverts, les critères d'éligibilité et les éventuelles conditions de visa
ou de droit au travail auprès de l'employeur.

## Contrat technique

`src/services/job_sources/base.py` définit le format commun des requêtes et annonces, ainsi qu'un
protocole de connecteur. Un premier connecteur Adzuna (`services/job_sources/adzuna.py`) permet
maintenant une recherche en lecture seule après configuration locale des variables
`ADZUNA_APP_ID` et `ADZUNA_APP_KEY` ou des secrets Streamlit dans
`.streamlit/secrets.toml`. L'appel ne part que lorsque l'utilisateur soumet le formulaire
**Recherche en ligne** ; les annonces ne sont sauvegardées que sur action explicite. Les résultats
importés conservent le lien d'origine, la source et l'extrait retourné. Le connecteur n'infère ni
contact e-mail, ni relocalisation, ni télétravail à partir d'un champ absent.
L'interface reprend le paramètre Adzuna standard `what`. En France, la recherche se lance
manuellement et forme des requêtes par chacune des huit compétences prioritaires du profil, plus
une requête pour le poste visé, sur les lieux configurés dans l'application. Les requêtes sont
exécutées en parallèle avec une limite de quatre appels actifs. Toutes les compétences du profil
servent ensuite à classer les annonces. Pour les autres marchés, les pays choisis individuellement
sont recherchés avec les termes disponibles dans le profil. Les annonces sont dédoublonnées et
classées par couverture pondérée de toutes les compétences explicites dans le titre ou l'extrait
fourni par Adzuna ; les compétences de niveau plus élevé pèsent davantage.
Ce pourcentage décrit uniquement les compétences repérées dans l'extrait (pas le niveau réel
requis ni une adéquation globale au poste). Le mode Europe/Canada permet de sélectionner
l'Autriche, la Belgique, le Canada, la Suisse, l'Allemagne, l'Espagne, la France, le Royaume-Uni,
l'Irlande, l'Italie, les Pays-Bas et la Pologne, avec une requête par pays sélectionné ; l'accès à
chaque marché dépend du compte Adzuna et doit être confirmé. Adzuna n'expose pas une requête
continentale unique.
Dans le mode Europe/Canada, `langdetect` écarte les annonces dont le texte (titre et extrait)
n'est pas détecté en français ou en anglais, y compris lorsque le texte est trop court ou
indéterminé. Le compteur des annonces écartées est affiché dans l'interface. La France n'applique
pas ce filtre de langue.

La rubrique **Découvrir des entreprises** utilise également l'API publique
[Recherche d'entreprises](https://recherche-entreprises.api.gouv.fr/docs) ; l'endpoint public
retourne des résultats sans identifiant lors de la vérification. Les recherches sont déclenchées
explicitement et limitées aux départements 06 et 83, avec un filtre optionnel sur les activités
informatiques (NAF/APE). Les résultats sont conservés séparément comme pistes à vérifier, avec
SIREN, zone, activité déclarée et liens du registre. Cette activité ne prouve pas qu'une équipe
tech interne existe ; aucune adresse e-mail n'est fournie ni devinée. L'API permet de repérer des
candidats, pas d'affirmer qu'une entreprise recrute. La recherche préremplie « informatique » est
volontairement plus large que « programmation informatique », qui peut ne retourner aucun résultat.

Le périmètre européen reste explicite : l'interface permet de choisir les pays disponibles
individuellement, sans lancer une recherche illimitée dans toute l'Europe. Le lieu, le mode de
travail, la mobilité et le droit au travail doivent être vérifiés sur la page d'origine.

La recherche s'exécute dans un travail en arrière-plan du processus Streamlit afin que l'interface
reste navigable. Elle n'est pas planifiée et son état n'est pas conservé après redémarrage du
processus. Les annonces déjà enregistrées sont filtrées après la réponse de l'API : Adzuna ne les
exclut pas avant la recherche. Avant usage, confirmer dans le compte Adzuna l'accès au marché
choisi, les quotas, tarifs, attribution et conditions de conservation. Les identifiants ne doivent
pas être placés dans le dépôt ou dans les données de recherche ; les configurer dans
`.streamlit/secrets.toml` ou dans l'environnement local du terminal qui lance Streamlit. Une clé
postée dans une conversation doit être renouvelée avant configuration.
Le formulaire ne présente pas de filtre « télétravail intégral » tant qu'un champ documenté et
fiable n'est pas confirmé pour ce marché ; hors France, le lieu, le mode de travail et la
relocalisation doivent être vérifiés sur la page d'origine.

Chaque connecteur supplémentaire devra :

1. avoir une fiche avec documentation officielle et méthode d'accès ;
2. appliquer les règles d'accès, quotas et limites de conservation de la source ;
3. mapper ses annonces au format commun sans perdre URL originale, source et éventuelle preuve ;
4. échouer de façon visible en cas d'erreur réseau/configuration et rester facultatif à l'usage ;
5. fournir des tests sur des réponses enregistrées anonymisées, sans appels réseau en CI.

## Connecteurs employeurs ciblés : Greenhouse et Lever

Ces connecteurs permettent d'interroger directement les offres publiées par une entreprise sans clé API ni scraping :

- **Greenhouse (`services/job_sources/greenhouse.py`)** : utilise l'API publique de tableau `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`. L'identifiant peut être saisi sous forme de slug ou d'URL complète de tableau. Les descriptions HTML sont nettoyées en texte brut lisible. Le connecteur détecte les contrats, les mentions explicites de relocalisation avec extrait justificatif, et extrait les e-mails de contact publiés.
- **Lever (`services/job_sources/lever.py`)** : utilise l'API publique de publications `https://api.lever.co/v0/postings/{slug}?mode=json` (ou `api.eu.lever.co` pour les instances hébergées en Europe). L'identifiant peut être un slug ou une URL de site (`jobs.lever.co/...` ou `jobs.eu.lever.co/...`). Il combine les sections de texte (description, listes de prérequis/responsabilités, compléments), mappe l'engagement en type de contrat, repère les mentions de relocalisation et isole les e-mails de contact sourcés.

Dans l'interface Streamlit (**Recherche d'offres en ligne → Greenhouse & Lever**), l'utilisateur peut cibler un employeur manuellement ou à partir d'une entreprise enregistrée dans la base locale, filtrer par mot-clé, examiner la correspondance avec son profil, et importer une offre individuellement ou en lot. L'offre importée est automatiquement reliée à la fiche de l'entreprise correspondante en base de données.
