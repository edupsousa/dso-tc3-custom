#!/usr/bin/env python3
"""DSO-TC3 host client; the code is in the tc3 package. Run with --help."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tc3.cli import main

if __name__ == "__main__":
    main()
