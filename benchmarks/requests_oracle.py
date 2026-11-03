"""Independent Acceptance Oracle Grader for psf/requests Grand Target Benchmark.
Evaluates RFC 7235 redirect authorization stripping, port normalization, and non-regression:
  1. Host / Subdomain change -> strips auth
  2. Protocol downgrade (HTTPS -> HTTP) -> strips auth
  3. Non-standard port change -> strips auth
  4. Standard port HTTP -> HTTPS upgrade exception -> retains auth
  5. Default port normalization (explicit :80/:443 vs None) -> retains auth
  6. Session redirect execution flow & header stripping
  7. Regression-free checks for requests structures and utilities
"""
import sys
from urllib.parse import urlparse
import pytest

def get_requests_modules():
    for mod in list(sys.modules.keys()):
        if mod == "requests" or mod.startswith("requests."):
            del sys.modules[mod]
    import requests
    from requests.sessions import Session, SessionRedirectMixin
    from requests.models import Request, Response, PreparedRequest
    return requests, Session, SessionRedirectMixin, Request, Response, PreparedRequest

def test_01_host_change_strips_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com/foo", "http://another.example.com/bar") is True

def test_02_subdomain_change_strips_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://api.example.com/v1", "http://auth.example.com/v1") is True

def test_03_http_downgrade_strips_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("https://example.com/secure", "http://example.com/insecure") is True

def test_04_port_change_strips_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com:1234/foo", "https://example.com:4321/bar") is True
    assert s.should_strip_auth("http://example.com:8000/foo", "http://example.com:9000/bar") is True

def test_05_nonstandard_port_https_upgrade_strips_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com:8080/foo", "https://example.com/bar") is True
    assert s.should_strip_auth("http://example.com/foo", "https://example.com:8443/bar") is True

def test_06_nonstandard_target_port_strips_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("https://example.com:8443/foo", "https://example.com:9443/bar") is True

def test_07_standard_http_to_https_upgrade_retains_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com/foo", "https://example.com/bar") is False

def test_08_standard_explicit_80_to_https_upgrade_retains_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com:80/foo", "https://example.com/bar") is False

def test_09_standard_http_to_explicit_443_upgrade_retains_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com/foo", "https://example.com:443/bar") is False

def test_10_default_port_normalization_both_https():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("https://example.com:443/foo", "https://example.com/bar") is False
    assert s.should_strip_auth("https://example.com/foo", "https://example.com:443/bar") is False

def test_11_default_port_normalization_both_http():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com:80/foo", "http://example.com/bar") is False
    assert s.should_strip_auth("http://example.com/foo", "http://example.com:80/bar") is False

def test_12_same_host_same_port_retains_auth():
    _, Session, _, _, _, _ = get_requests_modules()
    s = Session()
    assert s.should_strip_auth("http://example.com/api/login", "http://example.com/api/dashboard") is False
    assert s.should_strip_auth("https://example.com/a", "https://example.com/b") is False

def test_13_session_redirect_stripping_execution():
    _, Session, _, Request, Response, PreparedRequest = get_requests_modules()
    s = Session()
    
    # Create request with Authorization
    req = Request("GET", "http://example.com/login", headers={"Authorization": "Bearer secret123"}).prepare()
    
    # Create simulated 302 response pointing to external domain
    resp = Response()
    resp.status_code = 302
    resp.headers["location"] = "http://attacker.com/leak"
    resp.url = "http://example.com/login"
    resp.request = req
    
    # resolve_redirects generator with yield_requests=True (no actual network send)
    redirect_gen = s.resolve_redirects(resp, req, yield_requests=True)
    next_req = next(redirect_gen)
    assert next_req.url == "http://attacker.com/leak"
    assert "Authorization" not in next_req.headers

def test_14_session_redirect_retaining_execution():
    _, Session, _, Request, Response, PreparedRequest = get_requests_modules()
    s = Session()
    
    # Create request with Authorization
    req = Request("GET", "http://example.com/login", headers={"Authorization": "Bearer secret123"}).prepare()
    
    # Create simulated 302 response pointing to same host
    resp = Response()
    resp.status_code = 302
    resp.headers["location"] = "http://example.com/dashboard"
    resp.url = "http://example.com/login"
    resp.request = req
    
    redirect_gen = s.resolve_redirects(resp, req, yield_requests=True)
    next_req = next(redirect_gen)
    assert next_req.url == "http://example.com/dashboard"
    assert next_req.headers["Authorization"] == "Bearer secret123"

def test_15_structures_and_case_insensitive_dict():
    requests, _, _, _, _, _ = get_requests_modules()
    from requests.structures import CaseInsensitiveDict
    cid = CaseInsensitiveDict({"Authorization": "Bearer token"})
    assert cid["authorization"] == "Bearer token"
    assert cid["AUTHORIZATION"] == "Bearer token"
    assert "Authorization" in cid
    assert "authorization" in cid
