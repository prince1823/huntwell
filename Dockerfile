# Playwright's own image already ships Chromium plus every system library
# it needs -- a plain python:3.11 image would need `playwright install-deps`
# (a long list of apt packages) added by hand, and it's easy to miss one.
# The tag's Playwright version must match requirements.txt's exactly, since
# the browser binaries it bundles are pinned to that version.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The frontend is already built and checked into webapp/static/ (see
# webapp/frontend/README.md) -- no Node.js stage needed here, just copy
# the repo as-is.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "webapp.server:app", "--host", "0.0.0.0", "--port", "8000"]
