# Velocity Field Advection

## Advecting Points Through A Velocity Field

Each element is carried along by whatever is moving around it. At every step
we look up the local direction and speed where the element currently sits,
then move it that far.

## Backward Tracing In Advection

Rather than pushing forward we can trace where a value came from and read it
there. Doing it this way stays stable no matter how large the step is, at the
cost of some blurring each time we interpolate.

## Advection Step Size

Larger steps are cheaper and blurrier. Smaller steps preserve more of the
original structure but cost proportionally more.
