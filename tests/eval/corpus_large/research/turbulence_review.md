# Turbulence Octaves - review

Working notes on turbulence octaves. These are observations from shot work rather than a reference page, and they deliberately do not restate the underlying reason the setup behaves the way it does.

## Troubleshooting

### Result looks wrong around the lacunarity

Seen while working on turbulence octaves. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the roughness

Seen while working on turbulence octaves. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the amplitude

Seen while working on turbulence octaves. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

### Result looks wrong around the frequency

Seen while working on turbulence octaves. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the octave

Seen while working on turbulence octaves. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the lacunarity

Seen while working on turbulence octaves. Usually a resolution mismatch between what was authored and what is being evaluated. It disappears when they are matched.

### Result looks wrong around the roughness

Seen while working on turbulence octaves. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the amplitude

Seen while working on turbulence octaves. Reproducible only with a cold cache, which is why it survived review for as long as it did.

### Result looks wrong around the frequency

Seen while working on turbulence octaves. This one is a genuine limitation rather than a misconfiguration, and the workaround costs an extra evaluation.

## Worked examples

### Setup 1

Ran turbulence octaves with the roughness at 4 and the sample count at 32. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 2

Ran turbulence octaves with the amplitude at 8 and the sample count at 64. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 3

Ran turbulence octaves with the frequency at 12 and the sample count at 96. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 4

Ran turbulence octaves with the octave at 16 and the sample count at 128. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 5

Ran turbulence octaves with the lacunarity at 20 and the sample count at 160. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 6

Ran turbulence octaves with the roughness at 24 and the sample count at 192. Marginal on a workstation and comfortable on the farm, so it depends entirely on where the work runs.

### Setup 7

Ran turbulence octaves with the amplitude at 28 and the sample count at 224. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 8

Ran turbulence octaves with the frequency at 32 and the sample count at 256. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

### Setup 9

Ran turbulence octaves with the octave at 36 and the sample count at 288. This overshot the budget by roughly a third and was dropped, but it is recorded because the look was closer.

### Setup 10

Ran turbulence octaves with the lacunarity at 40 and the sample count at 320. The run finished inside the frame budget and the result held up under review, so this is the setup that shipped.

### Setup 11

Ran turbulence octaves with the roughness at 44 and the sample count at 352. Comparable quality at noticeably lower cost, which is what made it worth keeping in the notes.

## Parameters

### Octave 1

Controls how turbulence octaves responds to the octave at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Lacunarity 2

Controls how turbulence octaves responds to the lacunarity at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Roughness 3

Controls how turbulence octaves responds to the roughness at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Amplitude 4

Controls how turbulence octaves responds to the amplitude at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Frequency 5

Controls how turbulence octaves responds to the frequency at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do.

### Octave 6

Controls how turbulence octaves responds to the octave at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Lacunarity 7

Controls how turbulence octaves responds to the lacunarity at this stage of evaluation. The default is chosen for mid-scale setups and is usually too low on anything shot at close range. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Roughness 8

Controls how turbulence octaves responds to the roughness at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.

### Amplitude 9

Controls how turbulence octaves responds to the amplitude at this stage of evaluation. Leave it at the default unless a specific artefact is pushing you off it, and write down why when you do. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Frequency 10

Controls how turbulence octaves responds to the frequency at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. Raising it widens the affected region and increases evaluation cost roughly in proportion.

### Octave 11

Controls how turbulence octaves responds to the octave at this stage of evaluation. Raising it widens the affected region and increases evaluation cost roughly in proportion. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene.

### Lacunarity 12

Controls how turbulence octaves responds to the lacunarity at this stage of evaluation. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug. It interacts with the sampling rate, so changing one without the other moves the result in ways that look like a bug.

### Roughness 13

Controls how turbulence octaves responds to the roughness at this stage of evaluation. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it. The default is chosen for mid-scale setups and is usually too low on anything shot at close range.

### Amplitude 14

Controls how turbulence octaves responds to the amplitude at this stage of evaluation. It has no effect at all when the upstream input is uniform, which makes it look broken on a test scene. Values below the floor are clamped silently, which is worth knowing before spending an afternoon on it.
