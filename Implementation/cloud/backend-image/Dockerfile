# Artha read-serving backend (internal sidecar of Cloud Run service superdashboard-serving).
# Build context: Implementation/cloud/dist/backend, assembled by build_backend_context.py (allow-list copy only).
# No model weights, MLX, Apple Vision/pyobjc, google-genai, .env, stores, login codes or authoring material.
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/Artha/Implementation/ai-backend
COPY requirements/cloud.lock /app/Artha/Implementation/ai-backend/requirements/cloud.lock
# exact pins, binary wheels only (every pin was checked for a cp312 manylinux x86_64 wheel), then a dependency check
RUN python -m pip install --only-binary=:all: --no-deps -r /app/Artha/Implementation/ai-backend/requirements/cloud.lock && python -m pip check
COPY sdbackend /app/Artha/Implementation/ai-backend/sdbackend
COPY contracts /app/Artha/Implementation/ai-backend/contracts
COPY BUILD-INFO.json /app/Artha/Implementation/ai-backend/BUILD-INFO.json
ENV SDB_SHARED_CONTRACT_DIR=/app/Artha/Implementation/ai-backend/contracts/shared \
    SDB_DATA_CONTRACT_DIR=/app/Artha/Implementation/ai-backend/contracts/data \
    SDB_NO_DOTENV=1 \
    SDB_HOSTED_DEMO=1 \
    SDB_SERVING_STATE=firestore \
    ARTHA_PUBLICATION_SOURCE=firestore \
    ARTHA_RESTORE_ROOT=/tmp/artha \
    SDB_HOST=127.0.0.1 \
    SDB_PORT=8091
USER 65534:65534
EXPOSE 8091
CMD ["python", "-m", "sdbackend.cloud.boot"]
