# Open-source attribution

MetricThread's original workflow and application code is copyright 2026 Nivaen Giritharan, under the MIT license.

It uses these upstream packages as dependencies; their source is not vendored into this repository:

| Project | Repository | License | Role |
|---|---|---|---|
| DuckDB | https://github.com/duckdb/duckdb | MIT | Embedded analytics and storage |
| LangExtract | https://github.com/google/langextract | Apache-2.0 | Optional grounded extraction |
| Pydantic | https://github.com/pydantic/pydantic | MIT | Contracts and validation |

Upstream authors retain their copyrights and licenses. Dependency distributions include their own license notices. The lockfile includes transitive dependencies with their separate terms. No upstream project is represented as Nivaen's original creation.

The first validated local environment used DuckDB 1.5.6, LangExtract 1.7.2 and Pydantic 2.14.3. See `uv.lock` for the complete resolved dependency set.
