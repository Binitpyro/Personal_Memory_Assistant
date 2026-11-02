# Attribute Transfer

## What it does

Attribute transfer copies values from one piece of geometry onto another that does not share its topology. The two do not need matching point counts or matching order.

## Radius

Too small a radius leaves target points with nothing in range and they keep their default value, which usually reads as holes. Too large and unrelated regions bleed into each other.

## Cost

The lookup dominates. Cost scales with target point count times the average number of source points inside the radius, so widening the radius is more expensive than it looks.

## Parameters

### Source Geometry 1

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Target Points 2

Controls how attribute transfer responds to the target points at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Search Radius 3

Controls how attribute transfer responds to the search radius at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Kernel 4

Controls how attribute transfer responds to the kernel at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Match 5

Controls how attribute transfer responds to the match at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Source Geometry 6

Controls how attribute transfer responds to the source geometry at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Target Points 7

Controls how attribute transfer responds to the target points at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Search Radius 8

Controls how attribute transfer responds to the search radius at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Kernel 9

Controls how attribute transfer responds to the kernel at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Match 10

Controls how attribute transfer responds to the match at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Source Geometry 11

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

## Troubleshooting

### Result looks wrong around the target points

Seen while working on attribute transfer. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the search radius

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the kernel

Seen while working on attribute transfer. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the match

Seen while working on attribute transfer. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the source geometry

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the target points

Seen while working on attribute transfer. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

## Why it behaves this way

Selection is by nearest source point inside the search radius, and when several fall inside it the values are blended by distance rather than the closest one winning outright.

## Worked examples

### Setup 1

Ran attribute transfer with the search radius at 4 and the sample count at 32. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 2

Ran attribute transfer with the kernel at 8 and the sample count at 64. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 3

Ran attribute transfer with the match at 12 and the sample count at 96. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 4

Ran attribute transfer with the source geometry at 16 and the sample count at 128. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 5

Ran attribute transfer with the target points at 20 and the sample count at 160. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 6

Ran attribute transfer with the search radius at 24 and the sample count at 192. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 7

Ran attribute transfer with the kernel at 28 and the sample count at 224. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 8

Ran attribute transfer with the match at 32 and the sample count at 256. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 9

Ran attribute transfer with the source geometry at 36 and the sample count at 288. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 10

Ran attribute transfer with the target points at 40 and the sample count at 320. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

## Troubleshooting

### Result looks wrong around the target points

Seen while working on attribute transfer. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the search radius

Seen while working on attribute transfer. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the kernel

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the match

Seen while working on attribute transfer. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the source geometry

Seen while working on attribute transfer. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the target points

Seen while working on attribute transfer. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the search radius

Seen while working on attribute transfer. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the kernel

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

## Parameters

### Source Geometry 1

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Target Points 2

Controls how attribute transfer responds to the target points at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Search Radius 3

Controls how attribute transfer responds to the search radius at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Kernel 4

Controls how attribute transfer responds to the kernel at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Match 5

Controls how attribute transfer responds to the match at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Source Geometry 6

Controls how attribute transfer responds to the source geometry at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Target Points 7

Controls how attribute transfer responds to the target points at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Search Radius 8

Controls how attribute transfer responds to the search radius at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Kernel 9

Controls how attribute transfer responds to the kernel at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Match 10

Controls how attribute transfer responds to the match at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Source Geometry 11

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Raising it widens the affected region and increases evaluation cost roughly in proportion.
