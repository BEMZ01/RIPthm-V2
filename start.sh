#!/bin/bash

# Check if script folder path is provided
if [ -z "$1" ]; then
  echo "Error: Please provide the path to the bot script folder as an argument."
  exit 1
fi

# Check if folder exists
if [ ! -d "$1" ]; then
  echo "Error: Folder '$1' does not exist."
  exit 1
fi

# Check if main.py exists within the folder
if [ ! -f "$1/main.py" ]; then
  echo "Error: File 'main.py' not found in folder '$1'."
  exit 1
fi

# Navigate to the script folder
cd "$1" || exit

# Save requirements for comparason later
cp requirements.txt requirements.old.txt

# Update the bot (git pull)
git pull

if [ -f requirements.old.txt ]; then
  mv requirements.txt requirements.new.txt  # Rename current requirements
  diff requirements.old.txt requirements.new.txt > requirements.diff
  if [ -s requirements.diff ]; then # Check if there were any differences
    echo "requirements.txt has changed. Refreshing dependencies..."
    source venv/bin/activate
    python -m ensurepip --upgrade
    python -m pip install -r requirements.new.txt
    deactivate
  else
    echo "requirements.txt has not changed. Skipping dependency update."
  fi
  rm requirements.diff
  rm requirements.new.txt
else
  echo "Initial run. Installing dependencies..."
  source venv/bin/activate
  python -m ensurepip --upgrade
  python -m pip install -r requirements.txt
  deactivate
fi

source venv/bin/activate
python main.py
deactivate