# Noise Nodes

The noise node family generates procedural noise for geometry attributes.
Each noise node samples a noise function at every point and writes the noise
value into an attribute. Curl noise is the divergence-free noise variant used
for velocity attributes; curl noise guarantees the noise field has zero
divergence.

## Noise parameters

Every noise node exposes frequency, amplitude, octaves and offset. Increasing
noise octaves adds finer noise detail at higher noise cost. The noise offset
shifts the noise field without changing the noise pattern.

## Noise attribute output

The noise node writes to a point attribute by default. Set the attribute class
to primitive to write the noise value onto primitives instead.
