"""Funzioni comuni per Instagram API with Instagram Login (graph.instagram.com).

- Accetta sia i nomi variabile del Piano C (META_IG_USER_ID) sia quelli alternativi
  (INSTAGRAM_ACCOUNT_ID, INSTAGRAM_APP_ID, ...).
- Oscura sempre token e chiave segreta da errori e log.
"""
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOST = "https://graph.instagram.com"
DEFAULT_VERSION = "v24.0"


def env(*names, default=None):
    """Primo valore non vuoto tra i nomi indicati."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


def host():
    return env("META_GRAPH_HOST", default=DEFAULT_HOST).rstrip("/")


def version():
    return env("META_GRAPH_API_VERSION", default=DEFAULT_VERSION)


def token():
    return env("META_ACCESS_TOKEN", "INSTAGRAM_ACCESS_TOKEN")


def ig_user_id():
    return env("META_IG_USER_ID", "INSTAGRAM_ACCOUNT_ID")


def app_id():
    return env("META_APP_ID", "INSTAGRAM_APP_ID")


def app_secret():
    return env("META_APP_SECRET", "INSTAGRAM_APP_SECRET")


def redact(text):
    """Rimuove token e segreti da qualunque testo prima di stamparlo o salvarlo."""
    text = str(text)
    for secret in (token(), app_secret()):
        if secret:
            text = text.replace(secret, "***")
    return re.sub(r"(access_token|client_secret)=[^&\s'\"]+", r"\1=***", text)


class MetaError(RuntimeError):
    pass


def request_json(method, url, params=None, timeout=30):
    """Chiamata HTTP con errori leggibili e senza token nei messaggi."""
    try:
        r = requests.request(method, url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise MetaError(redact(f"Errore di rete: {exc}")) from None
    try:
        data = r.json()
    except ValueError:
        raise MetaError(f"Meta ha restituito una risposta non JSON (HTTP {r.status_code})") from None
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise MetaError(redact(f"Meta API {err.get('code', r.status_code)}: {err.get('message', r.text[:300])}"))
    return data


def get_json(path, params=None):
    """GET su host/versione/path. Il token viene aggiunto qui."""
    tok = token()
    if not tok:
        raise MetaError("META_ACCESS_TOKEN non configurato")
    p = dict(params or {})
    p["access_token"] = tok
    return request_json("GET", f"{host()}/{version()}/{path}", p)


def post_json(path, params=None):
    tok = token()
    if not tok:
        raise MetaError("META_ACCESS_TOKEN non configurato")
    p = dict(params or {})
    p["access_token"] = tok
    return request_json("POST", f"{host()}/{version()}/{path}", p, timeout=60)
