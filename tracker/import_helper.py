import csv
import io
import re
import datetime
import uuid
from django.db import transaction
from .models import Client, StarlinkAccount, StarlinkDevice, Credential

def parse_gps_from_address(addr):
    if not addr:
        return None, None
    # Match any pair of floating point numbers separated by a comma
    match = re.search(r'(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)', addr)
    if match:
        try:
            return float(match.group(1)), float(match.group(2))
        except ValueError:
            pass
    return None, None

def parse_date(val):
    if not val:
        return None
    val = val.strip()
    # Try different common formats
    for fmt in ('%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    # If it is just a number representing the day of the month
    if val.isdigit():
        day = int(val)
        if 1 <= day <= 31:
            today = datetime.date.today()
            try:
                return datetime.date(today.year, today.month, day)
            except ValueError:
                # Handle end of month constraints
                return datetime.date(today.year, today.month, 28)
    return None

def parse_money(val):
    if not val:
        return 0.00
    val = val.replace('$', '').replace(',', '').strip()
    try:
        return float(val)
    except ValueError:
        return 0.00

def map_status(val):
    if not val:
        return 'To install'
    val = val.strip().lower()
    if 'running' in val:
        return 'Running'
    elif 'suspend' in val or 'issue' in val:
        return 'Account issue'
    elif 'deactivate' in val:
        return 'Deactivated'
    elif 'fault' in val:
        return 'Hardware fault'
    else:
        return 'To install'

def map_model(val):
    if not val:
        return 'Gen3'
    val = val.strip().lower()
    if 'gen2' in val:
        return 'gen2'
    elif 'mini' in val:
        return 'Mini'
    else:
        return 'Gen3'

def map_company(val):
    if not val:
        return 'Comnet'
    val = val.strip().lower()
    if 'farbell' in val:
        return 'Farbell'
    elif 'afrinet' in val:
        return 'Afrinet'
    else:
        return 'Comnet'

def import_csv_with_mapping(file_file, mapping, skip_rows=3):
    """
    Imports CSV data based on mapping configuration.
    mapping parameter maps string field names (e.g. 'client_name') to integer column indexes.
    """
    # Read the file wrapper content
    content = file_file.read()
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='ignore')
        
    csv_file = io.StringIO(content)
    reader = csv.reader(csv_file)
    
    stats = {
        'clients_created': 0,
        'clients_updated': 0,
        'accounts_created': 0,
        'accounts_updated': 0,
        'devices_created': 0,
        'devices_updated': 0,
        'credentials_created': 0,
        'rows_processed': 0
    }
    
    # Helper to retrieve row value safely based on mapping key
    def get_val(row, field):
        idx = mapping.get(field)
        if idx is not None and idx != "":
            try:
                idx = int(idx)
                if 0 <= idx < len(row):
                    return row[idx].strip()
            except (ValueError, TypeError):
                pass
        return ""

    with transaction.atomic():
        for i, row in enumerate(reader):
            # Skip header rows
            if i < skip_rows:
                continue
            
            # Check if row is empty
            if not row or not any(row):
                continue
                
            stats['rows_processed'] += 1
            
            # 1. Process Client
            client_name = get_val(row, 'client_name')
            if not client_name:
                # Skip row if client name is empty
                continue
                
            company_val = map_company(get_val(row, 'client_company'))
            address_val = get_val(row, 'client_address')
            notes_val = get_val(row, 'device_notes')
            
            client, created = Client.objects.get_or_create(
                name=client_name,
                defaults={
                    'company': company_val,
                    'address': address_val,
                    'notes': f"Imported from CSV. Notes: {notes_val}" if notes_val else ""
                }
            )
            
            if created:
                stats['clients_created'] += 1
            else:
                # Update company and address if they were empty or different
                updated = False
                if client.company != company_val:
                    client.company = company_val
                    updated = True
                if address_val and not client.address:
                    client.address = address_val
                    updated = True
                if updated:
                    client.save()
                    stats['clients_updated'] += 1
            
            # 2. Process StarlinkAccount
            account_number = get_val(row, 'account_number')
            account = None
            if account_number:
                cost_val = parse_money(get_val(row, 'cost_to_us'))
                invoice_val = parse_money(get_val(row, 'client_invoice'))
                due_val = parse_date(get_val(row, 'due_date'))
                
                account, acc_created = StarlinkAccount.objects.get_or_create(
                    account_number=account_number,
                    defaults={
                        'client': client,
                        'cost_to_us': cost_val,
                        'client_invoice': invoice_val,
                        'account_due_by': due_val
                    }
                )
                
                if acc_created:
                    stats['accounts_created'] += 1
                else:
                    acc_updated = False
                    if not account.client:
                        account.client = client
                        acc_updated = True
                    if cost_val and account.cost_to_us != cost_val:
                        account.cost_to_us = cost_val
                        acc_updated = True
                    if invoice_val and account.client_invoice != invoice_val:
                        account.client_invoice = invoice_val
                        acc_updated = True
                    if due_val and account.account_due_by != due_val:
                        account.account_due_by = due_val
                        acc_updated = True
                    if acc_updated:
                        account.save()
                        stats['accounts_updated'] += 1
            
            # 3. Process Credentials
            if account:
                # Credential 1
                cred1_user = get_val(row, 'cred1_user')
                cred1_pass = get_val(row, 'cred1_pass')
                if cred1_user:
                    cred1, c1_created = Credential.objects.get_or_create(
                        account=account,
                        username=cred1_user,
                        defaults={
                            'password': cred1_pass or 'ImportedChangeMe',
                            'label': 'Main Account Login'
                        }
                    )
                    if c1_created:
                        stats['credentials_created'] += 1
                        
                # Credential 2
                cred2_user = get_val(row, 'cred2_user')
                cred2_pass = get_val(row, 'cred2_pass')
                if cred2_user:
                    cred2, c2_created = Credential.objects.get_or_create(
                        account=account,
                        username=cred2_user,
                        defaults={
                            'password': cred2_pass or 'ImportedChangeMe',
                            'label': 'Backup Account Login'
                        }
                    )
                    if c2_created:
                        stats['credentials_created'] += 1
            
            # 4. Process StarlinkDevice
            kit_number = get_val(row, 'kit_number')
            if not kit_number:
                # Auto generate if missing
                kit_number = f"TEMP-KIT-{uuid.uuid4().hex[:8].upper()}"
                
            location_val = get_val(row, 'location_name') or f"Location for {client_name}"
            model_val = map_model(get_val(row, 'device_model'))
            id_val = get_val(row, 'starlink_id')
            serial_val = get_val(row, 'serial_number')
            wifi_name_val = get_val(row, 'wifi_name')
            wifi_pass_val = get_val(row, 'wifi_password')
            status_val = map_status(get_val(row, 'device_status'))
            
            # Parse GPS from address
            lat_val, lng_val = parse_gps_from_address(address_val)
            
            device, dev_created = StarlinkDevice.objects.get_or_create(
                kit_number=kit_number,
                defaults={
                    'client': client,
                    'account': account,
                    'location_name': location_val,
                    'model': model_val,
                    'starlink_id': id_val,
                    'serial_number': serial_val,
                    'wifi_name': wifi_name_val,
                    'wifi_password': wifi_pass_val,
                    'status': status_val,
                    'latitude': lat_val,
                    'longitude': lng_val,
                    'notes': notes_val
                }
            )
            
            if dev_created:
                stats['devices_created'] += 1
            else:
                dev_updated = False
                if not device.client:
                    device.client = client
                    dev_updated = True
                if account and not device.account:
                    device.account = account
                    dev_updated = True
                if id_val and device.starlink_id != id_val:
                    device.starlink_id = id_val
                    dev_updated = True
                if serial_val and device.serial_number != serial_val:
                    device.serial_number = serial_val
                    dev_updated = True
                if wifi_name_val and device.wifi_name != wifi_name_val:
                    device.wifi_name = wifi_name_val
                    dev_updated = True
                if wifi_pass_val and device.wifi_password != wifi_pass_val:
                    device.wifi_password = wifi_pass_val
                    dev_updated = True
                if status_val and device.status != status_val:
                    device.status = status_val
                    dev_updated = True
                if notes_val and device.notes != notes_val:
                    device.notes = notes_val
                    dev_updated = True
                if lat_val is not None and device.latitude != lat_val:
                    device.latitude = lat_val
                    dev_updated = True
                if lng_val is not None and device.longitude != lng_val:
                    device.longitude = lng_val
                    dev_updated = True
                if dev_updated:
                    device.save()
                    stats['devices_updated'] += 1
                    
    return stats
