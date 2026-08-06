FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml setup.cfg README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim

ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="RepoSync" \
      org.opencontainers.image.description="Atomic force mirroring of Git repositories" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$VCS_REF"

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 reposync \
    && useradd --system --uid 10001 --gid reposync --create-home --home-dir /home/reposync reposync \
    && mkdir -p /etc/reposync /var/lib/reposync \
    && chown -R reposync:reposync /etc/reposync /var/lib/reposync

COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

USER reposync
VOLUME ["/var/lib/reposync"]
ENTRYPOINT ["reposync"]
CMD ["run", "--config", "/etc/reposync/config.yml"]
