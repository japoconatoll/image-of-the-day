#!/usr/bin/env python3
"""IMAGE OF THE DAY (versione gratuita, senza AI)
Attraversa le prime pagine del giorno (la rassegna quotidiana del Post, ~60 quotidiani),
trova le fotografie stampate su ogni pagina e sceglie la foto del giorno con una regola:
  1. la foto che compare sul maggior numero di prime pagine diverse;
  2. a parità, la più grande.
La ritaglia dalla pagina e la aggiunge a days.json.

Uso:
  python generate.py                                   # ieri, più i giorni mancanti dell'ultima settimana
  python generate.py 2026-09-27,2026-09-28,2026-09-29  # giorni specifici (prova / recupero)
  python generate.py 2026-09-28:2                      # rifà quel giorno con la seconda scelta
"""
import datetime as dt
import io
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

ROME = ZoneInfo("Europe/Rome")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "days.json")
IMAGES = os.path.join(HERE, "images")
UA = {"User-Agent": "Mozilla/5.0 (compatible; image-of-the-day)"}
CDN = "https://static-prod.cdnilpost.com/wp-content/uploads/"
SKIP_PAPERS = ("sport",)          # i quotidiani sportivi vincerebbero sempre per dimensione
MIN_AREA = 0.04                   # la foto deve occupare almeno il 4% della pagina
CATCH_UP = 7                      # giorni all'indietro recuperati se un'esecuzione è saltata


# ---------- download ----------

def get(url):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 + attempt * 3)
    return None


def find_page(day):
    ymd = day.strftime("%Y/%m/%d")
    pat = re.compile(r"https://www\.ilpost\.it/%s/le-prime-pagine-di-oggi[\w-]*/" % ymd)
    listing = ["https://www.ilpost.it/tag/prime-pagine/"] + [
        f"https://www.ilpost.it/tag/prime-pagine/page/{n}/" for n in range(2, 8)
    ]
    for url in listing:
        r = get(url)
        if r is not None:
            m = pat.search(r.text)
            if m:
                return m.group(0)
    return None


def paper_name(filename):
    s = filename.rsplit(".", 1)[0]
    s = re.sub(r"^\d+[-_]", "", s)              # timestamp del caricamento
    s = re.sub(r"^\d+[-_]", "", s)              # numero d'ordine (1_, 001-)
    s = re.sub(r"[-_]\d{6}[a-z0-9]{6,}$", "", s)  # codice finale casuale
    return s.replace("_", " ").replace("-", " ").strip()


def front_pages(page_url, day):
    r = get(page_url)
    if r is None:
        return []
    base = CDN + day.strftime("%Y/%m/%d") + "/"
    names = re.findall(re.escape(base) + r"(?:\d+x\d+/)?([\w.-]+\.(?:jpe?g|png|webp))", r.text)
    seen, out = set(), []
    for n in names:
        if n in seen or "apertura" in n:
            continue
        seen.add(n)
        out.append((paper_name(n), base + n))
    return out


def load_image(url):
    r = get(url)
    if r is None:
        return None
    try:
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None


# ---------- riconoscimento delle foto sulla pagina ----------

def entropy(gray):
    hist, _ = np.histogram(gray, bins=32, range=(0, 255))
    p = hist[hist > 0] / hist.sum()
    return float(-(p * np.log2(p)).sum())


def flat_share(gray, b=6):
    """Quota di tasselli a tinta unita: alta nelle pubblicità (fondi, loghi), bassa nelle foto stampate."""
    hh, ww = (gray.shape[0] // b) * b, (gray.shape[1] // b) * b
    if hh == 0 or ww == 0:
        return 1.0
    blocks = gray[:hh, :ww].reshape(hh // b, b, ww // b, b).swapaxes(1, 2).reshape(-1, b * b)
    return float((blocks.std(axis=1) < 3).mean())


def photo_regions(img):
    """Riquadri della pagina che sembrano fotografie: zone piene, senza carta bianca, con molti toni."""
    W = 400
    scale = img.width / W
    small = np.asarray(img.resize((W, max(1, round(img.height / scale))))).astype(np.int16)
    h, w, _ = small.shape

    edge = np.concatenate([small[:6].reshape(-1, 3), small[-6:].reshape(-1, 3),
                           small[:, :6].reshape(-1, 3), small[:, -6:].reshape(-1, 3)])
    paper = np.median(edge, axis=0)                                  # colore della carta
    ink = np.abs(small - paper).sum(axis=2) > 60                     # tutto ciò che non è carta
    dense = ndimage.uniform_filter(ink.astype(float), size=7) > 0.9  # il testo lascia buchi, le foto no
    dense = ndimage.binary_opening(dense, iterations=2)
    dense = ndimage.binary_closing(dense, iterations=2)

    labels, _ = ndimage.label(dense)
    out = []
    for i, sl in enumerate(ndimage.find_objects(labels), 1):
        ys, xs = sl
        bh, bw = ys.stop - ys.start, xs.stop - xs.start
        area = bw * bh / (w * h)
        if area < MIN_AREA or not (0.35 < bw / bh < 3.5):
            continue
        if (labels[sl] == i).mean() < 0.85:                          # deve essere un rettangolo pieno
            continue
        gray = small[sl].mean(axis=2)
        if entropy(gray) < 2.5:                                        # scritte e fasce di colore: pochi toni
            continue
        strict = (ys.start / h <= 0.72 and ys.stop / h >= 0.2         # non nella fascia pubblicitaria
                  and flat_share(gray) <= 0.4)                        # fondi piatti = grafica/pubblicità
        # rientra dell'1.5% per non prendere cornici e filetti
        dx, dy = int(bw * 0.015), int(bh * 0.015)
        box = (int((xs.start + dx) * scale), int((ys.start + dy) * scale),
               int((xs.stop - dx) * scale), int((ys.stop - dy) * scale))
        out.append((box, area, strict))
    return out


def dhash(img, size=12):
    g = np.asarray(img.convert("L").resize((size + 1, size)), dtype=np.int16)
    return (g[:, 1:] > g[:, :-1]).flatten()


# ---------- scelta ----------

GIORNALONE = "https://www.giornalone.it/"


def giornalone_pages(day):
    """Riserva per i giorni in cui il Post non pubblica la rassegna: i quotidiani nazionali di giornalone.it.
    Restituisce (testata, url immagine) solo per le edizioni di quel giorno (la data è nel percorso dell'immagine)."""
    r = get(GIORNALONE + "quotidiani-italiani/")
    if r is None:
        return []
    slugs = []
    for slug in re.findall(r"giornalone\.it/prima-pagina-([a-z0-9-]+)/", r.text):
        if slug not in slugs:
            slugs.append(slug)
    ymd = day.strftime("%Y/%m/%d")
    out = []
    for slug in slugs[:40]:
        page = get(f"{GIORNALONE}prima-pagina-{slug}/?d={day.isoformat()}")
        time.sleep(0.5)
        if page is None:
            continue
        m = re.search(r"(?:https://www\.giornalone\.it)?/g/%s/%s-[\w-]+\.(?:webp|jpe?g|png)" % (ymd, re.escape(slug)), page.text)
        if m:
            url = m.group(0)
            out.append((slug.replace("-", " "), url if url.startswith("http") else "https://www.giornalone.it" + url))
    return out


def process(day, rank=1):
    page = find_page(day)
    if page:
        sources = front_pages(page, day)
        if not sources:
            print(f"{day}: rassegna del Post trovata ma senza immagini leggibili, provo giornalone")
            sources = giornalone_pages(day)
    else:
        print(f"{day}: il Post non ha pubblicato la rassegna, provo giornalone")
        sources = giornalone_pages(day)
    if not sources:
        print(f"{day}: nessuna prima pagina disponibile")
        return None

    cands = []  # (paper, crop, area, hash, strict)
    n_pages = 0
    for paper, url in sources:
        if any(k in paper for k in SKIP_PAPERS):
            continue
        img = load_image(url)
        if img is None:
            continue
        n_pages += 1
        for box, area, strict in photo_regions(img):
            crop = img.crop(box)
            cands.append((paper, crop, area, dhash(crop), strict))
    good = [c for c in cands if c[4]]
    print(f"{day}: {n_pages} prime pagine, {len(good)} foto sicure, {len(cands) - len(good)} incerte")
    cands = good or cands          # se i filtri scartano tutto, meglio una foto incerta che nessuna
    if not cands:
        return None

    # quante ALTRE testate stampano la stessa foto
    def repeats(i):
        papers = {cands[j][0] for j in range(len(cands))
                  if j != i and cands[j][0] != cands[i][0]
                  and np.count_nonzero(cands[i][3] != cands[j][3]) <= 30}
        return len(papers)

    scored = sorted(((repeats(i), c[2], i) for i, c in enumerate(cands)), reverse=True)
    picked = []  # una sola voce per foto: le copie della stessa immagine su altre testate non contano come alternative
    for rep, _, i in scored:
        if all(np.count_nonzero(cands[i][3] != cands[j][3]) > 30 for _, j in picked):
            picked.append((rep, i))
    rep, best = picked[min(rank, len(picked)) - 1]
    paper, crop, area = cands[best][:3]
    print(f"{day}: scelta da {paper} (su {rep + 1} prime pagine, {area:.0%} della pagina)")

    os.makedirs(IMAGES, exist_ok=True)
    name = f"{day.isoformat()}.jpg"
    crop.save(os.path.join(IMAGES, name), "JPEG", quality=90)
    return {"date": day.isoformat(), "src": f"images/{name}", "w": crop.width, "h": crop.height,
            "paper": paper, "pages": rep + 1}


def main():
    now = dt.datetime.now(ROME)

    # "2026-09-28" = genera se manca; "2026-09-28:1" = rigenera; "2026-09-28:2" = seconda scelta, ecc.
    arg = ",".join(sys.argv[1:]).strip()
    jobs = []
    for item in (arg.split(",") if arg else []):
        item = item.strip()
        if not item:
            continue
        date_s, _, rank_s = item.partition(":")
        jobs.append((dt.date.fromisoformat(date_s), int(rank_s) if rank_s else None))
    if not jobs:  # esecuzione automatica: tutti i giorni mancanti dell'ultima settimana, fino a ieri
        yesterday = now.date() - dt.timedelta(days=1)
        jobs = [(yesterday - dt.timedelta(days=k), None) for k in range(CATCH_UP - 1, -1, -1)]

    entries = []
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            entries = json.load(f)
    done = {e["date"] for e in entries}

    for day, rank in jobs:
        if rank is None and day.isoformat() in done:
            print(f"{day}: già pubblicata")
            continue
        try:
            entry = process(day, rank or 1)
        except Exception as ex:
            print(f"{day}: errore — {ex}")
            entry = None
        if entry:
            entries = [e for e in entries if e["date"] != entry["date"]] + [entry]

    entries.sort(key=lambda e: e["date"])
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
