#!/bin/bash
# setup_environment.sh

echo "Setting up SPX 0DTE Trader Environment"

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-test.txt

# Install the package in development mode
pip install -e .

# Create necessary directories
mkdir -p logs
mkdir -p data
mkdir -p config

# Set PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

echo "Setup complete!"
echo "Run 'source venv/bin/activate' to activate the virtual environment"