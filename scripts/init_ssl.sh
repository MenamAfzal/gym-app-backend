#!/usr/bin/env bash
set -e

DOMAIN="api.fitverx.com"
EMAIL="${1:-admin@fitverx.com}"
COMPOSE_FILE="docker-compose.prod.yml"

echo "=== 1. Ensuring Nginx is running on port 80 for ACME HTTP-01 challenge ==="
docker compose -f "$COMPOSE_FILE" up -d nginx

echo "=== 2. Requesting Let's Encrypt SSL certificate for $DOMAIN ==="
docker compose -f "$COMPOSE_FILE" run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    --email $EMAIL \
    -d $DOMAIN \
    --rsa-key-size 4096 \
    --agree-tos \
    --non-interactive \
    --force-renewal" certbot

echo "=== 3. Activating HTTPS (port 443) Nginx configuration ==="
cp docker/nginx/prod.ssl.conf docker/nginx/prod.conf

echo "=== 4. Reloading Nginx with SSL certificate & starting auto-renewal container ==="
docker compose -f "$COMPOSE_FILE" exec nginx nginx -s reload
docker compose -f "$COMPOSE_FILE" up -d certbot

echo "✅ HTTPS SSL certificate successfully installed and active at https://$DOMAIN"
