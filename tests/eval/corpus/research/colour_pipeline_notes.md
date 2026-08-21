# Colour handling, informal notes

Everything upstream of comp is scene linear and everything downstream assumes
a display transform has been applied. The bugs all come from files that sit
ambiguously in between, usually textures painted by someone working outside
the pipeline.

A validation step at ingest would catch most of it. Nobody wants to own that
step.
