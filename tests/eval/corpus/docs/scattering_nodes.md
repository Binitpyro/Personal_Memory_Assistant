# Scatter

The scatter node distributes points across a geometry surface. Scatter density
may be driven by a geometry attribute, letting a scatter follow a painted
attribute mask.

## Scatter parameters

Force total count sets an exact scatter point count. Relax iterations push
scattered points apart for a more even scatter distribution.

## Scatter and attributes

Scattered points inherit interpolated attributes from the surface they were
scattered onto.
