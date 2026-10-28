BEGIN;
CREATE TABLE IF NOT EXISTS integration.credential (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid REFERENCES core.organization(id),
 facility_id uuid REFERENCES core.facility(id),
 provider_id uuid REFERENCES provider.healthcare_provider(id),
 credential_type varchar(50) NOT NULL,
 label text NOT NULL,
 secret_backend varchar(30) NOT NULL DEFAULT 'docker_secret',
 certificate_secret_ref text NOT NULL,
 password_secret_ref text NOT NULL,
 certificate_fingerprint text,
 valid_from timestamptz,
 valid_until timestamptz,
 status varchar(30) NOT NULL DEFAULT 'active',
 last_used_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK (organization_id IS NOT NULL OR provider_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credential_identity
ON integration.credential(credential_type, COALESCE(provider_id,'00000000-0000-0000-0000-000000000000'::uuid), COALESCE(facility_id,'00000000-0000-0000-0000-000000000000'::uuid), label);
CREATE TABLE IF NOT EXISTS integration.credential_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), credential_id uuid NOT NULL REFERENCES integration.credential(id),
 actor_user_id uuid REFERENCES core.app_user(id), action varchar(50) NOT NULL,
 success boolean NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL DEFAULT '{}'
);
COMMIT;
