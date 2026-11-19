from django.core.management.base import BaseCommand

from tracker.models import REDACTED, SECRET_FIELDS, AuditLog


class Command(BaseCommand):
    help = "Replace login passwords stored in existing audit log entries with '(hidden)'."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Only count what would change.")

    def handle(self, *args, **options):
        changed = 0
        for model_name, fields in SECRET_FIELDS.items():
            for log in AuditLog.objects.filter(model_name=model_name).iterator():
                dirty = False
                for field in fields:
                    vals = log.changes.get(field)
                    if not vals:
                        continue
                    new_vals = [REDACTED if v not in (None, '', REDACTED) else v for v in vals]
                    if new_vals != vals:
                        log.changes[field] = new_vals
                        dirty = True
                if dirty:
                    changed += 1
                    if not options['dry_run']:
                        log.save(update_fields=['changes'])
        verb = "Would redact" if options['dry_run'] else "Redacted"
        self.stdout.write(self.style.SUCCESS(f"{verb} passwords in {changed} audit log entr{'y' if changed == 1 else 'ies'}."))
