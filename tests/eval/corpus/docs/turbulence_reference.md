# Turbulence

Turbulence adds procedural turbulence detail to a velocity field. The
turbulence node layers several turbulence octaves, each turbulence octave at
double the frequency and half the amplitude of the previous turbulence
octave.

## Turbulence parameters

Turbulence scale sets the size of the largest turbulence feature. Turbulence
roughness controls how quickly turbulence amplitude falls off across
turbulence octaves.

## Applying turbulence

Apply turbulence after advection so the turbulence detail is not smoothed by
the advection step.
