import contextvars

_current_user = contextvars.ContextVar('current_user', default=None)

def get_current_user():
    return _current_user.get()

def set_current_user(user):
    _current_user.set(user)

class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user and request.user.is_authenticated:
            set_current_user(request.user)
        else:
            set_current_user(None)
        
        response = self.get_response(request)
        return response
