"""Local operator bootstrap; no public account-registration endpoint."""
import argparse
from getpass import getpass

from sqlalchemy import select

from ogk_server.config import Settings
from ogk_server.db.models import Child, Parent
from ogk_server.db.session import create_database, initialize_database
from ogk_server.security.passwords import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    parent_parser = commands.add_parser("create-parent")
    parent_parser.add_argument("email")
    child_parser = commands.add_parser("create-child")
    child_parser.add_argument("parent_email")
    child_parser.add_argument("label", help="Pseudonymous label, 1-100 characters")
    args = parser.parse_args()
    settings = Settings.from_env()
    # Hash before acquiring a write lock or opening a DB connection.
    password_hash = None
    if args.command == "create-parent":
        password = getpass("Password (at least 10 characters): ")
        if password != getpass("Confirm password: "):
            parser.error("Passwords do not match")
        try:
            password_hash = hash_password(password)
        except ValueError as error:
            parser.error(str(error))
    engine, sessions = create_database(settings.database_url)
    try:
        initialize_database(engine)
        with sessions.begin() as db:
            if args.command == "create-parent":
                email = args.email.strip().casefold()
                if not 3 <= len(email) <= 254 or "@" not in email:
                    parser.error("Invalid email")
                if db.scalar(select(Parent.id).where(Parent.email == email)):
                    parser.error("Parent already exists")
                parent = Parent(email=email, password_hash=password_hash)
                db.add(parent)
                db.flush()
                print(f"parent_id={parent.id}")
            elif args.command == "create-child":
                parent = db.scalar(select(Parent).where(Parent.email == args.parent_email.strip().casefold(), Parent.is_active.is_(True)))
                if parent is None:
                    parser.error("Active parent not found")
                if not 1 <= len(args.label.strip()) <= 100:
                    parser.error("Label must contain 1-100 characters")
                child = Child(parent_id=parent.id, display_name=args.label.strip())
                db.add(child)
                db.flush()
                print(f"child_id={child.id}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
