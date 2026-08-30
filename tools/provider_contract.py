"""Resolve provider-owned runtime coordinates from tracked project bindings."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import yaml


class ProviderContractError(ValueError):
    """Raised when a provider binding cannot establish a safe runtime route."""


def _https_base_url(value: object, *, label: str) -> SplitResult:
    """Return one normalized credential-free HTTPS provider base URL."""
    raw = str(value).strip()
    if any(character.isspace() for character in raw):
        raise ProviderContractError(
            f"{label} must be a credential-free HTTPS base URL"
        )
    parsed = urlsplit(raw)
    try:
        parsed.port
    except ValueError as exc:
        raise ProviderContractError(f"{label} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderContractError(
            f"{label} must be a credential-free HTTPS base URL"
        )
    path = parsed.path.rstrip("/")
    return SplitResult(parsed.scheme, parsed.netloc, path, "", "")


def normalized_base_url(value: object, *, label: str) -> str:
    """Normalize one provider base URL for exact runtime comparisons."""
    return urlunsplit(_https_base_url(value, label=label))


def provider_binding(root: Path, provider_name: str = "builder") -> dict:
    """Load one declared provider binding through ``standards-binding.yaml``."""
    standards_path = root / "standards-binding.yaml"
    standards = yaml.safe_load(standards_path.read_text(encoding="utf-8"))
    if not isinstance(standards, dict):
        raise ProviderContractError("standards-binding.yaml must contain a mapping")
    declarations = standards.get("spec", {}).get("providers", [])
    matches = [
        declaration
        for declaration in declarations
        if isinstance(declaration, dict) and declaration.get("name") == provider_name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("binding"), str):
        raise ProviderContractError(
            f"standards-binding.yaml must declare provider {provider_name!r} once"
        )
    binding_path = root / matches[0]["binding"]
    binding = yaml.safe_load(binding_path.read_text(encoding="utf-8"))
    if not isinstance(binding, dict):
        raise ProviderContractError(f"{binding_path.name} must contain a mapping")
    if binding.get("metadata", {}).get("name") != provider_name:
        raise ProviderContractError(
            f"{binding_path.name} metadata.name disagrees with its declaration"
        )
    return binding


def provider_base_url(root: Path, provider_name: str = "builder") -> str:
    """Return the normalized dispatch base URL declared by one provider."""
    binding = provider_binding(root, provider_name)
    value = binding.get("spec", {}).get("dispatch", {}).get("baseUrl", "")
    return normalized_base_url(value, label=f"{provider_name} dispatch baseUrl")


def provider_host(root: Path, provider_name: str = "builder") -> str:
    """Return the provider host derived from its tracked dispatch binding."""
    host = urlsplit(provider_base_url(root, provider_name)).hostname
    if host is None:  # Defensive: provider_base_url already proves a hostname.
        raise ProviderContractError(f"{provider_name} dispatch host is unavailable")
    return host


def require_provider_repository(repository: object, base_url: object) -> str:
    """Validate and return a repository URL below the approved provider base."""
    approved = _https_base_url(base_url, label="approved provider base URL")
    candidate = _https_base_url(repository, label="contract repository")
    try:
        approved_origin = (approved.scheme, approved.hostname, approved.port)
        candidate_origin = (candidate.scheme, candidate.hostname, candidate.port)
    except ValueError as exc:
        raise ProviderContractError("provider URL port is invalid") from exc
    base_path = approved.path.rstrip("/")
    repository_path = candidate.path.rstrip("/")
    if approved_origin != candidate_origin or not repository_path:
        raise ProviderContractError("contract repository is outside the approved provider")
    if base_path and not repository_path.startswith(f"{base_path}/"):
        raise ProviderContractError("contract repository is outside the approved provider path")
    return urlunsplit(candidate)
