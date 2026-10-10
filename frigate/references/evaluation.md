# Offline evaluation inputs

These helpers score saved outputs from candidate inference runs. They do not
generate predictions, train, export, quantize, benchmark a model, or change the
running service. Use `models.md` to run comparable candidate inference; save the
raw outputs and model/runtime/data provenance privately with each report.

## Retrieval

```json
{
  "ks": [1, 5, 10],
  "queries": [
    {"query_id": "white-cat-eating", "relevant_ids": ["image-a", "image-c"],
     "ranked_ids": ["image-a", "image-b", "image-c"], "latency_ms": 45}
  ]
}
```

Run `python3 scripts/evaluate.py retrieval /private/rankings.json` from the
skill directory. Every ranking and relevance list must contain unique IDs,
and query IDs must be unique. All models must use the same image corpus,
queries, relevance judgments and ranking depth. Use enough results to compare
AP fairly; truncated rankings omit later relevant hits.

Output includes per-query AP, macro mAP, MRR, precision/recall at k and judged
query coverage. AP divides by the total relevant set; precision at k divides
by k even if fewer results were returned. Queries with no relevant IDs are
excluded from these macro scores and reported explicitly, not assigned perfect
scores. Separately review false matches for genuinely empty/no-answer queries;
this schema does not distinguish unjudged queries from judged zero-relevance
queries. Latency is optional: missing measurements stay missing.

## Visual detectors

```json
{
  "labels": ["cat", "person"],
  "confidence_threshold": 0.7,
  "iou_threshold": 0.5,
  "images": [
    {"image_id": "frame-001", "view": "fullframe", "cohort": "white-night",
     "ground_truth": [{"label": "cat", "bbox": [10, 20, 90, 100]}],
     "predictions": [{"label": "cat", "bbox": [12, 21, 88, 99], "score": 0.9}],
     "latency_ms": 8}
  ]
}
```

Run `python3 scripts/evaluate.py detector /private/predictions.json`. Image IDs
are unique, view is `fullframe` or `crop`, cohort is optional. Boxes use finite,
positive-area `[x1,y1,x2,y2]` in the same pixel or normalized coordinate space
for both prediction and truth. Scores and thresholds range from 0 to 1.
Save empty-scene images with empty ground truth to measure false positives.
Duplicate boxes of the same label in one annotation list are rejected.

Matching is one-to-one within each label: predictions above/equal to the fixed
confidence threshold are visited in descending score order and matched to the
unmatched truth with highest IoU above/equal to the threshold. Output includes
TP/FP/FN, precision/recall/F1 overall, by label, view, cohort and view/cohort;
unknown labels are validated but explicitly counted as ignored. Precision or
recall with no denominator is null. This is fixed-threshold evaluation, not
detector mAP. Keep labels and confidence/IoU thresholds constant across runs.

Run threshold sweeps on validation data, choose a threshold, then evaluate the
untouched test data. Do not report train or calibration recall as held-out
accuracy. The scorer does not check encounter independence or source-split
leakage; that is a manifest/provenance responsibility.

## Output and timing

Both modes print JSON or write `--output /private/report.json`.
Output files are created with mode 0600; existing files are never overwritten.
Keep input manifests and reports outside Git; IDs and cohort names can reveal private
activity. Latency p50/p95 uses linear interpolation across supplied samples;
report cold/warm runs separately, with the timing boundary defined. Raw
per-image model timings do not include decoding and tracking unless measured
that way. Never combine incompatible timing boundaries in one latency report.
