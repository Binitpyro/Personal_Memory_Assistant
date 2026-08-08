# Caching Nodes

The cache node holds evaluated geometry in memory so downstream nodes do not
re-evaluate the cache input. The cache is keyed on the input geometry and the
cache parameters.

## Cache invalidation

The cache invalidates when any upstream parameter changes. Cache invalidation
also occurs when the cache memory limit is exceeded, at which point the cache
evicts the oldest cached geometry.

## File cache

The file cache node writes cached geometry to disk. A file cache survives
between sessions where an in-memory cache does not.
