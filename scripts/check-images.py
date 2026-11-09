#!/usr/bin/env python3
"""Vérifie les images épinglées sans démon Docker ni dépendance externe."""

import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def get_json(url):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def manifest_digest(repository, reference, token):
    request = Request(
        f"https://registry-1.docker.io/v2/{repository}/manifests/{reference}",
        headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
    )
    with urlopen(request, timeout=30) as response:
        digest = "sha256:" + hashlib.sha256(response.read()).hexdigest()
        if digest != response.headers.get("Docker-Content-Digest"):
            raise ValueError(f"Digest de manifeste invalide : {repository}:{reference}")
        return digest


def image_references():
    for name in ("gitlab-ce", "runner"):
        text = (ROOT / name / "compose.yaml").read_text()
        matches = re.findall(r"^\s+image:\s*(\S+)\s*$", text, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"Une image attendue dans {name}/compose.yaml")
        yield matches[0]
    config = tomllib.loads((ROOT / "runner/config.toml.example").read_text())
    yield config["runners"][0]["docker"]["image"]


def main():
    outdated = False
    for image in image_references():
        match = re.fullmatch(r"([^:@]+):([^@]+)@(sha256:[a-f0-9]{64})", image)
        if not match:
            raise ValueError(f"Version et digest SHA-256 requis : {image}")
        repository, tag, pinned = match.groups()
        if "/" not in repository:
            repository = "library/" + repository
        query = urlencode({
            "service": "registry.docker.io",
            "scope": f"repository:{repository}:pull",
        })
        token = get_json("https://auth.docker.io/token?" + query)["token"]
        tagged = manifest_digest(repository, tag, token)
        latest = manifest_digest(repository, "latest", token)
        if tagged != pinned:
            print(f"ÉCHEC {repository}:{tag} : digest du tag différent du digest épinglé")
            outdated = True
        elif latest != pinned:
            print(f"À VÉRIFIER {repository}:{tag} : latest a changé ({latest})")
            outdated = True
        else:
            print(f"OK {repository}:{tag}@{pinned}")
    return 1 if outdated else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Contrôle impossible : {error}", file=sys.stderr)
        sys.exit(2)
