# Curl Noise - review

Working notes on curl noise. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the divergence

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the vorticity

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the gradient

Seen while working on curl noise. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the streamline

Seen while working on curl noise. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the potential field

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the divergence

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the vorticity

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the gradient

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the streamline

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

## Worked examples

### Setup 1

Ran curl noise with the vorticity at 4 and the sample count at 32. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 2

Ran curl noise with the gradient at 8 and the sample count at 64. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 3

Ran curl noise with the streamline at 12 and the sample count at 96. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 4

Ran curl noise with the potential field at 16 and the sample count at 128. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 5

Ran curl noise with the divergence at 20 and the sample count at 160. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 6

Ran curl noise with the vorticity at 24 and the sample count at 192. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 7

Ran curl noise with the gradient at 28 and the sample count at 224. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 8

Ran curl noise with the streamline at 32 and the sample count at 256. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 9

Ran curl noise with the potential field at 36 and the sample count at 288. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 10

Ran curl noise with the divergence at 40 and the sample count at 320. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 11

Ran curl noise with the vorticity at 44 and the sample count at 352. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

## Parameters

### Potential Field 1

Controls how curl noise responds to the potential field at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Divergence 2

Controls how curl noise responds to the divergence at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Vorticity 3

Controls how curl noise responds to the vorticity at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Gradient 4

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Streamline 5

Controls how curl noise responds to the streamline at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Potential Field 6

Controls how curl noise responds to the potential field at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Divergence 7

Controls how curl noise responds to the divergence at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Vorticity 8

Controls how curl noise responds to the vorticity at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Gradient 9

Controls how curl noise responds to the gradient at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Streamline 10

Controls how curl noise responds to the streamline at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Potential Field 11

Controls how curl noise responds to the potential field at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Divergence 12

Controls how curl noise responds to the divergence at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Vorticity 13

Controls how curl noise responds to the vorticity at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Gradient 14

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.
