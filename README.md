# Workout Programming App

A personal workout tracking prototype built with Streamlit. Log sets in the gym, build workout templates, follow a weekly plan, and get set recommendations based on your history.

This is currently a **personal prototype** — not a production app. Data is stored on your machine locally, or on the Streamlit host when deployed.

## Entrypoint

**`app2.py`** is the Streamlit entrypoint for this project. Use it for local runs and Streamlit Community Cloud deployment.

## What it does

- **Today's Workout** — main gym dashboard with recommended sets, baseline logging, exercise swaps, session tracking, rest timer, and sticky timer bar
- **Manual Log** — log any exercise outside a template workout
- **Exercise History** — view progress and personal records
- **Exercise Library** — define exercises with rep ranges and weight increments
- **Workout Templates / Plans** — create templates, weekly schedules, and rotation plans
- **Data** — raw workout log, edit/delete history, and automatic backups

## Install dependencies

Create a virtual environment (recommended):

```bash
python -m venv .venv
```

Activate it:

- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source .venv/bin/activate`

Install packages:

```bash
pip install -r requirements.txt
```

## Run locally

From the project folder:

```bash
python -m streamlit run app2.py
```

Streamlit opens the app in your browser (usually `http://localhost:8501`).

## Data files

| File | Purpose |
|------|---------|
| `workout_app.db` | SQLite database for logged workout history |
| `workouts.json` | Legacy workout log (migrated into SQLite on first run if present) |
| `workout_templates.json` | Saved workout templates |
| `exercise_library.json` | Exercise definitions |
| `workout_plan.json` | Weekly schedule and rotation plan |
| `backups/` | Automatic JSON backups before edits and deletes |

On a fresh clone or cloud deploy, the app starts with empty data until you add exercises and templates in the app.

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub without personal workout data (see `.gitignore`).
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect the repository.
3. Set the main file path to **`app2.py`**.
4. Deploy.

Cloud deployments start with empty data unless you add your own files. For a personal prototype, local use is the simplest setup.

## Requirements

- Python 3.9+ recommended
- See `requirements.txt` for third-party Python packages
