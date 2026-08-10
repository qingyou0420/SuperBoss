# node:24-alpine
FROM node@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src

ARG VITE_OBJECT_STORAGE_ORIGIN
RUN test -n "$VITE_OBJECT_STORAGE_ORIGIN" && npm run build

# nginxinc/nginx-unprivileged:1.29-alpine
FROM nginxinc/nginx-unprivileged@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

USER 101:101

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
