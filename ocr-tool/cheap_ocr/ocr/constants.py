import os

# Mô hình OCR nhận diện chữ: Mặc định chuyển sang GLM-OCR (THUDM/glm-4v-9b) theo yêu cầu kiểm thử tốc độ.
# Có thể override bằng biến môi trường CHEAP_OCR_MODEL (vd: Qwen/Qwen2.5-VL-7B-Instruct)
OCR_MODEL = os.getenv("CHEAP_OCR_MODEL", "THUDM/glm-4v-9b")
SERVED_MODEL_NAME = os.getenv("CHEAP_OCR_SERVED_NAME", "glm-ocr")

