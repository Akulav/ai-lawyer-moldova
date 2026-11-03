import os
import tempfile

# Must run before any app import, so tests can never touch the real index.
os.environ["MAL_DATA"] = tempfile.mkdtemp(prefix="mal-test-")
