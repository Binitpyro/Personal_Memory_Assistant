# Curl Noise

## What the field is for

Curl noise gives a moving field that looks like fluid without running a fluid solve. It is cheap enough to evaluate per point per frame, which is what makes it usable on the counts we actually ship.

## Authoring the potential

Everything is controlled through the potential rather than the velocity. This takes some getting used to, because the shape you author and the motion you get are related but not the same thing.

## Boundaries

Near a collider the potential has to be flattened along the surface, otherwise the field pushes straight through it. Ramping the potential to a constant near the boundary is the usual fix and costs one extra lookup.

## Parameters

### Potential Field 1

Controls how curl noise responds to the potential field at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Divergence 2

Controls how curl noise responds to the divergence at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Vorticity 3

Controls how curl noise responds to the vorticity at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Gradient 4

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Streamline 5

Controls how curl noise responds to the streamline at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Potential Field 6

Controls how curl noise responds to the potential field at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Divergence 7

Controls how curl noise responds to the divergence at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Vorticity 8

Controls how curl noise responds to the vorticity at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Gradient 9

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Streamline 10

Controls how curl noise responds to the streamline at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Potential Field 11

Controls how curl noise responds to the potential field at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

## Troubleshooting

### Result looks wrong around the divergence

Seen while working on curl noise. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the vorticity

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the gradient

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the streamline

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the potential field

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the divergence

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

## Why it behaves this way

The reason the construction works is an identity rather than a tuning choice: the divergence of a curl is identically zero for any twice-differentiable potential field. So if the velocity is defined as the curl of some vector potential, incompressibility is not something the solver has to enforce afterwards, it is a property the field cannot violate in the first place. That is the whole appeal. A field built this way never needs a pressure projection pass to remove sources and sinks, because there were none to remove. The cost is that you give up direct control of the velocity itself: you author the potential and accept whatever velocity the curl produces from it.

## Worked examples

### Setup 1

Ran curl noise with the vorticity at 4 and the sample count at 32. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 2

Ran curl noise with the gradient at 8 and the sample count at 64. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 3

Ran curl noise with the streamline at 12 and the sample count at 96. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 4

Ran curl noise with the potential field at 16 and the sample count at 128. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 5

Ran curl noise with the divergence at 20 and the sample count at 160. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 6

Ran curl noise with the vorticity at 24 and the sample count at 192. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 7

Ran curl noise with the gradient at 28 and the sample count at 224. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 8

Ran curl noise with the streamline at 32 and the sample count at 256. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 9

Ran curl noise with the potential field at 36 and the sample count at 288. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 10

Ran curl noise with the divergence at 40 and the sample count at 320. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

## Troubleshooting

### Result looks wrong around the divergence

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the vorticity

Seen while working on curl noise. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the gradient

Seen while working on curl noise. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the streamline

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the potential field

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the divergence

Seen while working on curl noise. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the vorticity

Seen while working on curl noise. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the gradient

Seen while working on curl noise. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

## Parameters

### Potential Field 1

Controls how curl noise responds to the potential field at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Divergence 2

Controls how curl noise responds to the divergence at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Vorticity 3

Controls how curl noise responds to the vorticity at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Gradient 4

Controls how curl noise responds to the gradient at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Streamline 5

Controls how curl noise responds to the streamline at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Potential Field 6

Controls how curl noise responds to the potential field at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Divergence 7

Controls how curl noise responds to the divergence at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Vorticity 8

Controls how curl noise responds to the vorticity at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Gradient 9

Controls how curl noise responds to the gradient at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Streamline 10

Controls how curl noise responds to the streamline at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Potential Field 11

Controls how curl noise responds to the potential field at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.
