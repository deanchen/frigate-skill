"""Synthetic offline retrieval and fixed-threshold detector evaluation contracts."""
import contextlib
import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("evaluate", Path(__file__).resolve().parents[1] / "frigate/scripts/evaluate.py")
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def box(label="cat", bbox=None, score=None):
    row = {"label": label, "bbox": bbox or [0, 0, 10, 10]}
    if score is not None:
        row["score"] = score
    return row


def image(identifier="one", view="fullframe", cohort="day", ground_truth=None, predictions=None):
    return {"image_id": identifier, "view": view, "cohort": cohort,
            "ground_truth": [box()] if ground_truth is None else ground_truth,
            "predictions": [box(score=0.9)] if predictions is None else predictions}


class RetrievalTests(unittest.TestCase):
    def test_exact_ap_map_mrr_and_short_list_denominator(self):
        result = evaluate.evaluate_retrieval({"ks": [1, 3, 5], "queries": [
            {"query_id": "one", "relevant_ids": ["a", "b"], "ranked_ids": ["x", "a", "b"]},
            {"query_id": "two", "relevant_ids": ["c"], "ranked_ids": ["c"]}]})
        self.assertAlmostEqual(result["per_query"][0]["average_precision"], (1 / 2 + 2 / 3) / 2)
        self.assertAlmostEqual(result["map"], ((1 / 2 + 2 / 3) / 2 + 1) / 2)
        self.assertEqual(result["mrr"], 0.75)
        self.assertEqual(result["at_k"]["1"], {"precision": 0.5, "recall": 0.5})
        self.assertEqual(result["per_query"][1]["at_k"]["5"]["precision"], 0.2)
        self.assertEqual(result["at_k"]["3"]["recall"], 1)

    def test_zero_relevance_explicitly_excluded_not_perfect_score(self):
        result = evaluate.evaluate_retrieval({"queries": [
            {"query_id": "zero", "relevant_ids": [], "ranked_ids": ["a"]},
            {"query_id": "miss", "relevant_ids": ["b"], "ranked_ids": []}]})
        self.assertEqual(result["coverage"], 0.5)
        self.assertEqual(result["excluded_zero_relevance"], 1)
        self.assertEqual(result["map"], 0)
        self.assertIsNone(result["per_query"][0]["average_precision"])
        result = evaluate.evaluate_retrieval({"queries": [{"query_id": "zero", "relevant_ids": [], "ranked_ids": []}]})
        self.assertIsNone(result["map"])
        self.assertIsNone(result["mrr"])
        self.assertEqual(result["coverage"], 0)

    def test_missing_relevant_documents_penalize_ap_recall(self):
        result = evaluate.evaluate_retrieval({"queries": [{"query_id": "one", "relevant_ids": ["a", "b"], "ranked_ids": ["a"]}]})
        self.assertEqual(result["map"], 0.5)
        self.assertEqual(result["at_k"]["1"]["recall"], 0.5)

    def test_duplicate_ids_and_invalid_cutoffs_rejected(self):
        base = {"queries": [{"query_id": "one", "relevant_ids": ["a"], "ranked_ids": ["a"]}]}
        cases = [dict(base, ks=[0]), dict(base, ks=[True]), dict(base, ks=[1, 1]), dict(base, ks=[2 ** 63]),
                 {"queries": base["queries"] * 2}]
        for field in ("relevant_ids", "ranked_ids"):
            bad = copy.deepcopy(base)
            bad["queries"][0][field] = ["a", "a"]
            cases.append(bad)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(evaluate.EvaluationError):
                evaluate.evaluate_retrieval(case)


class DetectorTests(unittest.TestCase):
    def test_one_to_one_duplicate_detections_are_false_positives(self):
        result = evaluate.evaluate_detector({"images": [image(predictions=[box(score=0.9), box(bbox=[1, 1, 10, 10], score=0.8)])]})
        overall = result["overall"]
        self.assertEqual((overall["true_positives"], overall["false_positives"], overall["false_negatives"]), (1, 1, 0))
        self.assertEqual(overall["precision"], 0.5)
        self.assertEqual(overall["recall"], 1)
        self.assertAlmostEqual(overall["f1"], 2 / 3)
        self.assertEqual(result["mode"], "detector_fixed_threshold")
        self.assertNotIn("map", result)

    def test_matching_is_same_label_and_threshold_inclusive(self):
        result = evaluate.evaluate_detector({"confidence_threshold": 0.5, "iou_threshold": 0.5, "images": [
            image(predictions=[box(label="person", score=1), box(bbox=[0, 0, 5, 10], score=0.5),
                               box(bbox=[20, 20, 30, 30], score=0.49)])]})
        overall = result["overall"]
        self.assertEqual((overall["true_positives"], overall["false_positives"], overall["false_negatives"]), (1, 1, 0))
        self.assertEqual(result["excluded_low_confidence_predictions"], 1)

    def test_highest_score_first_greedy_matching(self):
        # The higher-score ambiguous box chooses the first GT; the lower box then cannot match it.
        targets = [box(bbox=[0, 0, 10, 10]), box(bbox=[5, 0, 15, 10])]
        predictions = [box(bbox=[0, 0, 10, 10], score=0.6), box(bbox=[2.5, 0, 12.5, 10], score=0.9)]
        result = evaluate.evaluate_detector({"images": [image(ground_truth=targets, predictions=predictions)]})
        self.assertEqual(result["overall"]["true_positives"], 1)
        self.assertEqual(result["overall"]["false_negatives"], 1)

    def test_fullframe_crop_cohort_and_label_groups(self):
        rows = [image("frame", cohort="day"), image("crop", view="crop", cohort="night", predictions=[]),
                image("person", cohort="night", ground_truth=[box(label="person")], predictions=[box(label="person", score=0.8)])]
        result = evaluate.evaluate_detector({"images": rows})
        self.assertEqual(result["overall"]["image_count"], 3)
        self.assertEqual(result["by_view"]["fullframe"]["true_positives"], 2)
        self.assertEqual(result["by_view"]["crop"]["false_negatives"], 1)
        self.assertEqual(result["by_cohort"]["night"]["ground_truth_count"], 2)
        self.assertEqual(result["by_view_cohort"]["crop"]["night"]["image_count"], 1)
        self.assertEqual(result["overall"]["by_label"]["cat"]["recall"], 0.5)

    def test_empty_detection_counts_and_ignored_labels(self):
        result = evaluate.evaluate_detector({"images": [image(ground_truth=[box(label="dog")], predictions=[box(label="dog", score=0.9)])]})
        self.assertEqual(result["ignored_ground_truth_labels"], 1)
        self.assertEqual(result["ignored_prediction_labels"], 1)
        self.assertEqual(result["overall"]["ground_truth_count"], 0)
        self.assertIsNone(result["overall"]["precision"])
        self.assertIsNone(result["overall"]["recall"])

    def test_malformed_nonfinite_degenerate_duplicate_data_rejected(self):
        invalid_rows = [box(bbox=[0, 0, 0, 1]), box(bbox=[0, 0, 1, math.inf]),
                        box(bbox=[0, 0, math.nan, 1]), box(bbox=[0, 0, True, 1]),
                        box(bbox=[0, 0, 1e308, 1e308]), {"label": "cat", "bbox": [0, 0, 1]}]
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(evaluate.EvaluationError):
                evaluate.evaluate_detector({"images": [image(ground_truth=[row])]})
        cases = [{"images": [image(), image()]}, {"images": [image(ground_truth=[box(), box()])]},
                 {"images": [image(predictions=[box(score=0.9), box(score=0.8)])]},
                 {"images": [image(predictions=[box(score=math.nan)])]},
                 {"images": [image(view="thumbnail")]}, {"images": [image()], "iou_threshold": 0}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(evaluate.EvaluationError):
                evaluate.evaluate_detector(case)


class SharedTests(unittest.TestCase):
    def test_latency_interpolation_missing_samples_and_invalid_measurement(self):
        result = evaluate.latency_summary([{"latency_ms": 10}, {"latency_ms": 30}, {}])
        self.assertEqual(result, {"samples": 2, "missing": 1, "p50_ms": 20, "p95_ms": 29})
        self.assertIsNone(evaluate.latency_summary([{}])["p50_ms"])
        for value in (-1, math.inf, True, 10 ** 1000):
            with self.subTest(value=value), self.assertRaises(evaluate.EvaluationError):
                evaluate.latency_summary([{"latency_ms": value}])

    def test_cli_outputs_json_and_rejects_invalid_input_without_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            target = Path(directory) / "output.json"
            path.write_text(json.dumps({"images": [image()]}))
            self.assertEqual(evaluate.main(["detector", str(path), "--output", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text())["overall"]["precision"], 1)
            path.write_text('{"images":[]}')
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(evaluate.main(["detector", str(path)]), 2)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
