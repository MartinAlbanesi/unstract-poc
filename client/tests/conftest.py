import sys
from pathlib import Path

# Allow `import client` / `import validate` from the client/ package root
# without needing an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
