"""
Hermes Multiplex Plugin — Configuration Management
===================================================

Loads ``config/config.yaml``, provides agent mapping lookups,
setting access with defaults, and platform configuration.

All defaults are embedded so the plugin works even without a config file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedded defaults — mirror the blueprint config.yaml exactly
# ---------------------------------------------------------------------------

_DEFAULT_AGENTS: Dict[str, Dict[str, Any]] = {
    "analyst": {
        "profile": "analyst",
        "prefix": "[analyst]",
        "description": "Root cause investigation, code analysis, FalkorDB graphs",
        "auto_create": True,
    },
    "coder": {
        "profile": "coder",
        "prefix": "[coder]",
        "description": "Code changes, OpenHands delegation, repo work",
        "auto_create": True,
    },
    "researcher": {
        "profile": "researcher",
        "prefix": "[researcher]",
        "description": "Web research, SearXNG, OSINT, comparative analysis",
        "auto_create": True,
    },
    "operative": {
        "profile": "operative",
        "prefix": "[operative]",
        "description": "Docker, SSH, infrastructure, deployments",
        "auto_create": True,
    },
    "reviewer": {
        "profile": "reviewer",
        "prefix": "[reviewer]",
        "description": "Code verification, Selenium testing, adversarial validation",
        "auto_create": True,
    },
    "architect": {
        "profile": "architect",
        "prefix": "[architect]",
        "description": "Blueprint design, Docmost publishing, system architecture",
        "auto_create": True,
    },
    "orchestrator": {
        "profile": "orchestrator",
        "prefix": "[orch]",
        "description": "Multi-agent lane coordination, taskboard management",
        "auto_create": True,
    },
    "conductor": {
        "profile": "conductor",
        "prefix": "[main]",
        "description": "Conductor mediation, delegation, oversight",
        "auto_create": True,
    },
    "shadow": {
        "profile": "shadow",
        "prefix": "[shadow]",
        "description": "Offensive security, dark reasoning, Venice API",
        "auto_create": False,
    },
    "valmet": {
        "profile": "valmet",
        "prefix": "[valmet]",
        "description": "Industrial automation, DNA protocols, LightRAG",
        "auto_create": False,
    },
}

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "default_agent": "conductor",
    "conductor_prefix": "[main]",
    "session_idle_timeout": 900,
    "auto_switch_on_spawn": True,
    "show_delegation_chain": True,
    "prefix_format": "[{agent}-{session_id}]",
    "auto_create_agents": True,
}

_DEFAULT_PLATFORMS: Dict[str, Dict[str, bool]] = {
    "telegram": {"enabled": True},
    "web": {"enabled": True},
    "discord": {"enabled": True},
    "cli": {"enabled": True},
}

# ---------------------------------------------------------------------------
# Module-level loaded state (populated by load_config())
# ---------------------------------------------------------------------------

_agents: Dict[str, Dict[str, Any]] = dict(_DEFAULT_AGENTS)
_settings: Dict[str, Any] = dict(_DEFAULT_SETTINGS)
_platforms: Dict[str, Dict[str, bool]] = dict(_DEFAULT_PLATFORMS)
_loaded: bool = False


def _load_yaml_file(path: Path) -> Optional[Dict[str, Any]]:
    """Best-effort YAML load. Returns None on any failure."""
    try:
        import yaml
    except ImportError:
        logger.debug("yaml module not available; using embedded defaults")
        return None
    if not path.is_file():
        return None
    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Failed to load config from %s: %s", path, exc)
        return None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (mutates base, returns it)."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def load_config(config_path: Optional[str] = None) -> None:
    """Load multiplex config from disk.

    Called once at plugin init. Merges YAML on top of embedded defaults.
    If *config_path* is not given, looks in the plugin's ``config/config.yaml``
    relative to this file.

    Idempotent: subsequent calls are no-ops unless *config_path* is given.
    """
    global _loaded, _agents, _settings, _platforms

    if _loaded and config_path is None:
        return

    # Re-init from defaults before merging
    _agents = dict(_DEFAULT_AGENTS)
    _settings = dict(_DEFAULT_SETTINGS)
    _platforms = dict(_DEFAULT_PLATFORMS)

    if config_path is None:
        config_path = str(Path(__file__).resolve().parent.parent / "config" / "config.yaml")

    data = _load_yaml_file(Path(config_path))
    if data is None:
        _loaded = True
        return

    multiplex_cfg = data.get("multiplex")
    if not isinstance(multiplex_cfg, dict):
        _loaded = True
        return

    # Merge agents
    yaml_agents = multiplex_cfg.get("agents")
    if isinstance(yaml_agents, dict):
        for name, agent_cfg in yaml_agents.items():
            if isinstance(agent_cfg, dict):
                if name in _agents:
                    _deep_merge(_agents[name], agent_cfg)
                else:
                    _agents[name] = dict(agent_cfg)

    # Merge settings
    yaml_settings = multiplex_cfg.get("settings")
    if isinstance(yaml_settings, dict):
        _deep_merge(_settings, yaml_settings)

    # Merge platforms
    yaml_platforms = multiplex_cfg.get("platforms")
    if isinstance(yaml_platforms, dict):
        for plat, plat_cfg in yaml_platforms.items():
            if isinstance(plat_cfg, dict):
                _platforms[plat] = plat_cfg

    _loaded = True
    logger.debug("Multiplex config loaded: %d agents, %d settings, %d platforms",
                 len(_agents), len(_settings), len(_platforms))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_agent_profile(agent_name: str) -> Optional[str]:
    """Return the profile name for *agent_name*, or None if unknown.

    Maps friendly names like "analyst" → "analyst" (profile).
    """
    agent_cfg = _agents.get(agent_name)
    if agent_cfg is None:
        return None
    return agent_cfg.get("profile")


def get_agent_config(agent_name: str) -> Optional[Dict[str, Any]]:
    """Return the full agent config dict for *agent_name*, or None."""
    return _agents.get(agent_name)


def list_agents() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the agent registry."""
    return dict(_agents)


def get_setting(key: str, default: Any = None) -> Any:
    """Return a settings value, falling back to *default*."""
    return _settings.get(key, default)


def is_platform_enabled(platform: str) -> bool:
    """Check if a platform is enabled in config."""
    plat_cfg = _platforms.get(platform)
    if plat_cfg is None:
        return False
    return bool(plat_cfg.get("enabled", False))


def get_state_dir() -> Path:
    """Return the directory for session persistence.

    ~/.hermes/profiles/conductor/plugins/multiplex/state/
    """
    try:
        from hermes_constants import get_hermes_home
        hermes_home = Path(get_hermes_home())
    except ImportError:
        hermes_home = Path.home() / ".hermes"
    return hermes_home / "profiles" / "conductor" / "plugins" / "multiplex" / "state"
