# -*- coding: utf-8 -*-
"""Worker entrypoint for packaged distributions.

This wrapper lets the GUI launch the processing engine as a separate executable,
so START/STOP works reliably in frozen (PyInstaller) builds.
"""

from run_profile import main


if __name__ == "__main__":
    main()
