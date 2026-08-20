# frontend/Dockerfile
# Stage 1: build the static Vite bundle
FROM node:20-bookworm-slim AS build
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
RUN echo 'server { \
    listen 80; \
    location / { \
        root /usr/share/nginx/html; \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]