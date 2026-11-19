import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .csv_import import run_import, export_csv
from .middleware import set_current_user
from .models import Client, Credential, ServicePlan, StarlinkAccount, StarlinkDevice

NETCONNECT_HEADER = (
    "Remarks,Address,Client,model,Account,StarLink,Serial,KIT ,Starlink,,Starlink,Billing\n"
    ",,,,Number, ID,Number,NUMBER,Sign-in User,,Sign-in Pass,Cycle\n"
)


def sheet(*rows):
    return NETCONNECT_HEADER + "\n".join(rows) + "\n"


class ImportTests(TestCase):
    def setUp(self):
        # The audit middleware keeps the request user in a context variable; don't
        # let a user from another test (since rolled back) leak into audit rows here.
        set_current_user(None)

    def test_blank_cells_do_not_wipe_existing_data(self):
        client = Client.objects.create(name='Asha')
        StarlinkDevice.objects.create(
            client=client, kit_number='KIT1', location_name='Roof', model='Mini', status='Running',
            starlink_id='SID', serial_number='SER', latitude=-20.1, longitude=28.4,
            wifi_name='Asha WiFi', notes='Installed by Tom',
        )
        run_import(sheet(",,Asha,,,,,KIT1,,,,"))
        device = StarlinkDevice.objects.get(kit_number='KIT1')
        self.assertEqual(device.model, 'Mini')
        self.assertEqual(device.status, 'Running')
        self.assertEqual(device.starlink_id, 'SID')
        self.assertEqual(device.serial_number, 'SER')
        self.assertEqual(device.latitude, -20.1)
        self.assertEqual(device.notes, 'Installed by Tom')
        self.assertEqual(device.location_name, 'Roof')

    def test_import_notes_are_appended_not_replacing_user_notes(self):
        client = Client.objects.create(name='Asha')
        StarlinkDevice.objects.create(client=client, kit_number='KIT1', location_name='Asha', notes='Gate code 1234')
        run_import(sheet("Running (moved),,Asha,GEN2,,,,KIT1,,,,"))
        notes = StarlinkDevice.objects.get(kit_number='KIT1').notes
        self.assertIn('Gate code 1234', notes)
        self.assertIn("Original remarks: 'Running (moved)'", notes)

    def test_export_round_trips_through_import(self):
        client = Client.objects.create(name='Asha', company='Farbell', email='a@x.co', phone='123')
        account = StarlinkAccount.objects.create(
            account_number='ACC-1', client=client, account_due_by=datetime.date(2026, 11, 1), cost_to_us=50,
        )
        Credential.objects.create(account=account, label='Starlink Login', username='a@x.co', password='p / w')
        StarlinkDevice.objects.create(
            client=client, account=account, kit_number='KIT1', location_name='Asha', model='gen2',
            wifi_name='W', latitude=1.5, longitude=2.5,
        )
        StarlinkDevice.objects.create(kit_number='KIT2', location_name='Spare')
        self.assertNotIn('p / w', export_csv())          # passwords only when asked for
        exported = export_csv(include_passwords=True)

        summary = run_import(exported, commit=False)
        self.assertEqual(summary.format, 'StarlinkOps export')
        self.assertEqual(summary.devices_unchanged, 2)
        self.assertEqual(summary.total_skipped, 0)

        # Restore into an empty database.
        StarlinkDevice.objects.all().delete()
        StarlinkAccount.objects.all().delete()
        Client.objects.all().delete()
        run_import(exported)
        device = StarlinkDevice.objects.get(kit_number='KIT1')
        self.assertEqual(device.model, 'gen2')
        self.assertEqual(device.wifi_name, 'W')
        self.assertEqual(device.client.company, 'Farbell')
        self.assertEqual(device.account.cost_to_us, 50)
        self.assertEqual(device.account.credentials.get().password, 'p / w')
        self.assertIsNone(StarlinkDevice.objects.get(kit_number='KIT2').client)

    def test_client_names_match_ignoring_case_and_spaces(self):
        Client.objects.create(name='Mrs Bristow')
        summary = run_import(sheet(",,mrs  bristow ,,,,,KIT1,,,,"))
        self.assertEqual(summary.clients_created, 0)
        self.assertEqual(Client.objects.count(), 1)

    def test_duplicate_client_names_do_not_crash(self):
        first = Client.objects.create(name='Lyme Mine')
        Client.objects.create(name='Lyme Mine')
        summary = run_import(sheet(",,Lyme Mine,,,,,KIT1,,,,"))
        self.assertEqual(StarlinkDevice.objects.get(kit_number='KIT1').client, first)
        self.assertTrue(summary.rows[0]['warnings'])

    def test_existing_account_gets_client_and_newer_billing_dates(self):
        account = StarlinkAccount.objects.create(account_number='ACC-1', account_due_by=datetime.date(2000, 1, 1))
        run_import(sheet(",,Asha,,ACC-1,,,KIT1,,,,June 2 - July 1"))
        account.refresh_from_db()
        self.assertEqual(account.client.name, 'Asha')
        self.assertGreater(account.account_due_by, datetime.date(2000, 1, 1))

    def test_older_billing_dates_do_not_move_due_date_back(self):
        far_future = datetime.date.today() + datetime.timedelta(days=800)
        account = StarlinkAccount.objects.create(account_number='ACC-1', account_due_by=far_future)
        run_import(sheet(",,Asha,,ACC-1,,,KIT1,,,,June 2 - July 1"))
        account.refresh_from_db()
        self.assertEqual(account.account_due_by, far_future)

    def test_archived_kits_are_flagged_and_restored_only_on_request(self):
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='X', is_archived=True, archived_reason='old')
        summary = run_import(sheet(",,Asha,,,,,KIT1,,,,"))
        self.assertEqual(summary.archived_found, 1)
        self.assertTrue(summary.rows[0]['archived'])
        self.assertTrue(StarlinkDevice.objects.get(kit_number='KIT1').is_archived)

        run_import(sheet(",,Asha,,,,,KIT1,,,,"), restore_archived=True)
        device = StarlinkDevice.objects.get(kit_number='KIT1')
        self.assertFalse(device.is_archived)
        self.assertIsNone(device.archived_reason)

    def test_duplicate_kit_in_same_csv_is_skipped(self):
        summary = run_import(sheet(
            "Running,,Asha,GEN2,,,,KIT1,,,,",
            "Running,,Other,MINI,,,,kit1,,,,",
        ))
        self.assertEqual(summary.skipped_duplicate, 1)
        self.assertEqual(summary.rows[1]['skip_reason'], 'Duplicate of line 3')
        self.assertEqual(StarlinkDevice.objects.get(kit_number='KIT1').client.name, 'Asha')

    def test_model_names_with_spaces_are_recognised(self):
        run_import(sheet(",,Asha,Gen 2,,,,KIT1,,,,"))
        self.assertEqual(StarlinkDevice.objects.get(kit_number='KIT1').model, 'gen2')

    def test_preview_writes_nothing(self):
        run_import(sheet("Running,,Asha,GEN2,ACC-1,,,KIT1,u@x.co,,pw,"), commit=False)
        self.assertFalse(StarlinkDevice.objects.exists())
        self.assertFalse(Client.objects.exists())


class AddKitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('staff', password='pw')
        self.client.force_login(self.user)
        self.owner = Client.objects.create(name='Asha')

    def post(self, **data):
        return self.client.post(reverse('add_kit'), {'model': 'Gen3', 'status': 'To install', **data})

    def test_add_kit_for_existing_client(self):
        res = self.post(kit_number=' kit401375658cm4 ', client_mode='existing', client_id=self.owner.id)
        self.assertEqual(res.status_code, 200)
        device = StarlinkDevice.objects.get()
        self.assertEqual(device.kit_number, 'KIT401375658CM4')
        self.assertEqual(device.client, self.owner)
        self.assertEqual(device.location_name, 'Asha')

    def test_add_kit_with_new_client_and_account(self):
        res = self.post(
            kit_number='KIT303196169', client_mode='new', new_client_name='New Farm', new_client_company='Afrinet',
            account_mode='new', new_account_number='ACC-1234567-12345-12', new_account_username='u@x.co',
            new_account_password='pw',
        )
        self.assertEqual(res.status_code, 200)
        device = StarlinkDevice.objects.get(kit_number='KIT303196169')
        self.assertEqual(device.client.company, 'Afrinet')
        self.assertEqual(device.account.client, device.client)
        self.assertEqual(device.account.credentials.get().username, 'u@x.co')

    def test_unassigned_kit(self):
        self.post(kit_number='KIT303196170', client_mode='none')
        self.assertIsNone(StarlinkDevice.objects.get().client)

    def test_errors_are_returned_per_field_and_nothing_is_saved(self):
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='X')
        res = self.post(kit_number='kit1', client_mode='new', account_mode='new', new_account_number='ACC-2',
                        latitude='abc', longitude='1')
        self.assertEqual(res.status_code, 400)
        errors = res.json()['errors']
        self.assertEqual(set(errors), {'kit_number', 'client', 'account', 'coordinates'})
        self.assertEqual(StarlinkDevice.objects.count(), 1)
        self.assertFalse(StarlinkAccount.objects.exists())

    def test_kit_check(self):
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='X', client=self.owner)
        data = self.client.get(reverse('kit_check'), {'kit_number': 'kit1'}).json()
        self.assertTrue(data['exists'])
        self.assertEqual(data['client'], 'Asha')
        self.assertFalse(self.client.get(reverse('kit_check'), {'kit_number': 'KIT2'}).json()['exists'])

    def test_form_options_list_account_owners(self):
        account = StarlinkAccount.objects.create(account_number='ACC-1')
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='X', client=self.owner, account=account)
        data = self.client.get(reverse('kit_form_options')).json()
        acc = data['accounts'][0]
        self.assertEqual(acc['owners'], ['Asha'])
        self.assertEqual(acc['client_ids'], [self.owner.id])


def export_sheet(header_and_rows):
    """Build a CSV in the app's own export layout from {column: value} dicts."""
    import csv
    import io
    from .csv_import import EXPORT_HEADER
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_HEADER)
    writer.writeheader()
    for row in header_and_rows:
        writer.writerow(row)
    return buf.getvalue()


class ServicePlanTests(TestCase):
    def setUp(self):
        set_current_user(None)
        self.owner = Client.objects.create(name='Asha')
        self.account = StarlinkAccount.objects.create(account_number='ACC-1', client=self.owner, cost_to_us=110)
        self.lite = ServicePlan.objects.create(name='Residential Lite', price=50)
        self.pri = ServicePlan.objects.create(name='Priority 40GB', price=65)

    def test_effective_price_uses_override_then_plan(self):
        d = StarlinkDevice.objects.create(kit_number='KIT1', location_name='A', plan=self.lite)
        self.assertEqual(d.effective_price, 50)
        d.price_override = 45
        self.assertEqual(d.effective_price, 45)
        self.assertIsNone(StarlinkDevice(kit_number='KIT2', location_name='B').effective_price)

    def test_account_plan_cost_adds_up_active_services(self):
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='A', account=self.account, plan=self.lite)
        StarlinkDevice.objects.create(kit_number='KIT2', location_name='B', account=self.account, plan=self.pri,
                                      price_override=60)
        StarlinkDevice.objects.create(kit_number='KIT3', location_name='C', account=self.account, plan=self.pri,
                                      is_archived=True)
        self.assertEqual(self.account.plan_cost, 110)
        unpriced = ServicePlan.objects.create(name='New plan')
        StarlinkDevice.objects.create(kit_number='KIT4', location_name='D', account=self.account, plan=unpriced)
        self.assertIsNone(self.account.plan_cost)       # no partial totals

    def test_import_creates_plan_and_sets_service_details(self):
        csv_text = export_sheet([{
            'Client': 'Asha', 'Account Number': 'ACC-1', 'Kit Number': 'KIT1',
            'Service Line': 'SL-1', 'Starlink Nickname': 'Farm', 'Service Address': 'QGXJ+67R, Bulawayo',
            'Plan': 'priority  - 40gb  subscription', 'Balance Due': '11.52',
            'Synced From Starlink': '2026-11-19T08:09:55+02:00',
        }])
        summary = run_import(csv_text)
        self.assertEqual(summary.plans_created, 1)
        device = StarlinkDevice.objects.get(kit_number='KIT1')
        self.assertEqual(device.plan.name, 'priority - 40gb subscription')
        self.assertIsNone(device.plan.price)
        self.assertEqual((device.service_line_number, device.starlink_nickname), ('SL-1', 'Farm'))
        self.assertEqual(device.service_address, 'QGXJ+67R, Bulawayo')
        account = StarlinkAccount.objects.get(account_number='ACC-1')
        self.assertEqual(str(account.balance_due), '11.52')
        self.assertEqual(account.starlink_synced_at.isoformat(), '2026-11-19T06:09:55+00:00')
        self.assertIn('without a price', ' '.join(summary.rows[0]['warnings']))

    def test_import_matches_existing_plan_ignoring_case_and_keeps_its_price(self):
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='A', client=self.owner, plan=self.pri,
                                      price_override=60, price_override_reason='Starlink discount')
        run_import(export_sheet([{'Client': 'Asha', 'Kit Number': 'KIT1', 'Plan': 'RESIDENTIAL LITE',
                                  'Plan Price': '99'}]))
        device = StarlinkDevice.objects.get(kit_number='KIT1')
        self.assertEqual(device.plan, self.lite)
        self.lite.refresh_from_db()
        self.assertEqual(self.lite.price, 50)            # plan prices are managed on the Plans page
        self.assertEqual(device.price_override, 60)      # blank cells never clear an override
        self.assertEqual(device.price_override_reason, 'Starlink discount')
        self.assertEqual(ServicePlan.objects.count(), 2)

    def test_new_plan_takes_price_from_import_when_given(self):
        run_import(export_sheet([{'Client': 'Asha', 'Kit Number': 'KIT1', 'Plan': 'Roam 50GB', 'Plan Price': '50'}]))
        self.assertEqual(ServicePlan.objects.get(name='Roam 50GB').price, 50)

    def test_export_round_trips_plan_and_override(self):
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='A', client=self.owner, account=self.account,
                                      plan=self.lite, price_override=45, price_override_reason='Promo',
                                      service_line_number='SL-1')
        exported = export_csv()
        StarlinkDevice.objects.all().update(plan=None, price_override=None, price_override_reason=None)
        summary = run_import(exported)
        device = StarlinkDevice.objects.get(kit_number='KIT1')
        self.assertEqual((device.plan, device.price_override, device.price_override_reason),
                         (self.lite, 45, 'Promo'))
        self.assertEqual(summary.plans_created, 0)

    def test_old_export_files_without_plan_columns_still_import(self):
        old_header = 'Client,Company,Client Email,Client Phone,Client Address,Account Number,' \
                     'Last Payment Date,Account Due By,Cost To Us,Client Invoice,Kit Number,Model,Status,' \
                     'Starlink ID,Serial Number,Latitude,Longitude,Wifi Name,Wifi Password,Device Notes,Credentials\n'
        StarlinkDevice.objects.create(kit_number='KIT1', location_name='A', client=self.owner, plan=self.lite)
        run_import(old_header + 'Asha,,,,,,,,,,KIT1,,,,,,,,,,\n')
        self.assertEqual(StarlinkDevice.objects.get(kit_number='KIT1').plan, self.lite)


# Pages render {% static %}; tests run with DEBUG off, which would need a collectstatic manifest.
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ServicePlanViewTests(TestCase):
    def setUp(self):
        set_current_user(None)
        self.user = User.objects.create_user('staff', password='pw')
        self.client.force_login(self.user)
        self.owner = Client.objects.create(name='Asha')
        self.plan = ServicePlan.objects.create(name='Residential Lite', price=50)
        self.device = StarlinkDevice.objects.create(kit_number='KIT1', location_name='A', client=self.owner)

    def test_plans_page_lists_plans_and_overrides(self):
        self.device.plan = self.plan
        self.device.price_override = 45
        self.device.save()
        res = self.client.get(reverse('plans_page'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Residential Lite')
        self.assertContains(res, 'KIT1')
        self.assertContains(res, '-5.00')

    def test_add_update_and_delete_plan(self):
        self.client.post(reverse('add_plan'), {'name': ' Priority  40GB ', 'price': '65', 'currency': 'usd',
                                               'is_active': 'on'})
        plan = ServicePlan.objects.get(name='Priority 40GB')
        self.assertEqual((plan.price, plan.currency, plan.is_active), (65, 'USD', True))
        self.client.post(reverse('update_plan', args=[plan.id]), {'name': 'Priority 40GB', 'price': '70',
                                                                  'currency': 'USD'})
        plan.refresh_from_db()
        self.assertEqual((plan.price, plan.is_active), (70, False))
        self.client.post(reverse('delete_plan', args=[plan.id]))
        self.assertFalse(ServicePlan.objects.filter(pk=plan.id).exists())

    def test_duplicate_or_bad_plan_input_is_rejected(self):
        self.client.post(reverse('add_plan'), {'name': 'residential lite', 'price': '1'})
        self.client.post(reverse('add_plan'), {'name': 'Other', 'price': 'abc'})
        self.assertEqual(ServicePlan.objects.count(), 1)

    def test_plan_in_use_cannot_be_deleted(self):
        self.device.plan = self.plan
        self.device.save()
        self.client.post(reverse('delete_plan', args=[self.plan.id]))
        self.assertTrue(ServicePlan.objects.filter(pk=self.plan.id).exists())

    def unit_form(self, **extra):
        return {'kit_number': 'KIT1', 'location_name': 'A', 'model': 'Gen3', 'status': 'Running',
                'account_id': 'unlinked', 'client_id': self.owner.id, **extra}

    def test_unit_form_sets_plan_override_and_reason(self):
        self.client.post(reverse('update_starlink', args=[self.device.id]),
                         self.unit_form(plan_id=self.plan.id, price_override='45.50',
                                        price_override_reason='Starlink discount', service_line_number='SL-9'))
        self.device.refresh_from_db()
        self.assertEqual(self.device.plan, self.plan)
        self.assertEqual(str(self.device.price_override), '45.50')
        self.assertEqual(self.device.price_override_reason, 'Starlink discount')
        self.assertEqual(self.device.service_line_number, 'SL-9')
        # clearing the override goes back to the plan price
        self.client.post(reverse('update_starlink', args=[self.device.id]),
                         self.unit_form(plan_id=self.plan.id, price_override='', price_override_reason=''))
        self.device.refresh_from_db()
        self.assertIsNone(self.device.price_override)
        self.assertEqual(self.device.effective_price, 50)

    def test_forms_without_plan_fields_leave_them_alone(self):
        self.device.plan = self.plan
        self.device.price_override = 45
        self.device.save()
        self.client.post(reverse('update_starlink', args=[self.device.id]), self.unit_form())
        self.device.refresh_from_db()
        self.assertEqual((self.device.plan, self.device.price_override), (self.plan, 45))

    def test_client_page_shows_plan_picker_and_account_check(self):
        account = StarlinkAccount.objects.create(account_number='ACC-1', client=self.owner, cost_to_us=60)
        self.device.plan = self.plan
        self.device.account = account
        self.device.save()
        res = self.client.get(reverse('client_detail', args=[self.owner.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'name="plan_id"')
        self.assertContains(res, 'name="price_override"')
        self.assertContains(res, 'Expected From Plans')
        self.assertContains(res, '≠ Cost to Us')

    def test_plan_price_change_is_audited(self):
        from .models import AuditLog
        self.client.post(reverse('update_plan', args=[self.plan.id]), {'name': 'Residential Lite', 'price': '55',
                                                                       'is_active': 'on'})
        log = AuditLog.objects.filter(model_name='ServicePlan', action='UPDATE').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes['price'], ['50.00', '55.00'])


HARVEST_FIELDS = [
    'bitwarden_item', 'bitwarden_item_id', 'email', 'account_number', 'account_name', 'service_line', 'nickname',
    'kit_numbers', 'starlink_ids', 'dish_serials', 'dish_types', 'plan', 'service_active', 'location',
    'coordinates', 'last_bill_amount', 'currency', 'last_bill_date', 'next_due_date', 'invoice_number',
    'balance_due', 'bill_scope', 'services_on_account', 'status', 'bill_source', 'error', 'scraped_at',
]


def harvest_csv(*rows):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=HARVEST_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, '') for k in HARVEST_FIELDS})
    return buf.getvalue()


HOTBURN = dict(
    bitwarden_item='Starlink', bitwarden_item_id='bw-123', email='hotburn@netconnect.co.zw',
    account_number='ACC-6749663-87165-33', account_name='Hotburn Minerals', currency='USD',
    last_bill_amount='59.99', last_bill_date='2026-10-27', next_due_date='2026-11-27',
    balance_due='11.52 USD', bill_scope='ACCOUNT TOTAL - shared by 2 services', services_on_account='2',
    status='ok', scraped_at='2026-11-19T08:09:55+02:00',
)
MINI_ROW = dict(HOTBURN, service_line='SL-3979245-95868-0', nickname='Mr Thompson', kit_numbers='KIT4M001777304C8',
                starlink_ids='30b8880f-c5116519-180d9aa6', dish_serials='M1HT00200469', dish_types='Mini',
                plan='Residential Lite', service_active='Active', location='QGXJ+67R, Bulawayo, Zimbabwe',
                coordinates='-20.20189,28.53064')
GEN3_ROW = dict(HOTBURN, service_line='SL-3979329-80221-80', nickname='HotBurn', kit_numbers='KIT4020003867FC',
                starlink_ids='50908e94-c500661b-58f27343', dish_serials='4PBA01919468', dish_types='Gen 3',
                plan='Priority - 40GB Subscription', service_active='Active', location='QQX5+J2V, Katiyo, Zimbabwe',
                coordinates='-17.20089,31.75755')


class HarvestImportTests(TestCase):
    def setUp(self):
        set_current_user(None)

    def test_new_units_arrive_unassigned_with_starlink_details(self):
        summary = run_import(harvest_csv(MINI_ROW, GEN3_ROW, dict(email='x@y', status='captcha')))
        self.assertEqual(summary.format, 'Starlink harvest')
        self.assertEqual(summary.devices_created, 2)
        self.assertEqual(summary.skipped_not_harvested, 1)
        mini = StarlinkDevice.objects.get(kit_number='KIT4M001777304C8')
        self.assertIsNone(mini.client)
        self.assertEqual((mini.model, mini.status, mini.location_name), ('Mini', 'Running', 'Mr Thompson'))
        self.assertEqual(mini.starlink_id, '30b8880f-c5116519-180d9aa6')
        self.assertEqual(mini.serial_number, 'M1HT00200469')
        self.assertEqual(mini.service_line_number, 'SL-3979245-95868-0')
        self.assertTrue(mini.starlink_service_active)
        self.assertEqual(mini.plan.name, 'Residential Lite')
        self.assertAlmostEqual(mini.latitude, -20.20189)
        account = mini.account
        self.assertEqual(str(account.cost_to_us), '59.99')
        self.assertEqual(str(account.balance_due), '11.52')
        self.assertEqual(account.last_payment_date, datetime.date(2026, 10, 27))
        self.assertEqual(account.account_due_by, datetime.date(2026, 11, 26))
        cred = account.credentials.get()
        self.assertEqual((cred.username, cred.password, cred.bitwarden_item_id),
                         ('hotburn@netconnect.co.zw', '', 'bw-123'))
        self.assertTrue(any('new unit' in n for n in summary.notes))
        self.assertTrue(any('outstanding' in n for n in summary.notes))

    def test_existing_units_keep_client_model_and_status(self):
        owner = Client.objects.create(name='Mr Kevin Thompson - Fabwell')
        StarlinkDevice.objects.create(kit_number='KIT4M001777304C8', client=owner, location_name='Site A',
                                      model='Gen3', status='To install', notes='Gate code 1')
        summary = run_import(harvest_csv(MINI_ROW))
        d = StarlinkDevice.objects.get(kit_number='KIT4M001777304C8')
        self.assertEqual((d.client, d.model, d.status, d.location_name, d.notes),
                         (owner, 'Gen3', 'To install', 'Site A', 'Gate code 1'))
        self.assertEqual(d.service_line_number, 'SL-3979245-95868-0')
        warnings = ' '.join(summary.rows[0]['warnings'])
        self.assertIn('Serial says Mini', warnings)
        self.assertIn("service is active but the unit is marked 'To install'", warnings)

    def test_inactive_service_on_running_unit_is_flagged(self):
        StarlinkDevice.objects.create(kit_number='KIT4M001777304C8', location_name='A', status='Running')
        summary = run_import(harvest_csv(dict(MINI_ROW, service_active='Inactive')))
        self.assertIn('INACTIVE', ' '.join(summary.rows[0]['warnings']))

    def test_bill_mismatch_against_plan_prices_is_noted(self):
        ServicePlan.objects.create(name='Residential Lite', price=50)
        ServicePlan.objects.create(name='Priority - 40GB Subscription', price=24.99)
        summary = run_import(harvest_csv(MINI_ROW, GEN3_ROW))
        self.assertTrue(any('plans/overrides add up to 74.99' in n for n in summary.notes))

    def test_harvest_does_not_rename_existing_login_or_wipe_password(self):
        account = StarlinkAccount.objects.create(account_number='ACC-6749663-87165-33')
        Credential.objects.create(account=account, label='Main', username='HotBurn@netconnect.co.zw', password='pw')
        run_import(harvest_csv(MINI_ROW))
        cred = Credential.objects.get()
        self.assertEqual((cred.label, cred.password, cred.bitwarden_item_id), ('Main', 'pw', 'bw-123'))


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}, BITWARDEN_ITEM_URL='https://vault.example.com/#/vault?itemId={id}')
class InboxAndCredentialTests(TestCase):
    def setUp(self):
        set_current_user(None)
        self.user = User.objects.create_user('staff', password='pw')
        self.client.force_login(self.user)

    def test_inbox_lists_new_units_with_suggested_client_and_assign_works(self):
        run_import(harvest_csv(MINI_ROW, GEN3_ROW))
        owner = Client.objects.create(name='Hotburn Minerals - Fabwell', company='Farbell')
        account = StarlinkAccount.objects.get()
        account.client = owner
        account.save()
        res = self.client.get(reverse('starlink_inbox'))
        self.assertContains(res, 'KIT4M001777304C8')
        self.assertContains(res, 'suggested from the account')
        self.assertContains(res, 'outstanding'.title()[:0] + 'Outstanding balance on Starlink')
        self.assertEqual(res.context['starlink_inbox_count'], 2)
        unit = StarlinkDevice.objects.get(kit_number='KIT4M001777304C8')
        self.client.post(reverse('assign_starlink', args=[unit.id]), {
            'new_client_name': 'Mr Kevin Thompson', 'new_client_company': 'Farbell', 'status': 'Running'})
        unit.refresh_from_db()
        self.assertEqual(unit.client.name, 'Mr Kevin Thompson')
        self.assertEqual(unit.location_name, 'Mr Thompson')
        other = StarlinkDevice.objects.get(kit_number='KIT4020003867FC')
        self.client.post(reverse('assign_starlink', args=[other.id]), {'client_id': owner.id})
        other.refresh_from_db()
        self.assertEqual(other.client, owner)
        self.assertEqual(self.client.get(reverse('starlink_inbox')).context['starlink_inbox_count'], 0)

    def test_assign_refuses_duplicate_new_client_and_missing_company(self):
        run_import(harvest_csv(MINI_ROW))
        Client.objects.create(name='Asha')
        unit = StarlinkDevice.objects.get()
        self.client.post(reverse('assign_starlink', args=[unit.id]), {'new_client_name': ' asha ',
                                                                      'new_client_company': 'Farbell'})
        self.client.post(reverse('assign_starlink', args=[unit.id]), {'new_client_name': 'New Co'})
        unit.refresh_from_db()
        self.assertIsNone(unit.client)
        self.assertEqual(Client.objects.count(), 1)

    def test_passwords_are_masked_and_bitwarden_linked(self):
        owner = Client.objects.create(name='Asha')
        account = StarlinkAccount.objects.create(account_number='ACC-1234567-12345-12', client=owner)
        Credential.objects.create(account=account, username='a@x.co', password='S3cret!', bitwarden_item_id='abc')
        StarlinkDevice.objects.create(kit_number='KIT303196169', location_name='A', client=owner, account=account)
        page = self.client.get(reverse('client_detail', args=[owner.id])).content.decode()
        self.assertNotIn('>S3cret!<', page)
        self.assertIn('••••••••', page)
        self.assertIn('https://vault.example.com/#/vault?itemId=abc', page)
        dash = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('>S3cret!<', dash)

    def test_credential_without_password_can_be_added(self):
        account = StarlinkAccount.objects.create(account_number='ACC-1234567-12345-12')
        self.client.post(reverse('add_credential'), {'account_id': account.id, 'username': 'a@x.co',
                                                     'bitwarden_item_id': 'bw-9'})
        cred = Credential.objects.get()
        self.assertEqual((cred.password, cred.bitwarden_item_id), ('', 'bw-9'))

    def test_inbox_page_renders_when_empty(self):
        self.assertContains(self.client.get(reverse('starlink_inbox')), 'Nothing waiting')


class LifecycleTests(TestCase):
    def setUp(self):
        set_current_user(None)

    def test_install_date_set_when_unit_goes_running(self):
        d = StarlinkDevice.objects.create(kit_number='KIT303196169', location_name='A', status='To install')
        self.assertIsNotNone(d.status_changed_at)
        self.assertIsNone(d.installed_on)
        d.status = 'Running'
        d.save()
        self.assertEqual(d.installed_on, datetime.date.today())

    def test_units_created_running_have_no_guessed_install_date(self):
        d = StarlinkDevice.objects.create(kit_number='KIT303196169', location_name='A', status='Running')
        self.assertIsNone(d.installed_on)

    def test_existing_install_date_is_not_overwritten(self):
        d = StarlinkDevice.objects.create(kit_number='KIT303196169', location_name='A', status='Hardware fault',
                                          installed_on=datetime.date(2025, 1, 5))
        d.status = 'Running'
        d.save()
        self.assertEqual(d.installed_on, datetime.date(2025, 1, 5))


class AddKitChecksTests(TestCase):
    def setUp(self):
        set_current_user(None)
        self.user = User.objects.create_user('staff', password='pw')
        self.client.force_login(self.user)
        self.owner = Client.objects.create(name='Asha')

    def post(self, **data):
        return self.client.post(reverse('add_kit'), {'model': 'Gen3', 'status': 'To install', **data})

    def test_format_checks(self):
        res = self.post(kit_number='12345', client_mode='none', starlink_id='not-an-id',
                        account_mode='new', new_account_number='12345', new_account_username='u@x.co')
        self.assertEqual(set(res.json()['errors']), {'kit_number', 'starlink_id', 'account'})

    def test_new_client_needs_company_and_unique_name(self):
        res = self.post(kit_number='KIT303196169', client_mode='new', new_client_name='New Farm')
        self.assertIn('company', res.json()['errors']['client'])
        res = self.post(kit_number='KIT303196169', client_mode='new', new_client_name=' ASHA ',
                        new_client_company='Farbell')
        self.assertIn('already exists', res.json()['errors']['client'])

    def test_new_account_needs_login_but_not_password(self):
        res = self.post(kit_number='KIT303196169', client_mode='none', account_mode='new',
                        new_account_number='acc-1234567-12345-12', new_account_username='u@x.co',
                        new_account_bitwarden='bw-1')
        self.assertEqual(res.status_code, 200)
        cred = StarlinkAccount.objects.get(account_number='ACC-1234567-12345-12').credentials.get()
        self.assertEqual((cred.password, cred.bitwarden_item_id), ('', 'bw-1'))

    def test_maps_link_coordinates_plan_and_override(self):
        plan = ServicePlan.objects.create(name='Residential Lite', price=50)
        res = self.post(kit_number='KIT4M001777304C8', client_mode='none', model='Mini',
                        latitude='https://www.google.com/maps/@-20.2018875,28.5306406,17z', longitude='',
                        starlink_id='ut30B8880F-C5116519-180D9AA6', plan_id=plan.id, price_override='45')
        self.assertEqual(res.status_code, 200)
        d = StarlinkDevice.objects.get()
        self.assertAlmostEqual(d.latitude, -20.2018875)
        self.assertAlmostEqual(d.longitude, 28.5306406)
        self.assertEqual(d.starlink_id, '30b8880f-c5116519-180d9aa6')
        self.assertEqual((d.plan, d.effective_price), (plan, 45))

    def test_model_mismatch_is_mentioned(self):
        self.post(kit_number='KIT4M001777304C8', client_mode='none', model='gen2')
        from django.contrib.messages import get_messages
        res = self.client.get(reverse('kit_form_options'))
        msgs = [str(m) for m in get_messages(res.wsgi_request)]
        self.assertTrue(any('looks like a Mini' in m for m in msgs))

    def test_add_account_kit_section_uses_same_checks(self):
        StarlinkDevice.objects.create(kit_number='KIT303196169', location_name='X')
        self.client.post(reverse('add_account'), {
            'account_number': 'ACC-1234567-12345-12', 'add_credentials': 'yes', 'cred_username': 'u@x.co',
            'cred_password': '', 'add_starlink': 'yes', 'sl_kit_number': 'kit303196169', 'sl_location_name': 'B',
        })
        self.assertEqual(StarlinkDevice.objects.count(), 1)      # case-insensitive duplicate caught
        self.assertFalse(StarlinkAccount.objects.exists())       # whole step rolled back
        self.client.post(reverse('add_account'), {
            'account_number': 'ACC-1234567-12345-12', 'add_credentials': 'yes', 'cred_username': 'u@x.co',
            'add_starlink': 'yes', 'sl_kit_number': 'kit4m001777304c8', 'sl_location_name': 'B',
            'sl_latitude': 'abc',
        })
        self.assertFalse(StarlinkAccount.objects.exists())       # bad coordinates: clear error, nothing saved
        self.client.post(reverse('add_account'), {
            'account_number': 'ACC-1234567-12345-12', 'add_credentials': 'yes', 'cred_username': 'u@x.co',
            'add_starlink': 'yes', 'sl_kit_number': 'kit4m001777304c8', 'sl_location_name': 'B',
        })
        self.assertEqual(StarlinkDevice.objects.get(kit_number='KIT4M001777304C8').account.credentials.get().password, '')


class AuditSecretTests(TestCase):
    def setUp(self):
        set_current_user(None)

    def test_passwords_never_reach_the_audit_log_and_undo_skips_them(self):
        from .models import AuditLog
        from .undo_helper import revert_log_entry
        account = StarlinkAccount.objects.create(account_number='ACC-1234567-12345-12')
        cred = Credential.objects.create(account=account, username='a@x.co', password='S3cret!', label='L')
        cred.password = 'N3w!'
        cred.label = 'Main'
        cred.save()
        dump = str(list(AuditLog.objects.values_list('changes', flat=True)))
        self.assertNotIn('S3cret!', dump)
        self.assertNotIn('N3w!', dump)
        revert_log_entry(AuditLog.objects.filter(model_name='Credential', action='UPDATE').first())
        cred.refresh_from_db()
        self.assertEqual((cred.label, cred.password), ('L', 'N3w!'))

    def test_redact_command_scrubs_old_logs(self):
        from django.core.management import call_command
        from .models import AuditLog
        AuditLog.objects.create(action='UPDATE', model_name='Credential', object_id=1, object_repr='x',
                                changes={'password': ['old', 'new'], 'label': ['a', 'b']})
        call_command('redact_audit_passwords', verbosity=0)
        log = AuditLog.objects.get()
        self.assertEqual(log.changes, {'password': ['(hidden)', '(hidden)'], 'label': ['a', 'b']})
