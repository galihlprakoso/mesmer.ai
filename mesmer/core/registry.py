"""Registry — auto-discovers modules from filesystem, converts to tools."""

from __future__ import annotations

from pathlib import Path

from mesmer.core.module import DEFAULT_TIER, ModuleConfig
from mesmer.core.modules import FileModuleCatalog, ModuleCatalog, ModuleRecord, ModuleSource


class Registry:
    """
    Runtime module registry.
    Loads module records from catalogs and builds a lookup table.
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
        self.sources: dict[str, str] = {}
        self.source_ids: dict[str, str] = {}
        self.module_paths: dict[str, str] = {}
        self.editable: dict[str, bool] = {}

    def register(self, config: ModuleConfig, category: str = ""):
        """Register a single module, optionally tagged with a category."""
        self.modules[config.name] = config
        if category:
            self.categories[config.name] = category
        self.sources.setdefault(config.name, "")
        self.source_ids.setdefault(config.name, "")
        self.module_paths.setdefault(config.name, "")
        self.editable.setdefault(config.name, False)

    def register_record(self, record: ModuleRecord) -> None:
        """Register one catalog record, overriding any earlier record by name."""
        name = record.config.name
        self.modules[name] = record.config
        if record.category:
            self.categories[name] = record.category
        self.sources[name] = record.source.value
        self.source_ids[name] = record.source_id
        self.module_paths[name] = record.path
        self.editable[name] = record.editable

    def load_catalog(self, catalog: ModuleCatalog) -> None:
        """Register every record from a catalog."""
        for record in catalog.list_records():
            self.register_record(record)

    def load_catalogs(self, *catalogs: ModuleCatalog) -> None:
        """Register records from multiple catalogs in order."""
        for catalog in catalogs:
            self.load_catalog(catalog)

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
            self.load_catalog(FileModuleCatalog(base_path, source=ModuleSource.LOCAL_PATH))

    def tier_of(self, name: str) -> int:
        """Return the attack-cost tier for a module, or the default.

        Unknown modules fall back to :data:`DEFAULT_TIER` (2, cognitive).
        The registry is the single source of truth for tier lookup.
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
                            "experiment_id": {
                                "type": "string",
                                "description": (
                                    "If this call executes a Recommended Experiment "
                                    "from the Belief Attack Graph (see the brief in your "
                                    "user prompt), pass the experiment id (`fx_…`) here. "
                                    "The resulting Attempt will link explicitly to that "
                                    "experiment's hypothesis and strategy, so the planner "
                                    "tracks belief shifts precisely. Executive dispatch "
                                    "requires an open matching experiment; if none fits, "
                                    "conclude honestly instead of freelancing."
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
                "source": self.sources.get(mod.name, ""),
                "source_id": self.source_ids.get(mod.name, ""),
                "path": self.module_paths.get(mod.name, ""),
                "editable": self.editable.get(mod.name, False),
            }
            for mod in self.modules.values()
        ]
