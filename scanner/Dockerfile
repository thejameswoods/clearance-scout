# Microsoft's Playwright image, kept as the base purely for its prebaked
# OS-level browser dependencies (fonts, codecs, etc.) even though the
# vanilla `playwright` pip package itself isn't used anymore -- see
# requirements.txt. `patchright install --with-deps chrome` below fetches
# the real, patchright-compatible Chrome build and fills in anything this
# base image doesn't already cover.
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
# Patchright bundles its own patched driver and needs its own browser
# fetch -- the vanilla Chromium baked into the base image isn't it.
# "chrome" (real Google Chrome), not "chromium", per patchright's own
# documented best-practice config for staying undetected.
RUN DEBIAN_FRONTEND=noninteractive patchright install --with-deps chrome

COPY adapters/ adapters/
COPY common/ common/
COPY scanner/ scanner/

ENV DISPLAY=:99 \
    PLAYWRIGHT_PROFILE_DIR=/data/browser-profile \
    PYTHONUNBUFFERED=1

VOLUME ["/data/browser-profile"]

EXPOSE 6901 8090

ENTRYPOINT ["scanner/entrypoint.sh"]
