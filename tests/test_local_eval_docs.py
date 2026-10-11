from pathlib import Path


def test_local_eval_schema_keeps_the_smoke_evidence_grade():
    schema = (Path(__file__).resolve().parents[1] / 'data' / 'local-evals' / 'SCHEMA.md').read_text()

    assert 'evidence_grade=D' in schema
    assert 'evidence_grade=A' not in schema
    assert 'harness smoke' in schema
