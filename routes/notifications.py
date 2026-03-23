from flask import Blueprint, jsonify
from flask_login import current_user, login_required
from flask_socketio import join_room

from extensions import socketio
from models import db, Notification

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")

ASSIGNMENT_TIMEOUT_MINUTES = 30


# ─── HTTP Routes ───────────────────────────────────────────────

@notifications_bp.route("/read/<int:notif_id>", methods=["POST"])
@login_required
def mark_read(notif_id):
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first_or_404()
    notif.is_read = True
    db.session.commit()
    return jsonify({"success": True})


@notifications_bp.route("/read-all", methods=["POST"])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"success": True})


@notifications_bp.route("/api/unread")
@login_required
def api_unread():
    notifs = (
        Notification.query
        .filter_by(user_id=current_user.id, is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([{
        "id": n.id,
        "message": n.message,
        "message_th": n.message_th or n.message,
        "type": n.type,
        "related_result_id": n.related_result_id,
        "created_at": n.created_at.strftime("%d/%m/%Y %H:%M"),
    } for n in notifs])


# ─── SocketIO Events ───────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    print(f"[SocketIO] connect: authenticated={current_user.is_authenticated}, id={getattr(current_user, 'id', None)}")
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")
        join_room(f"role_{current_user.role}")
        print(f"[SocketIO] user_{current_user.id} joined rooms: user_{current_user.id}, role_{current_user.role}")


# ─── Helper ────────────────────────────────────────────────────

def push_notification(user_id: int, message: str, message_th: str,
                      notif_type: str, result_id: int = None):
    """
    Persist a Notification row and emit a real-time SocketIO event
    to the target user's room. Safe to call from background jobs.
    """
    notif = Notification(
        user_id=user_id,
        message=message,
        message_th=message_th,
        type=notif_type,
        related_result_id=result_id,
    )
    db.session.add(notif)
    db.session.commit()

    try:
        socketio.emit("notification", {
            "id": notif.id,
            "message": message,
            "message_th": message_th,
            "type": notif_type,
            "related_result_id": result_id,
            "created_at": notif.created_at.strftime("%d/%m/%Y %H:%M"),
        }, room=f"user_{user_id}", namespace="/")
        print(f"[SocketIO] emitted to user_{user_id}")
    except Exception as e:
        print(f"[SocketIO] emit failed for user_{user_id}: {e}")


def push_broadcast_to_roles(roles: list, message: str, message_th: str,
                            notif_type: str, result_id: int = None,
                            persist: bool = True):
    """Emit a SocketIO event to all users in the given roles.
    Uses role-based rooms (role_nurse, role_admin, etc.).
    When persist=True, also saves Notification rows so they appear in the bell."""
    from models import User

    if persist:
        # Persist notification for each user in target roles
        users = User.query.filter(User.role.in_(roles)).all()
        for u in users:
            notif = Notification(
                user_id=u.id,
                message=message,
                message_th=message_th,
                type=notif_type,
                related_result_id=result_id,
            )
            db.session.add(notif)
        db.session.commit()

    payload = {
        "id": 0,
        "message": message,
        "message_th": message_th,
        "type": notif_type,
        "related_result_id": result_id,
        "created_at": "",
    }
    for role in roles:
        try:
            socketio.emit("notification", payload, room=f"role_{role}", namespace="/")
            print(f"[SocketIO] broadcast to role_{role}: {notif_type}")
        except Exception as e:
            print(f"[SocketIO] broadcast to role_{role} failed: {e}")


def push_case_unassigned(user_id: int, result_id: int, message: str, message_th: str):
    """Emit a special SocketIO event telling the doctor's detail page
    that their case has been unassigned/reassigned. The detail page JS
    listens for this and shows a modal + redirects."""
    try:
        socketio.emit("case_unassigned", {
            "result_id": result_id,
            "message": message,
            "message_th": message_th,
        }, room=f"user_{user_id}", namespace="/")
        print(f"[SocketIO] case_unassigned emitted to user_{user_id} for result {result_id}")
    except Exception as e:
        print(f"[SocketIO] case_unassigned emit failed for user_{user_id}: {e}")
