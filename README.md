# IMAGE OF THE DAY

Ogni notte a mezzanotte (ora di Roma) guarda le prime pagine del giorno appena chiuso (~60 quotidiani dalla rassegna del Post), sceglie una foto, la ritaglia dalla pagina e la pubblica con la data.

## Messa online (una volta, ~5 minuti)

1. Crea un repository su GitHub e carica tutti i file di questa cartella (inclusa `.github`).
2. **Settings → Secrets and variables → Actions → New repository secret**: nome `ANTHROPIC_API_KEY`, valore la tua chiave da console.anthropic.com.
3. **Settings → Pages → Source: GitHub Actions**.
4. **Actions → image of the day → Run workflow**, nel campo scrivi
   `2026-09-27,2026-09-28,2026-09-29`
   → è la prova con i tre giorni passati. Dopo 2–3 minuti il sito è su `https://<tuo-utente>.github.io/<repo>/`.

Da lì in poi gira da solo ogni mezzanotte.

## Note

- Una foto che non ti piace: cancella la riga in `days.json` e il file in `images/`, poi rilancia il workflow con quella data.
- Costo API: pochi centesimi al giorno.
- Il modello si cambia con la variabile `MODEL` (default `claude-sonnet-5-5`).
