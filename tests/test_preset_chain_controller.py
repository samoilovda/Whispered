"""Unit tests for ui/preset_chain_controller.py — Qt stand-ins from conftest."""

from ui.preset_chain_controller import PresetChainController


def _finished_calls(controller):
    calls = []
    controller.finished.connect(lambda had_error, ran: calls.append((had_error, ran)))
    return calls


class TestStepsForPreset:
    def test_transcribe_only_needs_nothing(self):
        assert PresetChainController.steps_for_preset("transcribe_only") == set()

    def test_youtube_preset(self):
        assert PresetChainController.steps_for_preset("youtube") == {"youtube"}

    def test_article_preset(self):
        assert PresetChainController.steps_for_preset("article") == {"article"}

    def test_full_preset_wants_both(self):
        assert PresetChainController.steps_for_preset("full") == {"youtube", "article"}

    def test_unknown_preset_needs_nothing(self):
        assert PresetChainController.steps_for_preset("something_else") == set()


class TestStartAndIsActive:
    def test_transcribe_only_returns_no_steps_and_stays_inactive(self):
        c = PresetChainController()
        assert c.start("transcribe_only") == set()
        assert not c.is_active()

    def test_youtube_only_is_active_until_it_reports_done(self):
        c = PresetChainController()
        steps = c.start("youtube")
        assert steps == {"youtube"}
        assert c.is_active()
        c.on_youtube_done(True)
        assert not c.is_active()

    def test_article_only_is_active_immediately_via_auto_article(self):
        """The article step isn't "pending" until on_generate_all_finished
        is wired up — is_active() must still report True right after
        start(), driven by the auto_article flag alone."""
        c = PresetChainController()
        c.start("article")
        assert c.is_active()

    def test_second_start_resets_had_error(self):
        c = PresetChainController()
        c.start("youtube")
        c.on_youtube_done(False)  # sets had_error, finishes
        c.start("youtube")
        # No public had_error getter; verify indirectly via a finish that
        # only fails if the stale flag leaked into the new chain.
        calls = _finished_calls(c)
        c.on_youtube_done(True)
        assert calls == [(False, {"youtube"})]


class TestFullChainSequencing:
    def test_full_preset_waits_for_both_steps(self):
        c = PresetChainController()
        c.start("full")
        calls = _finished_calls(c)

        c.on_youtube_done(True)
        assert calls == [], "must not finish while article is still pending"
        assert c.is_active()

        assert c.consume_auto_article() is True
        c.on_generate_all_finished()

        assert calls == [(False, {"youtube", "article"})]
        assert not c.is_active()

    def test_order_independent_youtube_after_article(self):
        c = PresetChainController()
        c.start("full")
        calls = _finished_calls(c)

        c.consume_auto_article()
        c.on_generate_all_finished()
        assert calls == [], "must not finish while youtube is still pending"

        c.on_youtube_done(True)
        assert calls == [(False, {"youtube", "article"})]

    def test_failed_youtube_step_excluded_from_ran_but_chain_still_finishes(self):
        c = PresetChainController()
        c.start("full")
        calls = _finished_calls(c)

        c.on_youtube_done(False)
        c.consume_auto_article()
        c.on_generate_all_finished()

        assert calls == [(True, {"article"})]

    def test_ai_error_on_article_step_reports_chain_internal_and_finishes(self):
        c = PresetChainController()
        c.start("article")
        calls = _finished_calls(c)

        assert c.consume_auto_article() is True
        handled = c.on_ai_error()

        assert handled is True
        # Matches the pre-extraction behaviour exactly: unlike a failed
        # youtube step, on_ai_error() does not discard "article" from
        # `ran` — only had_error is set. _finish_preset_chain only ever
        # acts on this when `saved` is non-empty (get_articles() is empty
        # after a real failure), so this asymmetry is inert in practice,
        # not something this extraction should silently change.
        assert calls == [(True, {"article"})]
        assert not c.is_active()

    def test_ai_error_outside_a_chain_is_not_swallowed(self):
        """A standalone "Clean" or "Generate" action failing (no chain
        running) must not be reported as chain-internal."""
        c = PresetChainController()
        assert c.on_ai_error() is False
        assert not c.is_active()


class TestYoutubeDoneOutsideChain:
    def test_ignored_when_youtube_not_pending(self):
        """generation_finished can fire from a manual (non-chain) YouTube
        tab click; the controller must not react to it."""
        c = PresetChainController()
        calls = _finished_calls(c)
        c.on_youtube_done(True)
        assert calls == []
        assert not c.is_active()


class TestConsumeAutoArticle:
    def test_one_shot(self):
        c = PresetChainController()
        c.start("article")
        assert c.consume_auto_article() is True
        assert c.consume_auto_article() is False

    def test_false_when_no_article_step(self):
        c = PresetChainController()
        c.start("youtube")
        assert c.consume_auto_article() is False


class TestCancel:
    def test_cancel_clears_pending_and_auto_article(self):
        c = PresetChainController()
        c.start("full")
        assert c.is_active()
        c.cancel()
        assert not c.is_active()

    def test_cancel_then_youtube_done_is_a_no_op(self):
        c = PresetChainController()
        c.start("youtube")
        c.cancel()
        calls = _finished_calls(c)
        c.on_youtube_done(True)
        assert calls == []
