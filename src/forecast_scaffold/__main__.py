"""``python -m forecast_scaffold`` — same CLI as the ``forecast-scaffold`` script."""

import sys

from .core import main

if __name__ == "__main__":
    sys.exit(main())
