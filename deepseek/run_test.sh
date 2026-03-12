#!/bin/bash
# run_tests.sh

echo "=== Running SPX 0DTE Trader Tests ==="

# Run unit tests
echo "Running unit tests..."
python -m pytest tests/test_connection/ tests/test_market/ tests/test_strategies/ tests/test_order_management/ -v -m "not integration" --cov=connection --cov=market --cov=strategies --cov=order_management

# Run integration tests
echo "Running integration tests..."
python -m pytest tests/test_integration/ -v -m integration --cov=main

# Generate coverage report
echo "Generating coverage report..."
python -m pytest --cov=. --cov-report=html --cov-report=term

echo "Tests complete!"