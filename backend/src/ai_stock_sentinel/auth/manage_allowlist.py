"""Operator-only membership maintenance. Writes require --apply; no HTTP admin API."""
from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ai_stock_sentinel.db.session import get_database_url
from ai_stock_sentinel.user_models.user import AllowedGoogleAccount, User


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "grant", "revoke"))
    parser.add_argument("--email")
    parser.add_argument("--google-sub", help="Confirmed Google subject; required for accounts not yet in users")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.action != "list" and not args.email:
        parser.error("grant/revoke require --email")
    engine = None
    try:
        url = get_database_url()
        if make_url(url).get_backend_name() == "postgresql" and not os.getenv("EXPECTED_DATABASE_NAME"):
            raise ValueError("Set EXPECTED_DATABASE_NAME explicitly before PostgreSQL maintenance")
        engine = create_engine(url, poolclass=NullPool)
        with Session(engine) as db:
            if args.action == "list":
                print(json.dumps([{"email": a.email, "google_sub": a.google_sub}
                                  for a in db.scalars(select(AllowedGoogleAccount).order_by(AllowedGoogleAccount.email))]))
                return 0
            email = args.email.strip().lower()
            if (len(email) > 255 or email.count("@") != 1 or not all(email.split("@"))
                    or any(c.isspace() for c in email)):
                raise ValueError("Invalid email")
            if args.google_sub is not None and not args.google_sub:
                raise ValueError("Invalid Google subject")
            existing = db.scalar(select(AllowedGoogleAccount).where(AllowedGoogleAccount.email == email))
            user = db.scalars(select(User).where(func.lower(User.email) == email)).one_or_none()
            subject = args.google_sub or (existing.google_sub if existing else user.google_sub if user else None)
            if not subject:
                if args.action == "revoke":
                    print(json.dumps({"action": "revoke", "changed": False, "email": email}))
                    return 0
                raise ValueError("Unknown account; supply its confirmed --google-sub, never infer identity from email")
            if len(subject) > 255 or any(c.isspace() for c in subject):
                raise ValueError("Invalid Google subject")
            if any(account and account.google_sub != subject for account in (existing, user)):
                raise ValueError("Email and Google subject identify different accounts")
            if args.action == "grant" and user and (not user.is_active or user.deleted_at is not None):
                raise ValueError("Cannot approve an inactive or deleted user")
            member = db.get(AllowedGoogleAccount, subject)
            if member and member.email != email:
                raise ValueError("Google subject is already labeled with a different email")
            changed = (member is None) if args.action == "grant" else (member is not None)
            if args.apply and changed:
                if args.action == "grant":
                    db.add(AllowedGoogleAccount(google_sub=subject, email=email))
                else:
                    db.delete(member)
                db.commit()
            print(json.dumps({"mode": "applied" if args.apply else "plan_only", "action": args.action,
                              "email": email, "google_sub": subject, "changed": changed}))
        return 0
    except SQLAlchemyError:
        print("Database operation failed; check target, migration and operator privileges.", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
