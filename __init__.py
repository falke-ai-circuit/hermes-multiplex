"""hermes-multiplex plugin — re-exports register() from src/__init__.py."""
import sys as _sys
import os as _os

# Hermes plugin loader does NOT add the plugin directory to sys.path.
# Without this, 'from src import register' fails with ModuleNotFoundError.
_plugin_dir = _os.path.dirname(_os.path.abspath(__file__))
if _plugin_dir not in _sys.path:
    _sys.path.insert(0, _plugin_dir)

from src import register

__all__ = ["register"]
