{#
  DuckDB has no built-in initcap() (data/build_lakehouse.py defines a session MACRO
  for it). Snowflake and Databricks/Spark SQL both have a native INITCAP(). Rather
  than carry a DuckDB-only macro, dispatch on the adapter so the exact same model SQL
  runs unchanged on every target this project's profiles.yml defines — the dbt
  version of the "same SQL shape on Snowflake/Spark SQL" claim in docs/GUIDE.md §4.
#}
{% macro initcap_portable(expr) %}
  {%- if target.type in ('snowflake', 'databricks', 'spark') -%}
    initcap({{ expr }})
  {%- else -%}
    (upper(substr(({{ expr }}), 1, 1)) || lower(substr(({{ expr }}), 2)))
  {%- endif -%}
{% endmacro %}
