import argparse
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from .config import Settings
from .db import Database
from .domain import apply_feed, audit
from .models import Service
from .schemas import Feed, SlotInput
from .security import Actor, Vault


def seed(db, vault, settings):
    if settings.mode == "production":
        raise ValueError("Synthetic fixtures are disabled in production")
    service_id = "riverside-urgent-care"
    with db.transaction(service_id) as session:
        service = session.get(Service, service_id)
        if not service:
            service = Service(id=service_id, tenant="synthetic-trust", name="Riverside Urgent Care · Synthetic",
                              address="Example hospital, East London · not a real care service",
                              phone="Synthetic service: NHS 111", latitude=51.56, longitude=0.07,
                              feed_at=0, feed_version=0, audit_seq=0, audit_head="0" * 64,
                              paused=False, callback_minutes=15)
            session.add(service)
            session.flush()
            audit(session, vault, service, "fixture", "service_created")
        now = datetime.now(UTC)
        feed = Feed(version=service.feed_version + 1, observed_at=now, slots=[
            SlotInput(external_id=f"synthetic-{int(now.timestamp())}-{i}",
                      start=now + timedelta(minutes=60 + i * 20),
                      end=now + timedelta(minutes=80 + i * 20), capacity=4)
            for i in range(6)])
        apply_feed(session, vault, service, Actor("fixture", "integration", "synthetic-trust"), feed)
    return service_id


def main():
    parser = argparse.ArgumentParser(description="NHS VirtualQueue operations")
    parser.add_argument("command", choices=["keys", "serve", "worker", "seed", "local", "audit"])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--service")
    args = parser.parse_args()
    if args.command == "keys":
        path = Path(".env")
        if path.exists():
            raise SystemExit(".env already exists; keys were not replaced")
        path.write_text("VQ_MODE=development\nDATABASE_URL=sqlite:///virtualqueue.db\nPUBLIC_URL=http://127.0.0.1:8000\n"
                        + "ENCRYPTION_KEY=" + Fernet.generate_key().decode() + "\nSIGNING_KEY=" + secrets.token_urlsafe(48) + "\n")
        path.chmod(0o600)
        print("Created .env with fresh keys. Keep it outside version control.")
        return
    # Only load literal NAME=value pairs; never source or evaluate a shell file.
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)
    settings = Settings().validate()
    db, vault = Database(settings.database_url), Vault(settings)
    if args.command == "seed":
        print("Refreshed synthetic service:", seed(db, vault, settings))
    elif args.command == "worker":
        from .worker import run
        run(db, vault, settings)
    elif args.command == "audit":
        from .domain import service_for, verify_audit
        with db.read() as session:
            service = service_for(session, args.service)
            valid = verify_audit(session, vault, service)
            print({"valid": valid, "sequence": service.audit_seq, "head": service.audit_head})
            if not valid:
                raise SystemExit(1)
    else:
        import uvicorn

        from .app import create_app
        if args.command == "local":
            if settings.mode == "production":
                raise SystemExit("Local development is disabled in production")
            from threading import Thread

            from .worker import run
            seed(db, vault, settings)
            # Separate local thread; production runs the durable worker independently.
            Thread(target=run, args=(db, vault, settings), daemon=True).start()
            def refresh():
                while True:
                    time.sleep(60)
                    with db.transaction("riverside-urgent-care") as session:
                        service = session.get(Service, "riverside-urgent-care")
                        service.feed_at = time.time()
            Thread(target=refresh, daemon=True).start()
        uvicorn.run(create_app(settings, db), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
