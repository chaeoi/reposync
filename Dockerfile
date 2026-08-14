FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="RepoSync" \
      org.opencontainers.image.description="Atomic force mirroring of Git repositories" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$VCS_REF"

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --root-user-action=ignore "PyYAML==6.0.3" \
    && groupadd --gid 10001 reposync \
    && useradd --uid 10001 --gid reposync --create-home --home-dir /home/reposync \
        --shell /usr/sbin/nologin reposync \
    && mkdir -p /etc/reposync /var/lib/reposync \
    && chown -R reposync:reposync /etc/reposync /var/lib/reposync

WORKDIR /app
COPY --chown=reposync:reposync core ./core

USER reposync
VOLUME ["/var/lib/reposync"]
ENTRYPOINT ["python", "-m", "core"]
CMD ["run", "--config", "/etc/reposync/config.yml"]
