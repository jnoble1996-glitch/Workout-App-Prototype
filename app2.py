import streamlit as st
import math
import json
from datetime import date

WORKOUTS_FILE = "workouts.json"
TEMPLATES_FILE = "workout_templates.json"
EXERCISE_LIBRARY_FILE = "exercise_library.json"
WORKOUT_PLAN_FILE = "workout_plan.json"

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


def load_workouts():
    """Read all logged workouts from workouts.json."""
    try:
        with open(WORKOUTS_FILE, "r") as file:
            content = file.read().strip()
            if content == "":
                return []
            return json.loads(content)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        st.error(f"Could not read {WORKOUTS_FILE}. The file contains invalid JSON. Using an empty workout log.")
        return []


def save_workouts(workouts):
    """Write all logged workouts to workouts.json."""
    with open(WORKOUTS_FILE, "w") as file:
        json.dump(workouts, file, indent=2)


def load_templates():
    """Read all saved workout templates from workout_templates.json."""
    try:
        with open(TEMPLATES_FILE, "r") as file:
            content = file.read().strip()
            if content == "":
                return []
            return json.loads(content)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        st.error(
            f"Could not read {TEMPLATES_FILE}. The file contains invalid JSON. Using no saved templates."
        )
        return []


def save_templates(templates):
    """Write all workout templates to workout_templates.json."""
    with open(TEMPLATES_FILE, "w") as file:
        json.dump(templates, file, indent=2)


def load_workout_plan():
    """Read the weekly workout plan from workout_plan.json."""
    try:
        with open(WORKOUT_PLAN_FILE, "r") as file:
            content = file.read().strip()
            if content == "":
                return dict(DEFAULT_WORKOUT_PLAN)
            plan = json.loads(content)
            if "schedule_mode" not in plan:
                plan["schedule_mode"] = "weekly"
            return plan
    except FileNotFoundError:
        return dict(DEFAULT_WORKOUT_PLAN)
    except json.JSONDecodeError:
        st.error(
            f"Could not read {WORKOUT_PLAN_FILE}. The file contains invalid JSON. Using the default plan."
        )
        return dict(DEFAULT_WORKOUT_PLAN)


def save_workout_plan(workout_plan):
    """Write the weekly workout plan to workout_plan.json."""
    with open(WORKOUT_PLAN_FILE, "w") as file:
        json.dump(workout_plan, file, indent=2)


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


def load_exercise_library():
    """Read all exercises from exercise_library.json."""
    try:
        with open(EXERCISE_LIBRARY_FILE, "r") as file:
            content = file.read().strip()
            if content == "":
                return []
            return json.loads(content)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        st.error(
            f"Could not read {EXERCISE_LIBRARY_FILE}. "
            "The file contains invalid JSON. Using an empty exercise library."
        )
        return []


def save_exercise_library(exercise_library):
    """Write all exercises to exercise_library.json."""
    with open(EXERCISE_LIBRARY_FILE, "w") as file:
        json.dump(exercise_library, file, indent=2)


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
        exercise_entries = get_entries_for_exercise(workouts, exercise)

        if not exercise_entries:
            exercise_plans.append({
                "exercise_index": exercise_index,
                "planned_exercise": exercise,
                "exercise": exercise,
                "swapped_to": None,
                "has_history": False,
                "recommendations": [],
            })
            continue

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

        exercise_plans.append({
            "exercise_index": exercise_index,
            "planned_exercise": exercise,
            "exercise": exercise,
            "swapped_to": None,
            "has_history": True,
            "recommendations": recommendations,
        })

    return {
        "template_name": template_name,
        "selection_mode": selection_mode,
        "generated_date": str(date.today()),
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


# --- 1. App title ---
st.title("Workout Programming App")

# Session state used across tabs
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

if "template_exercises" not in st.session_state:
    st.session_state.template_exercises = []

tab_todays_workout, tab_manual_log, tab_exercise_history, tab_exercise_library, tab_workout_templates, tab_data = st.tabs([
    "🏋️ Today's Workout",
    "📝 Manual Log",
    "📈 Exercise History / Progress",
    "📚 Exercise Library",
    "🗓️ Workout Templates / Plans",
    "⚙️ Data",
])


# --- Tab: Today's Workout ---
with tab_todays_workout:
    st.subheader("Today's Workout")
    st.caption("Your main dashboard for today's session and recommended sets.")

    # completed_recommended_sets tracks logged sets by stable set_id from active_workout_plan.
    todays_templates = load_templates()
    todays_workouts = load_workouts()

    if todays_templates:
        todays_template_names = get_template_names(todays_templates)
        workout_plan = load_workout_plan()
        today_key = str(date.today())

        # --- Workout selection mode ---
        selection_mode = st.radio(
            "Workout selection mode",
            ["Weekly Schedule Mode", "Rotation Mode", "Manual Override"],
            key="workout_selection_mode",
            horizontal=True,
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

        # Weekly mode: auto-select once per calendar day
        if mode_key == "weekly":
            if st.session_state.get("workout_plan_date") != today_key:
                st.session_state.workout_plan_date = today_key
                if recommended_template:
                    st.session_state.todays_workout_template = recommended_template

        # Rotation mode: auto-select when the rotation recommendation changes
        if mode_key == "rotation":
            rotation_list = build_rotation_from_weekly_plan(workout_plan["weekly_schedule"])
            last_workout = get_most_recent_logged_workout_name(todays_workouts)
            next_in_rotation = get_next_rotation_workout(rotation_list, last_workout)
            rotation_key = f"{last_workout}::{next_in_rotation}"

            if st.session_state.get("rotation_plan_key") != rotation_key:
                st.session_state.rotation_plan_key = rotation_key
                if recommended_template:
                    st.session_state.todays_workout_template = recommended_template

        # When switching away from Manual Override, apply the new mode's recommendation
        if st.session_state.get("last_workout_selection_mode") != selection_mode:
            st.session_state.last_workout_selection_mode = selection_mode
            if recommended_template:
                st.session_state.todays_workout_template = recommended_template

        st.divider()

        # --- Mode status and template picker ---
        status_col, template_col = st.columns([1, 1])

        with status_col:
            st.markdown("**Today's recommendation**")
            if mode_key == "weekly":
                planned_workout = get_weekly_mode_template(workout_plan, todays_templates)
                if planned_workout == "":
                    st.info("Today is scheduled as a rest day.")
                else:
                    st.success(planned_workout)
            elif mode_key == "rotation":
                if recommended_template is None:
                    st.info("The next item in your rotation is a rest day.")
                else:
                    st.success(recommended_template)
                if last_workout:
                    st.caption(f"Last logged workout: {last_workout}")
                st.caption("Rotation order: " + " → ".join(rotation_list))
            else:
                st.info("Manual mode — pick any template on the right.")

        with template_col:
            if mode_key == "manual":
                template_label = "Choose workout template"
                template_help = "Select any saved template for today's session."
            else:
                template_label = "Workout template"
                template_help = "Auto-selected from your plan. Change this anytime to override."

            selected_todays_template = st.selectbox(
                template_label,
                todays_template_names,
                key="todays_workout_template",
                help=template_help,
            )
            st.caption(f"Currently showing: **{selected_todays_template}**")

        refresh_col, new_workout_col = st.columns(2)

        if refresh_col.button("Refresh recommendations", key="refresh_recommendations_button"):
            # Force a new plan using the latest workout history.
            st.session_state.active_workout_plan = None

        if new_workout_col.button("Start new workout", key="start_new_workout_button"):
            # Clear the frozen plan and completed-set tracking for a fresh session.
            st.session_state.active_workout_plan = None
            st.session_state.completed_recommended_sets = []

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

        active_plan = st.session_state.active_workout_plan

        st.divider()
        st.markdown("**Recommended sets**")
        st.caption("Each card shows target sets. Edit actual weight and reps before logging if needed.")
        st.info("Recommendations are locked for this workout. Use Refresh recommendations to recalculate.")

        if active_plan:
            swap_exercise_library = load_exercise_library()
            swap_exercise_names = []
            for library_exercise in swap_exercise_library:
                swap_exercise_names.append(library_exercise["name"])

            for exercise_plan in active_plan["exercises"]:
                # planned_exercise = template slot; swapped_to = today's temporary replacement.
                exercise_index = exercise_plan.get("exercise_index", 0)
                planned_exercise = exercise_plan.get(
                    "planned_exercise",
                    exercise_plan.get("exercise", ""),
                )
                swapped_to = exercise_plan.get("swapped_to")
                todays_exercise = get_todays_exercise_name(exercise_plan)

                last_swap = get_last_swap_for_exercise(
                    todays_workouts,
                    selected_todays_template,
                    planned_exercise,
                )

                with st.container(border=True):
                    st.markdown(f"**Planned:** {planned_exercise}")
                    if swapped_to:
                        st.markdown(f"**Today's exercise:** {todays_exercise}")
                        st.caption(f"Swapped from {planned_exercise}")

                    if last_swap:
                        st.info(
                            f"Last time you did this workout, you swapped {planned_exercise} "
                            f"for {last_swap['swapped_to']}."
                        )
                        for logged_set in last_swap["sets"]:
                            st.write(
                                f"Set {logged_set['set_number']}: "
                                f"{int(logged_set['weight'])} lbs × {logged_set['reps']}"
                            )

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
                        ):
                            if replacement_exercise == "Use planned exercise":
                                apply_exercise_swap_to_active_plan(exercise_index, None)
                            else:
                                apply_exercise_swap_to_active_plan(
                                    exercise_index,
                                    replacement_exercise,
                                )
                            st.rerun()

                    if not exercise_plan["has_history"]:
                        st.caption("No history yet. Log this exercise in Manual Log first.")
                        continue

                    if not exercise_plan["recommendations"]:
                        st.caption("No target options found in the search range.")
                        continue

                for recommendation in exercise_plan["recommendations"]:
                    rank = recommendation["rank"]

                    with st.container(border=True):
                        st.markdown(f"**{planned_exercise}** · Recommendation #{rank}")
                        st.caption(
                            f"Set 1 projected 1RM: {recommendation['projected_1rm']} lbs "
                            f"(+{recommendation['improvement']} lbs)"
                        )
                        if swapped_to:
                            st.caption(f"Logging as: {todays_exercise}")
                        st.markdown("**Set plan**")

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

                            st.markdown(f"**Set {set_number}**")
                            st.caption(
                                f"Target: {int(target_weight)} lbs × {target_reps} reps"
                            )

                            if is_completed:
                                st.success("Completed ✅")
                            else:
                                actual_weight_col, actual_reps_col, button_col = st.columns([2, 2, 1])

                                actual_weight = actual_weight_col.number_input(
                                    "Actual weight (lbs)",
                                    min_value=0,
                                    value=int(target_weight),
                                    step=5,
                                    format="%d",
                                    key=f"actual_weight_{input_key_base}",
                                )
                                actual_reps = actual_reps_col.number_input(
                                    "Actual reps",
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

                                if button_col.button(
                                    "Log Set",
                                    key=f"log_recommended_set_{set_id}",
                                    use_container_width=True,
                                    disabled=needs_confirmation and not confirm_suspicious_set,
                                ):
                                    actual_estimated_1rm = round(
                                        estimate_1rm(actual_weight, actual_reps)
                                    )
                                    logged_weight = int(actual_weight)
                                    logged_reps = int(actual_reps)
                                    workouts = load_workouts()
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
                                        "logged_from": "Today's Workout",
                                    }

                                    if swapped_to:
                                        new_entry["planned_exercise"] = planned_exercise
                                        new_entry["swapped_from"] = planned_exercise
                                        new_entry["swapped_to"] = swapped_to

                                    workouts.append(new_entry)
                                    save_workouts(workouts)
                                    st.session_state.completed_recommended_sets.append(set_id)
                                    st.success(
                                        f"Logged Set {set_number}: "
                                        f"{logged_weight} lbs × {logged_reps} reps"
                                    )

        st.divider()

        with st.expander("Explore all target options"):
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
    else:
        st.info("Save a workout template first to use Today's Workout.")


# --- Tab: Manual Log ---
with tab_manual_log:
    st.subheader("Manual Log")
    st.caption("Log sets for any exercise. Each set is saved as its own entry.")

    exercise_library = load_exercise_library()

    if not exercise_library:
        st.info("Add exercises in the Exercise Library tab before using Manual Log.")
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
                detail_col1, detail_col2, detail_col3, detail_col4 = st.columns(4)
                detail_col1.caption("Category")
                detail_col1.write(selected_exercise["category"])
                detail_col2.caption("Primary muscle")
                detail_col2.write(selected_exercise["primary_muscle"])
                detail_col3.caption("Rep range")
                detail_col3.write(f"{selected_exercise['rep_min']}-{selected_exercise['rep_max']} reps")
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
            header_set, header_weight, header_reps = st.columns([1, 2, 2])
            header_set.write("**Set**")
            header_weight.write("**Weight (lbs)**")
            header_reps.write("**Reps**")

            logged_sets = []
            for set_number in range(1, int(num_sets) + 1):
                col_set, col_weight, col_reps = st.columns([1, 2, 2])
                col_set.write(f"Set {set_number}")

                weight = col_weight.number_input(
                    "Weight (lbs)",
                    min_value=0.0,
                    step=float(weight_step),
                    key=f"weight_{set_number}",
                    label_visibility="collapsed",
                )
                reps = col_reps.number_input(
                    "Reps",
                    min_value=1,
                    step=1,
                    key=f"reps_{set_number}",
                    label_visibility="collapsed",
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

            needs_manual_confirmation = False
            for set_data in logged_sets:
                set_warnings, set_needs_confirmation = get_suspicious_entry_warnings(
                    set_data["weight"],
                    set_data["reps"],
                    exercise_name,
                    load_workouts(),
                )
                for warning_message in set_warnings:
                    st.warning(
                        f"Set {set_data['set_number']}: {warning_message}"
                    )
                if set_needs_confirmation:
                    needs_manual_confirmation = True

            confirm_suspicious_manual_log = True
            if needs_manual_confirmation:
                confirm_suspicious_manual_log = st.checkbox(
                    "I confirm these sets look correct",
                    key="confirm_suspicious_manual_log",
                )

            submitted = st.form_submit_button(
                "Log Exercise",
                type="primary",
                use_container_width=True,
                disabled=needs_manual_confirmation and not confirm_suspicious_manual_log,
            )

        if submitted:
            workouts = load_workouts()
            today = str(date.today())
            previous_entries = get_entries_for_exercise(workouts, exercise_name)
            pr_messages = []

            for set_data in logged_sets:
                for message in detect_prs(set_data, previous_entries):
                    pr_messages.append(message)

                new_entry = {
                    "date": today,
                    "workout_name": workout_name,
                    "exercise": exercise_name,
                    "set_number": set_data["set_number"],
                    "weight": set_data["weight"],
                    "reps": set_data["reps"],
                    "estimated_1rm": round(set_data["estimated_1rm"]),
                    "volume": round(set_data["volume"]),
                    "notes": notes,
                }
                workouts.append(new_entry)

            save_workouts(workouts)
            st.success(f"Exercise logged! {int(num_sets)} sets saved.")

            for message in pr_messages:
                st.success(message)


# --- Tab: Exercise History ---
with tab_exercise_history:
    st.subheader("Exercise History / Progress")
    st.caption("Track personal records and progress over time for each exercise.")

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
        metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
        metric_col1.metric("Best estimated 1RM", f"{best_1rm} lbs")
        metric_col2.metric("Heaviest weight", f"{int(heaviest_weight)} lbs")
        metric_col3.metric("Highest reps", highest_reps)
        metric_col4.metric("Highest volume", f"{highest_volume} lbs")

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
        st.info("Log some exercises first to see history here.")


# --- Tab: Exercise Library ---
with tab_exercise_library:
    st.subheader("Exercise Library")
    st.caption("Your master list of exercises used when logging workouts and building templates.")

    exercise_library = load_exercise_library()

    if exercise_library:
        st.dataframe(exercise_library, use_container_width=True)
    else:
        st.info("No exercises in the library yet. Add your first one below.")

    st.divider()

    with st.expander("Add New Exercise"):
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
        sets_col, rep_min_col, rep_max_col, increment_col = st.columns(4)
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

        if st.button("Add Exercise", key="add_exercise_to_library_button", type="primary"):
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


# --- Tab: Workout Templates ---
with tab_workout_templates:
    st.subheader("Workout Templates / Plans")
    st.caption("Build templates, set your weekly plan, and choose how Today's Workout picks a session.")

    with st.expander("Weekly Workout Plan", expanded=True):
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

    with st.expander("Create New Template"):
        st.caption("Build a reusable list of exercises for Today's Workout.")
        template_name = st.text_input("Template name", placeholder="Push Day")
        new_exercise_name = st.text_input("Exercise to add", placeholder="Bench Press")

        if st.button("Add Exercise", key="add_exercise_to_template_button"):
            exercise = new_exercise_name.strip()
            if exercise:
                st.session_state.template_exercises.append(exercise)
                st.success(f"Added {exercise} to the template.")
            else:
                st.warning("Enter an exercise name first.")

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

    with st.expander("Edit Existing Template"):
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
            for index, exercise in enumerate(st.session_state.edit_template_exercises):
                exercise_col, remove_col = st.columns([4, 1])
                exercise_col.write(exercise)

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
                        st.success(f"Template '{new_name}' updated!")
                    else:
                        st.warning("Could not find that template to update.")
        else:
            st.info("Save a template first before editing.")

    st.divider()

    st.markdown("**View Saved Templates**")
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


# --- Tab: Data ---
with tab_data:
    st.subheader("Data")
    st.caption("Advanced view of raw app data. Most day-to-day use happens in the other tabs.")

    with st.expander("Raw Workout Log"):
        st.warning("This is raw stored data — not the main workout interface.")
        workouts = load_workouts()

        if workouts:
            st.dataframe(workouts, use_container_width=True)
        else:
            st.info("No workouts logged yet. Use **Manual Log** to save your first entry.")

    with st.expander("Edit Workout History"):
        st.caption("Filter first, then edit the exact row you need.")
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
                # Step 2: show matching rows in a readable table-like layout.
                header_col1, header_col2, header_col3, header_col4, header_col5, header_col6, header_col7, header_col8, header_col9, header_col10 = st.columns(
                    [0.9, 1, 1, 0.5, 0.9, 0.7, 1, 0.5, 0.7, 0.8]
                )
                header_col1.write("**Date**")
                header_col2.write("**Workout**")
                header_col3.write("**Exercise**")
                header_col4.write("**Set**")
                header_col5.write("**Weight × Reps**")
                header_col6.write("**e1RM**")
                header_col7.write("**Notes**")
                header_col8.write("**Edit**")
                header_col9.write("**Delete Set**")
                header_col10.write("**Delete Exercise**")

                for original_index in matching_indices:
                    entry = editable_workouts[original_index]
                    notes_text = entry.get("notes", "")
                    if notes_text == "":
                        notes_preview = "—"
                    elif len(notes_text) > 40:
                        notes_preview = notes_text[:40] + "..."
                    else:
                        notes_preview = notes_text

                    row_col1, row_col2, row_col3, row_col4, row_col5, row_col6, row_col7, row_col8, row_col9, row_col10 = st.columns(
                        [0.9, 1, 1, 0.5, 0.9, 0.7, 1, 0.5, 0.7, 0.8]
                    )
                    row_col1.write(entry.get("date", ""))
                    row_col2.write(entry.get("workout_name", ""))
                    row_col3.write(entry.get("exercise", ""))
                    row_col4.write(str(entry.get("set_number", "?")))
                    row_col5.write(
                        f"{int(entry.get('weight', 0))} lbs × {entry.get('reps', 0)}"
                    )
                    row_col6.write(str(entry.get("estimated_1rm", "—")))
                    row_col7.write(notes_preview)

                    if row_col8.button("Edit", key=f"edit_workout_row_{original_index}"):
                        # Store the original workouts.json index, not the filtered row position.
                        st.session_state.edit_workout_entry_index = original_index
                        st.session_state.delete_set_index = None
                        st.session_state.delete_exercise_info = None
                        st.rerun()

                    if row_col9.button("Delete Set", key=f"delete_set_row_{original_index}"):
                        st.session_state.delete_set_index = original_index
                        st.session_state.delete_exercise_info = None
                        st.session_state.edit_workout_entry_index = None
                        st.rerun()

                    if row_col10.button("Delete Exercise", key=f"delete_exercise_row_{original_index}"):
                        entry_date = entry.get("date", "Unknown date")
                        workout_name = entry.get("workout_name", "Unknown workout")
                        exercise_name = entry.get("exercise", "Unknown exercise")
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
                        workouts_after_delete = load_workouts()
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
                    workouts_after_delete = load_workouts()
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
