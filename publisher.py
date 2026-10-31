"""Publisher conservativo. Elabora SOLO i post con stato `pending` (cioè approvati a mano) e già scaduti.

- Instagram: pubblicazione diretta con Instagram API: Reel (media_url), post immagine e caroselli.
  Le immagini create con make_media.py (`media_files`) vengono caricate su Scaleway solo in --live.
  Post senza media o formati non supportati vanno in `needs_review`.
- TikTok: testo via Buffer (opzionale).
- Reddit: file Markdown da pubblicare a mano.
- `--dry-run` non scrive niente e non chiama le API di pubblicazione.
- `--live` richiede ALLOW_LIVE_PUBLISH=true.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import meta_common as mc
import storage

QUEUE = Path(os.getenv("QUEUE_FILE", "queue.json"))
# Lo stato di pubblicazione sta in un file a parte: lo scrive solo il publisher (anche su GitHub Actions),
# mentre queue.json lo modifichi solo tu. Così non ci sono conflitti di Git.
STATE = Path(os.getenv("STATE_FILE", "publish_state.json"))
STATE_FIELDS = ("status", "attempts", "last_error", "provider_id", "provider_status", "published_url",
                "provider_container_id", "media_urls", "media_url")
BUFFER_ENDPOINT = "https://api.buffer.com"
IMAGE_FORMATS = {"post", "image", "immagine"}
REEL_FORMATS = {"reel", "video"}
CAROUSEL_FORMATS = {"carousel", "carosello"}


def buffer_graphql(query, variables=None):
    key = os.getenv("BUFFER_API_KEY")
    if not key:
        raise RuntimeError("Manca BUFFER_API_KEY")
    r = requests.post(BUFFER_ENDPOINT, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"query": query, "variables": variables or {}}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    return data.get("data", {})


def publish_buffer_text(channel_id, caption, dry_run):
    if not channel_id:
        raise RuntimeError("Channel ID Buffer non configurato")
    query = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id text status } }
        ... on MutationError { message }
      }
    }
    """
    variables = {"input": {"text": caption, "channelId": channel_id, "schedulingType": "automatic", "mode": "addToQueue"}}
    if dry_run:
        return {"dry_run": True, "channel_id": channel_id, "text": caption[:120]}
    result = buffer_graphql(query, variables).get("createPost") or {}
    if result.get("message"):
        raise RuntimeError(result["message"])
    post = result.get("post")
    if not post:
        raise RuntimeError("Risposta Buffer senza post confermato: " + json.dumps(result))
    return {"provider_id": post.get("id"), "status": post.get("status")}


class PublishUncertain(RuntimeError):
    """La richiesta di pubblicazione è partita ma l'esito non è certo: non riprovare in automatico."""


def instagram_kind(post):
    fmt = (post.get("format") or "post").lower()
    if fmt in REEL_FORMATS:
        return "REELS"
    if fmt in CAROUSEL_FORMATS:
        return "CAROUSEL"
    if fmt in IMAGE_FORMATS:
        return "IMAGE"
    return None


def media_sources(post):
    """URL pubblici già pronti, oppure file locali da caricare."""
    urls = post.get("media_urls") or ([post["media_url"]] if post.get("media_url") else [])
    return urls, (post.get("media_files") or [])


def ensure_media(post, upload=None):
    """Carica su Scaleway i file locali (solo in live) e salva gli URL pubblici nella bozza."""
    urls, files = media_sources(post)
    if urls or not files:
        return False
    missing = [f for f in files if not Path(f).exists()]
    if missing and upload is None:
        raise ValueError("File locali non trovati (su GitHub Actions non esistono): esegui `python upload_media.py` sul PC, "
                         "poi fai commit e push di queue.json.")
    upload = upload or storage.upload_files
    post["media_urls"] = upload(files, post["id"])
    if len(post["media_urls"]) == 1:
        post["media_url"] = post["media_urls"][0]
    return True


def wait_finished(container, sleep, what):
    for _ in range(40):
        status = mc.get_json(container, {"fields": "status_code"}).get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Elaborazione {what} fallita: {status}")
        sleep(10)
    raise RuntimeError(f"{what.capitalize()} ancora in elaborazione dopo 400 secondi: riprova più tardi")


def publish_instagram(post, dry_run, sleep=time.sleep):
    kind = instagram_kind(post)
    if kind is None:
        raise ValueError(f"Formato '{post.get('format')}' non supportato per la pubblicazione automatica (reel, carousel, post immagine).")
    urls, files = media_sources(post)
    n = len(urls) or len(files)
    if n == 0:
        raise ValueError("Instagram richiede un media: manca media_url / media_urls (o immagini create con make_media.py).")
    if kind == "CAROUSEL" and not (2 <= n <= 10):
        raise ValueError(f"Un carosello richiede da 2 a 10 immagini, ne ho {n}.")
    if kind in ("IMAGE", "REELS") and n != 1:
        raise ValueError(f"Il formato '{post.get('format')}' richiede un solo media, ne ho {n}.")
    ig_id = mc.ig_user_id()
    if not ig_id:
        raise ValueError("META_IG_USER_ID non configurato")
    caption = post.get("caption", "")
    if dry_run:
        return {"dry_run": True, "kind": kind, "media": n, "da_caricare": bool(files and not urls),
                "media_url": (urls or [None])[0], "caption": caption[:120]}
    if not urls:
        raise RuntimeError("Media non ancora caricato: manca media_urls (ensure_media non eseguito)")
    if kind == "CAROUSEL":
        children = []
        for url in urls:
            cid = mc.post_json(f"{ig_id}/media", {"image_url": url, "is_carousel_item": "true"}).get("id")
            if not cid:
                raise RuntimeError("Meta non ha restituito l'ID di una slide del carosello")
            children.append(cid)
        container = mc.post_json(f"{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(children),
                                                    "caption": caption}).get("id")
    elif kind == "REELS":
        container = mc.post_json(f"{ig_id}/media", {"caption": caption, "media_type": "REELS", "video_url": urls[0]}).get("id")
    else:
        container = mc.post_json(f"{ig_id}/media", {"caption": caption, "image_url": urls[0]}).get("id")
    if not container:
        raise RuntimeError("Meta non ha restituito l'ID del contenitore media")
    post["provider_container_id"] = container
    if kind in ("REELS", "CAROUSEL"):  # video e caroselli vanno elaborati prima della pubblicazione
        wait_finished(container, sleep, "video" if kind == "REELS" else "carosello")
    try:
        media_id = mc.post_json(f"{ig_id}/media_publish", {"creation_id": container}).get("id")
    except Exception as exc:
        raise PublishUncertain(f"Esito di media_publish incerto, controlla il profilo prima di riprovare: {mc.redact(exc)}") from None
    if not media_id:
        raise PublishUncertain("media_publish senza ID: controlla il profilo Instagram prima di riprovare")
    try:
        link = mc.get_json(media_id, {"fields": "permalink"}).get("permalink")
    except Exception:
        link = None
    return {"provider_id": media_id, "status": "published", "permalink": link}


def process(queue, live, now=None, sleep=time.sleep, save=None):
    """Elabora la coda. In live chiama save() dopo ogni post, così un crash non causa doppie pubblicazioni."""
    now = now or datetime.now(timezone.utc)
    changed = False
    for post in queue.get("posts", []):
        if post.get("status") != "pending":
            continue
        try:
            due = datetime.fromisoformat(post["due_at"].replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
        except Exception:
            post["status"], post["last_error"] = "needs_review", "due_at non valido"
            changed = True
            continue
        if due > now:
            continue
        platform, caption = post.get("platform"), post.get("caption", "")
        try:
            if platform == "instagram":
                if live:
                    publish_instagram(post, True)  # solo controlli: se il post non è valido non carichiamo niente
                    if ensure_media(post) and save:
                        save()  # salva subito gli URL: se la pubblicazione fallisce non si ricarica tutto
                result = publish_instagram(post, not live, sleep)
            elif platform == "tiktok":
                if post.get("media_url"):
                    raise ValueError("TikTok con media non supportato dal publisher.")
                result = publish_buffer_text(os.getenv("BUFFER_CHANNEL_TIKTOK"), caption, not live)
            elif platform == "reddit":
                out = Path("output")
                out.mkdir(exist_ok=True)
                path = out / f"reddit-{post['id']}.md"
                path.write_text(f"# {post.get('reddit_title', post.get('title', 'Central DevLog'))}\n\n{caption}\n", encoding="utf-8")
                post["status"], post["provider_id"] = "needs_review", None
                post["last_error"] = "Reddit manual fallback creato; pubblicazione API non configurata."
                print(f"Reddit pronto per revisione: {path}")
                changed = True
                continue
            else:
                raise ValueError(f"Piattaforma sconosciuta: {platform}")
            if live:
                post["provider_id"] = result.get("provider_id")
                post["provider_status"] = result.get("status")
                post["published_url"] = result.get("permalink")
                post["status"] = "published" if platform == "instagram" else "scheduled"
            else:
                print("DRY-RUN:", json.dumps({"id": post["id"], "platform": platform, "result": result}, ensure_ascii=False))
            post["attempts"] = int(post.get("attempts", 0)) + 1
            post["last_error"] = None
            changed = True
            if live and save:
                save()
        except (ValueError, PublishUncertain) as exc:
            # Errori che un nuovo tentativo automatico non risolve: serve una persona.
            post["attempts"] = int(post.get("attempts", 0)) + 1
            post["status"], post["last_error"] = "needs_review", mc.redact(exc)[:1000]
            changed = True
            print(f"Da rivedere {post.get('id')}: {post['last_error']}", file=sys.stderr)
            if live and save:
                save()
        except Exception as exc:
            post["attempts"] = int(post.get("attempts", 0)) + 1
            post["last_error"] = mc.redact(exc)[:1000]
            post["status"] = "failed" if post["attempts"] >= 3 else "pending"
            changed = True
            print(f"Errore {post.get('id')}: {post['last_error']}", file=sys.stderr)
            if live and save:
                save()
    return changed


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def apply_state(queue, state):
    for post in queue.get("posts", []):
        if post.get("id") in state:
            post.update(state[post["id"]])


def collect_state(queue, state):
    """Aggiunge allo stato i post che il publisher ha toccato o chiuso."""
    for post in queue.get("posts", []):
        if post.get("status") not in ("proposed", "pending") or post.get("attempts") or post.get("last_error"):
            state[post["id"]] = {k: post.get(k) for k in STATE_FIELDS if post.get(k) is not None}
    return state


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = ap.parse_args()
    live = args.live
    if live and os.getenv("ALLOW_LIVE_PUBLISH", "false").lower() != "true":
        raise SystemExit("Bloccato: impostare ALLOW_LIVE_PUBLISH=true dopo il collaudo.")
    if not QUEUE.exists():
        raise SystemExit(f"Coda non trovata: {QUEUE}")
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    state = load_state()
    apply_state(queue, state)

    def save():
        STATE.write_text(json.dumps(collect_state(queue, state), indent=2, ensure_ascii=False), encoding="utf-8")

    changed = process(queue, live, save=save)
    if changed and live:
        save()
    elif changed:
        print("Nessuno stato persistito in dry-run.")


if __name__ == "__main__":
    main()
