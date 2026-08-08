"""OCR subsystem: detection gate, deferred queue, and an isolated worker.

Intentionally empty. Re-exporting `manager` (or anything that imports
`app.indexing.service`) from here would create the cycle

    app.indexing.extractors -> app.ocr -> app.indexing.service -> app.indexing.extractors

Import the submodules directly instead. The dependency direction is one-way:
`app.ocr` may import `app.indexing`, never the reverse at module scope.
"""
