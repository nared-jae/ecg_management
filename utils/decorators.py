from functools import wraps
from flask import abort, redirect, url_for
from flask_login import current_user


def roles_required(*roles):
    """Restrict a view to users whose role is in the given list."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def nurse_required(f):
    return roles_required("nurse", "admin")(f)


def doctor_required(f):
    return roles_required("doctor", "cardio", "admin")(f)
