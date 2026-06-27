#!/usr/bin/env python3
"""CLI tool for creating and managing Koottam accounts.

Run from the project root:
  python scripts/manage_accounts.py create-admin           # first-time setup
  python scripts/manage_accounts.py create-user <name>     # add a friend
  python scripts/manage_accounts.py list                   # show all accounts
  python scripts/manage_accounts.py set-password <name>    # change password
  python scripts/manage_accounts.py set-limit <name> <N>   # change token budget
  python scripts/manage_accounts.py toggle <name>          # enable / disable
  python scripts/manage_accounts.py reset-usage <name>     # clear usage counters
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth.auth_manager import auth_manager


def _by_username() -> dict:
    return {a["username"]: a for a in auth_manager.get_accounts()}


def cmd_create(username: str, role: str, token_limit: int, reset_hours: float) -> None:
    password = getpass.getpass(f"Password for '{username}': ")
    confirm  = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)
    try:
        acc = auth_manager.create_account(username, password, role, token_limit, reset_hours)
        print(f"\nCreated {role} account:")
        print(f"  Username : {acc['username']}")
        print(f"  ID       : {acc['id']}")
        print(f"  Limit    : {token_limit} tokens / {reset_hours}h window")
        print("\nStart the server and open the UI to log in.")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_list() -> None:
    accounts = auth_manager.get_accounts()
    if not accounts:
        print("No accounts found. Run: python scripts/manage_accounts.py create-admin")
        return
    fmt = "{:<4} {:<20} {:<7} {:<10} {:<7} {:<8} {}"
    print(fmt.format("ID", "Username", "Role", "Tokens", "Limit", "Active", "Last active"))
    print("-" * 75)
    for a in accounts:
        last = (a.get("last_active") or "never")[:19]
        print(fmt.format(
            a["id"], a["username"], a["role"],
            a["tokens_used"], a["token_limit"],
            "yes" if a["is_active"] else "NO",
            last,
        ))


def cmd_set_password(username: str) -> None:
    accounts = _by_username()
    if username not in accounts:
        print(f"No account '{username}'.")
        sys.exit(1)
    password = getpass.getpass(f"New password for '{username}': ")
    confirm  = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)
    auth_manager.update_account(accounts[username]["id"], password=password)
    print(f"Password updated for '{username}'.")


def cmd_set_limit(username: str, tokens: int, hours: float) -> None:
    accounts = _by_username()
    if username not in accounts:
        print(f"No account '{username}'.")
        sys.exit(1)
    auth_manager.update_account(accounts[username]["id"], token_limit=tokens, reset_hours=hours)
    print(f"Updated '{username}': {tokens} tokens / {hours}h window.")


def cmd_toggle(username: str) -> None:
    accounts = _by_username()
    if username not in accounts:
        print(f"No account '{username}'.")
        sys.exit(1)
    a = accounts[username]
    new_state = 0 if a["is_active"] else 1
    auth_manager.update_account(a["id"], is_active=new_state)
    state_str = "enabled" if new_state else "disabled"
    print(f"Account '{username}' is now {state_str}.")


def cmd_reset_usage(username: str) -> None:
    accounts = _by_username()
    if username not in accounts:
        print(f"No account '{username}'.")
        sys.exit(1)
    auth_manager.reset_usage(accounts[username]["id"])
    print(f"Usage counters reset for '{username}'.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Koottam account manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("create-admin", help="Create an admin account (first-time setup)")
    p.add_argument("--username", default="admin", metavar="NAME")
    p.add_argument("--limit", type=int, default=100_000, metavar="TOKENS")
    p.add_argument("--hours", type=float, default=24.0, metavar="H")

    p = sub.add_parser("create-user", help="Create a user account")
    p.add_argument("username")
    p.add_argument("--limit", type=int, default=5000, metavar="TOKENS")
    p.add_argument("--hours", type=float, default=3.0, metavar="H")

    sub.add_parser("list", help="List all accounts with usage")

    p = sub.add_parser("set-password", help="Change a password")
    p.add_argument("username")

    p = sub.add_parser("set-limit", help="Change token budget")
    p.add_argument("username")
    p.add_argument("tokens", type=int)
    p.add_argument("--hours", type=float, default=3.0, metavar="H")

    p = sub.add_parser("toggle", help="Enable or disable an account")
    p.add_argument("username")

    p = sub.add_parser("reset-usage", help="Clear usage counters")
    p.add_argument("username")

    args = parser.parse_args()

    if args.command == "create-admin":
        cmd_create(args.username, "admin", args.limit, args.hours)
    elif args.command == "create-user":
        cmd_create(args.username, "user", args.limit, args.hours)
    elif args.command == "list":
        cmd_list()
    elif args.command == "set-password":
        cmd_set_password(args.username)
    elif args.command == "set-limit":
        cmd_set_limit(args.username, args.tokens, args.hours)
    elif args.command == "toggle":
        cmd_toggle(args.username)
    elif args.command == "reset-usage":
        cmd_reset_usage(args.username)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
