#!/bin/sh
# Runs migrations (and optionally loads the sandbox demo data the first time)
# before handing over to the container command (gunicorn by default).
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python manage.py migrate --noinput
fi

if [ ! -w public/media ]; then
    echo "ERROR: public/media is not writable by uid $(id -u)." >&2
    echo "On the host, run: sudo chown -R $(id -u):$(id -g) <MEDIA_DIR>" >&2
    exit 1
fi

if [ ! -f public/media/image_not_found.jpg ]; then
    python -c "import oscar, os, shutil; shutil.copy(os.path.join(os.path.dirname(oscar.__file__), 'static/oscar/img/image_not_found.jpg'), 'public/media/')"
fi

# Demo catalogue, only loaded into an empty database.
if [ "${LOAD_SANDBOX_DATA:-false}" = "true" ] && \
   [ "$(python manage.py shell -c 'from oscar.core.loading import get_model; print(get_model("catalogue", "Product").objects.exists())')" = "False" ]; then
    echo "Loading sandbox demo data..."
    python manage.py loaddata fixtures/auth.json
    python manage.py loaddata fixtures/child_products.json
    python manage.py oscar_import_catalogue fixtures/*.csv
    python manage.py oscar_import_catalogue_images fixtures/images.tar.gz
    python manage.py oscar_populate_countries --initial-only
    python manage.py loaddata fixtures/pages.json fixtures/ranges.json fixtures/offers.json
    python manage.py loaddata fixtures/orders.json
    python manage.py clear_index --noinput
    python manage.py update_index catalogue
fi

exec "$@"
