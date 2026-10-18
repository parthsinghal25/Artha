FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/Artha/Implementation/ai-backend
COPY requirements.lock ./requirements.lock
RUN python -m pip install --only-binary=:all: --no-deps -r requirements.lock && python -m pip check
COPY sdbackend ./sdbackend
COPY contracts ./contracts
COPY BUILD-INFO.json ./BUILD-INFO.json
ENV SDB_SHARED_CONTRACT_DIR=/app/Artha/Implementation/ai-backend/contracts/shared SDB_DATA_CONTRACT_DIR=/app/Artha/Implementation/ai-backend/contracts/data SDB_NO_DOTENV=1 SDB_MODEL_PROVIDER=vertex SDB_VERTEX_LOCATION=global SDB_VERTEX_MODEL=gemini-3.8-flash SDB_MODEL_CALL_TIMEOUT_S=65 SDB_MODEL_CONCURRENCY=1 SDB_SERVING_STATE=sqlite
RUN python -c "from google import genai; from sdbackend.models.vertex import VertexProvider; from sdbackend.cloud.vertex_job import validate_input; from sdbackend.intel import worker, memory, discovery; from sdbackend.contracts import load_contracts; load_contracts(); print('Linux job imports and frozen contracts verified')"
USER 65534:65534
CMD ["python", "-m", "sdbackend.cloud.vertex_job"]
