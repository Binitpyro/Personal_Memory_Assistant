# Velocity Advection - field notes

Working notes on velocity field advection. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the backward trace

Seen while working on velocity field advection. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the interpolation

Seen while working on velocity field advection. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the step size

Seen while working on velocity field advection. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the stability

Seen while working on velocity field advection. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the velocity field

Seen while working on velocity field advection. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the backward trace

Seen while working on velocity field advection. Almost always an upstream input that is not what it is assumed to be. Check it before changing any setting here.

### Result looks wrong around the interpolation

Seen while working on velocity field advection. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the step size

Seen while working on velocity field advection. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the stability

Seen while working on velocity field advection. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

## Worked examples

### Setup 1

Ran velocity field advection with the interpolation at 4 and the sample count at 32. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 2

Ran velocity field advection with the step size at 8 and the sample count at 64. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 3

Ran velocity field advection with the stability at 12 and the sample count at 96. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 4

Ran velocity field advection with the velocity field at 16 and the sample count at 128. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 5

Ran velocity field advection with the backward trace at 20 and the sample count at 160. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 6

Ran velocity field advection with the interpolation at 24 and the sample count at 192. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 7

Ran velocity field advection with the step size at 28 and the sample count at 224. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 8

Ran velocity field advection with the stability at 32 and the sample count at 256. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 9

Ran velocity field advection with the velocity field at 36 and the sample count at 288. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 10

Ran velocity field advection with the backward trace at 40 and the sample count at 320. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 11

Ran velocity field advection with the interpolation at 44 and the sample count at 352. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

## Parameters

### Velocity Field 1

Controls how velocity field advection responds to the velocity field at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Backward Trace 2

Controls how velocity field advection responds to the backward trace at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Interpolation 3

Controls how velocity field advection responds to the interpolation at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Step Size 4

Controls how velocity field advection responds to the step size at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Stability 5

Controls how velocity field advection responds to the stability at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Velocity Field 6

Controls how velocity field advection responds to the velocity field at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Backward Trace 7

Controls how velocity field advection responds to the backward trace at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Interpolation 8

Controls how velocity field advection responds to the interpolation at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Step Size 9

Controls how velocity field advection responds to the step size at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Stability 10

Controls how velocity field advection responds to the stability at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Velocity Field 11

Controls how velocity field advection responds to the velocity field at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Backward Trace 12

Controls how velocity field advection responds to the backward trace at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Interpolation 13

Controls how velocity field advection responds to the interpolation at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Step Size 14

Controls how velocity field advection responds to the step size at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.
