# Curl Noise - field notes

Working notes on curl noise. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the divergence

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the vorticity

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the gradient

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the streamline

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the potential field

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the divergence

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the vorticity

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the gradient

Seen while working on curl noise. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the streamline

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

## Worked examples

### Setup 1

Ran curl noise with the vorticity at 4 and the sample count at 32. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 2

Ran curl noise with the gradient at 8 and the sample count at 64. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 3

Ran curl noise with the streamline at 12 and the sample count at 96. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 4

Ran curl noise with the potential field at 16 and the sample count at 128. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 5

Ran curl noise with the divergence at 20 and the sample count at 160. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 6

Ran curl noise with the vorticity at 24 and the sample count at 192. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 7

Ran curl noise with the gradient at 28 and the sample count at 224. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 8

Ran curl noise with the streamline at 32 and the sample count at 256. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 9

Ran curl noise with the potential field at 36 and the sample count at 288. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 10

Ran curl noise with the divergence at 40 and the sample count at 320. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 11

Ran curl noise with the vorticity at 44 and the sample count at 352. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

## Parameters

### Potential Field 1

Controls how curl noise responds to the potential field at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Divergence 2

Controls how curl noise responds to the divergence at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Vorticity 3

Controls how curl noise responds to the vorticity at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Gradient 4

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Streamline 5

Controls how curl noise responds to the streamline at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Potential Field 6

Controls how curl noise responds to the potential field at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Divergence 7

Controls how curl noise responds to the divergence at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Vorticity 8

Controls how curl noise responds to the vorticity at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Gradient 9

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Streamline 10

Controls how curl noise responds to the streamline at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Potential Field 11

Controls how curl noise responds to the potential field at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Divergence 12

Controls how curl noise responds to the divergence at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Vorticity 13

Controls how curl noise responds to the vorticity at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Gradient 14

Controls how curl noise responds to the gradient at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.
