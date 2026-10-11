from pathlib import Path
import json
import subprocess

def test_seven_short_sentences_trigger_fragment_guard():
    script=Path(__file__).resolve().parents[1]/"tools/slopgate/slopgate"
    text="We moved. It failed. We stopped. Logs vanished. Jobs stalled. Users waited. Nothing recovered."
    result=subprocess.run(["python3",str(script),"check","--stdin","--json","--fast"],input=text,text=True,capture_output=True)
    assert result.returncode==1
    assert any(f["rule"]=="fragment-pileup" for f in json.loads(result.stdout)["findings"])
