from django.contrib import admin
from .models import StarlinkAccount, Client, Credential, StarlinkDevice, ServicePlan

class CredentialInline(admin.TabularInline):
    model = Credential
    extra = 1

class StarlinkDeviceInline(admin.TabularInline):
    model = StarlinkDevice
    extra = 1

@admin.register(StarlinkAccount)
class StarlinkAccountAdmin(admin.ModelAdmin):
    list_display = ('account_number', 'last_payment_date', 'account_due_by', 'cost_to_us', 'client_invoice',
                    'balance_due', 'starlink_synced_at')
    search_fields = ('account_number',)
    inlines = [CredentialInline]

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'email', 'phone', 'created_at')
    list_filter = ('company',)
    search_fields = ('name', 'email', 'phone', 'address')
    inlines = [StarlinkDeviceInline]

@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    list_display = ('account', 'label', 'username', 'password', 'email_pass')
    search_fields = ('account__account_number', 'label', 'username')

@admin.register(StarlinkDevice)
class StarlinkDeviceAdmin(admin.ModelAdmin):
    list_display = ('kit_number', 'client', 'account', 'location_name', 'model', 'status', 'plan', 'price_override')
    list_filter = ('model', 'status', 'plan', 'account')
    search_fields = ('kit_number', 'client__name', 'location_name', 'serial_number', 'starlink_id',
                     'service_line_number', 'starlink_nickname')


@admin.register(ServicePlan)
class ServicePlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'currency', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
