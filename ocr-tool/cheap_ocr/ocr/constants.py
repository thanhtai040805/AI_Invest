"""OCR model identifiers used by the local vLLM server.

Qwen2.5-VL-7B-Instruct is used for recognition: unlike GLM-OCR it was trained
on multilingual OCR data that includes Vietnamese, so it preserves Vietnamese
diacritics instead of dropping them and confusing visually similar Cyrillic/
CJK glyphs.
"""

OCR_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
SERVED_MODEL_NAME = "qwen25-vl-7b"
