FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Image mặc định chỉ cài các phụ thuộc cho SQLite/LanceDB cục bộ; override production truyền postgres.
ARG SAG_API_EXTRAS=""

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# Cài phụ thuộc bên thứ ba trước; thay đổi code nghiệp vụ không làm lớp này mất cache.
COPY pyproject.toml ./
RUN pip install --upgrade pip hatchling \
 && python -c 'import os, tomllib; p=tomllib.load(open("pyproject.toml", "rb")); deps=list(p["project"]["dependencies"]); deps += p["project"]["optional-dependencies"].get(os.environ.get("SAG_API_EXTRAS", ""), []); print("\n".join(deps))' > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

# Rồi cài chính project này（không phân giải phụ thuộc, giữ build lại nhanh）。
COPY sag_api ./sag_api
COPY sag_agent ./sag_agent
RUN pip install --no-deps --no-build-isolation .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/system/health || exit 1

CMD ["uvicorn", "sag_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
