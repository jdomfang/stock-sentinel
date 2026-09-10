"""Public Education page; no authentication or paid work."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.education_public import render_native_page

render_native_page('education')
