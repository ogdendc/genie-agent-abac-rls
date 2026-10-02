-- =============================================================================
-- 03 — Grants
-- =============================================================================
-- Every end user who queries through Genie needs to be able to reach the data and
-- execute the filter function. Grant to a group (recommended) or to `account users`.
-- The ABAC policy still restricts the ROWS they see — these grants only make the
-- table and function reachable; the filter does the per-user scoping.
-- =============================================================================

GRANT USE CATALOG  ON CATALOG <catalog>                       TO `account users`;
GRANT USE SCHEMA   ON SCHEMA  <catalog>.<schema>              TO `account users`;
GRANT SELECT       ON TABLE   <catalog>.<schema>.client_book  TO `account users`;
GRANT EXECUTE      ON FUNCTION <catalog>.<schema>.fa_book_filter TO `account users`;

-- Also required, but granted outside SQL (UI / REST / CLI):
--   * Genie space:  CAN RUN  (so the user can query the space)
--   * SQL warehouse: CAN USE (the warehouse the Genie space runs on)
-- See docs/02-genie-space.md.
--
-- Prefer granting to a dedicated group (e.g. `advisors`) over `account users`
-- in production so access is managed through group membership.
