"""Agent profile loading and installation helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

import frontmatter

from agentfactor import constants
from agentfactor.models.agent_profile import AgentProfile
from agentfactor.utils.pathing import ensure_runtime_directories

ProfileScope = Literal["user", "project"]

_MANIFEST_FILENAME = "install-manifest.json"


class AgentProfileError(RuntimeError):
    """Raised when an agent profile cannot be loaded or parsed."""


def _project_agent_context_dir() -> Path:
    return Path.cwd() / ".conductor" / "agent-context"


def _resolve_scope_directory(scope: ProfileScope, *, create: bool) -> Path:
    if scope == "project":
        directory = _project_agent_context_dir()
    else:
        # Ensure global runtime directories are materialized before use.
        if create:
            ensure_runtime_directories()
        directory = constants.AGENT_CONTEXT_DIR

    if create:
        directory.mkdir(parents=True, exist_ok=True)

    return directory


def _parse_profile_text(text: str) -> AgentProfile:
    post = frontmatter.loads(text)
    metadata = dict(post.metadata)
    metadata["body"] = post.content.strip()
    return AgentProfile(**metadata)


def _load_profile_file(path: Path) -> Optional[AgentProfile]:
    """Return an AgentProfile if the markdown file exists."""
    if not path.exists():
        return None

    return _parse_profile_text(path.read_text())


def _manifest_path(directory: Path) -> Path:
    return directory / _MANIFEST_FILENAME


def _load_manifest(directory: Path) -> List[Dict[str, str]]:
    manifest_file = _manifest_path(directory)
    if not manifest_file.exists():
        return []
    try:
        data = json.loads(manifest_file.read_text())
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
    except json.JSONDecodeError:
        return []
    return []


def _write_manifest(directory: Path, entries: List[Dict[str, str]]) -> None:
    manifest_file = _manifest_path(directory)
    manifest_file.write_text(json.dumps(entries, indent=2) + "\n")


def _bundled_profiles_dir():
    return resources.files("agentfactor.agent_store")


def bundled_profile_names() -> List[str]:
    """Return the list of bundled profile names shipped with the package."""
    try:
        base = _bundled_profiles_dir()
    except FileNotFoundError:
        return []

    names = [
        path.stem for path in base.iterdir() if path.is_file() and path.suffix == ".md"
    ]
    return sorted(names)


def _load_bundled_profile_text(name: str) -> str:
    try:
        bundled_file = _bundled_profiles_dir() / f"{name}.md"
    except FileNotFoundError as exc:
        raise AgentProfileError(f"Bundled agent profile '{name}' not found.") from exc

    if not bundled_file.is_file():
        raise AgentProfileError(f"Bundled agent profile '{name}' not found.")

    return bundled_file.read_text()


def load_agent_profile(name: str) -> AgentProfile:
    """Load an agent profile from project overrides, user installs, or bundled store."""
    try:
        project_dir = _resolve_scope_directory("project", create=False)
        if project_dir.exists():
            profile = _load_profile_file(project_dir / f"{name}.md")
            if profile is not None:
                return profile

        user_profile = _load_profile_file(constants.AGENT_CONTEXT_DIR / f"{name}.md")
        if user_profile is not None:
            return user_profile

        return _parse_profile_text(_load_bundled_profile_text(name))

    except Exception as exc:  # pragma: no cover - wrapped for CLI friendly errors
        raise AgentProfileError(f"Failed to load agent profile '{name}': {exc}") from exc


def install_agent_profile(
    source: str,
    *,
    name: Optional[str] = None,
    scope: ProfileScope = "user",
    force: bool = False,
) -> Dict[str, str]:
    """Install an agent profile into the requested scope."""
    directory = _resolve_scope_directory(scope, create=True)
    source_path = Path(source).expanduser()

    if source_path.exists():
        raw_text = source_path.read_text()
        source_descriptor = f"path:{source_path}"
        default_name = source_path.stem
    else:
        if source not in bundled_profile_names():
            raise AgentProfileError(
                f"Agent profile source '{source}' not found as a file or bundled profile."
            )
        raw_text = _load_bundled_profile_text(source)
        source_descriptor = f"bundled:{source}"
        default_name = source

    profile = _parse_profile_text(raw_text)
    profile_name = profile.name or name or default_name
    target_name = name or default_name or profile.name
    if not target_name:
        raise AgentProfileError("Unable to determine profile name; provide --name explicitly.")

    destination = directory / f"{target_name}.md"
    if destination.exists() and not force:
        raise AgentProfileError(
            f"Agent profile '{target_name}' already exists in {scope} scope. Use --force to overwrite."
        )

    destination.write_text(raw_text)

    installed_at = datetime.now(timezone.utc).isoformat()
    manifest_entries = [
        entry
        for entry in _load_manifest(directory)
        if entry.get("filename") != target_name
    ]
    manifest_entries.append(
        {
            "name": profile_name or target_name,
            "filename": target_name,
            "description": profile.description,
            "scope": scope,
            "path": str(destination),
            "source": source_descriptor,
            "installed_at": installed_at,
        }
    )
    _write_manifest(directory, manifest_entries)

    return manifest_entries[-1]


def _catalog_entry(profile: AgentProfile, *, path: str, scope: str, source: str) -> Dict[str, Any]:
    return {
        "name": profile.name,
        "description": profile.description,
        "default_provider": profile.default_provider or "",
        "tags": profile.tags,
        "scope": scope,
        "source": source,
        "path": path,
        "filename": Path(path).name if scope != "bundled" else "",
    }


def _installed_profiles(scope: ProfileScope) -> Iterable[Dict[str, str]]:
    directory = _resolve_scope_directory(scope, create=False)
    if not directory.exists():
        return []

    manifest_entries = _load_manifest(directory)
    if manifest_entries:

        def sorted_entries():
            for entry in manifest_entries:
                entry.setdefault("scope", scope)
                entry.setdefault("filename", Path(entry.get("path", "")).name)
                yield entry

        return sorted(sorted_entries(), key=lambda item: item.get("name", ""))

    results: List[Dict[str, str]] = []
    for file in sorted(directory.glob("*.md")):
        profile = _parse_profile_text(file.read_text())
        results.append(
            _catalog_entry(
                profile,
                path=str(file),
                scope=scope,
                source="filesystem",
            )
        )
    return results


def get_persona_catalog(
    *,
    include_bundled: bool = True,
    include_installed: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return bundled and installed agent profile metadata."""
    catalog: Dict[str, List[Dict[str, Any]]] = {"bundled": [], "installed": []}

    if include_bundled:
        for name in bundled_profile_names():
            profile = _parse_profile_text(_load_bundled_profile_text(name))
            catalog["bundled"].append(
                _catalog_entry(
                    profile,
                    path=f"bundled:{name}",
                    scope="bundled",
                    source="bundled",
                )
            )
        catalog["bundled"].sort(key=lambda item: item["name"])

    if include_installed:
        installed = []
        for scope in ("project", "user"):
            installed.extend(_installed_profiles(scope=scope))  # type: ignore[arg-type]
        catalog["installed"] = sorted(installed, key=lambda item: item.get("name", ""))

    return catalog
