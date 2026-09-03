# Colour At Ingest - field notes

Working notes on colour management at ingest. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the display encoded

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the transfer function

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the primaries

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the LUT

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the scene linear

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the display encoded

Seen while working on colour management at ingest. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the transfer function

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the primaries

Seen while working on colour management at ingest. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the LUT

Seen while working on colour management at ingest. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

## Worked examples

### Setup 1

Ran colour management at ingest with the transfer function at 4 and the sample count at 32. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 2

Ran colour management at ingest with the primaries at 8 and the sample count at 64. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 3

Ran colour management at ingest with the LUT at 12 and the sample count at 96. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 4

Ran colour management at ingest with the scene linear at 16 and the sample count at 128. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 5

Ran colour management at ingest with the display encoded at 20 and the sample count at 160. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 6

Ran colour management at ingest with the transfer function at 24 and the sample count at 192. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 7

Ran colour management at ingest with the primaries at 28 and the sample count at 224. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 8

Ran colour management at ingest with the LUT at 32 and the sample count at 256. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 9

Ran colour management at ingest with the scene linear at 36 and the sample count at 288. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 10

Ran colour management at ingest with the display encoded at 40 and the sample count at 320. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 11

Ran colour management at ingest with the transfer function at 44 and the sample count at 352. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

## Parameters

### Scene Linear 1

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Display Encoded 2

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Transfer Function 3

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Primaries 4

Controls how colour management at ingest responds to the primaries at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Lut 5

Controls how colour management at ingest responds to the LUT at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Scene Linear 6

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Display Encoded 7

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Transfer Function 8

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Primaries 9

Controls how colour management at ingest responds to the primaries at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Lut 10

Controls how colour management at ingest responds to the LUT at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Scene Linear 11

Controls how colour management at ingest responds to the scene linear at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Display Encoded 12

Controls how colour management at ingest responds to the display encoded at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Transfer Function 13

Controls how colour management at ingest responds to the transfer function at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Primaries 14

Controls how colour management at ingest responds to the primaries at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.
