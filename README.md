# Veille Pokémon 30e Anniversaire

Version adaptée du bot de Mathieu
([pokemon_daftpunker_sacha](https://github.com/mathgirault9-sys/pokemon_daftpunker_sacha)).

Toutes les 20 minutes, ce bot regarde **16 boutiques** (11 en France, 5 en Espagne). Il vous envoie
un **email** dès qu'un coffret **Ultra-Premium 30e Anniversaire Mentali ou Noctali**
apparaît dans l'une d'elles **ou revient en stock**.

Différences avec le bot de Mathieu :

| | Bot de Mathieu | Cette version |
|---|---|---|
| Alerte | push à chaque nouveau produit Pokémon | **email** seulement pour les coffrets Mentali / Noctali |
| Boutiques | France | France **+ Espagne** |
| Retour en stock | non | **oui**, pour les boutiques qui l'affichent (Shopify, WooCommerce) |
| Tableau de bord | — | écrit `watch_status.json`, lu chaque matin par votre veille Claude |

## Ce qu'il vous faut

- Un compte **GitHub** gratuit ([github.com](https://github.com)).
- C'est tout : pas d'application ni de mot de passe à fournir. Les emails passent
  par le service gratuit [ntfy.sh](https://ntfy.sh).

## Installation (environ 10 minutes)

### 1. Créer le dépôt

1. Sur GitHub, cliquez sur **New** (bouton vert) pour créer un dépôt, par
   exemple `veille-pokemon`.
2. Choisissez **Public**. Il le faut pour deux raisons : GitHub Actions est gratuit
   et illimité pour un dépôt public (un dépôt privé dépasserait le quota gratuit
   avec un passage toutes les 20 minutes), et votre veille Claude doit pouvoir lire
   le résultat. Votre adresse email n'apparaît **jamais** dans le dépôt : elle est
   rangée dans un secret, à l'étape 2.
3. Cliquez sur **Add file → Upload files** et déposez **le contenu** du dossier
   décompressé : `check_pokemon.py`, `requirements.txt`, `seen_products.json`,
   `README.md`, `test_check_pokemon.py`.
4. Le dossier `.github` est souvent caché et ne se dépose pas toujours. Si, après
   l'envoi, vous ne voyez pas `.github/workflows/check.yml` dans le dépôt :
   **Add file → Create new file**, tapez comme nom
   `.github/workflows/check.yml`, puis collez le contenu du fichier `check.yml`
   fourni (ouvrez-le avec un éditeur de texte).

### 2. Indiquer votre adresse email

1. Dans le dépôt : **Settings → Secrets and variables → Actions**.
2. **New repository secret** :
   - Nom : `NOTIFY_EMAIL`
   - Valeur : votre adresse email
3. Enregistrez.

### 3. Premier lancement

1. Onglet **Actions**. Si GitHub le demande, cliquez sur
   **I understand my workflows, go ahead and enable them**.
2. Cliquez sur **Veille Pokemon 30 ans**, puis sur **Run workflow**.
3. Ce premier passage **enregistre ce qui est déjà en ligne** et n'envoie aucun
   email. Les suivants tournent seuls toutes les 20 minutes.
4. Envoyez-moi (Claude) l'adresse de votre dépôt : je branche votre tableau de bord
   dessus.

## Les emails

- Expéditeur : **ntfy@ntfy.sh**. Le premier peut arriver dans les indésirables :
  marquez-le « non spam ».
- Objet « 🚨 Ultra-Premium chez … » : le coffret vient d'apparaître dans une boutique.
- Objet « 🚨 Retour en stock chez … » : il était en rupture et redevient achetable.
- Chaque email contient le lien direct vers la fiche produit.
- ntfy.sh limite le nombre d'emails gratuits (une quinzaine d'affilée), ce qui suffit
  largement pour ces alertes rares. C'est pour cela que les autres produits
  30e Anniversaire n'envoient pas d'email par défaut.

## Réglages facultatifs

Dans **Settings → Secrets and variables → Actions** :

| Réglage | Type | Effet |
|---|---|---|
| `EMAIL_ALL_30` = `1` | Variable (onglet *Variables*) | email aussi pour chaque **nouveau** produit 30e Anniversaire (ETB, bundle, decks…) |
| `SMTP_USER` + `SMTP_PASSWORD` | Secrets | envoi par **votre propre Gmail** plutôt que par ntfy.sh (plus fiable, sans quota). `SMTP_PASSWORD` est un [mot de passe d'application Google](https://myaccount.google.com/apppasswords), pas votre mot de passe habituel. |
| `NTFY_TOPIC` | Secret | notifications push en plus, via l'app ntfy (comme le bot de Mathieu) |

## Ajouter ou retirer une boutique

Tout se passe dans la liste `SITES` de `check_pokemon.py`. Pour une boutique
Shopify (adresses en `/products/…`), une ligne suffit :

```python
("cle", "Nom affiché", "ES", lambda: fetch_shopify("https://www.boutique.es")),
```

## Dépannage

- **Rien ne se passe ?** Onglet **Actions** → dernière exécution → étape
  « Lancer la verification ». Chaque boutique y affiche son nombre de produits ou
  son erreur.
- **Une boutique est en erreur ?** Les autres continuent. Le site a peut-être changé
  ou bloque les robots. Le fichier `watch_status.json` liste les erreurs du dernier
  passage.
- **Tester sans internet** : `python test_check_pokemon.py`.
