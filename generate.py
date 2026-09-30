#!/usr/bin/env python3
"""IMAGE OF THE DAY
Attraversa le prime pagine del giorno (la rassegna quotidiana del Post, ~60 quotidiani),
chiede a Claude di scegliere LA foto del giorno, la ritaglia dalla pagina e la aggiunge a days.json.

Uso:
  python generate.py                                   # il giorno appena finito (ora di Roma)
  python generate.py 2026-09-27,2026-09-28,2026-09-29  # giorni specifici (prova / recupero)
"""
import base64
import datetime as dt
import io
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import anthropic
import requests
from PIL import Image

ROME = ZoneInfo("Europe/Rome")
MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "days.json")
IMAGES = os.path.join(HERE, "images")
UA = {"User-Agent": "Mozilla/5.0 (compatible; image-of-the-day)"}
CDN = "https://static-prod.cdnilpost.com/wp-content/uploads/"
MAX_PAGES = 90

PICK = """Queste sono le prime pagine dei quotidiani del {date}, numerate.
Scegli LA foto del giorno: una sola fotografia di cronaca stampata su una di queste prime pagine,
la più forte come immagine e la più rappresentativa della giornata
(se la stessa foto, o lo stesso soggetto, ricorre su più giornali è un buon segno).
Escludi pubblicità, testate, infografiche, illustrazioni, vignette, fototessere e foto piccole:
serve una fotografia vera, abbastanza grande da reggere a tutto schermo.
Rispondi solo con JSON: {{"page": <numero>, "photo": "<descrizione breve della foto e della sua posizione nella pagina>"}}"""

CROP = """Questa è la prima pagina di {paper}. Individua questa fotografia: {photo}
Dammi il riquadro stretto della sola fotografia, senza titoli, didascalie, cornici o testo,
in pixel su questa immagine ({w}x{h}, origine in alto a sinistra).
Rispondi solo con JSON: {{"x0": .., "y0": .., "x1": .., "y1": ..}}"""

client = anthropic.Anthropic()


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
    """URL della rassegna 'Le prime pagine di oggi' del Post per quel giorno."""
    ymd = day.strftime("%Y/%m/%d")
    pat = re.compile(r"https://www\.ilpost\.it/%s/le-prime-pagine-di-oggi[\w-]*/" % ymd)
    listing = ["https://www.ilpost.it/tag/prime-pagine/"] + [
        f"https://www.ilpost.it/tag/prime-pagine/page/{n}/" for n in range(2, 8)
    ]
    for url in listing:
        r = get(url)
        if r is None:
            continue
        m = pat.search(r.text)
        if m:
            return m.group(0)
    return None


def paper_name(filename):
    s = re.sub(r"^\d+-", "", filename.rsplit(".", 1)[0])
    s = re.sub(r"^\d+_", "", s)
    return s.replace("_", " ").strip()


def front_pages(page_url, day):
    r = get(page_url)
    if r is None:
        return []
    ymd = day.strftime("%Y/%m/%d")
    base = CDN + ymd + "/"
    names = re.findall(re.escape(base) + r"(?:\d+x\d+/)?([\w.-]+\.(?:jpe?g|png))", r.text)
    seen, out = set(), []
    for n in names:
        if n in seen or "apertura" in n:
            continue
        seen.add(n)
        out.append((paper_name(n), base + n))  # senza cartella di formato = immagine originale
    return out


def load_image(url):
    r = get(url)
    if r is None:
        return None
    try:
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None


def encode(img, longest, quality):
    im = img.copy()
    im.thumbnail((longest, longest))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    block = {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(buf.getvalue()).decode()}}
    return block, im.size


def ask(content):
    msg = client.messages.create(model=MODEL, max_tokens=800,
                                 messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("risposta non JSON: " + text[:200])
    return json.loads(m.group(0))


def process(day):
    page = find_page(day)
    if not page:
        print(f"{day}: rassegna del Post non trovata")
        return None

    pages = []
    for paper, url in front_pages(page, day)[:MAX_PAGES]:
        img = load_image(url)
        if img:
            pages.append((paper, img))
    if not pages:
        print(f"{day}: nessuna prima pagina scaricata")
        return None
    print(f"{day}: {len(pages)} prime pagine")

    # 1. scelta della foto, guardando tutte le pagine
    content = [{"type": "text", "text": PICK.format(date=day.strftime("%d.%m.%Y"))}]
    for i, (paper, img) in enumerate(pages, 1):
        block, _ = encode(img, 900, 70)
        content += [{"type": "text", "text": f"Pagina {i}: {paper}"}, block]
    choice = ask(content)
    paper, img = pages[int(choice["page"]) - 1]
    print(f"{day}: {paper} — {choice.get('photo', '')}")

    # 2. ritaglio preciso sulla pagina scelta
    block, (w, h) = encode(img, 1568, 90)
    box = ask([block, {"type": "text", "text": CROP.format(paper=paper, photo=choice.get("photo", ""), w=w, h=h)}])
    sx, sy = img.width / w, img.height / h
    x0, y0 = max(0, int(box["x0"] * sx)), max(0, int(box["y0"] * sy))
    x1, y1 = min(img.width, int(box["x1"] * sx)), min(img.height, int(box["y1"] * sy))
    if x1 - x0 < 80 or y1 - y0 < 80:
        raise ValueError(f"ritaglio troppo piccolo: {box}")
    crop = img.crop((x0, y0, x1, y1))

    os.makedirs(IMAGES, exist_ok=True)
    name = f"{day.isoformat()}.jpg"
    crop.save(os.path.join(IMAGES, name), "JPEG", quality=90)
    return {"date": day.isoformat(), "src": f"images/{name}", "w": crop.width, "h": crop.height,
            "paper": paper, "note": choice.get("photo", "")}


def main():
    now = dt.datetime.now(ROME)
    # le esecuzioni programmate girano a mezzanotte di Roma (due orari UTC per coprire l'ora legale)
    if os.environ.get("SCHEDULED") and now.hour > 1:
        print("non è mezzanotte a Roma: salto")
        return

    arg = ",".join(sys.argv[1:]).strip()
    if arg:
        days = [dt.date.fromisoformat(s.strip()) for s in arg.split(",") if s.strip()]
    else:
        days = [now.date() - dt.timedelta(days=1)]  # il giorno appena chiuso

    entries = []
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            entries = json.load(f)
    done = {e["date"] for e in entries}

    for day in days:
        if day.isoformat() in done:
            print(f"{day}: già pubblicata")
            continue
        try:
            entry = process(day)
        except Exception as ex:
            print(f"{day}: errore — {ex}")
            entry = None
        if entry:
            entries.append(entry)

    entries.sort(key=lambda e: e["date"])
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
