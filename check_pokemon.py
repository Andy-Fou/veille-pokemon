#!/usr/bin/env python3
"""
Veille Pokemon 30e Anniversaire — version d'Andy
(basee sur le bot de Mathieu : github.com/mathgirault9-sys/pokemon_daftpunker_sacha)

Toutes les 20 minutes (GitHub Actions) :
- recupere les produits Pokemon de plusieurs boutiques francaises et espagnoles ;
- compare avec le passage precedent (seen_products.json) ;
- envoie une alerte (email, et push ntfy si configure) :
    * URGENTE pour un coffret Ultra-Premium Mentali / Noctali (nouveau produit
      OU retour en stock) ;
    * normale pour un autre produit 30e Anniversaire nouvellement liste ;
    * rien pour le reste (sauf si NOTIFY_ALL=1) ;
- ecrit watch_status.json : l'etat des produits surveilles, lu chaque matin
  par la veille Claude pour enrichir le tableau de bord.

Configuration : variables d'environnement (secrets GitHub)
  NOTIFY_EMAIL   adresse qui recoit les alertes urgentes par email
  EMAIL_ALL_30   "1" pour recevoir aussi par email les autres produits 30e Anniversaire
  SMTP_USER / SMTP_PASSWORD / SMTP_HOST   facultatif : envoi direct par Gmail
                 (sinon l'email passe par le service gratuit ntfy.sh)
  NTFY_TOPIC     facultatif : notifications push sur l'app ntfy
  NOTIFY_ALL     "1" pour un push ntfy a chaque nouveau produit, comme le bot d'origine
"""

import hashlib
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "seen_products.json"
STATUS_FILE = ROOT / "watch_status.json"

NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "").strip()
EMAIL_ALL_30 = os.environ.get("EMAIL_ALL_30", "").strip() == "1"
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com"
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
# sujet ntfy utilise seulement pour transporter les emails quand NTFY_TOPIC est vide
NTFY_FALLBACK_TOPIC = ("veille-pokemon-" + hashlib.sha256(NOTIFY_EMAIL.encode()).hexdigest()[:16]) if NOTIFY_EMAIL else ""
NOTIFY_ALL = os.environ.get("NOTIFY_ALL", "").strip() == "1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,es-ES;q=0.8,en;q=0.7",
}
MAX_PAGES_DEFAULT = 15
PAUSE = 1.0  # secondes entre deux pages d'un meme site, par politesse


# ---------------------------------------------------------------------------
# Ce qu'on surveille. Les textes sont compares sans accents et en minuscules.
# ---------------------------------------------------------------------------

def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower())


RE_ULTRA_PREMIUM = re.compile(r"ultra[\s\-]*premium|\bupc\b")
RE_EVOLI = re.compile(r"mentali|noctali|espeon|umbreon|journee|soiree|\bday\b|\bnight\b|\bdia\b|\bnoche\b")
RE_30 = re.compile(r"30\s?(e|eme|th|ans)\b|30\.?\s?o\b|30th|30-ans|30e\b|30-aniv|30 aniv|xxx ans|30eme|me\s?0?5[.,_]5")
RE_ASIAN = re.compile(r"\bjp\b|\bjap\b|japonais|japones|japan|\bcn\b|chinois|chino|\bkr\b|coreen|coreano|\[ch\]")


def classify(title, url=""):
    """Retourne 'watch' (Ultra-Premium Mentali/Noctali), 'anniv' (autre
    produit 30e Anniversaire) ou None."""
    t = norm(f"{title} {url}")
    if RE_ASIAN.search(t):
        return None
    is_upc = bool(RE_ULTRA_PREMIUM.search(t))
    if is_upc and (RE_EVOLI.search(t) or RE_30.search(t)):
        return "watch"
    if RE_30.search(t):
        return "anniv"
    return None


# ---------------------------------------------------------------------------
# Recuperateurs. Chaque fonction renvoie {id: {title, url, available, price}}
# available : True / False, ou None si le site ne le dit pas.
# ---------------------------------------------------------------------------

def get(session, url, **kw):
    resp = session.get(url, timeout=30, **kw)
    resp.raise_for_status()
    return resp


def fetch_shopify(base_url, collection=None, max_pages=10):
    """Boutiques Shopify : API JSON publique /products.json. Donne aussi le
    stock (variants[].available) et le prix. Sans collection, on parcourt
    tout le catalogue (le filtre par mots-cles fait le tri)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    path = f"/collections/{collection}/products.json" if collection else "/products.json"
    products = {}
    for page in range(1, max_pages + 1):
        data = get(session, base_url + path, params={"limit": 250, "page": page}).json()
        batch = data.get("products", [])
        if not batch:
            break
        for p in batch:
            variants = p.get("variants") or []
            prices = [float(v["price"]) for v in variants if v.get("price")]
            products[str(p["id"])] = {
                "title": p.get("title") or "(titre indisponible)",
                "url": f"{base_url}/products/{p.get('handle', '')}",
                "available": any(v.get("available") for v in variants) if variants else None,
                "price": min(prices) if prices else None,
            }
        if len(batch) < 250:
            break
        time.sleep(PAUSE)
    return products


def fetch_woocommerce(base_url, search):
    """Boutiques WooCommerce : API publique Store API, avec le stock."""
    session = requests.Session()
    session.headers.update(HEADERS)
    products = {}
    for page in range(1, 6):
        resp = get(session, f"{base_url}/wp-json/wc/store/v1/products",
                   params={"search": search, "per_page": 100, "page": page})
        batch = resp.json()
        if not batch:
            break
        for p in batch:
            raw = (p.get("prices") or {}).get("price")
            minor = (p.get("prices") or {}).get("currency_minor_unit", 2)
            products[str(p["id"])] = {
                "title": BeautifulSoup(p.get("name", ""), "html.parser").get_text(),
                "url": p.get("permalink", ""),
                "available": p.get("is_in_stock"),
                "price": int(raw) / 10 ** minor if raw else None,
            }
        if len(batch) < 100:
            break
        time.sleep(PAUSE)
    return products


def fetch_html_links(category_url, id_pattern, base_url, max_pages=MAX_PAGES_DEFAULT,
                     page_style="query"):
    """Sites sans API (PrestaShop, Odoo, Wix...) : on lit les liens produits
    de la page categorie. Pas d'info de stock (available = None).
    page_style : 'query' (?page=N) ou 'path' (/page/N)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    pattern = re.compile(id_pattern)
    products = {}
    for page in range(1, max_pages + 1):
        if page_style == "path":
            url = category_url if page == 1 else f"{category_url}/page/{page}"
            resp = get(session, url)
        else:
            resp = get(session, category_url, params={"page": page})
        soup = BeautifulSoup(resp.text, "html.parser")
        found = 0
        for a in soup.find_all("a", href=True):
            m = pattern.search(a["href"])
            if not m:
                continue
            pid = m.group(1)
            title = a.get_text(" ", strip=True)
            link = a["href"].split("?")[0]
            if not link.startswith("http"):
                link = base_url + link
            if pid not in products:
                found += 1
                products[pid] = {"title": title or "(titre indisponible)", "url": link,
                                 "available": None, "price": None}
            elif title and products[pid]["title"] in ("(titre indisponible)", "Aperçu rapide"):
                products[pid]["title"] = title
        if found == 0:
            break
        time.sleep(PAUSE)
    for item in products.values():
        if item["title"] in ("(titre indisponible)", "Aperçu rapide", "Créer une alerte"):
            # le titre du lien est inutilisable : on garde l'adresse lisible
            item["title"] = item["url"].rstrip("/").split("/")[-1].replace("-", " ")
    return products


# ---------------------------------------------------------------------------
# Boutiques suivies : (cle, nom affiche, pays, fonction)
# Pour en ajouter une Shopify : ("cle", "Nom", "FR", lambda: fetch_shopify("https://site"))
# Si une boutique echoue, les autres continuent ; l'erreur apparait dans les
# journaux de GitHub Actions.
# ---------------------------------------------------------------------------

SITES = [
    # --- France (reprises du bot de Mathieu) ---
    ("philibert", "Philibert", "FR", lambda: fetch_html_links(
        "https://www.philibertnet.com/fr/212-pokemon/s-3/langues-francais",
        r"/fr/pokemon/(\d+)-[a-z0-9-]+\.html", "https://www.philibertnet.com")),
    ("strikegames", "Strike Games", "FR", lambda: fetch_shopify(
        "https://strikegames.shop", "tcg-pokemon-produit-en-francais")),
    ("investcollect", "InvestCollect", "FR", lambda: fetch_html_links(
        "https://investcollect.com/eshop/produits-scelles.html",
        r"/eshop/p/([a-z0-9\-_.]+)\.html", "https://investcollect.com")),
    ("auxtroiskoalas", "Aux Trois Koalas", "FR", lambda: fetch_html_links(
        "https://www.auxtroiskoalas.fr/shop/category/pokemon-coffrets-428",
        r"/shop/pokemon-coffrets-428/[a-z0-9\-]+-(\d+)", "https://www.auxtroiskoalas.fr",
        page_style="path")),
    ("arakemon", "Arakemon", "FR", lambda: fetch_html_links(
        "https://www.arakemon.com/coffrets-pokemon",
        r"/(product-page/[^?\s\"']+)", "https://www.arakemon.com")),
    ("pikaboutique", "Pika-Boutique", "FR", lambda: fetch_shopify("https://pika-boutique.fr")),
    # --- France (autres boutiques Shopify vues dans les donnees de Mathieu) ---
    ("kairyu", "Kairyu", "FR", lambda: fetch_shopify("https://kairyu.fr")),
    ("relictcg", "Relic TCG", "FR", lambda: fetch_shopify("https://www.relictcg.com")),
    ("lebordelmagique", "Le Bordel Magique", "FR", lambda: fetch_shopify("https://lebordelmagique.com")),
    ("tradingcardsxxx", "Trading Cards XXX", "FR", lambda: fetch_shopify("https://tradingcardsxxx.fr")),
    ("masterset", "Masterset", "FR", lambda: fetch_shopify("https://masterset.store")),
    # --- Espagne ---
    ("jjcollection", "JJ Collection", "ES", lambda: fetch_shopify("https://www.jjcollection.es")),
    ("pokeelite", "PokeElite TCG", "ES", lambda: fetch_shopify("https://pokeelitetcg.com")),
    ("metamorphcenter", "Metamorph Center", "ES", lambda: fetch_shopify("https://metamorphcenter.com")),
    ("pokemillon", "Pokemillon", "ES", lambda: fetch_shopify("https://www.pokemillon.com")),
    ("flashstore", "Flash Store", "ES", lambda: fetch_woocommerce("https://flashstore.es", "pokemon")),
]


# ---------------------------------------------------------------------------
# Notifications ntfy (envoi en JSON : accents et emojis passent sans souci)
# ---------------------------------------------------------------------------

def send_email_smtp(subject, body):
    """Plan B : envoi direct par SMTP (ex. Gmail avec un mot de passe
    d'application). Utilise seulement si SMTP_USER et SMTP_PASSWORD existent."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL
    msg.set_content(body)
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def send_notification(title, message, url=None, urgent=False, tags=None):
    """Email pour les alertes urgentes (et pour les autres produits 30e
    Anniversaire si EMAIL_ALL_30=1). Push sur telephone si NTFY_TOPIC est
    defini et que l'app ntfy est abonnee a ce sujet (facultatif)."""
    want_email = bool(NOTIFY_EMAIL) and (urgent or EMAIL_ALL_30)
    if not NTFY_TOPIC and not want_email:
        print(f"[notifications desactivees] {'URGENT ' if urgent else ''}{title} - {message} {url or ''}")
        return
    body = message + (f"\n\nLien : {url}" if url else "")

    if want_email and SMTP_USER and SMTP_PASSWORD:
        try:
            send_email_smtp(title, body)
            print(f"Email envoye : {title}")
        except Exception as e:
            print(f"Erreur envoi email SMTP: {e}", file=sys.stderr)
        want_email = False  # deja traite, ne pas doubler via ntfy

    if not NTFY_TOPIC and not want_email:
        return
    payload = {
        # ntfy a besoin d'un sujet meme pour un simple email ; sans app abonnee,
        # personne ne voit le push et seul l'email compte.
        "topic": NTFY_TOPIC or NTFY_FALLBACK_TOPIC,
        "title": title,
        "message": body,
        "priority": 5 if urgent else 3,
        "tags": tags or (["rotating_light"] if urgent else ["pokeball"]),
    }
    if want_email:
        payload["email"] = NOTIFY_EMAIL
    if url:
        payload["click"] = url
        payload["actions"] = [{"action": "view", "label": "Ouvrir la boutique", "url": url}]
    try:
        requests.post("https://ntfy.sh/", json=payload, timeout=15).raise_for_status()
        if want_email:
            print(f"Email demande via ntfy : {title}")
    except requests.RequestException as e:
        print(f"Erreur envoi notification ntfy: {e}", file=sys.stderr)


def fmt_price(price):
    return f"{price:.2f} €".replace(".", ",") if isinstance(price, (int, float)) else "prix ?"


# ---------------------------------------------------------------------------
# Comparaison et alertes
# ---------------------------------------------------------------------------

def diff_site(label, previous, current, notify=send_notification):
    """Compare l'etat precedent et l'etat courant d'un site, envoie les
    notifications et renvoie l'etat fusionne (on n'oublie jamais un produit
    qui disparait momentanement, sinon il redeclencherait une alerte)."""
    first_run = not previous
    for pid, item in current.items():
        kind = classify(item["title"], item["url"])
        before = previous.get(pid)
        if first_run:
            continue
        price = fmt_price(item.get("price"))
        if before is None:
            if kind == "watch":
                dispo = {True: "EN STOCK", False: "pas encore en stock", None: "stock inconnu"}[item.get("available")]
                notify(f"🚨 Ultra-Premium chez {label}", f"{item['title']} — {dispo}, {price}",
                       item["url"], urgent=True)
            elif kind == "anniv":
                notify(f"Nouveau 30e Anniversaire — {label}", f"{item['title']} — {price}", item["url"])
            elif NOTIFY_ALL:
                notify(f"Nouveau produit Pokémon — {label}", item["title"], item["url"])
        elif kind == "watch" and item.get("available") is True and before.get("available") is not True:
            notify(f"🚨 Retour en stock chez {label}", f"{item['title']} — {price}",
                   item["url"], urgent=True)
    if first_run:
        print(f"[{label}] Premiere execution : {len(current)} produits enregistres (pas de notif).")
    merged = dict(previous)
    for pid, item in current.items():
        merged[pid] = item
    # un produit absent de ce passage n'est plus considere comme en stock
    for pid in set(previous) - set(current):
        if merged[pid].get("available"):
            merged[pid] = {**merged[pid], "available": None}
    return merged


def build_status(state, site_info, errors):
    """Resume des produits surveilles, lu par la veille Claude."""
    watched = []
    for key, items in state.items():
        label, country = site_info.get(key, (key, "?"))
        for item in items.values():
            kind = classify(item.get("title", ""), item.get("url", ""))
            if kind:
                watched.append({"kind": kind, "shop": label, "country": country, **item})
    watched.sort(key=lambda w: (w["kind"] != "watch", w["shop"], w["title"]))
    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "errors": errors,
        "products": watched,
    }


def load_json(path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def main():
    state = load_json(STATE_FILE, {})
    site_info = {key: (label, country) for key, label, country, _ in SITES}
    errors = {}
    for key, label, _country, fetch_fn in SITES:
        try:
            current = fetch_fn()
        except Exception as e:  # un site en panne ne bloque pas les autres
            errors[label] = str(e)[:200]
            print(f"Erreur recuperation {label}: {e}", file=sys.stderr)
            continue
        state[key] = diff_site(label, state.get(key, {}), current)
        n_watch = sum(1 for i in current.values() if classify(i["title"], i["url"]))
        print(f"[{label}] {len(current)} produits, dont {n_watch} 30e Anniversaire.")
    save_json(STATE_FILE, state)
    save_json(STATUS_FILE, build_status(state, site_info, errors))


if __name__ == "__main__":
    main()
