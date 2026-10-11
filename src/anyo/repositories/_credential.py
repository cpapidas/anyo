"""Private Git credential helper; never persist credentials or handle store/erase."""

import os
import sys

if __name__ == "__main__" and sys.argv[1:] == ["get"]:
    # Git invokes this in its isolated child environment, not the consumer process.
    sys.stdout.write("username=" + os.environ["ANYO_GIT_USERNAME"] + "\n")
    sys.stdout.write("password=" + os.environ["ANYO_GIT_PASSWORD"] + "\n\n")
