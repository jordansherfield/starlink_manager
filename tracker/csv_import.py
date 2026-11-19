import csv
import datetime
import decimal
import io

from django.db import transaction

from .models import Client, Credential, ServicePlan, StarlinkAccount, StarlinkDevice

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

COMPANY_VALUES = {value for value, _ in Client.COMPANY_CHOICES}

# Header row written by export_csv(); kept in one place so import can recognise it.
EXPORT_HEADER = [
    'Client', 'Company', 'Client Email', 'Client Phone', 'Client Address',
    'Account Number', 'Last Payment Date', 'Account Due By', 'Cost To Us', 'Client Invoice',
    'Kit Number', 'Model', 'Status', 'Starlink ID', 'Serial Number',
    'Latitude', 'Longitude', 'Wifi Name', 'Wifi Password', 'Device Notes', 'Credentials',
    # Starlink service details (added with service plans; older files without them still import)
    'Service Line', 'Starlink Nickname', 'Service Address', 'Plan', 'Plan Price',
    'Price Override', 'Price Override Reason', 'Balance Due', 'Synced From Starlink',
]

FORMAT_NETCONNECT = 'NetConnect tracker sheet'
FORMAT_EXPORT = 'StarlinkOps export'
FORMAT_HARVEST = 'Starlink harvest'

# Columns that identify the Starlink harvester's starlink_accounts.csv
HARVEST_MARKERS = {'email', 'service_line', 'kit_numbers'}


def normalize_model(raw):
    """Return (model_value or None, note). None means "leave as is / use default"."""
    key = ''.join(raw.split()).upper()
    if not key:
        return None, None
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key], None
    return None, f"Unrecognized model type from import: '{raw.strip()}'"


def normalize_status(raw):
    """Return (status or None, note). None means the Remarks cell was blank."""
    text = raw.strip().lower()
    if not text:
        return None, None
    for keyword, status in STATUS_KEYWORDS:
        if keyword in text:
            return status, None if status.lower() == text else f"Original remarks: '{raw.strip()}'"
    return 'To install', f"Original remarks: '{raw.strip()}'"


def normalize_name(name):
    """Case/whitespace-insensitive key used to match client names."""
    return ' '.join(name.split()).casefold()


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


def parse_float(raw):
    try:
        return float(raw.strip()) if raw.strip() else None
    except ValueError:
        return None


def parse_decimal(raw):
    try:
        return decimal.Decimal(raw.strip()) if raw.strip() else None
    except decimal.InvalidOperation:
        return None


def parse_iso_datetime(raw):
    try:
        value = datetime.datetime.fromisoformat(raw.strip()) if raw.strip() else None
    except ValueError:
        return None
    if value is not None and value.tzinfo is None:
        from django.utils import timezone
        value = timezone.make_aware(value)
    return value


def parse_iso_date(raw):
    try:
        return datetime.date.fromisoformat(raw.strip()) if raw.strip() else None
    except ValueError:
        return None


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


def parse_export_credentials(raw):
    """Inverse of the 'Credentials' column in export_csv(): 'Label: user / pass | ...'."""
    creds = []
    for part in raw.split(' | '):
        part = part.strip()
        if not part:
            continue
        label, sep, rest = part.partition(': ')
        if not sep:
            label, rest = 'Starlink Account', part
        username, _, password = rest.partition(' / ')
        creds.append((label.strip() or 'Starlink Account', username.strip(), password.strip()))
    return creds


class _RollbackPreview(Exception):
    """Raised to unwind a transaction.atomic() block after a dry-run pass."""


class ImportSummary:
    def __init__(self):
        self.format = None
        self.rows = []
        self.clients_created = 0
        self.accounts_created = 0
        self.accounts_updated = 0
        self.devices_created = 0
        self.devices_updated = 0
        self.devices_unchanged = 0
        self.devices_restored = 0
        self.archived_found = 0
        self.credentials_created = 0
        self.credentials_updated = 0
        self.skipped_no_client = 0
        self.skipped_no_kit = 0
        self.skipped_duplicate = 0
        self.plans_created = 0
        self.skipped_not_harvested = 0
        self.notes = []          # account-level findings shown above the row list

    @property
    def total_processed(self):
        return self.devices_created + self.devices_updated + self.devices_unchanged

    @property
    def total_skipped(self):
        return self.skipped_no_client + self.skipped_no_kit + self.skipped_duplicate + self.skipped_not_harvested


def find_data_start(rows):
    for i, row in enumerate(rows):
        if len(row) > 2 and row[2].strip().lower() == 'client':
            return i + 2
    return None


def _cell(row, i):
    return row[i] if i < len(row) else ''


def _netconnect_records(rows, today):
    data_start = find_data_start(rows)
    if data_start is None:
        return None
    for line_num, row in enumerate(rows[data_start:], start=data_start + 1):
        remarks, address, client_name, model_raw, account_number, starlink_id, \
            serial_number, kit_number, user1, user2, password, billing_cycle = [_cell(row, i) for i in range(12)]
        latitude, longitude = parse_lat_long(address)
        start_date, due_date = parse_billing_cycle(billing_cycle, today)
        yield {
            'line': line_num,
            'client_name': client_name,
            # The sheet's Address column holds GPS text, so only fill an empty client address.
            'client_fill_only': {'address': address.strip()},
            'client_fields': {},
            'account_number': account_number.strip(),
            'last_payment_date': start_date,
            'account_due_by': due_date,
            'account_fields': {},
            'credentials': [
                (label, username.strip(), password.strip())
                for label, username in (('Starlink Login', user1), ('Email Login', user2))
                if username.strip()
            ],
            'kit_number': kit_number.strip(),
            'model_raw': model_raw,
            'status_raw': remarks,
            'device_fields': {
                'starlink_id': starlink_id.strip(),
                'serial_number': serial_number.strip(),
                'latitude': latitude,
                'longitude': longitude,
            },
            'device_notes': None,
            'plan': {},
            'allow_unassigned': False,
        }


def _export_records(rows):
    header = [h.strip() for h in rows[0]]
    col = {name: header.index(name) for name in EXPORT_HEADER if name in header}

    def get(row, name):
        return _cell(row, col[name]).strip() if name in col else ''

    for line_num, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        company = get(row, 'Company')
        yield {
            'line': line_num,
            'client_name': get(row, 'Client'),
            'client_fill_only': {},
            'client_fields': {
                'company': company if company in COMPANY_VALUES else '',
                'email': get(row, 'Client Email'),
                'phone': get(row, 'Client Phone'),
                'address': get(row, 'Client Address'),
            },
            'account_number': get(row, 'Account Number'),
            'last_payment_date': parse_iso_date(get(row, 'Last Payment Date')),
            'account_due_by': parse_iso_date(get(row, 'Account Due By')),
            'account_fields': {
                'cost_to_us': parse_decimal(get(row, 'Cost To Us')),
                'client_invoice': parse_decimal(get(row, 'Client Invoice')),
                'balance_due': parse_decimal(get(row, 'Balance Due')),
                'starlink_synced_at': parse_iso_datetime(get(row, 'Synced From Starlink')),
            },
            'credentials': [c for c in parse_export_credentials(get(row, 'Credentials')) if c[1]],
            'kit_number': get(row, 'Kit Number'),
            'model_raw': get(row, 'Model'),
            'status_raw': get(row, 'Status'),
            'device_fields': {
                'starlink_id': get(row, 'Starlink ID'),
                'serial_number': get(row, 'Serial Number'),
                'latitude': parse_float(get(row, 'Latitude')),
                'longitude': parse_float(get(row, 'Longitude')),
                'wifi_name': get(row, 'Wifi Name'),
                'wifi_password': get(row, 'Wifi Password'),
                'service_line_number': get(row, 'Service Line'),
                'starlink_nickname': get(row, 'Starlink Nickname'),
                'service_address': get(row, 'Service Address'),
                'price_override': parse_decimal(get(row, 'Price Override')),
                'price_override_reason': get(row, 'Price Override Reason'),
            },
            'device_notes': get(row, 'Device Notes'),
            'plan': {'name': get(row, 'Plan'), 'price': parse_decimal(get(row, 'Plan Price'))},
            # Exported unassigned kits have a blank Client column.
            'allow_unassigned': True,
        }


def _split(value):
    return [x.strip() for x in (value or '').split(' | ')] if (value or '').strip() else []


def _money(value):
    """'11.52 USD' / '59.99' -> Decimal, else None."""
    first = (value or '').replace(',', '').split()
    return parse_decimal(first[0]) if first else None


def _harvest_account_cost(rows):
    """What Starlink bills the login per cycle: the shared invoice, or the sum of per-service bills."""
    shared = [r for r in rows if (r.get('bill_scope') or '').startswith('ACCOUNT TOTAL')]
    if shared:
        return _money(shared[0].get('last_bill_amount'))
    amounts = [_money(r.get('last_bill_amount')) for r in rows if r.get('status') in ('ok', 'partial')]
    amounts = [a for a in amounts if a is not None]
    return sum(amounts, decimal.Decimal('0')) if amounts else None


def _harvest_records(rows):
    header = [h.strip() for h in rows[0]]
    data = []
    for line_num, row in enumerate(rows[1:], start=2):
        if any(c.strip() for c in row):
            rec = {h: _cell(row, i).strip() for i, h in enumerate(header)}
            rec['_line'] = line_num
            data.append(rec)
    by_login = {}
    for r in data:
        by_login.setdefault(r.get('email', '').lower(), []).append(r)
    cost_by_login = {k: _harvest_account_cost(v) for k, v in by_login.items()}

    for r in data:
        status = r.get('status', '')
        kits = [k.upper() for k in _split(r.get('kit_numbers'))]
        if status not in ('ok', 'partial') or not kits:
            yield {
                'line': r['_line'], 'skip_reason': (f"Not harvested ({status}) - {r.get('email', '')}"
                                                    if status not in ('ok', 'partial') else
                                                    f"No kit number found for service {r.get('service_line') or '?'}"),
                'client_name': '', 'kit_number': '', 'account_number': r.get('account_number', ''),
            }
            continue
        ids, dishes, types = _split(r.get('starlink_ids')), _split(r.get('dish_serials')), _split(r.get('dish_types'))
        lat_lon = (_split(r.get('coordinates')) or [''])[0]
        latitude, longitude = parse_lat_long(lat_lon)
        next_due = parse_iso_date(r.get('next_due_date', ''))
        active = {'Active': True, 'Inactive': False}.get(r.get('service_active', ''))
        bitwarden_id = r.get('bitwarden_item_id', '')
        for i, kit in enumerate(kits):
            yield {
                'line': r['_line'],
                'harvest': True,
                'client_name': '',
                'client_fill_only': {},
                'client_fields': {},
                'account_number': r.get('account_number', ''),
                'last_payment_date': parse_iso_date(r.get('last_bill_date', '')),
                'account_due_by': next_due - datetime.timedelta(days=1) if next_due else None,
                'account_fields': {
                    'cost_to_us': cost_by_login.get(r.get('email', '').lower()),
                    'balance_due': _money(r.get('balance_due')),
                    'starlink_synced_at': parse_iso_datetime(r.get('scraped_at', '')),
                },
                'credentials': ([('Starlink Login', r['email'], '', bitwarden_id)] if r.get('email') else []),
                'kit_number': kit,
                'model_raw': types[i] if i < len(types) else '',
                'status_raw': '',
                'service_active': active,
                'location_name': r.get('nickname') or r.get('location') or '',
                'device_fields': {
                    'starlink_id': ids[i] if i < len(ids) else '',
                    'serial_number': dishes[i] if i < len(dishes) else '',
                    'latitude': latitude,
                    'longitude': longitude,
                    'service_line_number': r.get('service_line', ''),
                    'starlink_nickname': r.get('nickname', ''),
                    'service_address': r.get('location', ''),
                    'starlink_service_active': active,
                },
                'device_notes': None,
                'plan': {'name': r.get('plan', ''), 'price': None},
                'allow_unassigned': True,
            }


def _is_blank(value):
    return value is None or value == ''


def _apply_fields(obj, values, changes, fill_only=False):
    """
    Copy non-blank values onto obj, recording [old, new] in changes.
    A blank CSV cell never clears existing data. With fill_only, only empty fields are filled.
    """
    for field, new in values.items():
        if _is_blank(new):
            continue
        old = getattr(obj, field)
        if fill_only and not _is_blank(old):
            continue
        if old != new:
            setattr(obj, field, new)
            changes[field] = ['' if old is None else str(old), str(new)]


class _PlanIndex:
    """Matches plan names to ServicePlans ignoring case and extra spaces; creates unknown plans."""

    def __init__(self):
        self.by_key = {normalize_name(p.name): p for p in ServicePlan.objects.all()}

    def resolve(self, name, price, summary, warnings):
        if not name.strip():
            return None
        key = normalize_name(name)
        plan = self.by_key.get(key)
        if plan is None:
            plan = ServicePlan.objects.create(name=' '.join(name.split()), price=price)
            self.by_key[key] = plan
            summary.plans_created += 1
            if price is None:
                warnings.append(f"New plan '{plan.name}' created without a price - set it on the Plans page")
        elif plan.price is None and price is not None:
            plan.price = price
            plan.save(update_fields=['price'])
        return plan


class _ClientIndex:
    """Matches CSV client names to existing clients, ignoring case and extra spaces."""

    def __init__(self):
        self.by_key = {}
        for client in Client.objects.order_by('id'):
            self.by_key.setdefault(normalize_name(client.name), []).append(client)

    def get_or_create(self, name):
        key = normalize_name(name)
        matches = self.by_key.get(key)
        if matches:
            note = None
            if len(matches) > 1:
                note = (f"{len(matches)} clients are named '{matches[0].name}' - "
                        f"used the oldest (ID {matches[0].pk})")
            return matches[0], False, note
        client = Client.objects.create(name=' '.join(name.split()))
        self.by_key[key] = [client]
        return client, True, None


def _apply_record(rec, clients, plans, seen_kits, summary, restore_archived):
    line = rec['line']
    client_name = rec['client_name'].strip()
    kit_number = rec['kit_number'].upper()
    account_number = rec['account_number']

    base_row = {
        'line': line, 'client_name': client_name or None, 'kit_number': kit_number or None,
        'account_number': account_number or None, 'skipped': True,
    }

    if rec.get('skip_reason'):
        summary.skipped_not_harvested += 1
        summary.rows.append({**base_row, 'skip_reason': rec['skip_reason']})
        return
    if not client_name and not rec['allow_unassigned']:
        summary.skipped_no_client += 1
        return
    if not kit_number:
        summary.skipped_no_kit += 1
        summary.rows.append({**base_row, 'skip_reason': 'No KIT NUMBER'})
        return
    if kit_number in seen_kits:
        summary.skipped_duplicate += 1
        summary.rows.append({**base_row, 'skip_reason': f'Duplicate of line {seen_kits[kit_number]}'})
        return
    seen_kits[kit_number] = line

    warnings = []
    changes = {}

    # --- Client ---
    client, client_created = None, False
    if client_name:
        client, client_created, client_note = clients.get_or_create(client_name)
        if client_note:
            warnings.append(client_note)
        if client_created:
            summary.clients_created += 1
        client_changes = {}
        _apply_fields(client, rec['client_fill_only'], client_changes, fill_only=True)
        _apply_fields(client, rec['client_fields'], client_changes)
        if client_changes:
            client.save(update_fields=list(client_changes))
            if not client_created:
                changes.update({f'client.{k}': v for k, v in client_changes.items()})

    # --- Account + credentials ---
    account, account_created = None, False
    creds_this_row = []
    if account_number:
        account, account_created = StarlinkAccount.objects.get_or_create(
            account_number=account_number,
            defaults={
                'client': client,
                'last_payment_date': rec['last_payment_date'],
                'account_due_by': rec['account_due_by'],
                **{k: v for k, v in rec['account_fields'].items() if v is not None},
            },
        )
        if account_created:
            summary.accounts_created += 1
        else:
            account_changes = {}
            if account.client_id is None and client:
                account.client = client
                account_changes['client'] = ['', client.name]
            # Only move billing dates forward, so an older sheet can't undo a "mark paid" in the app.
            new_due = rec['account_due_by']
            if new_due and (account.account_due_by is None or new_due > account.account_due_by):
                _apply_fields(account, {
                    'last_payment_date': rec['last_payment_date'],
                    'account_due_by': new_due,
                }, account_changes)
            _apply_fields(account, rec['account_fields'], account_changes)
            if account_changes:
                account.save()
                summary.accounts_updated += 1
                changes.update({f'account.{k}': v for k, v in account_changes.items()})

        for label, username, password, *extra in rec['credentials']:
            bitwarden_id = extra[0] if extra else ''
            cred = Credential.objects.filter(account=account, username__iexact=username).first()
            if cred is None:
                Credential.objects.create(account=account, username=username, label=label, password=password,
                                          bitwarden_item_id=bitwarden_id or None)
                summary.credentials_created += 1
            else:
                cred_changes = {}
                values = {'password': password, 'bitwarden_item_id': bitwarden_id}
                if not rec.get('harvest'):          # a harvest never renames an existing login
                    values['label'] = label
                _apply_fields(cred, values, cred_changes)
                if cred_changes:
                    cred.save()
                    summary.credentials_updated += 1
                    changes[f'login {username}'] = ['', ', '.join(cred_changes)]
            creds_this_row.append(username)

    # --- Device ---
    plan_info = rec.get('plan') or {}
    plan = plans.resolve(plan_info.get('name', ''), plan_info.get('price'), summary, warnings)
    status, status_note = normalize_status(rec['status_raw'])
    model_value, model_note = normalize_model(rec['model_raw'])
    import_notes = "; ".join(n for n in (status_note, model_note) if n)

    device = StarlinkDevice.objects.filter(kit_number__iexact=kit_number).first()
    archived = False
    restored = False
    active = rec.get('service_active')
    if rec.get('harvest'):
        import_notes = ''
        if device is None:      # new from Starlink: its subscription state says whether it's in use
            status = {True: 'Running', False: 'Deactivated'}.get(active, 'To install')
        else:                   # existing units keep their model/status; differences are only flagged
            if model_value and device.model != model_value:
                warnings.append(f"Serial says {dict(StarlinkDevice.MODEL_CHOICES)[model_value]}, "
                                f"app says {device.get_model_display()} - model kept")
            if active is False and device.status == 'Running':
                warnings.append("Starlink service is INACTIVE but the unit is marked Running")
            elif active is True and device.status in ('To install', 'Deactivated'):
                warnings.append(f"Starlink service is active but the unit is marked '{device.status}'")
            model_value, status = None, None
    if device is None:
        notes = rec['device_notes'] or import_notes or None
        device = StarlinkDevice.objects.create(
            kit_number=kit_number,
            client=client,
            account=account,
            location_name=rec.get('location_name') or client_name or kit_number,
            model=model_value or 'Gen3',
            status=status or 'To install',
            notes=notes,
            plan=plan,
            **{k: (None if _is_blank(v) else v) for k, v in rec['device_fields'].items()},
        )
        device_action = 'create'
        summary.devices_created += 1
    else:
        device_changes = {}
        _apply_fields(device, {
            'model': model_value,
            'status': status,
            'notes': rec['device_notes'],
            **rec['device_fields'],
        }, device_changes)
        if client and device.client_id != client.pk:
            device_changes['client'] = [device.client.name if device.client else '', client.name]
            device.client = client
        if plan and device.plan_id != plan.pk:
            device_changes['plan'] = [device.plan.name if device.plan else '', plan.name]
            device.plan = plan
        if account and device.account_id != account.pk:
            device_changes['account'] = [device.account.account_number if device.account else '',
                                         account.account_number]
            device.account = account
        if import_notes and import_notes not in (device.notes or ''):
            old_notes = device.notes or ''
            device.notes = f"{old_notes}\n{import_notes}" if old_notes else import_notes
            device_changes['notes'] = [old_notes, device.notes]
        if device.is_archived:
            archived = True
            summary.archived_found += 1
            if restore_archived:
                device.is_archived = False
                device.archived_reason = None
                device.archived_at = None
                device_changes['archived'] = ['yes', 'no (restored)']
                restored = True
                summary.devices_restored += 1
        if device_changes:
            device.save()
            summary.devices_updated += 1
        else:
            summary.devices_unchanged += 1
        changes.update(device_changes)
        # The row's result reflects every change it makes (client, account, logins), not just the kit.
        device_action = 'update' if changes else 'unchanged'

    if model_note and device_action != 'create':
        warnings.append(model_note + ' - kept existing model')

    summary.rows.append({
        'line': line,
        'client_name': client_name or 'Unassigned',
        'client_created': client_created,
        'kit_number': kit_number,
        'account_number': account_number or None,
        'account_created': account_created,
        'credentials': creds_this_row,
        'device_action': device_action,
        'status': device.status,
        'model': device.get_model_display(),
        'notes': import_notes or None,
        'changes': [(field, old, new) for field, (old, new) in changes.items()],
        'warnings': warnings,
        'archived': archived,
        'restored': restored,
        'skipped': False,
    })


def _harvest_account_checks(summary):
    """After a Starlink harvest: compare each account's bill with its plans, and list balances owed."""
    numbers = {r['account_number'] for r in summary.rows if not r.get('skipped') and r.get('account_number')}
    for account in StarlinkAccount.objects.filter(account_number__in=numbers).order_by('account_number'):
        plan_cost = account.plan_cost
        if plan_cost is not None and account.cost_to_us is not None and plan_cost != account.cost_to_us:
            summary.notes.append(
                f"{account.account_number}: Starlink billed {account.cost_to_us}, plans/overrides add up to "
                f"{plan_cost} - set a price override on the right unit, or fix the plan price.")
        if account.balance_due:
            summary.notes.append(f"{account.account_number}: {account.balance_due} outstanding on Starlink.")
    new_units = sum(1 for r in summary.rows if r.get('device_action') == 'create')
    if new_units:
        summary.notes.insert(0, f"{new_units} new unit(s) from Starlink will be added unassigned - "
                                "assign them on the Starlink Inbox page.")


def run_import(csv_text, commit=True, restore_archived=False):
    """
    Parse a CSV - either the NetConnect tracker sheet or this app's own export - and
    write Clients/Accounts/Devices/Credentials.

    Blank cells never overwrite existing data. When commit=False, every database
    operation still executes (so create-vs-update detection is exact) but is rolled
    back at the end - this makes the preview and the real import guaranteed to agree.
    """
    today = datetime.date.today()
    rows = list(csv.reader(io.StringIO(csv_text)))

    summary = ImportSummary()
    header = [h.strip() for h in rows[0]] if rows else []
    if HARVEST_MARKERS <= set(header):
        summary.format = FORMAT_HARVEST
        records = _harvest_records(rows)
    elif rows and header[:2] == EXPORT_HEADER[:2] and 'Kit Number' in header:
        summary.format = FORMAT_EXPORT
        records = _export_records(rows)
    else:
        summary.format = FORMAT_NETCONNECT
        records = _netconnect_records(rows, today)
        if records is None:
            raise ValueError(
                "Could not recognise this CSV. Expected the Starlink harvest (starlink_accounts.csv), "
                "the NetConnect tracker sheet (with a 'Client' header row) or a file exported from this app."
            )

    try:
        with transaction.atomic():
            clients = _ClientIndex()
            plans = _PlanIndex()
            seen_kits = {}
            for rec in records:
                _apply_record(rec, clients, plans, seen_kits, summary, restore_archived)
            if summary.format == FORMAT_HARVEST:
                _harvest_account_checks(summary)

            if not commit:
                raise _RollbackPreview()
    except _RollbackPreview:
        pass

    return summary


def export_csv(include_passwords=False):
    """Flatten current Clients/Accounts/Devices/Credentials into one CSV (one row per device)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADER)

    devices = (
        StarlinkDevice.objects
        .filter(is_archived=False)
        .select_related('client', 'account', 'plan')
        .prefetch_related('account__credentials')
        .order_by('client__name', 'kit_number')
    )
    for d in devices:
        account = d.account
        credentials = ''
        if account and not account.is_archived:
            credentials = ' | '.join(
                f"{c.label}: {c.username}" + (f" / {c.password}" if include_passwords and c.password else "")
                for c in account.credentials.filter(is_archived=False)
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
            d.service_line_number or '',
            d.starlink_nickname or '',
            d.service_address or '',
            d.plan.name if d.plan else '',
            d.plan.price if d.plan and d.plan.price is not None else '',
            d.price_override if d.price_override is not None else '',
            d.price_override_reason or '',
            account.balance_due if account and account.balance_due is not None else '',
            account.starlink_synced_at.isoformat() if account and account.starlink_synced_at else '',
        ])
    return buffer.getvalue()
