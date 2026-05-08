#!/bin/bash
# Replace YOUR_USERNAME and path to your python env
CONDA_PY="/opt/miniconda3/envs/algtrade/bin/python"
SCRIPT_PATH="/Users/nathanwang/algtrade/spx_0dte_dashboard/dashboard.py"

# AppleScript to toggle a visual terminal window
osascript -e "tell application \"Terminal\" to do script \"$CONDA_PY $SCRIPT_PATH\""