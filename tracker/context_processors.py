from django.db.models import Q
from tracker.models import AuditLog, Client, StarlinkDevice


def kit_form_choices(request):
    """Model/status choices for the Add Kit modal included on every page."""
    return {
        'kit_model_choices': StarlinkDevice.MODEL_CHOICES,
        'kit_status_choices': StarlinkDevice.STATUS_CHOICES,
    }

def page_audit_logs(request):
    if not request.user or not request.user.is_authenticated:
        return {}

    resolver_match = getattr(request, 'resolver_match', None)
    logs = AuditLog.objects.all()

    if resolver_match:
        if resolver_match.url_name == 'client_detail':
            client_pk = resolver_match.kwargs.get('pk')
            if client_pk:
                try:
                    client = Client.objects.get(pk=client_pk)
                    kits = list(client.starlinks.values_list('kit_number', flat=True))
                    logs = logs.filter(
                        Q(model_name='Client', object_id=client_pk) |
                        Q(kit_number__in=kits)
                    )
                except Client.DoesNotExist:
                    pass
        elif resolver_match.url_name == 'stats_dashboard':
            # Highlight billing, accounts, and credentials
            logs = logs.filter(Q(model_name='StarlinkAccount') | Q(model_name='Credential'))

    # Limit to 10 most recent logs for page drawer
    return {'page_audit_logs': logs[:10]}


def starlink_inbox_count(request):
    """Number of new-from-Starlink units waiting for a client, for the sidebar badge."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    count = (StarlinkDevice.objects.filter(is_archived=False, client__isnull=True)
             .exclude(service_line_number__isnull=True).exclude(service_line_number='').count())
    return {'starlink_inbox_count': count}
