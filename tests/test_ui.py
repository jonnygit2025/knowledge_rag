from pathlib import Path

from streamlit.testing.v1 import AppTest

from knowledge_rag.jobs import load_demo


def test_ui_pages_render(factory, cfg, monkeypatch):
    import knowledge_rag.config
    import knowledge_rag.db

    monkeypatch.setattr(knowledge_rag.config, "settings", lambda: cfg)
    monkeypatch.setattr(knowledge_rag.db, "session_factory", lambda: factory)
    load_demo(factory, cfg)
    path = Path(__file__).resolve().parents[1] / "src/knowledge_rag/ui.py"
    app = AppTest.from_file(str(path), default_timeout=15).run()
    assert not app.exception
    for page in ("Archivio", "Grafo", "Chat", "Elaborazioni", "Configurazione"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, f"UI page failed: {page}"
