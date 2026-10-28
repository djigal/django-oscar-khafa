# Development image for the dev container. The source is bind-mounted at /app by
# docker-compose.yml, so Python/JS dependencies are installed by postCreateCommand
# (make install) rather than baked into the image.
FROM python:3.12

ENV PYTHONUNBUFFERED=1

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs gettext \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

WORKDIR /app
