# Scatter Density

## Counts are approximate

The requested count is a target, not a guarantee. Relaxation and density weighting both move the final number, and on small surfaces the discrepancy is proportionally larger.

## Relaxation

Relaxation pushes points apart to even out spacing. It costs iterations and it fights the density attribute, so heavy relaxation flattens exactly the variation you asked for.

## Seeds

Changing the seed reshuffles every point. Anything downstream that binds to point number rather than position will pop when the seed moves.

## Parameters

### Density Attribute 1

Controls how point scattering responds to the density attribute at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Area 2

Controls how point scattering responds to the area at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Relaxation 3

Controls how point scattering responds to the relaxation at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Seed 4

Controls how point scattering responds to the seed at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Distribution 5

Controls how point scattering responds to the distribution at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Density Attribute 6

Controls how point scattering responds to the density attribute at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Area 7

Controls how point scattering responds to the area at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Relaxation 8

Controls how point scattering responds to the relaxation at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Seed 9

Controls how point scattering responds to the seed at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Distribution 10

Controls how point scattering responds to the distribution at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Density Attribute 11

Controls how point scattering responds to the density attribute at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

## Troubleshooting

### Result looks wrong around the area

Seen while working on point scattering. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the relaxation

Seen while working on point scattering. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the seed

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the distribution

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the density attribute

Seen while working on point scattering. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the area

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

## Why it behaves this way

Bind a per-point density attribute on the surface and the scatter treats it as a multiplier against the global count, so a value of zero suppresses points entirely and the total count is the integral of density over surface area rather than the number you typed.

## Worked examples

### Setup 1

Ran point scattering with the relaxation at 4 and the sample count at 32. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 2

Ran point scattering with the seed at 8 and the sample count at 64. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 3

Ran point scattering with the distribution at 12 and the sample count at 96. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 4

Ran point scattering with the density attribute at 16 and the sample count at 128. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 5

Ran point scattering with the area at 20 and the sample count at 160. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 6

Ran point scattering with the relaxation at 24 and the sample count at 192. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 7

Ran point scattering with the seed at 28 and the sample count at 224. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 8

Ran point scattering with the distribution at 32 and the sample count at 256. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 9

Ran point scattering with the density attribute at 36 and the sample count at 288. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 10

Ran point scattering with the area at 40 and the sample count at 320. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

## Troubleshooting

### Result looks wrong around the area

Seen while working on point scattering. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the relaxation

Seen while working on point scattering. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the seed

Seen while working on point scattering. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the distribution

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the density attribute

Seen while working on point scattering. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the area

Seen while working on point scattering. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the relaxation

Seen while working on point scattering. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the seed

Seen while working on point scattering. Reproducible only with a cold cache, which is why it survived review for as long as it did.

## Parameters

### Density Attribute 1

Controls how point scattering responds to the density attribute at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Area 2

Controls how point scattering responds to the area at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Relaxation 3

Controls how point scattering responds to the relaxation at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Seed 4

Controls how point scattering responds to the seed at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Distribution 5

Controls how point scattering responds to the distribution at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Density Attribute 6

Controls how point scattering responds to the density attribute at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Area 7

Controls how point scattering responds to the area at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Relaxation 8

Controls how point scattering responds to the relaxation at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Seed 9

Controls how point scattering responds to the seed at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Distribution 10

Controls how point scattering responds to the distribution at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Density Attribute 11

Controls how point scattering responds to the density attribute at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.
