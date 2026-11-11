"""
Stripe Checkout payment of an Oscar basket.

The basket is frozen while the customer pays on the Stripe page; the order is
placed only once Stripe reports the session as paid, by the webhook or by the
app polling the status (whichever comes first, see ``place_order``). An
expired or canceled session thaws the basket.
"""

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import stripe
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.mail import mail_admins
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext as _
from oscar.core.loading import get_class, get_model
from oscarapi.basket import operations

from apps.stripe_checkout.models import StripeCheckout, StripeCustomer

logger = logging.getLogger("apps.stripe_checkout")

OrderPlacementMixin = get_class("checkout.mixins", "OrderPlacementMixin")
OrderTotalCalculator = get_class("checkout.calculators", "OrderTotalCalculator")
OrderNumberGenerator = get_class("order.utils", "OrderNumberGenerator")
Repository = get_class("shipping.repository", "Repository")
Selector = get_class("partner.strategy", "Selector")
Applicator = get_class("offer.applicator", "Applicator")
Basket = get_model("basket", "Basket")
OrderNote = get_model("order", "OrderNote")
ShippingAddress = get_model("order", "ShippingAddress")
Source = get_model("payment", "Source")
SourceType = get_model("payment", "SourceType")

# https://docs.stripe.com/currencies#zero-decimal
ZERO_DECIMAL_CURRENCIES = {
    "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga",
    "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
}  # fmt: skip
PAID_STATUSES = ("paid", "no_payment_required")
# PaymentIntent statuses of a delayed payment that will not go through
FAILED_INTENT_STATUSES = ("requires_payment_method", "canceled")


class CheckoutError(Exception):
    """A checkout that cannot start, with a message for the customer"""


def alert(subject, message):
    """Tells the shop admins (ADMINS) by email, besides the error log"""
    logger.error("%s: %s", subject, message)
    mail_admins("Stripe checkout: %s" % subject, message, fail_silently=True)


def _field(session, name):
    """A field of a Stripe session (dict from a webhook, StripeObject from the
    API, without .get()), None when absent"""
    try:
        return session[name]
    except KeyError:
        return None


def payment_intent_id(session):
    """The PaymentIntent id, also when the session was retrieved with the
    PaymentIntent expanded"""
    intent = _field(session, "payment_intent")
    if intent is None or isinstance(intent, str):
        return intent or ""
    return intent["id"]


def payment_failed(session):
    """A delayed payment (e.g. SEPA debit) refused after the Stripe page; it
    is known only when the PaymentIntent is expanded"""
    intent = _field(session, "payment_intent")
    return (
        session["status"] == "complete"
        and session["payment_status"] == "unpaid"
        and intent is not None
        and not isinstance(intent, str)
        and _field(intent, "status") in FAILED_INTENT_STATUSES
    )


def can_access(checkout, request):
    """Only the user of a checkout, or the session of a guest, may follow or
    cancel it (its session id appears in URLs)"""
    if checkout.user_id is not None:
        return request.user.is_authenticated and request.user.pk == checkout.user_id
    if not checkout.session_key:
        # started before the session was recorded
        return True
    return request.session.session_key == checkout.session_key


def is_configured():
    return bool(settings.STRIPE_SECRET_KEY)


def is_live():
    return settings.STRIPE_SECRET_KEY.startswith(("sk_live_", "rk_live_"))


def customer_id(user, create=True):
    """Stripe Customer of a signed-in user, created on first use (None when
    ``create`` is false and there is none yet)"""
    livemode = is_live()
    existing = StripeCustomer.objects.filter(user=user, livemode=livemode).first()
    if existing is not None or not create:
        return existing and existing.customer_id
    params = {"email": user.email, "metadata": {"user_id": str(user.pk)}}
    if user.get_full_name():
        params["name"] = user.get_full_name()
    customer = stripe.Customer.create(api_key=settings.STRIPE_SECRET_KEY, **params)
    # a concurrent request may have created one meanwhile: keep the first
    stored, __ = StripeCustomer.objects.get_or_create(
        user=user, livemode=livemode, defaults={"customer_id": customer["id"]}
    )
    return stored.customer_id


def to_minor_units(amount, currency):
    """Stripe amounts are integers in the smallest currency unit"""
    if currency.lower() in ZERO_DECIMAL_CURRENCIES:
        return int(Decimal(amount).quantize(Decimal("1"), ROUND_HALF_UP))
    return int((Decimal(amount) * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def price_basket(basket, user, request=None):
    """Shipping method, shipping charge and order total of a basket whose
    strategy and offers are applied"""
    method = Repository().get_default_shipping_method(
        basket=basket, user=user, request=request
    )
    charge = method.calculate(basket)
    return method, charge, OrderTotalCalculator().calculate(basket, charge)


def amount(price):
    """Prices including tax, excluding it when the tax is unknown"""
    return price.incl_tax if price.is_tax_known else price.excl_tax


def line_amount(basket, line):
    return (
        line.line_price_incl_tax_incl_discounts
        if basket.is_tax_known
        else line.line_price_excl_tax_incl_discounts
    )


def check_stock(basket):
    """Raises CheckoutError when the basket is empty or a line can no longer
    be bought"""
    if basket.is_empty:
        raise CheckoutError(_("Your basket is empty"))
    for line in basket.all_lines():
        info = basket.strategy.fetch_for_line(line)
        allowed, message = info.availability.is_purchase_permitted(line.quantity)
        if not allowed:
            raise CheckoutError("%s: %s" % (line.product.get_title(), message))


def line_items(basket, charge, total, order_number):
    """One Stripe line per basket line (its price after discounts) plus the
    delivery; a single line for the order when they do not add up to the
    total (e.g. a discount on the delivery)"""
    currency = basket.currency.lower()

    def item(name, value):
        return {
            "quantity": 1,
            "price_data": {
                "currency": currency,
                "unit_amount": to_minor_units(value, currency),
                "product_data": {"name": name[:250]},
            },
        }

    items = [
        item(
            "%s × %d" % (line.product.get_title(), line.quantity),
            line_amount(basket, line),
        )
        for line in basket.all_lines()
    ]
    if amount(charge):
        items.append(item(_("Delivery"), amount(charge)))
    stripe_total = sum(i["price_data"]["unit_amount"] for i in items)
    if stripe_total != to_minor_units(amount(total), currency):
        items = [item(_("Order %s") % order_number, amount(total))]
    return items


def return_urls(request, client):
    """success_url and cancel_url of the Stripe session: the app on the web
    (same origin), a page asking to go back to the app otherwise"""
    if client == "web":
        base = request.build_absolute_uri("/app/") + "?checkout_session="
    else:
        base = (
            request.build_absolute_uri(reverse("api-checkout-return")) + "?session_id="
        )
    # placeholder replaced by Stripe, must not be URL-encoded
    success = base + "{CHECKOUT_SESSION_ID}"
    return success, success + "&canceled=1"


def create_checkout(request, basket, email, shipping_address, client):
    """Freezes the basket and opens a Stripe Checkout Session for its total;
    ``shipping_address`` holds ShippingAddress fields (validated)"""
    user = request.user if request.user.is_authenticated else None
    check_stock(basket)
    method, charge, total = price_basket(basket, request.user, request)
    order_number = str(OrderNumberGenerator().order_number(basket))
    success_url, cancel_url = return_urls(request, client)
    metadata = {"basket_id": str(basket.pk), "order_number": order_number}
    if user is None:
        payer = {"customer_email": email}
        if not request.session.session_key:
            request.session.save()
    else:
        # the Stripe page offers the saved cards and to save the new one
        payer = {
            "customer": customer_id(user),
            "saved_payment_method_options": {
                "payment_method_save": "enabled",
                "allow_redisplay_filters": ["always", "limited", "unspecified"],
            },
        }
    with transaction.atomic():
        # one payment per basket: a second request (e.g. a double tap) waits
        # here, then finds the basket frozen by the first one
        locked = Basket.objects.select_for_update().get(pk=basket.pk)
        if locked.status != Basket.OPEN:
            raise CheckoutError(_("A payment is already in progress for this basket"))
        return _open_session(
            request, basket, user, email, shipping_address, method, total,
            charge, order_number, metadata, payer, success_url, cancel_url,
        )  # fmt: skip


def _open_session(
    request, basket, user, email, shipping_address, method, total, charge,
    order_number, metadata, payer, success_url, cancel_url,
):  # fmt: skip
    """Creates the Stripe session and freezes the basket (in the transaction
    of create_checkout, which holds the basket lock)"""
    session = stripe.checkout.Session.create(
        api_key=settings.STRIPE_SECRET_KEY,
        mode="payment",
        line_items=line_items(basket, charge, total, order_number),
        **payer,
        client_reference_id=str(basket.pk),
        metadata=metadata,
        payment_intent_data={
            "metadata": metadata,
            "description": _("Order %s") % order_number,
        },
        success_url=success_url,
        cancel_url=cancel_url,
        expires_at=int(time.time()) + settings.STRIPE_CHECKOUT_EXPIRY_MINUTES * 60,
        locale="auto",
    )
    country = shipping_address.pop("country")
    shipping_address["country"] = country.pk
    basket.freeze()
    checkout = StripeCheckout.objects.create(
        session_id=session["id"],
        basket=basket,
        user=user,
        email=email,
        session_key="" if user else (request.session.session_key or ""),
        shipping_address=shipping_address,
        shipping_code=method.code,
        order_number=order_number,
        amount=amount(total),
        currency=basket.currency,
    )
    return checkout, session["url"]


def retrieve_session(checkout):
    # the PaymentIntent tells whether a delayed payment failed
    return stripe.checkout.Session.retrieve(
        checkout.session_id,
        api_key=settings.STRIPE_SECRET_KEY,
        expand=["payment_intent"],
    )


def apply_session(checkout, session):
    """Brings the checkout up to date with its Stripe session"""
    if session["status"] == "complete" and session["payment_status"] in PAID_STATUSES:
        try:
            place_order(checkout, session)
        except Exception as error:  # pylint: disable=broad-except
            # the customer paid: never lose it silently
            needs_attention(checkout, session, error)
    elif session["status"] == "expired":
        close(checkout, StripeCheckout.EXPIRED)
    elif payment_failed(session):
        close(checkout, StripeCheckout.FAILED)
    checkout.refresh_from_db()
    return checkout


def sync(checkout):
    """Asks Stripe about a checkout still waiting for its payment, or paid
    without its order (tries to place it again)"""
    if (
        checkout.status in (StripeCheckout.OPEN, StripeCheckout.ATTENTION)
        and is_configured()
    ):
        apply_session(checkout, retrieve_session(checkout))
    return checkout


def needs_attention(checkout, session, error):
    """Paid, but the order could not be placed: the checkout keeps the frozen
    basket and the payment, and the admins are told once"""
    logger.exception(
        "Stripe session %s paid, order %s not placed",
        checkout.session_id, checkout.order_number,
    )  # fmt: skip
    with transaction.atomic():
        checkout = StripeCheckout.objects.select_for_update().get(pk=checkout.pk)
        if checkout.order_id is not None:
            return
        first_time = checkout.status != StripeCheckout.ATTENTION
        checkout.status = StripeCheckout.ATTENTION
        checkout.payment_intent = payment_intent_id(session)
        checkout.error = "%s: %s" % (type(error).__name__, error)
        checkout.save(update_fields=["status", "payment_intent", "error", "updated"])
    if first_time:
        alert(
            "order %s paid but not placed" % checkout.order_number,
            "Stripe session %s (payment %s, %s %s) was paid but the order "
            "could not be placed:\n\n%s\n\nFix the cause, then use the admin "
            'action "Place the order again" on this Stripe checkout, or '
            "refund the payment in Stripe."
            % (
                checkout.session_id,
                checkout.payment_intent,
                checkout.amount,
                checkout.currency,
                checkout.error,
            ),
        )


def cancel(checkout):
    """Expires the Stripe session; a session paid meanwhile places the order"""
    if checkout.status != StripeCheckout.OPEN:
        return checkout
    try:
        session = stripe.checkout.Session.expire(
            checkout.session_id, api_key=settings.STRIPE_SECRET_KEY
        )
    except stripe.InvalidRequestError:
        # already complete or expired
        session = retrieve_session(checkout)
    if session["status"] == "expired":
        close(checkout, StripeCheckout.CANCELED)
    return apply_session(checkout, session)


def close(checkout, status):
    """Marks an unpaid checkout expired or canceled and thaws its basket"""
    with transaction.atomic():
        checkout = StripeCheckout.objects.select_for_update().get(pk=checkout.pk)
        if checkout.status != StripeCheckout.OPEN:
            return
        checkout.status = status
        checkout.save(update_fields=["status", "updated"])
        basket = checkout.basket
        if basket.status == Basket.FROZEN:
            basket.thaw()


def restore_basket(checkout, request):
    """Makes the thawed basket of an unpaid checkout the open basket of its
    owner again (the session of a guest; signed-in users get their open
    baskets merged by oscarapi)"""
    basket = checkout.basket
    if (
        checkout.status
        in (StripeCheckout.OPEN, StripeCheckout.PAID, StripeCheckout.ATTENTION)
        or basket.status != Basket.OPEN
        or checkout.user_id is not None
        or request.user.is_authenticated
    ):
        return
    current = operations.get_anonymous_basket(request)
    if current is not None and current.pk != basket.pk:
        basket.merge(current, add_quantities=True)
    request.session[settings.OSCAR_BASKET_COOKIE_OPEN] = basket.pk


class _OrderPlacer(OrderPlacementMixin):
    """OrderPlacementMixin without a request nor a checkout session"""

    request = None

    def __init__(self, user):
        self.user = user

    def get_message_context(self, order):
        ctx = {"user": self.user, "order": order, "lines": order.lines.all()}
        if self.user.is_authenticated:
            ctx["status_path"] = reverse(
                "customer:order", kwargs={"order_number": order.number}
            )
        else:
            ctx["status_path"] = reverse(
                "customer:anon-order",
                kwargs={
                    "order_number": order.number,
                    "hash": order.verification_hash(),
                },
            )
        return ctx


def place_order(checkout, session):
    """Places the order of a paid checkout, once"""
    with transaction.atomic():
        checkout = StripeCheckout.objects.select_for_update().get(pk=checkout.pk)
        if checkout.order_id is not None:
            return checkout.order
        user = checkout.user or AnonymousUser()
        basket = checkout.basket
        basket.strategy = Selector().strategy(user=checkout.user)
        Applicator().apply(basket, user)
        method, charge, total = price_basket(basket, user)
        zero_decimal = checkout.currency.lower() in ZERO_DECIMAL_CURRENCIES
        # quantized so the review note reads "30.00 GBP", not "30 GBP"
        paid = (
            Decimal(session["amount_total"]) / (1 if zero_decimal else 100)
        ).quantize(Decimal("1") if zero_decimal else Decimal("0.01"))
        paid_currency = (_field(session, "currency") or checkout.currency).upper()
        mismatch = None
        if paid != amount(total) or paid_currency != checkout.currency.upper():
            # the order is placed (the customer paid), marked for review
            mismatch = (
                "Stripe charged %s %s, the order totals %s %s "
                "(prices, offers or vouchers changed during the payment). "
                "Check the order, then refund the difference or ask for it."
                % (paid, paid_currency, amount(total), checkout.currency)
            )
        payment_intent = payment_intent_id(session)
        address = dict(checkout.shipping_address)
        address["country_id"] = address.pop("country")
        placer = _OrderPlacer(user)
        source_type, __ = SourceType.objects.get_or_create(name="Stripe")
        placer.add_payment_source(
            Source(
                source_type=source_type,
                currency=checkout.currency,
                amount_allocated=paid,
                amount_debited=paid,
                reference=payment_intent,
            )
        )
        placer.add_payment_event("Paid", paid, reference=payment_intent)
        order = placer.place_order(
            order_number=checkout.order_number,
            user=user,
            basket=basket,
            shipping_address=ShippingAddress(**address),
            shipping_method=method,
            shipping_charge=charge,
            order_total=total,
            guest_email="" if checkout.user_id else checkout.email,
        )
        basket.submit()
        checkout.status = StripeCheckout.PAID
        checkout.order = order
        checkout.payment_intent = payment_intent
        checkout.amount_paid = paid
        checkout.error = ""
        checkout.save()
        if mismatch:
            # shown on the order page of the dashboard
            OrderNote.objects.create(order=order, note_type="Warning", message=mismatch)
            number = order.number
            transaction.on_commit(
                lambda: alert("order %s to review" % number, mismatch)
            )
        transaction.on_commit(lambda: _send_confirmation(placer, order))
    return order


def _send_confirmation(placer, order):
    try:
        placer.send_order_placed_email(order)
    except Exception:  # pylint: disable=broad-except
        # the order is placed and paid: a failed email must not undo it
        logger.exception("Order %s: confirmation email failed", order.number)


def summary(basket, user, request=None):
    """What the app shows before paying"""
    method, charge, total = price_basket(basket, user, request)
    return {
        "currency": basket.currency,
        "num_items": basket.num_items,
        "basket_total": money(
            basket.total_incl_tax if basket.is_tax_known else basket.total_excl_tax
        ),
        "shipping_method": {
            "code": method.code,
            "name": str(method.name),
            "charge": money(amount(charge)),
        },
        "order_total": money(amount(total)),
        "guest_email_required": not user.is_authenticated,
        "payment_available": is_configured(),
    }


# what the app sees: paid without its order yet is still paid for the
# customer (the shop places the order), a failed delayed payment ends the
# checkout like a cancel
PUBLIC_STATUSES = {StripeCheckout.ATTENTION: StripeCheckout.PAID}


def status_payload(checkout):
    order = checkout.order
    return {
        "session_id": checkout.session_id,
        "status": PUBLIC_STATUSES.get(checkout.status, checkout.status),
        "order_number": checkout.order_number,
        "amount": money(checkout.amount),
        "currency": checkout.currency,
        "order": (
            None
            if order is None
            else {
                "id": order.pk,
                "number": order.number,
                "total_incl_tax": money(order.total_incl_tax),
                "status": order.status,
            }
        ),
    }


def money(value):
    return None if value is None else "%.2f" % value
