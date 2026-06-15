# step 1: install base image
# python:3.12-slim gives a lean Debian base with Python pre-installed.
FROM python:3.12-slim
ARG PORT=4999
ENV APP_PORT=$PORT

# step 2: any OS-level dependency install only, gcc included as sometimes .py libraries need to compile C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# step 3: work directory for the app/system
WORKDIR /app

# step 4: .py libraries installation
# do this first before adding our files so Docker caches it separately, skips rebuilding if requirements.txt doesn't change
# gunicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# step 5: copy everything in the current working directory (host machine) into the CWD of the container
COPY . .

# create directories the app expects to exist at runtime (based on current routes)
RUN mkdir -p /app/models/saved_models /app/reports /app/backend /app/data/processed

# runtime config stuff
# expose the port Flask/Gunicorn will listen on
EXPOSE $APP_PORT

# FLASK_ENV tells Flask not to use the reloader inside the container (turns off Flask debugger and server restarts when files change)
ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1

# actual launch command
# Gunicorn is a proper production WSGI server (more stable than flask's at least)
# Workers=2 is safe for a single-user local tool; would have to scale up a bit for a networked, multi-user tool (would also likely required some beefier hardware as well)
# Binding to 0.0.0.0 is required so Docker's port mapping can reach it.
CMD ["sh", "-c", "gunicorn --workers=2 --bind=0.0.0.0:${APP_PORT} --timeout=600 app.ui.app:app"]
