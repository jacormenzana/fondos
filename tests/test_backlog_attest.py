# tests/test_backlog_attest.py
# -*- coding: utf-8 -*-
"""Phase C slice 4 (FND-0334), no database: the signed attestation of the collision gate (shared/backlog_attest.py), the CI verifier (scripts/audit/verify_attestation.py), `backlog_sync.py --attest`
and the branch-protection reading (shared/backlog_protection.py). Real `ssh-keygen` keys are generated in a temporary directory; nothing touches the owner's keys."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_sync as bs  # noqa: E402
import verify_attestation as va  # noqa: E402
from shared import backlog_attest as att  # noqa: E402
from shared import backlog_protection as prot  # noqa: E402

HAVE_GIT = shutil.which("git") is not None
HAVE_KEYGEN = shutil.which("ssh-keygen") is not None
needs_keys = pytest.mark.skipif(not (HAVE_GIT and HAVE_KEYGEN), reason="git and ssh-keygen are needed")

SHA1, SHA2, SHA3 = "1" * 40, "2" * 40, "3" * 40
TREE1, TREE2 = "a" * 40, "b" * 40
NONCE = "ab" * 16


def commit(sha=SHA1, tree=TREE1, gate=NONCE, tickets=("FND-0001",), paths=("shared/x.py",)):
    return {"sha": sha, "tree": tree, "gate": gate, "tickets": list(tickets), "paths": list(paths)}


# ---------------------------------------------------------------- build and problems (pure)

def test_the_attestation_carries_ids_and_hashes_and_never_ticket_text():
    pairs = [{"a": "FND-0001", "b": "FND-0002", "decided": True}, {"a": "FND-0009", "b": "FND-0010", "decided": False}]
    doc = att.build("b" * 40, "c" * 40, [commit(), commit(SHA2, tickets=()), commit(SHA3, paths=("backlog_gate/attestation.json", "backlog_gate/attestation.json.sig"))], pairs, "2026-11-03T10:00:00+00:00")
    assert [e["sha"] for e in doc["commits"]] == [SHA1]                                    # no ticket: left out; the attestation commit: left out
    assert doc["commits"][0]["pairs"] == [{"a": "FND-0001", "b": "FND-0002", "decided": True}]      # only the pairs of its tickets
    flat = json.dumps(doc)
    assert "title" not in flat and "description" not in flat and "comment" not in flat
    assert att.dumps(doc) == att.dumps(json.loads(att.dumps(doc))) and att.dumps(doc).endswith("}\n")   # deterministic bytes


def test_the_attestation_commit_is_the_one_that_changes_only_files_under_backlog_gate():
    assert att.is_attestation_commit(["backlog_gate/attestation.json", "backlog_gate/attestation.json.sig"])
    assert not att.is_attestation_commit([]) and not att.is_attestation_commit(["backlog_gate/attestation.json", "shared/x.py"])


def doc_for(*commits, pairs=()):
    return att.build("0" * 40, "f" * 40, list(commits), list(pairs), "2026-11-03T10:00:00+00:00")


def test_problems_covers_every_way_the_range_can_disagree():
    ok = commit()
    assert att.problems(doc_for(ok), [ok]) == []
    assert att.problems(doc_for(), [ok])[0].startswith(SHA1[:12]) and "not in the attestation" in att.problems(doc_for(), [ok])[0]
    assert "tree hash" in att.problems(doc_for(ok), [commit(tree=TREE2)])[0]
    assert "gate nonce differs" in att.problems(doc_for(ok), [commit(gate="cd" * 16)])[0]
    assert "gate nonce differs" in att.problems(doc_for(ok), [commit(gate=None)])[0]
    assert att.problems(doc_for(commit(gate="offline")), [commit(gate="offline")]) == []             # a pending commit is attested as it is
    und = att.problems(doc_for(ok, pairs=[{"a": "FND-0001", "b": "FND-0002", "decided": False}]), [ok])
    assert und == [f"{SHA1[:12]}: collision pair FND-0001 / FND-0002 is undecided"]
    assert att.problems({"version": 2, "commits": []}, [ok]) == ["the attestation is not a version-1 document"]
    assert att.problems({"version": 1, "commits": [{"sha": "x", "tickets": []}]}, []) == ["malformed entry for x"]


def test_commits_without_a_ticket_and_the_attestation_commit_are_not_required():
    assert att.problems(doc_for(), [commit(tickets=()), commit(SHA3, paths=("backlog_gate/attestation.json",))]) == []


def test_has_signer_ignores_comments_and_blank_lines():
    assert not att.has_signer("") and not att.has_signer("# only comments\n\n   \n  # more\n")
    assert att.has_signer('# c\nbacklog-gate-owner namespaces="backlog-gate" ssh-ed25519 AAAA\n')


def test_the_key_must_not_be_inside_the_repository(tmp_path):
    assert att.inside(tmp_path / "a" / "key", tmp_path) and att.inside(tmp_path, tmp_path)
    assert not att.inside(tmp_path.parent / "elsewhere" / "key", tmp_path)


# ---------------------------------------------------------------- real signatures

def make_key(tmp_path, name="k"):
    key = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)], check=True, capture_output=True)
    pub = (tmp_path / f"{name}.pub").read_text(encoding="utf-8").strip()
    return key, f'{att.PRINCIPAL} namespaces="{att.NAMESPACE}" {pub}\n'


@needs_keys
def test_sign_and_verify_round_trip_and_every_tamper_fails(tmp_path):
    key, allowed_line = make_key(tmp_path)
    allowed = tmp_path / "allowed"
    allowed.write_text(allowed_line, encoding="utf-8")
    doc = tmp_path / "attestation.json"
    doc.write_text(att.dumps(doc_for(commit())), encoding="utf-8", newline="")
    sig = att.sign(doc, key)
    assert sig.name == "attestation.json.sig" and att.verify_signature(doc, sig, allowed)[0] is True
    doc.write_text(doc.read_text(encoding="utf-8").replace(SHA1, SHA2), encoding="utf-8", newline="")            # a tampered document
    assert att.verify_signature(doc, sig, allowed)[0] is False
    other, other_line = make_key(tmp_path, "other")
    doc.write_text(att.dumps(doc_for(commit())), encoding="utf-8", newline="")
    sig2 = att.sign(doc, other)                                                                                  # signed by a key that is not allowed
    assert att.verify_signature(doc, sig2, allowed)[0] is False
    wrong = tmp_path / "wrong_ns"
    wrong.write_text(allowed_line.replace(f'namespaces="{att.NAMESPACE}"', 'namespaces="git"'), encoding="utf-8")
    assert att.verify_signature(doc, att.sign(doc, key), wrong)[0] is False                                      # a key allowed for another namespace does not count


# ---------------------------------------------------------------- the CI verifier in a throwaway repository

class Repo:
    def __init__(self, tmp_path):
        self.root = tmp_path / "repo"
        self.root.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        self.git("init", "-q", "-b", "master")
        self.key, self.allowed_line = make_key(tmp_path)

    def git(self, *a):
        p = subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True, encoding="utf-8", env=self.env, timeout=60)
        assert p.returncode == 0, (a, p.stderr)
        return p.stdout.strip()

    def commit(self, name, text, message):
        f = self.root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def sign_range(self, base, pairs=(), mutate=None):
        """What `backlog_sync.py --attest` does, with the owner's test key: write and sign the attestation of base..HEAD, and commit it."""
        commits = []
        for sha in reversed(self.git("rev-list", f"{base}..HEAD").split()):
            msg = va.gate.parse_message(self.git("log", "-1", "--format=%B", sha))
            paths = self.git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha).split()
            commits.append({"sha": sha, "tree": self.git("rev-parse", f"{sha}^{{tree}}"), "gate": msg.gate, "tickets": msg.mentions, "paths": paths})
        doc = att.build(self.git("rev-parse", base), self.git("rev-parse", "HEAD"), commits, list(pairs), "2026-11-03T10:00:00+00:00")
        if mutate:
            mutate(doc)
        out = self.root / att.ATTEST_FILE
        out.parent.mkdir(exist_ok=True)
        out.write_text(att.dumps(doc), encoding="utf-8", newline="")
        att.sign(out, self.key)
        self.git("add", att.ATTEST_DIR)
        self.git("commit", "-q", "-m", "chore: attestation")

    def verify(self, base):
        return va.main(["--base", base])


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if not (HAVE_GIT and HAVE_KEYGEN):
        pytest.skip("git and ssh-keygen are needed")
    r = Repo(tmp_path)
    monkeypatch.setattr(va, "ROOT", r.root)
    r.commit(".github/backlog_gate_allowed_signers", r.allowed_line, "init: the owner's public key")
    r.base = r.git("rev-parse", "HEAD")
    return r


@needs_keys
def test_ci_passes_when_every_ticket_commit_is_attested_and_the_attestation_commit_is_excluded(repo, capsys):
    repo.commit("a.py", "a", "feat: FND-0001 first\n\nBacklog-Gate: " + NONCE)
    repo.commit("b.py", "b", "docs: nothing to attest")
    repo.sign_range(repo.base)
    assert repo.verify(repo.base) == 0 and "attestation OK: 1 commit(s) covered" in capsys.readouterr().out


@needs_keys
def test_ci_fails_for_a_missing_attestation_a_bad_signature_a_missing_commit_a_tree_change_and_an_undecided_pair(repo, capsys):
    repo.commit("a.py", "a", "feat: FND-0001 first\n\nBacklog-Gate: " + NONCE)
    assert repo.verify(repo.base) == 1 and "carries no signed attestation" in capsys.readouterr().out               # no attestation at all
    repo.sign_range(repo.base)
    sig = repo.root / att.SIG_FILE
    sig.write_text(sig.read_text(encoding="utf-8").replace("A", "B", 3), encoding="utf-8", newline="")               # a damaged signature
    repo.git("add", att.ATTEST_DIR)
    repo.git("commit", "-q", "-m", "chore: damage the signature")
    assert repo.verify(repo.base) == 1 and "does not verify" in capsys.readouterr().out
    repo.git("reset", "-q", "--hard", "HEAD~2")
    repo.commit("c.py", "c", "feat: FND-0002 second\n\nBacklog-Gate: " + NONCE)
    repo.sign_range(repo.base, mutate=lambda d: d["commits"].pop())                                                   # signed, but one ticket commit is not in it
    assert repo.verify(repo.base) == 1 and "not in the attestation" in capsys.readouterr().out
    repo.git("reset", "-q", "--hard", "HEAD~1")
    repo.sign_range(repo.base, mutate=lambda d: d["commits"][-1].update(tree="e" * 40))
    assert repo.verify(repo.base) == 1 and "tree hash" in capsys.readouterr().out
    repo.git("reset", "-q", "--hard", "HEAD~1")
    repo.sign_range(repo.base, pairs=[{"a": "FND-0001", "b": "FND-0003", "decided": False}])
    assert repo.verify(repo.base) == 1 and "is undecided" in capsys.readouterr().out


@needs_keys
def test_a_change_cannot_bring_its_own_key(repo, capsys, tmp_path):
    other, other_line = make_key(tmp_path, "attacker")
    repo.commit("a.py", "a", "feat: FND-0001 first\n\nBacklog-Gate: " + NONCE)
    (repo.root / ".github" / "backlog_gate_allowed_signers").write_text(other_line, encoding="utf-8")
    repo.git("add", ".github")
    repo.git("commit", "-q", "-m", "chore: swap the key")
    repo.key = other                                                                                                    # signed with the key the change itself introduces
    repo.sign_range(repo.base)
    assert repo.verify(repo.base) == 1 and "does not verify against the allowed signers of the base" in capsys.readouterr().out


@needs_keys
def test_without_a_key_in_the_base_the_gate_is_not_enforced_with_a_warning_not_silently_green(tmp_path, monkeypatch, capsys):
    r = Repo(tmp_path)
    monkeypatch.setattr(va, "ROOT", r.root)
    r.commit(".github/backlog_gate_allowed_signers", "# no key yet\n", "init")
    base = r.git("rev-parse", "HEAD")
    r.commit("a.py", "a", "feat: FND-0001 unattested")
    assert r.verify(base) == 0
    assert "NOT ENFORCED" in capsys.readouterr().out


@needs_keys
def test_a_range_without_a_ticket_commit_needs_no_attestation(repo, capsys):
    repo.commit("a.py", "a", "docs: no ticket")
    assert repo.verify(repo.base) == 0 and "nothing to attest" in capsys.readouterr().out


@needs_keys
def test_a_bad_base_is_a_usage_error(repo, capsys):
    assert repo.verify("does-not-exist") == 2 and "::error::" in capsys.readouterr().out


# ---------------------------------------------------------------- backlog_sync.py --attest

class FakeBc:
    def __init__(self, pairs=()):
        self.pairs, self.asked = list(pairs), None

    def collision_store_present(self):
        return True

    def gate_pairs(self, tickets):
        self.asked = list(tickets)
        return self.pairs


@needs_keys
def test_attest_writes_and_signs_for_the_range_and_refuses_a_key_inside_the_repository(repo, monkeypatch, capsys, tmp_path):
    repo.commit("a.py", "a", "feat: FND-0001 first\n\nBacklog-Gate: " + NONCE)
    repo.commit("b.py", "b", "fix: FND-0002 second")
    monkeypatch.setattr(bs, "ROOT", repo.root)
    fake = FakeBc([{"a": "FND-0001", "b": "FND-0002", "decided": False}])
    assert bs.attest(repo.base, None, fake) == bs.RC_USAGE and "needs the owner's private key" in capsys.readouterr().err
    inside_key = repo.root / "inside_key"
    shutil.copy(repo.key, inside_key)
    assert bs.attest(repo.base, str(inside_key), fake) == bs.RC_USAGE and "inside the repository" in capsys.readouterr().err
    assert not (repo.root / att.ATTEST_FILE).exists()                                                                   # nothing written
    assert bs.attest(repo.base, str(repo.key), fake) == bs.RC_OK
    out = capsys.readouterr().out
    assert "attested 2 commit(s)" in out and "1 collision pair(s) are undecided" in out and fake.asked == ["FND-0001", "FND-0002"]
    doc = json.loads((repo.root / att.ATTEST_FILE).read_text(encoding="utf-8"))
    assert [e["tickets"] for e in doc["commits"]] == [["FND-0001"], ["FND-0002"]] and doc["commits"][0]["nonce"] == NONCE and doc["commits"][1]["nonce"] is None
    assert att.verify_signature(repo.root / att.ATTEST_FILE, repo.root / att.SIG_FILE, repo.root / ".github" / "backlog_gate_allowed_signers")[0] is True


# ---------------------------------------------------------------- the branch protection reading

def test_classify_the_github_answers():
    ok = {"contexts": ["backlog-gate", "PG fixture smoke test"], "checks": []}
    assert prot.classify(200, ok)[0] == "PROTECTED"
    assert prot.classify(200, {"contexts": [], "checks": [{"context": "backlog-gate", "app_id": 1}]})[0] == "PROTECTED"
    assert prot.classify(200, {"contexts": ["PG fixture smoke test"], "checks": []})[0] == "UNPROTECTED"
    assert prot.classify(404, {"message": "Branch not protected"})[0] == "UNPROTECTED"
    assert prot.classify(404, {"message": "Required status checks not enabled"})[0] == "UNPROTECTED"
    assert prot.classify(404, {"message": "Not Found"})[0] == "UNVERIFIED"                                                # what a token without permission also gets
    for status in (401, 403, 500, 302):
        assert prot.classify(status, {"message": "x"})[0] == "UNVERIFIED"
    assert prot.classify(200, None)[0] == "UNVERIFIED"


class Resp:
    def __init__(self, status, body):
        self.status, self._body = status, json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_classify_the_public_branch_object():
    on = {"protected": True, "protection": {"enabled": True, "required_status_checks": {"enforcement_level": "everyone", "contexts": ["backlog-gate", "x"], "checks": []}}}
    assert prot.classify_public(200, on)[0] == "PROTECTED"
    assert prot.classify_public(200, {"protected": True, "protection": {"required_status_checks": {"enforcement_level": "everyone", "contexts": [], "checks": [{"context": "backlog-gate"}]}}})[0] == "PROTECTED"
    off = {"protected": True, "protection": {"required_status_checks": {"enforcement_level": "off", "contexts": ["backlog-gate"]}}}
    assert prot.classify_public(200, off)[0] == "UNPROTECTED"                                    # listed but not enforced
    assert prot.classify_public(200, {"protected": True, "protection": {"required_status_checks": {"enforcement_level": "everyone", "contexts": ["x"]}}})[0] == "UNPROTECTED"
    assert prot.classify_public(200, {"protected": False})[0] == "UNPROTECTED"
    assert prot.classify_public(200, {"name": "master"})[0] == "UNVERIFIED" and prot.classify_public(403, {})[0] == "UNVERIFIED"


def test_read_protection_never_raises_and_never_leaks_the_token():
    seen = []

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header("Authorization"), timeout))
        return Resp(200, {"contexts": ["backlog-gate"]})
    assert prot.read_protection("jacormenzana/fondos", "master", "SECRET-TOKEN", opener=opener) == ("PROTECTED", "backlog-gate is a required status check")
    assert seen == [("https://api.github.com/repos/jacormenzana/fondos/branches/master/protection/required_status_checks", "Bearer SECRET-TOKEN", 10.0)]

    def boom(req, timeout):
        raise ConnectionError("down")
    kind, detail = prot.read_protection("o/r", "master", "SECRET-TOKEN", opener=boom)
    assert kind == "UNVERIFIED" and "SECRET-TOKEN" not in detail and "network" in detail

    import urllib.error
    import io

    def refused(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message": "Branch not protected"}'))
    assert prot.read_protection("o/r", "master", "T", opener=refused)[0] == "UNPROTECTED"


def test_without_a_token_the_public_branch_object_answers_and_only_when_it_cannot_is_it_unverified():
    seen = []
    public = {"protected": True, "protection": {"required_status_checks": {"enforcement_level": "everyone", "contexts": ["backlog-gate"]}}}

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header("Authorization")))
        return Resp(200, public)
    assert prot.read_protection("o/r", "master", None, opener=opener)[0] == "PROTECTED"
    assert seen == [("https://api.github.com/repos/o/r/branches/master", None)]                 # one call, no Authorization header
    assert prot.read_protection("o/r", "master", "", opener=lambda r, timeout: Resp(200, {"protected": False}))[0] == "UNPROTECTED"
    kind, detail = prot.read_protection("o/r", "master", None, opener=lambda r, timeout: Resp(404, {"message": "Not Found"}))
    assert kind == "UNVERIFIED" and "BACKLOG_GATE_GITHUB_TOKEN is not set" in detail             # a missing token and an unreadable public object: never silent

    def boom(req, timeout):
        raise OSError("offline")
    assert prot.read_protection("o/r", "master", None, opener=boom)[0] == "UNVERIFIED"


def test_a_token_that_cannot_tell_falls_back_to_the_public_object():
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url.rsplit("/", 1)[-1])
        if req.full_url.endswith("required_status_checks"):
            return Resp(200, {}) if False else (_ for _ in ()).throw(__import__("urllib.error").error.HTTPError(req.full_url, 404, "x", {}, __import__("io").BytesIO(b'{"message": "Not Found"}')))
        return Resp(200, {"protected": True, "protection": {"required_status_checks": {"enforcement_level": "everyone", "contexts": ["backlog-gate"]}}})
    assert prot.read_protection("o/r", "master", "T", opener=opener)[0] == "PROTECTED" and calls == ["required_status_checks", "master"]


def test_the_sync_prints_an_alerta_only_for_an_unprotected_branch():
    assert bs.protection_line("UNPROTECTED", "d").startswith("[ALERTA] branch protection: UNPROTECTED")
    assert "V-GATE-UNVERIFIED" in bs.protection_line("UNVERIFIED", "d") and not bs.protection_line("UNVERIFIED", "d").startswith("[ALERTA]")
    assert bs.protection_line("PROTECTED", "d") == "branch protection: PROTECTED (d)"
