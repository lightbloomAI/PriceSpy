#!/bin/bash
# Try python3 first (Mac/Linux), fall back to python (some systems)
python3 "$(dirname "$0")/user-prompt-submit.py" 2>/dev/null || python "$(dirname "$0")/user-prompt-submit.py"
