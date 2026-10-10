# Training and evaluation frame extraction

Use this workflow for requests to add cases from Explore, a clip, an encounter,
or a camera/time range. Read `api.md` for transport details and `models.md` for
model comparisons. Dataset collection, offline evaluation, training and rollout
are separate actions; carry out the scope the user requested.

## Locate and preserve source footage

Resolve camera aliases from current inventory. Convert requested local times
using the deployment timezone and explicit date/UTC offset, including ranges
crossing midnight. Query ordinary labeled events first; use semantic search as
a candidate finder when appearance or behavior matters. Broaden the query start
to find tracks that began before the requested boundary. Save query parameters,
event IDs, full encounter bounds and selected metadata privately.

The API helper accepts `--limit` up to 500. If a query returns its limit, narrow
the time/camera windows and deduplicate IDs; do not claim complete coverage from
a capped response. Event-start filtering can omit overlapping long tracks.
Existing cat tracks bias selection toward successful detections. Inspect bounded
recordings for missed appearances and genuine negatives when those are needed.

Download retained footage promptly before ordinary retention removes it. Prefer
short `interval-clip` downloads for known camera/time windows or missing events;
an event thumbnail may show only one instant. Preserve source clips unchanged.
For UniFi footage, use the installed UniFi skill/API and retain equivalent
console/camera/time provenance.

```sh
python3 <skill-dir>/scripts/frigate_api.py --ssh [username]@frigate-host interval-clip camera_one --after 2030-01-15T10:00:00Z --before 2030-01-15T10:02:00Z --output /private/cat-cohort/source.mp4
```

## Extract candidates and review at native resolution

Use a new private directory outside Git and recording shares (directories 0700,
files 0600). Save ffprobe duration, dimensions, codec and sample/extraction
commands. A sparse contact sheet helps navigate; inspect each accepted frame
at native resolution before assigning identity, boxes or exclusion status.

Example candidate sampling, one native-size JPEG every four seconds:

```sh
umask 077
mkdir -m 700 /private/cat-cohort/frames
ffprobe -v error -show_entries format=duration:stream=index,codec_name,width,height -of json /private/cat-cohort/source.mp4 > /private/cat-cohort/probe.json
ffmpeg -n -i /private/cat-cohort/source.mp4 -map 0:v:0 -vf 'fps=1/4' -q:v 2 /private/cat-cohort/frames/frame-%03d.jpg
```

Choose cadence for the action: long stationary intervals need fewer frames;
walking, rolling or transitions may need denser samples. Pick distinct pose,
position, distance, occlusion and interaction cases rather than consecutive
copies of an animal resting. Keep selection and rejection reasons for reviewed
candidates. Collection count is not the accepted/materialized case count.

Keep requested clip bounds, target timestamp, extraction offset/frame identity
and observed camera OSD separate. Assembled clips can include segment padding
or timeline gaps; sample index alone does not prove exact wall time. Retain
native OSD where available and state any timestamp uncertainty.

Manually verify identity from coat markings and nearby trajectory/reference
frames. IR brightness alone cannot distinguish a smaller white cat from a grey
cat. Morning timestamps do not prove visible-color lighting: annotate observed
IR monochrome versus visible color independently of time of day. Use uncertain
identity when the appearance does not support a stronger conclusion. Some
builder/evaluator schemas use `lighting: day` for observed visible color or
`lighting: night` for observed IR; keep the descriptive distinction in
`illumination` (for example, `visible light` or `IR monochrome`). These field
values describe the footage and need not match the clock time.

## Annotate and split sources

Use native pixel `[x1,y1,x2,y2]` boxes with positive area and coordinates within
the image. Label every scored animal in each accepted frame. Existing detector
boxes/scores are selection hints, never ground truth. Preserve behavior, camera,
identity, lighting, blur/occlusion notes and annotation revision.

Freeze exclusion criteria before inference. Mark ambiguous fragments or severe
occlusion/truncation explicitly, or reject the case. A frame with an excluded
cat is not a clean empty negative. Mild identifiable occlusion can remain if
the labeling convention is consistent. Manually review empty/hard-negative
scenes too; absence of an event does not prove absence of an animal.

Assign whole encounters to one split before generating crops. All alternate
views and adjacent frames from that encounter inherit it. One convention is a
300-second guard between opposing splits:
group connected/nearby encounter intervals across cameras, then assign each
group wholly to train or test. Choose and record a guard appropriate to the
deployment; a time gap alone does not guarantee independence. Check full source intervals against previous
train, validation, calibration and test sources, including prior additions;
exclude boundary-spanning tracks rather than splitting one into both sets.
Some dataset builders check historical sources using only a single timestamp
while newer additions carry full intervals. Inspect the actual checker and
retained source/event interval provenance; passed timestamp assertions alone
do not prove full historical encounter separation.

Detect exact duplicates using decoded full-frame pixel hashes before crop/box
selection, globally within new candidates and against accessible prior source
images. File hashes and crop hashes alone can miss identical decoded frames
with different encodings or annotations. Recompression/rescaling can also
change decoded pixels, so exact pixel hashing does not catch all copies.
Also review near-identical poses;
same-camera box overlap and cropped-image perceptual hashes can help but do
not establish encounter independence. Report source frames, object instances,
views and independent encounter groups as different counts.

## Build versioned datasets and verify usable cases

Keep native full frames for held-out evaluation. Generate representative square
views as a separate scale diagnostic. Ground-truth-centered crops are easier
than actual motion-region proposals; crop recall cannot establish event recall.
Label every object intersecting a supervised crop. Each intersecting animal
must be fully contained; disjoint objects can be omitted. Try smaller padding
if a large crop partially cuts another animal, then omit an infeasible crop
rather than silently truncating its label. Deduplicate identical crop bounds.

If the training loss cannot honor ignored regions, every supervised crop must
avoid all such regions. Do not turn excluded fragments into background loss.
Check independently that every source counted as an added training case
actually produced at least one valid saved crop; a builder may count source
rows even when it omits them from supervised loss.

Build into a fresh version directory. Preserve prior annotation/image rows,
IDs and split membership. For append-only expansions, retain prior rows as
exact prefixes and keep frozen validation manifests byte-identical. Record the base version,
source manifest, exact builder, new counts and SHA-256 hashes. Keep source
queries, clips, native frames, manual labels, rejected-case audits and scripts
in the private parent directory. Upload to durable private storage before
removing staging; local temporary files alone are not durable completion.

Validate image decoding/dimensions, all box bounds, duplicate IDs/pixels,
cross-split guards, inherited rows and materialized training-source coverage.
Run the actual installed inference evaluator's annotation loader on all views
before claiming readiness; a Frigate config parser does not validate datasets.

Locate the deployment's maintained dataset builder and inference evaluator in
its repository/operations notes. Discover the authoritative base version and
private durable storage there. Reuse the maintained builder, save its exact
version privately with the output, and inspect its CLI/schema and dependencies
before preparing a manifest. Keep deployment-specific paths and private corpus
locations in local operations notes rather than the published skill.

## Evaluation handoff and reporting

Follow `evaluation.md` and `models.md`. Score a new cohort separately before
changing weights or thresholds. Use fixed comparable preprocessing/runtime,
confidence and IoU rules; keep train/validation diagnostics apart from held-out
accuracy. Report native full frames and square views separately because they
are correlated scales of the same source, not independent trials. Stratify
white/grey/uncertain identity and IR/color where coverage supports it.

The generic `scripts/evaluate.py` detector schema has no ignored-region field.
Use an evaluator that implements the frozen ignore policy, or explicitly
exclude ambiguous cases from that scorer. Do not silently count ignored cats
as empty negatives. Preserve the ignore rule and raw predictions privately.

Report accepted source counts, resulting partition/view counts, split guard,
private durable location, summary-document link and limitations such as
successful-track selection bias or one-source daylight coverage. Prior accuracy
reports belong to their original manifest hashes. Added data is not evidence
of improved detection until inference/training is actually performed.
