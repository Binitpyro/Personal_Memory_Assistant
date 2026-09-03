# Farm Batching - field notes

Working notes on render farm batching. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the startup cost

Seen while working on render farm batching. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the batch size

Seen while working on render farm batching. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the scheduler

Seen while working on render farm batching. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the retry

Seen while working on render farm batching. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the task

Seen while working on render farm batching. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the startup cost

Seen while working on render farm batching. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the batch size

Seen while working on render farm batching. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the scheduler

Seen while working on render farm batching. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the retry

Seen while working on render farm batching. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

## Worked examples

### Setup 1

Ran render farm batching with the batch size at 4 and the sample count at 32. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 2

Ran render farm batching with the scheduler at 8 and the sample count at 64. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 3

Ran render farm batching with the retry at 12 and the sample count at 96. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 4

Ran render farm batching with the task at 16 and the sample count at 128. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 5

Ran render farm batching with the startup cost at 20 and the sample count at 160. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 6

Ran render farm batching with the batch size at 24 and the sample count at 192. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 7

Ran render farm batching with the scheduler at 28 and the sample count at 224. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 8

Ran render farm batching with the retry at 32 and the sample count at 256. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 9

Ran render farm batching with the task at 36 and the sample count at 288. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 10

Ran render farm batching with the startup cost at 40 and the sample count at 320. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 11

Ran render farm batching with the batch size at 44 and the sample count at 352. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

## Parameters

### Task 1

Controls how render farm batching responds to the task at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Startup Cost 2

Controls how render farm batching responds to the startup cost at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Batch Size 3

Controls how render farm batching responds to the batch size at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Scheduler 4

Controls how render farm batching responds to the scheduler at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Retry 5

Controls how render farm batching responds to the retry at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Task 6

Controls how render farm batching responds to the task at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Startup Cost 7

Controls how render farm batching responds to the startup cost at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Batch Size 8

Controls how render farm batching responds to the batch size at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Scheduler 9

Controls how render farm batching responds to the scheduler at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Retry 10

Controls how render farm batching responds to the retry at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Task 11

Controls how render farm batching responds to the task at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Startup Cost 12

Controls how render farm batching responds to the startup cost at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Batch Size 13

Controls how render farm batching responds to the batch size at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Scheduler 14

Controls how render farm batching responds to the scheduler at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.
