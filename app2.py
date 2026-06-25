import streamlit as st
from streamlit_autorefresh import st_autorefresh
import math
import json
import os
import sqlite3
import time
from datetime import date, datetime

WORKOUTS_FILE = "workouts.json"
TEMPLATES_FILE = "workout_templates.json"
EXERCISE_LIBRARY_FILE = "exercise_library.json"
WORKOUT_PLAN_FILE = "workout_plan.json"
DATABASE_FILE = "workout_app.db"
BACKUPS_DIR = "backups"

# --- Database backend selection ---
# "sqlite" is the only supported backend today. This switch exists so a hosted
# backend (e.g. Supabase/Postgres) can be added later WITHOUT changing the
# persistence boundary functions (load_*/save_*) or any UI code.
DATABASE_BACKEND = "sqlite"

# Temporary single-user id until authentication is added.
DEFAULT_USER_ID = "local_user"

# Set ENABLE_AUTH to True when you are ready to use Streamlit OIDC login.
# False = single-user local app using DEFAULT_USER_ID (no secrets.toml required).
ENABLE_AUTH = False

# Development only — set to True to test multiple user ids without OIDC.
ENABLE_LOCAL_USER_SWITCHER = False

DEFAULT_WORKOUT_PLAN = {
    "plan_name": "Current Program",
    "schedule_mode": "weekly",
    "weekly_schedule": {
        "Monday": "",
        "Tuesday": "",
        "Wednesday": "",
        "Thursday": "",
        "Friday": "",
        "Saturday": "",
        "Sunday": "",
    },
}


WORKOUT_DB_COLUMNS = [
    "date",
    "workout_name",
    "exercise",
    "set_number",
    "weight",
    "reps",
    "estimated_1rm",
    "volume",
    "notes",
    "target_weight",
    "target_reps",
    "target_estimated_1rm",
    "logged_from",
    "planned_exercise",
    "swapped_from",
    "swapped_to",
    "session_id",
]

TEXT_WORKOUT_DB_COLUMNS = [
    "date",
    "workout_name",
    "exercise",
    "notes",
    "logged_from",
    "planned_exercise",
    "swapped_from",
    "swapped_to",
    "session_id",
]


def get_database_backend():
    """
    Return the active database backend name.

    For now this always returns "sqlite", so local development is unchanged.
    Later this can read from st.secrets or an environment variable to switch to
    a hosted Postgres backend, without touching any UI or persistence boundary
    function. Example future logic:

        return st.secrets.get("database", {}).get("backend", DATABASE_BACKEND)
    """
    return DATABASE_BACKEND


def is_auth_configured():
    """
    Return True when Streamlit OIDC auth secrets are present.

    This check is safe for local development where secrets.toml may be missing.
    """
    try:
        if "auth" not in st.secrets:
            return False

        auth_settings = st.secrets["auth"]
        redirect_uri = auth_settings.get("redirect_uri", "")
        cookie_secret = auth_settings.get("cookie_secret", "")

        if not redirect_uri or not cookie_secret:
            return False

        # Named provider sections look like [auth.google], [auth.microsoft], etc.
        provider_keys = {"redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"}
        for key, value in dict(auth_settings).items():
            if key not in provider_keys and isinstance(value, dict):
                if value.get("client_id") and value.get("server_metadata_url"):
                    return True

        # Single-provider config can also live directly under [auth].
        if auth_settings.get("client_id") and auth_settings.get("server_metadata_url"):
            return True

        return False
    except Exception:
        return False


def safe_user_is_logged_in():
    """
    Return True only when Streamlit OIDC auth is active and the user is logged in.

    Safe to call even when auth is not configured in secrets.toml — returns False
    instead of raising AttributeError. When ENABLE_AUTH is False, never touches st.user.
    """
    if not ENABLE_AUTH:
        return False

    try:
        return bool(getattr(st.user, "is_logged_in", False))
    except Exception:
        return False


def get_logged_in_user_id():
    """
    Return the stable user id from Streamlit auth when logged in.

    Prefer email, then sub, then DEFAULT_USER_ID. Never crashes if attributes are missing.
    When ENABLE_AUTH is False, returns DEFAULT_USER_ID without touching st.user.
    """
    if not ENABLE_AUTH:
        return DEFAULT_USER_ID

    if not safe_user_is_logged_in():
        return DEFAULT_USER_ID

    email = getattr(st.user, "email", None)
    if email:
        return email

    sub = getattr(st.user, "sub", None)
    if sub:
        return sub

    return DEFAULT_USER_ID


def get_logged_in_display_name():
    """Return a friendly label for the current user near the top of the app."""
    if ENABLE_AUTH and safe_user_is_logged_in():
        return get_logged_in_user_id()
    return get_current_user_id()


def get_current_user_id():
    """
    Return the active user id for user-scoped app data.

    Priority: real login (email or sub) → dev switcher → DEFAULT_USER_ID.
    st.login() proves identity; user_id filtering controls data ownership.
    """
    if ENABLE_AUTH and safe_user_is_logged_in():
        return get_logged_in_user_id()

    if ENABLE_LOCAL_USER_SWITCHER:
        # Development only — do not ship this to production.
        return st.session_state.get("dev_user_id", DEFAULT_USER_ID)

    return DEFAULT_USER_ID


def clear_user_specific_session_state():
    """Clear in-memory workout UI state when the active user changes."""
    st.session_state.active_workout_plan = None
    st.session_state.completed_recommended_sets = []
    st.session_state.todays_session_added_exercises = []
    st.session_state.pending_manual_log_submission = None
    st.session_state.pending_manual_log_warnings = []
    st.session_state.edit_workout_entry_index = None
    st.session_state.delete_set_index = None
    st.session_state.delete_exercise_info = None


def require_login():
    """
    Gate the app behind Streamlit OIDC login when auth is enabled.

    If auth secrets are missing locally, fall back to DEFAULT_USER_ID instead of
    blocking development.
    """
    if not ENABLE_AUTH:
        return

    if not is_auth_configured():
        st.warning("Auth is enabled but not configured. Using local_user.")
        return

    if not safe_user_is_logged_in():
        st.title("Workout Programming App")
        st.write("Log in to use your workout app.")
        if st.button("Log in", key="login_button"):
            st.login()
        st.stop()

    user_label_col, logout_col = st.columns([5, 1])
    with user_label_col:
        st.caption(f"Logged in as **{get_logged_in_display_name()}**")
    with logout_col:
        if st.button("Log out", key="logout_button"):
            st.logout()


# =============================================================================
# SQLite backend layer
# -----------------------------------------------------------------------------
# Everything in this section is SQLite-specific. These functions are named with
# a "_sqlite" suffix (or open sqlite3 connections directly) so they are easy to
# spot and easy to replace when adding a hosted backend later.
#
# The app does NOT call these directly. Instead it calls the backend-neutral
# persistence boundary functions further below (load_*_for_user / save_*_for_user
# and the no-argument load_*/save_* wrappers), which route to the active backend
# returned by get_database_backend().
# =============================================================================
def _ensure_workouts_user_id_column(connection):
    """Add user_id to older workout tables and backfill existing rows."""
    cursor = connection.execute("PRAGMA table_info(workouts)")
    column_names = [row[1] for row in cursor.fetchall()]

    if "user_id" not in column_names:
        connection.execute(
            """
            ALTER TABLE workouts
            ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local_user'
            """
        )
        connection.execute(
            "UPDATE workouts SET user_id = ? WHERE user_id IS NULL OR user_id = ''",
            (DEFAULT_USER_ID,),
        )


def initialize_database():
    """Create local SQLite tables for user-scoped workout and exercise data."""
    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'local_user',
                date TEXT,
                workout_name TEXT,
                exercise TEXT,
                set_number INTEGER,
                weight INTEGER,
                reps INTEGER,
                estimated_1rm INTEGER,
                volume INTEGER,
                notes TEXT,
                target_weight INTEGER,
                target_reps INTEGER,
                target_estimated_1rm INTEGER,
                logged_from TEXT,
                planned_exercise TEXT,
                swapped_from TEXT,
                swapped_to TEXT,
                session_id TEXT,
                extra_json TEXT
            )
            """
        )
        _ensure_workouts_user_id_column(connection)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_workouts_user_id ON workouts (user_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exercise_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT,
                primary_muscle TEXT,
                default_sets INTEGER,
                rep_min INTEGER,
                rep_max INTEGER,
                weight_increment INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_exercise_library_user_name
            ON exercise_library (user_id, name)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workout_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                template_name TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workout_template_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                exercise_name TEXT NOT NULL,
                position INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workout_templates_user_name
            ON workout_templates (user_id, template_name)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workout_plans (
                user_id TEXT PRIMARY KEY,
                plan_name TEXT,
                schedule_mode TEXT,
                monday TEXT,
                tuesday TEXT,
                wednesday TEXT,
                thursday TEXT,
                friday TEXT,
                saturday TEXT,
                sunday TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                skipped_onboarding INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
            """
        )


WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def get_workout_table_count(user_id=None):
    """Return the number of workout rows stored in SQLite."""
    initialize_database()
    with sqlite3.connect(DATABASE_FILE) as connection:
        if user_id is None:
            cursor = connection.execute("SELECT COUNT(*) FROM workouts")
        else:
            cursor = connection.execute(
                "SELECT COUNT(*) FROM workouts WHERE user_id = ?",
                (user_id,),
            )
        return cursor.fetchone()[0]


def backup_workouts_json_for_migration():
    """Save a copy of workouts.json before importing it into SQLite."""
    os.makedirs(BACKUPS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    backup_filename = f"workouts_backup_{timestamp}_before_sqlite_migration.json"
    backup_path = os.path.join(BACKUPS_DIR, backup_filename)

    with open(WORKOUTS_FILE, "r") as source_file:
        backup_content = source_file.read()

    with open(backup_path, "w") as backup_file:
        backup_file.write(backup_content)


def clean_integer_value(value):
    """Convert a stored workout value to an integer when possible."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def prepare_workout_row(entry):
    """
    Split one workout dictionary into known SQLite columns and extra JSON.

    This keeps old/future optional fields instead of dropping them when the app
    saves the full workout list back into SQLite.
    """
    row = {}

    for column_name in WORKOUT_DB_COLUMNS:
        value = entry.get(column_name)
        if column_name in TEXT_WORKOUT_DB_COLUMNS:
            row[column_name] = "" if value is None else str(value)
        elif value is None or value == "":
            row[column_name] = None
        else:
            row[column_name] = clean_integer_value(value)

    extra_fields = {}
    for key, value in entry.items():
        if key not in WORKOUT_DB_COLUMNS and key not in ["id", "extra_json", "user_id"]:
            extra_fields[key] = value

    row["extra_json"] = json.dumps(extra_fields) if extra_fields else ""
    return row


def workout_entry_from_row(row):
    """Convert one SQLite row back into the dictionary shape the app expects."""
    entry = {}

    for column_name in WORKOUT_DB_COLUMNS:
        value = row[column_name]
        if value is not None and value != "":
            entry[column_name] = value

    extra_json = row["extra_json"]
    if extra_json:
        try:
            extra_fields = json.loads(extra_json)
            if isinstance(extra_fields, dict):
                entry.update(extra_fields)
        except json.JSONDecodeError:
            entry["extra_json"] = extra_json

    return entry


def migrate_workouts_json_to_sqlite_if_needed():
    """
    Import existing workouts.json history once, only when the SQLite table is empty.

    Templates, exercise library, and workout plan remain JSON files for now.
    """
    initialize_database()

    user_id = get_current_user_id()
    if user_id != DEFAULT_USER_ID:
        return

    if get_workout_table_count(user_id) > 0:
        return

    if not os.path.exists(WORKOUTS_FILE):
        return

    try:
        with open(WORKOUTS_FILE, "r") as file:
            content = file.read().strip()

        if content == "":
            return

        workout_entries = json.loads(content)
    except json.JSONDecodeError:
        st.warning(f"Could not migrate {WORKOUTS_FILE} because it contains invalid JSON.")
        return

    if not isinstance(workout_entries, list):
        return

    valid_entries = []
    for entry in workout_entries:
        if isinstance(entry, dict):
            valid_entries.append(entry)

    if not valid_entries:
        return

    try:
        backup_workouts_json_for_migration()
        save_workouts_for_user(valid_entries, user_id)
        st.success(f"Migrated {len(valid_entries)} workout history entries to SQLite.")
    except OSError as error:
        st.warning(f"Could not back up {WORKOUTS_FILE} before migration: {error}")


def load_workouts_sqlite(user_id):
    """
    Read one user's workout history from SQLite.

    Callers that need auth later can pass the logged-in user id directly.
    """
    initialize_database()

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            "SELECT * FROM workouts WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        rows = cursor.fetchall()

    workouts = []
    for row in rows:
        workouts.append(workout_entry_from_row(row))

    return workouts


# --- Persistence boundary: workouts ---
# These route to the active backend. Today only SQLite is implemented.
def load_workouts_for_user(user_id):
    """Persistence boundary: read one user's workout history from the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        return load_workouts_sqlite(user_id)
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def load_workouts():
    """
    Read workout history for the current user.

    The rest of the app can keep calling load_workouts() like before, even
    though workouts.json is no longer the main workout-history store.
    """
    return load_workouts_for_user(get_current_user_id())


def save_workouts_sqlite(workouts, user_id):
    """
    Save one user's workout history to SQLite using the list-of-dicts interface.

    Only rows for this user_id are replaced, so other users' data stays intact.
    """
    initialize_database()

    insert_columns = ["user_id"] + list(WORKOUT_DB_COLUMNS) + ["extra_json"]
    placeholders = ", ".join(["?"] * len(insert_columns))
    column_names = ", ".join(insert_columns)

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute("DELETE FROM workouts WHERE user_id = ?", (user_id,))

        for entry in workouts:
            row = prepare_workout_row(entry)
            values = [user_id]
            for column_name in WORKOUT_DB_COLUMNS + ["extra_json"]:
                values.append(row[column_name])

            connection.execute(
                f"INSERT INTO workouts ({column_names}) VALUES ({placeholders})",
                values,
            )


def save_workouts_for_user(workouts, user_id):
    """Persistence boundary: save one user's workout history to the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        save_workouts_sqlite(workouts, user_id)
        return
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def save_workouts(workouts):
    """
    Save workout history to SQLite for the current user.

    This clears and rewrites that user's rows for now so edit/delete/undo code
    can keep working without a large refactor.
    """
    save_workouts_for_user(workouts, get_current_user_id())


def create_session_id(workout_name):
    """
    Create a readable unique ID for one workout session.

    Each logged set is stored separately, but session_id groups sets from the
    same workout so rotation, summaries, and deletes can treat a session as
    one unit instead of unrelated rows.
    """
    today_str = str(date.today())
    safe_name = workout_name.strip().replace(" ", "-")

    cleaned_name = ""
    for character in safe_name:
        if character.isalnum() or character in "-_":
            cleaned_name += character

    if cleaned_name == "":
        cleaned_name = "Workout"

    time_str = datetime.now().strftime("%H%M%S")
    return f"{today_str}_{cleaned_name}_{time_str}"


def format_session_id_caption(session_id):
    """Return a short session_id label for history cards."""
    if len(session_id) <= 30:
        return session_id
    return session_id[:30] + "..."


def create_workouts_backup(reason):
    """
    Save a JSON copy of the current SQLite workout history before an edit/delete.

    Backups are stored in backups/ so workout history can be recovered manually
    if something is changed or deleted by mistake.
    """
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        backup_content = json.dumps(load_workouts(), indent=2)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe_reason = reason.replace(" ", "_")
        backup_filename = f"workouts_backup_{timestamp}_{safe_reason}.json"
        backup_path = os.path.join(BACKUPS_DIR, backup_filename)

        with open(backup_path, "w") as backup_file:
            backup_file.write(backup_content)

        return True
    except OSError as error:
        st.warning(f"Could not create workout backup: {error}")
        return False


def load_templates_sqlite(user_id):
    """
    Read one user's workout templates from SQLite.

    Returns the same list-of-dicts shape the app used with workout_templates.json.
    Template ownership is controlled by user_id for future login support.
    """
    initialize_database()

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.row_factory = sqlite3.Row
        template_rows = connection.execute(
            """
            SELECT id, template_name
            FROM workout_templates
            WHERE user_id = ?
            ORDER BY template_name
            """,
            (user_id,),
        ).fetchall()

        templates = []
        for template_row in template_rows:
            exercise_rows = connection.execute(
                """
                SELECT exercise_name
                FROM workout_template_exercises
                WHERE template_id = ?
                ORDER BY position
                """,
                (template_row["id"],),
            ).fetchall()

            exercises = []
            for exercise_row in exercise_rows:
                exercises.append(exercise_row["exercise_name"])

            templates.append({
                "template_name": template_row["template_name"],
                "exercises": exercises,
            })

    return templates


# --- Persistence boundary: workout templates ---
def load_templates_for_user(user_id):
    """Persistence boundary: read one user's workout templates from the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        return load_templates_sqlite(user_id)
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def load_templates():
    """Read the current user's workout templates."""
    return load_templates_for_user(get_current_user_id())


def get_workout_templates_table_count(user_id):
    """Return how many templates one user has stored in SQLite."""
    initialize_database()
    with sqlite3.connect(DATABASE_FILE) as connection:
        cursor = connection.execute(
            "SELECT COUNT(*) FROM workout_templates WHERE user_id = ?",
            (user_id,),
        )
        return cursor.fetchone()[0]


def save_templates_sqlite(templates, user_id):
    """
    Save one user's workout templates to SQLite.

    Only this user's templates and related exercise rows are replaced.
    """
    initialize_database()
    timestamp = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(DATABASE_FILE) as connection:
        template_ids = connection.execute(
            "SELECT id FROM workout_templates WHERE user_id = ?",
            (user_id,),
        ).fetchall()

        for template_row in template_ids:
            connection.execute(
                "DELETE FROM workout_template_exercises WHERE template_id = ?",
                (template_row[0],),
            )

        connection.execute(
            "DELETE FROM workout_templates WHERE user_id = ?",
            (user_id,),
        )

        for template in templates:
            template_name = template.get("template_name", "").strip()
            if not template_name:
                continue

            cursor = connection.execute(
                """
                INSERT INTO workout_templates (
                    user_id,
                    template_name,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (user_id, template_name, timestamp, timestamp),
            )
            template_id = cursor.lastrowid

            for position, exercise_name in enumerate(template.get("exercises", [])):
                exercise_name = str(exercise_name).strip()
                if not exercise_name:
                    continue

                connection.execute(
                    """
                    INSERT INTO workout_template_exercises (
                        template_id,
                        exercise_name,
                        position
                    ) VALUES (?, ?, ?)
                    """,
                    (template_id, exercise_name, position),
                )


def save_templates_for_user(templates, user_id):
    """Persistence boundary: save one user's workout templates to the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        save_templates_sqlite(templates, user_id)
        return
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def save_templates(templates):
    """Save the current user's workout templates."""
    save_templates_for_user(templates, get_current_user_id())


def migrate_workout_templates_json_to_sqlite_if_needed():
    """
    Import workout_templates.json once for the current user when SQLite is empty.

    The JSON file is kept on disk as a backup/reference. Exercise order is stored
    in the position column.
    """
    initialize_database()

    user_id = get_current_user_id()
    if user_id != DEFAULT_USER_ID:
        return

    if get_workout_templates_table_count(user_id) > 0:
        return

    if not os.path.exists(TEMPLATES_FILE):
        return

    try:
        with open(TEMPLATES_FILE, "r") as file:
            content = file.read().strip()

        if content == "":
            return

        template_entries = json.loads(content)
    except json.JSONDecodeError:
        st.warning(
            f"Could not migrate {TEMPLATES_FILE} because it contains invalid JSON."
        )
        return

    if not isinstance(template_entries, list):
        return

    valid_entries = []
    for entry in template_entries:
        if isinstance(entry, dict) and entry.get("template_name", "").strip():
            valid_entries.append(entry)

    if not valid_entries:
        return

    save_templates_for_user(valid_entries, user_id)
    st.success(
        f"Migrated {len(valid_entries)} workout templates to SQLite for {user_id}."
    )


def load_workout_plan_sqlite(user_id):
    """
    Read one user's weekly workout plan from SQLite.

    Returns the same dictionary shape the app used with workout_plan.json.
    Plan ownership is controlled by user_id for future login support.
    """
    initialize_database()

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM workout_plans WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if row is None:
        return dict(DEFAULT_WORKOUT_PLAN)

    weekly_schedule = {}
    for day_name in WEEKDAY_NAMES:
        column_name = day_name.lower()
        weekly_schedule[day_name] = row[column_name] or ""

    plan = {
        "plan_name": row["plan_name"] or DEFAULT_WORKOUT_PLAN["plan_name"],
        "schedule_mode": row["schedule_mode"] or "weekly",
        "weekly_schedule": weekly_schedule,
    }
    return plan


# --- Persistence boundary: weekly workout plan ---
def load_workout_plan_for_user(user_id):
    """Persistence boundary: read one user's weekly workout plan from the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        return load_workout_plan_sqlite(user_id)
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def load_workout_plan():
    """Read the current user's weekly workout plan."""
    return load_workout_plan_for_user(get_current_user_id())


def user_has_workout_plan(user_id):
    """Return True if this user already has a plan row in SQLite."""
    initialize_database()
    with sqlite3.connect(DATABASE_FILE) as connection:
        cursor = connection.execute(
            "SELECT 1 FROM workout_plans WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        return cursor.fetchone() is not None


def save_workout_plan_sqlite(workout_plan, user_id):
    """Save one user's weekly workout plan to SQLite."""
    initialize_database()
    timestamp = datetime.now().isoformat(timespec="seconds")
    weekly_schedule = workout_plan.get("weekly_schedule", {})

    day_values = []
    for day_name in WEEKDAY_NAMES:
        day_values.append(weekly_schedule.get(day_name, ""))

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute(
            """
            INSERT INTO workout_plans (
                user_id,
                plan_name,
                schedule_mode,
                monday,
                tuesday,
                wednesday,
                thursday,
                friday,
                saturday,
                sunday,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan_name = excluded.plan_name,
                schedule_mode = excluded.schedule_mode,
                monday = excluded.monday,
                tuesday = excluded.tuesday,
                wednesday = excluded.wednesday,
                thursday = excluded.thursday,
                friday = excluded.friday,
                saturday = excluded.saturday,
                sunday = excluded.sunday,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                workout_plan.get("plan_name", DEFAULT_WORKOUT_PLAN["plan_name"]),
                workout_plan.get("schedule_mode", "weekly"),
                day_values[0],
                day_values[1],
                day_values[2],
                day_values[3],
                day_values[4],
                day_values[5],
                day_values[6],
                timestamp,
            ),
        )


def save_workout_plan_for_user(workout_plan, user_id):
    """Persistence boundary: save one user's weekly workout plan to the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        save_workout_plan_sqlite(workout_plan, user_id)
        return
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def save_workout_plan(workout_plan):
    """Save the current user's weekly workout plan."""
    save_workout_plan_for_user(workout_plan, get_current_user_id())


def migrate_workout_plan_json_to_sqlite_if_needed():
    """
    Import workout_plan.json once for the current user when SQLite has no plan.

    The JSON file is kept on disk as a backup/reference. If no valid plan exists,
    the default empty weekly plan is created for the current user.
    """
    initialize_database()

    user_id = get_current_user_id()
    if user_has_workout_plan(user_id):
        return

    plan_to_save = dict(DEFAULT_WORKOUT_PLAN)

    if os.path.exists(WORKOUT_PLAN_FILE) and user_id == DEFAULT_USER_ID:
        try:
            with open(WORKOUT_PLAN_FILE, "r") as file:
                content = file.read().strip()

            if content != "":
                imported_plan = json.loads(content)
                if isinstance(imported_plan, dict) and "weekly_schedule" in imported_plan:
                    plan_to_save = imported_plan
                    if "schedule_mode" not in plan_to_save:
                        plan_to_save["schedule_mode"] = "weekly"
                    if "plan_name" not in plan_to_save:
                        plan_to_save["plan_name"] = DEFAULT_WORKOUT_PLAN["plan_name"]
        except json.JSONDecodeError:
            st.warning(
                f"Could not migrate {WORKOUT_PLAN_FILE} because it contains invalid JSON. "
                "Using the default workout plan."
            )

    save_workout_plan_for_user(plan_to_save, user_id)

    if plan_to_save != DEFAULT_WORKOUT_PLAN:
        st.success(f"Migrated workout plan to SQLite for {user_id}.")


def update_workout_plan_template_name(old_name, new_name):
    """
    Replace a renamed template name in the current user's weekly workout plan.

    Today's Workout looks up the scheduled workout by template name, so leaving
    the old name in the plan would break weekly schedule mode.
    """
    user_id = get_current_user_id()
    workout_plan = load_workout_plan_for_user(user_id)
    days_updated = 0

    for day_name, assigned_workout in workout_plan["weekly_schedule"].items():
        if assigned_workout == old_name:
            workout_plan["weekly_schedule"][day_name] = new_name
            days_updated += 1

    if days_updated > 0:
        save_workout_plan_for_user(workout_plan, user_id)

    return days_updated


def build_rotation_from_weekly_plan(weekly_schedule):
    """
    Build a rotation list from Monday through Sunday.

    Blank days become "Rest" in the rotation order.
    """
    week_days = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    rotation_list = []
    for day_name in week_days:
        workout = weekly_schedule.get(day_name, "")
        if workout == "":
            rotation_list.append("Rest")
        else:
            rotation_list.append(workout)

    return rotation_list


def get_template_names(templates):
    """Return a list of template names from saved templates."""
    template_names = []
    for template in templates:
        template_names.append(template["template_name"])
    return template_names


def find_template_by_name(templates, template_name):
    """Return the template dict that matches the name, or None if not found."""
    for template in templates:
        if template["template_name"] == template_name:
            return template
    return None


def get_today_weekday():
    """Return today's weekday name, for example 'Monday'."""
    return date.today().strftime("%A")


def get_most_recent_logged_workout_name(workouts):
    """Return the most recent workout_name from the workout log."""
    if not workouts:
        return None

    for entry in reversed(workouts):
        workout_name = get_entry_workout_name(entry)
        if workout_name:
            return workout_name

    return None


def get_last_logged_workout_name(workouts):
    """Return the most recent workout_name from the workout log."""
    return get_most_recent_logged_workout_name(workouts)


def get_first_non_rest_workout(rotation_list):
    """Return the first workout in the rotation that is not Rest."""
    for workout in rotation_list:
        if workout != "Rest" and workout != "":
            return workout
    return ""


def get_next_rotation_workout(rotation_list, last_workout):
    """
    Return the next workout in the rotation list.

    If there is no history, or the last workout is not in the plan,
    return the first non-rest workout.

    If a workout appears more than once, use the last matching position
    in the weekly rotation order before moving to the next item.
    """
    if not rotation_list:
        return ""

    first_non_rest = get_first_non_rest_workout(rotation_list)

    if last_workout is None:
        return first_non_rest

    last_index = -1
    for index in range(len(rotation_list)):
        if rotation_list[index] == last_workout:
            last_index = index

    if last_index == -1:
        return first_non_rest

    next_index = (last_index + 1) % len(rotation_list)
    return rotation_list[next_index]


def get_weekly_mode_template(workout_plan, templates):
    """
    Return the template assigned to today's weekday in the workout plan.

    Returns an empty string for rest days or if the planned workout is not saved.
    """
    template_names = get_template_names(templates)
    planned_workout = workout_plan["weekly_schedule"].get(get_today_weekday(), "")

    if planned_workout == "" or planned_workout == "Rest":
        return ""

    if planned_workout in template_names:
        return planned_workout

    return ""


def get_rotation_mode_template(workout_plan, workouts, templates):
    """
    Return the next template in the weekly plan rotation.

    Uses Monday through Sunday order from workout_plan.json.
    Returns an empty string when the next item is Rest or not a saved template.
    """
    template_names = get_template_names(templates)
    rotation_list = build_rotation_from_weekly_plan(workout_plan["weekly_schedule"])
    last_workout = get_most_recent_logged_workout_name(workouts)
    recommended = get_next_rotation_workout(rotation_list, last_workout)

    if recommended == "Rest" or recommended == "":
        return ""

    if recommended in template_names:
        return recommended

    return ""


def get_default_template_for_mode(mode, workout_plan, workouts, templates):
    """
    Return the template name to auto-select in Today's Workout.

    mode is "weekly", "rotation", or "manual".
    Returns None for manual mode so the user's dropdown choice is kept.
    """
    if mode == "manual":
        return None

    if mode == "weekly":
        recommended = get_weekly_mode_template(workout_plan, templates)
        return recommended if recommended else None

    if mode == "rotation":
        recommended = get_rotation_mode_template(workout_plan, workouts, templates)
        return recommended if recommended else None

    return None


def get_planned_workout_for_today(workout_plan, workouts, template_names):
    """Return today's recommended workout, or blank for a rest day."""
    schedule_mode = workout_plan.get("schedule_mode", "weekly")

    if schedule_mode == "rotation":
        templates = []
        for name in template_names:
            templates.append({"template_name": name, "exercises": []})
        return get_rotation_mode_template(workout_plan, workouts, templates)

    return workout_plan["weekly_schedule"].get(get_today_weekday(), "")


def load_exercise_library_sqlite(user_id):
    """
    Read one user's exercise library from SQLite.

    Returns the same list-of-dicts shape the app used with exercise_library.json.
    """
    initialize_database()

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            """
            SELECT name, category, primary_muscle, default_sets, rep_min, rep_max,
                   weight_increment
            FROM exercise_library
            WHERE user_id = ?
            ORDER BY name
            """,
            (user_id,),
        )
        rows = cursor.fetchall()

    exercise_library = []
    for row in rows:
        exercise_library.append({
            "name": row["name"],
            "category": row["category"] or "",
            "primary_muscle": row["primary_muscle"] or "",
            "default_sets": int(row["default_sets"] or 3),
            "rep_min": int(row["rep_min"] or 8),
            "rep_max": int(row["rep_max"] or 12),
            "weight_increment": int(row["weight_increment"] or 5),
        })

    return exercise_library


# --- Persistence boundary: exercise library ---
def load_exercise_library_for_user(user_id):
    """Persistence boundary: read one user's exercise library from the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        return load_exercise_library_sqlite(user_id)
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def load_exercise_library():
    """Read the current user's exercise library."""
    return load_exercise_library_for_user(get_current_user_id())


def get_exercise_library_table_count(user_id):
    """Return how many exercises one user has stored in SQLite."""
    initialize_database()
    with sqlite3.connect(DATABASE_FILE) as connection:
        cursor = connection.execute(
            "SELECT COUNT(*) FROM exercise_library WHERE user_id = ?",
            (user_id,),
        )
        return cursor.fetchone()[0]


def save_exercise_library_sqlite(exercise_library, user_id):
    """
    Save one user's exercise library to SQLite.

    Only rows for this user_id are replaced so other users' exercises stay safe.
    """
    initialize_database()
    timestamp = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute(
            "DELETE FROM exercise_library WHERE user_id = ?",
            (user_id,),
        )

        for exercise in exercise_library:
            connection.execute(
                """
                INSERT INTO exercise_library (
                    user_id,
                    name,
                    category,
                    primary_muscle,
                    default_sets,
                    rep_min,
                    rep_max,
                    weight_increment,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    exercise.get("name", "").strip(),
                    exercise.get("category", ""),
                    exercise.get("primary_muscle", ""),
                    clean_integer_value(exercise.get("default_sets", 3)),
                    clean_integer_value(exercise.get("rep_min", 8)),
                    clean_integer_value(exercise.get("rep_max", 12)),
                    clean_integer_value(exercise.get("weight_increment", 5)),
                    timestamp,
                    timestamp,
                ),
            )


def save_exercise_library_for_user(exercise_library, user_id):
    """Persistence boundary: save one user's exercise library to the active backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        save_exercise_library_sqlite(exercise_library, user_id)
        return
    raise ValueError(f"Unsupported DATABASE_BACKEND: {backend}")


def save_exercise_library(exercise_library):
    """Save the current user's exercise library."""
    save_exercise_library_for_user(exercise_library, get_current_user_id())


def migrate_exercise_library_json_to_sqlite_if_needed():
    """
    Import exercise_library.json once for the current user when SQLite is empty.

    The JSON file is kept on disk as a backup/reference. This prepares the app
    for future login without changing the UI yet.
    """
    initialize_database()

    user_id = get_current_user_id()
    if user_id != DEFAULT_USER_ID:
        return

    if get_exercise_library_table_count(user_id) > 0:
        return

    if not os.path.exists(EXERCISE_LIBRARY_FILE):
        return

    try:
        with open(EXERCISE_LIBRARY_FILE, "r") as file:
            content = file.read().strip()

        if content == "":
            return

        exercise_entries = json.loads(content)
    except json.JSONDecodeError:
        st.warning(
            f"Could not migrate {EXERCISE_LIBRARY_FILE} because it contains invalid JSON."
        )
        return

    if not isinstance(exercise_entries, list):
        return

    valid_entries = []
    for entry in exercise_entries:
        if isinstance(entry, dict) and entry.get("name", "").strip():
            valid_entries.append(entry)

    if not valid_entries:
        return

    save_exercise_library_for_user(valid_entries, user_id)
    st.success(f"Migrated {len(valid_entries)} exercises to SQLite for {user_id}.")


STARTER_EXERCISES = [
    {
        "name": "Bench Press",
        "category": "Push",
        "primary_muscle": "Chest",
        "default_sets": 3,
        "rep_min": 6,
        "rep_max": 10,
        "weight_increment": 5,
    },
    {
        "name": "Incline Dumbbell Press",
        "category": "Push",
        "primary_muscle": "Chest",
        "default_sets": 3,
        "rep_min": 8,
        "rep_max": 12,
        "weight_increment": 5,
    },
    {
        "name": "Pull Up",
        "category": "Pull",
        "primary_muscle": "Back",
        "default_sets": 3,
        "rep_min": 6,
        "rep_max": 10,
        "weight_increment": 5,
    },
    {
        "name": "Barbell Row",
        "category": "Pull",
        "primary_muscle": "Back",
        "default_sets": 3,
        "rep_min": 6,
        "rep_max": 10,
        "weight_increment": 5,
    },
    {
        "name": "Squat",
        "category": "Legs",
        "primary_muscle": "Quads",
        "default_sets": 3,
        "rep_min": 6,
        "rep_max": 10,
        "weight_increment": 5,
    },
    {
        "name": "Romanian Deadlift",
        "category": "Legs",
        "primary_muscle": "Hamstrings",
        "default_sets": 3,
        "rep_min": 6,
        "rep_max": 10,
        "weight_increment": 5,
    },
    {
        "name": "Leg Press",
        "category": "Legs",
        "primary_muscle": "Quads",
        "default_sets": 3,
        "rep_min": 8,
        "rep_max": 12,
        "weight_increment": 5,
    },
    {
        "name": "Lateral Raise",
        "category": "Push",
        "primary_muscle": "Shoulders",
        "default_sets": 3,
        "rep_min": 8,
        "rep_max": 12,
        "weight_increment": 5,
    },
    {
        "name": "Triceps Pushdown",
        "category": "Push",
        "primary_muscle": "Triceps",
        "default_sets": 3,
        "rep_min": 8,
        "rep_max": 12,
        "weight_increment": 5,
    },
    {
        "name": "Dumbbell Curl",
        "category": "Pull",
        "primary_muscle": "Biceps",
        "default_sets": 3,
        "rep_min": 8,
        "rep_max": 12,
        "weight_increment": 5,
    },
]

STARTER_TEMPLATES = [
    {
        "template_name": "Push Day",
        "exercises": [
            "Bench Press",
            "Incline Dumbbell Press",
            "Lateral Raise",
            "Triceps Pushdown",
        ],
    },
    {
        "template_name": "Pull Day",
        "exercises": [
            "Pull Up",
            "Barbell Row",
            "Dumbbell Curl",
        ],
    },
    {
        "template_name": "Leg Day",
        "exercises": [
            "Squat",
            "Romanian Deadlift",
            "Leg Press",
        ],
    },
]


def user_has_any_setup_data(user_id):
    """
    Return True when the user already has exercises, templates, or workout history.

    An empty weekly plan alone does not count as setup data.
    """
    if get_exercise_library_table_count(user_id) > 0:
        return True
    if get_workout_templates_table_count(user_id) > 0:
        return True
    if get_workout_table_count(user_id) > 0:
        return True
    return False


def user_skipped_onboarding(user_id):
    """Return True when this user chose to start blank instead of using the starter setup."""
    initialize_database()
    with sqlite3.connect(DATABASE_FILE) as connection:
        cursor = connection.execute(
            "SELECT skipped_onboarding FROM user_settings WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return False
    return bool(row[0])


def set_user_skipped_onboarding(user_id):
    """Remember that this user dismissed the new-user onboarding card."""
    initialize_database()
    timestamp = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute(
            """
            INSERT INTO user_settings (user_id, skipped_onboarding, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                skipped_onboarding = 1,
                updated_at = excluded.updated_at
            """,
            (user_id, timestamp),
        )


def create_starter_exercise_library_for_user(user_id):
    """
    Create a small starter exercise library for a brand-new user.

    Existing exercises are never overwritten.
    """
    if get_exercise_library_table_count(user_id) > 0:
        return False

    save_exercise_library_for_user(STARTER_EXERCISES, user_id)
    return True


def create_starter_templates_for_user(user_id):
    """
    Create Push Day, Pull Day, and Leg Day templates for a brand-new user.

    Existing templates are never overwritten.
    """
    if get_workout_templates_table_count(user_id) > 0:
        return False

    save_templates_for_user(STARTER_TEMPLATES, user_id)
    return True


def create_starter_workout_plan_for_user(user_id):
    """
    Assign starter templates to the weekly plan when no days are scheduled yet.

    Existing scheduled days are never overwritten.
    """
    workout_plan = load_workout_plan_for_user(user_id)
    weekly_schedule = workout_plan.get("weekly_schedule", {})

    for day_name in WEEKDAY_NAMES:
        if str(weekly_schedule.get(day_name, "")).strip():
            return False

    weekly_schedule["Monday"] = "Push Day"
    weekly_schedule["Wednesday"] = "Pull Day"
    weekly_schedule["Friday"] = "Leg Day"
    workout_plan["weekly_schedule"] = weekly_schedule
    save_workout_plan_for_user(workout_plan, user_id)
    return True


def should_show_onboarding_card(user_id):
    """Return True when a new user still needs a gentle getting-started prompt."""
    if user_has_any_setup_data(user_id):
        return False
    if user_skipped_onboarding(user_id):
        return False
    return True


def render_onboarding_card(user_id):
    """Show a simple first-time setup card near the top of the app."""
    if not should_show_onboarding_card(user_id):
        return

    st.info("Welcome. Want to create starter exercises and templates?")
    onboarding_col1, onboarding_col2 = st.columns(2)

    with onboarding_col1:
        if st.button("Create starter setup", key="onboarding_create_starter_button"):
            created_exercises = create_starter_exercise_library_for_user(user_id)
            created_templates = create_starter_templates_for_user(user_id)
            created_plan = create_starter_workout_plan_for_user(user_id)

            if created_exercises or created_templates or created_plan:
                st.success(
                    "Starter setup created. Check Exercise Library, "
                    "Workout Templates / Plans, and Today's Workout."
                )
            else:
                st.info("Starter setup was already present for this user.")
            st.rerun()

    with onboarding_col2:
        if st.button("Start blank", key="onboarding_start_blank_button"):
            set_user_skipped_onboarding(user_id)
            st.rerun()


def get_exercise_by_name(exercise_library, exercise_name):
    """Return one exercise from the library that matches the selected name."""
    for exercise in exercise_library:
        if exercise["name"] == exercise_name:
            return exercise
    return None


def estimate_1rm(weight, reps):
    """Estimate one-rep max from weight and reps."""
    return (100 * weight) / (52.2 + 41.9 * math.exp(-0.055 * reps))


def get_entry_exercise(entry):
    """Return the exercise name from a workout entry, or an empty string."""
    return entry.get("exercise", "")


def get_entry_weight(entry):
    """Return weight from a workout entry, or 0 if missing."""
    weight = entry.get("weight", 0)
    try:
        return float(weight)
    except (TypeError, ValueError):
        return 0.0


def get_entry_reps(entry):
    """Return reps from a workout entry, or 0 if missing."""
    reps = entry.get("reps", 0)
    try:
        return int(reps)
    except (TypeError, ValueError):
        return 0


def get_entry_estimated_1rm(entry):
    """Return estimated 1RM from a workout entry, or 0 if missing."""
    estimated_1rm = entry.get("estimated_1rm", 0)
    try:
        return float(estimated_1rm)
    except (TypeError, ValueError):
        return 0.0


def get_entry_volume(entry):
    """Return volume from a workout entry, or 0 if missing."""
    volume = entry.get("volume", 0)
    try:
        return float(volume)
    except (TypeError, ValueError):
        return 0.0


def get_entry_workout_name(entry):
    """Return workout_name from a workout entry, or an empty string."""
    return entry.get("workout_name", "")


def entry_has_valid_lift_data(entry):
    """Return True when an entry has usable weight and reps for history math."""
    return get_entry_weight(entry) > 0 and get_entry_reps(entry) > 0


def get_suspicious_entry_warnings(weight, reps, exercise_name, workouts):
    """
    Return warning messages and whether the user must confirm before logging.

    Catches common data-entry mistakes like 0 weight or unusually high reps/1RM.
    """
    warnings = []
    needs_confirmation = False

    try:
        weight_value = float(weight)
        reps_value = int(reps)
    except (TypeError, ValueError):
        warnings.append("Weight and reps must be valid numbers.")
        return warnings, True

    if weight_value == 0:
        warnings.append("Weight is 0 lbs.")

    if reps_value > 30:
        warnings.append(f"Reps ({reps_value}) are unusually high (above 30).")

    new_1rm = estimate_1rm(weight_value, reps_value)
    exercise_entries = get_entries_for_exercise(workouts, exercise_name)
    valid_entries = []
    for entry in exercise_entries:
        if entry_has_valid_lift_data(entry):
            valid_entries.append(entry)

    if valid_entries:
        best_1rm = get_best_estimated_1rm(valid_entries)
        if best_1rm > 0 and new_1rm > best_1rm * 1.25:
            warnings.append(
                f"Estimated 1RM ({round(new_1rm)} lbs) is more than 25% higher than "
                f"your current best ({round(best_1rm)} lbs)."
            )

    if warnings:
        needs_confirmation = True

    return warnings, needs_confirmation


def get_manual_log_warnings(logged_sets, previous_entries):
    """
    Return warning strings for a Manual Log submission.

    Uses the submitted logged_sets values, not live widget state.
    """
    warnings = []

    valid_previous = []
    for entry in previous_entries:
        if entry_has_valid_lift_data(entry):
            valid_previous.append(entry)

    best_1rm = get_best_estimated_1rm(valid_previous) if valid_previous else 0

    for set_data in logged_sets:
        set_number = set_data["set_number"]

        try:
            weight_value = float(set_data["weight"])
            reps_value = int(set_data["reps"])
        except (TypeError, ValueError):
            warnings.append(f"Set {set_number} has invalid weight or reps.")
            continue

        if weight_value == 0:
            warnings.append(
                f"Set {set_number} has 0 lbs. Confirm if this is intentional."
            )

        if reps_value > 30:
            warnings.append(f"Set {set_number} has unusually high reps.")

        new_1rm = estimate_1rm(weight_value, reps_value)
        if best_1rm > 0 and new_1rm > best_1rm * 1.25:
            warnings.append(
                f"Set {set_number} would increase your estimated 1RM by more than 25%. "
                "Confirm the weight/reps are correct."
            )

    return warnings


def save_manual_log_submission(submission):
    """
    Save one Manual Log submission to workouts.json.

    Returns PR messages so both the normal and confirm-anyway paths share one save flow.
    """
    workouts = load_workouts()
    today = submission["date"]
    workout_name = submission["workout_name"]
    exercise_name = submission["exercise_name"]
    logged_sets = submission["logged_sets"]
    notes = submission["notes"]

    previous_entries = get_entries_for_exercise(workouts, exercise_name)
    pr_messages = []
    # One session_id ties every set from this Manual Log submission together.
    session_id = create_session_id(workout_name)

    for set_data in logged_sets:
        estimated_1rm = estimate_1rm(set_data["weight"], set_data["reps"])
        set_data_for_prs = {
            "set_number": set_data["set_number"],
            "weight": set_data["weight"],
            "reps": set_data["reps"],
            "estimated_1rm": estimated_1rm,
        }

        for message in detect_prs(set_data_for_prs, previous_entries):
            pr_messages.append(message)

        new_entry = {
            "date": today,
            "workout_name": workout_name,
            "exercise": exercise_name,
            "set_number": set_data["set_number"],
            "weight": set_data["weight"],
            "reps": set_data["reps"],
            "estimated_1rm": round(estimated_1rm),
            "volume": round(set_data["weight"] * set_data["reps"]),
            "notes": notes,
            "session_id": session_id,
            "logged_from": "Manual Log",
        }
        workouts.append(new_entry)

    save_workouts(workouts)
    return pr_messages


def get_unique_exercises(workouts):
    """Return a sorted list of unique exercise names from all logged workouts."""
    exercise_names = []
    for entry in workouts:
        name = get_entry_exercise(entry)
        if name and name not in exercise_names:
            exercise_names.append(name)
    return sorted(exercise_names)


def get_entries_for_exercise(workouts, exercise_name):
    """Return all logged entries that match the selected exercise."""
    matching_entries = []
    for entry in workouts:
        if get_entry_exercise(entry) == exercise_name:
            matching_entries.append(entry)
    return matching_entries


def format_workout_entry_label(entry):
    """Return a readable label for one logged set in a dropdown."""
    entry_date = entry.get("date", "Unknown date")
    workout_name = entry.get("workout_name", "Unknown workout")
    exercise = entry.get("exercise", "Unknown exercise")
    set_number = entry.get("set_number", "?")
    weight = entry.get("weight", 0)
    reps = entry.get("reps", 0)

    return (
        f"{entry_date} | {workout_name} | {exercise} | "
        f"Set {set_number} | {int(weight)} lbs × {reps}"
    )


def get_unique_workout_names(workouts):
    """Return a sorted list of unique workout names from logged sets."""
    workout_names = []
    for entry in workouts:
        name = entry.get("workout_name", "")
        if name and name not in workout_names:
            workout_names.append(name)
    return sorted(workout_names)


def get_unique_dates(workouts):
    """Return a sorted list of unique dates from logged sets."""
    dates = []
    for entry in workouts:
        entry_date = entry.get("date", "")
        if entry_date and entry_date not in dates:
            dates.append(entry_date)
    return sorted(dates)


def filter_workout_entry_indices(workouts, exercise_filter, workout_name_filter, date_filter):
    """
    Return the original workouts.json indices that match the selected filters.

    "All" means do not filter on that field.
    """
    matching_indices = []

    for index, entry in enumerate(workouts):
        if exercise_filter != "All" and entry.get("exercise", "") != exercise_filter:
            continue
        if workout_name_filter != "All" and entry.get("workout_name", "") != workout_name_filter:
            continue
        if date_filter != "All" and entry.get("date", "") != date_filter:
            continue

        matching_indices.append(index)

    return matching_indices


def count_matching_exercise_entries(workouts, entry_date, workout_name, exercise):
    """Count how many logged sets match the same date, workout, and exercise."""
    match_count = 0

    for entry in workouts:
        if entry.get("date") == entry_date and entry.get("workout_name") == workout_name and entry.get("exercise") == exercise:
            match_count += 1

    return match_count


def delete_matching_exercise_entries(workouts, entry_date, workout_name, exercise):
    """Remove all logged sets that match the same date, workout, and exercise."""
    remaining_entries = []

    for entry in workouts:
        if entry.get("date") == entry_date and entry.get("workout_name") == workout_name and entry.get("exercise") == exercise:
            continue

        remaining_entries.append(entry)

    return remaining_entries


def collect_matching_exercise_entries(workouts, entry_date, workout_name, exercise):
    """Return copies of entries that match the same date, workout, and exercise."""
    matching_entries = []

    for entry in workouts:
        if entry.get("date") == entry_date and entry.get("workout_name") == workout_name and entry.get("exercise") == exercise:
            matching_entries.append(dict(entry))

    return matching_entries


def detect_prs(set_data, previous_entries):
    """Return PR messages for one new set compared to previous history."""
    pr_messages = []

    new_1rm = round(set_data["estimated_1rm"])
    new_weight = set_data["weight"]
    new_reps = set_data["reps"]
    set_number = set_data["set_number"]

    best_1rm = 0
    heaviest_weight = 0
    best_reps_at_weight = 0
    has_history_at_weight = False

    for entry in previous_entries:
        entry_1rm = get_entry_estimated_1rm(entry)
        entry_weight = get_entry_weight(entry)
        entry_reps = get_entry_reps(entry)

        if entry_1rm > best_1rm:
            best_1rm = entry_1rm
        if entry_weight > heaviest_weight:
            heaviest_weight = entry_weight
        if entry_weight == new_weight:
            has_history_at_weight = True
            if entry_reps > best_reps_at_weight:
                best_reps_at_weight = entry_reps

    if new_1rm > best_1rm:
        pr_messages.append(
            f"Set {set_number}: New estimated 1RM PR — {new_1rm} lbs!"
        )

    if new_weight > heaviest_weight:
        pr_messages.append(
            f"Set {set_number}: New weight PR — {int(new_weight)} lbs!"
        )

    if has_history_at_weight and new_reps > best_reps_at_weight:
        pr_messages.append(
            f"Set {set_number}: New reps PR at {int(new_weight)} lbs — {new_reps} reps!"
        )

    return pr_messages


def get_1rm_trend_by_date(exercise_entries):
    """Return one row per date with the highest estimated 1RM for that day."""
    best_by_date = {}

    for entry in exercise_entries:
        entry_date = entry.get("date", "")
        entry_1rm = get_entry_estimated_1rm(entry)

        if not entry_date or entry_1rm <= 0:
            continue

        if entry_date not in best_by_date:
            best_by_date[entry_date] = entry_1rm
        elif entry_1rm > best_by_date[entry_date]:
            best_by_date[entry_date] = entry_1rm

    chart_rows = []
    for entry_date in sorted(best_by_date.keys()):
        chart_rows.append({
            "date": entry_date,
            "estimated_1rm": best_by_date[entry_date],
        })

    return chart_rows


def get_best_estimated_1rm(exercise_entries):
    """Return the highest estimated 1RM from an exercise's logged history."""
    best_1rm = 0
    for entry in exercise_entries:
        entry_1rm = get_entry_estimated_1rm(entry)
        if entry_1rm > best_1rm:
            best_1rm = entry_1rm
    return best_1rm


def get_workout_targets(current_best_1rm, exercise_entries, count=5):
    """
    Find up to 5 weight/reps targets that beat the current best 1RM by at least 0.25 lb.

    Searches reps from 4 to 10 and weight in 5 lb steps near the logged history.
    Returns options ranked easiest to hardest (smallest 1RM jump first).
    """
    candidates = []

    valid_entries = []
    for entry in exercise_entries:
        if entry_has_valid_lift_data(entry):
            valid_entries.append(entry)

    if not valid_entries:
        return []

    # Use logged weights to decide how far to search
    min_weight = get_entry_weight(valid_entries[0])
    max_weight = get_entry_weight(valid_entries[0])
    for entry in valid_entries:
        entry_weight = get_entry_weight(entry)
        if entry_weight < min_weight:
            min_weight = entry_weight
        if entry_weight > max_weight:
            max_weight = entry_weight

    start_weight = int(min_weight // 5 * 5)
    end_weight = int(max_weight // 5 * 5) + 101

    for weight in range(start_weight, end_weight, 5):
        for reps in range(4, 11):
            new_1rm = estimate_1rm(weight, reps)

            if new_1rm >= current_best_1rm + 0.25:
                candidates.append({
                    "target_weight": weight,
                    "target_reps": reps,
                    "new_estimated_1rm": round(new_1rm),
                    "improvement": round(new_1rm - current_best_1rm, 1),
                })

    candidates.sort(
        key=lambda item: (item["new_estimated_1rm"], item["target_weight"], item["target_reps"])
    )
    return candidates[:count]


def build_recommended_set_plan(target, num_sets=3):
    """
    Build a recommended set plan from one target option.

    Set 1 uses the recommended weight and reps.
    Each following set keeps the same weight but drops 1 rep.
    Sets with fewer than 1 rep are skipped.
    """
    set_plan = []
    weight = target["target_weight"]
    base_reps = target["target_reps"]

    for set_number in range(1, num_sets + 1):
        reps = base_reps - (set_number - 1)

        if reps < 1:
            break

        set_plan.append({
            "set_number": set_number,
            "weight": weight,
            "reps": reps,
            "estimated_1rm": round(estimate_1rm(weight, reps)),
        })

    return set_plan


def make_recommended_set_id(template_name, exercise_index, exercise, rank, set_number, weight, reps):
    """Return a stable ID for one recommended set in the active workout plan."""
    return (
        f"{template_name}_{exercise_index}_{exercise}_{rank}_"
        f"{set_number}_{int(weight)}_{reps}"
    )


def build_single_exercise_plan(
    template_name,
    exercise,
    exercise_index,
    workouts,
    added_during_workout=False,
):
    """
    Build one exercise entry for the active Today's Workout plan.

    Used for template exercises and for exercises added during the session.
    """
    exercise_plan = {
        "exercise_index": exercise_index,
        "planned_exercise": exercise,
        "exercise": exercise,
        "swapped_to": None,
        "has_history": False,
        "recommendations": [],
    }

    if added_during_workout:
        exercise_plan["added_during_workout"] = True

    exercise_entries = get_entries_for_exercise(workouts, exercise)

    if not exercise_entries:
        return exercise_plan

    best_1rm = get_best_estimated_1rm(exercise_entries)
    targets = get_workout_targets(best_1rm, exercise_entries, count=2)

    recommendations = []
    for rank, target in enumerate(targets, start=1):
        set_plan = build_recommended_set_plan(target)
        sets = []

        for planned_set in set_plan:
            weight = planned_set["weight"]
            reps = planned_set["reps"]
            sets.append({
                "set_number": planned_set["set_number"],
                "target_weight": weight,
                "target_reps": reps,
                "estimated_1rm": planned_set["estimated_1rm"],
                "set_id": make_recommended_set_id(
                    template_name,
                    exercise_index,
                    exercise,
                    rank,
                    planned_set["set_number"],
                    weight,
                    reps,
                ),
            })

        recommendations.append({
            "rank": rank,
            "projected_1rm": target["new_estimated_1rm"],
            "improvement": target["improvement"],
            "sets": sets,
        })

    exercise_plan["has_history"] = True
    exercise_plan["recommendations"] = recommendations
    return exercise_plan


def get_session_added_exercises(active_plan):
    """Return exercises the user added during this workout session only."""
    if not active_plan:
        return []

    added_exercises = []
    for exercise_plan in active_plan.get("exercises", []):
        if exercise_plan.get("added_during_workout"):
            added_exercises.append(exercise_plan)

    return added_exercises


def exercise_is_in_active_plan(active_plan, exercise_name):
    """Return True if the exercise is already part of today's active workout."""
    for exercise_plan in active_plan.get("exercises", []):
        if get_todays_exercise_name(exercise_plan) == exercise_name:
            return True

    return False


def add_exercise_to_active_plan(exercise_name, template_name, workouts):
    """
    Add one extra exercise to today's session plan only.

    This never changes workout_templates.json — the add is session-only.
    """
    active_plan = st.session_state.active_workout_plan
    if active_plan is None:
        return False, "Start a workout before adding exercises."

    if exercise_is_in_active_plan(active_plan, exercise_name):
        return False, f"'{exercise_name}' is already in today's workout."

    next_index = 0
    for exercise_plan in active_plan.get("exercises", []):
        exercise_index = exercise_plan.get("exercise_index", 0)
        if exercise_index >= next_index:
            next_index = exercise_index + 1

    new_exercise_plan = build_single_exercise_plan(
        template_name,
        exercise_name,
        next_index,
        workouts,
        added_during_workout=True,
    )
    active_plan["exercises"].append(new_exercise_plan)
    st.session_state.todays_session_added_exercises.append(new_exercise_plan)
    return True, f"Added {exercise_name} to today's workout."


def build_active_workout_plan(template_name, selection_mode, templates, workouts):
    """
    Build the full recommended workout plan for Today's Workout.

    This is stored in session_state so recommendations stay the same
    while the user logs sets during a workout.
    """
    template = find_template_by_name(templates, template_name)
    if template is None:
        return None

    exercise_plans = []

    for exercise_index, exercise in enumerate(template["exercises"]):
        exercise_plans.append(
            build_single_exercise_plan(
                template_name,
                exercise,
                exercise_index,
                workouts,
            )
        )

    return {
        "template_name": template_name,
        "selection_mode": selection_mode,
        "generated_date": str(date.today()),
        "session_id": create_session_id(template_name),
        "exercises": exercise_plans,
    }


def get_todays_exercise_name(exercise_plan):
    """
    Return the exercise the user will perform today.

    planned_exercise = what the saved template scheduled.
    swapped_to = temporary replacement for this session only (or None).
    """
    swapped_to = exercise_plan.get("swapped_to")
    if swapped_to:
        return swapped_to

    return exercise_plan.get("planned_exercise", exercise_plan.get("exercise", ""))


def apply_exercise_swap_to_active_plan(exercise_index, swapped_to):
    """
    Update only the frozen active workout plan in session_state.

    Swaps never change workout_templates.json — they last only for this session.
    exercise_index keeps duplicate template exercises separate.
    """
    active_plan = st.session_state.active_workout_plan
    if active_plan is None:
        return

    for exercise_plan in active_plan["exercises"]:
        if exercise_plan.get("exercise_index") == exercise_index:
            exercise_plan["swapped_to"] = swapped_to
            break


def get_last_swap_for_exercise(workouts, workout_name, planned_exercise):
    """
    Find the most recent swap session for a planned exercise in a workout template.

    Looks for logged sets where planned_exercise and swapped_from match the template
    slot, then groups sets from the most recent date.
    """
    matching_entries = []

    for entry in workouts:
        if entry.get("workout_name") != workout_name:
            continue
        if entry.get("planned_exercise") != planned_exercise:
            continue
        if entry.get("swapped_from") != planned_exercise:
            continue
        if not entry.get("swapped_to"):
            continue

        matching_entries.append(entry)

    if not matching_entries:
        return None

    # Use the most recent date that has swap entries for this planned exercise.
    dates = []
    for entry in matching_entries:
        entry_date = entry.get("date", "")
        if entry_date and entry_date not in dates:
            dates.append(entry_date)

    dates.sort(reverse=True)
    most_recent_date = dates[0]

    session_entries = []
    for entry in matching_entries:
        if entry.get("date") == most_recent_date:
            session_entries.append(entry)

    session_entries.sort(key=lambda item: item.get("set_number", 0))

    swapped_to = session_entries[0].get("swapped_to", "")
    logged_sets = []

    for entry in session_entries:
        logged_sets.append({
            "set_number": entry.get("set_number", "?"),
            "weight": entry.get("weight", 0),
            "reps": entry.get("reps", 0),
        })

    return {
        "swapped_to": swapped_to,
        "date": most_recent_date,
        "sets": logged_sets,
    }


def render_todays_workout_manual_log(
    selected_todays_template,
    exercise_name,
    exercise_index,
    active_plan,
):
    """
    Show a fallback logger for Today's Workout exercises without recommendations.

    This lets the user create baseline history from Today's Workout instead of
    forcing them to switch to Manual Log first.
    """
    exercise_plan = None
    if active_plan:
        for plan_item in active_plan.get("exercises", []):
            if plan_item.get("exercise_index") == exercise_index:
                exercise_plan = plan_item
                break

    planned_exercise = exercise_name
    swapped_to = None
    is_added_during_workout = False
    if exercise_plan:
        planned_exercise = exercise_plan.get("planned_exercise", exercise_name)
        swapped_to = exercise_plan.get("swapped_to")
        is_added_during_workout = exercise_plan.get("added_during_workout", False)

    active_exercise = exercise_name
    if swapped_to:
        active_exercise = swapped_to

    exercise_library = load_exercise_library()
    exercise_details = get_exercise_by_name(exercise_library, active_exercise)

    default_sets = 3
    weight_increment = 5
    default_reps = 8

    if exercise_details:
        default_sets = int(exercise_details.get("default_sets", 3))
        weight_increment = int(float(exercise_details.get("weight_increment", 5)))

        rep_min = int(exercise_details.get("rep_min", 8))
        rep_max = int(exercise_details.get("rep_max", 8))
        if rep_min <= 8 <= rep_max:
            default_reps = 8
        else:
            default_reps = rep_min

    if weight_increment <= 0:
        weight_increment = 5

    key_base = f"{selected_todays_template}_{exercise_index}_{active_exercise}"

    st.markdown(f"**{active_exercise}**")
    st.info("No recommendations yet. Log your sets below to create a baseline.")

    num_sets = st.number_input(
        "Number of sets",
        min_value=1,
        max_value=10,
        value=default_sets,
        step=1,
        key=f"todays_baseline_sets_{key_base}",
    )

    logged_sets = []
    for set_number in range(1, int(num_sets) + 1):
        with st.container(border=True):
            st.markdown(f"**Set {set_number}**")
            weight_col, reps_col = st.columns(2)
            weight = weight_col.number_input(
                "Weight (lbs)",
                min_value=0,
                step=weight_increment,
                format="%d",
                key=f"todays_baseline_weight_{key_base}_{set_number}",
            )
            reps = reps_col.number_input(
                "Reps",
                min_value=1,
                value=default_reps,
                step=1,
                key=f"todays_baseline_reps_{key_base}_{set_number}",
            )

        logged_sets.append({
            "set_number": set_number,
            "weight": int(weight),
            "reps": int(reps),
        })

    notes = st.text_area(
        "Notes (optional)",
        key=f"todays_baseline_notes_{key_base}",
    )

    if st.button(
        "Log Exercise",
        key=f"todays_baseline_log_button_{key_base}",
        type="primary",
        use_container_width=True,
    ):
        workouts = load_workouts()

        session_id = None
        if active_plan:
            session_id = active_plan.get("session_id")
        if not session_id:
            session_id = create_session_id(selected_todays_template)

        for set_data in logged_sets:
            estimated_1rm = round(
                estimate_1rm(set_data["weight"], set_data["reps"])
            )
            new_entry = {
                "date": str(date.today()),
                "workout_name": selected_todays_template,
                "exercise": active_exercise,
                "set_number": set_data["set_number"],
                "weight": set_data["weight"],
                "reps": set_data["reps"],
                "estimated_1rm": estimated_1rm,
                "volume": set_data["weight"] * set_data["reps"],
                "notes": notes,
                "planned_exercise": planned_exercise,
                "session_id": session_id,
                "logged_from": "Today's Workout",
            }

            if swapped_to:
                new_entry["swapped_from"] = planned_exercise
                new_entry["swapped_to"] = swapped_to

            if is_added_during_workout:
                new_entry["added_during_workout"] = True

            workouts.append(new_entry)

        save_workouts(workouts)
        st.success(f"Baseline logged! {int(num_sets)} sets saved.")
        st.info(
            "Baseline logged. Use Refresh recommendations next time you want "
            "this exercise to generate targets."
        )
        try_auto_start_rest_timer()


def format_seconds(seconds):
    """Format whole seconds as mm:ss for the rest timer display."""
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def start_rest_timer(duration_seconds):
    """
    Start the rest timer for the chosen number of seconds.

    We store an end timestamp instead of sleeping because Streamlit reruns
    the whole script on each interaction.
    """
    duration_seconds = int(duration_seconds)
    if duration_seconds <= 0:
        return

    st.session_state.rest_timer_duration_seconds = duration_seconds
    st.session_state.rest_timer_end_time = time.time() + duration_seconds
    st.session_state.rest_timer_is_running = True
    st.session_state.rest_timer_status_message = None


def stop_rest_timer():
    """Stop the rest timer without clearing the chosen duration."""
    if st.session_state.rest_timer_is_running:
        st.session_state.rest_timer_status_message = "Timer stopped"
    st.session_state.rest_timer_is_running = False
    st.session_state.rest_timer_end_time = None


def get_rest_timer_remaining_seconds():
    """Return how many seconds are left on the active rest timer."""
    if not st.session_state.rest_timer_is_running:
        return 0

    end_time = st.session_state.rest_timer_end_time
    if end_time is None:
        return 0

    remaining = end_time - time.time()
    if remaining <= 0:
        st.session_state.rest_timer_is_running = False
        return 0

    return remaining


def try_auto_start_rest_timer():
    """Start the rest timer after logging a set if auto-start is enabled."""
    if st.session_state.get("auto_start_rest_timer", True):
        start_rest_timer(st.session_state.rest_timer_duration_seconds)


def render_rest_timer_section():
    """Show rest timer controls (caller may wrap in an expander on Today's Workout)."""
    st.caption("Rest between sets. The bar at the bottom stays visible while you scroll.")

    duration_choice = st.radio(
        "Rest duration",
        [
            "60 seconds",
            "90 seconds",
            "120 seconds",
            "180 seconds",
            "Custom",
        ],
        key="rest_timer_duration_choice",
        horizontal=True,
    )

    if duration_choice == "Custom":
        custom_seconds = st.number_input(
            "Custom seconds",
            min_value=1,
            max_value=600,
            value=st.session_state.rest_timer_duration_seconds,
            step=15,
            key="rest_timer_custom_seconds",
        )
        selected_duration = int(custom_seconds)
    else:
        selected_duration = int(duration_choice.split()[0])

    st.session_state.rest_timer_duration_seconds = selected_duration

    st.checkbox(
        "Auto-start rest timer after logging a set",
        key="auto_start_rest_timer",
    )

    timer_col1, timer_col2 = st.columns(2)
    if timer_col1.button(
        "Start Timer",
        key="start_rest_timer_button",
        type="primary",
        use_container_width=True,
    ):
        start_rest_timer(st.session_state.rest_timer_duration_seconds)

    if timer_col2.button(
        "Stop / Reset",
        key="stop_rest_timer_button",
        use_container_width=True,
    ):
        stop_rest_timer()

    remaining_seconds = 0
    if st.session_state.rest_timer_is_running:
        remaining_seconds = get_rest_timer_remaining_seconds()

    if st.session_state.rest_timer_is_running and remaining_seconds > 0:
        st.markdown(f"Rest timer: **{format_seconds(remaining_seconds)}** remaining")
        # Rerun about once per second while counting down. No sleep loop needed.
        st_autorefresh(interval=1000, key="rest_timer_autorefresh")
    elif (
        st.session_state.rest_timer_end_time is not None
        and time.time() >= st.session_state.rest_timer_end_time
    ):
        st.success("Rest complete")
    else:
        st.caption("Rest timer: ready")


def render_sticky_rest_timer():
    """
    Show a fixed bottom bar with the current rest timer status.

    Display-only — controls stay in render_rest_timer_section() above.
    CSS position: fixed keeps the bar visible while scrolling exercise cards.
    """
    status_text = None
    bar_background = "#1e3a5f"
    bar_color = "#ffffff"

    if st.session_state.rest_timer_is_running:
        remaining_seconds = get_rest_timer_remaining_seconds()
        if remaining_seconds > 0:
            status_text = f"Rest: {format_seconds(remaining_seconds)} remaining"
        else:
            status_text = "Rest complete"
            bar_background = "#0d7377"
    elif (
        st.session_state.rest_timer_end_time is not None
        and time.time() >= st.session_state.rest_timer_end_time
    ):
        status_text = "Rest complete"
        bar_background = "#0d7377"
    elif st.session_state.get("rest_timer_status_message"):
        status_text = st.session_state.rest_timer_status_message
        bar_background = "#4a4a4a"

    if status_text is None:
        return

    st.markdown(
        f"""
        <div style="
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 9999;
            padding: 14px 16px;
            text-align: center;
            font-size: 20px;
            font-weight: 700;
            background-color: {bar_background};
            color: {bar_color};
            box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.25);
        ">
            {status_text}
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_finished_workout_summary(selected_todays_template, active_plan):
    """
    Build a simple summary for the current Today's Workout session.

    Newer entries have session_id, so use it when available. If an older active
    plan does not have one, fall back to today's date and workout name.
    """
    workouts = load_workouts()
    today_text = str(date.today())
    session_id = None
    if active_plan:
        session_id = active_plan.get("session_id")

    session_entries = []
    for entry in workouts:
        if entry.get("logged_from") != "Today's Workout":
            continue

        if session_id:
            if entry.get("session_id") == session_id:
                session_entries.append(entry)
        else:
            if (
                entry.get("date") == today_text
                and entry.get("workout_name") == selected_todays_template
            ):
                session_entries.append(entry)

    exercises = []
    total_volume = 0
    best_estimated_1rm = 0

    for entry in session_entries:
        exercise_name = get_entry_exercise(entry)
        if exercise_name and exercise_name not in exercises:
            exercises.append(exercise_name)

        total_volume += get_entry_volume(entry)

        entry_1rm = get_entry_estimated_1rm(entry)
        if entry_1rm > best_estimated_1rm:
            best_estimated_1rm = entry_1rm

    return {
        "workout_name": selected_todays_template,
        "exercises": exercises,
        "set_count": len(session_entries),
        "total_volume": total_volume,
        "best_estimated_1rm": best_estimated_1rm,
    }


def show_finished_workout_summary(summary):
    """Display the finished workout summary in a phone-friendly card."""
    with st.container(border=True):
        st.markdown("**Workout Summary**")
        st.write(f"Workout: {summary['workout_name']}")
        st.write(f"Sets logged: {summary['set_count']}")
        st.write(f"Total volume: {summary['total_volume']} lbs")

        if summary["best_estimated_1rm"] > 0:
            st.write(f"Best estimated 1RM: {summary['best_estimated_1rm']} lbs")
        else:
            st.write("Best estimated 1RM: No sets logged")

        if summary["exercises"]:
            st.write("Exercises logged:")
            for exercise_name in summary["exercises"]:
                st.write(f"- {exercise_name}")
        else:
            st.write("Exercises logged: No sets logged")


def inject_mobile_css():
    """
    Light CSS tweaks for phone use in the gym.

    Keeps tap targets large, leaves room for the sticky rest timer, and avoids
    rewriting the app in another framework.
    """
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 0.75rem;
            padding-bottom: 5rem;
            max-width: 42rem;
        }
        div[data-testid="stButton"] > button {
            min-height: 2.85rem;
            font-size: 1rem;
        }
        div[data-testid="stButton"] > button[kind="primary"] {
            font-weight: 700;
        }
        .gym-hero {
            font-size: 1.35rem;
            font-weight: 700;
            margin-bottom: 0.25rem;
        }
        .gym-subtle {
            color: #6b7280;
            font-size: 0.85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def count_completed_sets_in_plan(active_plan):
    """Return how many recommended sets are logged in the current workout session."""
    if not active_plan:
        return 0, 0

    total_sets = 0
    for exercise_plan in active_plan.get("exercises", []):
        if not exercise_plan.get("has_history") or not exercise_plan.get("recommendations"):
            continue
        for recommendation in exercise_plan["recommendations"]:
            for planned_set in recommendation.get("sets", []):
                total_sets += 1

    completed_sets = len(st.session_state.get("completed_recommended_sets", []))
    return completed_sets, total_sets


def render_admin_data_section():
    """Advanced raw data tools — shown inside the Settings tab."""
    with st.expander("Admin & data", expanded=False):
        st.caption("Raw workout log, edits, deletes, and backups.")

        with st.expander("Raw Workout Log"):
            st.warning("This is raw stored data — not the main workout interface.")
            workouts = load_workouts()

            if workouts:
                st.dataframe(workouts, use_container_width=True)
            else:
                st.info("No workouts logged yet. Use **Log** to save your first entry.")

        with st.expander("Edit Workout History"):
            st.caption("Filter first, then edit the exact row you need.")
            st.caption("Backups are saved automatically before edits and deletes.")

            # Undo only works during the current Streamlit session (until the app reloads).
            if st.session_state.last_deleted_entries:
                st.info(f"Last delete: {st.session_state.last_delete_description}")

                if st.button("Undo Last Delete", key="undo_last_delete_button"):
                    workouts = load_workouts()
                    for deleted_entry in st.session_state.last_deleted_entries:
                        workouts.append(deleted_entry)
                    save_workouts(workouts)
                    st.session_state.last_deleted_entries = []
                    st.session_state.last_delete_description = ""
                    st.success("Last delete undone.")
                    st.rerun()

            editable_workouts = load_workouts()

            if not editable_workouts:
                st.info("No logged workouts to edit yet.")
            else:
                # Step 1: choose filters to narrow down the list.
                exercise_options = ["All"] + get_unique_exercises(editable_workouts)
                workout_name_options = ["All"] + get_unique_workout_names(editable_workouts)
                date_options = ["All"] + get_unique_dates(editable_workouts)

                filter_col1, filter_col2, filter_col3 = st.columns(3)
                exercise_filter = filter_col1.selectbox(
                    "Exercise filter",
                    exercise_options,
                    key="edit_history_exercise_filter",
                )
                workout_name_filter = filter_col2.selectbox(
                    "Workout name filter",
                    workout_name_options,
                    key="edit_history_workout_name_filter",
                )
                date_filter = filter_col3.selectbox(
                    "Date filter",
                    date_options,
                    key="edit_history_date_filter",
                )

                matching_indices = filter_workout_entry_indices(
                    editable_workouts,
                    exercise_filter,
                    workout_name_filter,
                    date_filter,
                )

                st.caption(f"Showing {len(matching_indices)} matching entries.")

                if not matching_indices:
                    st.info("No entries match these filters.")
                else:
                    # Step 2: show matching entries as mobile-friendly cards.
                    for original_index in matching_indices:
                        entry = editable_workouts[original_index]
                        exercise_name = entry.get("exercise", "Unknown exercise")
                        entry_date = entry.get("date", "Unknown date")
                        workout_name = entry.get("workout_name", "Unknown workout")
                        set_number = entry.get("set_number", "?")
                        weight = entry.get("weight", 0)
                        reps = entry.get("reps", 0)
                        estimated_1rm = entry.get("estimated_1rm", "—")
                        notes_text = entry.get("notes", "")

                        with st.container(border=True):
                            st.markdown(f"**{exercise_name}**")
                            st.caption(f"{workout_name} · {entry_date}")
                            session_id = entry.get("session_id")
                            if session_id:
                                st.caption(f"Session: {format_session_id_caption(session_id)}")
                            st.write(f"Set {set_number}: {int(weight)} lbs × {reps} reps")
                            st.write(f"e1RM: {estimated_1rm} lbs")

                            if notes_text:
                                if len(notes_text) > 40:
                                    st.caption(f"Notes: {notes_text[:40]}...")
                                else:
                                    st.caption(f"Notes: {notes_text}")

                            edit_col, delete_set_col, delete_exercise_col = st.columns(3)

                            if edit_col.button("Edit", key=f"edit_workout_row_{original_index}"):
                                # Store the original workouts.json index, not the filtered row position.
                                st.session_state.edit_workout_entry_index = original_index
                                st.session_state.delete_set_index = None
                                st.session_state.delete_exercise_info = None
                                st.rerun()

                            if delete_set_col.button(
                                "Delete Set",
                                key=f"delete_set_row_{original_index}",
                            ):
                                st.session_state.delete_set_index = original_index
                                st.session_state.delete_exercise_info = None
                                st.session_state.edit_workout_entry_index = None
                                st.rerun()

                            if delete_exercise_col.button(
                                "Delete Exercise",
                                key=f"delete_exercise_row_{original_index}",
                            ):
                                match_count = count_matching_exercise_entries(
                                    editable_workouts,
                                    entry_date,
                                    workout_name,
                                    exercise_name,
                                )
                                st.session_state.delete_exercise_info = {
                                    "date": entry_date,
                                    "workout_name": workout_name,
                                    "exercise": exercise_name,
                                    "match_count": match_count,
                                }
                                st.session_state.delete_set_index = None
                                st.session_state.edit_workout_entry_index = None
                                st.rerun()

                # Step 3: confirm deleting one exact logged set.
                delete_set_index = st.session_state.delete_set_index

                if delete_set_index is not None:
                    current_workouts = load_workouts()

                    if delete_set_index < 0 or delete_set_index >= len(current_workouts):
                        st.session_state.delete_set_index = None
                    else:
                        st.divider()
                        entry_to_delete = current_workouts[delete_set_index]
                        entry_date = entry_to_delete.get("date", "Unknown date")
                        workout_name = entry_to_delete.get("workout_name", "Unknown workout")
                        exercise_name = entry_to_delete.get("exercise", "Unknown exercise")
                        set_number = entry_to_delete.get("set_number", "?")
                        weight = entry_to_delete.get("weight", 0)
                        reps = entry_to_delete.get("reps", 0)

                        st.warning("Are you sure you want to delete this set?")
                        st.write(
                            f"{entry_date} | {workout_name} | {exercise_name} | "
                            f"Set {set_number} | {int(weight)} lbs × {reps}"
                        )
                        confirm_delete_set = st.checkbox(
                            "I understand this will permanently delete this logged set.",
                            key=f"confirm_delete_set_checkbox_{delete_set_index}",
                        )

                        confirm_set_col, cancel_set_col = st.columns(2)

                        if confirm_set_col.button(
                            "Confirm Delete Set",
                            key=f"confirm_delete_set_button_{delete_set_index}",
                            disabled=not confirm_delete_set,
                            type="primary",
                        ):
                            create_workouts_backup("before_delete_set")
                            workouts_after_delete = load_workouts()
                            deleted_entry = dict(workouts_after_delete[delete_set_index])
                            st.session_state.last_deleted_entries = [deleted_entry]
                            st.session_state.last_delete_description = (
                                f"Deleted 1 set: {exercise_name}, {int(weight)} lbs × {reps}"
                            )
                            del workouts_after_delete[delete_set_index]
                            save_workouts(workouts_after_delete)
                            st.session_state.delete_set_index = None
                            st.session_state.edit_workout_entry_index = None
                            st.success("Logged set deleted.")
                            st.rerun()

                        if cancel_set_col.button(
                            "Cancel Delete Set",
                            key=f"cancel_delete_set_button_{delete_set_index}",
                        ):
                            st.session_state.delete_set_index = None
                            st.rerun()

                # Step 4: confirm deleting all sets for one exercise in one workout session.
                delete_exercise_info = st.session_state.delete_exercise_info

                if delete_exercise_info is not None:
                    st.divider()
                    entry_date = delete_exercise_info["date"]
                    workout_name = delete_exercise_info["workout_name"]
                    exercise_name = delete_exercise_info["exercise"]
                    match_count = delete_exercise_info["match_count"]

                    st.warning(
                        f"This will delete {match_count} logged sets for {exercise_name} "
                        f"from {workout_name} on {entry_date}."
                    )
                    confirm_delete_exercise = st.checkbox(
                        "I understand this will permanently delete this logged exercise.",
                        key=(
                            f"confirm_delete_exercise_checkbox_{entry_date}_"
                            f"{workout_name}_{exercise_name}"
                        ),
                    )

                    confirm_exercise_col, cancel_exercise_col = st.columns(2)

                    if confirm_exercise_col.button(
                        "Confirm Delete Exercise",
                        key=(
                            f"confirm_delete_exercise_button_{entry_date}_"
                            f"{workout_name}_{exercise_name}"
                        ),
                        disabled=not confirm_delete_exercise,
                        type="primary",
                    ):
                        create_workouts_backup("before_delete_exercise")
                        workouts_after_delete = load_workouts()
                        deleted_entries = collect_matching_exercise_entries(
                            workouts_after_delete,
                            entry_date,
                            workout_name,
                            exercise_name,
                        )
                        st.session_state.last_deleted_entries = deleted_entries
                        st.session_state.last_delete_description = (
                            f"Deleted {len(deleted_entries)} sets for {exercise_name} "
                            f"from {workout_name} on {entry_date}"
                        )
                        workouts_after_delete = delete_matching_exercise_entries(
                            workouts_after_delete,
                            entry_date,
                            workout_name,
                            exercise_name,
                        )
                        save_workouts(workouts_after_delete)
                        st.session_state.delete_exercise_info = None
                        st.session_state.edit_workout_entry_index = None
                        st.success("Logged exercise deleted.")
                        st.rerun()

                    if cancel_exercise_col.button(
                        "Cancel Delete Exercise",
                        key=(
                            f"cancel_delete_exercise_button_{entry_date}_"
                            f"{workout_name}_{exercise_name}"
                        ),
                    ):
                        st.session_state.delete_exercise_info = None
                        st.rerun()

                # Step 5: when a row is selected, show an edit form below the table.
                edit_index = st.session_state.edit_workout_entry_index

                if edit_index is not None:
                    if edit_index < 0 or edit_index >= len(editable_workouts):
                        st.session_state.edit_workout_entry_index = None
                    else:
                        st.divider()
                        st.markdown("**Edit selected entry**")
                        selected_entry = editable_workouts[edit_index]

                        edited_date = st.text_input(
                            "Date",
                            value=selected_entry.get("date", ""),
                            key=f"edit_workout_history_date_{edit_index}",
                        )
                        edited_workout_name = st.text_input(
                            "Workout name",
                            value=selected_entry.get("workout_name", ""),
                            key=f"edit_workout_history_workout_name_{edit_index}",
                        )
                        edited_exercise = st.text_input(
                            "Exercise",
                            value=selected_entry.get("exercise", ""),
                            key=f"edit_workout_history_exercise_{edit_index}",
                        )
                        edited_set_number = st.number_input(
                            "Set number",
                            min_value=1,
                            step=1,
                            value=int(selected_entry.get("set_number", 1)),
                            key=f"edit_workout_history_set_number_{edit_index}",
                        )
                        weight_col, reps_col = st.columns(2)
                        edited_weight = weight_col.number_input(
                            "Weight (lbs)",
                            min_value=0,
                            step=1,
                            format="%d",
                            value=int(selected_entry.get("weight", 0)),
                            key=f"edit_workout_history_weight_{edit_index}",
                        )
                        edited_reps = reps_col.number_input(
                            "Reps",
                            min_value=1,
                            step=1,
                            value=int(selected_entry.get("reps", 1)),
                            key=f"edit_workout_history_reps_{edit_index}",
                        )
                        edited_notes = st.text_area(
                            "Notes",
                            value=selected_entry.get("notes", ""),
                            key=f"edit_workout_history_notes_{edit_index}",
                        )

                        save_col, cancel_col = st.columns(2)

                        if save_col.button(
                            "Save Changes",
                            key=f"save_workout_history_button_{edit_index}",
                            type="primary",
                        ):
                            create_workouts_backup("before_edit")
                            # Start from the existing entry so optional fields are preserved.
                            updated_entry = dict(selected_entry)
                            updated_entry["date"] = edited_date.strip()
                            updated_entry["workout_name"] = edited_workout_name.strip()
                            updated_entry["exercise"] = edited_exercise.strip()
                            updated_entry["set_number"] = int(edited_set_number)
                            updated_entry["weight"] = int(edited_weight)
                            updated_entry["reps"] = int(edited_reps)
                            updated_entry["notes"] = edited_notes
                            updated_entry["estimated_1rm"] = round(
                                estimate_1rm(updated_entry["weight"], updated_entry["reps"])
                            )
                            updated_entry["volume"] = updated_entry["weight"] * updated_entry["reps"]

                            editable_workouts = load_workouts()
                            editable_workouts[edit_index] = updated_entry
                            save_workouts(editable_workouts)
                            st.session_state.edit_workout_entry_index = None
                            st.success("Workout entry updated!")
                            st.rerun()

                        if cancel_col.button(
                            "Cancel Edit",
                            key=f"cancel_edit_workout_button_{edit_index}",
                        ):
                            st.session_state.edit_workout_entry_index = None
                            st.rerun()

# --- 1. App title ---
initialize_database()

# Session state used across tabs
if "dev_user_id" not in st.session_state:
    st.session_state.dev_user_id = DEFAULT_USER_ID

if "authenticated_user_id" not in st.session_state:
    st.session_state.authenticated_user_id = None

if "completed_recommended_sets" not in st.session_state:
    st.session_state.completed_recommended_sets = []

if "active_workout_plan" not in st.session_state:
    st.session_state.active_workout_plan = None

if "edit_workout_entry_index" not in st.session_state:
    st.session_state.edit_workout_entry_index = None

if "delete_set_index" not in st.session_state:
    st.session_state.delete_set_index = None

if "delete_exercise_info" not in st.session_state:
    st.session_state.delete_exercise_info = None

if "last_deleted_entries" not in st.session_state:
    st.session_state.last_deleted_entries = []

if "last_delete_description" not in st.session_state:
    st.session_state.last_delete_description = ""

if "pending_manual_log_submission" not in st.session_state:
    st.session_state.pending_manual_log_submission = None

if "pending_manual_log_warnings" not in st.session_state:
    st.session_state.pending_manual_log_warnings = []

if "template_exercises" not in st.session_state:
    st.session_state.template_exercises = []

if "todays_session_added_exercises" not in st.session_state:
    st.session_state.todays_session_added_exercises = []

if "rest_timer_duration_seconds" not in st.session_state:
    st.session_state.rest_timer_duration_seconds = 90

if "rest_timer_end_time" not in st.session_state:
    st.session_state.rest_timer_end_time = None

if "rest_timer_is_running" not in st.session_state:
    st.session_state.rest_timer_is_running = False

if "auto_start_rest_timer" not in st.session_state:
    st.session_state.auto_start_rest_timer = True

if "rest_timer_status_message" not in st.session_state:
    st.session_state.rest_timer_status_message = None

require_login()

current_user_id = get_current_user_id()
if st.session_state.authenticated_user_id != current_user_id:
    if st.session_state.authenticated_user_id is not None:
        clear_user_specific_session_state()
    st.session_state.authenticated_user_id = current_user_id

migrate_workouts_json_to_sqlite_if_needed()
migrate_exercise_library_json_to_sqlite_if_needed()
migrate_workout_templates_json_to_sqlite_if_needed()
migrate_workout_plan_json_to_sqlite_if_needed()

inject_mobile_css()

st.title("Workout")
if not (ENABLE_AUTH and is_auth_configured() and safe_user_is_logged_in()):
    st.caption(f"Signed in as **{get_logged_in_display_name()}**")

render_onboarding_card(current_user_id)

if ENABLE_LOCAL_USER_SWITCHER:
    with st.expander("Developer user switcher", expanded=False):
        st.caption(
            "Development only. Switch user ids to test isolated workout data in SQLite."
        )
        dev_user_input = st.text_input(
            "User id",
            value=st.session_state.dev_user_id,
            key="dev_user_id_input",
        )

        if st.button("Switch user", key="switch_dev_user_button"):
            new_user_id = dev_user_input.strip()
            if new_user_id:
                st.session_state.dev_user_id = new_user_id
                clear_user_specific_session_state()
                st.rerun()

tab_workout, tab_log, tab_progress, tab_library, tab_settings = st.tabs([
    "🏋️ Workout",
    "📝 Log",
    "📈 Progress",
    "📚 Library",
    "⚙️ Settings",
])


# --- Tab: Workout (Today's Workout — home screen) ---
with tab_workout:
    todays_templates = load_templates()
    todays_workouts = load_workouts()

    if not todays_templates:
        st.info("No workout templates yet.")
        st.caption(
            "Create templates under **Settings → Workout templates**, or use the starter "
            "setup card above."
        )
    else:
        todays_template_names = get_template_names(todays_templates)
        workout_plan = load_workout_plan()
        today_key = str(date.today())

        with st.expander("Schedule & template options", expanded=False):
            selection_mode = st.radio(
                "Workout selection mode",
                ["Weekly Schedule Mode", "Rotation Mode", "Manual Override"],
                key="workout_selection_mode",
            )

            mode_help_text = {
                "Weekly Schedule Mode": "Uses the workout assigned to today's weekday in your saved plan.",
                "Rotation Mode": "Follows your weekly plan order based on your most recent logged workout.",
                "Manual Override": "You choose the template. The app will not auto-select for you.",
            }
            st.caption(mode_help_text[selection_mode])

            if selection_mode == "Weekly Schedule Mode":
                mode_key = "weekly"
            elif selection_mode == "Rotation Mode":
                mode_key = "rotation"
            else:
                mode_key = "manual"

            recommended_template = get_default_template_for_mode(
                mode_key,
                workout_plan,
                todays_workouts,
                todays_templates,
            )

            st.markdown("**Today's recommendation**")
            if mode_key == "weekly":
                planned_workout = get_weekly_mode_template(workout_plan, todays_templates)
                if planned_workout == "":
                    st.info("Today is scheduled as a rest day.")
                else:
                    st.success(planned_workout)
            elif mode_key == "rotation":
                rotation_list = build_rotation_from_weekly_plan(workout_plan["weekly_schedule"])
                last_workout = get_most_recent_logged_workout_name(todays_workouts)
                if recommended_template is None:
                    st.info("The next item in your rotation is a rest day.")
                else:
                    st.success(recommended_template)
                if last_workout:
                    st.caption(f"Last logged workout: {last_workout}")
                st.caption("Rotation order: " + " → ".join(rotation_list))
            else:
                st.info("Manual mode — pick any template below.")

            if mode_key == "manual":
                template_label = "Choose workout template"
                template_help = "Select any saved template for today's session."
            else:
                template_label = "Workout template"
                template_help = "Auto-selected from your plan. Change this anytime to override."

            # --- Widget state vs app state (intentionally separated) ---
            # Streamlit rule: you must NOT assign st.session_state[widget_key]
            # after the widget with that key has been created. Doing so raises
            # StreamlitAPIException. So we keep the app-controlled "desired"
            # template in a SEPARATE key (todays_workout_template_value) and feed
            # it into the selectbox via index=. The selectbox uses its own widget
            # key (todays_workout_template_widget) that we never mutate by hand.

            # Initialize the app-controlled value once.
            if "todays_workout_template_value" not in st.session_state:
                if recommended_template in todays_template_names:
                    st.session_state.todays_workout_template_value = recommended_template
                else:
                    st.session_state.todays_workout_template_value = todays_template_names[0]

            # Detect a mode change BEFORE rendering the selectbox so we can update
            # the desired default and safely reset the workout session. We never
            # touch the selectbox widget key here — only our own app-state key.
            if st.session_state.get("last_workout_selection_mode") != selection_mode:
                st.session_state.last_workout_selection_mode = selection_mode
                # Mode change resets the in-progress workout session.
                st.session_state.active_workout_plan = None
                st.session_state.completed_recommended_sets = []
                st.session_state.todays_session_added_exercises = []
                if mode_key in ("weekly", "rotation") and recommended_template:
                    st.session_state.todays_workout_template_value = recommended_template

            # Weekly mode: auto-select today's scheduled workout once per calendar day.
            if mode_key == "weekly":
                if st.session_state.get("workout_plan_date") != today_key:
                    st.session_state.workout_plan_date = today_key
                    if recommended_template:
                        st.session_state.todays_workout_template_value = recommended_template

            # Rotation mode: auto-select when the next recommended workout changes.
            if mode_key == "rotation":
                rotation_list = build_rotation_from_weekly_plan(workout_plan["weekly_schedule"])
                last_workout = get_most_recent_logged_workout_name(todays_workouts)
                next_in_rotation = get_next_rotation_workout(rotation_list, last_workout)
                rotation_key = f"{last_workout}::{next_in_rotation}"

                if st.session_state.get("rotation_plan_key") != rotation_key:
                    st.session_state.rotation_plan_key = rotation_key
                    if recommended_template:
                        st.session_state.todays_workout_template_value = recommended_template

            # Compute the selectbox index from the app-controlled value, then render.
            desired_template = st.session_state.todays_workout_template_value
            if desired_template not in todays_template_names:
                desired_template = todays_template_names[0]
            default_index = todays_template_names.index(desired_template)

            selected_todays_template = st.selectbox(
                template_label,
                todays_template_names,
                index=default_index,
                key="todays_workout_template_widget",
                help=template_help,
            )

            # The selectbox return value is the source of truth for the user's
            # choice. Persist it back into app state (NOT the widget key) so a
            # manual pick survives the next rerun.
            st.session_state.todays_workout_template_value = selected_todays_template

        with st.container(border=True):
            st.markdown(f'<p class="gym-hero">{selected_todays_template}</p>', unsafe_allow_html=True)
            completed_sets, total_sets = count_completed_sets_in_plan(
                st.session_state.active_workout_plan
            )
            if total_sets > 0:
                st.caption(
                    f"{get_today_weekday()} · {completed_sets}/{total_sets} sets logged"
                )
            else:
                st.caption(f"{get_today_weekday()} · Log sets below")

        with st.container(border=True):
            st.markdown("**Session controls**")
            if st.button(
                "Start new workout",
                key="start_new_workout_button",
                type="primary",
                use_container_width=True,
            ):
                st.session_state.active_workout_plan = None
                st.session_state.completed_recommended_sets = []
                st.session_state.todays_session_added_exercises = []

            if st.button(
                "Refresh recommendations",
                key="refresh_recommendations_button",
                use_container_width=True,
            ):
                st.session_state.active_workout_plan = None

            if st.button(
                "Finish workout",
                key="finish_todays_workout_button",
                type="primary",
                use_container_width=True,
            ):
                active_plan_for_finish = st.session_state.active_workout_plan
                finish_summary = get_finished_workout_summary(
                    selected_todays_template,
                    active_plan_for_finish,
                )
                show_finished_workout_summary(finish_summary)
                st.session_state.active_workout_plan = None
                st.session_state.completed_recommended_sets = []
                st.session_state.todays_session_added_exercises = []
                st.success("Workout finished.")

        # Clear stale session data from a previous day or template.
        active_plan = st.session_state.active_workout_plan
        if active_plan is not None and active_plan.get("generated_date") != today_key:
            st.session_state.active_workout_plan = None
            st.session_state.completed_recommended_sets = []
            active_plan = None

        if (
            active_plan is not None
            and active_plan.get("template_name") != selected_todays_template
        ):
            st.session_state.completed_recommended_sets = []

        # Build the plan once per workout session. Logging sets does NOT trigger this.
        if (
            active_plan is None
            or active_plan.get("template_name") != selected_todays_template
            or active_plan.get("selection_mode") != selection_mode
        ):
            st.session_state.active_workout_plan = build_active_workout_plan(
                selected_todays_template,
                selection_mode,
                todays_templates,
                todays_workouts,
            )
            # Keep session-only added exercises when the template plan is rebuilt.
            for added_exercise in st.session_state.todays_session_added_exercises:
                st.session_state.active_workout_plan["exercises"].append(added_exercise)

        active_plan = st.session_state.active_workout_plan

        with st.expander("Rest timer", expanded=False):
            render_rest_timer_section()

        st.markdown("**Exercises**")
        st.caption("Recommendations stay locked for this session. Refresh to recalculate.")

        if active_plan:
            swap_exercise_library = load_exercise_library()
            swap_exercise_names = []
            for library_exercise in swap_exercise_library:
                swap_exercise_names.append(library_exercise["name"])

            with st.expander("Add exercise today", expanded=False):
                st.caption(
                    "Add an extra exercise for this session only. "
                    "This does not change your saved template."
                )

                add_exercise_library_names = []
                for library_exercise in swap_exercise_library:
                    add_exercise_library_names.append(library_exercise["name"])

                if not add_exercise_library_names:
                    st.info("Add exercises in **Library** before adding one here.")
                else:
                    exercise_to_add_today = st.selectbox(
                        "Exercise to add",
                        add_exercise_library_names,
                        key=f"add_exercise_today_select_{selected_todays_template}",
                    )

                    if st.button(
                        "Add to today's workout",
                        key=f"add_exercise_today_button_{selected_todays_template}",
                        use_container_width=True,
                    ):
                        added_ok, add_message = add_exercise_to_active_plan(
                            exercise_to_add_today,
                            selected_todays_template,
                            todays_workouts,
                        )
                        if added_ok:
                            st.success(add_message)
                        else:
                            st.warning(add_message)
                        st.rerun()

            for exercise_plan in active_plan["exercises"]:
                # planned_exercise = template slot; swapped_to = today's temporary replacement.
                exercise_index = exercise_plan.get("exercise_index", 0)
                planned_exercise = exercise_plan.get(
                    "planned_exercise",
                    exercise_plan.get("exercise", ""),
                )
                swapped_to = exercise_plan.get("swapped_to")
                todays_exercise = get_todays_exercise_name(exercise_plan)
                added_during_workout = exercise_plan.get("added_during_workout", False)

                last_swap = None
                if not added_during_workout:
                    last_swap = get_last_swap_for_exercise(
                        todays_workouts,
                        selected_todays_template,
                        planned_exercise,
                    )

                with st.container(border=True):
                    st.markdown(f"**{todays_exercise}**")
                    if added_during_workout:
                        st.caption("Added this session")
                    elif swapped_to:
                        st.caption(f"Planned: {planned_exercise} · Active: {todays_exercise}")
                    else:
                        st.caption(f"Planned: {planned_exercise}")

                    if last_swap:
                        with st.expander("Last session swap", expanded=False):
                            st.caption(
                                f"Swapped {planned_exercise} for {last_swap['swapped_to']}"
                            )
                            for logged_set in last_swap["sets"]:
                                st.caption(
                                    f"Set {logged_set['set_number']}: "
                                    f"{int(logged_set['weight'])} lbs × {logged_set['reps']}"
                                )

                    if not added_during_workout:
                        with st.expander("Swap Exercise", expanded=False):
                            swap_options = ["Use planned exercise"] + swap_exercise_names
                            swap_index = 0
                            if swapped_to and swapped_to in swap_options:
                                swap_index = swap_options.index(swapped_to)

                            replacement_exercise = st.selectbox(
                                "Replacement exercise",
                                swap_options,
                                index=swap_index,
                                key=(
                                    f"swap_exercise_select_{selected_todays_template}_"
                                    f"{exercise_index}_{planned_exercise}"
                                ),
                            )

                            if st.button(
                                "Apply Swap",
                                key=(
                                    f"apply_swap_button_{selected_todays_template}_"
                                    f"{exercise_index}_{planned_exercise}"
                                ),
                                use_container_width=True,
                            ):
                                if replacement_exercise == "Use planned exercise":
                                    apply_exercise_swap_to_active_plan(exercise_index, None)
                                else:
                                    apply_exercise_swap_to_active_plan(
                                        exercise_index,
                                        replacement_exercise,
                                    )
                                st.rerun()

                    if (
                        not exercise_plan["has_history"]
                        or not exercise_plan["recommendations"]
                    ):
                        render_todays_workout_manual_log(
                            selected_todays_template,
                            todays_exercise,
                            exercise_index,
                            active_plan,
                        )
                        continue

                    for recommendation in exercise_plan["recommendations"]:
                        rank = recommendation["rank"]
                        st.caption(
                            f"Option #{rank} · projected 1RM {recommendation['projected_1rm']} lbs "
                            f"(+{recommendation['improvement']})"
                        )

                        for planned_set in recommendation["sets"]:
                            set_id = planned_set["set_id"]
                            is_completed = set_id in st.session_state.completed_recommended_sets
                            set_number = planned_set["set_number"]
                            target_weight = planned_set["target_weight"]
                            target_reps = planned_set["target_reps"]

                            # Target values come from the frozen plan.
                            # Actual inputs let the user log what they really performed.
                            input_key_base = (
                                f"{selected_todays_template}_{exercise_index}_"
                                f"{planned_exercise}_{rank}_{set_number}"
                            )

                            with st.container(border=True):
                                st.markdown(f"**Set {set_number}**")
                                st.caption(
                                    f"Target: {int(target_weight)} lbs × {target_reps} reps"
                                )

                                if is_completed:
                                    st.success("✓ Logged")
                                else:
                                    weight_col, reps_col = st.columns(2)
                                    actual_weight = weight_col.number_input(
                                        "Weight (lbs)",
                                        min_value=0,
                                        value=int(target_weight),
                                        step=5,
                                        format="%d",
                                        key=f"actual_weight_{input_key_base}",
                                    )
                                    actual_reps = reps_col.number_input(
                                        "Reps",
                                        min_value=1,
                                        value=int(target_reps),
                                        step=1,
                                        key=f"actual_reps_{input_key_base}",
                                    )

                                    suspicious_warnings, needs_confirmation = get_suspicious_entry_warnings(
                                        actual_weight,
                                        actual_reps,
                                        todays_exercise,
                                        todays_workouts,
                                    )
                                    for warning_message in suspicious_warnings:
                                        st.warning(warning_message)

                                    confirm_suspicious_set = True
                                    if needs_confirmation:
                                        confirm_suspicious_set = st.checkbox(
                                            "I confirm this set looks correct",
                                            key=f"confirm_suspicious_{input_key_base}",
                                        )

                                    if st.button(
                                        f"Log Set {set_number}",
                                        key=f"log_recommended_set_{set_id}",
                                        type="primary",
                                        use_container_width=True,
                                        disabled=needs_confirmation and not confirm_suspicious_set,
                                    ):
                                        actual_estimated_1rm = round(
                                            estimate_1rm(actual_weight, actual_reps)
                                        )
                                        logged_weight = int(actual_weight)
                                        logged_reps = int(actual_reps)
                                        workouts = load_workouts()
                                        # All sets in this Today's Workout session share one session_id.
                                        session_id = active_plan.get("session_id")
                                        if not session_id:
                                            session_id = create_session_id(selected_todays_template)
                                        # exercise = what was actually performed.
                                        # planned_exercise = what the template scheduled.
                                        new_entry = {
                                            "date": str(date.today()),
                                            "workout_name": selected_todays_template,
                                            "exercise": todays_exercise,
                                            "set_number": set_number,
                                            "weight": logged_weight,
                                            "reps": logged_reps,
                                            "estimated_1rm": actual_estimated_1rm,
                                            "volume": logged_weight * logged_reps,
                                            "notes": "",
                                            "target_weight": int(target_weight),
                                            "target_reps": int(target_reps),
                                            "target_estimated_1rm": planned_set["estimated_1rm"],
                                            "session_id": session_id,
                                            "logged_from": "Today's Workout",
                                        }

                                        if swapped_to:
                                            new_entry["planned_exercise"] = planned_exercise
                                            new_entry["swapped_from"] = planned_exercise
                                            new_entry["swapped_to"] = swapped_to

                                        if added_during_workout:
                                            new_entry["added_during_workout"] = True

                                        workouts.append(new_entry)
                                        save_workouts(workouts)
                                        st.session_state.completed_recommended_sets.append(set_id)
                                        st.success(
                                            f"Logged Set {set_number}: "
                                            f"{logged_weight} lbs × {logged_reps} reps"
                                        )
                                        try_auto_start_rest_timer()

        st.divider()

        with st.expander("Explore all target options", expanded=False):
            target_templates = load_templates()
            target_workouts = load_workouts()

            if target_templates:
                target_template_names = get_template_names(target_templates)

                selected_target_template = st.selectbox(
                    "Template to explore",
                    target_template_names,
                    key="target_template",
                )

                for template in target_templates:
                    if template["template_name"] == selected_target_template:
                        for exercise in template["exercises"]:
                            st.markdown(f"**{exercise}**")

                            exercise_entries = get_entries_for_exercise(target_workouts, exercise)

                            if not exercise_entries:
                                st.caption("No history yet. Log this exercise first.")
                                continue

                            best_1rm = get_best_estimated_1rm(exercise_entries)
                            st.caption(f"Current best estimated 1RM: {best_1rm} lbs")

                            targets = get_workout_targets(best_1rm, exercise_entries)

                            if targets:
                                for rank, target in enumerate(targets, start=1):
                                    st.write(
                                        f"#{rank} — {int(target['target_weight'])} lbs × "
                                        f"{target['target_reps']} reps → "
                                        f"{target['new_estimated_1rm']} lb max "
                                        f"(+{target['improvement']} lbs)"
                                    )
                            else:
                                st.caption("No target options found in the search range.")

                        break
            else:
                st.caption("Save a workout template first to see target options.")

        # Bottom spacer so the fixed rest timer bar does not cover content.
        st.markdown("<div style='height: 70px;'></div>", unsafe_allow_html=True)
        render_sticky_rest_timer()


# --- Tab: Log ---
with tab_log:
    st.caption("Log any exercise outside of today's template workout.")

    exercise_library = load_exercise_library()

    if not exercise_library:
        st.info("No exercises in your library yet.")
        st.caption("Add exercises in the **Library** tab first.")
    else:
        exercise_names = []
        for exercise in exercise_library:
            exercise_names.append(exercise["name"])

        with st.form("manual_log_form"):
            workout_name = st.text_input(
                "Workout name",
                value="Push Day",
                help="Use the same name as a saved template if you want rotation mode to track it.",
            )
            selected_exercise_name = st.selectbox("Exercise", exercise_names, key="manual_log_exercise")
            selected_exercise = get_exercise_by_name(exercise_library, selected_exercise_name)
            exercise_name = selected_exercise_name

            if selected_exercise:
                with st.expander("Exercise details", expanded=False):
                    detail_col1, detail_col2 = st.columns(2)
                    detail_col1.caption("Category")
                    detail_col1.write(selected_exercise["category"])
                    detail_col2.caption("Primary muscle")
                    detail_col2.write(selected_exercise["primary_muscle"])
                    detail_col3, detail_col4 = st.columns(2)
                    detail_col3.caption("Rep range")
                    detail_col3.write(
                        f"{selected_exercise['rep_min']}-{selected_exercise['rep_max']} reps"
                    )
                    detail_col4.caption("Weight step")
                    detail_col4.write(f"{selected_exercise['weight_increment']} lbs")

                num_sets = st.number_input(
                    "Number of sets",
                    min_value=1,
                    max_value=10,
                    value=selected_exercise["default_sets"],
                    step=1,
                    key=f"num_sets_{selected_exercise_name}",
                )
                weight_step = selected_exercise["weight_increment"]
            else:
                num_sets = st.number_input("Number of sets", min_value=1, max_value=10, value=3, step=1)
                weight_step = 5.0

            st.caption("Enter weight and reps for each set.")
            logged_sets = []
            for set_number in range(1, int(num_sets) + 1):
                with st.container(border=True):
                    st.markdown(f"**Set {set_number}**")
                    weight_col, reps_col = st.columns(2)
                    weight = weight_col.number_input(
                        "Weight (lbs)",
                        min_value=0.0,
                        step=float(weight_step),
                        key=f"weight_{set_number}",
                    )
                    reps = reps_col.number_input(
                        "Reps",
                        min_value=1,
                        step=1,
                        key=f"reps_{set_number}",
                    )

                logged_sets.append({
                    "set_number": set_number,
                    "weight": weight,
                    "reps": reps,
                })

            # Each set is saved separately, so volume = weight * reps for one set
            for set_data in logged_sets:
                set_data["estimated_1rm"] = estimate_1rm(set_data["weight"], set_data["reps"])
                set_data["volume"] = set_data["weight"] * set_data["reps"]

            notes = st.text_area(
                "Notes (optional)",
                placeholder="How did the set feel? Any form cues?",
            )

            with st.expander("Preview logged sets"):
                st.write("**Exercise:**", exercise_name)
                for set_data in logged_sets:
                    st.write(
                        f"Set {set_data['set_number']}: "
                        f"{int(set_data['weight'])} lbs × {set_data['reps']} reps → "
                        f"{round(set_data['estimated_1rm'])} lb 1RM, "
                        f"{round(set_data['volume'])} lb volume"
                    )

                if notes:
                    st.write("**Notes:**", notes)

            submitted = st.form_submit_button(
                "Log Exercise",
                type="primary",
                use_container_width=True,
            )

        # If the last submit had warnings, ask for confirmation before saving.
        if st.session_state.pending_manual_log_submission is not None:
            for warning_message in st.session_state.pending_manual_log_warnings:
                st.warning(warning_message)

            confirm_col, cancel_col = st.columns(2)

            if confirm_col.button("Confirm Log Anyway", key="confirm_manual_log_anyway"):
                pending_submission = st.session_state.pending_manual_log_submission
                num_sets = len(pending_submission["logged_sets"])
                pr_messages = save_manual_log_submission(pending_submission)
                st.session_state.pending_manual_log_submission = None
                st.session_state.pending_manual_log_warnings = []
                st.success(f"Exercise logged! {num_sets} sets saved.")
                for message in pr_messages:
                    st.success(message)
                st.rerun()

            if cancel_col.button("Cancel Log", key="cancel_manual_log"):
                st.session_state.pending_manual_log_submission = None
                st.session_state.pending_manual_log_warnings = []
                st.rerun()

        if submitted:
            workouts = load_workouts()
            today = str(date.today())
            previous_entries = get_entries_for_exercise(workouts, exercise_name)

            submission = {
                "date": today,
                "workout_name": workout_name,
                "exercise_name": exercise_name,
                "logged_sets": logged_sets,
                "notes": notes,
            }

            warning_messages = get_manual_log_warnings(logged_sets, previous_entries)

            if warning_messages:
                st.session_state.pending_manual_log_submission = submission
                st.session_state.pending_manual_log_warnings = warning_messages
                st.rerun()
            else:
                pr_messages = save_manual_log_submission(submission)
                st.success(f"Exercise logged! {int(num_sets)} sets saved.")

                for message in pr_messages:
                    st.success(message)


# --- Tab: Progress ---
with tab_progress:
    st.caption("Personal records and trends per exercise.")

    all_workouts = load_workouts()
    history_exercise_names = get_unique_exercises(all_workouts)

    if history_exercise_names:
        selected_exercise = st.selectbox(
            "Exercise",
            history_exercise_names,
            key="history_exercise_select",
        )
        exercise_entries = get_entries_for_exercise(all_workouts, selected_exercise)

        best_1rm = 0
        heaviest_weight = 0
        highest_reps = 0
        highest_volume = 0

        for entry in exercise_entries:
            entry_1rm = get_entry_estimated_1rm(entry)
            entry_weight = get_entry_weight(entry)
            entry_reps = get_entry_reps(entry)
            entry_volume = get_entry_volume(entry)

            if entry_1rm > best_1rm:
                best_1rm = entry_1rm
            if entry_weight > heaviest_weight:
                heaviest_weight = entry_weight
            if entry_reps > highest_reps:
                highest_reps = entry_reps
            if entry_volume > highest_volume:
                highest_volume = entry_volume

        st.divider()
        metric_row1_col1, metric_row1_col2 = st.columns(2)
        metric_row1_col1.metric("Best estimated 1RM", f"{best_1rm} lbs")
        metric_row1_col2.metric("Heaviest weight", f"{int(heaviest_weight)} lbs")
        metric_row2_col1, metric_row2_col2 = st.columns(2)
        metric_row2_col1.metric("Highest reps", highest_reps)
        metric_row2_col2.metric("Highest volume", f"{highest_volume} lbs")

        st.divider()
        st.markdown("**1RM trend**")
        chart_data = get_1rm_trend_by_date(exercise_entries)

        if chart_data:
            st.line_chart(chart_data, x="date", y="estimated_1rm")
        else:
            st.caption("Not enough data for a chart yet.")

        with st.expander("View detailed history"):
            st.dataframe(exercise_entries, use_container_width=True)
    else:
        st.info("No workout history yet.")
        st.caption("Log sets in **Workout** or **Log** to see progress here.")


# --- Tab: Library ---
with tab_library:
    st.caption("Master list of exercises for logging and templates.")

    exercise_library = load_exercise_library()

    if exercise_library:
        st.dataframe(exercise_library, use_container_width=True)
    else:
        st.info("Your exercise library is empty.")
        st.caption("Add your first exercise below.")

    st.divider()

    with st.expander("Add new exercise", expanded=not exercise_library):
        st.caption("New exercises appear in Manual Log and template builders.")
        new_exercise_name = st.text_input(
            "Exercise name",
            key="library_exercise_name",
            placeholder="Bench Press",
        )
        detail_col1, detail_col2 = st.columns(2)
        new_category = detail_col1.text_input(
            "Category",
            placeholder="Push",
            key="library_category",
            help="Examples: Push, Pull, Legs",
        )
        new_primary_muscle = detail_col2.text_input(
            "Primary muscle",
            placeholder="Chest",
            key="library_primary_muscle",
        )
        sets_col, rep_min_col = st.columns(2)
        rep_max_col, increment_col = st.columns(2)
        new_default_sets = sets_col.number_input(
            "Default sets",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            key="library_default_sets",
        )
        new_rep_min = rep_min_col.number_input(
            "Rep min",
            min_value=1,
            step=1,
            value=8,
            key="library_rep_min",
        )
        new_rep_max = rep_max_col.number_input(
            "Rep max",
            min_value=1,
            step=1,
            value=12,
            key="library_rep_max",
        )
        new_weight_increment = increment_col.number_input(
            "Weight increment (lbs)",
            min_value=1,
            step=1,
            value=5,
            key="library_weight_increment",
        )

        if st.button(
            "Add Exercise",
            key="add_exercise_to_library_button",
            type="primary",
            use_container_width=True,
        ):
            name = new_exercise_name.strip()

            if not name:
                st.warning("Enter an exercise name.")
            elif not new_category.strip():
                st.warning("Enter a category.")
            elif not new_primary_muscle.strip():
                st.warning("Enter a primary muscle.")
            elif new_rep_min > new_rep_max:
                st.warning("Rep min must be less than or equal to rep max.")
            else:
                exercise_library = load_exercise_library()
                already_exists = False

                for exercise in exercise_library:
                    if exercise["name"] == name:
                        already_exists = True
                        break

                if already_exists:
                    st.warning(f"'{name}' already exists in the library.")
                else:
                    new_exercise = {
                        "name": name,
                        "category": new_category.strip(),
                        "primary_muscle": new_primary_muscle.strip(),
                        "default_sets": int(new_default_sets),
                        "rep_min": int(new_rep_min),
                        "rep_max": int(new_rep_max),
                        "weight_increment": int(new_weight_increment),
                    }

                    exercise_library.append(new_exercise)
                    save_exercise_library(exercise_library)
                    st.success(f"'{name}' added to the library!")


# --- Tab: Settings (templates, plan, admin) ---
with tab_settings:
    st.caption("Templates, weekly plan, and advanced data tools.")

    with st.expander("Weekly workout plan", expanded=False):
        st.markdown("**Weekly workout plan**")
        workout_plan = load_workout_plan()
        plan_templates = load_templates()

        plan_options = ["Rest"]
        for template in plan_templates:
            plan_options.append(template["template_name"])

        saved_mode = workout_plan.get("schedule_mode", "weekly")
        mode_index = 0 if saved_mode == "weekly" else 1

        schedule_mode = st.radio(
            "Default planning mode (saved to your plan file)",
            ["Weekly Schedule Mode", "Rotation Mode"],
            index=mode_index,
            key="planning_mode_radio",
            horizontal=True,
        )
        st.caption("Today's Workout has its own mode picker — this sets the default saved in your plan.")

        week_days = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]

        st.markdown(f"**{workout_plan['plan_name']}**")
        st.caption("Assign a workout template to each day. Blank days count as rest in rotation mode.")

        day_col1, day_col2 = st.columns(2)
        day_selections = {}

        for index, day_name in enumerate(week_days):
            saved_workout = workout_plan["weekly_schedule"].get(day_name, "")
            if saved_workout == "":
                saved_workout = "Rest"

            if saved_workout not in plan_options:
                saved_workout = "Rest"

            day_index = plan_options.index(saved_workout)
            target_col = day_col1 if index % 2 == 0 else day_col2
            day_selections[day_name] = target_col.selectbox(
                day_name,
                plan_options,
                index=day_index,
                key=f"weekly_plan_{day_name}",
            )

        if schedule_mode == "Rotation Mode":
            rotation_preview = build_rotation_from_weekly_plan(workout_plan["weekly_schedule"])
            st.caption("Rotation order (Mon → Sun): " + " → ".join(rotation_preview))

        if st.button("Save Workout Plan", key="save_weekly_plan_button", type="primary"):
            if schedule_mode == "Weekly Schedule Mode":
                saved_schedule_mode = "weekly"
            else:
                saved_schedule_mode = "rotation"

            updated_plan = {
                "plan_name": workout_plan["plan_name"],
                "schedule_mode": saved_schedule_mode,
                "weekly_schedule": {},
            }

            for day_name in week_days:
                selected_workout = day_selections[day_name]
                if selected_workout == "Rest":
                    updated_plan["weekly_schedule"][day_name] = ""
                else:
                    updated_plan["weekly_schedule"][day_name] = selected_workout

            save_workout_plan(updated_plan)
            st.success("Workout plan saved!")

    st.divider()

    with st.expander("Create new template", expanded=False):
        st.caption("Build a reusable list of exercises for Today's Workout.")
        template_name = st.text_input("Template name", placeholder="Push Day")

        create_exercise_library = load_exercise_library()
        create_library_exercise_names = []
        for exercise in create_exercise_library:
            create_library_exercise_names.append(exercise["name"])

        if not create_library_exercise_names:
            st.info("Add exercises in **Library** before creating templates.")
        else:
            new_exercise_name = st.selectbox(
                "Exercise to add",
                create_library_exercise_names,
                key="create_template_exercise_select",
            )

            if st.button("Add Exercise", key="add_exercise_to_template_button"):
                if new_exercise_name in st.session_state.template_exercises:
                    st.warning(f"'{new_exercise_name}' is already in this template.")
                else:
                    st.session_state.template_exercises.append(new_exercise_name)
                    st.success(f"Added {new_exercise_name} to the template.")

        if st.session_state.template_exercises:
            st.markdown("**Exercises in this template**")
            for exercise in st.session_state.template_exercises:
                st.markdown(f"- {exercise}")

        if st.button("Save Template", key="save_template_button", type="primary"):
            name = template_name.strip()

            if not name:
                st.warning("Enter a workout template name first.")
            elif not st.session_state.template_exercises:
                st.warning("Add at least one exercise before saving.")
            else:
                templates = load_templates()
                new_template = {
                    "template_name": name,
                    "exercises": list(st.session_state.template_exercises),
                }

                found = False
                for index, template in enumerate(templates):
                    if template["template_name"] == name:
                        templates[index] = new_template
                        found = True
                        break

                if not found:
                    templates.append(new_template)

                save_templates(templates)
                st.session_state.template_exercises = []
                st.success(f"Template '{name}' saved!")

    with st.expander("Edit existing template", expanded=False):
        edit_templates = load_templates()

        if edit_templates:
            edit_template_names = get_template_names(edit_templates)
            selected_edit_template = st.selectbox(
                "Template to edit",
                edit_template_names,
                key="edit_template_select",
            )

            # Load the selected template into session state when the choice changes
            if st.session_state.get("edit_template_source") != selected_edit_template:
                template_to_edit = find_template_by_name(edit_templates, selected_edit_template)
                st.session_state.edit_template_source = selected_edit_template
                st.session_state.edit_template_name = template_to_edit["template_name"]
                st.session_state.edit_template_exercises = list(template_to_edit["exercises"])

            edited_template_name = st.text_input(
                "Template name",
                value=st.session_state.edit_template_name,
                key="edit_template_name_input",
            )

            st.markdown("**Exercises**")
            # Exercise order lives in session state until the user clicks Save Changes.
            exercise_count = len(st.session_state.edit_template_exercises)
            for index, exercise in enumerate(st.session_state.edit_template_exercises):
                exercise_col, up_col, down_col, remove_col = st.columns([4, 1, 1, 1])
                exercise_col.write(exercise)

                if up_col.button(
                    "Move Up",
                    key=f"move_up_edit_exercise_{selected_edit_template}_{index}_{exercise}",
                    disabled=(index == 0),
                ):
                    exercises = st.session_state.edit_template_exercises
                    exercises[index - 1], exercises[index] = exercises[index], exercises[index - 1]
                    st.rerun()

                if down_col.button(
                    "Move Down",
                    key=f"move_down_edit_exercise_{selected_edit_template}_{index}_{exercise}",
                    disabled=(index == exercise_count - 1),
                ):
                    exercises = st.session_state.edit_template_exercises
                    exercises[index], exercises[index + 1] = exercises[index + 1], exercises[index]
                    st.rerun()

                if remove_col.button(
                    "Remove",
                    key=f"remove_edit_exercise_{selected_edit_template}_{index}_{exercise}",
                ):
                    st.session_state.edit_template_exercises.pop(index)
                    st.rerun()

            edit_exercise_library = load_exercise_library()
            library_exercise_names = []
            for exercise in edit_exercise_library:
                library_exercise_names.append(exercise["name"])

            add_exercise_col, add_button_col = st.columns([3, 1])

            if library_exercise_names:
                exercise_to_add = add_exercise_col.selectbox(
                    "Add exercise from library",
                    library_exercise_names,
                    key="edit_add_exercise_select",
                )
            else:
                exercise_to_add = add_exercise_col.text_input(
                    "Exercise name",
                    placeholder="Bench Press",
                    key="edit_add_exercise_text",
                )

            if add_button_col.button("Add Exercise", key="edit_add_exercise_button"):
                exercise_name = exercise_to_add.strip()

                if not exercise_name:
                    st.warning("Choose or enter an exercise name first.")
                elif exercise_name in st.session_state.edit_template_exercises:
                    st.warning(f"'{exercise_name}' is already in this template.")
                else:
                    st.session_state.edit_template_exercises.append(exercise_name)
                    st.rerun()

            if st.button("Save Changes", key="save_edit_template_button", type="primary"):
                new_name = edited_template_name.strip()
                original_name = st.session_state.edit_template_source

                if not new_name:
                    st.warning("Enter a template name.")
                elif not st.session_state.edit_template_exercises:
                    st.warning("Add at least one exercise before saving.")
                else:
                    templates = load_templates()
                    updated_template = {
                        "template_name": new_name,
                        "exercises": list(st.session_state.edit_template_exercises),
                    }

                    found = False
                    for index, template in enumerate(templates):
                        if template["template_name"] == original_name:
                            templates[index] = updated_template
                            found = True
                            break

                    if found:
                        save_templates(templates)
                        st.session_state.edit_template_source = new_name
                        st.session_state.edit_template_name = new_name

                        if original_name != new_name:
                            days_updated = update_workout_plan_template_name(
                                original_name, new_name
                            )
                            if days_updated > 0:
                                st.success(
                                    f"Template renamed from {original_name} to {new_name}. "
                                    f"Updated {days_updated} weekly plan day(s)."
                                )
                            else:
                                st.success(
                                    "Template renamed. Weekly plan did not need updates."
                                )
                        else:
                            st.success(f"Template '{new_name}' updated!")
                    else:
                        st.warning("Could not find that template to update.")
        else:
            st.info("Save a template first before editing.")

    with st.expander("View saved templates", expanded=False):
        saved_templates = load_templates()

        if saved_templates:
            template_names = get_template_names(saved_templates)
            selected_template_name = st.selectbox(
                "Template",
                template_names,
                key="view_template",
            )

            for template in saved_templates:
                if template["template_name"] == selected_template_name:
                    exercise_rows = []
                    for exercise in template["exercises"]:
                        exercise_rows.append({"Exercise": exercise})

                    st.dataframe(exercise_rows, use_container_width=True, hide_index=True)
                    break
        else:
            st.info("No templates saved yet. Create one above to get started.")

    render_admin_data_section()

