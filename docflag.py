#!/usr/bin/env python3
"""Launch DocFlag (serves the web interface and opens it in your browser)."""
import multiprocessing

from docflag.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
