from django.db import models
from django.contrib.auth.models import User

class StarlinkAccount(models.Model):
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, blank=True, null=True, related_name='accounts')
    account_number = models.CharField(max_length=100, unique=True)
    last_payment_date = models.DateField(blank=True, null=True)
    account_due_by = models.DateField(blank=True, null=True)
    cost_to_us = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    client_invoice = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"Acc {self.account_number}"

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

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class Credential(models.Model):
    account = models.ForeignKey(StarlinkAccount, on_delete=models.CASCADE, related_name='credentials')
    label = models.CharField(max_length=100, default='Starlink Account')
    username = models.CharField(max_length=255)
    password = models.CharField(max_length=255)
    email_pass = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.label} ({self.username})"

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

    def __str__(self):
        return f"Kit {self.kit_number} - {self.location_name}"

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

