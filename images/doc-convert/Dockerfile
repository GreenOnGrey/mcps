# doc-convert — LibreOffice и Chromium без сети (FTR.NAB.CHAT-0003 tech §3):
# официальный Gotenberg и сайдкар legacy (DOC/XLS/PPT/ODF → OOXML на том же
# LibreOffice). Тег образа — DOC_CONVERT_VERSION, версия Gotenberg —
# GOTENBERG_IMAGE из config/versions.env.
ARG GOTENBERG_IMAGE=gotenberg/gotenberg:8.38.1
FROM ${GOTENBERG_IMAGE}
ARG VERSION
USER root
RUN apt-get update && apt-get -y upgrade && apt-get install -y --no-install-recommends python3 fonts-paratype \
 && rm -rf /var/lib/apt/lists/* && fc-cache -f
COPY legacy.py registrymodifications.xcu /opt/doc-convert/
LABEL org.opencontainers.image.version="${VERSION}"
USER gotenberg
