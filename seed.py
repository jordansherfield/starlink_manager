import os
import django
import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'starlink_project.settings')
django.setup()

from tracker.models import Client, Credential, StarlinkDevice, StarlinkAccount

# Clear existing database records
print("Cleaning database...")
StarlinkDevice.objects.all().delete()
Credential.objects.all().delete()
Client.objects.all().delete()
StarlinkAccount.objects.all().delete()

# --- Create Starlink Accounts ---
print("Creating Starlink Accounts...")
acc1 = StarlinkAccount.objects.create(
    account_number="ACC-1001",
    last_payment_date=datetime.date(2026, 5, 10),
    account_due_by=datetime.date(2026, 6, 10),
    cost_to_us=120.00,
    client_invoice=180.00
)

acc2 = StarlinkAccount.objects.create(
    account_number="ACC-1002",
    last_payment_date=datetime.date(2026, 5, 15),
    account_due_by=datetime.date(2026, 6, 15),
    cost_to_us=150.00,
    client_invoice=250.00
)

# --- Create Credentials linked to Accounts ---
print("Creating credentials for accounts...")
Credential.objects.create(
    account=acc1,
    label="Starlink Admin Login",
    username="ops_starlink_1@comnet.com",
    password="superSecurePassword#1",
    email_pass="recPass123!",
    notes="Main portal administration login."
)

Credential.objects.create(
    account=acc1,
    label="Recovery Console Login",
    username="ops_backup_1@comnet.com",
    password="secondarySecPassword#2",
    email_pass="recPassBackup456!",
    notes="Secondary/Backup portal login."
)

Credential.objects.create(
    account=acc2,
    label="Accounts Portal Login",
    username="admin_billing_2@farbell.com",
    password="farbellSecureBilling!2026",
    email_pass="farbellMailPass2026",
    notes="Accounts and subscriptions portal."
)

# --- Create Clients ---
print("Creating Clients...")
client_a = Client.objects.create(
    name="Apex Logistics Group",
    company="Comnet",
    email="ops@apexlogistics.com",
    phone="+1 (555) 234-5678",
    address="450 Denver Industrial Parkway, Denver, CO",
    notes="Primary logistics client with terminals across multiple centers."
)

client_b = Client.objects.create(
    name="Summit Wilderness Lodge",
    company="Farbell",
    email="reservations@summitlodge.net",
    phone="+1 (555) 876-5432",
    address="72 Aspen Mountain Road, Aspen, CO",
    notes="Remote tourist destination in Aspen, CO."
)

client_c = Client.objects.create(
    name="Marine Exploration Corp",
    company="Afrinet",
    email="vessels@marineexplore.org",
    phone="+1 (555) 321-4321",
    address="Pier 39, San Francisco, CA",
    notes="Oceanographic research organization. Starlinks installed on vessels."
)

# --- Create Starlink Devices (kits) ---
print("Deploying Starlink Devices (kits)...")

# Client A Kits
StarlinkDevice.objects.create(
    client=client_a,
    account=acc1,
    kit_number="KIT-A101",
    location_name="Warehouse North - Denver DC",
    model="Gen3",
    starlink_id="ut00892a",
    serial_number="FSP00892301",
    wifi_name="Apex_Denver_Starlink",
    wifi_password="ApexLogisticsDenver!",
    latitude=39.7392,
    longitude=-104.9903,
    status="Running",
    notes="Denver central distribution center main dish. Mounted on warehouse high tower roof."
)

StarlinkDevice.objects.create(
    client=client_a,
    account=acc1,
    kit_number="KIT-B102",
    location_name="Warehouse South - Phoenix DC",
    model="Gen3",
    starlink_id="ut00892b",
    serial_number="FSP00892302",
    wifi_name="Apex_Phoenix_Starlink",
    wifi_password="ApexLogisticsPhoenix!",
    latitude=33.4484,
    longitude=-112.0740,
    status="Running",
    notes="Phoenix auxiliary distribution center dish. Standard side wall mount."
)

StarlinkDevice.objects.create(
    client=client_a,
    account=acc2,
    kit_number="KIT-E202",
    location_name="Apex Corporate HQ - San Francisco Office",
    model="Mini",
    starlink_id="ut00980c",
    serial_number="FSP00982303",
    wifi_name="Apex_HQ_Backup",
    wifi_password="ApexCorporateHQBackup!",
    latitude=37.7749,
    longitude=-122.4194,
    status="To install",
    notes="Backup Mini dish for executive floor. Located on building ledge."
)

# Client B Kits
StarlinkDevice.objects.create(
    client=client_b,
    account=acc1,
    kit_number="KIT-C103",
    location_name="Main Reception Lodge - Aspen",
    model="gen2",
    starlink_id="ut00948c",
    serial_number="FSP00948577",
    wifi_name="Summit_Lodge_Guest",
    wifi_password="SummitLodgeGuestPass!",
    latitude=39.1911,
    longitude=-106.8175,
    status="Running",
    notes="Flat high-performance dish installed on main reception roof. Serves guest lodging."
)

# Client C Kits
StarlinkDevice.objects.create(
    client=client_c,
    account=acc2,
    kit_number="KIT-D201",
    location_name="RV Navigator (Research Vessel)",
    model="Mini",
    starlink_id="ut00481d",
    serial_number="FSP00481234",
    wifi_name="RV_Navigator_Starlink",
    wifi_password="MarineExplorer99!",
    latitude=34.0522,
    longitude=-118.2437,
    status="Running",
    notes="Maritime self-orienting flat dish mounted on vessel mast."
)

print("\nDatabase successfully seeded!")
print("Seeding Summary:")
print(f" - Accounts: {StarlinkAccount.objects.count()}")
print(f" - Credentials: {Credential.objects.count()}")
print(f" - Clients: {Client.objects.count()}")
print(f" - Starlinks: {StarlinkDevice.objects.count()}")
