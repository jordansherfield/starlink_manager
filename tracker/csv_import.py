import csv
import datetime
import io

from django.db import transaction

from .models import Client, Credential, StarlinkAccount, StarlinkDevice

MODEL_ALIASES = {
    'GEN2': 'gen2',
    'GEN3': 'Gen3',
    'MINI': 'Mini',
    'MIINI': 'Mini',
}

STATUS_KEYWORDS = [
    ('running', 'Running'),
    ('deactivat', 'Deactivated'),
    ('fault', 'Hardware fault'),
    ('account issue', 'Account issue'),
    ('install', 'To install'),
    ('deliver', 'To install'),
    ('commission', 'To install'),
]


def normalize_model(raw):
    key = raw.strip().upper()
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key], None
    return 'Gen3', f"Unrecognized model type from import: '{raw}'"


def normalize_status(raw):
    text = raw.strip().lower()
    if not text:
        return 'To install', None
    for keyword, status in STATUS_KEYWORDS:
        if keyword in text:
            return status, None if status.lower() == text else f"Original remarks: '{raw.strip()}'"
    return 'To install', f"Original remarks: '{raw.strip()}'"


def parse_lat_long(raw):
    if not raw.strip():
        return None, None
    parts = raw.split(',')
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None, None


def parse_billing_cycle(raw, today):
    text = raw.strip().rstrip('.')
    if not text or ' - ' not in text:
        return None, None
    start_text, end_text = [p.strip() for p in text.split(' - ', 1)]
    try:
        start = datetime.datetime.strptime(start_text, '%B %d').replace(year=today.year).date()
        end = datetime.datetime.strptime(end_text, '%B %d').replace(year=today.year).date()
    except ValueError:
        return None, None
    if end < start:
        end = end.replace(year=end.year + 1)
    while end < today:
        start = start.replace(year=start.year + 1)
        end = end.replace(year=end.year + 1)
    return start, end


class _RollbackPreview(Exception):
    """Raised to unwind a transaction.atomic() block after a dry-run pass."""


class ImportSummary:
    def __init__(self):
        self.rows = []
        self.clients_created = 0
        self.accounts_created = 0
        self.devices_created = 0
        self.devices_updated = 0
        self.credentials_created = 0
        self.credentials_updated = 0
        self.skipped_no_client = 0
        self.skipped_no_kit = 0

    @property
    def total_processed(self):
        return self.devices_created + self.devices_updated

    @property
    def total_skipped(self):
        return self.skipped_no_client + self.skipped_no_kit


def find_data_start(rows):
    for i, row in enumerate(rows):
        if len(row) > 2 and row[2].strip().lower() == 'client':
            return i + 2
    return None


def run_import(csv_text, commit=True):
    """
    Parse the NetConnect-format CSV and write Clients/Accounts/Devices/Credentials.

    When commit=False, every database operation still executes (so create-vs-update
    detection is exact) but is rolled back at the end - this makes the preview and
    the real import guaranteed to agree.
    """
    today = datetime.date.today()
    rows = list(csv.reader(io.StringIO(csv_text)))

    data_start = find_data_start(rows)
    if data_start is None:
        raise ValueError("Could not find the 'Client' header row - is this the expected CSV format?")
    data_rows = rows[data_start:]

    summary = ImportSummary()

    try:
        with transaction.atomic():
            for line_num, row in enumerate(data_rows, start=data_start + 1):
                row = row + [''] * (12 - len(row))
                remarks, address, client_name, model_raw, account_number, starlink_id, \
                    serial_number, kit_number, user1, user2, password, billing_cycle = row[:12]

                client_name = client_name.strip()
                if not client_name:
                    summary.skipped_no_client += 1
                    continue

                kit_number = kit_number.strip()
                if not kit_number:
                    summary.skipped_no_kit += 1
                    summary.rows.append({
                        'line': line_num, 'client_name': client_name, 'kit_number': None,
                        'account_number': account_number.strip() or None,
                        'skipped': True, 'skip_reason': 'No KIT NUMBER',
                    })
                    continue

                account_number = account_number.strip()
                status, status_note = normalize_status(remarks)
                model_value, model_note = normalize_model(model_raw)
                latitude, longitude = parse_lat_long(address)
                notes = "; ".join(n for n in (status_note, model_note) if n)

                client, client_created = Client.objects.get_or_create(name=client_name)
                if address.strip() and not client.address:
                    client.address = address.strip()
                    client.save(update_fields=['address'])
                if client_created:
                    summary.clients_created += 1

                account = None
                account_created = False
                creds_this_row = []
                if account_number:
                    start_date, due_date = parse_billing_cycle(billing_cycle, today)
                    account, account_created = StarlinkAccount.objects.get_or_create(
                        account_number=account_number,
                        defaults={
                            'last_payment_date': start_date,
                            'account_due_by': due_date,
                        },
                    )
                    if account_created:
                        summary.accounts_created += 1

                    for label, username in (('Starlink Login', user1), ('Email Login', user2)):
                        username = username.strip()
                        if not username:
                            continue
                        _, cred_created = Credential.objects.update_or_create(
                            account=account,
                            username=username,
                            defaults={'label': label, 'password': password.strip()},
                        )
                        if cred_created:
                            summary.credentials_created += 1
                        else:
                            summary.credentials_updated += 1
                        creds_this_row.append(username)

                _, device_created = StarlinkDevice.objects.update_or_create(
                    kit_number=kit_number,
                    defaults={
                        'client': client,
                        'account': account,
                        'location_name': client_name,
                        'model': model_value,
                        'starlink_id': starlink_id.strip() or None,
                        'serial_number': serial_number.strip() or None,
                        'latitude': latitude,
                        'longitude': longitude,
                        'status': status,
                        'notes': notes or None,
                    },
                )
                if device_created:
                    summary.devices_created += 1
                else:
                    summary.devices_updated += 1

                summary.rows.append({
                    'line': line_num,
                    'client_name': client_name,
                    'client_created': client_created,
                    'kit_number': kit_number,
                    'account_number': account_number or None,
                    'account_created': account_created,
                    'credentials': creds_this_row,
                    'device_action': 'create' if device_created else 'update',
                    'status': status,
                    'model': model_value,
                    'notes': notes or None,
                    'skipped': False,
                })

            if not commit:
                raise _RollbackPreview()
    except _RollbackPreview:
        pass

    return summary


def export_csv():
    """Flatten current Clients/Accounts/Devices/Credentials into one CSV (one row per device)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'Client', 'Company', 'Client Email', 'Client Phone', 'Client Address',
        'Account Number', 'Last Payment Date', 'Account Due By', 'Cost To Us', 'Client Invoice',
        'Kit Number', 'Model', 'Status', 'Starlink ID', 'Serial Number',
        'Latitude', 'Longitude', 'Wifi Name', 'Wifi Password', 'Device Notes', 'Credentials',
    ])

    devices = (
        StarlinkDevice.objects
        .select_related('client', 'account')
        .prefetch_related('account__credentials')
        .order_by('client__name', 'kit_number')
    )
    for d in devices:
        account = d.account
        credentials = ''
        if account:
            credentials = ' | '.join(
                f"{c.label}: {c.username} / {c.password}" for c in account.credentials.all()
            )
        writer.writerow([
            d.client.name if d.client else '',
            d.client.company if d.client else '',
            d.client.email if d.client else '',
            d.client.phone if d.client else '',
            d.client.address if d.client else '',
            account.account_number if account else '',
            account.last_payment_date.isoformat() if account and account.last_payment_date else '',
            account.account_due_by.isoformat() if account and account.account_due_by else '',
            account.cost_to_us if account else '',
            account.client_invoice if account else '',
            d.kit_number,
            d.get_model_display(),
            d.status,
            d.starlink_id or '',
            d.serial_number or '',
            d.latitude if d.latitude is not None else '',
            d.longitude if d.longitude is not None else '',
            d.wifi_name or '',
            d.wifi_password or '',
            d.notes or '',
            credentials,
        ])
    return buffer.getvalue()
