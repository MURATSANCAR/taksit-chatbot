# Taksitlio Query Golden Set v1

ADR-013 product-query golden (1000 cases). See:

- [`docs/adr/ADR-013-layered-verification-and-release-gates.md`](../../../docs/adr/ADR-013-layered-verification-and-release-gates.md)
- [`docs/runbooks/ADR-013-layered-verification.md`](../../../docs/runbooks/ADR-013-layered-verification.md)

Regenerate:

```bash
PYTHONPATH=src python evaluation/datasets/_generate_query_golden_v1.py
```

Run parser lane:

```bash
PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane parser
```
