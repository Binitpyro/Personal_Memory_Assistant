# Reading notes: divergence-free vector fields

Bridson's paper argues that taking the curl of a potential guarantees the
result has zero divergence, which is why the smoke never gains or loses
volume as it advects. I spent an afternoon convincing myself of this by hand
on a 2D field and it does fall out of the identity directly.

The practical upshot for our shots is that we stop needing a pressure solve
purely to keep the look plausible. That was eating most of the sim budget on
the tower sequence. Worth revisiting whether the cheaper field is good enough
for hero elements or only for background.

I remain unconvinced about the octave count recommendation. Three reads as
too few at our scale.
