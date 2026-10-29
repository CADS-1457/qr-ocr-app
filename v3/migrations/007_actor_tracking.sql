BEGIN;

-- Ajout des acteurs de création et de modification aux tables métier.
-- Les colonnes restent NULLABLE tant que l'authentification V3
-- et l'utilisateur système de migration ne sont pas encore en place.

DO $$
DECLARE
    target RECORD;
    constraint_name TEXT;
BEGIN
    FOR target IN
        SELECT *
        FROM (
            VALUES
                ('core',           'app_user'),
                ('core',           'facility'),
                ('core',           'organization'),
                ('patient',        'consent'),
                ('patient',        'contact_point'),
                ('patient',        'household'),
                ('patient',        'patient'),
                ('patient',        'relationship'),
                ('agenda',         'booking_group'),
                ('billing',        'delegation'),
                ('correspondence', 'attachment'),
                ('correspondence', 'transmission'),
                ('integration',    'credential')
        ) AS tables_to_track(schema_name, table_name)
    LOOP
        IF to_regclass(
            format('%I.%I', target.schema_name, target.table_name)
        ) IS NULL THEN
            RAISE NOTICE 'Table %.% absente, ignorée',
                target.schema_name,
                target.table_name;
            CONTINUE;
        END IF;

        EXECUTE format(
            'ALTER TABLE %I.%I ADD COLUMN IF NOT EXISTS created_by uuid',
            target.schema_name,
            target.table_name
        );

        EXECUTE format(
            'ALTER TABLE %I.%I ADD COLUMN IF NOT EXISTS updated_by uuid',
            target.schema_name,
            target.table_name
        );

        constraint_name :=
            target.schema_name || '_' ||
            target.table_name || '_created_by_fkey';

        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I
                 ADD CONSTRAINT %I
                 FOREIGN KEY (created_by)
                 REFERENCES core.app_user(id)
                 ON DELETE SET NULL',
                target.schema_name,
                target.table_name,
                constraint_name
            );
        END IF;

        constraint_name :=
            target.schema_name || '_' ||
            target.table_name || '_updated_by_fkey';

        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I
                 ADD CONSTRAINT %I
                 FOREIGN KEY (updated_by)
                 REFERENCES core.app_user(id)
                 ON DELETE SET NULL',
                target.schema_name,
                target.table_name,
                constraint_name
            );
        END IF;
    END LOOP;
END
$$;

-- Les mouvements de stock sont des écritures métier append-only.
-- On conserve uniquement l'acteur ayant créé le mouvement.

ALTER TABLE inventory.stock_movement
    ADD COLUMN IF NOT EXISTS created_by uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'inventory_stock_movement_created_by_fkey'
    ) THEN
        ALTER TABLE inventory.stock_movement
            ADD CONSTRAINT inventory_stock_movement_created_by_fkey
            FOREIGN KEY (created_by)
            REFERENCES core.app_user(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

COMMENT ON COLUMN patient.patient.created_by IS
    'Utilisateur ayant créé le dossier patient';

COMMENT ON COLUMN patient.patient.updated_by IS
    'Utilisateur ayant effectué la dernière modification du dossier patient';

COMMENT ON COLUMN inventory.stock_movement.created_by IS
    'Utilisateur à l’origine du mouvement de stock';

COMMIT;
