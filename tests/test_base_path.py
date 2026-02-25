"""Tests for base path support (reverse proxy sub-path deployment)."""

import re
from pathlib import Path


def test_live_html_has_no_absolute_static_paths():
    """Verify the live.html template uses relative paths for static assets.

    When served behind a reverse proxy at a sub-path like /env/default/,
    absolute paths like /static/js/app.js would bypass the proxy prefix.
    The script tag must use a relative path (no leading /).
    """
    template_path = (
        Path(__file__).parent.parent
        / "src"
        / "vibedeck"
        / "templates"
        / "live.html"
    )
    content = template_path.read_text()

    # Should NOT have absolute static asset paths
    absolute_static = re.findall(r'src="/static/', content)
    assert absolute_static == [], (
        f"Found absolute static asset paths in live.html: {absolute_static}. "
        "Use relative paths (e.g., src=\"static/js/app.js\") for reverse proxy compatibility."
    )

    # Should have the relative static asset path
    assert 'src="static/js/app.js"' in content, (
        "Expected relative script path src=\"static/js/app.js\" in live.html"
    )


def test_all_js_files_use_relative_paths():
    """Verify no JS file has hardcoded absolute fetch/EventSource/src paths.

    All API calls must use relative paths (no leading /) so the browser
    resolves them relative to the page URL, supporting sub-path deployment.
    """
    js_dir = (
        Path(__file__).parent.parent
        / "src"
        / "vibedeck"
        / "templates"
        / "static"
        / "js"
    )

    # Patterns that indicate hardcoded absolute paths
    bad_patterns = [
        # fetch('/...' or fetch(`/...
        re.compile(r"""fetch\(\s*['"`]/"""),
        # new EventSource('/... or new EventSource(`/...
        re.compile(r"""new\s+EventSource\(\s*['"`]/"""),
        # .src = '/api/... or .src = `/api/...
        re.compile(r"""\.src\s*=\s*['"`]/api/"""),
    ]

    violations = []
    for js_file in sorted(js_dir.glob("*.js")):
        content = js_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in bad_patterns:
                if pattern.search(line):
                    violations.append(f"  {js_file.name}:{i}: {line.strip()}")

    assert violations == [], (
        "Found hardcoded absolute paths in JS files (use relative paths):\n"
        + "\n".join(violations)
    )


def test_no_apiurl_references_in_js():
    """Verify no JS file references apiUrl — relative paths are used instead."""
    js_dir = (
        Path(__file__).parent.parent
        / "src"
        / "vibedeck"
        / "templates"
        / "static"
        / "js"
    )

    violations = []
    for js_file in sorted(js_dir.glob("*.js")):
        content = js_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if "apiUrl" in line:
                violations.append(f"  {js_file.name}:{i}: {line.strip()}")

    assert violations == [], (
        "Found apiUrl references in JS files (use relative paths instead):\n"
        + "\n".join(violations)
    )
