import contextvars
from django.db.models import Max
from .models import AuditLog

_current_user = contextvars.ContextVar('current_user', default=None)
_disable_audit_log = contextvars.ContextVar('disable_audit_log', default=False)

def get_current_user():
    return _current_user.get()

def set_current_user(user):
    _current_user.set(user)

def is_audit_log_disabled():
    return _disable_audit_log.get()

def set_audit_log_disabled(disabled):
    _disable_audit_log.set(disabled)

class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user and request.user.is_authenticated:
            set_current_user(request.user)
        else:
            set_current_user(None)
        
        # Track maximum audit log ID before executing request
        max_log_id = 0
        is_post = (request.method == 'POST')
        
        if is_post:
            max_log_id = AuditLog.objects.aggregate(max_id=Max('id'))['max_id'] or 0
        
        response = self.get_response(request)
        
        # After executing, if it was a POST request and audit logging wasn't disabled, check for new logs
        if is_post and not is_audit_log_disabled() and request.user and request.user.is_authenticated:
            new_logs = list(AuditLog.objects.filter(id__gt=max_log_id).order_by('id'))
            if new_logs:
                log_ids = [log.id for log in new_logs]
                
                # Fetch stacks from session
                undo_stack = request.session.get('undo_stack', [])
                
                # Push log IDs group to undo stack (limit to last 20 actions)
                undo_stack.append(log_ids)
                if len(undo_stack) > 20:
                    undo_stack.pop(0)
                    
                request.session['undo_stack'] = undo_stack
                request.session['redo_stack'] = [] # Clear redo stack on new action
                request.session.modified = True
                
        return response

