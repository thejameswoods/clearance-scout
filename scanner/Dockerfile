# Playwright's own image ships Chromium + all its system deps prebuilt and
# version-matched to the playwright pip package pinned in requirements.txt —
# far less brittle than installing Chromium deps by hand on a generic base.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# Xvfb + x11vnc + noVNC: the scanner runs a real, visible (not headless)
# Chromium so the one-time interactive Home Depot login (CAPTCHA/2FA) can be
# driven by a human over noVNC, then the same persistent profile is reused
# headed-but-unattended for every scheduled scan afterward.
# DEBIAN_FRONTEND=noninteractive avoids a debconf prompt (e.g. from a
# tzdata/keyboard-configuration dependency of these packages) hanging the
# build forever on CI, where there's no TTY to answer it.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY scanner/requirements.txt scanner/requirements.txt
RUN pip install --no-cache-dir -r scanner/requirements.txt

COPY adapters/ adapters/
COPY common/ common/
COPY scanner/ scanner/

ENV DISPLAY=:99 \
    PLAYWRIGHT_PROFILE_DIR=/data/browser-profile \
    PYTHONUNBUFFERED=1

VOLUME ["/data/browser-profile"]

EXPOSE 6901 8090

ENTRYPOINT ["scanner/entrypoint.sh"]
