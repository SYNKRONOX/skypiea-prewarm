"""Issue #8: `skypiea status` / `policy get` report the predictor the gateway actually calls,
and `policy set` refuses (unless --force) when the gateway does not serve the stub."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from tests.test_cli import Fake, climod  # noqa: F401


class Served(Fake):
    def __init__(self, url):
        super().__init__()
        self.url = url

    def gateway_predictor_url(self):
        return self.url


@pytest.fixture
def run(monkeypatch):
    def go(url, *args):
        fake = Served(url)
        monkeypatch.setattr(climod, "BACKEND_FACTORY", lambda: fake)
        return CliRunner().invoke(climod.cli, list(args)), fake
    return go


MODEL = "http://skypiea-model.skypiea-tools.svc:80"
STUB = "http://skypiea-predictor.skypiea-tools.svc:80"


def test_status_reports_the_model_when_the_gateway_serves_it(run):
    r, _ = run(MODEL, "status")
    assert "policy: predictive (real predictor skypiea-model)" in r.output
    assert "scale_to_zero" in r.output and "not used" in r.output


def test_status_reports_the_stub_policy_when_the_gateway_serves_the_stub(run):
    r, _ = run(STUB, "status")
    assert "policy: scale_to_zero" in r.output and "not used" not in r.output


def test_no_predictor_is_scale_to_zero(run):
    r, _ = run("", "policy", "get")
    assert "scale_to_zero (the gateway has no predictor" in r.output


def test_policy_set_refused_when_the_gateway_serves_the_model(run):
    r, fake = run(MODEL, "policy", "set", "markov1")
    assert r.exit_code != 0 and "no effect" in r.output
    assert fake.policy["policy"] == "scale_to_zero"
    r, fake = run(MODEL, "policy", "set", "markov1", "--force")
    assert r.exit_code == 0 and fake.policy["policy"] == "markov1"
