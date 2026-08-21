# Notes on the semi-Lagrangian paper

The unconditional stability claim is real but it buys that stability with
smoothing. Every step interpolates, every interpolation loses a little, and
after a hundred steps the result is noticeably softer than where it started.

Their compensation scheme recovers some of it. Whether it is worth the second
lookup depends entirely on how many steps you take.
