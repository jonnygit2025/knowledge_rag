# Architettura implementata — v0.1

La richiesta operativa dell’utente autorizza lavoro e commit direttamente su `main`, sostituendo la proposta di branch/PR presente nella specifica originale. Nessun deployment remoto è previsto.

## Autorità, identità e versioni

Ogni tabella canonica ha una PK UUID `id`; i riferimenti usano `document_id`, `version_id`, `chunk_id`, `graph_id`. Gli ID YouTube sono separati. Date PostgreSQL `timestamptz`, UTC; UI Europe/Rome. I dati flessibili sono JSONB. Documenti, versioni e grafi sono distinti e le entità hanno una chiave esplicita (URL o identità concettuale), senza fusione fuzzy.

L’hash di revisione include segmenti, testo importato, metadati, asserzioni strutturate e configurazione di elaborazione. Le versioni sono immutabili; una fonte tornata a un contenuto già acquisito riattiva la revisione esistente. Un aggiornamento identico aggiorna `last_checked_at` e non duplica la versione. PostgreSQL rifiuta modifiche dirette a versioni, segmenti, chunk, audit e grafo; verifica che la versione attiva appartenga al documento.

La migrazione iniziale usa uno snapshot dello schema (`migrations/schema_v1.py`) che non deve essere aggiornato quando cambiano i modelli. Cambiamenti futuri richiedono una nuova migrazione. La specifica contiene uno schema concettuale: la query seguente usa i nomi effettivamente implementati.

```sql
SELECT s.canonical_name AS subject,
       r.predicate,
       t.canonical_name AS object,
       e.quote, e.chunk_id, d.id AS document_id, g.version_id
FROM documents d
JOIN document_graphs g ON g.version_id = d.active_version_id
JOIN relation_evidence e ON e.graph_id = g.id
JOIN relations r ON r.id = e.relation_id
JOIN entities s ON s.id = r.subject_entity_id
JOIN entities t ON t.id = r.object_entity_id
WHERE d.enabled AND d.id = :document_id;
```

Una citazione di relazione è validata rispetto a un chunk prima del commit. Il grafo attivo è ottenuto filtrando versione attiva e documento abilitato; le evidenze storiche non vengono fisicamente cancellate. Due fonti possono condividere una relazione e conservarne evidenze separate. Il modello registra affermazioni delle fonti, non ne certifica la verità.

## Indicizzazione e retrieval

Gli eventi `index_outbox` vengono inseriti nella transazione che crea la revisione. Il worker mantiene il lock dell’evento durante le operazioni esterne e aggiorna `index_manifest` solo al successo. Gli ID punto sono UUIDv5 di namespace e chunk. Se il processo cade dopo l’upsert, il retry usa gli stessi ID; non esiste una transazione distribuita.

Le azioni di ritiro sono valutate sulla situazione canonica corrente: un vecchio evento di cancellazione non rimuove una versione nel frattempo riattivata. Modifiche e ritiri accodano eventi per tutti i namespace conosciuti della revisione. Il manifest descrive una proiezione; la query PostgreSQL sulla versione attiva è l’ultima autorità sulla citabilità. Un mancato export non invalida il salvataggio canonico e può essere recuperato con `export`.

Il retrieval usa Qdrant più menzioni/relazioni SQL, rilegge chunk e versione canonici, applica vincoli di data/canale/tag e ricostruisce il timestamp dai segmenti originali. Oversampling fino ad almeno 100 candidati limita l’effetto di punti obsoleti; non è una garanzia di recall su archivi arbitrariamente grandi. Il filtro canonico impedisce di citare punti vecchi anche se la loro eliminazione è in ritardo.

## Job, budget e contenuti esterni

Job persistenti con chiavi di deduplicazione, retry/backoff, lease, heartbeat e token di assegnazione. Claim con `FOR UPDATE SKIP LOCKED`. L’ingestione usa un advisory lock transazionale globale nella prima versione: semplifica la deduplicazione di fonti/entità a costo di limitare il throughput di scrittura. Le richieste ai provider sono memorizzate con input, endpoint e modello nella chiave cache; prenotazione sotto advisory lock e budget giornaliero in UTC. Limite di chiamate, non limite monetario.

I dati originali delle trascrizioni rimangono nei segmenti e nel JSON acquisito. Quando un segmento supera la dimensione del chunk viene suddiviso, mantenendo il legame al segmento intero: il timestamp porta all’inizio del segmento, non a una parola precisa. Le patch del glossario contengono testo prima/dopo; la normalizzazione è una patch separata. Non si applicano patch LLM in questa release.

Il lettore web valida gli IP e li usa nella connessione per evitare un secondo DNS non validato; valida ogni redirect e limita i byte decodificati. Il lettore GitHub risolve il commit e acquisisce il README a quella SHA. Le pagine sono fonti indipendenti, deduplicate per chiave canonica e revisione, con link dai documenti originari. Una revisione esterna è controllata quando un nuovo riferimento/job la acquisisce: non c’è ancora un polling autonomo di tutti i README già salvati.

## LightRAG e limiti deliberati

È attivo un solo percorso: `retrieval.retrieve` con Qdrant e grafo SQL. LightRAG non è una dipendenza e non viene dichiarato operativo. Il README upstream consultato elenca Qdrant e backend PostgreSQL/AGE; non basta per garantire che versionamento, ritiro e citazioni coincidano con quelli canonici.

Per un futuro spike fissare una release e verificare: storage interni, necessità di AGE, mapping in `rag_id_map`, fonti di ogni risultato, aggiornamento/rimozione di un solo contributo, recovery e numero chiamate. Non attivare una seconda estrazione LLM parallela a quella applicativa.

La prima versione non comprende hosting remoto, autenticazione UI, audio-to-text, text-to-SQL generativo, tassonomie normalizzate, riassunti e classificazioni LLM, crawling di documentazione multi-pagina, correzioni LLM con revisione o LightRAG. Il budget e il provider sono opzionali; il flusso deterministico rimane disponibile.

## Fonti tecniche verificate

- YouTube playlistItems: https://developers.google.com/youtube/v3/docs/playlistItems/list
- Trascrizioni e limitazioni: https://github.com/jdepoix/youtube-transcript-api (installato 1.2.4)
- Qdrant client / `query_points`: https://qdrant.tech/documentation/quickstart/ (client 1.19.1, server fissato 1.19.2)
- Qdrant server release: https://github.com/qdrant/qdrant/releases/tag/v1.19.2
- Snapshot: https://qdrant.tech/documentation/snapshots/
- LightRAG storage: https://github.com/HKUDS/LightRAG/blob/main/docs/ProgramingWithCore.md
- Dipendenze Python esatte e hash: `uv.lock`; riproduzione con `uv sync --frozen`.
