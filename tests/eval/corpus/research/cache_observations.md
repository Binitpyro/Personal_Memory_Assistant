# Observations on stale reads

Three times this month a shot rendered with geometry from the previous
version. Each time the file on disk had been replaced but the process kept
serving the old copy from memory.

The common factor was that the timestamp comparison used second resolution
while our writes complete well inside one second. Two edits in the same
second are indistinguishable to that check. Content hashing would settle it
but costs a full read of every candidate.

I want to measure how often that read actually happens before arguing for it.
