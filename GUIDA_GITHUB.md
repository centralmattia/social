# Guida GitHub Actions (una volta sola)

## 1. Repository privato
1. Su github.com: **New repository**, nome `central-social-autopilot`, **Private**. Non aggiungere README.
2. Nella cartella del progetto (PowerShell):
```
git init
git add .
git status
```
Controlla nell'elenco che **NON** compaiano `.env`, `data/` e `output/`. Se compaiono, fermati e dimmelo.
```
git commit -m "Piano C"
git branch -M main
git remote add origin https://github.com/TUO_UTENTE/central-social-autopilot.git
git push -u origin main
```

## 2. Secret e variabili
Repository -> **Settings -> Secrets and variables -> Actions**.
- Scheda **Secrets**, *New repository secret*: `META_ACCESS_TOKEN` = il token Instagram.
- Scheda **Variables**, *New repository variable*:
  - `META_IG_USER_ID` = l'`user_id` del tuo account
  - `META_GRAPH_API_VERSION` = `v24.0`
  - `ALLOW_LIVE_PUBLISH` = `false` (per ora)
Non servono le chiavi Scaleway su GitHub.

## 3. Prova senza pubblicare
**Actions -> Central Social Autopilot -> Run workflow -> dry-run**. Deve finire verde e scrivere nel log cosa farebbe.

## 4. Primo test live con un solo post
1. Prepara e approva UN post con orario vicino, `upload_media.py`, commit e push.
2. Imposta `ALLOW_LIVE_PUBLISH` = `true`.
3. Aspetta il controllo successivo (fino a 30 minuti, più eventuale ritardo di GitHub) o lancia **Run workflow -> live**.
4. Controlla il profilo Instagram e `publish_state.json` (dopo `git pull`).
5. Se qualcosa va male, riporta `ALLOW_LIVE_PUBLISH` a `false`.

## Costi e limiti
Con 32 controlli al giorno ogni run dura circa 1 minuto fatturato: ~1.000 minuti al mese. Per i repository privati il piano gratuito GitHub include (per quanto ne so) 2.000 minuti al mese: verifica nelle tue impostazioni di fatturazione. Per ridurre i consumi restringi l'orario del `cron` in `.github/workflows/social-autopilot.yml`.
