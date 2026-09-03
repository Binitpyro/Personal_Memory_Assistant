# Scatter Density - field notes

Working notes on point scattering. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the area

Seen while working on point scattering. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the relaxation

Seen while working on point scattering. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the seed

Seen while working on point scattering. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the distribution

Seen while working on point scattering. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the density attribute

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the area

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the relaxation

Seen while working on point scattering. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the seed

Seen while working on point scattering. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the distribution

Seen while working on point scattering. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

## Worked examples

### Setup 1

Ran point scattering with the relaxation at 4 and the sample count at 32. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 2

Ran point scattering with the seed at 8 and the sample count at 64. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 3

Ran point scattering with the distribution at 12 and the sample count at 96. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 4

Ran point scattering with the density attribute at 16 and the sample count at 128. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 5

Ran point scattering with the area at 20 and the sample count at 160. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 6

Ran point scattering with the relaxation at 24 and the sample count at 192. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 7

Ran point scattering with the seed at 28 and the sample count at 224. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 8

Ran point scattering with the distribution at 32 and the sample count at 256. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 9

Ran point scattering with the density attribute at 36 and the sample count at 288. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 10

Ran point scattering with the area at 40 and the sample count at 320. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 11

Ran point scattering with the relaxation at 44 and the sample count at 352. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

## Parameters

### Density Attribute 1

Controls how point scattering responds to the density attribute at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Area 2

Controls how point scattering responds to the area at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Relaxation 3

Controls how point scattering responds to the relaxation at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Seed 4

Controls how point scattering responds to the seed at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Distribution 5

Controls how point scattering responds to the distribution at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Density Attribute 6

Controls how point scattering responds to the density attribute at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Area 7

Controls how point scattering responds to the area at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Relaxation 8

Controls how point scattering responds to the relaxation at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Seed 9

Controls how point scattering responds to the seed at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Distribution 10

Controls how point scattering responds to the distribution at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Density Attribute 11

Controls how point scattering responds to the density attribute at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Area 12

Controls how point scattering responds to the area at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Relaxation 13

Controls how point scattering responds to the relaxation at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Seed 14

Controls how point scattering responds to the seed at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.
