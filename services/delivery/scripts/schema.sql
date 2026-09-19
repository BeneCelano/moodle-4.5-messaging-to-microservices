-- Provisioning of the DeliveryGateway database, on VM2, with an administrative user. The placeholders
-- PASSWORD_SVC and PASSWORD_VIEW are replaced on the fly:
--
--   read -rsp 'delivery_svc: ' P_SVC; echo
--   read -rsp 'delivery_view: ' P_VIEW; echo
--   sed -e "s/PASSWORD_SVC/$P_SVC/g" -e "s/PASSWORD_VIEW/$P_VIEW/g" schema.sql | sudo mysql
--   unset P_SVC P_VIEW
--
-- Passwords: `openssl rand -hex 24`. The script can be re-run. The tables are created by the service
-- migrations; here schema, users and views.

-- ---------------------------------------------------------------------------------------------------
-- 1. The service schema
-- ---------------------------------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS delivery_gateway
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------------------------------
-- 2. The service user: privileges on its own schema only
-- ---------------------------------------------------------------------------------------------------

CREATE USER IF NOT EXISTS 'delivery_svc'@'%' IDENTIFIED BY 'PASSWORD_SVC';

-- ALTER USER after CREATE USER IF NOT EXISTS: on an existing user the first statement is
-- silent.
ALTER USER 'delivery_svc'@'%' IDENTIFIED BY 'PASSWORD_SVC';

GRANT ALL PRIVILEGES ON delivery_gateway.* TO 'delivery_svc'@'%';

-- ---------------------------------------------------------------------------------------------------
-- 3. The definer of the views
-- ---------------------------------------------------------------------------------------------------
--
-- A view runs with the privileges of its definer: this user reads the needed tables only, is declared
-- on 'localhost' and never connects.

CREATE USER IF NOT EXISTS 'delivery_view'@'localhost' IDENTIFIED BY 'PASSWORD_VIEW';
ALTER USER 'delivery_view'@'localhost' IDENTIFIED BY 'PASSWORD_VIEW';

GRANT SELECT ON moodle_db.mdl_user                        TO 'delivery_view'@'localhost';
GRANT SELECT ON moodle_db.mdl_user_preferences            TO 'delivery_view'@'localhost';
GRANT SELECT ON moodle_db.mdl_config_plugins              TO 'delivery_view'@'localhost';
GRANT SELECT ON moodle_db.mdl_user_devices                TO 'delivery_view'@'localhost';
GRANT SELECT ON moodle_db.mdl_message_airnotifier_devices TO 'delivery_view'@'localhost';

-- ---------------------------------------------------------------------------------------------------
-- 4. The recipient
-- ---------------------------------------------------------------------------------------------------
--
--   email, mailformat         address and body format (email_to_user())
--   auth, suspended, deleted  whether the user is skipped (message_output_email::send_message())
--   firstname, lastname       the name shown in the header
--   emailstop                 notifications switched off by the user (message_send())
--
-- Read at delivery time, not when the message was written.

CREATE ALGORITHM = MERGE
    DEFINER = 'delivery_view'@'localhost'
    SQL SECURITY DEFINER
    VIEW delivery_gateway.recipient_view AS
SELECT u.id         AS user_id,
       u.email      AS email,
       u.mailformat AS mail_format,
       u.auth       AS auth_method,
       u.suspended  AS suspended,
       u.deleted    AS deleted,
       u.firstname  AS first_name,
       u.lastname   AS last_name,
       u.emailstop  AS email_stop
  FROM moodle_db.mdl_user u;

-- ---------------------------------------------------------------------------------------------------
-- 5. The channel preferences
-- ---------------------------------------------------------------------------------------------------
--
-- From user_preferences, the keys the gateway reads:
--
--   message_provider_<component>_<name>_enabled   which channels for that kind of message
--   message_processor_email_email                 alternative address for notifications
--
-- LEFT() instead of LIKE: in MySQL the underscore is a wildcard.

CREATE ALGORITHM = MERGE
    DEFINER = 'delivery_view'@'localhost'
    SQL SECURITY DEFINER
    VIEW delivery_gateway.channel_preference_view AS
SELECT p.userid AS user_id,
       p.name   AS preference_name,
       p.value  AS preference_value
  FROM moodle_db.mdl_user_preferences p
 WHERE LEFT(p.name, 17) = 'message_provider_'
    OR LEFT(p.name, 18) = 'message_processor_';

-- ---------------------------------------------------------------------------------------------------
-- 6. The site defaults
-- ---------------------------------------------------------------------------------------------------
--
-- From config_plugins, plugin 'message':
--
--   <processor>_provider_<component>_<name>_locked    the administrator locked the choice
--   message_provider_<component>_<name>_enabled       the default channels

CREATE ALGORITHM = MERGE
    DEFINER = 'delivery_view'@'localhost'
    SQL SECURITY DEFINER
    VIEW delivery_gateway.site_default_view AS
SELECT c.name  AS setting_name,
       c.value AS setting_value
  FROM moodle_db.mdl_config_plugins c
 WHERE c.plugin = 'message';

-- ---------------------------------------------------------------------------------------------------
-- 7. The registered devices, for the airnotifier channel
-- ---------------------------------------------------------------------------------------------------
--
-- user_devices for the push token, message_airnotifier_devices for which ones the owner enabled.

CREATE ALGORITHM = MERGE
    DEFINER = 'delivery_view'@'localhost'
    SQL SECURITY DEFINER
    VIEW delivery_gateway.device_view AS
SELECT d.id       AS device_id,
       d.userid   AS user_id,
       d.appid    AS app_id,
       d.platform AS platform,
       d.pushid   AS push_token,
       a.enable   AS enabled
  FROM moodle_db.mdl_user_devices d
  JOIN moodle_db.mdl_message_airnotifier_devices a ON a.userdeviceid = d.id;

-- ---------------------------------------------------------------------------------------------------
-- 8. Check
-- ---------------------------------------------------------------------------------------------------
--
-- The four views must answer, the service user must hold no privilege on moodle_db, and
-- `site_default_view` must not be empty.

SELECT 'recipient_view'           AS view_name, COUNT(*) AS rows_found FROM delivery_gateway.recipient_view
UNION ALL
SELECT 'channel_preference_view',               COUNT(*)              FROM delivery_gateway.channel_preference_view
UNION ALL
SELECT 'site_default_view',                     COUNT(*)              FROM delivery_gateway.site_default_view
UNION ALL
SELECT 'device_view',                           COUNT(*)              FROM delivery_gateway.device_view;

SHOW GRANTS FOR 'delivery_svc'@'%';
