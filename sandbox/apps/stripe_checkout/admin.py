import stripe
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.stripe_checkout import services
from apps.stripe_checkout.models import StripeCheckout, StripeCustomer


@admin.register(StripeCheckout)
class StripeCheckoutAdmin(admin.ModelAdmin):
    list_display = ("order_number", "email", "amount", "currency", "status", "created")
    list_filter = ("status",)
    search_fields = ("order_number", "email", "session_id", "payment_intent")
    readonly_fields = [field.name for field in StripeCheckout._meta.fields]
    actions = ["place_order_again"]

    @admin.action(description=_("Place the order again (paid, order not placed)"))
    def place_order_again(self, request, queryset):
        """Once the cause is fixed (see the error), asks Stripe again and
        places the order of the paid checkouts still without one"""
        for checkout in queryset.filter(status=StripeCheckout.ATTENTION):
            try:
                checkout = services.sync(checkout)
            except stripe.StripeError as error:
                self.message_user(
                    request,
                    "%s: Stripe %s" % (checkout.order_number, error),
                    messages.ERROR,
                )
                continue
            if checkout.status == StripeCheckout.PAID:
                self.message_user(
                    request, _("Order %s placed") % checkout.order_number
                )
            else:
                self.message_user(
                    request,
                    "%s: %s" % (checkout.order_number, checkout.error),
                    messages.ERROR,
                )


@admin.register(StripeCustomer)
class StripeCustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "user", "livemode", "created")
    list_filter = ("livemode",)
    search_fields = ("customer_id", "user__email")
    readonly_fields = [field.name for field in StripeCustomer._meta.fields]
