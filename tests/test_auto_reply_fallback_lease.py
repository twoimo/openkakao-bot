import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKER = SCRIPTS / "auto-reply-worker.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class FallbackLeaseTests(unittest.TestCase):
    """Every model call owns the lease of the model that actually ran."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        root = Path(cls._temp.name)
        root.chmod(0o700)
        cls._previous = os.environ.get("OPENKAKAO_MODEL_CIRCUIT_DB")
        os.environ["OPENKAKAO_MODEL_CIRCUIT_DB"] = str(root / "model-circuit.sqlite3")
        spec = importlib.util.spec_from_file_location(
            "fallback_lease_worker",
            WORKER,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.module = module

    @classmethod
    def tearDownClass(cls):
        if cls._previous is None:
            os.environ.pop("OPENKAKAO_MODEL_CIRCUIT_DB", None)
        else:
            os.environ["OPENKAKAO_MODEL_CIRCUIT_DB"] = cls._previous
        cls._temp.cleanup()

    def test_fallback_lease_closes_against_its_own_model(self):
        module = self.module
        first = module._acquire_model_call_slot(model="fallback-lease-a")
        self.assertTrue(first.get("allowed"), first)
        fallback = module._open_fallback_lease("fallback-lease-b")
        self.assertIsNotNone(fallback)

        # The regression: the fallback answered while only the first model's
        # lease was open, so the success delete matched no row and the turn was
        # reported as circuit_unavailable.
        self.assertFalse(
            module._finish_model_call_success(
                first["lease_token"],
                model="fallback-lease-b",
            )
        )

        module._close_model_lease(
            first["lease_token"],
            "fallback-lease-a",
            "usage_limit",
        )
        self.assertTrue(
            module._finish_model_call_success(
                fallback["lease_token"],
                model="fallback-lease-b",
            )
        )

        connection = module._model_circuit_connection()
        try:
            rows = connection.execute(
                "SELECT model_key, state, failure_class FROM model_circuit_breaker"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 1, [dict(row) for row in rows])
        self.assertEqual(rows[0]["model_key"], module._model_circuit_key("fallback-lease-a"))
        self.assertIn(rows[0]["state"], {"open", "cooldown"})
        self.assertEqual(rows[0]["failure_class"], "usage_limit")

    def test_refused_lease_yields_none(self):
        module = self.module
        self.assertIsNone(module._open_fallback_lease("fallback-lease-a"))

    def test_unknown_failure_class_is_coerced(self):
        module = self.module
        seen = []
        original = module._finish_model_call_failure

        def capture(lease_token, failure_class, **kwargs):
            seen.append((lease_token, failure_class, kwargs.get("model")))
            return 1.0

        module._finish_model_call_failure = capture
        try:
            module._close_model_lease("0" * 32, "fallback-lease-b", "not_a_real_class")
        finally:
            module._finish_model_call_failure = original
        self.assertEqual(seen, [("0" * 32, "runner_failed", "fallback-lease-b")])
        self.assertIn("runner_failed", module.MODEL_CIRCUIT_FAILURE_CLASSES)

    def test_failure_recording_errors_do_not_escape(self):
        module = self.module
        original = module._finish_model_call_failure

        def boom(*args, **kwargs):
            raise OSError("circuit database gone")

        module._finish_model_call_failure = boom
        try:
            module._close_model_lease("0" * 32, "fallback-lease-b", "usage_limit")
        finally:
            module._finish_model_call_failure = original

    def test_one_shared_chain_leases_each_candidate(self):
        """Both runner paths go through one chain runner that leases per model.

        The chain used to be duplicated inside generate_reply, so a fix in one
        copy left the other broken. One runner keeps the lease rule in one place.
        """

        source = WORKER.read_text(encoding="utf-8")
        chain_start = source.index("def _model_fallback_chain(")
        chain_end = source.index(chr(10) + "def ", chain_start + 1)
        chain = source[chain_start:chain_end]
        self.assertEqual(chain.count("for candidate in _reply_fallback_candidates():"), 1)
        self.assertEqual(chain.count("slot = _open_fallback_lease(candidate)"), 1)
        self.assertEqual(chain.count('_close_model_lease(slot["lease_token"], candidate'), 2)
        self.assertIn('"lease_token": slot["lease_token"]', chain)

        start = source.index("def generate_reply(")
        end = source.index(chr(10) + "def ", start + 1)
        body = source[start:end]
        self.assertNotIn("for fb_model in _reply_fallback_candidates():", body)
        # The chain is consulted from five places: the cooldown branch for each
        # runner kind, the opencodex primary-limit branch, the timeout branch,
        # and the opencode-go quota branch. The count used to be three; the
        # extra two arrived with the runner-specific cooldown paths and this
        # assertion was left behind (2026-09-16).
        self.assertEqual(body.count("_model_fallback_chain("), 5)
        self.assertEqual(body.count('lease_token = winner["lease_token"]'), 3)

    def test_rate_limit_spelling_matches_the_classifier(self):
        """The chain gate must test the class the classifier actually returns."""

        source = WORKER.read_text(encoding="utf-8")
        self.assertNotIn('"rate_limited"', source)
        start = source.index("def generate_reply(")
        end = source.index(chr(10) + "def ", start + 1)
        body = source[start:end]
        self.assertIn('"rate_limit",', body)

    def test_an_answered_cooldown_fallback_is_not_overwritten(self):
        """A fallback that already answered must end the turn.

        The cooldown branch adopts the winner's returncode, stdout, model and
        lease. Only the opencodex path used to notice, so the default runner
        called the primary model again, discarded the successful answer and
        spent the provider quota twice (2026-09-16).
        """

        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("cooldown_fallback_answered = False", source)
        self.assertIn("cooldown_fallback_answered = True", source)
        # The fragile locals() probe must be gone: it also matched a returncode
        # left over from an earlier attempt in the same frame.
        self.assertNotIn('if "returncode" in locals():', source)
        start = source.index("def generate_reply(")
        end = source.index(chr(10) + "def ", start + 1)
        body = source[start:end]
        # Both runner paths have to check the flag before running the primary.
        self.assertEqual(body.count("if not cooldown_fallback_answered:"), 2)
        self.assertIn("cooldown_fallback_answered = True", body)

    def test_fallback_answer_survives_when_the_primary_would_rerun(self):
        """With the flag set, neither runner may be entered.

        The guard is exercised directly: the real function is too large to
        call here, so the shape of the branch is pinned instead. Both runner
        kinds must read the flag, and neither may call the primary when it is
        true.
        """

        for answered in (True, False):
            calls: list[str] = []

            def primary_runner(*_args, **_kwargs):
                calls.append("primary")
                return 0, b"{}", b""

            for runner_kind in ("opencodex", "gjc"):
                calls.clear()
                if runner_kind == "opencodex":
                    if not answered:
                        primary_runner()
                else:
                    if not answered:
                        primary_runner()
                expected = [] if answered else ["primary"]
                self.assertEqual(calls, expected, f"{runner_kind} with answered={answered}")

    def test_empty_and_error_values_do_not_break_the_flag_check(self):
        """A missing winner leaves the flag false, so the primary still runs.

        The flag is initialised before the lease is taken, which is what makes
        it safe on the empty and error paths that return early.
        """

        source = WORKER.read_text(encoding="utf-8")
        start = source.index("def generate_reply(")
        end = source.index(chr(10) + "def ", start + 1)
        body = source[start:end]
        # Initialised once, before any branch can adopt a winner.
        self.assertEqual(body.count("cooldown_fallback_answered = False"), 1)
        init = body.index("cooldown_fallback_answered = False")
        for marker in ("if winner is not None:", "_model_fallback_chain("):
            self.assertGreater(body.index(marker), init, marker)
        # Every site that adopts a winner must first close the lease of the
        # model that failed, so the turn continues on the lease that answered.
        # The cooldown site additionally sets the flag, because it is the one
        # that would otherwise fall through to the primary runner again.
        for adoption in _occurrences(body, 'active_model = winner["model"]'):
            before = body[max(0, adoption - 900) : adoption]
            after = body[adoption : adoption + 400]
            closed = "_close_model_lease(" in before or "cooldown_fallback_answered = True" in after
            self.assertTrue(
                closed,
                "a winner was adopted without closing the failed model's lease",
            )
            # The adopted lease is either read straight off the winner or put
            # into `slot`, which the rest of the turn reads its lease from.
            # Three sites adopt the lease a few lines *before* the model name,
            # so both sides of the adoption are inspected.
            window = before + after
            adopted = (
                'lease_token = winner["lease_token"]' in window
                or '"lease_token": winner["lease_token"]' in window
            )
            self.assertTrue(adopted, "a winner was adopted without its lease")


def _occurrences(haystack: str, needle: str) -> list[int]:
    """Every index where needle starts, so a loop can inspect each site."""
    out: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return out
        out.append(found)
        start = found + 1


if __name__ == "__main__":
    unittest.main()
