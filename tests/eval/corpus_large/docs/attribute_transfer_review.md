# Attribute Transfer - review

Working notes on attribute transfer. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the target points

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the search radius

Seen while working on attribute transfer. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the kernel

Seen while working on attribute transfer. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the match

Seen while working on attribute transfer. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the source geometry

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the target points

Seen while working on attribute transfer. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the search radius

Seen while working on attribute transfer. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the kernel

Seen while working on attribute transfer. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the match

Seen while working on attribute transfer. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

## Worked examples

### Setup 1

Ran attribute transfer with the search radius at 4 and the sample count at 32. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 2

Ran attribute transfer with the kernel at 8 and the sample count at 64. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 3

Ran attribute transfer with the match at 12 and the sample count at 96. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 4

Ran attribute transfer with the source geometry at 16 and the sample count at 128. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 5

Ran attribute transfer with the target points at 20 and the sample count at 160. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 6

Ran attribute transfer with the search radius at 24 and the sample count at 192. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 7

Ran attribute transfer with the kernel at 28 and the sample count at 224. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 8

Ran attribute transfer with the match at 32 and the sample count at 256. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 9

Ran attribute transfer with the source geometry at 36 and the sample count at 288. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 10

Ran attribute transfer with the target points at 40 and the sample count at 320. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 11

Ran attribute transfer with the search radius at 44 and the sample count at 352. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

## Parameters

### Source Geometry 1

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Target Points 2

Controls how attribute transfer responds to the target points at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Search Radius 3

Controls how attribute transfer responds to the search radius at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Kernel 4

Controls how attribute transfer responds to the kernel at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Match 5

Controls how attribute transfer responds to the match at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Source Geometry 6

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Target Points 7

Controls how attribute transfer responds to the target points at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Search Radius 8

Controls how attribute transfer responds to the search radius at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Kernel 9

Controls how attribute transfer responds to the kernel at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Match 10

Controls how attribute transfer responds to the match at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Source Geometry 11

Controls how attribute transfer responds to the source geometry at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Target Points 12

Controls how attribute transfer responds to the target points at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Search Radius 13

Controls how attribute transfer responds to the search radius at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Kernel 14

Controls how attribute transfer responds to the kernel at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.
