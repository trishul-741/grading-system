"""Local operator provisioning. Passwords are prompted, never command arguments.

Usage: python -m app.auth.manage create-user --email ... --name ... --role ADMIN
       python -m app.auth.manage set-password --email ...
       python -m app.auth.manage disable-user --email ...
"""

import argparse
import getpass
import sys
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.auth.service import revoke_user_sessions
from app.database import SessionLocal
from app.enums import UserRole
from app.models.user import User
from app.schemas.auth import LoginRequest


def _prompt_password() -> str:
    password = getpass.getpass("Password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation or not 12 <= len(password) <= 1024:
        raise ValueError("Passwords must match and contain 12 to 1024 characters")
    try:
        password.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("Password must contain valid Unicode") from None
    return password


def _find_user(db: Session, email: str) -> User | None:
    return (db.query(User).filter(func.lower(User.email) == email)
            .populate_existing().with_for_update().one_or_none())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local grading accounts securely")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[role.value for role in UserRole])
    reset = commands.add_parser("set-password")
    reset.add_argument("--email", required=True)
    disable = commands.add_parser("disable-user")
    disable.add_argument("--email", required=True)
    arguments = parser.parse_args(argv)
    try:
        email = LoginRequest(email=arguments.email, password="validation-only").email
        name = arguments.name.strip() if arguments.command == "create-user" else ""
        if arguments.command == "create-user" and (not 1 <= len(name) <= 100 or any(ord(c) < 32 for c in name)):
            raise ValueError("Name must contain 1 to 100 printable characters")
        with SessionLocal() as db:
            user = _find_user(db, email)
            if arguments.command == "create-user":
                if user is not None:
                    print("An account with this email already exists", file=sys.stderr)
                    return 1
                password = _prompt_password()
                user = User(email=email, name=name, role=UserRole(arguments.role),
                            password_hash=hash_password(password), is_active=True)
                db.add(user)
            else:
                if user is None:
                    print("Account was not found", file=sys.stderr)
                    return 1
                if arguments.command == "set-password":
                    user.password_hash = hash_password(_prompt_password())
                else:
                    user.is_active = False
                revoke_user_sessions(db, user.id)
            db.commit()
            account_id = user.id
        print(f"Account updated successfully: {account_id}")
        return 0
    except (ValidationError, ValueError):
        # Validation details can contain submitted passwords or database values.
        print("Account details or password confirmation are invalid", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Account database operation failed; verify database configuration and migrations", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Account operation cancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
