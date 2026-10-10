#!/usr/bin/env python3
"""Offline, dependency-free evaluation of retrieval or fixed-threshold detection.

Retrieval JSON: {"queries":[{"query_id":"q1","relevant_ids":["a"],
"ranked_ids":["a","b"],"latency_ms":12}],"ks":[1,5,10]}.
AP divides by all relevant IDs; precision@k divides by k even for short lists.
Queries without relevance judgments are explicitly excluded from macro metrics.

Detector JSON: {"images":[{"image_id":"i1","view":"fullframe",
"cohort":"night","ground_truth":[{"label":"cat","bbox":[0,0,20,20]}],
"predictions":[{"label":"cat","bbox":[0,0,20,20],"score":0.9}],
"latency_ms":15}],"labels":["cat","person"],"iou_threshold":0.5,
"confidence_threshold":0.5}. view must be fullframe or crop; cohort is optional.
Boxes are finite positive-area xyxy coordinates in the same coordinate system.
Predictions match same-label ground truth one-to-one, highest score first, then
highest IoU, with input order breaking ties. Score equality includes a prediction.
This measures precision/recall/F1 at fixed thresholds, not detector mAP.

IDs are nonempty strings, unique per query/image collection and per ID list.
Duplicate same-label boxes within an annotation list are rejected. Unknown
annotation labels are validated but ignored when outside the requested labels.
latency_ms is optional, finite and nonnegative. p50/p95 use linear interpolation.
"""

import argparse
import json
import math
import os
from pathlib import Path
import sys


class EvaluationError(ValueError):
    """Invalid or incomparable evaluation input."""


def _object(value, context):
    if not isinstance(value, dict):
        raise EvaluationError(context + " must be an object")
    return value


def _list(value, context, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise EvaluationError(context + " must be " + ("a nonempty list" if nonempty else "a list"))
    return value


def _identifier(value, context):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(context + " must be a nonempty string")
    return value


def _number(value, context, minimum=None, maximum=None):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise EvaluationError(context + " must be finite numeric data")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise EvaluationError(context + " is outside the allowed range")
    return value


def _unique_ids(value, context):
    values = _list(value, context)
    checked = [_identifier(item, context + " item") for item in values]
    if len(set(checked)) != len(checked):
        raise EvaluationError(context + " contains duplicate IDs")
    return checked


def latency_summary(rows):
    values = sorted(_number(row["latency_ms"], "latency_ms", 0) for row in rows if "latency_ms" in row)
    def percentile(fraction):
        if not values:
            return None
        position = fraction * (len(values) - 1)
        low, high = math.floor(position), math.ceil(position)
        return values[low] + (values[high] - values[low]) * (position - low)
    return {"samples": len(values), "missing": len(rows) - len(values),
            "p50_ms": percentile(0.5), "p95_ms": percentile(0.95)}


def evaluate_retrieval(data):
    data = _object(data, "input")
    queries = _list(data.get("queries"), "queries", True)
    ks = _list(data.get("ks", [1, 5, 10]), "ks", True)
    if any(isinstance(k, bool) or not isinstance(k, int) or k < 1 or k > 2 ** 63 - 1 for k in ks) or len(set(ks)) != len(ks):
        raise EvaluationError("ks must contain unique positive 64-bit integers")
    ks = sorted(ks)
    seen, per_query = set(), []
    for query in queries:
        query = _object(query, "query")
        query_id = _identifier(query.get("query_id"), "query_id")
        if query_id in seen:
            raise EvaluationError("duplicate query_id")
        seen.add(query_id)
        relevant = set(_unique_ids(query.get("relevant_ids"), "relevant_ids"))
        ranked = _unique_ids(query.get("ranked_ids"), "ranked_ids")
        row = {"query_id": query_id, "relevant_count": len(relevant), "ranked_count": len(ranked),
               "included": bool(relevant)}
        if relevant:
            hits, precision_sum, first = 0, 0.0, None
            for rank, identifier in enumerate(ranked, 1):
                if identifier in relevant:
                    hits += 1
                    precision_sum += hits / rank
                    first = rank if first is None else first
            row.update(average_precision=precision_sum / len(relevant), reciprocal_rank=1 / first if first else 0.0)
            row["at_k"] = {str(k): {"precision": sum(item in relevant for item in ranked[:k]) / k,
                                     "recall": sum(item in relevant for item in ranked[:k]) / len(relevant)} for k in ks}
        else:
            row.update(average_precision=None, reciprocal_rank=None, at_k=None,
                       exclusion_reason="zero_relevant_ids")
        per_query.append(row)
    eligible = [row for row in per_query if row["included"]]
    def mean(values):
        return sum(values) / len(values) if values else None
    return {"mode": "retrieval", "query_count": len(queries), "evaluated_queries": len(eligible),
            "excluded_zero_relevance": len(queries) - len(eligible), "coverage": len(eligible) / len(queries),
            "map": mean([row["average_precision"] for row in eligible]),
            "mrr": mean([row["reciprocal_rank"] for row in eligible]),
            "at_k": {str(k): {metric: mean([row["at_k"][str(k)][metric] for row in eligible])
                               for metric in ("precision", "recall")} for k in ks},
            "per_query": per_query, "latency": latency_summary(queries)}


def _annotations(value, predictions):
    annotations = _list(value, "predictions" if predictions else "ground_truth")
    seen, result = set(), []
    for row in annotations:
        row = _object(row, "annotation")
        label = _identifier(row.get("label"), "annotation label")
        bbox = _list(row.get("bbox"), "bbox")
        if len(bbox) != 4:
            raise EvaluationError("bbox must contain four xyxy coordinates")
        bbox = tuple(_number(number, "bbox coordinate") for number in bbox)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise EvaluationError("bbox must have positive area")
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if not math.isfinite(area) or area <= 0:
            raise EvaluationError("bbox area must be finite and positive")
        identity = (label, bbox)
        if identity in seen:
            raise EvaluationError("duplicate same-label annotation box")
        seen.add(identity)
        item = {"label": label, "bbox": bbox}
        if predictions:
            item["score"] = _number(row.get("score"), "score", 0, 1)
        result.append(item)
    return result


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    scale = max(area_a, area_b)
    return (intersection / scale) / (area_a / scale + area_b / scale - intersection / scale)


def _metrics(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "ground_truth_count": tp + fn, "prediction_count": tp + fp,
            "precision": precision, "recall": recall, "f1": f1}


def _aggregate(rows, labels):
    by_label = {label: _metrics(*(sum(row["by_label"][label][key] for row in rows)
                                 for key in ("true_positives", "false_positives", "false_negatives"))) for label in labels}
    return {"image_count": len(rows), **_metrics(*(sum(row["by_label"][label][key] for row in rows for label in labels)
                                                  for key in ("true_positives", "false_positives", "false_negatives"))),
            "by_label": by_label}


def evaluate_detector(data):
    data = _object(data, "input")
    images = _list(data.get("images"), "images", True)
    labels = _unique_ids(data.get("labels", ["cat", "person"]), "labels")
    if not labels:
        raise EvaluationError("labels must be nonempty")
    threshold = _number(data.get("iou_threshold", 0.5), "iou_threshold", 0, 1)
    if threshold == 0:
        raise EvaluationError("iou_threshold must be greater than zero")
    confidence = _number(data.get("confidence_threshold", 0.5), "confidence_threshold", 0, 1)
    seen, rows, ignored_gt, ignored_pred, low_confidence = set(), [], 0, 0, 0
    for image in images:
        image = _object(image, "image")
        identifier = _identifier(image.get("image_id"), "image_id")
        if identifier in seen:
            raise EvaluationError("duplicate image_id")
        seen.add(identifier)
        view = image.get("view")
        if view not in ("fullframe", "crop"):
            raise EvaluationError("view must be fullframe or crop")
        cohort = _identifier(image.get("cohort", "unspecified"), "cohort")
        ground_truth = _annotations(image.get("ground_truth"), False)
        predictions = _annotations(image.get("predictions"), True)
        ignored_gt += sum(item["label"] not in labels for item in ground_truth)
        ignored_pred += sum(item["label"] not in labels for item in predictions)
        low_confidence += sum(item["label"] in labels and item["score"] < confidence for item in predictions)
        counts = {}
        for label in labels:
            targets = [item for item in ground_truth if item["label"] == label]
            candidates = sorted((item for item in predictions if item["label"] == label and item["score"] >= confidence),
                                key=lambda item: -item["score"])
            unmatched, matched = set(range(len(targets))), 0
            for candidate in candidates:
                overlaps = [(iou(candidate["bbox"], targets[index]["bbox"]), -index, index) for index in unmatched]
                if overlaps:
                    overlap, _, index = max(overlaps)
                    if overlap >= threshold:
                        unmatched.remove(index)
                        matched += 1
            counts[label] = _metrics(matched, len(candidates) - matched, len(targets) - matched)
        rows.append({"image_id": identifier, "view": view, "cohort": cohort, "by_label": counts})
    views = sorted({row["view"] for row in rows})
    cohorts = sorted({row["cohort"] for row in rows})
    return {"mode": "detector_fixed_threshold", "labels": labels, "iou_threshold": threshold,
            "confidence_threshold": confidence, "overall": _aggregate(rows, labels),
            "by_view": {view: _aggregate([row for row in rows if row["view"] == view], labels) for view in views},
            "by_cohort": {cohort: _aggregate([row for row in rows if row["cohort"] == cohort], labels) for cohort in cohorts},
            "by_view_cohort": {view: {cohort: _aggregate([row for row in rows if row["view"] == view and row["cohort"] == cohort], labels)
                                      for cohort in cohorts if any(row["view"] == view and row["cohort"] == cohort for row in rows)} for view in views},
            "ignored_ground_truth_labels": ignored_gt, "ignored_prediction_labels": ignored_pred,
            "excluded_low_confidence_predictions": low_confidence, "per_image": rows,
            "latency": latency_summary(images)}


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("retrieval", "detector"))
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = evaluate_retrieval(data) if args.mode == "retrieval" else evaluate_detector(data)
        encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            fd = os.open(str(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(encoded)
        else:
            sys.stdout.write(encoded)
    except (OSError, ValueError, TypeError) as error:
        print("Evaluation failed: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
