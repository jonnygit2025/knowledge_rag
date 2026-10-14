# Knowledge RAG — Specifica del progetto e prompt per GitHub

Versione: 1.0 · Data: 13 ottobre 2026

Documento di passaggio alla prossima sessione. Descrive le decisioni concordate e una proposta tecnica da implementare e verificare. Non rappresenta un'applicazione già sviluppata o testata.

## 1. Obiettivo e modalità di lavoro

Costruire un'applicazione personale che monitora alcuni canali YouTube, acquisisce le trascrizioni disponibili, corregge prudentemente gli errori tecnici, raccoglie i riferimenti esterni e rende il corpus ricercabile e interrogabile con fonti verificabili.

Il codice sarà sviluppato su GitHub in una prossima sessione, usando la connessione GitHub che l'utente dichiara di aver attivato. L'utente farà successivamente clone/pull sul proprio computer e testerà l'applicazione. Questa sessione produce soltanto la specifica e il prompt di avvio.

Requisiti richiesti:

- Verificare all'avvio se i canali configurati hanno pubblicato nuovi video.
- Acquisire tutti i video nuovi nel periodo considerato, senza limitarsi all'ultimo.
- Recuperare la trascrizione, quando disponibile, e conservarne l'originale.
- Produrre testo leggibile senza timestamp visibili e correggere refusi o termini trascritti male, evitando aggiunte e riscritture del significato.
- Salvare/esportare Markdown con titolo, data, canale, URL, argomenti, parole chiave e riferimenti.
- Acquisire documentazione, README e pagine pertinenti citate nelle descrizioni.
- Usare Qdrant come database vettoriale.
- Usare PostgreSQL come fonte autorevole e interrogabile dei dati applicativi, compresi i grafi e la loro provenienza documentale.
- Offrire una UI con novità, filtri, archivio, stato elaborazioni, visualizzazione dei grafi e chat con citazioni.
- Valutare LightRAG per la ricerca basata su grafi e vettori.

La proposta precedente con SQLite viene sostituita: PostgreSQL e Qdrant fanno parte dell'architettura fin dalla prima versione.

## 2. Significato di «verità assoluta»

PostgreSQL sarà la **single source of truth del sistema**: il riferimento autorevole per identificativi, contenuti conservati, versioni, stato di elaborazione, provenienza e relazioni.

Questo non rende automaticamente vere le affermazioni dei video o le estrazioni di un LLM. Distinguere sempre:

1. Metadati acquisiti dalla fonte: titolo, URL, canale, date.
2. Contenuto originale acquisito: trascrizione o pagina.
3. Trasformazioni: testo normalizzato, correzioni e chunk.
4. Interpretazioni: argomenti, riassunti, entità e relazioni estratti dal modello.
5. Eventuali verifiche umane o riscontri in altre fonti.

Una relazione del grafo significa «questa fonte sostiene o menziona questa relazione». Deve conservare la prova documentale; il punteggio di confidenza del modello non è una prova di correttezza.

Qdrant conterrà una proiezione ricostruibile dei contenuti autorevoli. Gli esporti Markdown saranno rigenerabili da PostgreSQL. Le modifiche manuali ai Markdown non aggiorneranno implicitamente il database: un futuro import dovrà creare una revisione esplicita.

## 3. Architettura proposta

| Componente | Tecnologia/proposta | Responsabilità |
| --- | --- | --- |
| Codice e collaborazione | Repository GitHub, preferibilmente privato | Codice, configurazioni di esempio, migrazioni, test e istruzioni |
| Servizi locali | Docker Compose | Avvio riproducibile di PostgreSQL, Qdrant, app e worker |
| Logica applicativa | Python, moduli separati | Acquisizione, trasformazioni, esportazione e interrogazione |
| Persistenza canonica | PostgreSQL | Fonti, documenti, versioni, chunk, grafi, evidenze, job e mapping |
| Ricerca semantica | Qdrant | Embedding e payload con riferimenti ai dati canonici |
| Accesso al database | SQLAlchemy + Alembic, proposta | Modelli applicativi e migrazioni versionate |
| UI | Streamlit, proposta | Dashboard, ricerca, consultazione, chat e stato processi |
| Elaborazioni | Worker separato dalla UI | Job riprendibili e sincronizzazione degli indici |
| YouTube | Data API + provider trascrizioni sostituibile | Metadati ufficiali e trascrizioni disponibili |
| Web e repository | Lettore GitHub + estrattore web | README, documentazione selezionata e pagine citate |
| LLM/embedding | Provider configurabile | Correzioni, metadati derivati, grafo, embedding e risposte |
| Ricerca con grafo | Adapter LightRAG, da validare | Combinazione del recupero semantico con relazioni pertinenti |

Per il prototipo non è necessario un backend FastAPI separato: UI, worker e CLI possono condividere un package Python. La separazione logica permette di introdurre API in seguito.

Docker Compose userà volumi persistenti. Il README dovrà spiegare la configurazione su Windows con Docker Desktop e PowerShell, se questo sarà il computer di test confermato dall'utente. Un pull aggiorna il codice, non trasferisce automaticamente i database.

## 4. Acquisizione YouTube e limiti reali

YouTube Premium non è necessario per i metadati pubblici e non concede all'app il diritto tecnico di scaricare qualsiasi trascrizione tramite API ufficiale. Il metodo ufficiale `captions.download` richiede il permesso di modificare il video [1].

Proposta per i metadati: risolvere il canale e conservare l'ID della playlist degli upload; scorrere `playlistItems.list` con paginazione, confrontare gli ID con il database e recuperare i metadati necessari dei video [2]. Non usare solo l'ultimo video come checkpoint. Al primo avvio applicare una finestra configurabile, per esempio ultimi 30 giorni o ultimi 10 video per canale.

Proposta per le trascrizioni: adapter per `youtube-transcript-api`, libreria non ufficiale. Preferire la lingua originale e, quando disponibili nella lingua desiderata, sottotitoli manuali. Registrare lingua, tipo di traccia e provider. La libreria segnala blocchi degli IP cloud e possibili blocchi anche su altre connessioni [3]. La disponibilità va misurata sui canali reali.

Prevedere fin dall'inizio importazione manuale di TXT/SRT/VTT o JSON. Mancanza di sottotitoli, blocco della richiesta, video indisponibile e errore temporaneo sono condizioni distinte. Un video senza testo rimane visibile con lo stato corretto. Nessuna trascrizione inventata come fallback.

La trascrizione audio è un possibile sviluppo successivo, dipendente dalla disponibilità lecita dell'audio e dal budget. Non costituisce il fallback automatico della prima versione.

GitHub Actions sarà usato inizialmente per verifiche del codice. L'acquisizione avverrà sul computer di test; l'esecuzione cloud o pianificata verrà valutata dopo avere verificato affidabilità e persistenza.

## 5. Flusso di elaborazione

1. Avviare UI e servizi; mostrare subito l'archivio esistente.
2. All'avvio applicativo inviare un job di aggiornamento. La riesecuzione di Streamlit o il refresh del browser non devono creare automaticamente job duplicati.
3. Individuare nuovi video e salvare metadati e descrizioni in PostgreSQL.
4. Recuperare la trascrizione o registrare uno stato esplicito di indisponibilità.
5. Salvare originale e segmenti temporali prima di qualsiasi trasformazione.
6. Normalizzare il testo ed eseguire correzioni controllate.
7. Estrarre metadati derivati e URL della descrizione.
8. Acquisire le fonti esterne selezionate come documenti autonomi.
9. Generare versioni, chunk ed evidenze; estrarre il grafo documentale se abilitato.
10. Registrare in PostgreSQL gli eventi di indicizzazione nella stessa transazione delle modifiche canoniche.
11. Il worker aggiorna Qdrant ed eventuali indici LightRAG; registra esito e versione indicizzata.
12. Esportare Markdown e rendere disponibili i nuovi contenuti nella UI.

Ogni passaggio deve essere riprendibile. Conservare numero tentativi, ultimo errore e data del prossimo tentativo. Limitare concorrenza e usare retry con backoff solo per errori compatibili con un nuovo tentativo. Separare stati di acquisizione, trasformazione e indicizzazione, così un problema su Qdrant non annulla una trascrizione già salvata.

## 6. Correzione prudente e provenienza

La normalizzazione di spazi, codifica e formattazione è deterministica. I timestamp vengono esclusi dal testo leggibile ma conservati nei segmenti originali.

Usare un glossario tecnico contestuale per termini come GitHub, Claude e nomi di librerie. Le sostituzioni non devono essere cieche: un termine ambiguo va segnalato.

Il modello propone patch strutturate associate a un segmento o intervallo: testo originale, proposta, motivo e stato di revisione. Il programma verifica che il testo originale corrisponda prima di applicarle. Punteggiatura e correzioni linguistiche restano tracciabili.

Conservare nomi propri, versioni, numeri, comandi, URL, unità e negazioni con particolare attenzione. Differenze anomale, omissioni o sostituzioni ambigue richiedono revisione. Nessun controllo può garantire da solo zero errori.

Registrare modello, versione del prompt, versione del glossario e data di elaborazione. Riassunti e classificazioni sono risultati separati: non entrano nella trascrizione come se fossero parole dell'autore.

La corrispondenza fra testo elaborato e segmenti originali deve sopravvivere al chunking, anche quando cambia la lunghezza del testo. Ogni citazione della chat deve risalire a testo, versione, URL e timestamp quando disponibili.

## 7. Modello dati PostgreSQL

Schema concettuale da tradurre in migrazioni nella sessione di implementazione. Gli ID interni sono UUID; gli ID esterni di YouTube o dei repository sono campi separati con vincoli di unicità appropriati. Tutte le date applicative sono `timestamptz` in UTC; la UI le presenta in Europe/Rome.

| Tabella | Campi/legami essenziali | Scopo |
| --- | --- | --- |
| `channels` | `channel_id`, `youtube_channel_id`, `uploads_playlist_id`, `name`, `enabled` | Canali monitorati |
| `videos` | `video_id`, `youtube_video_id UNIQUE`, `channel_id`, titolo, URL, descrizione, `published_at`, `first_seen_at` | Metadati e identità del video |
| `documents` | `document_id`, `source_type`, chiave fonte canonica univoca, URL, `video_id` opzionale, `active_version_id` | Identità stabile del documento |
| `document_versions` | `version_id`, `document_id`, `raw_text`, `raw_json` opzionale, `normalized_text`, `corrected_text`, hash, date, metadata JSONB, versione pipeline | Revisioni immutabili e originali conservati |
| `transcript_segments` | `segment_id`, `version_id`, posizione, testo originale, `start_seconds`, `duration_seconds` | Localizzazione nel video |
| `corrections` | `correction_id`, `version_id`, segmento/intervallo, prima/dopo, motivo, stato, modello/prompt | Audit delle modifiche |
| `chunks` | `chunk_id`, `version_id`, posizione, testo, hash, versione chunker | Segmenti di recupero canonici |
| `chunk_segments` | `chunk_id`, `segment_id`, intervalli quando necessari | Corrispondenza fra chunk e segmenti originali |
| `document_links` | documento origine, documento destinazione, URL incontrato, tipo di collegamento | Provenienza dei riferimenti e deduplicazione |
| `document_graphs` | `graph_id`, `version_id`, versione estrattore, stato | Grafo locale di una versione documentale |
| `entities` | `entity_id`, tipo, nome canonico, chiave di disambiguazione, alias | Entità condivise fra documenti |
| `entity_mentions` | `mention_id`, `graph_id`, `entity_id`, `chunk_id`, citazione, stato | Nodi presenti nel grafo documentale con evidenza |
| `relations` | `relation_id`, entità sorgente/destinazione, predicato | Relazioni normalizzate, senza perdere le evidenze |
| `relation_evidence` | `evidence_id`, `relation_id`, `graph_id`, `chunk_id`, citazione/intervallo, stato, confidenza opzionale | Affermazioni documentate che sostengono una relazione |
| `jobs` / `job_steps` | ID, target, passaggio, stato, tentativi, errore e date | Ripresa delle elaborazioni |
| `index_outbox` | `event_id`, target/versione, azione, stato, tentativi | Sincronizzazione affidabile degli indici derivati |
| `index_manifest` | versione, namespace, modello embedding, versione chunker, stato | Tracciamento delle proiezioni effettivamente disponibili |
| `rag_id_map` | backend/workspace, tipo/ID interno, `document_id`, `version_id`, `chunk_id` opzionale | Mapping con gli identificativi del motore RAG |

Usare foreign key, vincoli univoci e indici sui campi consultati [4]. JSONB serve per metadata flessibili e risultati strutturati [5], mentre le relazioni principali e i grafi rimangono in tabelle interrogabili. Se serve conservare il JSON byte per byte, salvarne anche il testo originale: JSONB non ne conserva la formattazione originaria.

Per tassonomie e filtri frequenti prevedere tabelle `tags` e associazioni ai documenti/versioni, anziché dipendere soltanto da etichette libere generate dal modello.

### Grafo per documento e grafo complessivo

Ogni `document_id` può avere più versioni; ogni versione può avere una diversa estrazione del grafo. Il grafo della versione attiva è ricostruito tramite `document_graphs`, `entity_mentions` e `relation_evidence`.

Il grafo complessivo collega le entità condivise, ma ogni arco mantiene una o più evidenze. Rimuovere un documento ritira soltanto il suo contributo: una relazione sostenuta da altri documenti resta consultabile. Eventuali affermazioni contraddittorie restano separate e attribuite alle rispettive fonti.

Non unificare entità soltanto perché hanno un nome simile. Conservare alias e identificatori verificabili, per esempio URL del repository. Le relazioni con provenienza diretta, come «video cita repository nella descrizione», sono distinte dalle relazioni semantiche proposte dal modello.

Esempio di interrogazione del grafo di un documento, limitata alla versione attiva:

```sql
SELECT s.canonical_name AS subject,
       r.predicate,
       t.canonical_name AS object,
       e.quote,
       e.chunk_id,
       e.status
FROM documents d
JOIN document_versions v ON v.version_id = d.active_version_id
JOIN document_graphs g ON g.version_id = v.version_id
JOIN relation_evidence e ON e.graph_id = g.graph_id
JOIN relations r ON r.relation_id = e.relation_id
JOIN entities s ON s.entity_id = r.subject_entity_id
JOIN entities t ON t.entity_id = r.object_entity_id
WHERE d.document_id = :document_id;
```

È una query illustrativa dello schema proposto, non SQL già eseguito. I nomi dei campi definitivi devono rimanere coerenti con le migrazioni.

## 8. Qdrant e collegamento tramite ID

Qdrant conserva embedding e payload che rimandano ai record canonici; PostgreSQL conserva testo autorevole e provenienza. Qdrant supporta ID UUID e upsert sullo stesso ID, utile per ritentare l'indicizzazione senza duplicare punti [6].

Per l'indice applicativo dei chunk, proposta: un UUID deterministico del punto derivato da `chunk_id` e namespace dell'indice. Il namespace comprende modello embedding, dimensione e versione della strategia di chunking. Non usare un solo punto per documento: normalmente un documento contiene più chunk.

Payload minimo proposto:

```json
{
  "document_id": "<uuid-documento>",
  "version_id": "<uuid-versione>",
  "chunk_id": "<uuid-chunk>",
  "source_type": "youtube_transcript",
  "channel_id": "<uuid-canale>",
  "published_at": "2026-10-13T15:00:00Z",
  "ingested_at": "2026-10-13T20:00:00Z",
  "language": "it",
  "tags": ["RAG", "GitHub"],
  "index_namespace": "<modello-dimensione-chunker>"
}
```

Le date sono esempi. Creare indici payload solo per i filtri realmente usati [7]. I filtri vettoriali velocizzano il recupero, ma la verifica finale dell'identità e della versione attiva avviene in PostgreSQL.

Se LightRAG gestisce collezioni proprie di chunk, entità o relazioni, rispettare i suoi identificativi e conservarne il mapping in `rag_id_map`; non presumere che coincidano con gli UUID applicativi. Evitare collezioni duplicate senza una finalità e un responsabile chiari.

Cambiare modello o dimensione degli embedding richiede un nuovo namespace/indice e una reindicizzazione esplicita; non mescolare vettori incompatibili.

## 9. Coerenza fra i database, versioni e ripresa

PostgreSQL e Qdrant non condividono una transazione atomica. Usare un outbox transazionale:

1. Salvare una versione documentale con chunk ed eventi nell'outbox in una transazione PostgreSQL.
2. Il worker preleva l'evento e scrive i punti in Qdrant con ID idempotenti.
3. Dopo l'esito positivo registra il completamento nell'`index_manifest`.
4. In caso di interruzione, ritenta lo stesso evento senza ricreare documenti o chunk.

Una nuova versione canonica può essere visibile nell'archivio prima di essere disponibile per la chat: mostrare lo stato «indicizzazione in corso». La chat non presenta automaticamente vecchi chunk come se fossero aggiornati. Scartare risultati di versioni inattive consultando PostgreSQL e, se necessario, recuperare più candidati.

Una modifica al contenuto, al correttore o al chunker può generare una nuova versione di elaborazione anche se la fonte esterna non è cambiata. Una nuova acquisizione senza variazioni aggiorna `last_checked_at`, senza produrre inutilmente un nuovo corpus o nuove chiamate LLM.

Rimozioni e disattivazioni devono aggiornare stato canonico, contributi del grafo e outbox per eliminare/ritirare i relativi punti. Prevedere una riconciliazione che rilevi eventi incompleti, punti mancanti e proiezioni obsolete.

PostgreSQL è il riferimento per ricostruire gli indici. Prevedere backup e istruzioni di ripristino per PostgreSQL, oltre a snapshot Qdrant per ridurre tempi e costo di ricostruzione. Il database canonico deve includere anche configurazioni e versioni di elaborazione necessarie alla ricostruzione.

## 10. Fonti esterne e Markdown

Estrarre gli URL dalle descrizioni, normalizzarli e classificare repository, documentazione, articoli e collegamenti non pertinenti. Conservare sia URL incontrato sia URL canonico; normalizzare con prudenza senza eliminare parametri che identificano il contenuto.

Per GitHub acquisire inizialmente README e file documentali selezionati; registrare path e commit SHA effettivamente acquisito. Per il web partire dalla pagina citata, registrando data e hash, con limiti di profondità, numero pagine e dimensione. Trafilatura è una candidata per estrarre testo web [8]. Un servizio esterno di lettura rimane opzionale.

Le pagine e i file sono documenti autonomi. Lo stesso README citato da più video viene acquisito una sola volta per revisione e collegato a tutti i video pertinenti. Una pagina non raggiungibile rimane un riferimento con stato di errore.

Trattare istruzioni presenti nei contenuti acquisiti come testo della fonte, senza concedere loro controllo su strumenti o configurazione. Limitare le richieste a URL HTTP(S) pubblici e validare anche le destinazioni dei redirect.

Formato di export proposto, rigenerabile da PostgreSQL:

```markdown
---
document_id: "<uuid>"
version_id: "<uuid>"
source_type: youtube_transcript
title: "Titolo del video"
channel: "Nome del canale"
published_at: "2026-10-13T15:00:00Z"
ingested_at: "2026-10-13T20:00:00Z"
url: "https://www.youtube.com/watch?v=VIDEO_ID"
language: it
topics: [AI, RAG]
keywords: [Claude, GitHub]
---

# Titolo del video

## Riferimenti
- [Repository citato](https://github.com/owner/repo)

## Trascrizione corretta
Testo completo leggibile...
```

Originali e audit rimangono consultabili dal database e dalla UI. Un riassunto opzionale è una sezione esplicitamente derivata, distinta dalla trascrizione.

## 11. LightRAG: integrazione da verificare

La documentazione consultata presenta backend Qdrant per i vettori e più opzioni PostgreSQL per dati e grafi [9]. I nomi e le dipendenze vanno verificati sulla release esatta scelta; fissare quella versione nel progetto.

Prima di integrare LightRAG svolgere una prova limitata che dimostri:

- Quali storage vengono usati per chunk, stato, entità, relazioni e vettori.
- Come mantenere `document_id` e `version_id` applicativi nelle fonti restituite.
- Come aggiornare e rimuovere un contributo senza perdere evidenze di altri documenti.
- Quali estensioni PostgreSQL sono richieste dal backend grafo scelto.
- Come ottenere evidenze e citazioni utilizzabili, senza dedurre la provenienza dal solo testo generato.

Preferire un backend PostgreSQL a tabelle se la release scelta lo supporta e soddisfa i requisiti. Un backend che richiede Apache AGE necessita di immagine/configurazione compatibile; non presumere che funzioni con una normale istanza PostgreSQL senza estensioni.

Separare lo schema applicativo canonico dagli storage interni LightRAG. Il grafo con evidenze per documento deve restare interrogabile anche senza LightRAG. L'adapter alimenta il motore con dati canonici o restituisce estrazioni che vengono validate e registrate nel modello applicativo; definire un solo flusso di estrazione attivo per evitare doppie chiamate LLM.

Se l'integrazione non preserva i requisiti, il prototipo può usare recupero Qdrant più espansione SQL del grafo canonico attraverso la stessa interfaccia di retrieval. Questa alternativa va dichiarata e testata: non chiamare LightRAG un'integrazione non realizzata. Microsoft GraphRAG rimane una valutazione successiva, anche per il costo di indicizzazione segnalato dal progetto [10].

## 12. Interrogazione e UI

Distinguere date di pubblicazione, prima acquisizione, ultima verifica e data di un eventuale rilascio citato. Un video nuovo può parlare di una tecnologia vecchia. La presenza di un repository in un video non dimostra che il repository sia appena nato.

| Domanda | Percorso di risposta |
| --- | --- |
| «Quali video sono usciti ieri?» | Query PostgreSQL su pubblicazione e canali |
| «Quali repository ho scoperto questa settimana?» | Query PostgreSQL su prima acquisizione e document links |
| «Come funziona questa libreria?» | Recupero semantico Qdrant, testo canonico e risposta citata |
| «Quali strumenti sono collegati a questo modello?» | Recupero entità/relazioni con evidenze, eventualmente LightRAG |
| «Il video e il README concordano?» | Recupero separato delle fonti e confronto attribuito |

Per domande miste: applicare prima i vincoli strutturati, poi retrieval semantico/grafo sul sottoinsieme. I risultati vettoriali sono candidati: la risposta usa testo e versione risolti in PostgreSQL. Gestire esplicitamente mancanza di evidenze e contraddizioni.

Usare funzioni di query parametrizzate. Un'eventuale futura funzione text-to-SQL avrà accesso di sola lettura e limiti di esecuzione, separata dal ruolo del worker che scrive i dati.

Pagine iniziali:

1. **Novità:** nuovi video e fonti, filtri per data/canale/argomento, conteggi e ultimo aggiornamento.
2. **Archivio:** testo, originale, correzioni, metadati e riferimenti.
3. **Grafo:** vista del documento/versione selezionati e, in seguito, vista globale con evidenze apribili.
4. **Chat:** risposta, elenco fonti, passaggi citati e URL al timestamp quando disponibile.
5. **Elaborazioni:** stato, errori, avanzamento e retry.
6. **Configurazione:** canali, lingue, limiti, provider e budget.

La UI Streamlit può usare i componenti chat documentati [11]. La prima versione sarà personale e locale; l'accesso remoto/mobile richiede una successiva scelta di hosting e accesso.

## 13. Incrementi e criteri di accettazione

### Incremento A — Base locale e acquisizione

Docker Compose, PostgreSQL, Qdrant, migrazioni, configurazione, CLI/worker, 2–3 canali o fixture, importazione manuale, originali conservati, Markdown e dashboard. Qdrant è presente nell'infrastruttura; i primi documenti possono essere acquisiti senza chiamate embedding.

Accettazione: due aggiornamenti consecutivi non duplicano video/documenti; una pipeline interrotta riparte; sottotitoli mancanti non bloccano altri video; i dati persistono dopo un riavvio normale; esiste un percorso demo senza credenziali.

### Incremento B — Qualità, fonti e ricerca vettoriale

Glossario, patch tracciate, chunk con mapping temporale, fonti esterne deduplicate, embedding Qdrant e outbox.

Accettazione: originali immutati, correzioni consultabili, upsert ripetuti senza duplicati, nuovo contenuto/versione distinguibile dal precedente, errore Qdrant recuperabile senza riacquisizione del video.

### Incremento C — Grafi e chat con fonti

Grafi per documento in PostgreSQL, evidenze, interrogazioni SQL, adapter di retrieval e prova LightRAG. Una demo integrata deve arrivare a una risposta citata e a un grafo consultabile.

Accettazione: ogni relazione estratta ha evidenza; il grafo è filtrabile per `document_id` e versione; due fonti possono sostenere la stessa relazione senza perdere provenienza; un documento ritirato non cancella contributi altrui; la chat non cita versioni inattive come correnti e indica quando non trova prove.

### Incremento D — Automazione e gestione

Pianificazione facoltativa, riconciliazione degli indici, backup/ripristino, metriche di token/costi e valutazione hosting. Nessuna promessa di aggiornamenti cloud affidabili prima della prova sulle trascrizioni.

Test prioritari: deduplicazione, versioni, provenienza, ripresa dei job, sincronizzazione PostgreSQL/Qdrant, ritiro dei contributi del grafo e integrità delle citazioni. Usare fixture deterministiche; i test live con YouTube/provider sono separati, opzionali e documentati.

## 14. Repository e parametri ancora da definire

Organizzazione proposta: package `src/knowledge_rag/` con moduli `ingestion`, `processing`, `storage`, `graph`, `retrieval`, `jobs`, `export` e `ui`; directory `migrations`, `tests/fixtures` e `docs`; file `pyproject.toml`, lock delle dipendenze, `compose.yaml`, `.env.example`, `README.md` e workflow CI.

Su GitHub conservare codice, migrazioni, test, fixture e documentazione. Escludere segreti, volumi, database, indici, log e corpus reale per impostazione predefinita. Gli export Markdown reali rimangono locali, salvo una successiva scelta esplicita di sincronizzazione.

Parametri da confermare nella prossima sessione:

- Repository GitHub da creare/usare e branch di lavoro.
- Link dei primi 2–3 canali.
- Finestra iniziale, lingue e gestione di Shorts/live.
- Provider LLM, provider embedding e budget massimo.
- Computer di test e disponibilità di Docker Desktop.

Se mancano canali o chiavi, procedere con fixture e configurazione di esempio, senza inventare credenziali né canali personali. Conservare metrica delle chiamate, cache per input/configurazione e budget configurabile; evitare stime economiche non misurate.

## 15. Prompt da usare nella prossima sessione

Copiare il testo seguente e allegare questo documento. È una bozza riutilizzabile di prompt; diventa un'istruzione operativa quando l'utente la invia nella nuova sessione.

```text
Voglio iniziare a implementare il progetto Knowledge RAG descritto nel documento allegato. Usa quel documento come specifica e conserva le decisioni seguenti: Qdrant per la ricerca vettoriale; PostgreSQL come single source of truth per contenuti, versioni, provenienza, stato e grafi; grafi documentali interrogabili e collegati tramite document_id; corpus ed export Markdown; sviluppo su GitHub e successivo test locale sul mio computer.

Ho connesso GitHub. Verifica quali capacità GitHub sono effettivamente disponibili e usa il repository che indico. Se il repository non è identificabile, chiedimi soltanto quale usare; se fornisco il nome di un nuovo repository, crealo privato. Rispetta AGENTS.md e convenzioni del progetto. Lavora su un branch dedicato, pubblica i commit sul branch e apri una draft PR se il connettore lo consente. Non fare merge automatico e non distribuire l'app: la testerò localmente dopo clone/pull.

Prima delle modifiche, ispeziona il repository, leggi la specifica e definisci un piano breve. Verifica nelle fonti ufficiali le API e le versioni effettivamente usate, soprattutto YouTube, Qdrant e gli storage LightRAG. Non considerare YouTube Premium una soluzione per l'accesso alle trascrizioni. Mantieni il provider trascrizioni sostituibile e prevedi import manuale.

Realizza il lavoro in incrementi verificabili. Inizia dalla base eseguibile dell'incremento A e dalla demo con fixture; poi procedi verso una prima versione integrata che includa acquisizione, PostgreSQL, export, Qdrant, grafo documentale e chat con fonti. Mantieni le integrazioni esterne configurabili, così il progetto può partire senza chiavi e senza chiamate a pagamento. Se un'integrazione live non è verificabile, implementa e testa il percorso con fixture e segnala con precisione ciò che resta da provare sul mio computer.

Usa Python con moduli separati, Docker Compose con volumi persistenti, PostgreSQL fin dall'inizio, Qdrant, migrazioni versionate e una UI Streamlit. Un worker separato dalla UI gestisce job e indicizzazione; non è necessario introdurre FastAPI nel primo prototipo.

Definisci identità stabili e distinte per documento, versione, chunk e grafo. Conserva gli originali e i segmenti temporali. Le correzioni LLM devono essere tracciate e non sostituire l'originale. Riassunti e metadati derivati rimangono separati dalla trascrizione.

I nodi e gli archi del grafo devono risalire alle evidenze tramite document_id, version_id e chunk_id. Le entità possono essere condivise fra documenti, ma un aggiornamento o un ritiro deve interessare soltanto i contributi della fonte interessata. PostgreSQL rappresenta l'autorità sui dati registrati, non una garanzia che le affermazioni delle fonti o del modello siano vere.

Usa un outbox transazionale per sincronizzare PostgreSQL con Qdrant. Rendi gli upsert idempotenti, verifica le versioni attive nel recupero e documenta la gestione delle proiezioni obsolete. Non presumere una transazione distribuita fra i due database.

Valida l'integrazione LightRAG su una release fissata: backend Qdrant, persistenza PostgreSQL dei grafi, eventuali estensioni richieste, mapping degli ID, aggiornamenti/rimozioni e citazioni. Non confondere i suoi storage interni con lo schema canonico applicativo. Se serve un adapter o se un requisito non è supportato, rendi esplicita la soluzione; il grafo canonico deve essere interrogabile anche senza LightRAG.

Prevedi dashboard novità, archivio, grafo per documento, chat e pagina stato job. Usa query strutturate per date/canali/novità e retrieval semantico o grafo per domande sui contenuti. Una repository citata in un video recente non è automaticamente una repository appena creata. Le risposte devono citare evidenze e dichiarare quando non sono disponibili.

Non committare segreti, database, volumi o indici. Includi .env.example, fixture, demo senza credenziali, test significativi di deduplicazione/provenienza/ripresa/sincronizzazione e README con istruzioni di clone/pull, configurazione, avvio, migrazioni e test locale, preferibilmente anche per Windows/PowerShell. Evita comandi che eliminano i dati durante il normale aggiornamento del codice.

Al termine indica branch/PR, cosa funziona, quali verifiche sono passate, quali integrazioni live restano da verificare e i comandi esatti che devo eseguire sul mio computer. Non fermarti a descrivere l'architettura: implementa il codice necessario al risultato concordato.
```

## 16. Fonti tecniche

Fonti consultate il 13 ottobre 2026. Le API e i backend vanno ricontrollati sulla versione scelta durante l'implementazione. Le scelte di schema, outbox, UI e versionamento sono proposte progettuali, non funzionalità fornite automaticamente dai prodotti citati.

1. YouTube Data API, Captions download: https://developers.google.com/youtube/v3/docs/captions/download
2. YouTube Data API, Implementation: Videos: https://developers.google.com/youtube/v3/guides/implementation/videos
3. youtube-transcript-api, repository e limitazioni: https://github.com/jdepoix/youtube-transcript-api
4. PostgreSQL, Constraints: https://www.postgresql.org/docs/current/ddl-constraints.html
5. PostgreSQL, JSON Types: https://www.postgresql.org/docs/current/datatype-json.html
6. Qdrant, Points: https://qdrant.tech/documentation/manage-data/points/
7. Qdrant, Payload: https://qdrant.tech/documentation/manage-data/payload/
8. Trafilatura, documentazione: https://trafilatura.readthedocs.io/en/latest/
9. LightRAG, Programming With Core: https://github.com/HKUDS/LightRAG/blob/main/docs/ProgramingWithCore.md
10. Microsoft GraphRAG, repository: https://github.com/microsoft/graphrag
11. Streamlit, Chat input: https://docs.streamlit.io/develop/api-reference/chat/st.chat_input
