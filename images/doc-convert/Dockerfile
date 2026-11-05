# doc-convert: LibreOffice and Chromium without a network (FTR.NAB.CHAT-0003 tech §3):
# the official Gotenberg with the sidecar legacy (DOC/XLS/PPT/ODF → OOXML on the same
# LibreOffice). The tag of the image is DOC_CONVERT_VERSION, the version of Gotenberg is
# GOTENBERG_IMAGE from config/versions.env.
ARG GOTENBERG_IMAGE=gotenberg/gotenberg:8.38.1
FROM ${GOTENBERG_IMAGE}
ARG VERSION
USER root
RUN apt-get update && apt-get -y upgrade && apt-get install -y --no-install-recommends python3 fonts-paratype \
 && rm -rf /var/lib/apt/lists/* && fc-cache -f
COPY legacy.py registrymodifications.xcu /opt/doc-convert/
LABEL org.opencontainers.image.version="${VERSION}"
USER gotenberg
