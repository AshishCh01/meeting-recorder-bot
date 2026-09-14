# frontend/Dockerfile
# Stage 1: build the static Vite bundle
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# VITE_* vars are baked into the JS bundle at build time, not read at
# container runtime - they must be passed in as build args.
ARG VITE_API_URL
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_SUPABASE_URL=$VITE_SUPABASE_URL
ENV VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY
RUN npm run build

# Stage 2: serve the built /dist folder with a tiny static server
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# React Router / client-side routing: unknown paths should fall back
# to index.html instead of nginx's default 404.
#
# Written as a *.template into /etc/nginx/templates/, not directly into
# conf.d/ - the official nginx image's entrypoint runs envsubst over
# every file there at container start and writes the result into
# conf.d/, substituting ${PORT}. Cloud Run injects a PORT env var and
# expects the container to listen on it (which it can pick per
# deployment, so a static `listen 80` doesn't reliably match). ENV
# PORT=80 below is the fallback for platforms that don't set PORT
# (local docker run, Render if it doesn't inject one) - Cloud Run's own
# injected value overrides it at runtime.
ENV PORT=80
RUN mkdir -p /etc/nginx/templates
RUN echo 'server { \
    listen ${PORT}; \
    location / { \
        root /usr/share/nginx/html; \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/templates/default.conf.template

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]