# Solver

The solver node evaluates its contents once per frame, feeding the previous
frame result back as input. A solver builds state over time where an ordinary
node network does not.

## Solver and caching

A solver depends on every prior frame, so scrubbing backwards forces the
solver to re-evaluate from the start frame. Cache the solver output to avoid
this.

## Nested solvers

A solver may contain another solver. The inner solver evaluates once per outer
solver step.
