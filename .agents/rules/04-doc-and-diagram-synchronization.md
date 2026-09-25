# Rule 04: Keep Impacted Documentation Accurate

Update documentation or diagrams when a change makes them inaccurate, changes a
contract or system relationship they describe, or an applicable project process
requires an update. Do not edit architecture docs or diagrams for unrelated
implementation changes.

Use the mappings below to find likely references, then confirm that the
referenced document actually describes the behavior being changed:

| Change area | Likely reference |
|---|---|
| Services, ports, protocols, network boundaries, gateway routes | `docs/architecture/IT_SYSTEM_ARCHITECTURE.md` and relevant diagrams |
| BCTC ingestion, MinerU OCR, SAG hashing, GIL/MOAT, quant debate, HOSE execution | `docs/architecture/PROJECT_MEMORY.md` and `docs/diagrams/paper-grade-algorithmic-data-flow.html` |
| Prisma/SQLAlchemy schema or migrations | Persistence documentation and relevant entity/store diagrams |
| HOSE settlement, price bands, risk model | Project memory and relevant risk documentation/diagrams |

For an impacted diagram, follow its existing visual conventions and run the
repository's documented validator if one is available and relevant. Do not
invent diagram requirements or run a machine-specific validator path unless the
project still provides it.
