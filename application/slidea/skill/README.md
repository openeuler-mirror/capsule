# Slidea Skill Package

This directory defines the exported Slidea skill package layout used by `scripts/export_skill.py`.

The repository root under `application/slidea/` remains the source workspace. The actual installed skill package is assembled from `skill/manifest.json` and written to a target directory.

## Export the skill package

From `application/slidea/`:

```bash
python3 scripts/export_skill.py --target "<SKILLS_DIR>/slidea"
```

If you want export and runtime bootstrap in one step:

```bash
python3 scripts/export_skill.py --target "<SKILLS_DIR>/slidea" --bootstrap
```

That command creates a clean skill package at `<SKILLS_DIR>/slidea` with:

- `SKILL.md`
- `INSTALL.md`
- `core/`
- `docs/`
- `scripts/`

In the exported skill package, the first level of `scripts/` is reserved for runtime entrypoints. Install helpers live under `scripts/install/`.
