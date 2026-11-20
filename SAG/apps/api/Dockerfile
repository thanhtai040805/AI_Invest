FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# Install dependency layers separately from application code.
# Keep the LiteLLM/MCP SDK pair aligned with uv.lock; newer LiteLLM needs MCP v2.
COPY pyproject.toml uv.lock ./
RUN pip install --upgrade pip hatchling \
 && python -c 'import re, tomllib; p=tomllib.load(open("pyproject.toml", "rb")); locked={pkg["name"]:pkg["version"] for pkg in tomllib.load(open("uv.lock", "rb"))["package"]}; deps=list(p["project"]["dependencies"])+p["project"]["optional-dependencies"]["postgres"]; deps=[name+"=="+locked[name] if (name:=re.match(r"[A-Za-z0-9_.-]+", dep).group()) in {"litellm", "mcp"} else dep for dep in deps]; print("\n".join(deps))' > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

# 再安装本项目本身（无依赖解析，保持快速重建）。
COPY sag_api ./sag_api
COPY sag_agent ./sag_agent
RUN pip install --no-deps --no-build-isolation .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/system/health || exit 1

CMD ["uvicorn", "sag_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
