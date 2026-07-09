# Shared data-pack schemas

| Schema | Document |
|--------|----------|
| [pack-v1.schema.json](pack-v1.schema.json) | `data/<pack>/pack.json` |
| [index-v1.schema.json](index-v1.schema.json) | `data/index.json` |

Pack-specific payload schemas live under `data/<pack>/schemas/` (e.g. model-catalog pricing/performance).

```bash
./scripts/validate_data_pack.py
```
