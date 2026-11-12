#!/data/data/com.termux/files/usr/bin/env python
import sys
from pathlib import Path
import steaky_tape  # or `import script as steaky_tape` if that's the filename


def main():
    entry = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    steaky_tape.consolidate(
        str(entry),
        output=None,  # let the patched _default_output_name handle it
        include_package_data=["*"],
    )


if __name__ == "__main__":
    main()
