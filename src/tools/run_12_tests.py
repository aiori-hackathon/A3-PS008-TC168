"""Run the AIORI-3 PS-008 12 mandatory test suite."""

import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ps008.suite import run_all

if __name__ == "__main__":
    is_json = "--json" in sys.argv
    sys.exit(run_all(json_output=is_json))
