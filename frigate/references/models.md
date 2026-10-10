# Evaluate and optimize models

Treat object detection and semantic retrieval as separate evaluations. A fast
detector does not establish good cat recognition, and an embedding retrieval
score does not measure object detection accuracy.

## Establish the baseline

Read the deployment's instructions and inspect the saved configuration and
running service before choosing a candidate. Record image version/digest,
detector backend, accelerator, device permissions, model hash and path, input
dimensions, layout, color order, normalization, label map, postprocessing and
thresholds. Record semantic backend, vector dimension, query formatting and
index state separately. Configuration APIs can expand credentials: extract only
the fields needed for the comparison, never dump the entire response.

Capture baseline camera capture/processing/skipped FPS, detector timing,
CPU/RAM/VRAM, recording freshness and service health under ordinary load.
Distinguish detector inference time from video decoding, resizing, tracking,
storage and browser latency. Camera frame rate, motion regions, connection
limits and decoder contention can explain apparent detection regressions.

Keep candidate builds and dependencies isolated from the production image.
Pin upstream source, weights, exporter and inference runtime; record hashes and
commands. Download authoritative model files and inspect their license and
required preprocessing. Do not assume a supported inference engine implies
Frigate supports the model's input/output contract.

## Freeze an accuracy dataset

For footage collection and dataset expansion, read
[frame-extraction.md](frame-extraction.md).

Use authorized footage only. Keep images, annotations, crops and vectors private
and outside source control. Save a manifest with source camera, recording
interval, frame identity, annotation version and split. Use recording intervals
and encounters to separate training, calibration, validation and final testing;
adjacent frames and crops from one scene belong in the same split. Check exact
and near duplicates across splits. Never select calibration images by scanning
a shared directory containing held-out data.

Include daylight and IR/night scenes, different cat colors, distances,
occlusions, motion, empty scenes and confusing negatives. Include people and
other retained labels to detect regressions. Manually review ground truth;
existing detector labels are not annotations. Freeze ambiguous-object exclusion
rules before comparing candidates and report excluded samples explicitly.

Evaluate both full frames and representative detector-region crops, with
separate results. Crops can substantially improve apparent accuracy; crop
recall does not establish full-frame, motion-region or tracked-event recall.
Report independent encounters as well as image/view counts.

## Compare visual detectors

Run every candidate on the same frozen inputs using its correct preprocessing
and postprocessing. Use confidence-ordered one-to-one matching against labeled
boxes at a fixed IoU threshold, commonly 0.5. Report true positives, false
positives, false negatives, precision and recall separately for cats, people and
relevant scene strata. Define treatment of ignored regions consistently.

Compare at the current production threshold and a predetermined threshold
sweep. Select thresholds using validation data, then report final results on an
untouched test split. Include precision/recall curves or AP when useful. A small
sample with zero false positives does not establish reliable precision, and a
count of matched crops is not a general benchmark.

Validate selected examples through Frigate's actual detector runner. Check
coordinate scaling, labels, confidence, NMS and finite outputs against an
appropriate reference backend. A successful export or low inference time can
hide incorrect confidence values or preprocessing.

## Measure latency and quantization correctly

Use the deployed runtime and accelerator. Separate cold compile/load time,
warm inference and end-to-end processing. Warm up before measuring; report
sample count, mean/p50/p95, input shapes and concurrent workload. Synthetic
zero tensors are useful for timing and compatibility only. Repeat meaningful
measurements with real evaluation inputs while ordinary camera processing runs.

For reduced precision, compare numerical outputs and held-out accuracy with the
known-correct floating-point reference. Use calibration data representative of
deployment, including IR when applicable, without calibration/test leakage.
Validate the exported graph and actual Frigate path after quantization.

Distinguish compressed integer weights from calibrated weight-and-activation
quantization. Q4/Q8 file names, FakeQuantize nodes or a compiler precision hint
alone do not prove executed INT8 kernels. Use runtime profiling or isolated
kernel evidence when claiming quantized execution; instruction evidence does
not establish accelerator occupancy. Keep diagnostic tracing out of production.
Honor model-specific arithmetic requirements: FP16 can silently corrupt some
models even when outputs are finite and fast.

## Compare semantic embeddings

Freeze a representative thumbnail corpus and human-reviewed query relevance
sets. Include cat locations/interactions, people and hard negatives; do not use
detector categories as relevance truth. Use the same corpus and queries for all
candidates. Compare the exact Frigate request protocol first; evaluate required
vendor query prefixes separately and document any adapter needed for deployment.

Check multimodal support, pooling, image conversion, token limits, vector
dimension and normalization. Truncate vectors only when the model supports that
dimension. Copy runtime output views before storing vectors, verify all values
are finite, and compare CPU/GPU or reference vectors and repeated rankings.

Report per-query AP, mAP, precision at k and failures. When fewer than k relevant
items exist, state the attainable precision-at-k ceiling. mAP describes ranking
quality, not a percentage of correctly classified images. Small, correlated
household samples support local choices rather than universal model rankings.

Measure cold/warm text queries and image indexing separately, including
preprocessing and HTTP where applicable. Repeat queries to expose first-shape
outliers and ranking instability. Measure peak total and model-resident memory
separately, plus camera FPS/skips during indexing. Weight compression can reduce
memory without speeding image attention or queries. Account for reindex duration
and expected production indexing load before choosing a quality/resource tradeoff.

## Roll out and verify

Retain the working weights, relevant configuration and index/database backup.
Validate the candidate configuration with the installed Frigate parser before
deployment. Apply the smallest semantic change to the current live file;
restoring an old template can undo newer camera or recording settings.

For custom embeddings, establish a persistent supported inference service first,
with private binding, device access and health checks. Assign its embeddings role
explicitly. A backend/vector-space change requires a complete index rebuild;
never mix old and new vectors. Plan restoration of the old compatible index or
a full reindex when rolling back, and do not claim search readiness mid-rebuild.

After restart, read back both saved and runtime model/provider selection. Verify
service health, recording freshness, every camera's processing and skipped FPS,
real labeled detector outputs or representative search rankings, memory and
errors. Stop temporary builders and test inference services after evaluation.
If acceptance criteria fail, restore only the affected settings and compatible
artifacts, restart as needed, and verify the same health checks. Record the final
state, evidence and limitations without exposing credentials or private footage.
