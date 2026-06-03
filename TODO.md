# TODO - Fix static CSS not loading on other laptops

## Plan (high level)
1. Make Flask serve static files from a deterministic location even with nested `smart campus/.../static` folders.
2. Ensure all templates reference the same CSS path (they already use `url_for('static', ...)`).
3. Add a small runtime debug route/log to confirm which static folder Flask is actually serving.

## Steps to implement
- [ ] Update `app.py` so `static_folder` uses a merged/static fallback strategy:
  - Prefer `<repo_root>/static` if present.
  - Else fall back to `<repo_root>/smart campus/static` or `<repo_root>/smart campus/smart campus/static`.
- [ ] Optionally copy/merge nested static contents into `<repo_root>/static` at startup (safer for images too).
- [ ] Run the app and confirm `GET /static/style.css` returns 200 from a fresh browser.

