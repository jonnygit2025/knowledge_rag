# Knowledge RAG

Applicazione personale per archiviare trascrizioni YouTube e fonti collegate, cercare nei contenuti e consultarne i grafi con evidenze. PostgreSQL conserva i dati canonici; Qdrant contiene un indice ricostruibile. Prima versione locale, con demo completa senza chiavi API.

## Avvio rapido su Windows / PowerShell

Prerequisiti: Git e Docker Desktop avviato con container Linux e Docker Compose v2. Non serve installare Python sul PC per avviare l’app via Docker.

```powershell
git clone https://github.com/jonnygit2025/knowledge_rag.git
cd knowledge_rag
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
docker compose up -d --build
docker compose logs -f init worker
```

Dopo la migrazione e l’indicizzazione dei tre documenti sintetici, aprire **http://localhost:8501**. `Ctrl+C` interrompe solo la visualizzazione dei log. La demo è marcata `DEMO`, usa URL di esempio e non rappresenta video reali.

Provare:

1. **Archivio:** testo originale, testo normalizzato, versioni, segmenti temporali e Markdown scaricabile.
2. **Grafo:** selezionare il primo documento; ogni relazione mostra citazione, `document_id`, `version_id` e `chunk_id`.
3. **Chat:** chiedere «Come vengono usati PostgreSQL e Qdrant?». Senza LLM vengono restituiti passaggi citati; non una risposta generata.
4. **Elaborazioni:** verificare gli eventi dell’outbox con stato `done`.
5. Riavviare `docker compose restart worker ui`: archivio e indici devono persistere.

Il primo build scarica dipendenze e immagini. Gli export si trovano in `data/exports`; gli originali e gli audit restano nel database. Con la configurazione iniziale non vengono fatte chiamate YouTube o provider a pagamento. Il worker si avvia separatamente dalla UI: un refresh Streamlit non crea aggiornamenti automatici duplicati.

## Aggiornare il codice già clonato

```powershell
git pull --ff-only origin main
docker compose up -d --build
```

L’inizializzazione applica le migrazioni pendenti. Configurazione e dati locali non sono in Git. `docker compose down` arresta i servizi mantenendo i volumi. Non usare `down -v` durante aggiornamenti normali: rimuove i dati persistenti. Un clone/pull su un altro PC trasferisce il codice, non i database.

## Canali YouTube reali

Nel file `.env` configurare `YOUTUBE_API_KEY`, ottenuta da un progetto Google con **YouTube Data API v3** abilitata. In `config.yaml` inserire canali realmente scelti dall’utente:

```yaml
channels:
  - reference: "@handle_del_canale"
    enabled: true
languages: [it, en]
initial_days: 30
initial_max_videos: 10
include_live: false
fetch_external: false
max_external_links: 5
glossary: {}
```

Sono supportati `@handle` e ID `UC...`. Al primo aggiornamento vengono acquisiti al massimo 10 video degli ultimi 30 giorni; usare `initial_max_videos: 0` per eliminare il limite numerico. Gli aggiornamenti successivi scorrono la playlist degli upload con paginazione, confrontano gli ID nel database e coprono anche un periodo offline più lungo della finestra iniziale. La finestra resta configurabile. Shorts inclusi; live e anteprime ancora in corso esclusi salvo `include_live: true`.

Dopo una modifica a `.env`:

```powershell
docker compose up -d --force-recreate worker ui
docker compose exec worker knowledge-rag sync
```

Il worker aggiorna i canali al suo avvio e, per default, ogni 60 minuti mentre rimane in esecuzione. Disabilitare con `STARTUP_SYNC=false`. Il pulsante **Aggiorna canali** e la CLI condividono una deduplicazione per intervallo: un job già completato nello stesso intervallo viene riutilizzato. Per un nuovo tentativo usare **Elaborazioni → Ritenta** oppure `knowledge-rag retry ID_JOB`.

YouTube Premium non sblocca l’accesso API alle trascrizioni. L’adapter `youtube-transcript-api` è non ufficiale e può subire blocchi. Gli stati `no_transcript`, `disabled`, `blocked`, `unavailable` e `temporary_error` restano distinti. I primi quattro richiedono un intervento/retry esplicito; gli errori temporanei usano backoff. Nessun testo viene inventato.

Per sostituire un testo mancante, usare **Archivio → Importa** e inserire l’ID del video nel campo apposito, oppure:

```powershell
# Copiare prima il file in data/imports sul PC.
docker compose exec worker knowledge-rag import /app/data/imports/sottotitoli.srt --youtube-video-id ID_VIDEO
```

Se il video è già stato scoperto, l’import ne conserva ID interno, titolo, canale e data di pubblicazione. Supportati TXT, MD, SRT, VTT e JSON strutturato; massimo 5 MB. Il nome del file è l’identità predefinita per import manuali. `--source-key manual:mia-fonte` permette di mantenerla esplicita. Il JSON di `tests/fixtures/demo.json` documenta il formato: ogni import JSON contiene **un oggetto documento**, mentre la fixture demo è un elenco caricato dal comando `demo`.

## Fonti esterne e correzioni

Gli URL nelle descrizioni sono sempre conservati come riferimenti. Con `fetch_external: true` il worker acquisisce al massimo `max_external_links` fonti per video. Repository GitHub in forma `https://github.com/owner/repo`: viene letto il README alla SHA risolta e vengono conservati path e commit. Altre pagine: HTML leggibile tramite Trafilatura, testo o Markdown, massimo 1 MB. Nessun crawling ricorsivo e nessuna autenticazione a repository privati nella prima versione.

Redirect e indirizzi DNS sono validati; la richiesta usa l’IP pubblico validato, con Host/TLS SNI originali. Query URL conservate, frammenti rimossi. Un errore non elimina il riferimento. Contenuti acquisiti e loro istruzioni sono trattati come dati, senza accesso a strumenti.

La normalizzazione è deterministica. Le correzioni del glossario sono sostituzioni esatte, approvate tramite `config.yaml`, e conservano audit per segmento. Il glossario iniziale è vuoto. **Non sono implementate correzioni LLM automatiche**, né estrazione LLM libera di relazioni: i grafi live contengono menzioni di un piccolo catalogo con identità esplicite; gli import strutturati possono aggiungere relazioni con citazioni testuali validate. Una revisione del glossario/chunker crea una nuova versione quando si riacquisisce/reimporta la fonte.

## Ricerca semantica e generazione opzionali

La demo usa feature hashing deterministico a 256 dimensioni: serve a verificare indicizzazione, filtri e citazioni. **Non equivale a un modello semantico multilingue**.

Per un endpoint OpenAI-compatible, modificare `.env`:

```dotenv
EMBEDDING_PROVIDER=openai-compatible
PROVIDER_BASE_URL=https://api.openai.com/v1
PROVIDER_API_KEY=chiave_locale
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
MAX_PAID_CALLS_PER_DAY=20
# Opzionale: nome di un modello chat disponibile sul proprio provider.
LLM_MODEL=
```

Il provider deve supportare `/embeddings`, il parametro `dimensions` e, se abilitato, `/chat/completions`. Sono esempi di configurazione, non integrazioni live già collaudate sul tuo account. Il limite predefinito è **zero chiamate a pagamento**; quello sopra è un limite di chiamate, non di euro. Vengono conservati cache, stato delle richieste e token dichiarati dal provider. Errori e richieste dall’esito incerto non vengono ritentati automaticamente: controllare `provider_calls` prima di intervenire sul relativo record.

Cambiare modello, endpoint, dimensione o chunker crea un namespace separato. Riavviare i servizi e richiedere la reindicizzazione dei documenti attivi:

```powershell
docker compose up -d --force-recreate worker ui
docker compose exec worker knowledge-rag reconcile
```

La chat rilegge testo e versione attiva da PostgreSQL dopo il recupero Qdrant; scarta documenti ritirati e versioni precedenti. Applica filtri per canale, tag e data. Con un LLM valida anche i numeri delle citazioni, ma questo controllo non garantisce da solo che ogni frase sia corretta: i passaggi sono sempre mostrati per la verifica.

## CLI e recupero degli errori

```powershell
docker compose exec worker knowledge-rag status
docker compose exec worker knowledge-rag ask "Come funziona l'outbox?"
docker compose exec worker knowledge-rag export
docker compose exec worker knowledge-rag retry ID_JOB
docker compose exec worker knowledge-rag retry-index ID_EVENTO
docker compose exec worker knowledge-rag reconcile
docker compose exec worker knowledge-rag withdraw ID_DOCUMENTO
```

Acquisizione e indicizzazione hanno stati separati. Un guasto Qdrant lascia gli originali salvati. Il worker aggiorna l’outbox in modo idempotente e ritenta gli errori con backoff fino al limite. Job interrotti vengono ripresi alla scadenza del lease; heartbeat e token impediscono a un vecchio worker di confermare un job assegnato a un altro. Le versioni precedenti e le evidenze rimangono nell’archivio, ma il loro contributo viene ritirato dalla ricerca corrente.

`reconcile` confronta gli ID attesi con i punti presenti nel namespace corrente e accoda i punti mancanti; inoltre ritira le proiezioni note di versioni inattive. Le vecchie collezioni di modelli diversi sono conservate e vanno eventualmente gestite separatamente, dopo averne verificato l’uso. Il ritiro di un documento non elimina le evidenze di altre fonti sulla stessa relazione.

## Sviluppo e test

Con Python 3.12/3.13 e uv:

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Senza server i test rapidi usano SQLite **solo come doppio di test** e Qdrant locale in memoria. L’applicazione e le migrazioni richiedono PostgreSQL. I test PostgreSQL sono saltati se `TEST_DATABASE_URL` non è impostato.

Per integrazione, creare un **database di test separato**:

```powershell
docker compose exec postgres createdb -U knowledge knowledge_test
$env:TEST_DATABASE_URL="postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge_test"
$env:TEST_QDRANT_URL="http://localhost:6333"
uv run pytest -q
```

Adattare la password se modificata. Ogni test PostgreSQL crea e rimuove uno schema isolato. La CI verifica anche migrazioni, immutabilità delle revisioni, Qdrant server, build Docker e salute Streamlit. I test YouTube e provider usano fixture e HTTP simulato; il collaudo sui canali reali e sulle chiavi dell’utente resta separato.

## Backup

PostgreSQL contiene originali, configurazioni di elaborazione, versioni ed evidenze: è il backup indispensabile. Con Python sul PC:

```powershell
python scripts/backup.py
```

Il dump binario viene scritto in `backups/` senza passare dalla redirezione testuale PowerShell. Salvare anche `.env` e `config.yaml` in un luogo privato. Per provare il ripristino in un **database nuovo**, senza sovrascrivere quello in uso:

```powershell
python scripts/restore.py backups/NOME_BACKUP.dump --database knowledge_restore
```

Per snapshot Qdrant vedere la documentazione ufficiale nelle note tecniche. In alternativa Qdrant è ricostruibile dal database con `reconcile`; un modello embedding a pagamento può richiedere chiamate aggiuntive se manca la cache.

## Stato e documentazione

- Implementati: base locale, acquisizione/import, versionamento e audit, outbox, Qdrant, grafi SQL con evidenze, UI e retrieval citato; provider esterni configurabili.
- **LightRAG non integrato.** È implementata l’alternativa prevista dalla specifica: Qdrant con espansione del grafo canonico SQL. La prova di compatibilità LightRAG e il mapping dei suoi storage restano un incremento separato; non viene richiesta Apache AGE.
- Ancora da collaudare localmente con l’utente: canali reali, blocchi trascrizioni, provider embedding/chat e costi effettivi.
- Incrementi successivi: patch LLM con revisione, classificazioni/riassunti, tassonomie normalizzate, file documentali GitHub selezionati, LightRAG, hosting e autenticazione remota.

La UI è pensata per uso locale; le porte pubblicate sono limitate a `127.0.0.1`. Codice in `src/knowledge_rag`, schema/migrazioni in `migrations`, fixture e test in `tests`. La specifica originale è in [docs/SPECIFICA.md](docs/SPECIFICA.md); decisioni e query dello schema implementato in [docs/ARCHITETTURA.md](docs/ARCHITETTURA.md).
