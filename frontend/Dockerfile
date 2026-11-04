FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# Dev server with the /api and /ws proxy (used by docker compose).
FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]

# Production build: the static site is in /app/dist.
FROM deps AS build
COPY . .
RUN npm run build
