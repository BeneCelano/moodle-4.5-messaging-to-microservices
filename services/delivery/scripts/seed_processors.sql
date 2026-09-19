-- Seed of the processor register, on VM2, after the service migrations (which create the `processor`
-- table).
--
--   sudo mysql < services/delivery/scripts/seed_processors.sql
--
-- The list of processors comes from `message_processors`; the enabled flag is not copied: here it
-- means "the gateway delivers on this channel", and after the cutover it is the opposite of the
-- monolith's. `popup` is not registered. Re-run when the administrator installs or removes a processor.

INSERT INTO delivery_gateway.processor (name, enabled, updated_at)
SELECT p.name,
       1,
       UNIX_TIMESTAMP()
  FROM moodle_db.mdl_message_processors p
 WHERE p.name IN ('email', 'airnotifier')
    ON DUPLICATE KEY UPDATE
       enabled    = VALUES(enabled),
       updated_at = VALUES(updated_at);

-- Check of the cutover invariant: enabled in the gateway, disabled in the monolith.

SELECT g.name,
       g.enabled AS in_gateway,
       m.enabled AS in_monolith,
       CASE
           WHEN g.enabled = 1 AND m.enabled = 0 THEN 'correct'
           WHEN g.enabled = 1 AND m.enabled = 1 THEN 'DOUBLE DELIVERY: disable the processor in Moodle'
           WHEN g.enabled = 0 AND m.enabled = 1 THEN 'ONLY THE MONOLITH DELIVERS: the cutover did not happen'
           ELSE 'NOBODY DELIVERS: enable the channel in the gateway'
       END AS outcome
  FROM delivery_gateway.processor g
  JOIN moodle_db.mdl_message_processors m ON m.name = g.name;
