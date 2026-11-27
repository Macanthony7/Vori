"""Run every check, with coverage. From the repository root:

    python run_tests.py          everything: static checks, backend tests, E2E browser tests, coverage
    python run_tests.py --fast   skip the browser tests (about 2 minutes faster)

Exits non-zero on the first failure, or if backend coverage drops below COVERAGE_MIN.
Process and what each layer covers: docs/testing.md
"""
import filecmp
import glob
import os
import shutil
import subprocess
import sys
import tempfile

COVERAGE_MIN = 90  # percent of backend lines; raise it as tests grow, never lower it to make a change pass
ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(title, *cmd, cwd=ROOT):
    print(f"\n=== {title} ===", flush=True)
    if subprocess.run(cmd, cwd=cwd).returncode != 0:
        sys.exit(f"\nFAILED: {title}")


def check_app_build(npm):
    """frontend/public/app/ (committed, served as is) must be what the sources in frontend/app/ build to."""
    app = os.path.join(ROOT, "frontend", "app")
    if not os.path.isdir(os.path.join(app, "node_modules")):
        run("Frontend app: install packages", npm, "ci", cwd=app)
    run("Frontend app: type check", npm, "run", "typecheck", cwd=app)
    with tempfile.TemporaryDirectory() as out:
        run("Frontend app: build", npm, "exec", "--", "vite", "build", "--outDir", out, "--emptyOutDir", "--logLevel", "warn", cwd=app)
        diff = filecmp.dircmp(out, os.path.join(ROOT, "frontend", "public", "app"))
        def changed(d):
            return d.left_only + d.right_only + d.diff_files + [f for s in d.subdirs.values() for f in changed(s)]
        if changed(diff):
            sys.exit(f"\nFAILED: frontend/public/app/ is out of date ({', '.join(changed(diff))}): "
                     "run `npm run build` in frontend/app")
    print("frontend/public/app/ is up to date")


def main():
    fast = "--fast" in sys.argv
    for f in glob.glob(os.path.join(ROOT, ".coverage*")):
        os.remove(f)

    run("Seed frame is built from its sources", PY, "build_frame.py", "--check", cwd=os.path.join(ROOT, "frontend"))
    node = shutil.which("node")
    if node:
        scripts = glob.glob(os.path.join(ROOT, "frontend", "public", "*.js")) + \
            glob.glob(os.path.join(ROOT, "frontend", "frame", "*.js"))
        for js in scripts:
            run(f"JavaScript syntax: {os.path.relpath(js, ROOT)}", node, "--check", js)
        check_app_build(shutil.which("npm"))
    else:
        print("\n(node not found: skipping the JavaScript syntax check and the app build check)")

    # Separate processes: each suite sets its own temp database and settings before importing the app.
    run("Backend: unit and API tests", PY, "-m", "coverage", "run", "-m", "pytest", "backend", "-q")
    if not fast:
        run("E2E: real browser, real frame, fake blockchain", PY, "-m", "coverage", "run", "-m", "pytest",
            "tests/e2e", "-q")

    run("Coverage", PY, "-m", "coverage", "combine", "-q")
    run("Coverage report", PY, "-m", "coverage", "report", f"--fail-under={0 if fast else COVERAGE_MIN}")
    subprocess.run([PY, "-m", "coverage", "html", "-q"], cwd=ROOT)
    print("\nAll checks passed. Line-by-line coverage: htmlcov/index.html")


if __name__ == "__main__":
    main()
