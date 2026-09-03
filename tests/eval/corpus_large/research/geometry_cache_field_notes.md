# Geometry Cache - field notes

Working notes on the geometry cache. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the invalidation

Seen while working on the geometry cache. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the frame range

Seen while working on the geometry cache. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the checkpoint

Seen while working on the geometry cache. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the revision

Seen while working on the geometry cache. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the cache key

Seen while working on the geometry cache. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the invalidation

Seen while working on the geometry cache. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the frame range

Seen while working on the geometry cache. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the checkpoint

Seen while working on the geometry cache. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the revision

Seen while working on the geometry cache. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

## Worked examples

### Setup 1

Ran the geometry cache with the frame range at 4 and the sample count at 32. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 2

Ran the geometry cache with the checkpoint at 8 and the sample count at 64. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 3

Ran the geometry cache with the revision at 12 and the sample count at 96. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 4

Ran the geometry cache with the cache key at 16 and the sample count at 128. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 5

Ran the geometry cache with the invalidation at 20 and the sample count at 160. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 6

Ran the geometry cache with the frame range at 24 and the sample count at 192. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 7

Ran the geometry cache with the checkpoint at 28 and the sample count at 224. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 8

Ran the geometry cache with the revision at 32 and the sample count at 256. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 9

Ran the geometry cache with the cache key at 36 and the sample count at 288. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 10

Ran the geometry cache with the invalidation at 40 and the sample count at 320. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 11

Ran the geometry cache with the frame range at 44 and the sample count at 352. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

## Parameters

### Cache Key 1

Controls how the geometry cache responds to the cache key at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Invalidation 2

Controls how the geometry cache responds to the invalidation at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Frame Range 3

Controls how the geometry cache responds to the frame range at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Checkpoint 4

Controls how the geometry cache responds to the checkpoint at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Revision 5

Controls how the geometry cache responds to the revision at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Cache Key 6

Controls how the geometry cache responds to the cache key at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Invalidation 7

Controls how the geometry cache responds to the invalidation at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Frame Range 8

Controls how the geometry cache responds to the frame range at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Checkpoint 9

Controls how the geometry cache responds to the checkpoint at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Revision 10

Controls how the geometry cache responds to the revision at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Cache Key 11

Controls how the geometry cache responds to the cache key at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Invalidation 12

Controls how the geometry cache responds to the invalidation at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Frame Range 13

Controls how the geometry cache responds to the frame range at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Checkpoint 14

Controls how the geometry cache responds to the checkpoint at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.
