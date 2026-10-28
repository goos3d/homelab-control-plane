from __future__ import annotations

import argparse
import getpass

from controlplane.core.security import ROLES, UserStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Control Plane accounts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create or replace an account")
    create.add_argument("username")
    create.add_argument("--role", required=True, choices=sorted(ROLES))

    state = subparsers.add_parser("set-active", help="enable or disable an account")
    state.add_argument("username")
    state.add_argument("value", choices=("yes", "no"))

    role = subparsers.add_parser("set-role", help="change an account role without resetting its password")
    role.add_argument("username")
    role.add_argument("role", choices=sorted(ROLES))

    args = parser.parse_args()
    store = UserStore.from_environment()
    if args.command == "create":
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("Passwords do not match")
        user = store.save_user(args.username, password, args.role)
        print(f"Saved {user.username} as {user.role} in {store.path}")
    elif args.command == "set-active":
        user = store.set_active(args.username, args.value == "yes")
        state_label = "enabled" if user.active else "disabled"
        print(f"{user.username} is now {state_label}")
    else:
        user = store.set_role(args.username, args.role)
        print(f"{user.username} is now {user.role}")


if __name__ == "__main__":
    main()
