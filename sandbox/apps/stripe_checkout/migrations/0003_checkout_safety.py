from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stripe_checkout", "0002_stripecustomer"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stripecheckout",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Waiting for payment"),
                    ("paid", "Paid"),
                    ("expired", "Expired"),
                    ("canceled", "Canceled"),
                    ("failed", "Payment failed"),
                    ("attention", "Paid, order not placed"),
                ],
                default="open",
                max_length=16,
                verbose_name="Status",
            ),
        ),
        migrations.AddField(
            model_name="stripecheckout",
            name="session_key",
            field=models.CharField(blank=True, max_length=40, verbose_name="Session"),
        ),
        migrations.AddField(
            model_name="stripecheckout",
            name="amount_paid",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                verbose_name="Amount paid",
            ),
        ),
        migrations.AddField(
            model_name="stripecheckout",
            name="error",
            field=models.TextField(blank=True, verbose_name="Error"),
        ),
    ]
