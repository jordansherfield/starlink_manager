from django.db.models import Q
from tracker.models import AuditLog, Client

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
