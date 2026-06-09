import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from .models import StarlinkAccount, Client, Credential, StarlinkDevice, AuditLog

@login_required
def dashboard(request):
    query = request.GET.get('q', '').strip()
    
    # Base querysets
    clients = Client.objects.prefetch_related('starlinks__account__credentials')
    unassigned_starlinks = StarlinkDevice.objects.filter(client__isnull=True).select_related('account')
    accounts = StarlinkAccount.objects.prefetch_related('credentials', 'starlinks__client')
    
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
            Q(starlinks__kit_number__icontains=query)
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
        associated_clients = list(Client.objects.filter(starlinks__account=acc).distinct())
        if acc.client and acc.client not in associated_clients:
            associated_clients.append(acc.client)
        acc.associated_clients = associated_clients
        acc.clients_count = len(associated_clients)

    # Associate accounts and credentials directly onto clients for template use
    for client in clients:
        associated_accounts = []
        seen_accs = set()
        for sl in client.starlinks.all():
            sl.email_history = AuditLog.objects.filter(kit_number=sl.kit_number).order_by('-timestamp')
            if sl.account and sl.account.id not in seen_accs:
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
        'all_accounts_list': StarlinkAccount.objects.all(),
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
        if username and password:
            Credential.objects.create(
                account=account,
                label=label,
                username=username,
                password=password,
                email_pass=email_pass,
                notes=notes
            )
            messages.success(request, f"Credential added for account '{account.account_number}'.")
        else:
            messages.error(request, "Username and password are required.")
            
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def add_starlink(request):
    if request.method == 'POST':
        client_id = request.POST.get('client_id')
        account_id = request.POST.get('account_id')
        kit_number = request.POST.get('kit_number')
        location_name = request.POST.get('location_name')
        model = request.POST.get('model', 'Gen3')
        starlink_id = request.POST.get('starlink_id')
        serial_number = request.POST.get('serial_number')
        wifi_name = request.POST.get('wifi_name')
        wifi_password = request.POST.get('wifi_password')
        latitude = request.POST.get('latitude')
        longitude = request.POST.get('longitude')
        status = request.POST.get('status', 'To install')
        notes = request.POST.get('notes')
        
        client = None
        if client_id and client_id != "unassigned" and client_id.strip() != "":
            client = get_object_or_404(Client, id=client_id)
            
        account = None
        if account_id and account_id.strip() != "" and account_id != "unlinked":
            account = get_object_or_404(StarlinkAccount, id=account_id)
            
        if kit_number and location_name:
            try:
                lat_val = float(latitude) if latitude else None
                lng_val = float(longitude) if longitude else None
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
                    notes=notes
                )
                owner = client.name if client else "Unassigned"
                messages.success(request, f"Starlink Kit '{kit_number}' added successfully (Owner: {owner}).")
            except Exception as e:
                messages.error(request, f"Error adding Starlink: {str(e)}")
        else:
            messages.error(request, "Kit number and location name are required.")
            
    return redirect('dashboard')

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
            
        # Verify that at least one credential username and password is provided
        if not add_credentials:
            messages.error(request, "At least one login credential is required to create a billing account.")
            return redirect('dashboard')
            
        usernames = request.POST.getlist('cred_username')
        passwords = request.POST.getlist('cred_password')
        has_credential = False
        for i in range(len(usernames)):
            u = usernames[i].strip() if i < len(usernames) else ""
            p = passwords[i].strip() if i < len(passwords) else ""
            if u and p:
                has_credential = True
                break
                
        if not has_credential:
            messages.error(request, "At least one login credential (username and password) is required.")
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
                        if u and p:
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
                    
                    if not kit_number or not location_name:
                        raise ValueError("Kit number and location name are required for the Starlink device.")
                        
                    if StarlinkDevice.objects.filter(kit_number=kit_number).exists():
                        raise ValueError(f"Starlink Kit with number '{kit_number}' already exists.")
                        
                    lat_val = float(latitude) if latitude else None
                    lng_val = float(longitude) if longitude else None
                    
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
        messages.success(request, f"Starlink Kit '{starlink.kit_number}' updated successfully.")
        
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
def client_detail(request, pk):
    client = get_object_or_404(Client.objects.prefetch_related('starlinks__account__credentials'), pk=pk)
    
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
    for acc in client.accounts.all():
        if acc.id not in seen_accs:
            seen_accs.add(acc.id)
            acc.margin = acc.client_invoice - acc.cost_to_us
            associated_accounts.append(acc)
            
    # 2. Account links via devices
    for sl in client.starlinks.all():
        sl.email_history = AuditLog.objects.filter(kit_number=sl.kit_number).order_by('-timestamp')
        if sl.account and sl.account.id not in seen_accs:
            seen_accs.add(sl.account.id)
            sl.account.margin = sl.account.client_invoice - sl.account.cost_to_us
            associated_accounts.append(sl.account)
            
    client.associated_accounts = associated_accounts

    context = {
        'client': client,
        'starlinks_json': starlinks_data,
        'all_clients_list': Client.objects.all(),
        'all_accounts_list': StarlinkAccount.objects.all(),
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
        credential.password = request.POST.get('password')
        credential.email_pass = request.POST.get('email_pass')
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
def stats_dashboard(request):
    total_starlinks = StarlinkDevice.objects.count()
    
    # Status counts dictionary
    status_counts = {}
    for choice in StarlinkDevice.STATUS_CHOICES:
        status_key = choice[0]
        norm_key = status_key.replace(' ', '_')
        status_counts[norm_key] = StarlinkDevice.objects.filter(status=status_key).count()
        
    # Financial summaries
    total_cost = 0.00
    total_income = 0.00
    accounts = StarlinkAccount.objects.all()
    for acc in accounts:
        total_cost += float(acc.cost_to_us)
        total_income += float(acc.client_invoice)
    total_profit = total_income - total_cost
    
    # Due accounts next 7 days
    today = datetime.date.today()
    seven_days_later = today + datetime.timedelta(days=7)
    upcoming_due_accounts = StarlinkAccount.objects.filter(
        account_due_by__lte=seven_days_later
    ).order_by('account_due_by')
    
    for acc in upcoming_due_accounts:
        acc.margin = acc.client_invoice - acc.cost_to_us
        acc.associated_clients = Client.objects.filter(starlinks__account=acc).distinct()
        
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

from tracker.middleware import set_audit_log_disabled
from .undo_helper import revert_log_entry, redo_log_entry

@login_required
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


