from django.core.management.base import BaseCommand, CommandError

from tracker.csv_import import run_import


class Command(BaseCommand):
    help = "Import Clients, Starlink Accounts, Devices and Credentials from the NetConnect-format tracker CSV."

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)
        parser.add_argument('--dry-run', action='store_true', help="Parse and report without writing to the database.")

    def handle(self, *args, **options):
        csv_path = options['csv_path']
        dry_run = options['dry_run']

        try:
            with open(csv_path, encoding='utf-8-sig', newline='') as f:
                csv_text = f.read()
        except OSError as e:
            raise CommandError(f"Could not open {csv_path}: {e}")

        try:
            summary = run_import(csv_text, commit=not dry_run)
        except ValueError as e:
            raise CommandError(str(e))

        for row in summary.rows:
            if row['skipped']:
                self.stdout.write(f"Line {row['line']}: skipping '{row['client_name']}' - {row['skip_reason']}.")

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}Done. Clients created: {summary.clients_created}, Accounts created: {summary.accounts_created}, "
            f"Devices created: {summary.devices_created}, Devices updated: {summary.devices_updated}, "
            f"Credentials created: {summary.credentials_created}, Credentials updated: {summary.credentials_updated}, "
            f"Skipped (no client name): {summary.skipped_no_client}, "
            f"Skipped (no kit number): {summary.skipped_no_kit}"
        ))
