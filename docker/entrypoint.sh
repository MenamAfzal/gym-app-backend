#!/bin/sh

# Ensure SQLite directory exists for volume mount
mkdir -p /app/db

if [ "$DATABASE" = "postgres" ]
then
    echo "Waiting for postgres..."

    while ! nc -z $SQL_HOST $SQL_PORT; do
      sleep 0.1
    done

    echo "PostgreSQL started"
fi

# Run migrations
python manage.py migrate --fake-initial

echo "Collecting static files..."
python manage.py collectstatic --no-input


exec "$@"
