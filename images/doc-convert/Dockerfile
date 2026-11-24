# doc-convert: LibreOffice and Chromium without a network (FTR.NAB.CHAT-0003 tech §3):
# the official Gotenberg with the sidecar legacy (DOC/XLS/PPT/ODF → OOXML on the same
# LibreOffice). The tag of the image is DOC_CONVERT_VERSION, the version of Gotenberg is
# GOTENBERG_IMAGE from config/versions.env, the version of lok is pinned below.
ARG GOTENBERG_IMAGE=gotenberg/gotenberg:9.9.0

# lok converts documents with LibreOfficeKit; Gotenberg ships it as a prebuilt release
# binary. The one in the base image is built with Go 1.27.3, whose stdlib has a fixable
# HIGH (CVE-2026-97054, fixed in 1.27.4), and the release has no newer build yet: the
# same source is rebuilt here with the current Go against the LibreOfficeKit headers of
# Debian 13 — the LibreOffice the final image runs. The headers come from the package,
# unpacked, so the stage stays small. Drop the stage when a release of lok uses a Go with
# the fix.
ARG LOK_VERSION=v0.0.7
FROM golang:1.27.4-trixie AS lok
ARG LOK_VERSION
RUN apt-get update \
 && apt-get install -y --no-install-recommends dpkg \
 && cd /tmp && apt-get download libreofficekit-dev \
 && dpkg -x ./*.deb /tmp/lok \
 && CGO_ENABLED=1 CGO_CFLAGS="-I/tmp/lok/usr/include" go install github.com/gotenberg/lok/cmd/lok@${LOK_VERSION} \
 && rm -rf /var/lib/apt/lists/* /tmp/*.deb

FROM ${GOTENBERG_IMAGE}
ARG VERSION
USER root
# The file is ours: a binary of the same version, built with the current Go (see above).
COPY --from=lok /go/bin/lok /usr/bin/lok
RUN apt-get update && apt-get -y upgrade && apt-get install -y --no-install-recommends python3 \
      fonts-paratype fonts-montserrat \
 && rm -rf /var/lib/apt/lists/* && fc-cache -f \
 && fc-list : family | grep -qi montserrat
COPY legacy.py registrymodifications.xcu /opt/doc-convert/
LABEL org.opencontainers.image.version="${VERSION}"
USER gotenberg
