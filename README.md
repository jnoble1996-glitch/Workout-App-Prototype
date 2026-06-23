# Workout Programming App

A personal prototype Streamlit app for logging workouts, building templates, and getting set recommendations based on your history.

This is currently a **personal prototype** — not a production app. Data is stored locally on your machine (or on the Streamlit host when deployed).

## What it does

- **Today's Workout** — main gym dashboard with recommended sets, baseline logging, exercise swaps, and session tracking
- **Manual Log** — log any exercise outside a template workout
- **Exercise History** — view progress and personal records
- **Exercise Library** — define exercises with rep ranges and weight increments
- **Workout Templates / Plans** — create templates and weekly schedules
- **Data** — raw workout log, edit/delete history, and backups

## Install dependencies

Create a virtual environment (recommended), then install packages:

```bash
python -m venv .venv
```

Activate it:

- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source .venv/bin/activate`

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run locally

From the project folder:

```bash
python -m streamlit run app2.py
```

Streamlit will open the app in your browser (usually `http://localhost:8501`).

## Data files

| File | Purpose |
|------|---------|
| `workout_app.db` | SQLite database for logged workout history |
| `workouts.json` | Legacy workout log (migrated into SQLite on first run if present) |
| `workout_templates.json` | Saved workout templates |
| `exercise_library.json` | Exercise definitions |
| `workout_plan.json` | Weekly schedule and rotation plan |
| `backups/` | Automatic JSON backups before edits and deletes |

On a fresh clone, you may need to add exercises and templates through the app before Today's Workout has content to show.

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub (without personal data — see `.gitignore`).
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect the repo.
3. Set the main file path to **`app2.py`**.
4. Deploy.

Note: Cloud deployments start with empty data unless you provide your own database or JSON files. For a personal prototype, local use is the simplest setup.

## Requirements

- Python 3.9+ recommended
- See `requirements.txt` for Python package dependencies
