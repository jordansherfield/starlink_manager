import re
import datetime
import decimal
from django.apps import apps
from django.db import models
from .models import AuditLog

def parse_related_id(val):
    if not val:
        return None
    match = re.search(r'\(ID:\s*(\d+)\)', val)
    if match:
        return int(match.group(1))
    return None

def restore_field_value(field, val_str):
    if val_str is None:
        return None
    
    # If the field is a foreign key
    if field.is_relation and (field.many_to_one or field.one_to_one):
        related_id = parse_related_id(val_str)
        if related_id is not None:
            related_model = field.related_model
            try:
                return related_model.objects.get(pk=related_id)
            except related_model.DoesNotExist:
                return None
        return None

    # Handle nullable fields when value is empty
    if val_str == "" and field.null:
        return None

    # Handle Date and DateTime fields
    if isinstance(field, models.DateTimeField):
        try:
            return datetime.datetime.fromisoformat(val_str)
        except ValueError:
            return None
    elif isinstance(field, models.DateField):
        try:
            return datetime.date.fromisoformat(val_str)
        except ValueError:
            return None

    # Handle Numeric fields
    if isinstance(field, models.DecimalField):
        try:
            return decimal.Decimal(val_str)
        except (ValueError, decimal.InvalidOperation):
            return decimal.Decimal('0.00')
    elif isinstance(field, models.FloatField):
        try:
            return float(val_str)
        except ValueError:
            return 0.0
    elif isinstance(field, (models.IntegerField, models.AutoField)):
        try:
            return int(val_str)
        except ValueError:
            return 0

    # Handle Boolean fields
    if isinstance(field, models.BooleanField):
        return val_str.lower() in ('true', '1', 'yes')

    # Default to string/text fields
    return val_str

def revert_log_entry(log):
    """
    Reverts a single audit log entry (reversing the action).
    """
    model = apps.get_model('tracker', log.model_name)
    if log.action == 'CREATE':
        # Revert creation -> Delete the created object
        try:
            obj = model.objects.get(pk=log.object_id)
            obj.delete()
        except model.DoesNotExist:
            pass # already deleted
            
    elif log.action == 'UPDATE':
        # Revert update -> Restore old values
        try:
            obj = model.objects.get(pk=log.object_id)
            for field_name, vals in log.changes.items():
                old_val_str = vals[0]
                field = model._meta.get_field(field_name)
                restored_val = restore_field_value(field, old_val_str)
                setattr(obj, field_name, restored_val)
            obj.save()
        except model.DoesNotExist:
            pass # object no longer exists
            
    elif log.action == 'DELETE':
        # Revert deletion -> Recreate the object with its old values
        field_values = {}
        for field_name, vals in log.changes.items():
            old_val_str = vals[0]
            field = model._meta.get_field(field_name)
            restored_val = restore_field_value(field, old_val_str)
            field_values[field_name] = restored_val
        
        obj = model(pk=log.object_id, **field_values)
        obj.save()

def redo_log_entry(log):
    """
    Re-applies a single audit log entry.
    """
    model = apps.get_model('tracker', log.model_name)
    if log.action == 'CREATE':
        # Redo creation -> Recreate the object with its new values
        field_values = {}
        for field_name, vals in log.changes.items():
            new_val_str = vals[1]
            field = model._meta.get_field(field_name)
            restored_val = restore_field_value(field, new_val_str)
            field_values[field_name] = restored_val
        
        obj = model(pk=log.object_id, **field_values)
        obj.save()
        
    elif log.action == 'UPDATE':
        # Redo update -> Apply new values
        try:
            obj = model.objects.get(pk=log.object_id)
            for field_name, vals in log.changes.items():
                new_val_str = vals[1]
                field = model._meta.get_field(field_name)
                restored_val = restore_field_value(field, new_val_str)
                setattr(obj, field_name, restored_val)
            obj.save()
        except model.DoesNotExist:
            pass
            
    elif log.action == 'DELETE':
        # Redo deletion -> Delete the object
        try:
            obj = model.objects.get(pk=log.object_id)
            obj.delete()
        except model.DoesNotExist:
            pass
