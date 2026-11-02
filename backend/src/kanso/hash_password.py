"""Print the PASSWORD_HASH line for .env.

    docker compose run --rm --no-deps app python -m kanso.hash_password
    docker compose run --rm -T --no-deps app python -m kanso.hash_password < password.txt

Asks twice at a terminal; otherwise reads the password from the first line of stdin.
"""

import sys
from getpass import getpass

from argon2 import PasswordHasher


def main() -> None:
    if sys.stdin.isatty():
        password = getpass("Password: ")
        if getpass("Again: ") != password:
            sys.exit("The passwords don't match.")
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    if len(password) < 8:
        sys.exit("Use at least 8 characters.")
    # Single quotes keep docker compose and dotenv from expanding the $ signs in the hash.
    print(f"PASSWORD_HASH='{PasswordHasher().hash(password)}'")


if __name__ == "__main__":
    main()
