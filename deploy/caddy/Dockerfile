# Caddy with the built front end baked in (deploy/docker-compose.prod.yml).
# Main build context: frontend/. Named context "caddyconf": deploy/caddy/.
# Baking the site and the Caddyfile into the image means `docker compose up --build`
# picks up any change to either.

# The same steps as the build stage in frontend/Dockerfile. Keep the Node version in step.
FROM node:22-slim AS site
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build

FROM caddy:2.11-alpine
COPY --from=caddyconf Caddyfile /etc/caddy/Caddyfile
COPY --from=site /app/dist /srv
