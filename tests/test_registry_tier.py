"""Tests for Registry.tier_of / tiers_for — bulk tier lookup by name."""

from mesmer.core.module import DEFAULT_TIER, ModuleConfig
from mesmer.core.registry import Registry


class TestTierOf:
    def test_returns_module_tier(self):
        """Explicit tier on a registered module is returned verbatim."""
        r = Registry()
        r.register(ModuleConfig(name="naive", tier=0))
        r.register(ModuleConfig(name="cog", tier=2))
        assert r.tier_of("naive") == 0
        assert r.tier_of("cog") == 2

    def test_unknown_module_returns_default_tier(self):
        """Typoed sub_modules entry falls back to the default — never KeyErrors."""
        r = Registry()
        assert r.tier_of("not-a-module") == DEFAULT_TIER

    def test_tiers_for_bulk_lookup(self):
        """`tiers_for` returns one dict entry per input name, in order."""
        r = Registry()
        r.register(ModuleConfig(name="a", tier=0))
        r.register(ModuleConfig(name="b", tier=1))
        r.register(ModuleConfig(name="c", tier=2))
        got = r.tiers_for(["a", "b", "c", "missing"])
        assert got == {"a": 0, "b": 1, "c": 2, "missing": DEFAULT_TIER}
