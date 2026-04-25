"""Registry — auto-discovers modules from filesystem, converts to tools."""

from __future__ import annotations

from pathlib import Path

from mesmer.core.module import DEFAULT_TIER, ModuleConfig, load_module_config


class Registry:
    """
    Module registry with filesystem auto-discovery.
    Scans directories for module.yaml and builds a lookup table.
    Converts modules to OpenAI function-calling tool format.
    """

    def __init__(self):
        self.modules: dict[str, ModuleConfig] = {}
        # Module-name → category folder it lives in (e.g. "attacks",
        # "planners", "profilers", "techniques"). Populated during
        # ``auto_discover`` from the top-level subdirectory under the
        # discover root. Surfaced through :meth:`list_modules` for UI
        # grouping; not consulted at attack runtime.
        self.categories: dict[str, str] = {}

    def register(self, config: ModuleConfig, category: str = ""):
        """Register a single module, optionally tagged with a category."""
        self.modules[config.name] = config
        if category:
            self.categories[config.name] = category

    def get(self, name: str) -> ModuleConfig | None:
        """Get a module by name."""
        return self.modules.get(name)

    def category_of(self, name: str) -> str:
        """Category folder this module was discovered in, or ``""`` if unknown."""
        return self.categories.get(name, "")

    def __contains__(self, name: str) -> bool:
        return name in self.modules

    def __len__(self) -> int:
        return len(self.modules)

    def auto_discover(self, *paths: str | Path):
        """
        Scan directories for modules. Each subdirectory containing
        module.yaml is loaded as a module.

        Modules are tagged with the **top-level subdirectory** they live
        in relative to the discover root — that's the category. So under
        ``modules/`` the children ``attacks/`` ``planners/`` ``profilers/``
        ``techniques/`` become category labels for everything below them.
        Modules loaded from a path that *is* itself a module (no nested
        walk) take that path's directory name as the category.
        """
        for base_path in paths:
            base = Path(base_path)
            if not base.exists():
                continue

            # Path itself is a module — use its dir name as the category.
            config = load_module_config(base)
            if config:
                self.modules[config.name] = config
                self.categories[config.name] = base.name
                continue

            # Recurse into subdirectories — each top-level child names a
            # category that propagates to every module discovered below it.
            for child in sorted(base.iterdir()):
                if child.is_dir() and not child.name.startswith("_"):
                    self._discover_in_category(child, child.name)

    def _discover_in_category(self, path: Path, category: str):
        """Recurse under ``path``, tagging every module found with ``category``."""
        config = load_module_config(path)
        if config:
            self.modules[config.name] = config
            self.categories[config.name] = category
            return
        for child in sorted(path.iterdir()):
            if child.is_dir() and not child.name.startswith("_"):
                self._discover_in_category(child, category)

    def tier_of(self, name: str) -> int:
        """Return the attack-cost tier for a module, or the default.

        Unknown modules fall back to :data:`DEFAULT_TIER` (2, cognitive) so the
        frontier ranker never KeyErrors on a typoed ``sub_modules`` entry.
        The registry is the single source of truth for tier lookup;
        :meth:`propose_frontier` calls this through :meth:`tiers_for`.
        """
        mod = self.modules.get(name)
        return mod.tier if mod else DEFAULT_TIER

    def tiers_for(self, names: list[str]) -> dict[str, int]:
        """Bulk tier lookup — one dict keyed by module name."""
        return {n: self.tier_of(n) for n in names}

    def as_tools(self, names: list[str] | None = None) -> list[dict]:
        """
        Convert modules to OpenAI function-calling tool format.
        If names is provided, only include those modules.
        """
        targets = (
            [self.modules[n] for n in names if n in self.modules]
            if names
            else list(self.modules.values())
        )

        return [
            {
                "type": "function",
                "function": {
                    "name": mod.name,
                    "description": mod.tool_description(),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instruction": {
                                "type": "string",
                                "description": (
                                    "What you want this module to accomplish. "
                                    "Be specific about the goal and any context from "
                                    "the conversation so far."
                                ),
                            },
                            "max_turns": {
                                "type": "integer",
                                "description": (
                                    "Maximum conversation turns to give this module. "
                                    "Leave empty for unlimited."
                                ),
                            },
                            "frontier_id": {
                                "type": "string",
                                "description": (
                                    "If this call executes a suggested 'Frontier' node "
                                    "(see Attack Intelligence), pass its ID here. The "
                                    "graph will then record the attempt as a refinement "
                                    "of that frontier's parent. Omit when making a fresh "
                                    "attempt not on the frontier list."
                                ),
                            },
                        },
                        "required": ["instruction"],
                    },
                },
            }
            for mod in targets
        ]

    def list_modules(self) -> list[dict]:
        """List all modules with basic info."""
        return [
            {
                "name": mod.name,
                "description": mod.description[:100],
                "tier": mod.tier,
                "category": self.categories.get(mod.name, ""),
                "sub_modules": mod.sub_module_names,
            }
            for mod in self.modules.values()
        ]
