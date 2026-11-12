# Development image for the dev container. The source is bind-mounted at /app by
# docker-compose.yml, so Python/JS dependencies are installed by postCreateCommand
# (make install) rather than baked into the image.
FROM python:3.14-slim

# The slim image has no curl, make or git (make install and VS Code need them)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential ca-certificates make git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs gettext \
    && apt-get -y install libpq-dev gcc git git-flow vim bash-completion \
    && rm -rf /var/lib/apt/lists/*


# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

RUN pip install --upgrade pip

WORKDIR /app
