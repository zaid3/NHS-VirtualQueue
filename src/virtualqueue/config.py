import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from cryptography.fernet import Fernet


@dataclass
class Settings:
    mode: str = field(default_factory=lambda: os.getenv("VQ_MODE", "development"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///virtualqueue.db"))
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://127.0.0.1:8000"))
    encryption_key: str = field(default_factory=lambda: os.getenv("ENCRYPTION_KEY", ""))
    signing_key: str = field(default_factory=lambda: os.getenv("SIGNING_KEY", ""))
    oidc_issuer: str = field(default_factory=lambda: os.getenv("OIDC_ISSUER", ""))
    oidc_client_id: str = field(default_factory=lambda: os.getenv("OIDC_CLIENT_ID", ""))
    oidc_client_secret: str = field(default_factory=lambda: os.getenv("OIDC_CLIENT_SECRET", ""))
    api_audience: str = field(default_factory=lambda: os.getenv("OIDC_API_AUDIENCE", "virtualqueue"))
    gateway_url: str = field(default_factory=lambda: os.getenv("TRUST_GATEWAY_URL", ""))
    gateway_key: str = field(default_factory=lambda: os.getenv("TRUST_GATEWAY_KEY", ""))
    gateway_cert: str = field(default_factory=lambda: os.getenv("TRUST_GATEWAY_CERT", ""))
    gateway_private_key: str = field(default_factory=lambda: os.getenv("TRUST_GATEWAY_PRIVATE_KEY", ""))
    gateway_ca: str = field(default_factory=lambda: os.getenv("TRUST_GATEWAY_CA", ""))
    clinical_release_id: str = field(default_factory=lambda: os.getenv("CLINICAL_RELEASE_ID", ""))
    google_routes_key: str = field(default_factory=lambda: os.getenv("GOOGLE_ROUTES_KEY", ""))
    feed_ttl_seconds: int = 300
    session_seconds: int = 1800
    max_body_bytes: int = 65536

    def validate(self):
        if self.mode not in {"development", "test", "production"}:
            raise ValueError("VQ_MODE must be development, test or production")
        if not self.encryption_key or len(self.signing_key) < 32:
            raise ValueError("Set persistent ENCRYPTION_KEY and SIGNING_KEY. Run virtualqueue keys.")
        Fernet(self.encryption_key.encode())
        if self.mode == "production":
            required = [self.oidc_issuer, self.oidc_client_id, self.oidc_client_secret,
                        self.gateway_url, self.gateway_key, self.gateway_cert,
                        self.gateway_private_key, self.clinical_release_id]
            if not all(required):
                raise ValueError("Production requires OIDC, a mutual-TLS trust gateway and clinical release reference")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires PostgreSQL")
            for url in [self.public_url, self.oidc_issuer, self.gateway_url]:
                p = urlparse(url)
                if p.scheme != "https" or not p.hostname or p.username or p.password:
                    raise ValueError("Production endpoints must use HTTPS without URL credentials")
            if len(self.gateway_key) < 32:
                raise ValueError("TRUST_GATEWAY_KEY must contain at least 32 characters")
        return self
