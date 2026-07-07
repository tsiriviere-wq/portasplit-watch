#!/usr/bin/env python3
"""PortaSplitWatch Cloud — surveillance du Midea PortaSplit (MMCS-12HRN8-QRD0).

Interroge l'API ClimRadar, alerte via ntfy.sh (push + e-mail) quand le produit
repasse en stock <= 999 EUR chez une enseigne francaise en ligne.

Modes (variable d'environnement MODE) :
  check     - execution cron : silencieux, n'alerte que sur transition rupture -> stock
  manual    - declenchement a la demande : push systematique avec l'etat complet
  heartbeat - bilan quotidien : push "OK" + resume, ou "EN PANNE" si API morte
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _PARIS = ZoneInfo("Europe/Paris")
except Exception:  # noqa: BLE001 - Windows sans tzdata : repli UTC+2 approximatif
    _PARIS = timezone(timedelta(hours=2))

API_URL = "https://climradar.fr/api/stock"
NTFY_URL = "https://ntfy.sh"
PRODUCT = "portasplit"
MAX_PRICE = 999
COOLDOWN_HOURS = 6
ALERT_EMAIL = os.environ.get("NTFY_EMAIL", "")  # secret GitHub : jamais dans le code public
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
PARIS = _PARIS
UA = "PortaSplitWatch/1.0 (veille stock personnelle)"

FALLBACK_URLS = {
    "amazon": "https://www.amazon.fr/dp/B0CY2YW8BT",
    "manomano": ("https://www.manomano.fr/p/midea-climatiseur-split-mobile-"
                 "reversible-froid-chaud-3500w12000btu-wifi-deshumidificateur-"
                 "ventilateur-jusqua-40m2-kit-fenetre-inclus-83810402"),
}

MODE = os.environ.get("MODE", "check")
TOPIC = os.environ.get("NTFY_TOPIC", "")


def now_paris() -> datetime:
    return datetime.now(tz=PARIS)


def fetch_api() -> dict:
    req = urllib.request.Request(API_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def first_scalar(value):
    """Certains champs de l'API sont des tableaux -> premier scalaire non vide."""
    if isinstance(value, list):
        for v in value:
            if v not in (None, ""):
                return v
        return None
    return value if value not in (None, "") else None


def as_entries(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def get_online_offers(api: dict) -> list:
    names = {r["id"]: r["name"] for r in api.get("retailers", [])}
    offers = []
    for store in api.get("stores", []):
        if store.get("channel") != "online" or store.get("country") != "FR":
            continue
        for e in as_entries(api.get("stockByStore", {}).get(store["id"])):
            if not e or e.get("productId") != PRODUCT:
                continue
            name = names.get(store["retailerId"], store["retailerId"])
            url = first_scalar(store.get("url")) or FALLBACK_URLS.get(store["retailerId"], "")
            offers.append({
                "key": name,
                "name": name,
                "price": first_scalar(e.get("price")),
                "inStock": first_scalar(e.get("status")) == "en_stock",
                "url": url,
            })
    return offers


def count_stores_in_stock(api: dict) -> int:
    count = 0
    store_ids = {s["id"] for s in api.get("stores", [])
                 if s.get("channel") == "store" and s.get("country") == "FR"}
    for sid, value in api.get("stockByStore", {}).items():
        if sid not in store_ids:
            continue
        for e in as_entries(value):
            if e and e.get("productId") == PRODUCT and first_scalar(e.get("status")) == "en_stock":
                count += 1
    return count


def notify(title: str, message: str, priority: int = 3,
           click: str = "", email: bool = False) -> bool:
    if not TOPIC:
        print("ERREUR: NTFY_TOPIC absent, notification impossible")
        return False
    payload = {"topic": TOPIC, "title": title, "message": message,
               "priority": priority, "tags": ["air_conditioner"]}
    if click:
        payload["click"] = click
    if email and ALERT_EMAIL:
        payload["email"] = ALERT_EMAIL
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(NTFY_URL, data=data, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"ntfy: HTTP {resp.status} ({title})")
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 - on veut logguer et continuer
        print(f"ERREUR ntfy: {exc}")
        return False


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def price_txt(price) -> str:
    return f"{price} EUR" if price is not None else "prix a verifier"


def summary_lines(offers: list, stores_in_stock: int) -> str:
    lines = []
    for o in offers:
        statut = "EN STOCK" if o["inStock"] else "rupture"
        lines.append(f"{o['name']} : {statut} ({price_txt(o['price'])})")
    lines.append(f"Magasins physiques FR en stock : {stores_in_stock}")
    return "\n".join(lines)


def main() -> int:
    ts = now_paris()
    try:
        api = fetch_api()
        offers = get_online_offers(api)
        if not offers:
            raise ValueError("aucune offre en ligne FR trouvee (structure API changee ?)")
    except Exception as exc:  # noqa: BLE001
        print(f"ERREUR API ClimRadar: {exc}")
        if MODE == "heartbeat":
            notify("PortaSplitWatch EN PANNE",
                   f"API ClimRadar injoignable ou illisible ({exc}). "
                   "La surveillance ne fonctionne plus - a corriger.", priority=5)
        elif MODE == "manual":
            notify("PortaSplitWatch - erreur",
                   f"Verification impossible : API ClimRadar injoignable ({exc}).")
        return 0  # transitoire en mode check : le heartbeat signalera si ca persiste

    stores_in_stock = count_stores_in_stock(api)
    state = load_state()
    prev_entries = state.get("entries", {})
    first_run = not prev_entries

    # --- detection des transitions rupture -> stock ---
    alerts = []
    for o in offers:
        prev = prev_entries.get(o["key"], {})
        was_in_stock = bool(prev.get("inStock"))
        last_alert = prev.get("lastAlert")
        cooldown_ok = True
        if last_alert:
            try:
                cooldown_ok = ts - datetime.fromisoformat(last_alert) >= timedelta(hours=COOLDOWN_HOURS)
            except ValueError:
                pass
        price_ok = o["price"] is None or float(o["price"]) <= MAX_PRICE
        if not first_run and o["inStock"] and not was_in_stock and price_ok and cooldown_ok:
            alerts.append(o)

    # --- notification d'alerte (push haute priorite + e-mail) ---
    alert_sent = False
    if alerts:
        names = ", ".join(a["name"] for a in alerts)
        body_lines = [f"{a['name']} : {price_txt(a['price'])}\n{a['url']}" for a in alerts]
        body_lines.append("Stock tres volatil (penurie nationale) : commander vite !")
        alert_sent = notify(
            f"PortaSplit DISPONIBLE : {names}",
            "\n\n".join(body_lines),
            priority=5, click=alerts[0]["url"], email=True)

    # --- notifications de mode ---
    if MODE == "manual":
        dispo = [o["name"] for o in offers if o["inStock"]]
        etat = f"DISPONIBLE : {', '.join(dispo)}" if dispo else "Tout en rupture"
        notify(f"PortaSplit - {etat}",
               f"Verification manuelle du {ts.strftime('%d/%m %H:%M')}\n\n"
               + summary_lines(offers, stores_in_stock))
    elif MODE == "heartbeat":
        dispo = [f"{o['name']} ({price_txt(o['price'])})" for o in offers if o["inStock"]]
        etat = f"En stock : {', '.join(dispo)}" if dispo else "tout en rupture"
        notify("PortaSplitWatch OK",
               f"Bilan du {ts.strftime('%d/%m %H:%M')} - {etat}. "
               f"Magasins physiques en stock : {stores_in_stock}. "
               f"{len(offers)} enseignes en ligne surveillees.")

    # --- nouvel etat ---
    new_entries = {}
    for o in offers:
        prev = prev_entries.get(o["key"], {})
        entry = {"name": o["name"], "inStock": o["inStock"],
                 "price": o["price"], "url": o["url"]}
        if prev.get("lastAlert"):
            entry["lastAlert"] = prev["lastAlert"]
        if o in alerts:
            if alert_sent:
                entry["lastAlert"] = ts.isoformat()
            else:
                entry["inStock"] = False  # echec d'envoi -> nouvelle tentative au prochain run
        new_entries[o["key"]] = entry

    # n'ecrit (et donc ne committe) que si le contenu utile change,
    # ou une fois par jour via le heartbeat (garde le depot actif et l'horodatage frais)
    changed = new_entries != prev_entries or state.get("storesInStock") != stores_in_stock
    if changed or MODE == "heartbeat":
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"updatedAt": ts.isoformat(), "entries": new_entries,
                       "storesInStock": stores_in_stock,
                       "product": "Midea PortaSplit 12000 BTU (MMCS-12HRN8-QRD0)",
                       "maxPrice": MAX_PRICE}, fh, ensure_ascii=False, indent=2)
        print("state.json mis a jour")

    statuts = " ; ".join(f"{o['name']}={'STOCK' if o['inStock'] else 'rupture'}" for o in offers)
    print(f"{ts.isoformat()} | {statuts} | magasins:{stores_in_stock} | "
          f"alertes:{len(alerts)} | mode:{MODE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
