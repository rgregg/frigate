# Lightweight overlay on the official frigate image.
# Only rebuilds the web UI and copies changed files — no recompilation of
# ffmpeg, nginx binary, OpenVINO, TensorRT, etc.

ARG BASE_IMAGE=ghcr.io/blakeblackshear/frigate:stable-tensorrt

# Stage 1: rebuild web assets (only stage that takes time)
FROM node:20 AS web-build
WORKDIR /work
COPY web/package.json web/package-lock.json ./
RUN npm install
COPY web/ ./
RUN npm run build \
    && mv dist/BASE_PATH/monacoeditorwork/* dist/assets/ \
    && rm -rf dist/BASE_PATH

# Stage 2: overlay onto official image
FROM ${BASE_IMAGE}

# Copy modified Python source files
COPY frigate/ /opt/frigate/frigate/

# Copy rebuilt web assets
COPY --from=web-build /work/dist/ /opt/frigate/web/

# Copy modified nginx config
COPY docker/main/rootfs/usr/local/nginx/conf/nginx.conf /usr/local/nginx/conf/nginx.conf
