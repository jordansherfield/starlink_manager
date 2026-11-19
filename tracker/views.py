import datetime
import decimal
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, F, Q, Prefetch
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import StarlinkAccount, Client, Credential, StarlinkDevice, AuditLog, ServicePlan
from .csv_import import run_import, export_csv

@login_required
def dashboard(request):
    query = request.GET.get('q', '').strip()
    
    # Base querysets - archived credentials/accounts/devices are excluded from
    # every normal listing (see archived_items() for the recovery view). Prefetch
    # with a filtered queryset (rather than filtering in Python) so this stays a
    # single query per relation instead of reintroducing per-client N+1 lookups.
    active_credentials_qs = Credential.objects.filter(is_archived=False)
    active_starlinks_qs = StarlinkDevice.objects.filter(is_archived=False).select_related('account').prefetch_related(
        Prefetch('account__credentials', queryset=active_credentials_qs)
    )
    active_accounts_qs = StarlinkAccount.objects.filter(is_archived=False).prefetch_related(
        Prefetch('credentials', queryset=active_credentials_qs)
    )

    clients = Client.objects.prefetch_related(
        Prefetch('starlinks', queryset=active_starlinks_qs),
        Prefetch('accounts', queryset=active_accounts_qs),
    )
    unassigned_starlinks = StarlinkDevice.objects.filter(client__isnull=True, is_archived=False).select_related('account')
    accounts = StarlinkAccount.objects.filter(is_archived=False).prefetch_related(
        Prefetch('credentials', queryset=active_credentials_qs),
        Prefetch('starlinks', queryset=StarlinkDevice.objects.filter(is_archived=False).select_related('client')),
    )
    
    if query:
        clients = clients.filter(
            Q(name__icontains=query) |
            Q(email__icontains=query) |
            Q(phone__icontains=query) |
            Q(address__icontains=query) |
            Q(company__icontains=query) |
            Q(starlinks__account__credentials__username__icontains=query) |
            Q(starlinks__account__credentials__label__icontains=query) |
            Q(starlinks__kit_number__icontains=query) |
            Q(starlinks__location_name__icontains=query) |
            Q(starlinks__account__account_number__icontains=query)
        ).distinct()
        
        unassigned_starlinks = unassigned_starlinks.filter(
            Q(kit_number__icontains=query) |
            Q(location_name__icontains=query) |
            Q(serial_number__icontains=query) |
            Q(starlink_id__icontains=query) |
            Q(account__account_number__icontains=query)
        )
        
        accounts = accounts.filter(
            Q(account_number__icontains=query) |
            Q(client__name__icontains=query) |
            Q(starlinks__client__name__icontains=query) |
            Q(starlinks__kit_number__icontains=query) |
            Q(credentials__username__icontains=query) |
            Q(credentials__label__icontains=query)
        ).distinct()
        
    # Serialize starlink devices for map visualization
    starlinks_data = []
    
    # Client-assigned starlinks
    for client in clients:
        for sl in client.starlinks.all():
            if sl.latitude is not None and sl.longitude is not None:
                starlinks_data.append({
                    'id': sl.id,
                    'kit_number': sl.kit_number,
                    'location_name': sl.location_name,
                    'client_name': client.name,
                    'lat': sl.latitude,
                    'lng': sl.longitude,
                    'status': sl.status,
                    'model': sl.get_model_display(),
                    'wifi_name': sl.wifi_name or 'None',
                    'serial_number': sl.serial_number or 'None',
                })
                
    # Unassigned starlinks
    for sl in unassigned_starlinks:
        if sl.latitude is not None and sl.longitude is not None:
            starlinks_data.append({
                'id': sl.id,
                'kit_number': sl.kit_number,
                'location_name': sl.location_name,
                'client_name': 'Unassigned',
                'lat': sl.latitude,
                'lng': sl.longitude,
                'status': sl.status,
                'model': sl.get_model_display(),
                'wifi_name': sl.wifi_name or 'None',
                'serial_number': sl.serial_number or 'None',
            })
                
    for acc in accounts:
        acc.margin = acc.client_invoice - acc.cost_to_us
        # Combine direct client and client linked via starlinks
        associated_clients = list(Client.objects.filter(starlinks__account=acc, starlinks__is_archived=False).distinct())
        if acc.client and acc.client not in associated_clients:
            associated_clients.append(acc.client)
        acc.associated_clients = associated_clients
        acc.clients_count = len(associated_clients)

    # Associate accounts and credentials directly onto clients for template use
    for client in clients:
        associated_accounts = []
        seen_accs = set()

        # Direct account links (assigned via the account's own "Link Client" field)
        for acc in client.accounts.all():
            if acc.id not in seen_accs:
                seen_accs.add(acc.id)
                associated_accounts.append(acc)

        # Account links via the client's hardware devices
        for sl in client.starlinks.all():
            sl.email_history = AuditLog.objects.filter(kit_number=sl.kit_number).order_by('-timestamp')
            if sl.account and not sl.account.is_archived and sl.account.id not in seen_accs:
                seen_accs.add(sl.account.id)
                associated_accounts.append(sl.account)
        client.associated_accounts = associated_accounts

    for sl in unassigned_starlinks:
        sl.email_history = AuditLog.objects.filter(kit_number=sl.kit_number).order_by('-timestamp')

    context = {
        'clients': clients,
        'unassigned_starlinks': unassigned_starlinks,
        'accounts': accounts,
        'query': query,
        'starlinks_json': starlinks_data,
        'all_clients_list': Client.objects.all(),
        'all_accounts_list': StarlinkAccount.objects.filter(is_archived=False),
    }
    return render(request, 'tracker/dashboard.html', context)

@login_required
def add_client(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        company = request.POST.get('company', 'Comnet')
        address = request.POST.get('address')
        email = request.POST.get('email')
        phone = request.POST.get('phone')
        notes = request.POST.get('notes')
        
        if name:
            client = Client.objects.create(
                name=name,
                company=company,
                address=address,
                email=email,
                phone=phone,
                notes=notes
            )
            messages.success(request, f"Client '{client.name}' created.")
        else:
            messages.error(request, "Client name is required.")
            
    return redirect('dashboard')

@login_required
def add_credential(request):
    if request.method == 'POST':
        account_id = request.POST.get('account_id')
        label = request.POST.get('label', 'Starlink Account')
        username = request.POST.get('username')
        password = request.POST.get('password')
        email_pass = request.POST.get('email_pass')
        notes = request.POST.get('notes')
        
        account = get_object_or_404(StarlinkAccount, id=account_id)
        if username and username.strip():
            Credential.objects.create(
                account=account,
                label=label,
                username=username.strip(),
                password=(password or '').strip(),
                email_pass=email_pass,
                notes=notes,
                bitwarden_item_id=(request.POST.get('bitwarden_item_id') or '').strip() or None,
            )
            messages.success(request, f"Credential added for account '{account.account_number}'.")
        else:
            messages.error(request, "A login username / email is required.")
            
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

KIT_RE = re.compile(r'^KIT[A-Z0-9]{6,}$')
ACCOUNT_RE = re.compile(r'^ACC-[A-Z0-9]+(-[A-Z0-9]+)+$')
STARLINK_ID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{8}-[0-9a-f]{8}$')
COORDS_RE = re.compile(r'(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)')


def clean_kit_number(raw):
    """Uppercase, no spaces. Returns (kit, error)."""
    kit = re.sub(r'\s+', '', raw or '').upper()
    if not kit:
        return kit, "Kit number is required."
    if not KIT_RE.match(kit):
        return kit, f"'{kit}' doesn't look like a Starlink kit number (e.g. KIT401375658CM4)."
    return kit, None


def clean_starlink_id(raw):
    """Starlink IDs look like 01000000-00000000-007d0247 (sometimes shown with a 'ut' prefix)."""
    value = (raw or '').strip().lower()
    if value.startswith('ut'):
        value = value[2:]
    if not value:
        return None, None
    if not STARLINK_ID_RE.match(value):
        return value, "Starlink ID should look like 01000000-00000000-007d0247."
    return value, None


def parse_coordinates(lat_raw, lng_raw):
    """Accepts separate numbers, 'lat, long' in the first box, or a Google Maps link.
    Returns (lat, lng, error)."""
    lat_raw, lng_raw = (lat_raw or '').strip(), (lng_raw or '').strip()
    if not lat_raw and not lng_raw:
        return None, None, None
    if lat_raw and (not lng_raw or 'http' in lat_raw or ',' in lat_raw):
        m = COORDS_RE.search(lat_raw.replace('%2C', ','))
        if not m:
            return None, None, "Couldn't find coordinates there - use e.g. -20.0986, 28.4572 or a Google Maps link."
        lat_raw, lng_raw = m.group(1), m.group(2)
    try:
        lat, lng = float(lat_raw), float(lng_raw)
    except ValueError:
        return None, None, "Coordinates must be numbers, e.g. -20.0986, 28.4572."
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None, "Those coordinates are out of range."
    return lat, lng, None


def detect_model(kit, serial):
    """Dish type from its serial / kit number: M1… / KIT4M… = Mini, 2DUN/2DWC… / KIT30… = Gen 2,
    4PBA… / KIT40… = Gen 3. Returns a MODEL_CHOICES value or None."""
    s_, k_ = (serial or '').upper(), (kit or '').upper()
    if s_.startswith('M1') or k_.startswith('KIT4M'):
        return 'Mini'
    if s_.startswith(('2DUN', '2DWC')) or k_.startswith('KIT30'):
        return 'gen2'
    if s_.startswith('4PBA') or k_.startswith('KIT40'):
        return 'Gen3'
    return None


def _account_owner_names(accounts):
    """Map account id -> client names, from the account's own client plus any kits on it."""
    names = {acc.id: set() for acc in accounts}
    for acc in accounts:
        if acc.client:
            names[acc.id].add(acc.client.name)
    rows = (StarlinkDevice.objects
            .filter(account__in=accounts, client__isnull=False, is_archived=False)
            .values_list('account_id', 'client__name'))
    for account_id, client_name in rows:
        names[account_id].add(client_name)
    return names


@login_required
def kit_form_options(request):
    """Clients and accounts for the Add Kit form's searchable pickers."""
    clients = list(Client.objects.values('id', 'name', 'company'))
    accounts = list(StarlinkAccount.objects.filter(is_archived=False).select_related('client'))
    owners = _account_owner_names(accounts)
    client_ids_by_account = {}
    for account_id, client_id in (StarlinkDevice.objects
                                  .filter(account__in=accounts, client__isnull=False, is_archived=False)
                                  .values_list('account_id', 'client_id')):
        client_ids_by_account.setdefault(account_id, set()).add(client_id)
    return JsonResponse({
        'clients': clients,
        'plans': [{'id': p.id, 'name': p.name, 'price': str(p.price) if p.price is not None else None,
                   'currency': p.currency} for p in ServicePlan.objects.filter(is_active=True)],
        'companies': [value for value, _ in Client.COMPANY_CHOICES],
        'accounts': [
            {
                'id': acc.id,
                'number': acc.account_number,
                'owners': sorted(owners[acc.id]),
                'client_ids': sorted(client_ids_by_account.get(acc.id, set()) | ({acc.client_id} if acc.client_id else set())),
            }
            for acc in accounts
        ],
    })


@login_required
def kit_check(request):
    """Live duplicate check for the Add Kit form's kit number field."""
    kit_number = request.GET.get('kit_number', '').strip()
    device = StarlinkDevice.objects.filter(kit_number__iexact=kit_number).select_related('client').first() if kit_number else None
    if not device:
        return JsonResponse({'exists': False})
    return JsonResponse({
        'exists': True,
        'archived': device.is_archived,
        'client': device.client.name if device.client else 'Unassigned',
        'client_id': device.client_id,
    })


@login_required
@require_POST
def add_kit(request):
    """
    Create a kit, optionally creating its client and billing account in the same step.
    Responds with JSON so the form can stay open and show errors next to each field.
    """
    post = request.POST
    errors = {}

    kit_number, kit_error = clean_kit_number(post.get('kit_number'))
    if kit_error:
        errors['kit_number'] = kit_error
    elif StarlinkDevice.objects.filter(kit_number__iexact=kit_number).exists():
        errors['kit_number'] = f"Kit '{kit_number}' already exists."

    client_mode = post.get('client_mode', 'none')
    client = None
    if client_mode == 'existing':
        client = Client.objects.filter(pk=post.get('client_id') or None).first()
        if not client:
            errors['client'] = "Pick a client from the list, or choose Unassigned."
    elif client_mode == 'new':
        new_client_name = ' '.join(post.get('new_client_name', '').split())
        if not new_client_name:
            errors['client'] = "Enter the new client's name."
        elif post.get('new_client_company') not in dict(Client.COMPANY_CHOICES):
            errors['client'] = "Choose which company the new client belongs to."
        else:
            existing = _find_client_by_name(new_client_name)
            if existing:
                errors['client'] = f"A client called '{existing.name}' already exists - pick it under Existing."

    account_mode = post.get('account_mode', 'none')
    account = None
    if account_mode == 'existing':
        account = StarlinkAccount.objects.filter(pk=post.get('account_id') or None, is_archived=False).first()
        if not account:
            errors['account'] = "Pick an account from the list, or choose No account."
    elif account_mode == 'new':
        new_number = re.sub(r'\s+', '', post.get('new_account_number', '')).upper()
        if not new_number:
            errors['account'] = "Enter the new account number."
        elif not ACCOUNT_RE.match(new_number):
            errors['account'] = f"'{new_number}' doesn't look like a Starlink account number (e.g. ACC-1234567-12345-12)."
        elif StarlinkAccount.objects.filter(account_number__iexact=new_number).exists():
            errors['account'] = f"Account '{new_number}' already exists - pick it from the list instead."
        elif not post.get('new_account_username', '').strip():
            errors['account'] = "A new account needs its Starlink login (email). The password can stay in Bitwarden."

    model = post.get('model', 'Gen3')
    if model not in dict(StarlinkDevice.MODEL_CHOICES):
        errors['model'] = "Pick a model."
    status = post.get('status', 'To install')
    if status not in dict(StarlinkDevice.STATUS_CHOICES):
        errors['status'] = "Pick a status."

    latitude, longitude, coord_error = parse_coordinates(post.get('latitude'), post.get('longitude'))
    if coord_error:
        errors['coordinates'] = coord_error

    starlink_id, sid_error = clean_starlink_id(post.get('starlink_id'))
    if sid_error:
        errors['starlink_id'] = sid_error

    plan = None
    if post.get('plan_id'):
        plan = ServicePlan.objects.filter(pk=post.get('plan_id')).first()
        if not plan:
            errors['plan'] = "Pick a plan from the list."
    price_override = _parse_money(post.get('price_override'))
    if price_override is False:
        errors['plan'] = "Price override must be a number (or left blank)."

    if errors:
        return JsonResponse({'ok': False, 'errors': errors}, status=400)

    with transaction.atomic():
        if client_mode == 'new':
            client = Client.objects.create(name=new_client_name, company=post['new_client_company'])
        if account_mode == 'new':
            account = StarlinkAccount.objects.create(
                client=client,
                account_number=new_number,
                account_due_by=post.get('new_account_due_by') or None,
            )
            Credential.objects.create(
                account=account,
                label='Starlink Login',
                username=post['new_account_username'].strip(),
                password=post.get('new_account_password', '').strip(),
                bitwarden_item_id=post.get('new_account_bitwarden', '').strip() or None,
            )
        location_name = post.get('location_name', '').strip() or (client.name if client else kit_number)
        StarlinkDevice.objects.create(
            client=client,
            account=account,
            kit_number=kit_number,
            location_name=location_name,
            model=model,
            status=status,
            starlink_id=starlink_id,
            serial_number=post.get('serial_number', '').strip().upper() or None,
            plan=plan,
            price_override=price_override,
            wifi_name=post.get('wifi_name', '').strip() or None,
            wifi_password=post.get('wifi_password', '').strip() or None,
            latitude=latitude,
            longitude=longitude,
            notes=post.get('notes', '').strip() or None,
        )

    message = f"Kit '{kit_number}' added for {client.name if client else 'Unassigned'}"
    if account:
        message += f" on account {account.account_number}"
    message += "."
    detected = detect_model(kit_number, post.get('serial_number'))
    if detected and detected != model:
        message += (f" Note: the kit/serial number looks like a {dict(StarlinkDevice.MODEL_CHOICES)[detected]}, "
                    f"but it was saved as {dict(StarlinkDevice.MODEL_CHOICES)[model]}.")
    messages.success(request, message)
    return JsonResponse({'ok': True})

@login_required
def add_account(request):
    if request.method == 'POST':
        account_number = request.POST.get('account_number')
        last_payment_date = request.POST.get('last_payment_date')
        account_due_by = request.POST.get('account_due_by')
        cost_to_us = request.POST.get('cost_to_us', '0.00')
        client_invoice = request.POST.get('client_invoice', '0.00')
        
        # Section toggles
        add_credentials = request.POST.get('add_credentials') == 'yes'
        add_starlink = request.POST.get('add_starlink') == 'yes'
        
        if not account_number:
            messages.error(request, "Account number is required.")
            return redirect('dashboard')
            
        # At least one login (username/email) is required; passwords live in Bitwarden.
        if not add_credentials:
            messages.error(request, "At least one login is required to create a billing account.")
            return redirect('dashboard')

        usernames = request.POST.getlist('cred_username')
        if not any(u.strip() for u in usernames):
            messages.error(request, "At least one login username / email is required.")
            return redirect('dashboard')
            
        # Check uniqueness of account number beforehand
        if StarlinkAccount.objects.filter(account_number=account_number).exists():
            messages.error(request, f"Error: Starlink Account with number '{account_number}' already exists.")
            return redirect('dashboard')
            
        try:
            with transaction.atomic():
                last_pay = last_payment_date if last_payment_date else None
                due_by = account_due_by if account_due_by else None
                
                # Make sure empty string dates map to None
                if last_pay == "": last_pay = None
                if due_by == "": due_by = None

                # Process client selection/creation at the top level
                client_option = request.POST.get('client_option')
                client = None
                
                if client_option == 'new':
                    new_client_name = request.POST.get('new_client_name')
                    new_client_company = request.POST.get('new_client_company', 'Comnet')
                    new_client_email = request.POST.get('new_client_email')
                    new_client_phone = request.POST.get('new_client_phone')
                    new_client_address = request.POST.get('new_client_address')
                    new_client_notes = request.POST.get('new_client_notes')
                    
                    if not new_client_name:
                        raise ValueError("Client name is required when creating a new client.")
                        
                    client = Client.objects.create(
                        name=new_client_name,
                        company=new_client_company if new_client_company else 'Comnet',
                        email=new_client_email,
                        phone=new_client_phone,
                        address=new_client_address,
                        notes=new_client_notes
                    )
                elif client_option:
                    client = get_object_or_404(Client, id=client_option)
                
                account = StarlinkAccount.objects.create(
                    client=client,
                    account_number=account_number,
                    last_payment_date=last_pay,
                    account_due_by=due_by,
                    cost_to_us=cost_to_us if cost_to_us else 0.00,
                    client_invoice=client_invoice if client_invoice else 0.00
                )
                success_message = f"Starlink Account '{account_number}' created successfully."
                if client:
                    success_message += f" Linked to client '{client.name}'."
                
                # Handle Multiple Credentials Creation
                if add_credentials:
                    labels = request.POST.getlist('cred_label')
                    usernames = request.POST.getlist('cred_username')
                    passwords = request.POST.getlist('cred_password')
                    email_passes = request.POST.getlist('cred_email_pass')
                    notes = request.POST.getlist('cred_notes')
                    
                    added_count = 0
                    for i in range(len(usernames)):
                        u = usernames[i].strip() if i < len(usernames) else ""
                        p = passwords[i].strip() if i < len(passwords) else ""
                        if u:
                            l = labels[i].strip() if i < len(labels) else "Starlink Account"
                            ep = email_passes[i].strip() if i < len(email_passes) else ""
                            n = notes[i].strip() if i < len(notes) else ""
                            
                            Credential.objects.create(
                                account=account,
                                label=l if l else 'Starlink Account',
                                username=u,
                                password=p,
                                email_pass=ep,
                                notes=n
                            )
                            added_count += 1
                    
                    if added_count > 0:
                        success_message += f" {added_count} Credentials added."
                
                # Handle Starlink Kit Creation (linked to the same client)
                if add_starlink:
                    kit_number = request.POST.get('sl_kit_number')
                    location_name = request.POST.get('sl_location_name')
                    model = request.POST.get('sl_model', 'Gen3')
                    starlink_id = request.POST.get('sl_starlink_id')
                    serial_number = request.POST.get('sl_serial_number')
                    wifi_name = request.POST.get('sl_wifi_name')
                    wifi_password = request.POST.get('sl_wifi_password')
                    latitude = request.POST.get('sl_latitude')
                    longitude = request.POST.get('sl_longitude')
                    status = request.POST.get('sl_status', 'To install')
                    sl_notes = request.POST.get('sl_notes')
                    
                    kit_number, kit_error = clean_kit_number(kit_number)
                    if kit_error:
                        raise ValueError(kit_error)
                    if not (location_name or '').strip():
                        raise ValueError("Location name is required for the Starlink device.")
                    if StarlinkDevice.objects.filter(kit_number__iexact=kit_number).exists():
                        raise ValueError(f"Starlink Kit with number '{kit_number}' already exists.")
                    starlink_id, sid_error = clean_starlink_id(starlink_id)
                    if sid_error:
                        raise ValueError(sid_error)
                    lat_val, lng_val, coord_error = parse_coordinates(latitude, longitude)
                    if coord_error:
                        raise ValueError(coord_error)
                    if model not in dict(StarlinkDevice.MODEL_CHOICES):
                        model = detect_model(kit_number, serial_number) or 'Gen3'
                    
                    StarlinkDevice.objects.create(
                        client=client,
                        account=account,
                        kit_number=kit_number,
                        location_name=location_name,
                        model=model,
                        starlink_id=starlink_id,
                        serial_number=serial_number,
                        wifi_name=wifi_name,
                        wifi_password=wifi_password,
                        latitude=lat_val,
                        longitude=lng_val,
                        status=status,
                        notes=sl_notes
                    )
                    success_message += f" Starlink Terminal '{kit_number}' mapped."
                    
                messages.success(request, success_message)
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
            
    return redirect('dashboard')

@login_required
def transfer_starlink(request, pk):
    if request.method == 'POST':
        starlink = get_object_or_404(StarlinkDevice, pk=pk)
        client_id = request.POST.get('client_id')
        account_id = request.POST.get('account_id')
        status = request.POST.get('status')
        
        # Owner update
        if client_id == "unassigned" or not client_id or client_id.strip() == "":
            starlink.client = None
            owner = "Unassigned"
        else:
            client = get_object_or_404(Client, id=client_id)
            starlink.client = client
            owner = client.name
            
        # Account link update
        if account_id == "unlinked" or not account_id or account_id.strip() == "":
            starlink.account = None
        else:
            account = get_object_or_404(StarlinkAccount, id=account_id)
            starlink.account = account
            
        if status:
            starlink.status = status
            
        starlink.save()
        message = f"Starlink Kit '{starlink.kit_number}' updated successfully."
        if starlink.plan or starlink.price_override is not None:
            message += " Check its plan, price override and location for the new owner."
        messages.success(request, message)
        
    return redirect('dashboard')

@login_required
def delete_client(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        name = client.name
        client.delete()
        messages.success(request, f"Client '{name}' deleted.")
    return redirect('dashboard')

@login_required
def delete_credential(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    if request.method == 'POST':
        credential.delete()
        messages.success(request, f"Credential deleted.")
    return redirect('dashboard')

@login_required
def delete_starlink(request, pk):
    starlink = get_object_or_404(StarlinkDevice, pk=pk)
    if request.method == 'POST':
        kit_number = starlink.kit_number
        starlink.delete()
        messages.success(request, f"Starlink Kit '{kit_number}' deleted.")
    return redirect('dashboard')

@login_required
def delete_account(request, pk):
    account = get_object_or_404(StarlinkAccount, pk=pk)
    if request.method == 'POST':
        num = account.account_number
        account.delete()
        messages.success(request, f"Account '{num}' deleted.")
    return redirect('dashboard')

@login_required
def archive_credential(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "A reason is required to archive a credential.")
        else:
            credential.is_archived = True
            credential.archived_reason = reason
            credential.archived_at = timezone.now()
            credential.save()
            messages.success(request, f"Credential '{credential.label}' archived.")
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def unarchive_credential(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    if request.method == 'POST':
        credential.is_archived = False
        credential.archived_reason = None
        credential.archived_at = None
        credential.save()
        messages.success(request, f"Credential '{credential.label}' restored from archive.")
    return redirect(request.META.get('HTTP_REFERER', 'archived_items'))

@login_required
def archive_starlink(request, pk):
    starlink = get_object_or_404(StarlinkDevice, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "A reason is required to archive a Starlink kit.")
        else:
            starlink.is_archived = True
            starlink.archived_reason = reason
            starlink.archived_at = timezone.now()
            starlink.save()
            messages.success(request, f"Starlink Kit '{starlink.kit_number}' archived.")
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def unarchive_starlink(request, pk):
    starlink = get_object_or_404(StarlinkDevice, pk=pk)
    if request.method == 'POST':
        starlink.is_archived = False
        starlink.archived_reason = None
        starlink.archived_at = None
        starlink.save()
        messages.success(request, f"Starlink Kit '{starlink.kit_number}' restored from archive.")
    return redirect(request.META.get('HTTP_REFERER', 'archived_items'))

@login_required
def archive_account(request, pk):
    account = get_object_or_404(StarlinkAccount, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "A reason is required to archive a billing account.")
        else:
            account.is_archived = True
            account.archived_reason = reason
            account.archived_at = timezone.now()
            account.save()
            messages.success(request, f"Account '{account.account_number}' archived.")
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def unarchive_account(request, pk):
    account = get_object_or_404(StarlinkAccount, pk=pk)
    if request.method == 'POST':
        account.is_archived = False
        account.archived_reason = None
        account.archived_at = None
        account.save()
        messages.success(request, f"Account '{account.account_number}' restored from archive.")
    return redirect(request.META.get('HTTP_REFERER', 'archived_items'))

@login_required
def archived_items(request):
    context = {
        'archived_credentials': Credential.objects.filter(is_archived=True).select_related('account').order_by('-archived_at'),
        'archived_accounts': StarlinkAccount.objects.filter(is_archived=True).order_by('-archived_at'),
        'archived_starlinks': StarlinkDevice.objects.filter(is_archived=True).select_related('client', 'account').order_by('-archived_at'),
    }
    return render(request, 'tracker/archived_items.html', context)

@login_required
def client_detail(request, pk):
    active_credentials_qs = Credential.objects.filter(is_archived=False)
    active_starlinks_qs = StarlinkDevice.objects.filter(is_archived=False).select_related('account').prefetch_related(
        Prefetch('account__credentials', queryset=active_credentials_qs)
    )
    client = get_object_or_404(
        Client.objects.prefetch_related(Prefetch('starlinks', queryset=active_starlinks_qs)),
        pk=pk
    )

    # Serialize starlink devices for map visualization
    starlinks_data = []
    for sl in client.starlinks.all():
        if sl.latitude is not None and sl.longitude is not None:
            starlinks_data.append({
                'id': sl.id,
                'kit_number': sl.kit_number,
                'location_name': sl.location_name,
                'client_name': client.name,
                'lat': sl.latitude,
                'lng': sl.longitude,
                'status': sl.status,
                'model': sl.get_model_display(),
                'wifi_name': sl.wifi_name or 'None',
                'serial_number': sl.serial_number or 'None',
            })
            
    # Unique accounts linked directly and via client's devices
    associated_accounts = []
    seen_accs = set()
    
    # 1. Direct account links
    for acc in client.accounts.filter(is_archived=False):
        if acc.id not in seen_accs:
            seen_accs.add(acc.id)
            acc.margin = acc.client_invoice - acc.cost_to_us
            associated_accounts.append(acc)
            
    # 2. Account links via devices
    for sl in client.starlinks.all():
        sl.email_history = AuditLog.objects.filter(kit_number=sl.kit_number).order_by('-timestamp')
        if sl.account and not sl.account.is_archived and sl.account.id not in seen_accs:
            seen_accs.add(sl.account.id)
            sl.account.margin = sl.account.client_invoice - sl.account.cost_to_us
            associated_accounts.append(sl.account)
            
    for acc in associated_accounts:
        acc.active_credentials = acc.credentials.filter(is_archived=False)
    client.associated_accounts = associated_accounts

    context = {
        'client': client,
        'starlinks_json': starlinks_data,
        'all_clients_list': Client.objects.all(),
        'all_accounts_list': StarlinkAccount.objects.filter(is_archived=False),
        'all_plans': ServicePlan.objects.all(),
    }
    return render(request, 'tracker/client_detail.html', context)

@login_required
def update_client(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        client.name = request.POST.get('name')
        client.company = request.POST.get('company', 'Comnet')
        client.address = request.POST.get('address')
        client.email = request.POST.get('email')
        client.phone = request.POST.get('phone')
        client.notes = request.POST.get('notes')
        client.save()
        messages.success(request, f"Client profile '{client.name}' updated successfully.")
        
    return redirect('client_detail', pk=pk)

@login_required
def update_credential(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    if request.method == 'POST':
        credential.label = request.POST.get('label', 'Starlink Account')
        credential.username = request.POST.get('username')
        credential.password = (request.POST.get('password') or '').strip()
        credential.email_pass = request.POST.get('email_pass')
        if 'bitwarden_item_id' in request.POST:
            credential.bitwarden_item_id = request.POST.get('bitwarden_item_id', '').strip() or None
        credential.notes = request.POST.get('notes')
        
        new_account_id = request.POST.get('account_id')
        if new_account_id:
            target_account = get_object_or_404(StarlinkAccount, id=new_account_id)
            credential.account = target_account
            
        credential.save()
        messages.success(request, f"Credential '{credential.label}' updated successfully.")
        
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def update_starlink(request, pk):
    starlink = get_object_or_404(StarlinkDevice, pk=pk)
    if request.method == 'POST':
        starlink.kit_number = request.POST.get('kit_number')
        starlink.location_name = request.POST.get('location_name')
        starlink.model = request.POST.get('model', 'Gen3')
        starlink.starlink_id = request.POST.get('starlink_id')
        starlink.serial_number = request.POST.get('serial_number')
        starlink.wifi_name = request.POST.get('wifi_name')
        starlink.wifi_password = request.POST.get('wifi_password')
        
        latitude = request.POST.get('latitude')
        longitude = request.POST.get('longitude')
        starlink.latitude = float(latitude) if latitude else None
        starlink.longitude = float(longitude) if longitude else None
        
        starlink.status = request.POST.get('status', 'To install')
        starlink.notes = request.POST.get('notes')
        
        account_id = request.POST.get('account_id')
        if account_id == "unlinked" or not account_id or account_id.strip() == "":
            starlink.account = None
        else:
            starlink.account = get_object_or_404(StarlinkAccount, id=account_id)
            
        client_id = request.POST.get('client_id')
        if client_id == "unassigned" or not client_id or client_id.strip() == "":
            starlink.client = None
        else:
            starlink.client = get_object_or_404(Client, id=client_id)

        if 'plan_id' in request.POST:
            plan_id = request.POST.get('plan_id', '').strip()
            starlink.plan = get_object_or_404(ServicePlan, id=plan_id) if plan_id else None
        if 'price_override' in request.POST:
            override = _parse_money(request.POST.get('price_override'))
            if override is False:
                messages.error(request, "Price override must be a number - it was not changed.")
            else:
                starlink.price_override = override
        if 'price_override_reason' in request.POST:
            starlink.price_override_reason = request.POST.get('price_override_reason', '').strip() or None
        for field in ('service_line_number', 'starlink_nickname', 'service_address', 'installed_by'):
            if field in request.POST:
                setattr(starlink, field, request.POST.get(field, '').strip() or None)
        if 'installed_on' in request.POST:
            starlink.installed_on = request.POST.get('installed_on') or None

        starlink.save()
        messages.success(request, f"Starlink Kit '{starlink.kit_number}' updated successfully.")
        
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def update_account(request, pk):
    account = get_object_or_404(StarlinkAccount, pk=pk)
    if request.method == 'POST':
        cost_to_us = request.POST.get('cost_to_us')
        client_invoice = request.POST.get('client_invoice')
        last_payment_date = request.POST.get('last_payment_date')
        account_due_by = request.POST.get('account_due_by')
        
        if cost_to_us: account.cost_to_us = cost_to_us
        if client_invoice: account.client_invoice = client_invoice
        
        if last_payment_date == "":
            account.last_payment_date = None
        elif last_payment_date:
            account.last_payment_date = last_payment_date
            
        if account_due_by == "":
            account.account_due_by = None
        elif account_due_by:
            account.account_due_by = account_due_by
            
        account.save()
        messages.success(request, f"Billing account details updated successfully.")
        
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

def _parse_money(raw):
    """'' -> None, '59.99' -> Decimal, anything else -> False."""
    raw = (raw or '').strip().replace(',', '').lstrip('$')
    if not raw:
        return None
    try:
        value = decimal.Decimal(raw).quantize(decimal.Decimal('0.01'))
    except decimal.InvalidOperation:
        return False
    return value if value >= 0 else False


def _normalized(name):
    return ' '.join((name or '').split()).casefold()


def _find_client_by_name(name):
    key = _normalized(name)
    for client in Client.objects.filter(name__iexact=' '.join(name.split())):
        if _normalized(client.name) == key:
            return client
    return next((c for c in Client.objects.all() if _normalized(c.name) == key), None)


def _suggested_client(device, active_devices):
    """The client a new-from-Starlink unit most likely belongs to: its account's client, or the client
    of another unit on the same account."""
    if not device.account:
        return None
    if device.account.client:
        return device.account.client
    other = (active_devices.filter(account=device.account, client__isnull=False)
             .exclude(pk=device.pk).select_related('client').first())
    return other.client if other else None


@login_required
def starlink_inbox(request):
    active = StarlinkDevice.objects.filter(is_archived=False).select_related('client', 'account', 'plan')
    new_units = list(active.filter(client__isnull=True)
                     .exclude(service_line_number__isnull=True).exclude(service_line_number='')
                     .order_by('account__account_number', 'kit_number'))
    for d in new_units:
        d.suggested_client = _suggested_client(d, active)

    mismatches = list(active.filter(status='Running', starlink_service_active=False)) + list(
        active.filter(starlink_service_active=True, status__in=['To install', 'Deactivated'], client__isnull=False))
    for d in mismatches:
        d.issue = ("Starlink service is inactive, but the unit is marked Running"
                   if d.status == 'Running' else
                   f"Starlink service is active, but the unit is marked '{d.status}'")

    install_queue = active.filter(status='To install').order_by(F('status_changed_at').asc(nulls_first=True))

    accounts = (StarlinkAccount.objects.filter(is_archived=False)
                .filter(Q(balance_due__gt=0) | Q(starlink_synced_at__isnull=False))
                .select_related('client').order_by('account_number'))
    owing, bill_mismatch = [], []
    for acc in accounts:
        if acc.balance_due:
            owing.append(acc)
        if acc.starlink_synced_at:
            acc.expected = acc.plan_cost
            if acc.expected is not None and acc.expected != acc.cost_to_us:
                bill_mismatch.append(acc)

    return render(request, 'tracker/starlink_inbox.html', {
        'new_units': new_units,
        'mismatches': mismatches,
        'install_queue': install_queue,
        'owing': owing,
        'bill_mismatch': bill_mismatch,
        'billing_count': len(owing) + len(bill_mismatch),
        'clients': Client.objects.all(),
        'company_choices': Client.COMPANY_CHOICES,
        'status_choices': StarlinkDevice.STATUS_CHOICES,
    })


@login_required
@require_POST
def assign_starlink(request, pk):
    """Assign a unit (usually new from Starlink) to an existing or new client in one step."""
    device = get_object_or_404(StarlinkDevice, pk=pk)
    new_name = ' '.join((request.POST.get('new_client_name') or '').split())
    with transaction.atomic():
        if new_name:
            company = request.POST.get('new_client_company', '')
            if company not in dict(Client.COMPANY_CHOICES):
                messages.error(request, "Choose the company for the new client.")
                return redirect('starlink_inbox')
            existing = _find_client_by_name(new_name)
            if existing:
                messages.error(request, f"A client called '{existing.name}' already exists - pick it from the list.")
                return redirect('starlink_inbox')
            client = Client.objects.create(name=new_name, company=company)
        else:
            client = Client.objects.filter(pk=request.POST.get('client_id') or None).first()
            if not client:
                messages.error(request, "Pick a client, or enter a new client's name.")
                return redirect('starlink_inbox')
        device.client = client
        location = (request.POST.get('location_name') or '').strip()
        if location:
            device.location_name = location
        elif device.location_name in ('', device.kit_number):
            device.location_name = client.name
        status = request.POST.get('status')
        if status in dict(StarlinkDevice.STATUS_CHOICES):
            device.status = status
        device.save()
        if device.account and device.account.client_id is None:
            device.account.client = client
            device.account.save()
    messages.success(request, f"Kit {device.kit_number} assigned to {client.name}.")
    return redirect('starlink_inbox')


@login_required
def plans_page(request):
    plans = ServicePlan.objects.annotate(
        services=Count('devices', filter=Q(devices__is_archived=False)),
        overrides=Count('devices', filter=Q(devices__is_archived=False, devices__price_override__isnull=False)),
    )
    overridden = (StarlinkDevice.objects.filter(is_archived=False, price_override__isnull=False)
                  .select_related('plan', 'client', 'account').order_by('client__name', 'kit_number'))
    for d in overridden:
        d.difference = (d.price_override - d.plan.price) if d.plan and d.plan.price is not None else None
    no_plan_count = StarlinkDevice.objects.filter(is_archived=False, plan__isnull=True).count()
    return render(request, 'tracker/plans.html', {
        'plans': plans, 'overridden': overridden, 'no_plan_count': no_plan_count,
    })


def _plan_from_post(request, plan):
    name = ' '.join((request.POST.get('name') or '').split())
    if not name:
        messages.error(request, "A plan needs a name.")
        return False
    clash = ServicePlan.objects.filter(name__iexact=name).exclude(pk=plan.pk).first()
    if clash:
        messages.error(request, f"There is already a plan called '{clash.name}'.")
        return False
    price = _parse_money(request.POST.get('price'))
    if price is False:
        messages.error(request, "Price must be a number (or left blank).")
        return False
    plan.name = name
    plan.price = price
    plan.currency = (request.POST.get('currency') or 'USD').strip().upper()[:3] or 'USD'
    plan.notes = (request.POST.get('notes') or '').strip() or None
    plan.is_active = request.POST.get('is_active') == 'on'
    return True


@login_required
@require_POST
def add_plan(request):
    plan = ServicePlan(is_active=True)
    if _plan_from_post(request, plan):
        plan.save()
        messages.success(request, f"Plan '{plan.name}' added.")
    return redirect('plans_page')


@login_required
@require_POST
def update_plan(request, pk):
    plan = get_object_or_404(ServicePlan, pk=pk)
    if _plan_from_post(request, plan):
        plan.save()
        messages.success(request, f"Plan '{plan.name}' updated.")
    return redirect('plans_page')


@login_required
@require_POST
def delete_plan(request, pk):
    plan = get_object_or_404(ServicePlan, pk=pk)
    in_use = plan.devices.count()
    if in_use:
        messages.error(request, f"'{plan.name}' is used by {in_use} service(s). Move them to another plan "
                                f"or untick Active to hide it instead.")
    else:
        name = plan.name
        plan.delete()
        messages.success(request, f"Plan '{name}' deleted.")
    return redirect('plans_page')


def add_one_month(orig_date):
    # Add exactly 1 month to orig_date
    year = orig_date.year
    month = orig_date.month + 1
    if month > 12:
        month = 1
        year += 1
    day = orig_date.day
    while True:
        try:
            return datetime.date(year, month, day)
        except ValueError:
            day -= 1

@login_required
def mark_account_paid(request, pk):
    account = get_object_or_404(StarlinkAccount, pk=pk)
    if request.method == 'POST':
        if account.account_due_by:
            initial_due = account.account_due_by
            account.last_payment_date = datetime.date.today()
            account.account_due_by = add_one_month(initial_due)
            account.save()
            messages.success(request, f"Billing account '{account.account_number}' payment processed. Due date advanced from {initial_due} to {account.account_due_by}.")
        else:
            account.last_payment_date = datetime.date.today()
            account.account_due_by = add_one_month(datetime.date.today())
            account.save()
            messages.success(request, f"Billing account '{account.account_number}' payment processed. Created new due date: {account.account_due_by}.")
            
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def mark_accounts_paid_for_day(request):
    if request.method == 'POST':
        due_date_str = request.POST.get('due_date')
        try:
            due_date = datetime.datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            messages.error(request, "Invalid due date supplied.")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        accounts = StarlinkAccount.objects.filter(account_due_by=due_date, is_archived=False)
        count = accounts.count()
        for account in accounts:
            account.last_payment_date = datetime.date.today()
            account.account_due_by = add_one_month(due_date)
            account.save()

        if count:
            messages.success(request, f"Marked {count} account(s) due on {due_date.strftime('%A %d %B %Y')} as paid.")
        else:
            messages.info(request, "No accounts found due on that date.")

    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def stats_dashboard(request):
    total_starlinks = StarlinkDevice.objects.filter(is_archived=False).count()

    # Status counts dictionary
    status_counts = {}
    for choice in StarlinkDevice.STATUS_CHOICES:
        status_key = choice[0]
        norm_key = status_key.replace(' ', '_')
        status_counts[norm_key] = StarlinkDevice.objects.filter(status=status_key, is_archived=False).count()

    # Financial summaries
    total_cost = 0.00
    total_income = 0.00
    accounts = StarlinkAccount.objects.filter(is_archived=False)
    for acc in accounts:
        total_cost += float(acc.cost_to_us)
        total_income += float(acc.client_invoice)
    total_profit = total_income - total_cost

    # Due accounts next 7 days
    today = datetime.date.today()
    seven_days_later = today + datetime.timedelta(days=7)
    upcoming_due_accounts = StarlinkAccount.objects.filter(
        account_due_by__lte=seven_days_later, is_archived=False
    ).order_by('account_due_by')

    for acc in upcoming_due_accounts:
        acc.margin = acc.client_invoice - acc.cost_to_us
        acc.associated_clients = Client.objects.filter(starlinks__account=acc, starlinks__is_archived=False).distinct()
        
    context = {
        'total_starlinks': total_starlinks,
        'status_counts': status_counts,
        'total_cost': total_cost,
        'total_income': total_income,
        'total_profit': total_profit,
        'upcoming_due_accounts': upcoming_due_accounts,
    }
    return render(request, 'tracker/stats_dashboard.html', context)


@login_required
def audit_logs_page(request):
    from django.core.paginator import Paginator
    query = request.GET.get('q', '').strip()
    action_filter = request.GET.get('action', '').strip()
    model_filter = request.GET.get('model', '').strip()

    logs = AuditLog.objects.all().select_related('user')

    if query:
        logs = logs.filter(
            Q(model_name__icontains=query) |
            Q(object_repr__icontains=query) |
            Q(kit_number__icontains=query) |
            Q(user__username__icontains=query) |
            Q(changes__icontains=query)
        )

    if action_filter:
        logs = logs.filter(action=action_filter)

    if model_filter:
        logs = logs.filter(model_name=model_filter)

    # Paginate
    paginator = Paginator(logs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    actions_list = ['CREATE', 'UPDATE', 'DELETE']
    models_list = ['Client', 'StarlinkAccount', 'Credential', 'StarlinkDevice']

    context = {
        'page_obj': page_obj,
        'query': query,
        'action_filter': action_filter,
        'model_filter': model_filter,
        'actions_list': actions_list,
        'models_list': models_list,
    }
    return render(request, 'tracker/audit_logs.html', context)

SESSION_IMPORT_TEXT = 'pending_import_csv_text'
SESSION_IMPORT_FILENAME = 'pending_import_filename'


@login_required
def import_export_page(request):
    pending_filename = request.session.get(SESSION_IMPORT_FILENAME)
    preview = None

    if pending_filename:
        csv_text = request.session.get(SESSION_IMPORT_TEXT, '')
        try:
            preview = run_import(csv_text, commit=False)
        except ValueError as e:
            messages.error(request, f"Could not re-parse pending import: {e}")
            request.session.pop(SESSION_IMPORT_TEXT, None)
            request.session.pop(SESSION_IMPORT_FILENAME, None)
            pending_filename = None

    context = {
        'pending_filename': pending_filename,
        'preview': preview,
    }
    return render(request, 'tracker/import_export.html', context)


@login_required
@require_POST
def import_csv_preview(request):
    upload = request.FILES.get('csv_file')
    if not upload:
        messages.error(request, "Please choose a CSV file to upload.")
        return redirect('import_export_page')

    try:
        raw = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        messages.error(request, "Could not read that file as text - is it a CSV export?")
        return redirect('import_export_page')

    try:
        run_import(raw, commit=False)
    except ValueError as e:
        messages.error(request, f"Import failed: {e}")
        return redirect('import_export_page')

    request.session[SESSION_IMPORT_TEXT] = raw
    request.session[SESSION_IMPORT_FILENAME] = upload.name
    messages.success(request, f"Parsed '{upload.name}' - review the changes below before confirming.")

    return redirect('import_export_page')


@login_required
@require_POST
def import_csv_confirm(request):
    csv_text = request.session.get(SESSION_IMPORT_TEXT)
    if not csv_text:
        messages.warning(request, "No pending import to confirm.")
        return redirect('import_export_page')

    try:
        restore_archived = request.POST.get('restore_archived') == 'yes'
        summary = run_import(csv_text, commit=True, restore_archived=restore_archived)
        message = (
            f"Import complete. Clients created: {summary.clients_created}, "
            f"Accounts created: {summary.accounts_created}, updated: {summary.accounts_updated}, "
            f"Devices created: {summary.devices_created}, updated: {summary.devices_updated}, "
            f"unchanged: {summary.devices_unchanged}, "
            f"Credentials created: {summary.credentials_created}, updated: {summary.credentials_updated}. "
            f"Skipped {summary.total_skipped} row(s)."
        )
        if summary.plans_created:
            message += f" Plans created: {summary.plans_created} - check their prices on the Plans page."
        if summary.archived_found:
            if restore_archived:
                message += f" Restored {summary.devices_restored} archived kit(s)."
            else:
                message += f" {summary.archived_found} matching kit(s) are archived and were left archived."
        messages.success(request, message)
    except ValueError as e:
        messages.error(request, f"Import failed: {e}")
    finally:
        request.session.pop(SESSION_IMPORT_TEXT, None)
        request.session.pop(SESSION_IMPORT_FILENAME, None)

    return redirect('import_export_page')


@login_required
@require_POST
def import_csv_cancel(request):
    request.session.pop(SESSION_IMPORT_TEXT, None)
    request.session.pop(SESSION_IMPORT_FILENAME, None)
    messages.info(request, "Pending import discarded.")
    return redirect('import_export_page')


@login_required
def export_csv_view(request):
    csv_text = export_csv(include_passwords=request.GET.get('include_passwords') == '1')
    response = HttpResponse(csv_text, content_type='text/csv')
    stamp = datetime.date.today().isoformat()
    response['Content-Disposition'] = f'attachment; filename="starlink_export_{stamp}.csv"'
    return response


from tracker.middleware import set_audit_log_disabled
from .undo_helper import revert_log_entry, redo_log_entry

@login_required
@require_POST
def undo_action(request):
    undo_stack = request.session.get('undo_stack', [])
    if not undo_stack:
        messages.warning(request, "Nothing to undo.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

    log_ids = undo_stack.pop()
    
    try:
        with transaction.atomic():
            set_audit_log_disabled(True)
            for log_id in reversed(log_ids):
                try:
                    log = AuditLog.objects.get(pk=log_id)
                    revert_log_entry(log)
                except AuditLog.DoesNotExist:
                    pass
            set_audit_log_disabled(False)
            
        redo_stack = request.session.get('redo_stack', [])
        redo_stack.append(log_ids)
        if len(redo_stack) > 20:
            redo_stack.pop(0)
            
        request.session['undo_stack'] = undo_stack
        request.session['redo_stack'] = redo_stack
        request.session.modified = True
        
        messages.success(request, "Action undone successfully.")
    except Exception as e:
        set_audit_log_disabled(False)
        messages.error(request, f"Failed to undo action: {str(e)}")
        
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
@require_POST
def redo_action(request):
    redo_stack = request.session.get('redo_stack', [])
    if not redo_stack:
        messages.warning(request, "Nothing to redo.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

    log_ids = redo_stack.pop()
    
    try:
        with transaction.atomic():
            set_audit_log_disabled(True)
            for log_id in log_ids:
                try:
                    log = AuditLog.objects.get(pk=log_id)
                    redo_log_entry(log)
                except AuditLog.DoesNotExist:
                    pass
            set_audit_log_disabled(False)
            
        undo_stack = request.session.get('undo_stack', [])
        undo_stack.append(log_ids)
        if len(undo_stack) > 20:
            undo_stack.pop(0)
            
        request.session['undo_stack'] = undo_stack
        request.session['redo_stack'] = redo_stack
        request.session.modified = True
        
        messages.success(request, "Action redone successfully.")
    except Exception as e:
        set_audit_log_disabled(False)
        messages.error(request, f"Failed to redo action: {str(e)}")
        
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))


