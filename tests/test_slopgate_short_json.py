import json
from pathlib import Path
import subprocess

def test_short_cli_input_has_valid_empty_json():
    script=Path(__file__).resolve().parents[1]/"tools/slopgate/slopgate"
    result=subprocess.run(["python3",str(script),"check","--stdin","--json","--fast"],input="Fix retry.",text=True,capture_output=True)
    assert result.returncode==0
    assert json.loads(result.stdout)=={"findings":[],"notes":[]}
