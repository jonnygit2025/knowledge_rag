"""Local Streamlit UI. Reruns never schedule automatic synchronization jobs."""

import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
import yaml
from sqlalchemy import select

from knowledge_rag.config import settings
from knowledge_rag.db import session_factory
from knowledge_rag.export import markdown
from knowledge_rag.graph import graph_rows
from knowledge_rag.jobs import enqueue, enqueue_sync, load_config, retry_job
from knowledge_rag.models import (
    Channel,
    Correction,
    Document,
    DocumentLink,
    Job,
    Manifest,
    Outbox,
    ProviderCall,
    Segment,
    Version,
    Video,
    now,
)
from knowledge_rag.providers import Providers
from knowledge_rag.retrieval import retrieve
from knowledge_rag.vectors import VectorIndex

ROME = ZoneInfo("Europe/Rome")


def local(value):
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ROME).strftime("%d/%m/%Y %H:%M")


def utc_day(value):
    return datetime.combine(value, time.min, ROME).astimezone(UTC)


def selected_document(session, include_withdrawn=False):
    query = select(Document).order_by(Document.first_seen_at.desc())
    if not include_withdrawn:
        query = query.where(Document.enabled.is_(True))
    documents = session.scalars(query).all()
    if not documents:
        st.info("Archivio vuoto. Carica la demo oppure importa un documento.")
        return None
    return st.selectbox(
        "Documento", documents, format_func=lambda d: f"{d.title} · {str(d.id)[:8]}"
    )


def main():
    st.set_page_config(page_title="Knowledge RAG", page_icon="📚", layout="wide")
    cfg, factory = settings(), session_factory()
    st.title("Knowledge RAG")
    st.caption("Archivio personale con versioni, grafi documentali e fonti verificabili")
    page = st.sidebar.radio(
        "Apri", ["Novità", "Archivio", "Grafo", "Chat", "Elaborazioni", "Configurazione"]
    )
    st.sidebar.caption("PostgreSQL: dati canonici · Qdrant: indice ricostruibile")
    if cfg.embedding_provider == "demo":
        st.sidebar.info(
            "Modalità demo: vettori deterministici e passaggi citati. Nessuna chiamata a pagamento."
        )
    if st.sidebar.button("Aggiorna canali"):
        with factory.begin() as session:
            job = enqueue_sync(session, cfg)
            st.sidebar.success(f"Aggiornamento: {job.state}")
    if st.sidebar.button("Ricarica vista"):
        st.rerun()
    with factory() as session:
        if page == "Novità":
            news(session, cfg)
        elif page == "Archivio":
            archive(session, cfg, factory)
        elif page == "Grafo":
            graph_view(session)
        elif page == "Chat":
            chat(session, cfg, factory)
        elif page == "Elaborazioni":
            jobs_view(session, factory)
        else:
            configuration(session, cfg)


def news(session, cfg):
    st.subheader("Novità")
    channels = session.scalars(select(Channel).order_by(Channel.name)).all()
    channel = st.selectbox(
        "Canale", [None, *channels], format_func=lambda c: c.name if c else "Tutti"
    )
    field = st.radio("Data da filtrare", ["Pubblicazione", "Prima acquisizione"], horizontal=True)
    date_from = st.date_input("Dal", value=datetime.now(ROME).date() - timedelta(days=30))
    date_to = st.date_input("Al", value=datetime.now(ROME).date())
    tag = st.text_input("Argomento/tag esatto (facoltativo)")
    query = (
        select(Video)
        .where(
            (Video.published_at if field == "Pubblicazione" else Video.first_seen_at)
            >= utc_day(date_from),
            (Video.published_at if field == "Pubblicazione" else Video.first_seen_at)
            < utc_day(date_to + timedelta(days=1)),
        )
        .order_by(Video.published_at.desc())
    )
    if channel:
        query = query.where(Video.channel_id == channel.id)
        st.caption(f"Ultima verifica del canale: {local(channel.last_checked_at)}")
    videos = session.scalars(query).all()
    if videos:
        st.dataframe(
            [
                {
                    "Titolo": v.title,
                    "Pubblicato": local(v.published_at),
                    "Acquisito": local(v.first_seen_at),
                    "Stato testo": v.acquisition_status,
                    "URL": v.url,
                    "Errore": v.last_error or "",
                }
                for v in videos
            ],
            hide_index=True,
        )
    documents = session.scalars(
        select(Document).where(Document.enabled.is_(True)).order_by(Document.first_seen_at.desc())
    ).all()
    st.write("Documenti acquisiti")
    for doc in documents:
        if not doc.active_version_id:
            continue
        version = session.get(Version, doc.active_version_id)
        meta = version.metadata_json
        if tag and tag not in meta.get("tags", []):
            continue
        if channel and meta.get("channel_id") != str(channel.id):
            continue
        if field == "Prima acquisizione":
            date = doc.first_seen_at
            date = date.replace(tzinfo=UTC) if date.tzinfo is None else date
        else:
            value = meta.get("published_at")
            if not value:
                # Web/README acquisition is not presented as publication.
                if doc.source_type != "youtube_transcript":
                    continue
                continue
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not utc_day(date_from) <= date < utc_day(date_to + timedelta(days=1)):
            continue
        manifest = session.get(Manifest, (version.id, cfg.namespace))
        st.write(
            f"**{doc.title}** · {doc.source_type} · indice: {manifest.state if manifest else 'pending'}"
        )
        st.caption(
            f"Prima acquisizione: {local(doc.first_seen_at)} · Ultima verifica: {local(doc.last_checked_at)}"
        )
    st.caption(
        "Una fonte acquisita oggi può descrivere una tecnologia precedente. La data di acquisizione non è una data di rilascio."
    )


def archive(session, cfg, factory):
    st.subheader("Archivio")
    uploaded = st.file_uploader(
        "Importa testo o sottotitoli", type=["txt", "md", "srt", "vtt", "json"]
    )
    youtube_id = st.text_input("ID YouTube da associare all’import (facoltativo)")
    if uploaded and st.button("Accoda importazione"):
        if uploaded.size > 5_000_000:
            st.error("Il limite di importazione è 5 MB.")
        else:
            cfg.import_dir.mkdir(parents=True, exist_ok=True)
            suffix = "." + uploaded.name.rsplit(".", 1)[-1].lower()
            name = f"{uuid.uuid4()}{suffix}"
            (cfg.import_dir / name).write_bytes(uploaded.getvalue())
            with factory.begin() as write:
                enqueue(
                    write,
                    "import",
                    {
                        "filename": name,
                        "title": uploaded.name,
                        "source_key": "manual:" + uploaded.name,
                        "youtube_video_id": youtube_id or None,
                    },
                    "import:" + name,
                )
            st.success("Importazione accodata. Il worker acquisirà il documento.")
    doc = selected_document(
        session, include_withdrawn=st.checkbox("Mostra anche documenti ritirati")
    )
    if not doc:
        return
    st.code(f"document_id: {doc.id}\nactive_version_id: {doc.active_version_id}")
    if not doc.active_version_id:
        st.info("Metadati conservati; trascrizione non ancora disponibile.")
        if doc.video_id:
            video = session.get(Video, doc.video_id)
            st.write(video.acquisition_status, video.last_error or "")
        return
    versions = session.scalars(
        select(Version).where(Version.document_id == doc.id).order_by(Version.created_at.desc())
    ).all()
    version = st.selectbox(
        "Versione",
        versions,
        format_func=lambda v: (
            f"{local(v.created_at)} · {str(v.id)[:8]} {'(attiva)' if v.id == doc.active_version_id else '(storica)'}"
        ),
    )
    st.caption(f"URL della versione: {version.metadata_json.get('source_url', doc.url)}")
    tabs = st.tabs(["Testo", "Originale", "Correzioni", "Metadati", "Riferimenti"])
    with tabs[0]:
        # Plain text avoids interpreting source HTML/instructions as UI controls.
        st.text(version.corrected_text)
        st.download_button(
            "Scarica Markdown",
            markdown(session, doc, version),
            file_name=f"{doc.id}-{version.id}.md",
            mime="text/markdown",
        )
    with tabs[1]:
        st.text(version.raw_text)
        segments = session.scalars(
            select(Segment).where(Segment.version_id == version.id).order_by(Segment.position)
        ).all()
        st.dataframe(
            [
                {
                    "Testo": s.raw_text,
                    "Inizio (s)": s.start_seconds,
                    "Durata (s)": s.duration_seconds,
                }
                for s in segments
            ],
            hide_index=True,
        )
    with tabs[2]:
        patches = session.scalars(
            select(Correction).where(Correction.version_id == version.id)
        ).all()
        st.dataframe(
            [
                {"Prima": p.before, "Dopo": p.after, "Motivo": p.reason, "Stato": p.status}
                for p in patches
            ],
            hide_index=True,
        )
        if not patches:
            st.caption("Nessuna correzione applicata.")
    with tabs[3]:
        st.json(version.metadata_json)
        st.code(version.pipeline_version)
    with tabs[4]:
        links = session.scalars(
            select(DocumentLink).where(DocumentLink.source_document_id == doc.id)
        ).all()
        st.dataframe(
            [
                {
                    "URL": item.encountered_url,
                    "Stato": item.status,
                    "Documento": str(item.target_document_id or ""),
                    "Errore": item.last_error or "",
                }
                for item in links
            ],
            hide_index=True,
        )


def graph_view(session):
    st.subheader("Grafo documentale")
    doc = selected_document(session)
    if not doc or not doc.active_version_id:
        return
    rows = graph_rows(session, document_id=doc.id)
    if not rows:
        st.info("Nessuna relazione con evidenza per questo documento.")
        return
    import json

    names = sorted({row[0] for row in rows} | {row[2] for row in rows})
    ids = {name: f"n{i}" for i, name in enumerate(names)}
    dot = "digraph G { rankdir=LR; node [shape=box];\n"
    dot += "\n".join(f"{ids[name]} [label={json.dumps(name)}];" for name in names)
    dot += "\n" + "\n".join(
        f"{ids[row[0]]} -> {ids[row[2]]} [label={json.dumps(row[1])}];" for row in rows
    )
    st.graphviz_chart(dot + "\n}")
    st.dataframe(
        [
            {
                "Soggetto": r[0],
                "Relazione": r[1],
                "Oggetto": r[2],
                "Evidenza": r[3],
                "chunk_id": str(r[4]),
                "document_id": str(r[5]),
                "version_id": str(r[6]),
            }
            for r in rows
        ],
        hide_index=True,
    )
    st.caption(
        "Gli archi rappresentano menzioni o affermazioni attribuite alla fonte. Ogni arco conserva una prova testuale."
    )


def chat(session, cfg, factory):
    st.subheader("Chat con fonti")
    channels = session.scalars(select(Channel)).all()
    channel = st.selectbox(
        "Limita al canale", [None, *channels], format_func=lambda c: c.name if c else "Tutti"
    )
    tag = st.text_input("Tag esatto (facoltativo)")
    limit_dates = st.checkbox("Limita per data di pubblicazione")
    date_from = st.date_input("Pubblicato dal") if limit_dates else None
    question = st.chat_input("Come si collegano PostgreSQL e Qdrant?")
    if question:
        provider = Providers(cfg, factory)
        index = VectorIndex(cfg, provider)
        with st.chat_message("user"):
            st.write(question)
        try:
            with st.spinner("Recupero delle evidenze…"):
                citations = retrieve(
                    session,
                    index,
                    question,
                    channel_id=channel.id if channel else None,
                    tag=tag or None,
                    published_after=utc_day(date_from) if date_from else None,
                )
                answer = provider.answer(question, citations)
            with st.chat_message("assistant"):
                st.write(answer)
                for i, citation in enumerate(citations, 1):
                    with st.expander(f"[{i}] {citation['title']}"):
                        st.text(citation["text"])
                        if citation["url"]:
                            st.link_button("Apri fonte", citation["url"])
                        st.code(
                            f"document_id: {citation['document_id']}\nversion_id: {citation['version_id']}\nchunk_id: {citation['chunk_id']}"
                        )
            st.session_state["last_answer"] = (answer, citations)
        except Exception as exc:
            from knowledge_rag.jobs import safe_error

            st.error(safe_error(exc, cfg))
    elif "last_answer" in st.session_state:
        st.write(st.session_state["last_answer"][0])
        st.caption(
            "Risposta precedente. Invia una nuova domanda per verificare le versioni attualmente attive."
        )


def jobs_view(session, factory):
    st.subheader("Elaborazioni")
    for model, label in ((Job, "Job di acquisizione"), (Outbox, "Outbox di indicizzazione")):
        st.write(label)
        items = session.scalars(select(model)).all()
        st.dataframe(
            [
                {
                    "ID": str(item.id),
                    "Stato": item.state,
                    "Tentativi": item.attempts,
                    "Ultimo errore": item.last_error or "",
                }
                for item in items
            ],
            hide_index=True,
        )
        retryable = [item for item in items if item.state in ("failed", "retry", "done")]
        if retryable:
            value = st.selectbox(
                "Elemento da ritentare",
                retryable,
                format_func=lambda item: f"{item.id} · {item.state}",
                key=label,
            )
            if st.button("Ritenta", key="retry" + label):
                with factory.begin() as write:
                    if model is Job:
                        retry_job(write, value.id)
                    else:
                        item = write.get(Outbox, value.id, with_for_update=True)
                        item.state, item.attempts, item.next_attempt_at = "pending", 0, now()
                st.rerun()


def configuration(session, cfg):
    st.subheader("Configurazione")
    st.caption("Canali e glossario sono in config.yaml. Le chiavi restano nel file locale .env.")
    value = st.text_area(
        "config.yaml",
        yaml.safe_dump(load_config(cfg), allow_unicode=True, sort_keys=False),
        height=320,
    )
    if st.button("Salva configurazione canali"):
        try:
            parsed = yaml.safe_load(value)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("channels", []), list):
                raise ValueError("Configurazione non valida")
            cfg.config_file.write_text(
                yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            st.success("Salvata. Premi Aggiorna canali per avviare un aggiornamento.")
        except (ValueError, yaml.YAMLError, OSError) as exc:
            st.error(str(exc))
    st.write(f"Embedding: {cfg.embedding_provider} · Namespace: {cfg.namespace}")
    st.write(f"Limite di chiamate provider al giorno (UTC): {cfg.max_paid_calls_per_day}")
    calls = session.scalars(
        select(ProviderCall).order_by(ProviderCall.created_at.desc()).limit(100)
    ).all()
    st.dataframe(
        [
            {
                "Tipo": c.kind,
                "Modello": c.model,
                "Stato": c.state,
                "Data": local(c.created_at),
                "Uso dichiarato dal provider": str(c.usage or {}),
            }
            for c in calls
        ],
        hide_index=True,
    )
    st.caption(
        "Il limite conta le chiamate, comprese quelle fallite. Non è un limite monetario; i costi non sono stimati."
    )


if __name__ == "__main__":
    main()
