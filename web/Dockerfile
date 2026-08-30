FROM python:3.12-slim

WORKDIR /app

COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY common/ common/
COPY web/ web/

# Stamped at image build time so the running dashboard can show exactly
# when it was last rebuilt -- cheap way to rule out "am I looking at a
# stale cached build" during active iteration, no git/CI wiring required.
RUN date -u +%Y-%m-%dT%H:%M:%SZ > web/BUILD_TIME

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
