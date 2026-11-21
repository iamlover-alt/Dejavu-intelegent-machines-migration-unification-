from __future__ import annotations

import pytest

from midplane.store import SqliteEpisodeStore

SAMPLE_DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def hello():
-    return "hi"
+    return "hello"
"""


@pytest.fixture
def store() -> SqliteEpisodeStore:
    return SqliteEpisodeStore(":memory:")
