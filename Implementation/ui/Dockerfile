# Static UI + same-origin gateway. Build context: the dist/ directory produced by build.py.
#   python3 build.py --hosting superdashboard-serving   (copies this file into dist/)
#   gcloud builds submit dist --tag us-central1-docker.pkg.dev/<project>/superdashboard/ui-gateway:<build>
FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV SD_HOST=0.0.0.0 SD_PORT=8080 SD_CONTRACT_ROOT=/app/contracts
USER 65534
EXPOSE 8080
CMD ["python3", "/app/server/devserver.py"]
