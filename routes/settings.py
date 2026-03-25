import os
import uuid
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, current_app,
)
from flask_login import login_required, current_user

from models import db, SystemSetting, User, AssignmentLog, ECGResult, Notification, get_setting
from utils.decorators import roles_required

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


# ---------------------------------------------------------------------------
# Main page (tabbed)
# ---------------------------------------------------------------------------
@settings_bp.route("/", methods=["GET", "POST"])
@login_required
@roles_required("admin", "it_admin", "nurse")
def index():
    if request.method == "POST":
        # Only allow updating known settings, not API key via form injection
        _protected_keys = {"api_key"}

        for key, value in request.form.items():
            if key in _protected_keys:
                continue
            s = SystemSetting.query.filter_by(key=key).first()
            if s:
                s.value = value.strip()
                s.updated_at = datetime.now()
                s.updated_by_id = current_user.id
        db.session.commit()
        flash("Settings saved successfully. | บันทึกการตั้งค่าเรียบร้อยแล้ว", "success")
        return redirect(url_for("settings.index"))

    # Keys managed by other tabs/sections — exclude from General tab
    _excluded_keys = {
        "ext_mwl_host", "ext_mwl_port", "ext_mwl_ae", "ext_mwl_local_ae",
        "ext_mwl_auto_sync", "ext_mwl_sync_interval", "ext_mwl_last_sync_at",
        "pacs_host", "pacs_port", "pacs_ae", "pacs_local_ae",
        "scp_mwl_ae_title", "scp_mwl_port",
        "scp_store_ae_title", "scp_store_port", "scp_storage_dir",
        "api_key",
        "export_pdf_path", "export_hl7_path",
    }
    all_settings = SystemSetting.query.order_by(SystemSetting.key).all()
    general_settings = [s for s in all_settings if s.key not in _excluded_keys]

    dicom_config = {
        "MWL_AE_TITLE": current_app.config.get("MWL_AE_TITLE", "MWL"),
        "MWL_PORT": current_app.config.get("MWL_PORT", 6701),
        "STORE_AE_TITLE": current_app.config.get("STORE_AE_TITLE", "ECG_STORE"),
        "STORE_PORT": current_app.config.get("STORE_PORT", 6702),
        "DICOM_STORAGE_DIR": current_app.config.get("DICOM_STORAGE_DIR", ""),
    }

    all_roles = ["admin", "doctor", "nurse", "it_admin", "viewer"]

    # DICOM settings for tab forms (SCP + SCU)
    dicom_keys = [
        # SCP settings (require restart)
        "scp_mwl_ae_title", "scp_mwl_port",
        "scp_store_ae_title", "scp_store_port", "scp_storage_dir",
        # SCU settings (no restart needed)
        "ext_mwl_host", "ext_mwl_port", "ext_mwl_ae", "ext_mwl_local_ae",
        "ext_mwl_auto_sync", "ext_mwl_sync_interval", "ext_mwl_last_sync_at",
        "pacs_host", "pacs_port", "pacs_ae", "pacs_local_ae",
    ]
    setting_values = {k: get_setting(k, "") for k in dicom_keys}

    export_settings = {
        "export_pdf_path": get_setting("export_pdf_path", ""),
        "export_hl7_path": get_setting("export_hl7_path", ""),
    }

    return render_template(
        "settings/index.html",
        settings=general_settings,
        dicom_config=dicom_config,
        all_roles=all_roles,
        setting_values=setting_values,
        export_settings=export_settings,
    )


# ---------------------------------------------------------------------------
# User Management API
# ---------------------------------------------------------------------------
@settings_bp.route("/api/users")
@login_required
@roles_required("admin", "it_admin")
def api_users():
    """DataTables server-side JSON for user list."""
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    query = User.query
    total = query.count()

    if search_value:
        like = f"%{search_value}%"
        query = query.filter(
            db.or_(
                User.username.ilike(like),
                User.display_name.ilike(like),
                User.role.ilike(like),
            )
        )

    filtered = query.count()

    order_col = request.args.get("order[0][column]", "0", type=int)
    order_dir = request.args.get("order[0][dir]", "asc")
    col_map = {
        0: User.username,
        1: User.display_name,
        2: User.role,
        3: User.is_active_user,
        4: User.created_at,
    }
    order_column = col_map.get(order_col, User.username)
    if order_dir == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    users = query.offset(start).limit(length).all()

    data = [{
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active_user,
        "created_at": u.created_at.strftime("%d/%m/%Y %H:%M") if u.created_at else "-",
    } for u in users]

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


@settings_bp.route("/api/user/<int:user_id>")
@login_required
@roles_required("admin", "it_admin")
def get_user(user_id):
    """Single user JSON for edit modal."""
    user = User.query.get_or_404(user_id)
    return jsonify({
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "display_name_en": user.display_name_en or "",
        "role": user.role,
        "is_active": user.is_active_user,
        "can_be_assigned": user.can_be_assigned,
    })


@settings_bp.route("/users/create", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def create_user():
    """Create a new user."""
    username = request.form.get("username", "").strip()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "user").strip()

    if not username or not display_name or not password:
        return jsonify({"success": False, "error": "All fields are required | กรุณากรอกข้อมูลให้ครบ"}), 400

    if len(password) < 4:
        return jsonify({"success": False, "error": "Password must be at least 4 characters | รหัสผ่านต้องมีอย่างน้อย 4 ตัวอักษร"}), 400

    valid_roles = ["admin", "doctor", "nurse", "it_admin", "viewer", "user"]
    if role not in valid_roles:
        return jsonify({"success": False, "error": "Invalid role"}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"success": False, "error": "Username already exists | ชื่อผู้ใช้นี้มีอยู่แล้ว"}), 409

    display_name_en = request.form.get("display_name_en", "").strip()
    can_be_assigned = request.form.get("can_be_assigned") == "1"
    user = User(username=username, display_name=display_name, display_name_en=display_name_en or None, role=role, can_be_assigned=can_be_assigned)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({"success": True, "message": f"User {username} created | สร้างผู้ใช้ {username} สำเร็จ"})


@settings_bp.route("/users/<int:user_id>/edit", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def edit_user(user_id):
    """Edit an existing user."""
    user = User.query.get_or_404(user_id)
    display_name = request.form.get("display_name", "").strip()
    role = request.form.get("role", "").strip()
    password = request.form.get("password", "").strip()

    if not display_name:
        return jsonify({"success": False, "error": "Display name is required | กรุณากรอกชื่อที่แสดง"}), 400

    if user.id == current_user.id and role and role != user.role:
        return jsonify({"success": False, "error": "Cannot change your own role | ไม่สามารถเปลี่ยน role ของตัวเองได้"}), 400

    valid_roles = ["admin", "doctor", "nurse", "it_admin", "viewer", "user"]
    if role and role not in valid_roles:
        return jsonify({"success": False, "error": "Invalid role"}), 400

    display_name_en = request.form.get("display_name_en", "").strip()
    user.display_name = display_name
    user.display_name_en = display_name_en or None
    user.can_be_assigned = request.form.get("can_be_assigned") == "1"
    if role:
        user.role = role
    if password:
        if len(password) < 4:
            return jsonify({"success": False, "error": "Password must be at least 4 characters | รหัสผ่านต้องมีอย่างน้อย 4 ตัวอักษร"}), 400
        user.set_password(password)

    db.session.commit()
    return jsonify({"success": True, "message": f"User {user.username} updated | อัปเดตผู้ใช้ {user.username} สำเร็จ"})


@settings_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def toggle_active(user_id):
    """Toggle user active status."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        return jsonify({"success": False, "error": "Cannot deactivate yourself | ไม่สามารถปิดใช้งานตัวเองได้"}), 400

    user.is_active_user = not user.is_active_user
    db.session.commit()

    return jsonify({"success": True, "is_active": user.is_active_user})


@settings_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def delete_user(user_id):
    """Delete a user (soft-delete if FK references exist)."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        return jsonify({"success": False, "error": "Cannot delete yourself | ไม่สามารถลบตัวเองได้"}), 400

    # Check FK references
    has_refs = (
        ECGResult.query.filter(
            db.or_(ECGResult.assigned_to_id == user_id, ECGResult.locked_by_id == user_id)
        ).first()
        or AssignmentLog.query.filter(
            db.or_(AssignmentLog.actor_id == user_id, AssignmentLog.target_id == user_id)
        ).first()
    )

    if has_refs:
        user.is_active_user = False
        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"User {user.username} deactivated (has related records) | ปิดใช้งานผู้ใช้ {user.username} แล้ว (มีข้อมูลที่เกี่ยวข้อง)",
            "soft_delete": True,
        })

    Notification.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()

    return jsonify({"success": True, "message": f"User {user.username} deleted | ลบผู้ใช้ {user.username} สำเร็จ"})


# ---------------------------------------------------------------------------
# Audit Trail API
# ---------------------------------------------------------------------------
@settings_bp.route("/api/audit")
@login_required
@roles_required("admin", "it_admin")
def api_audit():
    """DataTables server-side JSON for audit trail."""
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    action_filter = request.args.get("action", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    actor_filter = request.args.get("actor_id", "").strip()

    query = AssignmentLog.query

    if action_filter:
        query = query.filter(AssignmentLog.action == action_filter)

    if date_from:
        try:
            query = query.filter(AssignmentLog.timestamp >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass

    if date_to:
        try:
            query = query.filter(
                AssignmentLog.timestamp < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            )
        except ValueError:
            pass

    if actor_filter:
        try:
            query = query.filter(AssignmentLog.actor_id == int(actor_filter))
        except ValueError:
            pass

    total = AssignmentLog.query.count()

    if search_value:
        like = f"%{search_value}%"
        query = query.join(ECGResult, AssignmentLog.ecg_result_id == ECGResult.id, isouter=True)
        query = query.filter(
            db.or_(
                AssignmentLog.action.ilike(like),
                AssignmentLog.notes.ilike(like),
                ECGResult.accession_number.ilike(like),
            )
        )

    filtered = query.count()

    order_col = request.args.get("order[0][column]", "0", type=int)
    order_dir = request.args.get("order[0][dir]", "desc")
    col_map = {
        0: AssignmentLog.timestamp,
        1: AssignmentLog.action,
    }
    order_column = col_map.get(order_col, AssignmentLog.timestamp)
    if order_dir == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    logs = query.offset(start).limit(length).all()

    data = []
    for log in logs:
        data.append({
            "id": log.id,
            "timestamp": log.timestamp.strftime("%d/%m/%Y %H:%M:%S") if log.timestamp else "-",
            "action": log.action,
            "actor_name": log.actor.display_name if log.actor else "-",
            "target_name": log.target.display_name if log.target else "-",
            "ecg_accession": log.ecg_result.accession_number if log.ecg_result else "-",
            "ecg_result_id": log.ecg_result_id,
            "notes": log.notes or "",
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


# ---------------------------------------------------------------------------
# DICOM Integration API (MWL SCU + PACS Store SCU)
# ---------------------------------------------------------------------------
@settings_bp.route("/api/dicom/test-mwl", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def test_mwl():
    """Test connection to external MWL server."""
    data = request.get_json(silent=True) or {}
    host = data.get("host", "").strip()
    port = int(data.get("port", 104))
    remote_ae = data.get("remote_ae", "MWL").strip()
    local_ae = data.get("local_ae", "ECG_SCU").strip()

    from services.mwl_scu import test_mwl_connection
    success, message = test_mwl_connection(host, port, remote_ae, local_ae)
    return jsonify({"success": success, "message": message})


@settings_bp.route("/api/dicom/test-pacs", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def test_pacs():
    """Test connection to PACS server via C-ECHO."""
    data = request.get_json(silent=True) or {}
    host = data.get("host", "").strip()
    port = int(data.get("port", 104))
    remote_ae = data.get("remote_ae", "PACS").strip()
    local_ae = data.get("local_ae", "ECG_SCU").strip()

    from services.store_scu import test_pacs_connection
    success, message = test_pacs_connection(host, port, remote_ae, local_ae)
    return jsonify({"success": success, "message": message})


@settings_bp.route("/api/dicom/sync-mwl", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def sync_mwl():
    """Trigger immediate MWL synchronization."""
    from services.mwl_scu import sync_from_external_mwl
    result = sync_from_external_mwl(current_app._get_current_object())
    return jsonify(result)


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------
@settings_bp.route("/api-key")
@login_required
@roles_required("admin", "it_admin")
def get_api_key():
    """Get current API key."""
    key = get_setting("api_key", "")
    return jsonify({"success": True, "api_key": key})


@settings_bp.route("/api-key/regenerate", methods=["POST"])
@login_required
@roles_required("admin", "it_admin")
def regenerate_api_key():
    """Generate a new API key (invalidates the old one)."""
    new_key = str(uuid.uuid4())
    s = SystemSetting.query.filter_by(key="api_key").first()
    if s:
        s.value = new_key
        s.updated_at = datetime.now()
        s.updated_by_id = current_user.id
    else:
        db.session.add(SystemSetting(
            key="api_key", value=new_key,
            label="API Key",
            description="API Key for external system integration (HIS)",
        ))
    db.session.commit()
    return jsonify({"success": True, "api_key": new_key, "message": "API key regenerated | สร้าง API Key ใหม่สำเร็จ"})


# ---------------------------------------------------------------------------
# Log Viewer API
# ---------------------------------------------------------------------------
@settings_bp.route("/api/logs/mwl-sync")
@login_required
@roles_required("admin", "it_admin")
def view_mwl_log():
    """Return the MWL sync log file contents (last N lines)."""
    lines = request.args.get("lines", 200, type=int)
    lines = min(lines, 2000)  # cap at 2000

    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "mwl_sync.log")
    if not os.path.exists(log_path):
        return jsonify({"success": True, "content": "(log file not found)", "lines": 0})

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return jsonify({
            "success": True,
            "content": "".join(tail),
            "total_lines": len(all_lines),
            "showing": len(tail),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
