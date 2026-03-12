#!/usr/bin/env python
# run_trader.py
"""
Entry point script for running the SPX 0DTE trader
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main

if __name__ == "__main__":
    main()