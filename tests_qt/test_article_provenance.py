"""ArticleView.set_provenance() stores the state export_all_articles()
needs (R5-full step 3, see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md). The
actual manifest-writing logic is covered directly, without Qt, in
tests/test_article_generator.py::TestExportAllArticlesProvenance; this
only checks the wiring on the widget side. Driving _on_export_all()
itself through QFileDialog.getExistingDirectory is deliberately not
attempted here — doing that for the equivalent Cover flow hung
indefinitely under the offscreen QPA platform in this environment.
"""

from __future__ import annotations


def test_set_provenance_stores_record_id_and_source(process_events):
    from ui.article_view import ArticleView

    view = ArticleView()
    view.set_provenance(42, "/media/talk.mp4", [{"text": "hello"}], "en")

    assert view._record_id == 42
    assert view._source_path == "/media/talk.mp4"
    assert view._segments == [{"text": "hello"}]
    assert view._transcript_language == "en"

    view.close()


def test_set_provenance_defaults_are_safe_before_any_call(process_events):
    """A freshly constructed ArticleView (no transcript loaded yet) must
    not crash if _on_export_all() ran before set_provenance() — matches
    Cover's "unsaved" sentinel behavior for the same situation."""
    from ui.article_view import ArticleView

    view = ArticleView()
    assert view._record_id is None
    assert view._source_path is None
    assert view._segments == []
    assert view._transcript_language == ""

    view.close()
