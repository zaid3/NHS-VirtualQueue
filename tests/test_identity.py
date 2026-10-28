import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from virtualqueue.auth import IdentityProvider
from virtualqueue.models import AuthFlow


@pytest.fixture
def idp(env):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": "test-key", "use": "sig", "alg": "RS256"}
    settings = replace(env[3], oidc_issuer="https://identity.example.org", oidc_client_id="vq-client",
                       oidc_client_secret="synthetic-secret")
    base = {"iss": settings.oidc_issuer, "sub": "clinician-1", "aud": "vq-client", "iat": time.time(),
            "exp": time.time() + 300, "vq_role": "clinician", "vq_tenant": "synthetic-trust", "nonce": "test-nonce", "amr": ["mfa"]}
    def token(**changes):
        return jwt.encode({**base, **changes}, key, algorithm="RS256", headers={"kid": "test-key"})
    def transport(request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": settings.oidc_issuer,
                "authorization_endpoint": settings.oidc_issuer + "/authorize", "token_endpoint": settings.oidc_issuer + "/token",
                "jwks_uri": settings.oidc_issuer + "/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path == "/token":
            assert b"code_verifier=verifier" in request.content
            return httpx.Response(200, json={"id_token": token()})
        raise AssertionError("Unexpected identity request")
    return IdentityProvider(settings, httpx.MockTransport(transport)), token


@pytest.mark.parametrize("changes", [{"iss": "https://evil.invalid"}, {"aud": "another-client"},
    {"exp": 1}, {"iat": time.time() + 3600}, {"azp": "another-client"}, {"vq_role": "superuser"},
    {"vq_tenant": ""}, {"aud": ["vq-client", "other"]}, {"sub": ""}])
def test_signed_token_claims_are_enforced(idp, changes):
    provider, token = idp
    with pytest.raises((ValueError, jwt.PyJWTError)):
        provider.decode(token(**changes), "vq-client")


def test_id_token_is_not_api_access_token(idp):
    provider, token = idp
    with pytest.raises(ValueError):
        provider.access_actor(token(aud="virtualqueue"))
    actor = provider.access_actor(token(aud="virtualqueue", token_use="access", scope="virtualqueue"))
    assert actor.role == "clinician"


def test_nonce_pkce_and_encrypted_verifier(env, idp):
    provider, _ = idp
    with env[1].transaction() as s:
        url, state = provider.begin(s, env[2])
        s.flush()
        flow = s.get(AuthFlow, env[2].digest(state))
        params = parse_qs(urlparse(url).query)
        assert params["code_challenge_method"] == ["S256"]
        assert params["state"] == [state]
        assert env[2].open(flow.verifier) != flow.verifier
    assert provider.exchange("code", "verifier", "test-nonce")["sub"] == "clinician-1"
    with pytest.raises(ValueError, match="nonce"):
        provider.exchange("code", "verifier", "wrong-nonce")


def test_production_human_access_requires_mfa(idp):
    provider, token = idp
    provider.settings = replace(provider.settings, mode="production")
    with pytest.raises(ValueError, match="MFA"):
        provider.access_actor(token(aud="virtualqueue", token_use="access", scope="virtualqueue", amr=[]))
    assert provider.access_actor(token(aud="virtualqueue", token_use="access", scope="virtualqueue",
                                      amr=[], vq_role="integration")).role == "integration"
