#!/usr/bin/env python3
"""R6.20 entry point for the M2-F source-audio/source-scene plan validator."""

import sys

sys.dont_write_bytecode = True

from validate_r617_source_audio_plan import main


if __name__ == "__main__":
    raise SystemExit(main())
