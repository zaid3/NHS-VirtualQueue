import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from fastapi import HTTPException

from .models import AuthFlow
from .security import Actor


class IdentityProvider:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.metadata = None
        self.keys = None
        self.loaded_at = 0

    def load(self, force=False):
        if not self.settings.oidc_issuer:
            raise HTTPException(503, "Staff sign-in is not configured")
        if not force and self.metadata and time.time() - self.loaded_at < 300:
            return
        with httpx.Client(timeout=10, transport=self.transport, follow_redirects=False) as client:
            metadata = client.get(self.settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")
            metadata.raise_for_status()
            metadata = metadata.json()
            if metadata.get("issuer") != self.settings.oidc_issuer:
                raise ValueError("Unexpected identity issuer")
            for name in ["authorization_endpoint", "token_endpoint", "jwks_uri"]:
                if urlparse(metadata[name]).scheme != "https":
                    raise ValueError("Identity endpoints must use HTTPS")
            keys = client.get(metadata["jwks_uri"])
            keys.raise_for_status()
            self.keys, self.metadata, self.loaded_at = keys.json()["keys"], metadata, time.time()

    def decode(self, token, audience):
        self.load()
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in {"RS256", "ES256"}:
            raise ValueError("Unsupported signing algorithm")
        key = next((k for k in self.keys if k.get("kid") == header.get("kid")), None)
        if not key:
            self.load(force=True)
            key = next((k for k in self.keys if k.get("kid") == header.get("kid")), None)
        if not key:
            raise ValueError("Unknown signing key")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=[header["alg"]],
                            audience=audience, issuer=self.settings.oidc_issuer,
                            options={"require": ["sub", "iss", "aud", "iat", "exp"]}, leeway=10)
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and "azp" not in claims:
            raise ValueError("Multiple audiences require an authorised party")
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 160:
            raise ValueError("Invalid identity subject")
        if claims.get("azp", self.settings.oidc_client_id) != self.settings.oidc_client_id:
            raise ValueError("Unexpected authorised party")
        if not isinstance(claims.get("vq_tenant"), str) or not 1 <= len(claims["vq_tenant"]) <= 80:
            raise ValueError("Missing organisation assignment")
        if claims.get("vq_role") not in {"clinician", "coordinator", "admin", "auditor", "integration"}:
            raise ValueError("Missing role assignment")
        return claims

    def access_actor(self, token):
        claims = self.decode(token, self.settings.api_audience)
        if claims.get("token_use") != "access" or "virtualqueue" not in claims.get("scope", "").split():
            raise ValueError("Not a VirtualQueue access token")
        if self.settings.mode == "production" and claims["vq_role"] != "integration" and "mfa" not in claims.get("amr", []):
            raise ValueError("Staff authentication requires MFA")
        return Actor(claims["sub"], claims["vq_role"], claims["vq_tenant"])

    def begin(self, session, vault):
        self.load()
        state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        session.add(AuthFlow(state_hash=vault.digest(state), verifier=vault.seal(verifier), nonce=nonce,
                             expires=time.time() + 300))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urlencode({"client_id": self.settings.oidc_client_id, "response_type": "code",
                           "redirect_uri": self.settings.public_url + "/auth/callback", "scope": "openid profile",
                           "state": state, "nonce": nonce, "code_challenge": challenge,
                           "code_challenge_method": "S256", "max_age": 900})
        return self.metadata["authorization_endpoint"] + "?" + query, state

    def exchange(self, code, verifier, nonce):
        self.load()
        with httpx.Client(timeout=10, transport=self.transport, follow_redirects=False) as client:
            response = client.post(self.metadata["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "client_id": self.settings.oidc_client_id, "client_secret": self.settings.oidc_client_secret,
                "redirect_uri": self.settings.public_url + "/auth/callback"})
            response.raise_for_status()
            claims = self.decode(response.json()["id_token"], self.settings.oidc_client_id)
        if not secrets.compare_digest(claims.get("nonce", ""), nonce):
            raise ValueError("Invalid nonce")
        if self.settings.mode == "production" and "mfa" not in claims.get("amr", []):
            raise ValueError("Staff authentication requires MFA")
        if claims["vq_role"] == "integration":
            raise ValueError("Integration identities cannot start browser sessions")
        return claims
