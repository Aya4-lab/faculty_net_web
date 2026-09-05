# Data Layout

This project separates data by role:

- `raw/` - scraped or source data kept as-is
- `analysis/` - embeddings, intermediate tables, and derived analysis inputs
- `display/` - final JSON used by the web app

Current layout:

- `raw/researchmap/` - researchmap index, profile outputs, session cookie
- `raw/ridb/` - RIDB source files
- `raw/syllabus/` - syllabus source files
- `analysis/` - embedding and analysis intermediates
- `display/` - final network JSON for the UI

Legacy files at the repository root are still supported as fallbacks while the new layout is being adopted.
