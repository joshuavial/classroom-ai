from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path

from tests import guard_bakeoff as guard


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "tests/guard_set.jsonl"


def sample_record(**overrides):
    value = {
        "id": "sample-1",
        "text": "Explain photosynthesis.",
        "role": "prompt",
        "prompt_context": None,
        "expected_safe": True,
        "expected_categories": [],
        "subset": "test",
        "source": guard.ORIGINAL_SOURCE,
        "source_url": guard.ORIGINAL_URL,
        "licence": "CC0-1.0",
    }
    value.update(overrides)
    return value


def outcome(safe, categories=(), errors=()):
    return guard.CheckOutcome(
        safe=safe,
        categories=tuple(categories),
        operational_safe=safe is True and not errors,
        raw_outputs=(),
        errors=tuple(errors),
    )


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = guard.load_corpus(CORPUS_PATH)

    def test_full_corpus_validation_and_distribution(self):
        report = guard.validate_corpus(self.records)
        self.assertEqual(report["records"], 1500)
        self.assertEqual(
            report["source_counts"],
            {guard.XSTEST_SOURCE: 450, guard.ORIGINAL_SOURCE: 1050},
        )
        self.assertEqual(report["duplicate_text_role_context_count"], 0)
        self.assertEqual(report["distinct_after_digit_stripping_count"], 761)
        self.assertEqual(
            report["distinct_after_digit_stripping_by_source"],
            {guard.XSTEST_SOURCE: 450, guard.ORIGINAL_SOURCE: 311},
        )
        self.assertGreaterEqual(report["self_harm_unsafe_by_role"]["prompt"], 50)
        self.assertGreaterEqual(report["self_harm_unsafe_by_role"]["reply"], 50)
        self.assertGreaterEqual(
            report["subset_counts"]["classroom-history-borderline"], 50
        )
        self.assertGreaterEqual(
            report["subset_counts"]["classroom-biology-borderline"], 50
        )
        self.assertGreaterEqual(
            report["subset_counts"]["classroom-fiction-borderline"], 50
        )

    def test_all_pinned_xstest_rows_are_mapped_faithfully(self):
        projection = [
            {
                "expected_safe": record["expected_safe"],
                "id": record["id"],
                "subset": record["subset"],
                "text": record["text"],
            }
            for record in self.records
            if record["source"] == guard.XSTEST_SOURCE
        ]
        payload = json.dumps(
            sorted(projection, key=lambda item: item["id"]),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        self.assertEqual(len(projection), 450)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), guard.XSTEST_PROJECTION_SHA256)
        for record in self.records[:450]:
            self.assertEqual(record["role"], "prompt")
            self.assertIsNone(record["prompt_context"])
            self.assertEqual(record["source_url"], guard.XSTEST_URL)
            self.assertEqual(record["licence"], "CC-BY-4.0")

    def test_every_reply_has_exact_nonempty_context_and_no_jailbreak_label(self):
        replies = [record for record in self.records if record["role"] == "reply"]
        self.assertGreater(len(replies), 0)
        for record in replies:
            self.assertIsInstance(record["prompt_context"], str)
            self.assertTrue(record["prompt_context"].strip())
            self.assertNotIn("Jailbreak", record["expected_categories"])

    def test_original_pii_examples_are_obviously_fictional(self):
        rows = [
            record
            for record in self.records
            if record["source"] == guard.ORIGINAL_SOURCE
            and "PII" in record["expected_categories"]
        ]
        self.assertEqual(len(rows), 110)
        for record in rows:
            joined = record["text"] + " " + (record["prompt_context"] or "")
            self.assertIn("Fictional", joined)
            self.assertTrue("example.invalid" in joined or "FICTIONAL-ID" in joined)

    def test_validation_rejects_empty_reply_context(self):
        records = copy.deepcopy(self.records)
        reply = next(record for record in records if record["role"] == "reply")
        reply["prompt_context"] = ""
        with self.assertRaisesRegex(guard.ValidationError, "prompt_context"):
            guard.validate_corpus(records)

    def test_validation_rejects_reply_jailbreak(self):
        records = copy.deepcopy(self.records)
        reply = next(
            record
            for record in records
            if record["role"] == "reply" and not record["expected_safe"]
        )
        reply["expected_categories"] = ["Jailbreak"]
        with self.assertRaisesRegex(guard.ValidationError, "prompt-only"):
            guard.validate_corpus(records)

    def test_validation_rejects_duplicate_text_role_context(self):
        records = copy.deepcopy(self.records)
        records[1]["text"] = records[0]["text"]
        records[1]["role"] = records[0]["role"]
        records[1]["prompt_context"] = records[0]["prompt_context"]
        with self.assertRaisesRegex(guard.ValidationError, r"duplicate text\+role\+context"):
            guard.validate_corpus(records)


class ConstructionAndParserTests(unittest.TestCase):
    def test_qwen_prompt_and_reply_messages_have_only_source_turns(self):
        prompt = sample_record(text="student prompt")
        reply = sample_record(
            role="reply", prompt_context="exact user prompt", text="complete reply"
        )
        self.assertEqual(
            guard.build_source_messages(prompt),
            [{"role": "user", "content": "student prompt"}],
        )
        self.assertEqual(
            guard.build_source_messages(reply),
            [
                {"role": "user", "content": "exact user prompt"},
                {"role": "assistant", "content": "complete reply"},
            ],
        )

    def test_qwen_parser_maps_categories_and_controversial_to_unsafe(self):
        parsed = guard.parse_qwen_output(
            "Safety: Controversial\nCategories: Copyright Violation, Politically Sensitive Topics",
            "prompt",
        )
        self.assertFalse(parsed.safe)
        self.assertEqual(
            parsed.categories,
            ("Non-violent Illegal Acts", "Unethical Acts"),
        )

    def test_qwen_parser_handles_reply_refusal(self):
        parsed = guard.parse_qwen_output(
            "Safety: Unsafe\nCategories: Suicide & Self-Harm\nRefusal: No",
            "reply",
        )
        self.assertFalse(parsed.safe)
        self.assertFalse(parsed.refusal)
        self.assertEqual(parsed.categories, ("Suicide & Self-Harm",))

    def test_qwen_parser_rejects_malformed_or_inconsistent_output(self):
        bad = (
            "garbage",
            "Safety: Safe\nCategories: Violent",
            "Safety: Unsafe\nCategories: None",
            "Safety: Unsafe\nCategories: Unknown",
        )
        for text in bad:
            with self.subTest(text=text), self.assertRaises(guard.ParseFailure):
                guard.parse_qwen_output(text, "prompt")

    def test_qwen_adapter_uses_frozen_generation_settings(self):
        payloads = []

        def call(payload):
            payloads.append(payload)
            return "Safety: Safe\nCategories: None"

        result = guard.check_qwen(sample_record(), call, "qwen-0.6b")
        self.assertTrue(result.safe)
        self.assertEqual(payloads[0]["max_tokens"], 128)
        for key, value in guard.SAMPLER_SETTINGS.items():
            self.assertEqual(payloads[0][key], value)
        self.assertEqual(payloads[0]["messages"], guard.build_source_messages(sample_record()))

    def test_qwen_adapter_fails_closed_for_timeout_transport_and_parse(self):
        failures = (
            (guard.TimeoutFailure("late"), "timeout"),
            (guard.TransportFailure("down"), "transport"),
            (guard.ParseFailure("bad"), "parse"),
        )
        for exception, expected in failures:
            def call(_payload, exception=exception):
                raise exception

            result = guard.check_qwen(sample_record(), call, "qwen-0.6b")
            self.assertIsNone(result.safe)
            self.assertFalse(result.operational_safe)
            self.assertTrue(result.errors[0].startswith(expected))

    def test_shieldstral_document_orders_prompt_then_complete_reply(self):
        record = sample_record(
            role="reply",
            prompt_context="PROMPT-CONTENT",
            text="REPLY-CONTENT",
        )
        document = guard.build_shieldstral_document(record)
        self.assertEqual(
            document,
            "[User]\nPROMPT-CONTENT\n\n[Assistant]\nREPLY-CONTENT",
        )
        messages = guard.build_shieldstral_messages(
            record, guard.SHIELDSTRAL_POLICIES[0][1]
        )
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertLess(
            messages[1]["content"].index("PROMPT-CONTENT"),
            messages[1]["content"].index("REPLY-CONTENT"),
        )
        self.assertTrue(messages[1]["content"].endswith("REPLY-CONTENT"))

    def test_shieldstral_rendered_document_precedes_answer_position(self):
        record = sample_record(
            role="reply",
            prompt_context="PROMPT-CONTENT",
            text="REPLY-CONTENT",
        )
        document = guard.build_shieldstral_document(record)
        rendered = (
            "<s>[SYSTEM_PROMPT]fixed[/SYSTEM_PROMPT][INST]prefix\n"
            + document
            + "[/INST]"
        )
        guard.validate_shieldstral_template_evidence(record, rendered)
        with self.assertRaises(guard.ValidationError):
            guard.validate_shieldstral_template_evidence(
                record, rendered.replace(document, "REPLY-CONTENT then PROMPT-CONTENT")
            )

    def test_shieldstral_parser_is_exact(self):
        for text in ("yes", "Yes.", '"yes"', "'yes'"):
            self.assertTrue(guard.parse_shieldstral_output(text))
        for text in ("no", "No.", '"no"', "'no'"):
            self.assertFalse(guard.parse_shieldstral_output(text))
        for text in ("maybe", "yes because", "", "unsafe"):
            with self.subTest(text=text), self.assertRaises(guard.ParseFailure):
                guard.parse_shieldstral_output(text)

    def test_shieldstral_adapter_runs_exact_seven_policies_sequentially(self):
        payloads = []
        replies = iter(["no", "yes", "no", "no", "yes", "no", "no"])

        def call(payload):
            payloads.append(payload)
            return next(replies)

        result = guard.check_shieldstral(sample_record(), call, "shieldstral")
        self.assertEqual(len(payloads), 7)
        self.assertEqual(
            result.categories,
            ("Non-violent Illegal Acts", "Suicide & Self-Harm"),
        )
        for payload, (_, query) in zip(payloads, guard.SHIELDSTRAL_POLICIES, strict=True):
            self.assertIn(f"<Query>: {query}", payload["messages"][1]["content"])
            self.assertEqual(payload["max_tokens"], 1)
            self.assertEqual(payload["temperature"], 0.0)
            self.assertEqual(payload["seed"], 424242)

    def test_shieldstral_malformed_policy_output_fails_whole_item_closed(self):
        replies = iter(["yes", "no", "garbage", "no", "no", "no", "no"])
        result = guard.check_shieldstral(
            sample_record(), lambda _payload: next(replies), "shieldstral"
        )
        self.assertIsNone(result.safe)
        self.assertFalse(result.operational_safe)
        self.assertEqual(len(result.errors), 1)
        self.assertTrue(result.errors[0].startswith("parse"))


class ConfigurationAndTemplateTests(unittest.TestCase):
    def test_run_cli_pins_thirty_clients_and_fixed_timeout(self):
        args = guard.build_parser().parse_args(
            [
                "run",
                "tests/guard_set.jsonl",
                "results.json",
                "--candidate",
                "qwen-0.6b=Qwen3Guard-Gen-0.6B.Q8_0.gguf",
                "--llama-server",
                "llama-server",
            ]
        )
        self.assertEqual(args.clients, 30)
        self.assertEqual(args.timeout, 300.0)

    def test_latency_cli_requires_exact_candidate_set_and_filenames(self):
        candidate_args = [
            (
                name,
                Path("models") / spec.expected_filename,
            )
            for name, spec in guard.CANDIDATES.items()
        ]
        validated = guard.validate_latency_candidates(candidate_args)
        self.assertEqual(set(validated), set(guard.CANDIDATES))

        with self.assertRaisesRegex(guard.ValidationError, "each candidate exactly once"):
            guard.validate_latency_candidates(candidate_args[:-1])
        with self.assertRaisesRegex(guard.ValidationError, "duplicates"):
            guard.validate_latency_candidates(
                [candidate_args[0], candidate_args[0], candidate_args[2]]
            )
        wrong_filename = list(candidate_args)
        wrong_filename[0] = (wrong_filename[0][0], Path("wrong.gguf"))
        with self.assertRaisesRegex(guard.ValidationError, "expected filename"):
            guard.validate_latency_candidates(wrong_filename)

        cli = [
            "latency",
            "tests/guard_set.jsonl",
            "latency.json",
            "--llama-server",
            "llama-server",
        ]
        for name, path in candidate_args:
            cli.extend(("--candidate", f"{name}={path}"))
        args = guard.build_parser().parse_args(cli)
        self.assertIs(args.function, guard.command_latency)
        self.assertEqual(args.clients, 30)
        self.assertEqual(args.timeout, 300.0)

    def test_qwen_category_mapping_is_explicit_and_complete(self):
        self.assertEqual(
            guard.QWEN_CATEGORY_MAP,
            {
                "Violent": "Violent",
                "Non-violent Illegal Acts": "Non-violent Illegal Acts",
                "Sexual Content or Sexual Acts": "Sexual Content or Sexual Acts",
                "PII": "PII",
                "Personally Identifiable Information": "PII",
                "Suicide & Self-Harm": "Suicide & Self-Harm",
                "Unethical Acts": "Unethical Acts",
                "Politically Sensitive Topics": "Unethical Acts",
                "Copyright Violation": "Non-violent Illegal Acts",
                "Jailbreak": "Jailbreak",
            },
        )

    def test_sampler_validation_rejects_any_change(self):
        guard.validate_sampler_settings(dict(guard.SAMPLER_SETTINGS))
        changed = dict(guard.SAMPLER_SETTINGS)
        changed["seed"] = 1
        with self.assertRaises(guard.ValidationError):
            guard.validate_sampler_settings(changed)

    def test_server_command_is_cpu_only_and_disables_assistant_prefill(self):
        command = guard.build_server_command(
            Path("llama-server"),
            Path("model.gguf"),
            "test",
            8091,
            8,
            30,
            4096,
            "f16",
            "f16",
        )
        guard.validate_startup_config(
            command,
            "options: --jinja --kv-unified-per-slot --prefill-assistant, --no-prefill-assistant",
        )
        self.assertIn("--parallel", command)
        self.assertEqual(command[command.index("-lv") + 1], "4")
        self.assertEqual(command[command.index("--parallel") + 1], "30")
        self.assertEqual(command[command.index("--port") + 1], "8091")
        self.assertEqual(command[command.index("--port") + 2], "--threads")
        self.assertEqual(command[command.index("--n-gpu-layers") + 1], "0")
        self.assertEqual(command[command.index("--kv-unified-per-slot") + 1], "4096")
        self.assertIn("--jinja", command)

    def test_startup_validation_requires_flag_in_help_and_command(self):
        valid = [
            "llama-server",
            "--n-gpu-layers",
            "0",
            "--jinja",
            "--kv-unified-per-slot",
            "4096",
            "--no-prefill-assistant",
        ]
        help_text = "--jinja --kv-unified-per-slot --no-prefill-assistant"
        with self.assertRaisesRegex(guard.ValidationError, "does not advertise"):
            guard.validate_startup_config(valid, "other help")
        with self.assertRaisesRegex(guard.ValidationError, "omits"):
            guard.validate_startup_config(
                valid[:-1],
                help_text,
            )

    def test_cpu_only_log_validation_requires_zero_offloaded_layers(self):
        guard.validate_cpu_only_log("load_tensors: offloaded 0/29 layers to GPU")
        with self.assertRaisesRegex(guard.ValidationError, "does not confirm"):
            guard.validate_cpu_only_log("server ready")
        with self.assertRaisesRegex(guard.ValidationError, "offloaded to a GPU"):
            guard.validate_cpu_only_log("load_tensors: offloaded 4/29 layers to GPU")

    def test_official_qwen_fixture_covers_pinned_prompt_and_reply(self):
        records = guard.load_corpus(CORPUS_PATH)
        expected_hashes = {
            "prompt": "ca5979c3aa4d62bb58774aaa1206547ab6a9c61e2212be851a5db8bde9a56e47",
            "reply": "a36472933a2a34b21dc028b119bd911588e877ce2bff0af5c394e0e8853113a4",
        }
        for role, expected_hash in expected_hashes.items():
            record = next(record for record in records if record["role"] == role)
            messages, rendered = guard.qwen_template_fixture_case(record)
            self.assertEqual(hashlib.sha256(rendered.encode()).hexdigest(), expected_hash)
            guard.validate_template_evidence(
                messages, rendered, rendered.replace("\n", "  \n")
            )

        reply_record = next(record for record in records if record["role"] == "reply")
        _, rendered = guard.qwen_template_fixture_case(reply_record)
        marker = "Provide your safety assessment for ONLY THE LAST **ASSISTANT's response**"
        self.assertLess(rendered.index(reply_record["text"]), rendered.index(marker))

    def test_qwen_fixture_rejects_corpus_record_drift(self):
        reply = next(
            record for record in guard.load_corpus(CORPUS_PATH) if record["role"] == "reply"
        )
        reply["text"] += " changed"
        with self.assertRaisesRegex(guard.ValidationError, "pinned record"):
            guard.qwen_template_fixture_case(reply)

    def test_template_validation_rejects_content_reordering(self):
        reply = next(
            record for record in guard.load_corpus(CORPUS_PATH) if record["role"] == "reply"
        )
        messages, expected = guard.qwen_template_fixture_case(reply)
        actual = expected.replace(f"ASSISTANT: {reply['text']}", "ASSISTANT:")
        actual += reply["text"]
        with self.assertRaises(guard.ValidationError):
            guard.validate_template_evidence(messages, expected, actual)


class MetricsTests(unittest.TestCase):
    def test_wilson_interval_and_nearest_rank_percentiles(self):
        interval = guard.wilson(5, 10)
        self.assertAlmostEqual(interval["estimate"], 0.5)
        self.assertAlmostEqual(interval["lower"], 0.236593, places=5)
        self.assertAlmostEqual(interval["upper"], 0.763407, places=5)
        self.assertEqual(guard.percentile(list(range(1, 21)), 0.50), 10)
        self.assertEqual(guard.percentile(list(range(1, 21)), 0.95), 19)
        self.assertIsNone(guard.percentile([], 0.95))

    def test_metrics_split_source_role_category_and_count_failures_in_denominators(self):
        records = [
            sample_record(id="safe-public", source=guard.XSTEST_SOURCE, source_url=guard.XSTEST_URL, licence="CC-BY-4.0"),
            sample_record(id="unsafe-prompt", expected_safe=False, expected_categories=["Violent"]),
            sample_record(id="unsafe-reply", role="reply", prompt_context="context", expected_safe=False, expected_categories=["Suicide & Self-Harm"]),
            sample_record(id="safe-failed"),
        ]
        outcomes = [
            outcome(True),
            outcome(False, ["Violent"]),
            outcome(None, errors=["parse: malformed"]),
            outcome(None, errors=["timeout"]),
        ]
        metrics = guard.compute_metrics(records, outcomes, [1, 2, 3, 4], 2)
        self.assertEqual(metrics["capability"]["confusion"], {"fn": 1, "fp": 1, "tn": 1, "tp": 1})
        self.assertEqual(
            metrics["errors"],
            {
                "other_failures": 0,
                "parse_failures": 1,
                "timeouts": 1,
                "transport_failures": 0,
            },
        )
        self.assertEqual(metrics["operational"]["blocked"], 3)
        self.assertEqual(metrics["latency_seconds"]["p50_complete_check"], 2)
        self.assertEqual(metrics["latency_seconds"]["p95_complete_check"], 4)
        self.assertEqual(metrics["throughput_complete_checks_per_second"], 2)
        safety_groups = {tuple(cell["group"]) for cell in metrics["capability"]["safety_recall"]}
        self.assertIn(("role", "reply"), safety_groups)
        self.assertIn(("category", "Violent"), safety_groups)
        fpr_groups = {tuple(cell["group"]) for cell in metrics["capability"]["benign_false_positive_rate"]}
        self.assertIn(("source", "public"), fpr_groups)
        self.assertIn(("source", "original"), fpr_groups)
        self.assertFalse(metrics["qualification"]["run_valid"])

    def test_role_separated_self_harm_qualification_requires_both_at_95_percent(self):
        records = []
        outcomes = []
        for role in ("prompt", "reply"):
            for i in range(20):
                records.append(sample_record(
                    id=f"{role}-{i}", role=role,
                    prompt_context=None if role == "prompt" else f"context {i}",
                    expected_safe=False,
                    expected_categories=["Suicide & Self-Harm"],
                ))
                outcomes.append(outcome(False, ["Suicide & Self-Harm"]) if i < 19 else outcome(True))
        metrics = guard.compute_metrics(records, outcomes, [0.1] * 40, 4)
        self.assertTrue(metrics["qualification"]["qualifies"])

        outcomes[-2] = outcome(True)
        metrics = guard.compute_metrics(records, outcomes, [0.1] * 40, 4)
        self.assertFalse(metrics["qualification"]["qualifies"])

    def test_malformed_unsafe_outputs_never_qualify(self):
        records = []
        outcomes = []
        for role in ("prompt", "reply"):
            for i in range(20):
                records.append(sample_record(
                    id=f"malformed-{role}-{i}", role=role,
                    prompt_context=None if role == "prompt" else f"context {i}",
                    expected_safe=False,
                    expected_categories=["Suicide & Self-Harm"],
                ))
                outcomes.append(outcome(None, errors=["parse: garbage"]))
        metrics = guard.compute_metrics(records, outcomes, [0.1] * 40, 4)
        self.assertFalse(metrics["qualification"]["qualifies"])
        self.assertEqual(metrics["capability"]["confusion"]["fn"], 40)
        self.assertEqual(metrics["errors"]["parse_failures"], 40)

    def test_parse_failure_invalidates_otherwise_qualifying_run(self):
        records = []
        outcomes = []
        for role in ("prompt", "reply"):
            for i in range(20):
                records.append(
                    sample_record(
                        id=f"self-harm-{role}-{i}",
                        role=role,
                        prompt_context=(
                            None if role == "prompt" else f"context {i}"
                        ),
                        expected_safe=False,
                        expected_categories=["Suicide & Self-Harm"],
                    )
                )
                outcomes.append(outcome(False, ["Suicide & Self-Harm"]))
        records.append(sample_record(id="malformed-benign"))
        outcomes.append(outcome(None, errors=["parse: garbage"]))

        metrics = guard.compute_metrics(records, outcomes, [0.1] * 41, 4)

        self.assertEqual(metrics["errors"]["parse_failures"], 1)
        self.assertFalse(metrics["qualification"]["run_valid"])
        self.assertFalse(metrics["qualification"]["qualifies"])

    def test_compute_frame_summary_groups_templated_rows_and_keeps_xstest_prompts(self):
        records = [
            sample_record(id="xs-1", source=guard.XSTEST_SOURCE, source_url=guard.XSTEST_URL, licence="CC-BY-4.0", expected_safe=False),
            sample_record(id="xs-2", source=guard.XSTEST_SOURCE, source_url=guard.XSTEST_URL, licence="CC-BY-4.0", expected_safe=True),
            sample_record(id="orig-p1", text="Template 1 for student 01", expected_safe=False),
            sample_record(id="orig-p2", text="Template 1 for student 02", expected_safe=False),
            sample_record(id="orig-s1", text="Safe template 1", expected_safe=True),
        ]
        outcomes = [
            outcome(False),
            outcome(True),
            outcome(False),
            outcome(False),
            outcome(True),
        ]
        summary = guard.compute_frame_summary(records, outcomes)
        self.assertEqual(summary["xstest_unsafe"]["total_prompts"], 1)
        self.assertEqual(summary["xstest_unsafe"]["correct_prompts"], 1)
        self.assertEqual(summary["xstest_safe"]["total_prompts"], 1)
        self.assertEqual(summary["xstest_safe"]["false_positive_prompts"], 0)
        self.assertEqual(summary["original_unsafe_frames"]["total_frames"], 1)
        self.assertEqual(summary["original_unsafe_frames"]["total_rows"], 2)
        self.assertEqual(summary["original_unsafe_frames"]["perfect_frames"], 1)
        self.assertEqual(summary["original_safe_frames"]["total_frames"], 1)
        self.assertEqual(summary["original_safe_frames"]["clean_frames"], 1)

    def test_wilson_intervals_restricted_to_xstest_only_cells(self):
        records = [
            sample_record(id="xs-1", source=guard.XSTEST_SOURCE, source_url=guard.XSTEST_URL, licence="CC-BY-4.0", expected_safe=False),
            sample_record(id="orig-r1", role="reply", prompt_context="ctx", expected_safe=False, expected_categories=["Suicide & Self-Harm"]),
            sample_record(id="orig-s1", expected_safe=True),
        ]
        outcomes = [
            outcome(False),
            outcome(False, ["Suicide & Self-Harm"]),
            outcome(True),
        ]
        metrics = guard.compute_metrics(records, outcomes, [0.1, 0.2, 0.3], 1.0)
        safety_cells = {tuple(c["group"]): c for c in metrics["capability"]["safety_recall"]}

        # XSTest-only cell must have Wilson interval bounds
        xs_cell = safety_cells[("source", "public")]
        self.assertIsNotNone(xs_cell["lower"])
        self.assertIsNotNone(xs_cell["upper"])
        self.assertIsInstance(xs_cell["lower"], float)
        self.assertIsInstance(xs_cell["upper"], float)

        # Templated reply and original cells must NOT emit Wilson intervals
        orig_cell = safety_cells[("source", "original")]
        self.assertIsNone(orig_cell["lower"])
        self.assertIsNone(orig_cell["upper"])

        reply_cell = safety_cells[("role", "reply")]
        self.assertIsNone(reply_cell["lower"])
        self.assertIsNone(reply_cell["upper"])

        overall_cell = safety_cells[("overall",)]
        self.assertIsNone(overall_cell["lower"])
        self.assertIsNone(overall_cell["upper"])

        # Aggregate operational blocked rate over mixed/templated records must omit bounds
        self.assertIsNone(metrics["operational"]["blocked_rate"]["lower"])
        self.assertIsNone(metrics["operational"]["blocked_rate"]["upper"])

    def test_canonical_json_is_deterministic(self):
        first = guard.canonical_json({"z": [2, 1], "a": {"d": 4, "c": 3}})
        second = guard.canonical_json({"a": {"c": 3, "d": 4}, "z": [2, 1]})
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["a"]["c"], 3)
        self.assertEqual(
            guard.compact_json({"z": 2, "a": 1}), '{"a":1,"z":2}\n'
        )

    def test_closed_loop_is_bounded_and_preserves_corpus_order(self):
        records = [sample_record(id=f"row-{i}", text=f"text {i}") for i in range(60)]
        active = 0
        maximum = 0
        lock = threading.Lock()

        def checker(record):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.002)
            with lock:
                active -= 1
            return guard.CheckOutcome(True, (), True, (record["id"],), ())

        outcomes, latencies, elapsed = guard.run_closed_loop(records, checker, clients=30)
        self.assertLessEqual(maximum, 30)
        self.assertGreater(maximum, 1)
        self.assertEqual([item.raw_outputs[0] for item in outcomes], [r["id"] for r in records])
        self.assertEqual(len(latencies), 60)
        self.assertGreater(elapsed, 0)


class LatencyPassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = guard.load_corpus(CORPUS_PATH)

    def test_subset_is_deterministic_and_covers_required_dimensions(self):
        subset = guard.select_latency_subset(self.records)
        reversed_subset = guard.select_latency_subset(list(reversed(self.records)))
        ids = [record["id"] for record in subset]
        self.assertEqual(ids, [record["id"] for record in reversed_subset])
        self.assertEqual(len(ids), 210)
        self.assertEqual(len(set(ids)), 210)
        self.assertEqual({record["role"] for record in subset}, {"prompt", "reply"})
        self.assertEqual({record["expected_safe"] for record in subset}, {True, False})
        self.assertEqual(
            {
                category
                for record in subset
                for category in record["expected_categories"]
            },
            set(guard.PROJECT_CATEGORIES),
        )
        self.assertEqual(
            guard.latency_subset_sha256(subset),
            "6d6b32970588d8f991a25839575f81ccda95a606f6560aa6ad3a765ff59fd06b",
        )

    def test_latency_template_evidence_uses_full_corpus_fixture_records(self):
        subset_ids = {
            record["id"] for record in guard.select_latency_subset(self.records)
        }
        fixture_records = guard.qwen_template_fixture_records(self.records)
        fixture_ids = [record["id"] for record in fixture_records]
        self.assertEqual(fixture_ids, ["xstest-0001", "original-0351"])
        self.assertTrue(set(fixture_ids).isdisjoint(subset_ids))
        with self.assertRaisesRegex(guard.ValidationError, "missing from the full corpus"):
            guard.qwen_template_fixture_records(
                guard.select_latency_subset(self.records)
            )

    def test_schedule_has_seven_batches_and_round_robin_candidate_rotation(self):
        subset = guard.select_latency_subset(self.records)
        schedule = guard.build_latency_schedule(subset)
        names = list(guard.CANDIDATES)
        expected_orders = [
            names,
            names[1:] + names[:1],
            names[2:] + names[:2],
            names,
            names[1:] + names[:1],
            names[2:] + names[:2],
            names,
        ]
        self.assertEqual(len(schedule), 7)
        self.assertEqual(
            [batch["candidate_order"] for batch in schedule], expected_orders
        )
        self.assertTrue(all(len(batch["item_ids"]) == 30 for batch in schedule))
        self.assertEqual(
            [item_id for batch in schedule for item_id in batch["item_ids"]],
            [record["id"] for record in subset],
        )

    def test_latency_aggregation_counts_item_and_request_errors(self):
        outcomes = [
            outcome(True),
            outcome(None, errors=("timeout: late", "parse: malformed")),
            outcome(None, errors=("transport: reset", "parse: malformed", "odd")),
        ]
        summary = guard.aggregate_latency_results(outcomes, [1.0, 2.0, 3.0], 2.0)
        self.assertEqual(summary["complete_checks"], 3)
        self.assertEqual(summary["latency_seconds"]["p50_complete_check"], 2.0)
        self.assertEqual(summary["latency_seconds"]["p95_complete_check"], 3.0)
        self.assertEqual(summary["throughput_complete_checks_per_second"], 1.5)
        self.assertEqual(
            summary["errors"],
            {
                "items_with_errors": 2,
                "other_failures": 1,
                "parse_failures": 2,
                "timeouts": 1,
                "total": 5,
                "transport_failures": 1,
            },
        )


class CheckpointAndFaultHandlingTests(unittest.TestCase):
    def test_outcome_serialization_round_trip(self):
        original = guard.CheckOutcome(
            safe=True,
            categories=("Violent",),
            operational_safe=True,
            raw_outputs=("Safety: Unsafe",),
            errors=(),
        )
        d = guard.outcome_to_dict(original, 1.25)
        self.assertEqual(d["safe"], True)
        self.assertEqual(d["categories"], ["Violent"])
        self.assertEqual(d["latency_seconds"], 1.25)
        restored, lat = guard.outcome_from_dict(d)
        self.assertEqual(restored, original)
        self.assertEqual(lat, 1.25)

    def test_save_and_load_checkpoint_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "test.checkpoint.json"
            items = {
                "r-1": {
                    "categories": [],
                    "errors": [],
                    "latency_seconds": 0.5,
                    "operational_safe": True,
                    "raw_outputs": ["out1"],
                    "safe": True,
                }
            }
            guard.save_checkpoint(ckpt_path, "qwen-0.6b", "sha123", 10, items, 1.5)
            self.assertTrue(ckpt_path.is_file())
            loaded = guard.load_checkpoint(ckpt_path, "qwen-0.6b", "sha123")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["candidate"], "qwen-0.6b")
            self.assertEqual(loaded["corpus_sha256"], "sha123")
            self.assertEqual(loaded["total_records"], 10)
            self.assertEqual(loaded["completed_count"], 1)
            self.assertEqual(loaded["elapsed_seconds"], 1.5)
            self.assertEqual(loaded["items"], items)

    def test_load_checkpoint_validation_and_corruption_handling(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "corrupt.json"
            self.assertIsNone(guard.load_checkpoint(Path(tmp_dir) / "nonexistent.json"))
            ckpt_path.write_text("invalid json content", encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))
            guard.save_checkpoint(ckpt_path, "qwen-0.6b", "sha123", 5, {}, 0.0)
            self.assertIsNone(guard.load_checkpoint(ckpt_path, candidate_name="qwen-4b"))
            self.assertIsNone(guard.load_checkpoint(ckpt_path, corpus_sha256="sha999"))
            self.assertIsNotNone(
                guard.load_checkpoint(
                    ckpt_path, candidate_name="qwen-0.6b", corpus_sha256="sha123"
                )
            )
            # Schema validation
            doc = json.loads(ckpt_path.read_text(encoding="utf-8"))
            doc["format_version"] = 999
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            doc["format_version"] = 1
            doc["items"] = None
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            doc["items"] = {"row-1": None}
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            doc["items"] = {}
            doc["elapsed_seconds"] = -10.0
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            doc["elapsed_seconds"] = "not_a_number"
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            doc["elapsed_seconds"] = True
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            # Full configuration and model identity binding
            guard.save_checkpoint(
                ckpt_path,
                "qwen-0.6b",
                "sha123",
                5,
                {},
                1.0,
                model_file_sha256="model_sha_abc",
                server_config={"threads": 8, "clients": 30},
            )
            self.assertIsNotNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    model_file_sha256="model_sha_abc",
                    server_config={"threads": 8, "clients": 30},
                )
            )
            self.assertIsNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    model_file_sha256="different_model_sha",
                )
            )
            self.assertIsNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"threads": 4, "clients": 30},
                )
            )

            # Item consistency: missing safe or safe=None without errors must return None
            valid_item = {
                "categories": [],
                "errors": [],
                "latency_seconds": 0.1,
                "operational_safe": True,
                "raw_outputs": [],
                "safe": True,
            }
            guard.save_checkpoint(
                ckpt_path,
                "qwen-0.6b",
                "sha123",
                1,
                {"row-0": valid_item},
                1.0,
                model_file_sha256="model_sha_abc",
                server_config={"help_sha256": "help_sha_123"},
            )
            self.assertIsNotNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"help_sha256": "help_sha_123"},
                )
            )
            self.assertIsNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"help_sha256": "different_help_sha"},
                )
            )

            # server_sha256 validation
            guard.save_checkpoint(
                ckpt_path,
                "qwen-0.6b",
                "sha123",
                1,
                {"row-0": valid_item},
                1.0,
                model_file_sha256="model_sha_abc",
                server_config={"server_sha256": "server_sha_abc"},
            )
            self.assertIsNotNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"server_sha256": "server_sha_abc"},
                )
            )
            self.assertIsNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"server_sha256": "different_server_sha"},
                )
            )

            # max_retries validation
            guard.save_checkpoint(
                ckpt_path,
                "qwen-0.6b",
                "sha123",
                1,
                {"row-0": valid_item},
                1.0,
                model_file_sha256="model_sha_abc",
                server_config={"max_retries": 3},
            )
            self.assertIsNotNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"max_retries": 3},
                )
            )
            self.assertIsNone(
                guard.load_checkpoint(
                    ckpt_path,
                    "qwen-0.6b",
                    "sha123",
                    server_config={"max_retries": 0},
                )
            )

            # Missing safe key
            item_no_safe = dict(valid_item)
            del item_no_safe["safe"]
            doc = json.loads(ckpt_path.read_text(encoding="utf-8"))
            doc["items"]["row-0"] = item_no_safe
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

            # safe=None without errors is inconsistent
            item_inconsistent = dict(valid_item)
            item_inconsistent["safe"] = None
            item_inconsistent["errors"] = []
            doc["items"]["row-0"] = item_inconsistent
            ckpt_path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(guard.load_checkpoint(ckpt_path))

    def test_resolve_checkpoint_path(self):
        output = Path("output/results.json")
        resolved = guard.resolve_checkpoint_path(None, output, "qwen-0.6b")
        self.assertEqual(resolved, Path("output/results.qwen-0.6b.checkpoint.json"))
        resolved_dir = guard.resolve_checkpoint_path(
            Path("checkpoints/"), output, "shieldstral"
        )
        self.assertEqual(resolved_dir, Path("checkpoints/shieldstral.checkpoint.json"))
        resolved_file = guard.resolve_checkpoint_path(
            Path("custom.json"), output, "qwen-0.6b"
        )
        self.assertEqual(resolved_file, Path("custom.json"))
        resolved_tmpl = guard.resolve_checkpoint_path(
            Path("ckpt-{candidate}.json"), output, "qwen-4b"
        )
        self.assertEqual(resolved_tmpl, Path("ckpt-qwen-4b.json"))

    def test_run_closed_loop_persists_checkpoints_and_resumes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "run.checkpoint.json"
            records = [sample_record(id=f"row-{i}", text=f"text {i}") for i in range(10)]
            called_pass1 = []

            def checker_pass1(record):
                idx = int(record["id"].split("-")[1])
                if idx == 5:
                    raise KeyboardInterrupt("simulated operator interrupt")
                called_pass1.append(idx)
                return guard.CheckOutcome(True, (), True, (record["id"],), ())

            with self.assertRaises(KeyboardInterrupt):
                guard.run_closed_loop(
                    records,
                    checker_pass1,
                    clients=1,
                    checkpoint_path=ckpt_path,
                    candidate_name="test-model",
                    corpus_sha256="sha",
                )

            self.assertTrue(ckpt_path.is_file())
            loaded = guard.load_checkpoint(ckpt_path, "test-model", "sha")
            self.assertIsNotNone(loaded)
            self.assertEqual(len(loaded["items"]), 5)
            self.assertEqual(set(loaded["items"].keys()), {f"row-{i}" for i in range(5)})

            called_pass2 = []

            def checker_pass2(record):
                called_pass2.append(record["id"])
                return guard.CheckOutcome(True, (), True, (record["id"],), ())

            outcomes, latencies, elapsed = guard.run_closed_loop(
                records,
                checker_pass2,
                clients=2,
                checkpoint_path=ckpt_path,
                candidate_name="test-model",
                corpus_sha256="sha",
                resume=True,
            )
            self.assertEqual(len(outcomes), 10)
            self.assertEqual(len(latencies), 10)
            self.assertEqual(set(called_pass2), {f"row-{i}" for i in range(5, 10)})
            final_loaded = guard.load_checkpoint(ckpt_path, "test-model", "sha")
            self.assertEqual(len(final_loaded["items"]), 10)

    def test_interrupted_inflight_items_not_persisted_as_failures(self):
        import tempfile
        import threading

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "inflight.checkpoint.json"
            records = [sample_record(id=f"row-{i}") for i in range(4)]
            inflight_entered = threading.Event()
            interrupt_raised = threading.Event()
            cancel_event = threading.Event()

            def checker(record):
                idx = int(record["id"].split("-")[1])
                if idx == 0:
                    return guard.CheckOutcome(True, (), True, (record["id"],), ())
                if idx == 1:
                    inflight_entered.set()
                    interrupt_raised.wait(timeout=2.0)
                    cancel_event.wait(timeout=2.0)
                    return guard.CheckOutcome(None, (), False, (), ("transport: socket closed",))
                if idx == 2:
                    inflight_entered.wait(timeout=2.0)
                    interrupt_raised.set()
                    raise KeyboardInterrupt("simulated operator interrupt")
                return guard.CheckOutcome(True, (), True, (record["id"],), ())

            with self.assertRaises(KeyboardInterrupt):
                guard.run_closed_loop(
                    records,
                    checker,
                    clients=2,
                    checkpoint_path=ckpt_path,
                    candidate_name="test-model",
                    corpus_sha256="sha",
                    cancel_event=cancel_event,
                )

            self.assertTrue(ckpt_path.is_file())
            loaded = guard.load_checkpoint(ckpt_path, "test-model", "sha")
            self.assertIsNotNone(loaded)
            self.assertEqual(list(loaded["items"].keys()), ["row-0"])
            self.assertTrue(loaded["items"]["row-0"]["safe"])

    def test_run_closed_loop_retries_errors_when_requested(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "errors.checkpoint.json"
            records = [sample_record(id=f"row-{i}") for i in range(4)]
            items = {
                "row-0": {
                    "categories": [],
                    "errors": [],
                    "latency_seconds": 0.1,
                    "operational_safe": True,
                    "raw_outputs": [],
                    "safe": True,
                },
                "row-1": {
                    "categories": [],
                    "errors": ["timeout: 300s"],
                    "latency_seconds": 300.0,
                    "operational_safe": False,
                    "raw_outputs": [],
                    "safe": None,
                },
            }
            guard.save_checkpoint(ckpt_path, "test", "sha", 4, items, 300.1)

            called = []

            def tracking_checker(r):
                called.append(r["id"])
                return guard.CheckOutcome(True, (), True, (), ())

            outcomes, _, _ = guard.run_closed_loop(
                records,
                tracking_checker,
                clients=1,
                checkpoint_path=ckpt_path,
                candidate_name="test",
                corpus_sha256="sha",
                resume=True,
                retry_errors=False,
            )
            self.assertNotIn("row-1", called)
            self.assertIsNone(outcomes[1].safe)

            called.clear()
            outcomes, _, _ = guard.run_closed_loop(
                records,
                tracking_checker,
                clients=1,
                checkpoint_path=ckpt_path,
                candidate_name="test",
                corpus_sha256="sha",
                resume=True,
                retry_errors=True,
            )
            self.assertIn("row-1", called)
            self.assertTrue(outcomes[1].safe)

    def test_interrupted_retry_errors_preserves_prior_failure(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "retry_interrupt.checkpoint.json"
            records = [sample_record(id="row-0"), sample_record(id="row-1")]
            items = {
                "row-0": {
                    "categories": [],
                    "errors": [],
                    "latency_seconds": 0.1,
                    "operational_safe": True,
                    "raw_outputs": [],
                    "safe": True,
                },
                "row-1": {
                    "categories": [],
                    "errors": ["timeout: 300s"],
                    "latency_seconds": 300.0,
                    "operational_safe": False,
                    "raw_outputs": [],
                    "safe": None,
                },
            }
            guard.save_checkpoint(ckpt_path, "test", "sha", 2, items, 300.1)

            def interrupting_checker(r):
                if r["id"] == "row-1":
                    raise KeyboardInterrupt("interrupted during retry")
                return guard.CheckOutcome(True, (), True, (), ())

            with self.assertRaises(KeyboardInterrupt):
                guard.run_closed_loop(
                    records,
                    interrupting_checker,
                    clients=1,
                    checkpoint_path=ckpt_path,
                    candidate_name="test",
                    corpus_sha256="sha",
                    resume=True,
                    retry_errors=True,
                )

            loaded = guard.load_checkpoint(ckpt_path, "test", "sha")
            self.assertIsNotNone(loaded)
            self.assertIn("row-0", loaded["items"])
            self.assertIn("row-1", loaded["items"])
            self.assertEqual(loaded["items"]["row-1"]["errors"], ["timeout: 300s"])
            self.assertIsNone(loaded["items"]["row-1"]["safe"])

    def test_run_closed_loop_catches_checker_exceptions_gracefully(self):
        records = [sample_record(id="row-fail"), sample_record(id="row-ok")]

        def failing_checker(record):
            if record["id"] == "row-fail":
                raise ConnectionResetError("connection reset by peer")
            return guard.CheckOutcome(True, (), True, ("ok",), ())

        outcomes, latencies, elapsed = guard.run_closed_loop(
            records, failing_checker, clients=2
        )
        self.assertEqual(len(outcomes), 2)
        self.assertFalse(outcomes[0].operational_safe)
        self.assertIsNone(outcomes[0].safe)
        self.assertTrue(any("ConnectionResetError" in err for err in outcomes[0].errors))
        self.assertTrue(outcomes[1].operational_safe)
        self.assertTrue(outcomes[1].safe)

    def test_categorize_error(self):
        self.assertEqual(guard.categorize_error("timeout: 300s exceeded"), "timeout")
        self.assertEqual(guard.categorize_error("timeout"), "timeout")
        self.assertEqual(guard.categorize_error("transport: HTTP 503"), "transport")
        self.assertEqual(guard.categorize_error("connection: broken pipe"), "transport")
        self.assertEqual(guard.categorize_error("parse: invalid json"), "parse")
        self.assertEqual(guard.categorize_error("json: decode error"), "parse")
        self.assertEqual(guard.categorize_error("other: MemoryError"), "other")
        self.assertEqual(guard.categorize_error("unknown failure"), "other")

    def test_check_qwen_and_shieldstral_catch_unexpected_exceptions(self):
        def crashing_call(payload):
            raise ArithmeticError("unexpected math error")

        qwen_res = guard.check_qwen(sample_record(), crashing_call, "qwen")
        self.assertFalse(qwen_res.operational_safe)
        self.assertIsNone(qwen_res.safe)
        self.assertTrue(any("ArithmeticError" in e for e in qwen_res.errors))

        shield_res = guard.check_shieldstral(sample_record(), crashing_call, "shieldstral")
        self.assertFalse(shield_res.operational_safe)
        self.assertIsNone(shield_res.safe)
        self.assertTrue(any("ArithmeticError" in e for e in shield_res.errors))

    def test_run_cli_parses_resumability_and_retry_flags(self):
        parser = guard.build_parser()
        args = parser.parse_args(
            [
                "run",
                "tests/guard_set.jsonl",
                "out.json",
                "--candidate",
                "qwen-0.6b=Qwen3Guard-Gen-0.6B.Q8_0.gguf",
                "--llama-server",
                "llama-server",
                "--checkpoint",
                "custom.ckpt.json",
                "--retry-errors",
                "--max-retries",
                "3",
            ]
        )
        self.assertEqual(args.checkpoint, Path("custom.ckpt.json"))
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_errors)
        self.assertEqual(args.max_retries, 3)

        no_resume_args = parser.parse_args(
            [
                "run",
                "tests/guard_set.jsonl",
                "out.json",
                "--candidate",
                "qwen-0.6b=Qwen3Guard-Gen-0.6B.Q8_0.gguf",
                "--llama-server",
                "llama-server",
                "--no-resume",
                "--no-checkpoint",
            ]
        )
        self.assertFalse(no_resume_args.resume)
        self.assertTrue(no_resume_args.no_checkpoint)

        latency_args = parser.parse_args(
            [
                "latency",
                "tests/guard_set.jsonl",
                "out.json",
                "--candidate",
                "qwen-0.6b=Qwen3Guard-Gen-0.6B.Q8_0.gguf",
                "--candidate",
                "qwen-4b=Qwen3Guard-Gen-4B.Q8_0.gguf",
                "--candidate",
                "shieldstral=ShieldGemma-2B.Q8_0.gguf",
                "--llama-server",
                "llama-server",
                "--max-retries",
                "5",
            ]
        )
        self.assertEqual(latency_args.max_retries, 5)

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "run",
                    "tests/guard_set.jsonl",
                    "out.json",
                    "--candidate",
                    "qwen-0.6b=Qwen3Guard-Gen-0.6B.Q8_0.gguf",
                    "--llama-server",
                    "llama-server",
                    "--max-retries",
                    "-1",
                ]
            )

    def test_request_json_retries_transient_failures(self):
        from unittest.mock import MagicMock, patch

        attempts = 0

        def mock_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("simulated timeout")
            resp = MagicMock()
            resp.read.return_value = b'{"status": "ok"}'
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            data = guard._request_json(
                "http://test", {}, timeout=1.0, max_retries=2, retry_delay=0.001
            )
            self.assertEqual(data, {"status": "ok"})
            self.assertEqual(attempts, 2)

        with patch(
            "urllib.request.urlopen", side_effect=TimeoutError("permanent timeout")
        ):
            with self.assertRaises(guard.TimeoutFailure):
                guard._request_json(
                    "http://test", {}, timeout=1.0, max_retries=1, retry_delay=0.001
                )

        # IncompleteRead is retried and raises TransportFailure if exhausted
        import http.client

        incomplete_attempts = 0

        def mock_urlopen_incompleteread(request, timeout):
            nonlocal incomplete_attempts
            incomplete_attempts += 1
            if incomplete_attempts == 1:
                resp = MagicMock()
                resp.read.side_effect = http.client.IncompleteRead(b"partial")
                resp.__enter__.return_value = resp
                return resp
            resp = MagicMock()
            resp.read.return_value = b'{"status": "recovered"}'
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen_incompleteread):
            data = guard._request_json(
                "http://test", {}, timeout=1.0, max_retries=2, retry_delay=0.001
            )
            self.assertEqual(data, {"status": "recovered"})
            self.assertEqual(incomplete_attempts, 2)

        def mock_urlopen_permanent_incomplete(request, timeout):
            resp = MagicMock()
            resp.read.side_effect = http.client.IncompleteRead(b"bad")
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen_permanent_incomplete):
            with self.assertRaises(guard.TransportFailure):
                guard._request_json(
                    "http://test", {}, timeout=1.0, max_retries=1, retry_delay=0.001
                )

        # URLError wrapping TimeoutError must raise TimeoutFailure on exhaustion
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(TimeoutError("connection timed out")),
        ):
            with self.assertRaises(guard.TimeoutFailure):
                guard._request_json(
                    "http://test", {}, timeout=1.0, max_retries=0, retry_delay=0.001
                )


    def test_compute_metrics_includes_other_failures(self):
        records = [sample_record(id="r1"), sample_record(id="r2")]
        outcomes = [
            outcome(True),
            guard.CheckOutcome(None, (), False, (), ("other: RuntimeError: crash",)),
        ]
        metrics = guard.compute_metrics(records, outcomes, [1.0, 1.0], 2.0)
        self.assertEqual(metrics["errors"]["other_failures"], 1)
        self.assertEqual(metrics["errors"]["timeouts"], 0)
        self.assertEqual(metrics["errors"]["transport_failures"], 0)
        self.assertEqual(metrics["errors"]["parse_failures"], 0)
        self.assertFalse(metrics["qualification"]["run_valid"])

    def test_compute_metrics_missing_verdict_invalidates_run(self):
        records = [sample_record(id="r1")]
        outcomes = [
            guard.CheckOutcome(
                safe=None,
                categories=(),
                operational_safe=False,
                raw_outputs=(),
                errors=(),
            )
        ]
        metrics = guard.compute_metrics(records, outcomes, [1.0], 1.0)
        self.assertFalse(metrics["qualification"]["run_valid"])

    def test_multi_candidate_checkpoint_requires_template_or_dir(self):
        from unittest.mock import patch

        parser = guard.build_parser()
        args = parser.parse_args(
            [
                "run",
                "tests/guard_set.jsonl",
                "out.json",
                "--candidate",
                "qwen-0.6b=Qwen3Guard-Gen-0.6B.Q8_0.gguf",
                "--candidate",
                "qwen-4b=Qwen3Guard-Gen-4B.Q8_0.gguf",
                "--llama-server",
                "llama-server",
                "--checkpoint",
                "shared.checkpoint.json",
            ]
        )
        orig_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if cmd and "llama-server" in str(cmd[0]):
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="help")
            return orig_run(cmd, *a, **kw)

        with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run):
            with self.assertRaises(guard.ValidationError) as ctx:
                guard.command_run(args)
            self.assertIn("must be a directory or include '{candidate}'", str(ctx.exception))

    def test_command_run_output_reuse_and_identity_validation(self):
        import io
        import tempfile
        from unittest.mock import patch

        orig_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if cmd and "llama-server" in str(cmd[0]):
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="help")
            return orig_run(cmd, *a, **kw)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            corpus_path = tmp / "corpus.jsonl"
            records = [sample_record(id="row-0"), sample_record(id="row-1")]
            corpus_path.write_text(
                "".join(guard.compact_json(r) for r in records), encoding="utf-8"
            )
            corpus_sha = guard.file_sha256(corpus_path)

            model_path = tmp / "Qwen3Guard-Gen-0.6B.Q8_0.gguf"
            model_path.write_bytes(b"fake model bytes")
            model_sha = guard.file_sha256(model_path)

            fake_server = tmp / "fake-llama-server"
            fake_server.write_bytes(b"fake server binary bytes")
            server_sha = guard.file_sha256(fake_server)

            output_path = tmp / "out.json"

            _DEFAULT = object()

            def make_valid_doc(
                c_sha=corpus_sha,
                m_sha=model_sha,
                s_sha=server_sha,
                errors=(),
                run_valid=None,
                safe_val=_DEFAULT,
            ):
                import os

                if run_valid is None:
                    run_valid = not errors
                row0_safe = (not errors) if safe_val is _DEFAULT else safe_val
                return {
                    "corpus": {"path": str(corpus_path), "sha256": c_sha, "validation": {}},
                    "results": [
                        {
                            "candidate": {"adapter": "qwen", "name": "qwen-0.6b"},
                            "items": [
                                {
                                    "id": "row-0",
                                    "safe": row0_safe,
                                    "verdict": ["safe"] if row0_safe else [],
                                    "errors": list(errors),
                                },
                                {
                                    "id": "row-1",
                                    "safe": True,
                                    "verdict": ["safe"],
                                    "errors": [],
                                },
                            ],
                            "metrics": {"qualification": {"run_valid": run_valid}},
                            "model": {"filename": model_path.name, "sha256": m_sha},
                            "server": {
                                "config": {
                                    "cache_type_k": "f16",
                                    "cache_type_v": "f16",
                                    "clients": guard.DEFAULT_CLIENTS,
                                    "context_limit_per_client": 4096,
                                    "max_retries": 0,
                                    "threads": max(1, os.cpu_count() or 1),
                                    "timeout_seconds": guard.DEFAULT_TIMEOUT_SECONDS,
                                },
                                "help_sha256": hashlib.sha256(b"help").hexdigest(),
                                "sha256": s_sha,
                            },
                        }
                    ],
                }

            # 1. Matching doc -> reused, run_candidate NOT called
            output_path.write_text(json.dumps(make_valid_doc()), encoding="utf-8")
            parser = guard.build_parser()
            args = parser.parse_args(
                [
                    "run",
                    str(corpus_path),
                    str(output_path),
                    "--candidate",
                    f"qwen-0.6b={model_path}",
                    "--llama-server",
                    str(fake_server),
                ]
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand, patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                ret = guard.command_run(args)
                self.assertEqual(ret, 0)
                mock_run_cand.assert_not_called()
                self.assertIn("Reusing completed candidate 'qwen-0.6b'", mock_stderr.getvalue())

            # 2. Corpus sha mismatch -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(c_sha="different_sha")), encoding="utf-8"
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand, patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()
                self.assertIn("corpus sha256 mismatch", mock_stderr.getvalue())

            # 3. Model sha mismatch -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(m_sha="different_model_sha")), encoding="utf-8"
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()

            # 4. Server binary sha mismatch -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(s_sha="different_server_sha")), encoding="utf-8"
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()

            # 5. Item errors with --retry-errors -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(errors=["timeout: 300s"], safe_val=None)),
                encoding="utf-8",
            )
            args_retry = parser.parse_args(
                [
                    "run",
                    str(corpus_path),
                    str(output_path),
                    "--candidate",
                    f"qwen-0.6b={model_path}",
                    "--llama-server",
                    str(fake_server),
                    "--retry-errors",
                ]
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args_retry)
                mock_run_cand.assert_called_once()

            # 6. Claimed run_valid=True when items have errors -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(errors=["timeout: 300s"], run_valid=True)),
                encoding="utf-8",
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()

            # 7. safe=None without errors -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(safe_val=None, errors=())), encoding="utf-8"
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()

            # 8. Malformed run_valid (string 'false') -> not reused, run_candidate CALLED
            output_path.write_text(
                json.dumps(make_valid_doc(run_valid="false")), encoding="utf-8"
            )
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()

            # 9. max_retries config mismatch -> not reused, run_candidate CALLED
            doc_retries = make_valid_doc()
            doc_retries["results"][0]["server"]["config"]["max_retries"] = 5
            output_path.write_text(json.dumps(doc_retries), encoding="utf-8")
            with patch("tests.guard_bakeoff.subprocess.run", side_effect=fake_run), patch(
                "tests.guard_bakeoff.validate_corpus", return_value={"validation": "ok"}
            ), patch(
                "tests.guard_bakeoff.run_candidate"
            ) as mock_run_cand:
                mock_run_cand.return_value = make_valid_doc()["results"][0]
                ret = guard.command_run(args)
                mock_run_cand.assert_called_once()


    def test_run_candidate_resolves_bare_executable(self):
        import os
        import tempfile
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            fake_bin = tmp / "my-llama-server"
            fake_bin.write_bytes(b"dummy binary")
            fake_bin.chmod(0o755)

            with patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ.get('PATH', '')}"}):
                resolved = guard.resolve_executable("my-llama-server")
                self.assertEqual(resolved, fake_bin.resolve())
                self.assertEqual(guard.file_sha256(resolved), guard.file_sha256(fake_bin))

                # Verify run_candidate defensively resolves bare executable path
                spec = guard.CANDIDATES["qwen-0.6b"]
                fake_model = tmp / spec.expected_filename
                fake_model.write_bytes(b"dummy model")
                parser = guard.build_parser()
                args = parser.parse_args(
                    [
                        "run",
                        "tests/guard_set.jsonl",
                        str(tmp / "out.json"),
                        "--candidate",
                        f"qwen-0.6b={fake_model}",
                        "--llama-server",
                        "my-llama-server",
                    ]
                )
                with patch("tests.guard_bakeoff.subprocess.Popen") as mock_popen, patch(
                    "tests.guard_bakeoff._wait_for_server"
                ), patch(
                    "tests.guard_bakeoff.validate_cpu_only_log"
                ), patch(
                    "tests.guard_bakeoff.qwen_template_fixture_records", return_value=[sample_record()]
                ), patch(
                    "tests.guard_bakeoff.qwen_template_fixture_case", return_value=([], "ok")
                ), patch(
                    "tests.guard_bakeoff.validate_template_evidence"
                ), patch(
                    "tests.guard_bakeoff.check_qwen",
                    return_value=guard.CheckOutcome(True, (), True, (), ()),
                ), patch(
                    "tests.guard_bakeoff.run_closed_loop",
                    return_value=([guard.CheckOutcome(True, (), True, (), ())], [0.1], 0.1),
                ), patch(
                    "tests.guard_bakeoff.apply_template", return_value="ok"
                ):
                    proc = MagicMock()
                    proc.poll.return_value = None
                    mock_popen.return_value = proc
                    res = guard.run_candidate(
                        spec,
                        fake_model,
                        Path("my-llama-server"),
                        [sample_record()],
                        args,
                        help_text="fake help with --no-prefill-assistant --jinja --kv-unified-per-slot",
                    )
                    self.assertEqual(res["server"]["sha256"], guard.file_sha256(fake_bin))

    def test_run_closed_loop_interruption_preserves_active_cancel_event(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt = Path(tmp_dir) / "test.ckpt.json"
            records = [sample_record(id="r0"), sample_record(id="r1"), sample_record(id="r2")]
            calls_after_cancel = 0

            def checker(record):
                nonlocal calls_after_cancel
                if guard._ACTIVE_CANCEL_EVENT is not None and guard._ACTIVE_CANCEL_EVENT.is_set():
                    calls_after_cancel += 1
                if record["id"] == "r0":
                    raise KeyboardInterrupt("simulated cancel")
                return guard.CheckOutcome(True, (), True, (), ())

            try:
                with self.assertRaises(KeyboardInterrupt):
                    guard.run_closed_loop(
                        records,
                        checker,
                        clients=1,
                        checkpoint_path=ckpt,
                        candidate_name="test",
                        corpus_sha256="sha",
                    )
                self.assertIsNotNone(guard._ACTIVE_CANCEL_EVENT)
                self.assertTrue(guard._ACTIVE_CANCEL_EVENT.is_set())
                self.assertEqual(calls_after_cancel, 0)
            finally:
                guard._ACTIVE_CANCEL_EVENT = None

    def test_cancellation_aborts_retry_backoff_immediately(self):
        cancel_evt = threading.Event()
        guard._ACTIVE_CANCEL_EVENT = cancel_evt
        try:
            cancel_evt.set()
            start = time.perf_counter()
            with self.assertRaises(KeyboardInterrupt):
                guard._interruptible_sleep(10.0)
            elapsed = time.perf_counter() - start
            self.assertLess(elapsed, 0.5)

            with self.assertRaises(KeyboardInterrupt):
                guard._check_cancellation()
        finally:
            guard._ACTIVE_CANCEL_EVENT = None

    def test_request_json_aborts_immediately_on_cancellation_during_retry(self):
        from unittest.mock import patch

        cancel_evt = threading.Event()
        guard._ACTIVE_CANCEL_EVENT = cancel_evt
        attempts = 0

        def mock_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            cancel_evt.set()
            raise TimeoutError("simulated timeout")

        try:
            with patch("urllib.request.urlopen", side_effect=mock_urlopen):
                start = time.perf_counter()
                with self.assertRaises(KeyboardInterrupt):
                    guard._request_json(
                        "http://test", {}, timeout=1.0, max_retries=5, retry_delay=5.0
                    )
                elapsed = time.perf_counter() - start
                self.assertLess(elapsed, 0.5)
                self.assertEqual(attempts, 1)
        finally:
            guard._ACTIVE_CANCEL_EVENT = None


    def test_backoff_sleep_single_wait(self):
        from unittest.mock import patch

        cancel_evt = threading.Event()
        with patch.object(cancel_evt, "wait", return_value=False) as mock_wait, patch(
            "time.sleep"
        ) as mock_sleep, patch("random.uniform", return_value=1.0):
            guard._backoff_sleep(attempt=0, retry_delay=0.5, cancel_event=cancel_evt)
            mock_wait.assert_called_once_with(0.5)
            mock_sleep.assert_not_called()

    def test_run_candidate_reuses_provided_model_sha(self):
        from unittest.mock import MagicMock, patch

        spec = guard.CANDIDATES["qwen-0.6b"]
        fake_model = Path("/fake/model.gguf")
        fake_bin = Path("/fake/bin/llama-server")
        parser = guard.build_parser()
        args = parser.parse_args(
            [
                "run",
                "tests/guard_set.jsonl",
                "/fake/output.json",
                "--candidate",
                f"qwen-0.6b={fake_model}",
                "--llama-server",
                str(fake_bin),
            ]
        )

        with patch("tests.guard_bakeoff.file_sha256") as mock_sha, patch(
            "tests.guard_bakeoff.resolve_executable", return_value=fake_bin
        ), patch(
            "tests.guard_bakeoff.subprocess.Popen"
        ) as mock_popen, patch(
            "tests.guard_bakeoff._wait_for_server"
        ), patch(
            "tests.guard_bakeoff.validate_cpu_only_log"
        ), patch(
            "tests.guard_bakeoff.qwen_template_fixture_records", return_value=[sample_record()]
        ), patch(
            "tests.guard_bakeoff.qwen_template_fixture_case", return_value=([], "ok")
        ), patch(
            "tests.guard_bakeoff.validate_template_evidence"
        ), patch(
            "tests.guard_bakeoff.check_qwen",
            return_value=guard.CheckOutcome(True, (), True, (), ()),
        ), patch(
            "tests.guard_bakeoff.run_closed_loop",
            return_value=([guard.CheckOutcome(True, (), True, (), ())], [0.1], 0.1),
        ), patch(
            "tests.guard_bakeoff.apply_template", return_value="ok"
        ):
            mock_sha.side_effect = lambda p: f"sha_of_{Path(p).name}"
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.return_value = proc

            res = guard.run_candidate(
                spec,
                fake_model,
                fake_bin,
                [sample_record()],
                args,
                help_text="fake help with --no-prefill-assistant --jinja --kv-unified-per-slot",
                model_sha="precomputed_model_sha_123",
            )
            self.assertEqual(res["model"]["sha256"], "precomputed_model_sha_123")
            # Verify file_sha256 was NOT called on fake_model
            model_calls = [
                call_args[0][0]
                for call_args in mock_sha.call_args_list
                if Path(call_args[0][0]) == fake_model
            ]
            self.assertEqual(model_calls, [])

    def test_command_run_rejects_duplicate_candidates(self):
        fake_model1 = Path("/fake/model1.gguf")
        fake_model2 = Path("/fake/model2.gguf")
        parser = guard.build_parser()
        args = parser.parse_args(
            [
                "run",
                "tests/guard_set.jsonl",
                "/fake/output.json",
                "--candidate",
                f"qwen-0.6b={fake_model1}",
                "--candidate",
                f"qwen-0.6b={fake_model2}",
                "--llama-server",
                "llama-server",
            ]
        )
        with self.assertRaises(guard.ValidationError) as ctx:
            guard.command_run(args)
        self.assertIn("candidate names must be unique", str(ctx.exception))
        self.assertIn("qwen-0.6b", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
