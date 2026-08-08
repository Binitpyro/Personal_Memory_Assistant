"""OCR worker package.

Every module here runs inside the isolated OCR venv and MUST NOT import
`app.*`. `tests/test_ocr_protocol.py` enforces that by AST inspection.

At install time these files are copied flat into `<ocr_env>/worker/` and run
by path, so they import each other by bare name (`import raster`), not as a
package.
"""
