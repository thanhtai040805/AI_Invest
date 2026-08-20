"""Modal orchestration for the financial OCR pipeline (financial-ocr app).

The cheap-ocr app owns the GPU OCR workers; this app owns the CPU side:
page classification (download + prune on Modal CPU) and the batch supervisor.
The local machine only submits URLs and waits for the supervisor.
"""
