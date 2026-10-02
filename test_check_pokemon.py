"""Tests hors ligne : python -m pytest -q (ou python test_check_pokemon.py)"""
import json
import os
import sys
from unittest import mock

import check_pokemon as cp


def test_classify():
    w = [
        "Coffret Collection Ultra Premium 30 ans Journée Mentali - Pokémon FR",
        "Pokémon ME 5.5 - 30e Anniversaire - Collection Ultra-Premium Soirée | FR",
        "30th Celebration - Ultra-Premium Collection Day (ESPAÑOL)",
        "Colección Ultra Premium Noche Umbreon ex 30.º Aniversario",
        "UPC Noctali 30 ans",
    ]
    a = [
        "Pokémon - 30 ans : Coffret Dresseur d'Elite",
        "Pokémon - Coffret 30e Anniversaire ME05.5 : Nymphali ex",
        "Pokémon ME 5.5 - 30e Anniversaire - Collection poster | FR",
        "Pokémon XXX ans - Mini Tin",
    ]
    no = [
        "Coffret - Pokemon - Collection Ultra Premium Amphinobi - Scellé - Français",
        "Ultra-Premium Méga Dracaufeu X ME02 Flammes Fantasmagoriques",
        "Display de 20 boosters Pokémon 30 ans 30th Celebration [M6A] - CN",
        "[PRECOMMANDE] Display - M6a Pokémon 30th - JAP",
        "Pokémon - Display - Futur Flash - Boîte de 30 Boosters en coréen",
        "Boîte de 30 Boosters de 5 cartes",
        "Coffret 3 boosters Pokémon DAY 30 ANS - 2026",  # pas un Ultra-Premium -> 'anniv' accepte
    ]
    for t in w:
        assert cp.classify(t) == "watch", t
    for t in a:
        assert cp.classify(t) == "anniv", t
    for t in no[:-1]:
        assert cp.classify(t) is None, t
    assert cp.classify(no[-1]) == "anniv"


def run_diff(prev, cur):
    calls = []
    merged = cp.diff_site("Boutique", prev, cur,
                          notify=lambda *a, **k: calls.append((a, k)))
    return merged, calls


UPC = {"title": "Collection Ultra-Premium 30e Anniversaire Journée Mentali", "url": "https://x/upc",
       "available": False, "price": 229.9}
ETB = {"title": "Coffret Dresseur d'Élite 30e Anniversaire", "url": "https://x/etb", "available": True, "price": 59.9}
OLD = {"title": "Booster Évolutions Prismatiques", "url": "https://x/old", "available": True, "price": 6.0}


def test_first_run_silent():
    merged, calls = run_diff({}, {"1": UPC, "2": ETB})
    assert calls == [] and set(merged) == {"1", "2"}


def test_new_upc_is_urgent_and_new_anniv_is_normal():
    _, calls = run_diff({"0": OLD}, {"0": OLD, "1": UPC, "2": ETB})
    urgent = [c for c in calls if c[1].get("urgent")]
    assert len(calls) == 2 and len(urgent) == 1
    assert "Ultra-Premium" in urgent[0][0][0] and "pas encore en stock" in urgent[0][0][1]


def test_restock_alert_once():
    _, calls = run_diff({"1": UPC}, {"1": {**UPC, "available": True}})
    assert len(calls) == 1 and calls[0][1]["urgent"] and "Retour en stock" in calls[0][0][0]
    _, calls = run_diff({"1": {**UPC, "available": True}}, {"1": {**UPC, "available": True}})
    assert calls == []


def test_disappeared_product_kept_but_not_in_stock():
    merged, calls = run_diff({"1": {**UPC, "available": True}, "0": OLD}, {"0": OLD})
    assert calls == [] and merged["1"]["available"] is None
    _, calls = run_diff(merged, {"0": OLD, "1": {**UPC, "available": True}})
    assert len(calls) == 1 and calls[0][1]["urgent"]


def test_other_products_silent_unless_notify_all():
    _, calls = run_diff({"0": OLD}, {"0": OLD, "9": {**OLD, "title": "Booster Flammes"}})
    assert calls == []


def test_email_routing():
    sent = []
    with mock.patch.object(cp, "NOTIFY_EMAIL", "andy@example.com"), \
         mock.patch.object(cp, "NTFY_TOPIC", ""), \
         mock.patch.object(cp, "SMTP_USER", ""), mock.patch.object(cp, "SMTP_PASSWORD", ""), \
         mock.patch.object(cp, "NTFY_FALLBACK_TOPIC", "veille-pokemon-abc"), \
         mock.patch.object(cp.requests, "post", side_effect=lambda *a, **k: sent.append(k["json"]) or mock.Mock()):
        cp.send_notification("Nouveau 30e", "ETB", "https://x/etb")          # non urgent : pas d'email
        cp.send_notification("🚨 Ultra-Premium", "UPC", "https://x/upc", urgent=True)
    assert len(sent) == 1
    assert sent[0]["email"] == "andy@example.com" and sent[0]["priority"] == 5
    assert sent[0]["topic"] == "veille-pokemon-abc" and "https://x/upc" in sent[0]["message"]


def test_smtp_used_when_configured():
    with mock.patch.object(cp, "NOTIFY_EMAIL", "andy@example.com"), \
         mock.patch.object(cp, "NTFY_TOPIC", ""), \
         mock.patch.object(cp, "SMTP_USER", "moi@gmail.com"), mock.patch.object(cp, "SMTP_PASSWORD", "x"), \
         mock.patch.object(cp, "send_email_smtp") as smtp, \
         mock.patch.object(cp.requests, "post") as post:
        cp.send_notification("🚨 Ultra-Premium", "UPC", "https://x/upc", urgent=True)
    smtp.assert_called_once()
    post.assert_not_called()


def test_shopify_parsing():
    page = {"products": [{"id": 1, "title": UPC["title"], "handle": "upc-mentali",
                          "variants": [{"available": False, "price": "249.90"}, {"available": True, "price": "259.90"}]}]}
    resp = mock.Mock(); resp.json.return_value = page; resp.raise_for_status.return_value = None
    with mock.patch.object(cp.requests.Session, "get", return_value=resp):
        out = cp.fetch_shopify("https://shop.test")
    assert out["1"] == {"title": UPC["title"], "url": "https://shop.test/products/upc-mentali",
                        "available": True, "price": 249.9}


def test_status_file():
    st = cp.build_status({"strikegames": {"1": UPC, "0": OLD}}, {"strikegames": ("Strike Games", "FR")}, {})
    assert [p["kind"] for p in st["products"]] == ["watch"]
    json.dumps(st)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
