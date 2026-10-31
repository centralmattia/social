"""Caricamento dei file su Scaleway Object Storage (compatibile S3) e verifica dell'URL pubblico.

Instagram scarica il media da un URL pubblico: dopo ogni upload controlliamo che l'URL risponda davvero (HTTP 200),
così non creiamo contenitori Meta destinati a fallire.
Credenziali solo da .env: SCW_ACCESS_KEY, SCW_SECRET_KEY.
"""
import mimetypes
import os
from pathlib import Path

import requests

import meta_common as mc


class StorageError(RuntimeError):
    pass


def settings():
    s = {
        "access_key": mc.env("SCW_ACCESS_KEY"),
        "secret_key": mc.env("SCW_SECRET_KEY"),
        "bucket": mc.env("SCW_BUCKET", default="central-social"),
        "region": mc.env("SCW_REGION", default="fr-par"),
        "prefix": (mc.env("SCW_PREFIX", default="social/") or "").lstrip("/"),
    }
    s["endpoint"] = mc.env("SCW_ENDPOINT", default=f"https://s3.{s['region']}.scw.cloud")
    s["public_base"] = (mc.env("SCW_PUBLIC_BASE", default=f"https://{s['bucket']}.s3.{s['region']}.scw.cloud")).rstrip("/")
    return s


def public_url(key, cfg=None):
    cfg = cfg or settings()
    return f"{cfg['public_base']}/{key}"


def _client(cfg):
    if not cfg["access_key"] or not cfg["secret_key"]:
        raise StorageError("Credenziali Scaleway mancanti: imposta SCW_ACCESS_KEY e SCW_SECRET_KEY in .env")
    import boto3  # import tardivo: serve solo per l'upload
    return boto3.client("s3", endpoint_url=cfg["endpoint"], region_name=cfg["region"],
                        aws_access_key_id=cfg["access_key"], aws_secret_access_key=cfg["secret_key"])


def check_public(url, timeout=20):
    """True se l'URL è leggibile senza login."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 405:  # alcuni server non accettano HEAD
            r = requests.get(url, timeout=timeout, stream=True)
        return r.status_code == 200
    except requests.RequestException:
        return False


def upload_files(paths, folder, client=None, checker=check_public):
    """Carica i file in <prefix>/<folder>/<nome>. Ritorna la lista di URL pubblici, nello stesso ordine."""
    cfg = settings()
    client = client or _client(cfg)
    urls = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise StorageError(f"File non trovato: {p}")
        key = f"{cfg['prefix']}{folder}/{p.name}"
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        extra = {"ContentType": ctype}
        acl = mc.env("SCW_OBJECT_ACL")  # facoltativo: es. public-read se il bucket non è già pubblico
        if acl:
            extra["ACL"] = acl
        try:
            client.upload_file(str(p), cfg["bucket"], key, ExtraArgs=extra)
        except Exception as exc:
            raise StorageError(mc.redact(f"Upload fallito per {p.name}: {exc}")) from None
        url = public_url(key, cfg)
        if not checker(url):
            raise StorageError(f"Caricato ma non leggibile pubblicamente: {url}. Controlla che il bucket sia Public "
                               "(o imposta SCW_OBJECT_ACL=public-read).")
        urls.append(url)
    return urls
