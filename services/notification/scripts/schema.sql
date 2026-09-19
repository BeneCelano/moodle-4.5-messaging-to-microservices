-- Provisioning of the NotificationService database, on VM2, with an administrative user. The
-- placeholders PASSWORD_SVC, PASSWORD_SEED and PASSWORD_VIEW are replaced on the fly:
--
--   read -rsp 'notification_svc: '  P_SVC;  echo
--   read -rsp 'notification_seed: ' P_SEED; echo
--   read -rsp 'notification_view: ' P_VIEW; echo
--   sed -e "s/PASSWORD_SVC/$P_SVC/g" -e "s/PASSWORD_SEED/$P_SEED/g" -e "s/PASSWORD_VIEW/$P_VIEW/g" \
--       schema.sql | sudo mysql
--   unset P_SVC P_SEED P_VIEW
--
-- Passwords: `openssl rand -hex 24`. The script can be re-run. The tables are created by the service
-- migrations; here schema, users and views.

-- ---------------------------------------------------------------------------------------------------
-- 1. The service schema
-- ---------------------------------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS notification_service
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------------------------------
-- 2. The service user: privileges on its own schema only
-- ---------------------------------------------------------------------------------------------------

CREATE USER IF NOT EXISTS 'notification_svc'@'%' IDENTIFIED BY 'PASSWORD_SVC';

-- ALTER USER after CREATE USER IF NOT EXISTS: on an existing user the first statement is
-- silent.
ALTER USER 'notification_svc'@'%' IDENTIFIED BY 'PASSWORD_SVC';

GRANT ALL PRIVILEGES ON notification_service.* TO 'notification_svc'@'%';

-- ---------------------------------------------------------------------------------------------------
-- 3. The seed and reconciliation user
-- ---------------------------------------------------------------------------------------------------
--
-- The only identity that reads both schemas: read on the two copied tables of the monolith, write on
-- the service schema. Removed with its Secret when the migration is closed:
--
--   kubectl -n messaging delete secret notification-service-seed-secret
--   DROP USER 'notification_seed'@'%';

CREATE USER IF NOT EXISTS 'notification_seed'@'%' IDENTIFIED BY 'PASSWORD_SEED';
ALTER USER 'notification_seed'@'%' IDENTIFIED BY 'PASSWORD_SEED';

GRANT ALL PRIVILEGES ON notification_service.* TO 'notification_seed'@'%';

GRANT SELECT ON moodle_db.mdl_notifications              TO 'notification_seed'@'%';
GRANT SELECT ON moodle_db.mdl_message_popup_notifications TO 'notification_seed'@'%';

-- ---------------------------------------------------------------------------------------------------
-- 4. The definer of the views
-- ---------------------------------------------------------------------------------------------------
--
-- A view runs with the privileges of its definer: this user reads the needed tables only, is declared
-- on 'localhost' and never connects.

CREATE USER IF NOT EXISTS 'notification_view'@'localhost' IDENTIFIED BY 'PASSWORD_VIEW';
ALTER USER 'notification_view'@'localhost' IDENTIFIED BY 'PASSWORD_VIEW';

GRANT SELECT ON moodle_db.mdl_user   TO 'notification_view'@'localhost';
GRANT SELECT ON moodle_db.mdl_config TO 'notification_view'@'localhost';
GRANT SELECT ON moodle_db.mdl_course TO 'notification_view'@'localhost';

-- ---------------------------------------------------------------------------------------------------
-- 5. The recipient
-- ---------------------------------------------------------------------------------------------------
--
--   email_stop, deleted    whether this person receives notifications
--   lang                   the language of the notifications the service composes
--   first_name, last_name  the name of who made a contact request

CREATE OR REPLACE ALGORITHM = MERGE
    DEFINER = 'notification_view'@'localhost'
    SQL SECURITY DEFINER
    VIEW notification_service.user_view AS
SELECT u.id        AS user_id,
       u.firstname AS first_name,
       u.lastname  AS last_name,
       u.lang      AS lang,
       u.emailstop AS email_stop,
       u.deleted   AS deleted
  FROM moodle_db.mdl_user u;

-- ---------------------------------------------------------------------------------------------------
-- 6. The site settings
-- ---------------------------------------------------------------------------------------------------
--
-- The site name (the full name of the course with id 1) and `fullnamedisplay` (default 'language').
-- `wwwroot` lives in config.php and comes from the ConfigMap.

CREATE OR REPLACE ALGORITHM = UNDEFINED
    DEFINER = 'notification_view'@'localhost'
    SQL SECURITY DEFINER
    VIEW notification_service.site_view AS
SELECT 'fullnamedisplay' AS setting_name,
       CAST(COALESCE((SELECT c.value FROM moodle_db.mdl_config c WHERE c.name = 'fullnamedisplay'),
                     'language') AS CHAR) AS setting_value
UNION ALL
SELECT 'sitename' AS setting_name,
       CAST(COALESCE((SELECT s.fullname FROM moodle_db.mdl_course s WHERE s.id = 1),
                     '') AS CHAR) AS setting_value;


-- ---------------------------------------------------------------------------------------------------
-- 7. Read privilege on the views
-- ---------------------------------------------------------------------------------------------------

GRANT SELECT ON notification_service.user_view TO 'notification_svc'@'%';
GRANT SELECT ON notification_service.site_view TO 'notification_svc'@'%';

FLUSH PRIVILEGES;

-- ---------------------------------------------------------------------------------------------------
-- 8. Check
-- ---------------------------------------------------------------------------------------------------
--
-- The views answer; for site_view the empty values are counted (an empty `sitename` means the course
-- with id 1 is missing).

SELECT 'user_view' AS view_name, COUNT(*) AS rows_found, NULL AS empty_values
  FROM notification_service.user_view
UNION ALL
SELECT 'site_view', COUNT(*),
       SUM(setting_value IS NULL OR setting_value = '')
  FROM notification_service.site_view;
