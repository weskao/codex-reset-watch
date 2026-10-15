# TODO

- [ ] Publish update-kit 0.1.0 (CI and release run with `UV_NO_SOURCES=1`, so they stay red until it is
      on the index); then remove the `[tool.uv.sources]` block from `pyproject.toml` (it also breaks `scripts/install.py` /
      `make install` on clones without `../update-kit`) and re-lock, before the first push.
- [ ] Verify `crw update` on Windows (detached install; running `.exe` is locked).
