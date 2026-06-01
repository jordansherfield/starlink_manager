import datetime
import decimal
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.db import models
from django.contrib.auth.models import User
from .models import Client, StarlinkAccount, Credential, StarlinkDevice, AuditLog
from tracker.middleware import get_current_user

def serialize_value(val):
    if val is None:
        return ""
    if isinstance(val, (int, float, decimal.Decimal)):
        return str(val)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    # Check if this is a model instance
    if isinstance(val, models.Model):
        if hasattr(val, 'email') and getattr(val, 'email'):
            return f"{val} ({val.email}) (ID: {val.pk})"
        if hasattr(val, 'account_number'):
            return f"Account {getattr(val, 'account_number')} (ID: {val.pk})"
        return f"{val} (ID: {val.pk})"
    return str(val)

@receiver(pre_save, sender=Client)
@receiver(pre_save, sender=StarlinkAccount)
@receiver(pre_save, sender=Credential)
@receiver(pre_save, sender=StarlinkDevice)
def track_pre_save_changes(sender, instance, **kwargs):
    if not instance.pk:
        # This is a creation, handled in post_save
        instance._pending_changes = None
        return

    try:
        old_instance = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        instance._pending_changes = None
        return

    changes = {}
    for field in sender._meta.fields:
        field_name = field.name
        if field_name in ['created_at', 'updated_at', 'id']:
            continue
        
        try:
            old_val = getattr(old_instance, field_name)
            new_val = getattr(instance, field_name)
        except AttributeError:
            continue

        if old_val != new_val:
            changes[field_name] = [serialize_value(old_val), serialize_value(new_val)]

    instance._pending_changes = changes

@receiver(post_save, sender=Client)
@receiver(post_save, sender=StarlinkAccount)
@receiver(post_save, sender=Credential)
@receiver(post_save, sender=StarlinkDevice)
def track_post_save_changes(sender, instance, created, **kwargs):
    user = get_current_user()
    model_name = sender.__name__
    object_id = instance.pk
    object_repr = str(instance)
    
    # Extract kit_number if applicable
    kit_number = None
    if isinstance(instance, StarlinkDevice):
        kit_number = instance.kit_number

    if created:
        action = "CREATE"
        changes = {}
        for field in sender._meta.fields:
            field_name = field.name
            if field_name in ['created_at', 'updated_at', 'id']:
                continue
            changes[field_name] = [None, serialize_value(getattr(instance, field_name))]
        
        # Log the creation
        log = AuditLog.objects.create(
            user=user,
            action=action,
            model_name=model_name,
            object_id=object_id,
            object_repr=object_repr,
            kit_number=kit_number,
            changes=changes
        )
        
        # If this is a StarlinkDevice, log its association
        if isinstance(instance, StarlinkDevice):
            pass # already logged on the device creation log
        
        # If a Credential is created, propagate to all linked kits
        elif isinstance(instance, Credential) and instance.account:
            for sl in instance.account.starlinks.all():
                AuditLog.objects.create(
                    user=user,
                    action="UPDATE",
                    model_name="StarlinkDevice",
                    object_id=sl.id,
                    object_repr=str(sl),
                    kit_number=sl.kit_number,
                    changes={"associated_login": [None, f"{instance.label}: {instance.username}"]}
                )
    else:
        # Update
        action = "UPDATE"
        changes = getattr(instance, '_pending_changes', None)
        if not changes:
            return

        # Log main entry
        main_log = AuditLog.objects.create(
            user=user,
            action=action,
            model_name=model_name,
            object_id=object_id,
            object_repr=object_repr,
            kit_number=kit_number,
            changes=changes
        )

        # Propagate specific changes to associated Starlink kits
        if isinstance(instance, Client):
            # If email changes, log it for all client's kits
            if 'email' in changes:
                for sl in instance.starlinks.all():
                    AuditLog.objects.create(
                        user=user,
                        action="UPDATE",
                        model_name="StarlinkDevice",
                        object_id=sl.id,
                        object_repr=str(sl),
                        kit_number=sl.kit_number,
                        changes={"client_email": changes['email']}
                    )
        
        elif isinstance(instance, Credential):
            # If username/email or password changes, log it for all account's kits
            if 'username' in changes or 'password' in changes or 'email_pass' in changes:
                prop_changes = {}
                if 'username' in changes:
                    prop_changes['login_username'] = changes['username']
                if 'password' in changes:
                    prop_changes['login_password'] = changes['password']
                if 'email_pass' in changes:
                    prop_changes['login_email_password'] = changes['email_pass']
                
                if instance.account:
                    for sl in instance.account.starlinks.all():
                        AuditLog.objects.create(
                            user=user,
                            action="UPDATE",
                            model_name="StarlinkDevice",
                            object_id=sl.id,
                            object_repr=str(sl),
                            kit_number=sl.kit_number,
                            changes=prop_changes
                        )

@receiver(post_delete, sender=Client)
@receiver(post_delete, sender=StarlinkAccount)
@receiver(post_delete, sender=Credential)
@receiver(post_delete, sender=StarlinkDevice)
def track_post_delete(sender, instance, **kwargs):
    user = get_current_user()
    model_name = sender.__name__
    object_id = instance.pk
    object_repr = str(instance)
    
    # Extract kit_number if applicable
    kit_number = None
    if isinstance(instance, StarlinkDevice):
        kit_number = instance.kit_number

    action = "DELETE"
    changes = {}
    for field in sender._meta.fields:
        field_name = field.name
        if field_name in ['created_at', 'updated_at', 'id']:
            continue
        changes[field_name] = [serialize_value(getattr(instance, field_name)), None]

    AuditLog.objects.create(
        user=user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        object_repr=object_repr,
        kit_number=kit_number,
        changes=changes
    )
