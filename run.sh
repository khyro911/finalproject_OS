#!/usr/bin/env sh
# Start the Process & Resource Monitor on Linux.
# The first run creates a private Python environment in .venv and installs psutil.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    echo "Setting up for the first run..."
    python3 -m venv .venv || { echo "Install venv first:  sudo apt install python3-venv python3-tk"; exit 1; }
    .venv/bin/pip install --quiet -r requirements.txt || exit 1
fi

if [ "$1" = "--text" ]; then
    exec .venv/bin/python monitor.py
fi
exec .venv/bin/python app.py
