# Colour At Ingest

## Where it goes wrong

Almost every problem here is a double conversion or a missing one, and neither announces itself. The image still looks plausible, it is just wrong by a gamma.

## Naming

Encoding the intended space in the filename is crude and it works. It survives being copied between machines, which sidecar metadata frequently does not.

## Review

Review has to happen through the same display transform the shot will be graded under, otherwise notes are being given on an image nobody will ever see again.

## Parameters

### Scene Linear 1

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Display Encoded 2

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Transfer Function 3

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Primaries 4

Controls how colour management at ingest responds to the primaries at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Lut 5

Controls how colour management at ingest responds to the LUT at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Scene Linear 6

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Display Encoded 7

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Transfer Function 8

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Primaries 9

Controls how colour management at ingest responds to the primaries at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Lut 10

Controls how colour management at ingest responds to the LUT at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Scene Linear 11

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

## Troubleshooting

### Result looks wrong around the display encoded

Seen while working on colour management at ingest. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the transfer function

Seen while working on colour management at ingest. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the primaries

Seen while working on colour management at ingest. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the LUT

Seen while working on colour management at ingest. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the scene linear

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the display encoded

Seen while working on colour management at ingest. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

## Why it behaves this way

The rule at ingest is that anything feeding lighting maths has to be scene linear, and anything that was authored to be looked at on a monitor is display encoded until you convert it. Albedo and emission maps are the first kind and need the transfer function removed on read. Masks, roughness and normal maps are data rather than colour and must not be converted at all, because applying a transfer function to a roughness map silently changes its midpoint. The failure is subtle in both directions: converting twice darkens midtones, and not converting at all leaves lighting maths operating on values that are not proportional to light.

## Worked examples

### Setup 1

Ran colour management at ingest with the transfer function at 4 and the sample count at 32. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 2

Ran colour management at ingest with the primaries at 8 and the sample count at 64. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 3

Ran colour management at ingest with the LUT at 12 and the sample count at 96. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 4

Ran colour management at ingest with the scene linear at 16 and the sample count at 128. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 5

Ran colour management at ingest with the display encoded at 20 and the sample count at 160. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 6

Ran colour management at ingest with the transfer function at 24 and the sample count at 192. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 7

Ran colour management at ingest with the primaries at 28 and the sample count at 224. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 8

Ran colour management at ingest with the LUT at 32 and the sample count at 256. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 9

Ran colour management at ingest with the scene linear at 36 and the sample count at 288. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 10

Ran colour management at ingest with the display encoded at 40 and the sample count at 320. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

## Troubleshooting

### Result looks wrong around the display encoded

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the transfer function

Seen while working on colour management at ingest. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the primaries

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the LUT

Seen while working on colour management at ingest. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the scene linear

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the display encoded

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the transfer function

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the primaries

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

## Parameters

### Scene Linear 1

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Display Encoded 2

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Transfer Function 3

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Primaries 4

Controls how colour management at ingest responds to the primaries at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Lut 5

Controls how colour management at ingest responds to the LUT at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Scene Linear 6

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Display Encoded 7

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Transfer Function 8

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Primaries 9

Controls how colour management at ingest responds to the primaries at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Lut 10

Controls how colour management at ingest responds to the LUT at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Scene Linear 11

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.
