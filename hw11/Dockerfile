# Build context: mlops_hw, so the original backend is reused, not copied/forked.
FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/hw11
COPY hw11/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY hw8910/backends.py hw8910/release.py /srv/hw8910/
COPY hw11/app ./app
COPY hw11/artifacts ./artifacts
COPY hw11/scripts ./scripts
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "55", "--timeout-keep-alive", "2"]
