# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Django Oscar: a domain-driven e-commerce framework for Django, distributed as the `django-oscar` package (source in `src/oscar/`). Supported matrix (CI): Python 3.10–3.14, Django 4.2 / 5.2 / 6.0.

### Khafa project

This fork (Oscar 4.2.1) runs the khafa shop: the site is `sandbox/` (settings via env, Postgres, prod behind host nginx, see `docker-compose.prod.yml`). It is also the **backend of the Flutter mobile app** (`/workspace`), which is migrating here from WooCommerce:
- REST API via **django-oscar-api** 3.3 (DRF), mounted at `/api/` in `sandbox/urls.py`, **outside** `i18n_patterns`
- Mobile overrides in `sandbox/apps/api/` (`OSCARAPI_OVERRIDE_MODULES = ['apps.api']`): product list (paginated: `?page=`, `?page_size=`) and detail with inline price and availability, `?category=<id>` filter (descendants included), `?q=<words>` search through **Haystack** (the website's `ProductIndex`, every word required, start of a title word enough, most relevant first, intersected with the browsable queryset: `search_products` in `views/product.py`; tests use a temporary Whoosh index, see `MobileProductSearchTest`), categories with `id`/`parent_id`/`has_children` and `?parent=<id>`
- Mobile auth: DRF token (`Authorization: Token <t>`) from `/api/auth/token/` (email as `username`, via `EmailBackend`; `ThrottledObtainAuthToken` in `sandbox/apps/api/views/auth.py`, scope `login` = `API_LOGIN_RATE`, 10/minute); `POST /api/auth/logout/` deletes the token sent (`Logout`, 204, 401 without token); tokens expire after `API_TOKEN_TTL_DAYS` days without use (`apps/api/authentication.py` `ExpiringTokenAuthentication`, in `DEFAULT_AUTHENTICATION_CLASSES` and in the views that set their own classes: cart, checkout, payment methods, logout; a used token is moved forward at most once a day; an expired one is deleted and answers 401 "Token has expired.", on public endpoints too (the deletion survives `ATOMIC_REQUESTS` thanks to `exception_handler` in the same module, `REST_FRAMEWORK["EXCEPTION_HANDLER"]`); signing in again gives a new token; `0`, the default, turns the expiry off); registration via `/api/register/` (`OSCARAPI_ENABLE_REGISTRATION`; `RegistrationView` overridden in `sandbox/apps/api/views/login.py` through `OSCARAPI_OVERRIDE_MODULES`, scope `register` = `API_REGISTER_RATE`, 5/hour); `GET /api/login/` returns the signed-in user for the app profile (`OSCARAPI_EXPOSE_USER_DETAILS`, `OSCARAPI_USER_FIELDS`, both overridable by env); order history via `/api/orders/` (+ `id`, `OSCARAPI_ORDER_FIELDS`) and `/api/orders/<id>/lines/` (+ `title`, `OSCARAPI_ORDERLINE_FIELDS`), both lists set to the oscarapi 3.3 defaults plus these fields; product reviews (not in oscarapi) in `sandbox/apps/api/views/review.py`, routed by `sandbox/apps/api/urls.py` (included before `oscarapi.urls`): `products/<id>/reviews/` (GET approved / POST as the user) and `reviews/mine/`; products get `reviews_count` (annotated in the product views); account settings in `sandbox/apps/api/views/account.py`: `account/` (GET / PATCH first and last name, email read-only) and `account/password/` (POST `old_password`, `new_password1`, `new_password2` through Django's `PasswordChangeForm`, 204, the token stays valid) and `account/password-reset/` (POST `email`, Oscar's `PasswordResetForm` emails the website reset link, always 204, `ScopedRateThrottle` scope `password_reset` = `API_PASSWORD_RESET_RATE`, client IP behind `API_NUM_PROXIES` proxies; links use `OSCAR_URL_SCHEMA`, https by default; real emails need `EMAIL_HOST`, otherwise they are printed on the console); app basket in `sandbox/apps/api/views/cart.py` (`cart/`, `cart/lines/`, `cart/vouchers/`, answers the whole priced basket, checks the stock on add and quantity changes, guest basket from the session merged into the user basket when the token comes with the session cookie; it never saves the session mid-request, see `get_cart`)
- Settings defaults are safe: `DEBUG` is off unless the env sets `DEBUG=True` (the dev `docker-compose.yml` does), and without `SECRET_KEY` the server refuses to start outside debug (`ImproperlyConfigured`); the prod `.env.prod` must define `SECRET_KEY`
- Payment: **Stripe Checkout** in `sandbox/apps/stripe_checkout/` (routed in `apps/api/urls.py` before oscarapi, whose `checkout/` places unpaid orders): `checkout/` GET totals / POST freezes the basket (from `get_cart`) and opens a Stripe session (`StripeCheckout` row: basket, user, email, address JSON with the country code, pre-generated order number, amount); the order is placed **only once paid**, by the webhook `checkout/stripe/webhook/` (`stripe.Webhook.construct_event`, `STRIPE_WEBHOOK_SECRET`) or by `checkout/<session_id>/` polling Stripe, once (`select_for_update` on the row) via `OrderPlacementMixin` without request (payment source "Stripe", event "Paid", confirmation email on commit); `checkout.session.expired` / `checkout/<session_id>/cancel/` thaw the basket (a guest gets it back in their session). Stripe lines = basket lines after discounts + delivery, one "Order N" line if they do not add up. Safety: only the user (or, for a guest, the Django session stored in `StripeCheckout.session_key`) may follow or cancel a checkout (`services.can_access`, 404 otherwise); `create_checkout` locks the basket row (`select_for_update`) and refuses a basket no longer open, so a double tap cannot open two payable sessions; a paid session whose order cannot be placed becomes status `attention` (basket kept frozen, `error` stored, `ADMINS` emailed once, webhook answers 500 so Stripe retries, the status poll retries, admin action "Place the order again"; the app sees `paid`); a delayed payment refused (`checkout.session.async_payment_failed`, or the poll seeing the expanded PaymentIntent `requires_payment_method`/`canceled`) becomes `failed` and thaws the basket (the app treats it like a cancel); a paid amount or currency different from the order total still places the order but adds a "Warning" `OrderNote` and emails `ADMINS` (`amount_paid` keeps what Stripe charged). Env: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CHECKOUT_EXPIRY_MINUTES` (30), `ADMINS` (comma separated emails, also `SERVER_EMAIL`). Tests mock `stripe.checkout.Session.*` and sign webhooks with HMAC: `./manage.py test apps.stripe_checkout`. Spec in the Flutter repo: `specs/005-stripe-checkout/`
- Saved cards in `sandbox/apps/stripe_checkout/payment_methods.py` (token required): each user gets a Stripe Customer (`StripeCustomer`, one per test/live mode from the key prefix, created on first use by `services.customer_id`); `GET payment-methods/` lists the cards (`id`, `brand`, `last4`, `exp_month`, `exp_year`, `name`; `[]` without a customer), `POST` (`client` `app`/`web`) answers the `session_id` + `url` of a Checkout page in **setup** mode (503 without key), `DELETE payment-methods/<pm_id>/` (204, 404 for a card of another customer); back to `payment-methods/return/` (app) or `/app/?payment_method_session=<id>[&canceled=1]` (web). Signed-in checkouts pass `customer` (not `customer_email`) with `saved_payment_method_options`, so the Stripe page offers the saved cards and to save the new one. Stripe refuses `payment_method_types` on new sessions (payment methods come from the dashboard), and stripe-python objects are not dicts (`obj["key"]`, never `.get()`): test mocks build them with `construct_from`
- Delivery: shipping app forked in `sandbox/apps/shipping/` (`oscar_fork_app`, migrations copied): `Repository` offers only `FlatRate` (`methods.py`, `FixedPrice` of `KHAFA_SHIPPING_CHARGE`, 10.00 by default, tax included), for the website and the app
- API tests: `cd sandbox && ./manage.py test apps.api` (needs a writable `whoosh_index`, or `HAYSTACK_SIGNAL_PROCESSOR = 'haystack.signals.BaseSignalProcessor'` in a test settings module). If the API output changes, regenerate the Flutter fixtures in `/workspace/test/fixtures/oscar/`
- Deps: `djangorestframework`, `django-oscar-api` and `stripe` are in the `test` extra of `pyproject.toml` (installed by `make install`) and must be in `docker/prod/Dockerfile`
- Project-specific customisations go in `sandbox/` (`settings.py`, `urls.py`, `apps/`), not in `src/oscar/`
- Shares the Docker network `oscar` with the Flutter devcontainer (reachable there as `http://django-oscar:8000`)

## Commands

```bash
pip install -e .[test]          # install package + test deps
make install                    # also builds static assets (npm install && npm run build)

# Tests (pytest, settings module: tests.settings)
pytest                          # defaults to PostgreSQL (DATABASE_USER/PASSWORD/HOST/PORT env vars)
pytest --sqlite                 # use in-memory SQLite instead (no Postgres needed)
pytest --sqlite tests/integration/basket/test_models.py::TestClassName::test_name
pytest --lf                     # rerun failures
pytest --deprecation=strict     # turn deprecation/runtime warnings into errors

# Lint (what CI runs)
make lint                       # black --check + pylint on src/oscar and tests
make black                      # auto-format (migrations excluded)
npm run eslint                  # JS lint for src/oscar/static_src/oscar/js/oscar/

# Frontend assets (gulp: copies vendor libs and compiles SCSS from static_src → static)
npm run build
npm run watch

# Sandbox demo site (sandbox/, SQLite db, loads fixtures + catalogue CSVs)
make sandbox                    # install + build from scratch
sandbox/manage.py runserver

make test_migrations            # migration tests (sandbox/test_migrations.sh)
make docs                       # Sphinx docs in docs/
make extract_translations / compile_translations
```

## Architecture

**Forkable apps.** Each feature lives in `src/oscar/apps/<app>/` (catalogue, basket, checkout, order, offer, partner, shipping, payment, customer, voucher, wishlists, search, communication, analytics, address) plus `apps/dashboard/<sub-app>/`. Projects customise Oscar by "forking" an app (`oscar.core.customisation`, `oscar_fork_app` management command) and replacing the Oscar entry in `INSTALLED_APPS`. Everything in the codebase is written to support this, which drives these conventions:

- **Never import Oscar classes directly across apps.** Use `get_class("basket.forms", "AddToBasketForm")` / `get_classes(...)` from `oscar.core.loading`. The loader resolves the module against the app registry, so a forked app's module wins and falls back to the Oscar core module. Use `get_model("catalogue", "Product")` for models.
- **Models are split into `abstract_models.py` (all logic, `Abstract*` classes) and `models.py`** (concrete classes, each wrapped in `if not is_model_registered(app_label, "Name"):` so forks can override them). Put model changes in `abstract_models.py`; migrations live in each app's `migrations/`.
- **AppConfigs are `OscarConfig` subclasses** (`oscar.core.application`) that own their URLs: views are loaded via `get_class` in `ready()` and wired in `get_urls()`. `oscar.config.Shop` is the root config that mounts the sub-apps' URLs. Dashboard apps use `OscarDashboardConfig` with permission maps (`apps/dashboard/permissions.py`).
- Settings defaults are in `src/oscar/defaults.py` (`OSCAR_*`); `OSCAR_DYNAMIC_CLASS_LOADER` makes the class loader itself replaceable.

**Key domain pieces:** pricing/availability strategies in `apps/partner/strategy.py` (a strategy is attached to each request/basket and selects stockrecords); offers/conditions/benefits in `apps/offer/` with the applicator applying them to baskets; checkout session and order placement in `apps/checkout/` (`mixins.OrderPlacementMixin`) and `apps/order/utils.OrderCreator`; order status pipelines and event processing in `apps/order/processing.py`. Search uses django-haystack (Whoosh in tests, Solr/Elasticsearch in production setups).

**Templates and static:** templates in `src/oscar/templates/oscar/`; SCSS/JS source in `src/oscar/static_src/` (built output under `src/oscar/static/` is generated, not edited).

**Tests** (`tests/`): split into `unit/`, `integration/` and `functional/` (WebTest-based, e.g. `oscar.test.testcases.WebTestCase`). `tests/_site/` contains a test project including forked apps (`tests/_site/apps/`) used to exercise the customisation machinery. Reusable factories and helpers ship in `src/oscar/test/` (`factories`, `basket`, `testcases`).

## Style

- black formatting; pylint config in `pylintrc` (code often uses targeted `# pylint: disable=...` comments). flake8/isort settings in `setup.cfg`.
- User-facing strings are translated with `gettext_lazy as _`.

## Flutter web app (/app/)

The app's web build is served by the host nginx from `/srv/oscar/app/` (copied with `rsync -a --delete build/web/ <server>:/srv/oscar/app/`), on the same origin as the API. The host site file (`/etc/nginx/sites-enabled/khafadropshipping`, not in this repo; `docker/prod/nginx-host.conf.example` is the older template) has, in the HTTPS `server` block before `location /`:

```nginx
# top of the file (http context): main.dart.js is not hashed, so the app
# files are revalidated on every visit (ETag, 304 when unchanged)
map $uri $khafa_app_cache {
    default            "no-cache";
    ~^/app/icons/      "public, max-age=604800";
    =/app/favicon.png  "public, max-age=604800";
}

location = /app { return 301 /app/; }
location /app/ {
    alias /srv/oscar/app/;
    try_files $uri $uri/ /app/index.html;
    gzip on; gzip_vary on; gzip_comp_level 6; gzip_min_length 1024;
    gzip_types application/javascript text/javascript application/json application/wasm
               text/css image/svg+xml text/plain application/manifest+json;
    add_header Cache-Control $khafa_app_cache always;
    # no nested location: an add_header there would drop all of these
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "same-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'wasm-unsafe-eval' https://www.gstatic.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://www.gstatic.com https://fonts.gstatic.com; worker-src 'self' blob:; manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'" always;
}

# API JSON compressed (20 products: 14.7 KB -> 3.6 KB); the HTML of the
# site is not (CSRF token in the page: BREACH)
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    gzip on; gzip_vary on; gzip_proxied any; gzip_comp_level 5; gzip_min_length 1024;
    gzip_types application/json;
}
```

- The CSP allows `www.gstatic.com` because the build loads CanvasKit from Google's CDN (default, no `--no-web-resources-cdn`) and `fonts.gstatic.com` for fallback fonts; Stripe needs nothing (full-page redirect). Product images come from this origin (`/media/`)
- HSTS on the Django answers (site, API) comes from `SECURE_HSTS_SECONDS=31536000` in `.env.prod`; `/media/` is served by nginx without it (the browser applies HSTS to the whole host anyway)
- Check after a change: `curl -sI -H "Accept-Encoding: gzip" https://khafadropshipping.ddns.net/app/main.dart.js` (gzip, no-cache), `curl -sI https://khafadropshipping.ddns.net/app/` (6 security headers), `/api/products/` (gzip + HSTS), `/` (HSTS, no gzip)

`REST_FRAMEWORK` is token-only (`TokenAuthentication`): with session authentication, a customer signed in on the website would see the app's API calls refused for CSRF (see `WebAppOriginTest`). Guest baskets keep using the session.
