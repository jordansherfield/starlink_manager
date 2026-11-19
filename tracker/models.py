from decimal import Decimal

from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


# Audit logs never store login passwords; this is written in their place.
REDACTED = '(hidden)'
SECRET_FIELDS = {'Credential': {'password', 'email_pass'}}


class ServicePlan(models.Model):
    """A Starlink subscription plan and what Starlink charges for it per month."""
    name = models.CharField(max_length=150, unique=True,
                            help_text="As Starlink names it, e.g. 'Residential Lite'")
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True,
                                help_text="Standard monthly price Starlink charges for this plan")
    currency = models.CharField(max_length=3, default='USD')
    notes = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True, help_text="Inactive plans are hidden from the plan picker")

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class StarlinkAccount(models.Model):
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, blank=True, null=True, related_name='accounts')
    account_number = models.CharField(max_length=100, unique=True)
    last_payment_date = models.DateField(blank=True, null=True)
    account_due_by = models.DateField(blank=True, null=True)
    cost_to_us = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    client_invoice = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    balance_due = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True,
                                      help_text="Amount outstanding on the Starlink account at the last sync")
    starlink_synced_at = models.DateTimeField(blank=True, null=True,
                                              help_text="When this account was last updated from Starlink")

    is_archived = models.BooleanField(default=False)
    archived_reason = models.TextField(blank=True, null=True)
    archived_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Acc {self.account_number}"

    @property
    def plan_cost(self):
        """What the account's active services should cost per month from their plans/overrides.
        None unless every active service on the account has a known price (a partial total would
        look like a billing difference)."""
        prices = [d.effective_price for d in self.starlinks.filter(is_archived=False).select_related('plan')]
        if not prices or any(p is None for p in prices):
            return None
        return sum(prices, Decimal('0.00'))

    class Meta:
        ordering = ['account_number']

class Client(models.Model):
    COMPANY_CHOICES = [
        ('Comnet', 'Comnet'),
        ('Farbell', 'Farbell'),
        ('Afrinet', 'Afrinet'),
    ]

    name = models.CharField(max_length=255)
    company = models.CharField(max_length=50, choices=COMPANY_CHOICES, default='Comnet')
    address = models.TextField(blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Denormalized counters kept in sync by signals (see signals.py) so pages
    # listing many clients can read counts without per-client aggregate queries.
    accounts_count = models.PositiveIntegerField(default=0, editable=False)
    devices_count = models.PositiveIntegerField(default=0, editable=False)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

    def recalculate_counts(self):
        account_ids = set(self.accounts.filter(is_archived=False).values_list('id', flat=True))
        account_ids.update(
            self.starlinks.filter(is_archived=False, account__isnull=False, account__is_archived=False)
            .values_list('account_id', flat=True)
        )
        accounts_count = len(account_ids)
        devices_count = self.starlinks.filter(is_archived=False).count()

        if accounts_count != self.accounts_count or devices_count != self.devices_count:
            # .update() bypasses save()/signals so this never recurses or
            # generates spurious audit-log "changed" entries.
            Client.objects.filter(pk=self.pk).update(
                accounts_count=accounts_count, devices_count=devices_count
            )
            self.accounts_count = accounts_count
            self.devices_count = devices_count

class Credential(models.Model):
    account = models.ForeignKey(StarlinkAccount, on_delete=models.CASCADE, related_name='credentials')
    label = models.CharField(max_length=100, default='Starlink Account')
    username = models.CharField(max_length=255)
    password = models.CharField(max_length=255, blank=True, default='',
                                help_text="Optional - keep passwords in Bitwarden and link the item instead")
    bitwarden_item_id = models.CharField(max_length=100, blank=True, null=True,
                                         help_text="ID of the Bitwarden/Vaultwarden item holding this login")
    email_pass = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    is_archived = models.BooleanField(default=False)
    archived_reason = models.TextField(blank=True, null=True)
    archived_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.label} ({self.username})"

    @property
    def bitwarden_url(self):
        """Link to the item in the web vault, if BITWARDEN_ITEM_URL is configured."""
        template = getattr(settings, 'BITWARDEN_ITEM_URL', '')
        if self.bitwarden_item_id and template:
            return template.format(id=self.bitwarden_item_id)
        return None

class StarlinkDevice(models.Model):
    STATUS_CHOICES = [
        ('Running', 'Running'),
        ('To install', 'To install'),
        ('Deactivated', 'Deactivated'),
        ('Hardware fault', 'Hardware fault'),
        ('Account issue', 'Account issue'),
    ]

    MODEL_CHOICES = [
        ('gen2', 'Gen 2'),
        ('Mini', 'Mini'),
        ('Gen3', 'Gen 3'),
    ]

    client = models.ForeignKey(Client, on_delete=models.SET_NULL, blank=True, null=True, related_name='starlinks')
    account = models.ForeignKey(StarlinkAccount, on_delete=models.SET_NULL, blank=True, null=True, related_name='starlinks')
    kit_number = models.CharField(max_length=100, unique=True)
    location_name = models.CharField(max_length=255)
    model = models.CharField(max_length=50, choices=MODEL_CHOICES, default='Gen3')
    starlink_id = models.CharField(max_length=100, blank=True, null=True)
    serial_number = models.CharField(max_length=100, blank=True, null=True)
    wifi_name = models.CharField(max_length=100, blank=True, null=True)
    wifi_password = models.CharField(max_length=100, blank=True, null=True)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='To install')
    notes = models.TextField(blank=True, null=True)

    # Starlink service (subscription) details
    plan = models.ForeignKey(ServicePlan, on_delete=models.SET_NULL, blank=True, null=True, related_name='devices')
    price_override = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True,
                                         help_text="What Starlink actually charges for this service when it "
                                                   "differs from the plan price. Blank = use the plan price.")
    price_override_reason = models.CharField(max_length=255, blank=True, null=True)
    service_line_number = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    starlink_nickname = models.CharField(max_length=255, blank=True, null=True)
    service_address = models.CharField(max_length=255, blank=True, null=True)
    starlink_service_active = models.BooleanField(blank=True, null=True,
                                                  help_text="Whether the Starlink subscription was active at the last sync")

    # Lifecycle
    status_changed_at = models.DateTimeField(blank=True, null=True)
    installed_on = models.DateField(blank=True, null=True)
    installed_by = models.CharField(max_length=100, blank=True, null=True)

    is_archived = models.BooleanField(default=False)
    archived_reason = models.TextField(blank=True, null=True)
    archived_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Kit {self.kit_number} - {self.location_name}"

    def save(self, *args, **kwargs):
        """Stamp status changes, and the install date when a unit first goes Running."""
        update_fields = kwargs.get('update_fields')
        if update_fields is None or 'status' in update_fields:
            old_status = None
            if self.pk:
                old_status = type(self).objects.filter(pk=self.pk).values_list('status', flat=True).first()
            if old_status != self.status:
                self.status_changed_at = timezone.now()
                extra = ['status_changed_at']
                if self.status == 'Running' and old_status is not None and self.installed_on is None:
                    self.installed_on = timezone.localdate()
                    extra.append('installed_on')
                if update_fields is not None:
                    kwargs['update_fields'] = list(update_fields) + extra
        super().save(*args, **kwargs)

    @property
    def days_in_status(self):
        if not self.status_changed_at:
            return None
        return (timezone.now() - self.status_changed_at).days

    @property
    def effective_price(self):
        """The monthly price for this service: the override if set, otherwise the plan price."""
        if self.price_override is not None:
            return self.price_override
        return self.plan.price if self.plan else None

class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    action = models.CharField(max_length=50) # CREATE, UPDATE, DELETE
    model_name = models.CharField(max_length=100)
    object_id = models.IntegerField(null=True, blank=True)
    object_repr = models.CharField(max_length=255)
    kit_number = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    changes = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        user_str = self.user.username if self.user else "System"
        return f"{self.timestamp} - {user_str} - {self.action} - {self.model_name}: {self.object_repr}"

    class Meta:
        ordering = ['-timestamp']

